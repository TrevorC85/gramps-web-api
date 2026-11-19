#!/usr/bin/env python3
"""Maintain one "Record links" note per person: one line per attached file that has a
known source URL (title — URL — record date). Idempotent: the note is found by its first
line and rewritten only when its content changes; it is removed from a person who no
longer has any linked file with a URL. Files moved or unlinked by the media audit drop
out automatically because the note is computed from the person's current media links.

URL sources, in order: the manifest entry's `url` for that file (either bot's
website_manifest.jsonl), a URL in the citation page that holds the file, then a URL on
the same line as the file name in the entry's source_log. Private when the person is.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api, first_new_handle  # noqa: E402
import media_overrides  # noqa: E402

HEADER = "Record links"
MANIFESTS = [Path("/workspace/genealogy/website_manifest.jsonl"),
             Path("/workspace/genealogy-cindy/website_manifest.jsonl")]
URL_RE = re.compile(r"https?://[^\s)\]>\"'|,]+")
ROOT = Path(__file__).resolve().parent
OVR = media_overrides.load()


def load_manifest():
    ents = {}
    for f in MANIFESTS:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("delete"):
                ents.pop(e.get("id"), None)
                continue
            ents[e.get("id")] = e
    by_path = {}
    for e in ents.values():
        if e.get("path"):
            by_path.setdefault(os.path.realpath(e["path"]), []).append(e)
    return by_path


_log_cache: dict[str, list[str]] = {}


def url_from_log(log: str, fname: str) -> str:
    if not log or not os.path.exists(log):
        return ""
    if log not in _log_cache:
        _log_cache[log] = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    for line in _log_cache[log]:
        i = line.find(fname)
        if i < 0:
            continue
        found = [(m.start(), m.group(0).rstrip(".;")) for m in URL_RE.finditer(line)]
        if not found:
            continue
        before = [u for pos, u in found if pos < i]
        return before[-1] if before else found[0][1]
    return ""


def media_original(m: dict, checksum_paths: dict) -> str:
    for a in m.get("attribute_list") or []:
        if a.get("type") == "Original file":
            return a.get("value") or ""
    ps = checksum_paths.get(m.get("checksum") or "") or []
    return ps[0] if ps else ""


def checksum_index() -> dict:
    """md5 -> original paths, cached in data/checksum_paths.json (built on demand)."""
    import hashlib
    cache = ROOT / "data" / "checksum_paths.json"
    idx = json.loads(cache.read_text()) if cache.exists() else {}
    return idx


def main():
    api = Api()
    by_path = load_manifest()
    ck = checksum_index()
    media = {m["handle"]: m for m in api.get("/api/media/?pagesize=100000") or []}
    cits = {c["handle"]: c for c in api.get("/api/citations/?pagesize=100000") or []}
    people = api.get("/api/people/?pagesize=100000") or []
    stats = {"people_with_note": 0, "created": 0, "updated": 0, "removed": 0, "lines": 0}
    for p in people:
        refs = [mr["ref"] for mr in p.get("media_list") or []]
        cite_of = {}
        for ch in p.get("citation_list") or []:
            c = cits.get(ch)
            for mr in (c or {}).get("media_list") or []:
                cite_of.setdefault(mr["ref"], c)
                if mr["ref"] not in refs:
                    refs.append(mr["ref"])
        lines = []
        for mh in refs:
            m = media.get(mh)
            if not m:
                continue
            orig = media_original(m, ck)
            ents = by_path.get(os.path.realpath(orig), []) if orig else []
            # prefer an entry for this person
            ents = sorted(ents, key=lambda e: 0 if e.get("person") == p["gramps_id"] else 1)
            ov = media_overrides.for_path(OVR, orig) or {}
            url = ov.get("url") or next((e["url"] for e in ents if e.get("url")), "")
            if not url and orig:
                for e in ents:
                    url = url_from_log(e.get("source_log") or "", Path(orig).name)
                    if url:
                        break
            c1 = cite_of.get(mh)
            if not url and c1 and len(c1.get("media_list") or []) == 1:
                mm = URL_RE.search(c1.get("page") or "")
                url = mm.group(0) if mm else ""
            if not url:
                continue
            title = ov.get("title") or next((e["title"] for e in ents if e.get("title")), "") or \
                (m.get("desc") or "").split(" [web-ready copy; original:")[0] or Path(orig).name
            date = ov.get("date") or next((e["date"] for e in ents if e.get("date")), "") or \
                (((cite_of.get(mh) or {}).get("date") or {}).get("text") or "")
            line = f"- {title} — {url} — record date: {date or 'not recorded'} ({m['gramps_id']})"
            if line not in lines:
                lines.append(line)
        lines.sort()
        text = HEADER + "\n" + "\n".join(lines) if lines else ""
        existing = None
        for nh in p.get("note_list") or []:
            n = api.get(f"/api/notes/{nh}")
            if ((n.get("text") or {}).get("string") or "").startswith(HEADER + "\n"):
                if existing is None:
                    existing = n
                else:  # duplicate from an older run: detach it
                    p["note_list"].remove(nh)
                    api.put(f"/api/people/{p['handle']}", p)
        private = bool(p.get("private"))
        if not lines:
            if existing:
                p = api.get(f"/api/people/{p['handle']}")
                p["note_list"] = [h for h in p["note_list"] if h != existing["handle"]]
                api.put(f"/api/people/{p['handle']}", p)
                stats["removed"] += 1
            continue
        stats["people_with_note"] += 1
        stats["lines"] += len(lines)
        if existing:
            if existing["text"]["string"] != text or bool(existing.get("private")) != private:
                existing["text"] = {"string": text, "tags": []}
                existing["private"] = private
                api.put(f"/api/notes/{existing['handle']}", existing)
                stats["updated"] += 1
            continue
        nh = first_new_handle(api.post("/api/notes/", {
            "_class": "Note", "type": "General", "private": private,
            "format": 0, "text": {"string": text, "tags": []}}))
        p = api.get(f"/api/people/{p['handle']}")
        p.setdefault("note_list", []).append(nh)
        api.put(f"/api/people/{p['handle']}", p)
        stats["created"] += 1
    (ROOT / "data" / "record_links_report.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats))


if __name__ == "__main__":
    main()

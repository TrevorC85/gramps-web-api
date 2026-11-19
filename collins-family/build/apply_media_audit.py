#!/usr/bin/env python3
"""Apply shared/audit_mismatches.csv to the live tree (idempotent).

For each audited file: remove it (media ref, its citations, and the manifest URL)
from every wrong person; strip those citations from the wrong person's events and
from families where every parent is a wrong person; for relinked rows add the file
and its citations to the right person and clear fact-level citations the audit
rejected. Corrected titles, dates and links are written to the media object, its
source and its citation page. Nothing is deleted from disk or from the database.
"""
from __future__ import annotations

import hashlib
import unicodedata
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_overrides  # noqa: E402
from lib_api import Api  # noqa: E402

MANIFESTS = [Path("/workspace/genealogy/website_manifest.jsonl"),
             Path("/workspace/genealogy-cindy/website_manifest.jsonl")]
SUFFIX = " [web-ready copy; original:"


def manifest_by_path():
    out = {}
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
            if e.get("path"):
                out.setdefault(os.path.realpath(e["path"]), []).append(e)
    return out


def live_by_path():
    """Live manifest entries (last record of an id wins; delete drops it), by real path."""
    by_id = {}
    n_line = 0
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
            if not e.get("id"):
                continue
            n_line += 1
            e["_n"] = n_line
            if e.get("delete") is True:
                by_id.pop(e["id"], None)
            else:
                by_id[e["id"]] = e
    out = {}
    for e in by_id.values():
        if e.get("path"):
            out.setdefault(os.path.realpath(e["path"]), []).append(e)
    return out


def main():
    api = Api()
    ov = media_overrides.load()
    mans = manifest_by_path()
    people = {p["gramps_id"]: p for p in api.get("/api/people/?pagesize=100000")}
    media = api.get("/api/media/?pagesize=100000")
    cits = api.get("/api/citations/?pagesize=100000")
    fams = api.get("/api/families/?pagesize=100000")
    log = []

    def find_media_own(path):
        """Media whose Original file is this path (not just identical bytes)."""
        own = [m for m in media if path in [os.path.realpath(a.get("value") or "")
                                             for a in m.get("attribute_list") or [] if a.get("type") == "Original file"]]
        return own

    def find_media(path):
        md5 = hashlib.md5(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None
        hits = []
        for m in media:
            attrs = [a.get("value") for a in m.get("attribute_list") or [] if a.get("type") == "Original file"]
            if path in attrs or (md5 and m.get("checksum") == md5):
                hits.append(m)
        return hits

    live = live_by_path()
    linked = {}
    for p in people.values():
        for r in p.get("media_list") or []:
            linked.setdefault(r["ref"], set()).add(p["gramps_id"])

    def fold(s):
        return " ".join(unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().split())

    def full(p):
        n = p.get("primary_name") or {}
        return " ".join([n.get("first_name", "")] + [x.get("surname", "") for x in n.get("surname_list") or []]).strip()

    by_name = {}
    for p in people.values():
        by_name.setdefault(fold(full(p)), []).append(p["gramps_id"])

    def expected_people(path):
        """People the bot's live entries (after audit guards) put on this file; None if unsure."""
        exp = set()
        for e in live.get(path, []):
            e2, _ = media_overrides.rewrite_entry(e, ov)
            if not e2:
                continue
            for x in [e2.get("person")] + list(e2.get("also_for") or []):
                if isinstance(x, str) and x:
                    exp.add(x)
                elif isinstance(x, dict):
                    c = by_name.get(fold(x.get("name")), [])
                    if len(c) != 1:
                        return None
                    exp.add(c[0])
        return exp

    empty = {"drop": set(), "add": [], "block_new": False, "title": "", "date": "", "url": "", "drop_supports": False}
    work = dict(ov)
    for path in live:
        work.setdefault(path, empty)
    for path, o in work.items():
        es = live.get(path, [])
        if es and not any(e.get("replaces") for e in es) and not o["title"]:
            # plain entry: the site shows the manifest's own title
            titles = {e.get("title") for e in es if e.get("title")}
            last = max(es, key=lambda e: e.get("_n", 0))
            if titles:
                for m in find_media_own(path):
                    if (m.get("desc") or "").split(SUFFIX)[0] not in titles:
                        o = dict(o, title=last.get("title"))
        if any(e.get("replaces") for e in es):
            # The bot rewrote its entry after the audit. Its people, title, date and URL
            # win; the audit only keeps guarding against the people it found wrong.
            o = {"drop": set(o["drop"]) - set(o["add"]), "add": [], "block_new": False, "title": "",
                 "date": "", "url": "", "drop_supports": False}
            exp = expected_people(path)
            last = max(es, key=lambda e: (e.get("replaces") is True, e.get("_n", 0)))
            titles = {e.get("title") for e in es if e.get("title")}
            for m in find_media_own(path):
                if exp is not None:
                    o["drop"] |= linked.get(m["handle"], set()) - exp
                desc = (m.get("desc") or "").split(SUFFIX)[0]
                if titles and desc not in titles:
                    o["title"] = last.get("title") or ""
            if o["title"] and last.get("url"):
                o["url"] = last["url"]
            if o["title"] and last.get("date"):
                o["date"] = str(last["date"])
        for m in find_media(path):
            mh = m["handle"]
            C = [c for c in cits if any(x["ref"] == mh for x in c.get("media_list") or [])]
            ch_all = {c["handle"] for c in C}
            # citations that carry only audited media (safe to move with the file)
            movable = set()
            for c in C:
                refs = [x["ref"] for x in c.get("media_list") or []]
                if len(refs) == 1:
                    movable.add(c["handle"])
                else:
                    others = [r for r in refs if r != mh]
                    if all(any(om["handle"] == r for om in find_media_all(media, ov)) for r in others):
                        movable.add(c["handle"])
                    elif o["drop"] or o["add"]:
                        # shared citation: take this file out of it if another citation carries it
                        if any(c2["handle"] != c["handle"] and len(c2.get("media_list") or []) == 1 for c2 in C):
                            cc = api.get(f"/api/citations/{c['handle']}")
                            cc["media_list"] = [x for x in cc["media_list"] if x["ref"] != mh]
                            api.put(f"/api/citations/{cc['handle']}", cc)
                            log.append(f"{m['gramps_id']}: removed from shared citation {cc['gramps_id']}")
            old_titles = {e.get("title") for e in mans.get(path, []) if e.get("title")}
            old_urls = {e.get("url") for e in mans.get(path, []) if e.get("url")}
            # ---- wrong people
            for gid in sorted(o["drop"]):
                if gid in o["add"] or gid not in people:
                    continue
                p = api.get(f"/api/people/{people[gid]['handle']}")
                ch = False
                if any(x["ref"] == mh for x in p.get("media_list") or []):
                    p["media_list"] = [x for x in p["media_list"] if x["ref"] != mh]
                    ch = True
                keep = [c for c in p.get("citation_list") or [] if c not in movable]
                if keep != (p.get("citation_list") or []):
                    p["citation_list"] = keep
                    ch = True
                urls = [u for u in p.get("urls") or [] if not (u.get("path") in old_urls | ({o["url"]} if o["url"] else set()) and (u.get("desc") in old_titles or u.get("desc") == o["title"]))]
                if urls != (p.get("urls") or []):
                    p["urls"] = urls
                    ch = True
                if ch:
                    api.put(f"/api/people/{p['handle']}", p)
                    log.append(f"{m['gramps_id']}: unlinked from {gid}")
                for er in p.get("event_ref_list") or []:
                    ev = api.get(f"/api/events/{er['ref']}")
                    if set(ev.get("citation_list") or []) & movable:
                        ev["citation_list"] = [c for c in ev["citation_list"] if c not in movable]
                        api.put(f"/api/events/{ev['handle']}", ev)
                        log.append(f"{m['gramps_id']}: citation removed from {gid} {ev.get('type')} event")
            # families where every parent is a wrong person
            dropped_h = {people[g]["handle"] for g in o["drop"] if g in people and g not in o["add"]}
            for f in fams:
                par = {h for h in (f.get("father_handle"), f.get("mother_handle")) if h}
                if not par or not par <= dropped_h:
                    continue
                ff = api.get(f"/api/families/{f['handle']}")
                ch = False
                if set(ff.get("citation_list") or []) & movable:
                    ff["citation_list"] = [c for c in ff["citation_list"] if c not in movable]
                    ch = True
                for cr in ff.get("child_ref_list") or []:
                    if set(cr.get("citation_list") or []) & movable:
                        cr["citation_list"] = [c for c in cr["citation_list"] if c not in movable]
                        ch = True
                for er in ff.get("event_ref_list") or []:
                    ev = api.get(f"/api/events/{er['ref']}")
                    if set(ev.get("citation_list") or []) & movable:
                        ev["citation_list"] = [c for c in ev["citation_list"] if c not in movable]
                        api.put(f"/api/events/{ev['handle']}", ev)
                if ch:
                    api.put(f"/api/families/{ff['handle']}", ff)
                    log.append(f"{m['gramps_id']}: citation removed from family {ff['gramps_id']}")
            # ---- relinked: rejected fact-level citations come off every event/family
            if o["add"]:
                for p in people.values():
                    for er in p.get("event_ref_list") or []:
                        ev = api.get(f"/api/events/{er['ref']}")
                        if set(ev.get("citation_list") or []) & movable:
                            ev["citation_list"] = [c for c in ev["citation_list"] if c not in movable]
                            api.put(f"/api/events/{ev['handle']}", ev)
                            log.append(f"{m['gramps_id']}: citation removed from {p['gramps_id']} {ev.get('type')} event")
                for f in fams:
                    ff = None
                    if set(f.get("citation_list") or []) & movable or any(set(cr.get("citation_list") or []) & movable for cr in f.get("child_ref_list") or []):
                        ff = api.get(f"/api/families/{f['handle']}")
                        ff["citation_list"] = [c for c in ff.get("citation_list") or [] if c not in movable]
                        for cr in ff.get("child_ref_list") or []:
                            cr["citation_list"] = [c for c in cr.get("citation_list") or [] if c not in movable]
                        api.put(f"/api/families/{ff['handle']}", ff)
                        log.append(f"{m['gramps_id']}: citation removed from family {ff['gramps_id']}")
                for gid in o["add"]:
                    if gid not in people:
                        log.append(f"{m['gramps_id']}: right person {gid} not in tree")
                        continue
                    p = api.get(f"/api/people/{people[gid]['handle']}")
                    ch = False
                    if not any(x["ref"] == mh for x in p.get("media_list") or []):
                        p.setdefault("media_list", []).append({"ref": mh, "rect": [], "private": bool(p.get("private")),
                                                               "citation_list": [], "note_list": [], "attribute_list": []})
                        ch = True
                    for c in sorted(movable):
                        if c not in (p.get("citation_list") or []):
                            p.setdefault("citation_list", []).append(c)
                            ch = True
                    if ch:
                        api.put(f"/api/people/{p['handle']}", p)
                        log.append(f"{m['gramps_id']}: linked to {gid}")
            # ---- corrected title / date / url
            if o["title"] or o["url"] or o["date"]:
                mm = api.get(f"/api/media/{mh}")
                if o["title"]:
                    suffix = ""
                    if SUFFIX in (mm.get("desc") or ""):
                        suffix = mm["desc"][mm["desc"].index(SUFFIX):]
                    if mm.get("desc") != o["title"] + suffix:
                        mm["desc"] = o["title"] + suffix
                        api.put(f"/api/media/{mh}", mm)
                        log.append(f"{m['gramps_id']}: title corrected")
                for c in C:
                    cc = api.get(f"/api/citations/{c['handle']}")
                    page = cc.get("page") or ""
                    fname = page.split(" ")[0] if page else Path(path).name
                    url = o["url"] or next(iter(old_urls), "")
                    date = o["date"]
                    new_page = fname + (f" {url}" if url else "") + (f" ({date})" if date else "")
                    if o["url"] or o["date"]:
                        if page != new_page:
                            cc["page"] = new_page
                            api.put(f"/api/citations/{cc['handle']}", cc)
                    if o["title"]:
                        src = api.get(f"/api/sources/{cc['source_handle']}")
                        if src.get("title") != o["title"]:
                            src["title"] = o["title"]
                            src["abbrev"] = o["title"][:40]
                            if o["date"]:
                                src["pubinfo"] = o["date"]
                            api.put(f"/api/sources/{src['handle']}", src)
                # stale manifest URL with the old title on any person
                for p in people.values():
                    if p["gramps_id"] in o["drop"] and p["gramps_id"] not in o["add"]:
                        continue
                    stale = [u for u in p.get("urls") or []
                             if u.get("path") in old_urls and u.get("desc") in old_titles
                             and ((o["url"] and u.get("path") != o["url"]) or (o["title"] and u.get("desc") != o["title"]))]
                    if not stale:
                        continue
                    pp = api.get(f"/api/people/{p['handle']}")
                    new_urls = []
                    for u in pp.get("urls") or []:
                        if u.get("path") in old_urls and u.get("desc") in old_titles:
                            if o["url"] and u.get("path") != o["url"]:
                                continue  # wrong link: attach_manifest re-adds the corrected one
                            u = dict(u, desc=o["title"] or u.get("desc"))
                        if any(x.get("path") == u.get("path") and x.get("desc") == u.get("desc") for x in new_urls):
                            continue
                        new_urls.append(u)
                    if new_urls == (pp.get("urls") or []):
                        continue
                    pp["urls"] = new_urls
                    api.put(f"/api/people/{pp['handle']}", pp)
                    log.append(f"{m['gramps_id']}: stale link fixed on {p['gramps_id']}")
    # ---- a citation whose page names one file must not carry a different file
    # (older runs matched media on title alone and could hang the wrong image on it)
    mby = {m["handle"]: m for m in api.get("/api/media/?pagesize=100000")}
    def base_names(m):
        return {os.path.basename(a.get("value") or "") for a in m.get("attribute_list") or []
                if a.get("type") == "Original file"}
    for c in api.get("/api/citations/?pagesize=100000"):
        fname = (c.get("page") or "").split(" ")[0]
        refs = [r["ref"] for r in c.get("media_list") or []]
        if not fname or "." not in fname or fname.startswith("http") or not refs:
            continue
        right = [h for h in refs if h in mby and fname in base_names(mby[h])]
        if not right:
            own = [h for h, m in mby.items() if fname in base_names(m)]
            if len(own) == 1 and refs and all(base_names(mby[h]) and fname not in base_names(mby[h])
                                              and mby[h].get("checksum") != mby[own[0]].get("checksum")
                                              for h in refs if h in mby):
                # the page's own file exists but the citation carries another file
                cc = api.get(f"/api/citations/{c['handle']}")
                cc["media_list"] = [dict(cc["media_list"][0], ref=own[0])]
                api.put(f"/api/citations/{cc['handle']}", cc)
                log.append(f"{c['gramps_id']}: now carries {mby[own[0]]['gramps_id']} instead of "
                           f"{', '.join(mby[h]['gramps_id'] for h in refs if h in mby)} (page names {fname})")
            continue
        if len(refs) < 2:
            continue
        sums = {mby[h].get("checksum") for h in right}
        wrong = [h for h in refs if h in mby and h not in right and base_names(mby[h])
                 and mby[h].get("checksum") not in sums]
        if wrong:
            cc = api.get(f"/api/citations/{c['handle']}")
            cc["media_list"] = [r for r in cc["media_list"] if r["ref"] not in wrong]
            api.put(f"/api/citations/{cc['handle']}", cc)
            log.append(f"{c['gramps_id']}: removed {', '.join(mby[h]['gramps_id'] for h in wrong)} (page names {fname})")
    out = Path(__file__).resolve().parent / "data" / "media_audit_apply_log.json"
    out.write_text(json.dumps(log, indent=1))
    print(f"apply_media_audit: {len(log)} changes")
    for line in log:
        print(" ", line)


def find_media_all(media, ov):
    """Media objects that belong to any audited file."""
    res = []
    for m in media:
        for a in m.get("attribute_list") or []:
            if a.get("type") == "Original file" and os.path.realpath(a.get("value") or "") in ov:
                res.append(m)
    return res


if __name__ == "__main__":
    main()

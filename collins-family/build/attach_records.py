#!/usr/bin/env python3
"""Attach a high-confidence allowlist of original record files, and count
who already has URLs. Files are attached only when the filename uniquely
identifies a deceased person already in the graph and the file is a named
vital/memorial image (not a generated chart). Ambiguous or unverified
scans are listed in data/skipped_records.json for later review.
"""
from __future__ import annotations

import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_prep  # noqa: E402
import media_overrides  # noqa: E402

OVERRIDES = media_overrides.load()

ROOT = Path(__file__).resolve().parent
MAP_PATH = ROOT / "data" / "handle_map.json"
GRAPH = ROOT / "data" / "graph.json"
PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"

# (canon key, path, source title, media description)
# Only items confirmed by distinctive filename + prior visual or record mention.
ALLOWLIST = [
    # eli_collins_headstone.jpg removed 18 Nov 2026 (round 5, item 18): it is a saved Cloudflare
    # block page (HTML), not a headstone image. The file stays on disk; it is not on the site.
    ("collins:EC", "/workspace/genealogy/eli_collins_findagrave_509573.png",
     "Find a Grave memorial page, Eli Collins (509573)",
     "Find a Grave screenshot, Eli Collins memorial 509573"),
    ("collins:EC", "/workspace/genealogy/eli_collins_1860_chester_rows.png",
     "1860 U.S. census, Chester Township, Eli Collins household (row crop)",
     "1860 census row crop, Eli Collins household"),
    ("judy:JL", "/workspace/genealogy/census_1698_portroyal_jacques_leger_c2572_img124_crop.jpg",
     "1698 census, Port-Royal, Jacques Léger household",
     "1698 Port-Royal census crop, Jacques Léger"),
    ("judy:JL", "/workspace/genealogy/census_1700_portroyal_jacques_leger_c2572_img164_crop.jpg",
     "1700 census, Port-Royal, Jacques Léger household",
     "1700 Port-Royal census crop, Jacques Léger"),
    ("judy:B", "/workspace/genealogy/benoni_leger_bapt_1815_full.png",
     "Baptism of Benoni Léger, 1815",
     "Baptism record image, Benoni Léger, 1815"),
    ("cindy:johnjr", "/workspace/genealogy-cindy/john_donald_schneider_jr_headstone_2009_tahoma_findagrave.jpg",
     "Already attached in the 3-generation pass if present",
     "Headstone of John Donald Schneider Jr."),
]


def load_owner():
    user = pw = None
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and user is None:
            user = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and pw is None and user:
            pw = line.split("=", 1)[1].strip()
            break
    return user, pw


def token():
    user, pw = load_owner()
    body = json.dumps({"username": user, "password": pw}).encode()
    for attempt in range(8):
        req = urllib.request.Request(BASE + "/api/token/", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["access_token"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + attempt * 5)
                continue
            raise
    raise SystemExit("rate limited")


class Api:
    def __init__(self):
        self.tok = token()

    def req(self, method, path, data=None, content_type=None):
        if method == "PUT":  # round 5: skip unchanged saves, retry transient failures
            from lib_api import guarded_put
            return guarded_put(lambda: self._req(method, path, data, content_type), lambda: self._req("GET", path), path, data)
        return self._req(method, path, data, content_type)

    def _req(self, method, path, data=None, content_type=None):
        headers = {"Authorization": f"Bearer {self.tok}"}
        body = None
        if data is not None:
            if isinstance(data, (bytes, bytearray)):
                body = data
                headers["Content-Type"] = content_type or "application/octet-stream"
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def get(self, path):
        return self.req("GET", path)


def first_handle(resp):
    if isinstance(resp, list):
        for it in resp:
            if isinstance(it, dict) and it.get("type") == "add":
                return it.get("handle") or (it.get("new") or {}).get("handle")
        return resp[0].get("handle")
    return resp.get("handle")


def already_has_desc(api, person, desc, path=None):
    """True when the person already carries this record.

    Matches by the description, and also by the stored file itself (same
    "Original file" attribute or same bytes), because the media audit may
    retitle the media to the manifest's title; a title-only check would
    re-upload the file (plus a new source and citation) on every rebuild.
    """
    want_md5 = want_real = None
    if path:
        import hashlib, os
        try:
            want_md5 = hashlib.md5(Path(path).read_bytes()).hexdigest()
            want_real = os.path.realpath(path)
        except OSError:
            pass
    for mr in person.get("media_list") or []:
        m = api.get(f"/api/media/{mr['ref']}")
        if (m.get("desc") or "") == desc:
            return True
        if want_md5 and (m.get("checksum") or "") == want_md5:
            return True
        if want_real:
            import os
            for a in m.get("attribute_list") or []:
                if (a.get("type") == "Original file" or (isinstance(a.get("type"), dict) and a["type"].get("string") == "Original file")) \
                        and os.path.realpath(a.get("value") or "") == want_real:
                    return True
    return False


def main():
    api = Api()
    mmap = json.loads(MAP_PATH.read_text())
    graph = json.loads(GRAPH.read_text())
    people = {p["canon"]: p for p in graph["people"]}

    attached = []
    skipped_allow = []
    for canon, path, title, desc in ALLOWLIST:
        pth = Path(path)
        if not pth.exists():
            skipped_allow.append({"canon": canon, "path": path, "reason": "missing file"})
            continue
        handle = mmap.get(canon)
        if not handle:
            skipped_allow.append({"canon": canon, "path": path, "reason": "person not in handle map"})
            continue
        person = api.get(f"/api/people/{handle}")
        if media_overrides.person_blocked(OVERRIDES, path, person.get("gramps_id", "")):
            skipped_allow.append({"canon": canon, "path": path, "reason": "removed by media audit"})
            continue
        if already_has_desc(api, person, desc, path):
            attached.append({"canon": canon, "path": path, "status": "already"})
            continue
        up, info = media_prep.prepare_upload(pth)  # >10 MiB -> web-ready copy
        mime = mimetypes.guess_type(str(up))[0] or "image/jpeg"
        raw = up.read_bytes()
        resp = api.req("POST", "/api/media/", raw, content_type=mime)
        mh = first_handle(resp)
        media = api.get(f"/api/media/{mh}")
        media["desc"] = desc
        media["private"] = False
        media = media_prep.set_derivative_metadata(media, info)
        api.req("PUT", f"/api/media/{mh}", media)
        # source + citation
        src = api.req("POST", "/api/sources/", {
            "_class": "Source", "title": title, "abbrev": title[:40],
            "pubinfo": pth.name, "author": "",
        })
        sh = first_handle(src)
        cite = api.req("POST", "/api/citations/", {
            "_class": "Citation",
            "source_handle": sh,
            "page": pth.name,
            "confidence": 3,
            "private": False,
            "media_list": [{"ref": mh, "rect": [], "private": False,
                             "citation_list": [], "note_list": [], "attribute_list": []}],
        })
        ch = first_handle(cite)
        person = api.get(f"/api/people/{handle}")
        person.setdefault("citation_list", []).append(ch)
        person.setdefault("media_list", []).append({
            "ref": mh, "rect": [], "private": False,
            "citation_list": [], "note_list": [], "attribute_list": [],
        })
        api.req("PUT", f"/api/people/{handle}", person)
        attached.append({"canon": canon, "path": path, "status": "attached", "title": title})
        print("ATTACHED", canon, pth.name)

    # Coverage stats
    all_people = api.get("/api/people/")
    n = len(all_people)
    with_media = 0
    with_urls = 0
    with_cites = 0
    neither = 0
    for p in all_people:
        full = p  # list may already include lists
        has_m = bool(full.get("media_list"))
        has_u = bool(full.get("urls"))
        has_c = bool(full.get("citation_list"))
        if has_m:
            with_media += 1
        if has_u:
            with_urls += 1
        if has_c:
            with_cites += 1
        if not has_m and not has_u:
            neither += 1

    report = {
        "attached": attached,
        "skipped_allowlist": skipped_allow,
        "coverage": {
            "people": n,
            "with_media": with_media,
            "with_urls": with_urls,
            "with_citations": with_cites,
            "with_neither_media_nor_url": neither,
        },
        "note": (
            "Thousands of branch-scan and research-screenshot files exist under "
            "/workspace/genealogy and /workspace/genealogy-cindy. Only allowlisted, "
            "unambiguously named originals were auto-attached in this pass, plus the "
            "3-generation originals already attached earlier. Generated charts were not attached."
        ),
    }
    (ROOT / "data" / "attach_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["coverage"], indent=2))
    print("allowlist attached/already", len(attached), "skipped", len(skipped_allow))


if __name__ == "__main__":
    main()

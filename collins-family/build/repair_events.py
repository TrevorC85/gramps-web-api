#!/usr/bin/env python3
"""Retype events whose description shows they were misparsed (bur. as Birth)."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys as _sys_r5
_sys_r5.path.insert(0, str(Path(__file__).resolve().parent))
from lib_parse import EVENT_PREFIX  # noqa: E402

PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"
OUT = Path("/workspace/gramps-web/build/data/repair_events.json")


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

    def req(self, method, path, data=None):
        if method == "PUT":  # round 5: skip unchanged saves, retry transient failures
            from lib_api import guarded_put
            return guarded_put(lambda: self._req(method, path, data), lambda: self._req("GET", path), path, data)
        return self._req(method, path, data)

    def _req(self, method, path, data=None):
        headers = {"Authorization": f"Bearer {self.tok}"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def get(self, path):
        return self.req("GET", path)


BURIAL_RE = re.compile(r"^\s*bur(?:\.|ied|\s)|^\s*burial\b", re.I)


def looks_like_burial(ev) -> bool:
    blob = " ".join([
        ev.get("description") or "",
        ev.get("type") or "",
        (ev.get("date") or {}).get("text") or "",
    ])
    return bool(BURIAL_RE.search(blob))


def main():
    api = Api()
    events = api.get("/api/events/")
    changed = []
    skipped = []
    for ev in events:
        if ev.get("type") == "Burial" or ev.get("type") == "Death":
            continue
        if not looks_like_burial(ev):
            continue
        if ev.get("type") != "Birth":
            skipped.append({"gramps_id": ev.get("gramps_id"), "type": ev.get("type"),
                             "description": ev.get("description")})
            continue
        full = api.get(f"/api/events/{ev['handle']}")
        full["type"] = "Burial"
        api.req("PUT", f"/api/events/{ev['handle']}", full)
        changed.append({
            "gramps_id": ev.get("gramps_id"),
            "handle": ev["handle"],
            "description": ev.get("description"),
        })
        print("FIXED", ev.get("gramps_id"), ev.get("description"))

    # If a person's birth_ref_index now points at a Burial, clear or retarget it.
    birth_fixes = []
    if changed:
        people = api.get("/api/people/")
        burial_handles = {c["handle"] for c in changed}
        for p in people:
            idx = p.get("birth_ref_index", -1)
            refs = p.get("event_ref_list") or []
            if idx is None or idx < 0 or idx >= len(refs):
                continue
            if refs[idx]["ref"] not in burial_handles:
                continue
            full = api.get(f"/api/people/{p['handle']}")
            new_idx = -1
            for i, ref in enumerate(full.get("event_ref_list") or []):
                ev = api.get(f"/api/events/{ref['ref']}")
                if ev.get("type") == "Birth":
                    new_idx = i
                    break
            full["birth_ref_index"] = new_idx
            api.req("PUT", f"/api/people/{p['handle']}", full)
            birth_fixes.append({"gramps_id": p.get("gramps_id"), "new_birth_ref_index": new_idx})
            print("BIRTH INDEX", p.get("gramps_id"), "->", new_idx)

    # 19 Nov 2026: the old "b" prefix bug left places named "ur. X" and dates whose text is
    # the raw chart clause. Clean both: clear raw-clause date text, strip "ur. " from place
    # names, merging into an existing same-named sibling place when there is one.
    text_fixes, place_fixes = [], []
    for ev in api.get("/api/events/"):
        d = ev.get("date") or {}
        if d.get("text") and not any(d.get("dateval") or [0, 0, 0]) and EVENT_PREFIX.match(d["text"]):
            full = api.get(f"/api/events/{ev['handle']}")
            full["date"]["text"] = ""
            api.req("PUT", f"/api/events/{ev['handle']}", full)
            text_fixes.append(ev.get("gramps_id"))
    places = api.get("/api/places/")
    for pl in places:
        name = (pl.get("name") or {}).get("value") or ""
        if not re.match(r"^b?ur\.\s+", name):
            continue
        clean = re.sub(r"^b?ur\.\s+", "", name)
        parent = [r["ref"] for r in pl.get("placeref_list") or []]
        twin = next((q for q in places if q["handle"] != pl["handle"]
                     and (q.get("name") or {}).get("value") == clean
                     and [r["ref"] for r in q.get("placeref_list") or []] == parent), None)
        if twin:
            for ev in api.get("/api/events/"):
                if ev.get("place") == pl["handle"]:
                    full = api.get(f"/api/events/{ev['handle']}")
                    full["place"] = twin["handle"]
                    api.req("PUT", f"/api/events/{ev['handle']}", full)
            api.req("DELETE", f"/api/places/{pl['handle']}")
        else:
            full = api.get(f"/api/places/{pl['handle']}")
            full["name"]["value"] = clean
            full["title"] = re.sub(r"^b?ur\.\s+", "", full.get("title") or clean)
            api.req("PUT", f"/api/places/{pl['handle']}", full)
        place_fixes.append({"gramps_id": pl.get("gramps_id"), "name": clean, "merged": bool(twin)})

    report = {"retyped_to_burial": changed, "n": len(changed),
              "date_text_cleared": text_fixes, "ur_places_fixed": place_fixes,
              "skipped_non_birth": skipped, "birth_index_fixes": birth_fixes}
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({"n": len(changed), "birth_index_fixes": len(birth_fixes)}, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Merge known Collins-side duplicate people and fix a few family roles.

Safe / idempotent: skips a merge if the titanic handle is already gone.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
MAP_PATH = ROOT / "data" / "handle_map.json"
PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"

# survivor canon <- list of duplicate canons to merge into it
MERGES = {
    "collins:ER": ["collins:fam_m9"],
    "collins:TM": ["collins:fam_m7"],
    "collins:WC": ["collins:fam_c5_0"],
    "collins:EC": ["collins:fam_c0_5"],
    "collins:fam_c4_2": ["collins:fam_c3_0"],  # Ida Philomena survives
}


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
        req = urllib.request.Request(
            BASE + "/api/token/", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["access_token"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + attempt * 5)
                continue
            raise
    raise SystemExit("token rate limited")


class Api:
    def __init__(self):
        self.tok = token()

    def req(self, method, path, data=None):
        if method == "PUT":  # round 5: skip unchanged saves, retry transient failures
            from lib_api import guarded_put
            def send():
                code, out = self._req(method, path, data)
                if code not in (200, 201, 204):
                    raise RuntimeError(f"PUT {path} -> {code}: {str(out)[:300]}")
                return code, out
            def fetch():
                code, out = self._req("GET", path)
                return out if code == 200 else None
            r = guarded_put(send, fetch, path, data)
            return r if r is not None else (200, None)
        return self._req(method, path, data)

    def _req(self, method, path, data=None):
        headers = {"Authorization": f"Bearer {self.tok}"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")
            return e.code, err

    def get(self, path):
        code, data = self.req("GET", path)
        if code != 200:
            raise RuntimeError(f"GET {path} {code} {data}")
        return data


def nam(api, h):
    if not h:
        return "-"
    p = api.get(f"/api/people/{h}")
    pn = p.get("primary_name") or {}
    return p.get("gramps_id", "") + " " + (pn.get("first_name") or "") + " " + " ".join(
        s.get("surname", "") for s in pn.get("surname_list") or []
    )


def main():
    api = Api()
    mmap = json.loads(MAP_PATH.read_text())

    # --- merges ---
    for survivor, dups in MERGES.items():
        sh = mmap.get(survivor)
        if not sh:
            print("missing survivor", survivor)
            continue
        # confirm survivor exists
        code, _ = api.req("GET", f"/api/people/{sh}")
        if code != 200:
            print("survivor gone", survivor, sh)
            continue
        for dup in dups:
            th = mmap.get(dup)
            if not th:
                print("no map for", dup, "- skip")
                continue
            if th == sh:
                print("already same handle", survivor, dup)
                continue
            code, _ = api.req("GET", f"/api/people/{th}")
            if code == 404:
                print("already merged", dup)
                mmap[dup] = sh
                continue
            print(f"MERGE {dup} ({nam(api, th)})  ->  {survivor} ({nam(api, sh)})")
            # try POST merge
            code, data = api.req("POST", f"/api/people/{sh}/merge/{th}")
            print("  result", code, str(data)[:300])
            if code in (200, 201, 204):
                mmap[dup] = sh
            else:
                # some versions want a body
                code2, data2 = api.req("POST", f"/api/people/{sh}/merge/{th}", {})
                print("  retry", code2, str(data2)[:300])
                if code2 in (200, 201, 204):
                    mmap[dup] = sh

    MAP_PATH.write_text(json.dumps(mmap, indent=2))

    # --- fix specific families ---
    # Refresh handles after merge
    def h(canon):
        return mmap[canon]

    def fix_family(gid, father_canon, mother_canon, child_canons, extra=None):
        # find family by current child list / or by gid
        families = api.get("/api/families/")
        fam = next((f for f in families if f.get("gramps_id") == gid), None)
        if not fam:
            print("no family", gid)
            return
        fam = api.get(f"/api/families/{fam['handle']}")
        fam["father_handle"] = h(father_canon) if father_canon else ""
        fam["mother_handle"] = h(mother_canon) if mother_canon else ""
        # ensure children
        have = {c["ref"] for c in fam.get("child_ref_list") or []}
        for c in child_canons:
            ch = h(c)
            if ch not in have:
                fam.setdefault("child_ref_list", []).append({
                    "ref": ch, "frel": "Birth", "mrel": "Birth",
                    "private": False, "citation_list": [], "note_list": [],
                })
        code, data = api.req("PUT", f"/api/families/{fam['handle']}", fam)
        print(f"FIX {gid} father={father_canon} mother={mother_canon} -> {code}")
        # ensure parent family_list / child parent_family_list
        for role, canon in (("family_list", father_canon), ("family_list", mother_canon)):
            if not canon:
                continue
            p = api.get(f"/api/people/{h(canon)}")
            if fam["handle"] not in (p.get(role) or []):
                p.setdefault(role, []).append(fam["handle"])
                api.req("PUT", f"/api/people/{p['handle']}", p)
        for c in child_canons:
            p = api.get(f"/api/people/{h(c)}")
            if fam["handle"] not in (p.get("parent_family_list") or []):
                p.setdefault("parent_family_list", []).append(fam["handle"])
                api.req("PUT", f"/api/people/{p['handle']}", p)

    # After merges, F0387 should be Joseph Henry x Elizabeth, child James William
    fix_family("F0387", "collins:JH", "collins:ER", ["I0004"])
    # F0388 should be William J. Francis x Therese, children Joseph Henry + James Collins
    fix_family("F0388", "collins:WC", "collins:TM", ["collins:JH", "collins:fam_c7_1"])

    # If F0403 is now a duplicate of F0388 (same parents), move any leftover children then we leave it;
    # try to attach James Collins if needed (already in F0388 list).

    # F0007: Mike's legal father Paul, birth mother Donna — frel Adopted, mrel Birth
    families = api.get("/api/families/")
    f0007 = next((f for f in families if f.get("gramps_id") == "F0007"), None)
    if f0007:
        fam = api.get(f"/api/families/{f0007['handle']}")
        changed = False
        for cr in fam.get("child_ref_list") or []:
            if cr.get("ref") == mmap.get("I0009"):
                if cr.get("mrel") != "Birth" or cr.get("frel") != "Adopted":
                    cr["frel"] = "Adopted"
                    cr["mrel"] = "Birth"
                    changed = True
        if changed:
            code, _ = api.req("PUT", f"/api/families/{fam['handle']}", fam)
            print("FIX F0007 Mike frel=Adopted mrel=Birth ->", code)
        else:
            print("F0007 already correct or child not found")

    # Christian North is the wife of Nicholas James
    # find by handle map
    mmap = json.loads(MAP_PATH.read_text())
    for canon, handle in mmap.items():
        if canon == "judy:CN" or canon.endswith(":CN"):
            p = api.get(f"/api/people/{handle}")
            if p.get("gender") != 0:  # Gramps: 0 = female
                p["gender"] = 0
                code, _ = api.req("PUT", f"/api/people/{handle}", p)
                print("FIX gender Christian North female ->", code)
            break

    # Recount
    people = api.get("/api/people/")
    fams = api.get("/api/families/")
    print("people", len(people), "families", len(fams))

    # Re-check the fixed families
    for gid in ("F0387", "F0388", "F0007", "F0006"):
        fam = next((f for f in fams if f.get("gramps_id") == gid), None)
        if not fam:
            print(gid, "missing")
            continue
        fam = api.get(f"/api/families/{fam['handle']}")
        print(gid, "father=", nam(api, fam.get("father_handle")), "mother=", nam(api, fam.get("mother_handle")),
              "kids=", [nam(api, cr["ref"]) + f"/{cr.get('frel')}/{cr.get('mrel')}" for cr in fam.get("child_ref_list") or []])


if __name__ == "__main__":
    main()

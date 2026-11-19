#!/usr/bin/env python3
"""Download a GEDCOM snapshot of the current tree (artifact only; re-import would
duplicate people — the official reload path is the API loader in load.py)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"
OUT = Path("/workspace/gramps-web/build/data/collins_family_full.ged")


def token():
    user = pw = None
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and user is None:
            user = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and pw is None and user:
            pw = line.split("=", 1)[1].strip()
            break
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


def main():
    tok = token()
    req = urllib.request.Request(
        BASE + "/api/exporters/ged/file",
        headers={"Authorization": f"Bearer {tok}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
    OUT.write_bytes(raw)
    print(f"Wrote {OUT} ({len(raw)} bytes)")


if __name__ == "__main__":
    main()

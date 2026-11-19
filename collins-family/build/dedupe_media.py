#!/usr/bin/env python3
"""Merge media objects that hold byte-identical files (same checksum) into one.

Keeps the object linked to people (else the lowest id), moves citation and person
references onto it, and deletes the extra Gramps objects. Gramps Web does not delete
stored files on object delete, and original files are never touched. Idempotent.
"""
import collections, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api

def main():
    a = Api(); log = []
    media = a.get("/api/media/?pagesize=100000")
    people = a.get("/api/people/?pagesize=100000")
    cits = a.get("/api/citations/?pagesize=100000")
    linked = collections.Counter(r["ref"] for p in people for r in p.get("media_list") or [])
    groups = collections.defaultdict(list)
    for m in media:
        if m.get("checksum"):
            groups[m["checksum"]].append(m)
    for ck, ms in groups.items():
        if len(ms) < 2:
            continue
        ms.sort(key=lambda m: (-linked[m["handle"]], m["gramps_id"]))
        keep, extra = ms[0], ms[1:]
        xh = {m["handle"] for m in extra}
        for c in cits:
            refs = [r["ref"] for r in c.get("media_list") or []]
            if xh & set(refs):
                cc = a.get(f"/api/citations/{c['handle']}")
                new, seen = [], set()
                for r in cc["media_list"]:
                    ref = keep["handle"] if r["ref"] in xh else r["ref"]
                    if ref in seen:
                        continue
                    seen.add(ref); new.append(dict(r, ref=ref))
                cc["media_list"] = new
                a.put(f"/api/citations/{cc['handle']}", cc)
        for p in people:
            refs = [r["ref"] for r in p.get("media_list") or []]
            if xh & set(refs):
                pp = a.get(f"/api/people/{p['handle']}")
                new, seen = [], set()
                for r in pp["media_list"]:
                    ref = keep["handle"] if r["ref"] in xh else r["ref"]
                    if ref in seen:
                        continue
                    seen.add(ref); new.append(dict(r, ref=ref))
                pp["media_list"] = new
                a.put(f"/api/people/{pp['handle']}", pp)
        for m in extra:
            a.request("DELETE", f"/api/media/{m['handle']}")
            log.append(f"{m['gramps_id']} merged into {keep['gramps_id']} (same file bytes; '{(m.get('desc') or '')[:70]}')")
    Path(__file__).resolve().parent.joinpath("data", "dedupe_media_log.json").write_text(json.dumps(log, indent=1))
    print(f"dedupe_media: {len(log)} merged")
    for l in log:
        print(" ", l)

if __name__ == "__main__":
    main()

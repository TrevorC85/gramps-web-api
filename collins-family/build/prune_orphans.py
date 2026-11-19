#!/usr/bin/env python3
"""Round 5 (item 12): remove events the build left with no owner (no person/family links),
plus the build notes that hung only on those events. Events carrying a citation or media
are never deleted, only reported. Also reports (does not delete) other orphan objects."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api  # noqa: E402

OUT = Path(__file__).resolve().parent / "data" / "prune_report.json"


def main() -> int:
    api = Api()
    report = {"deleted_events": [], "deleted_notes": [], "kept_events_with_sources": [], "empty_events": []}
    evs = api.get("/api/events/?backlinks=1&keys=handle,gramps_id,type,description,date,place,citation_list,media_list,note_list,backlinks") or []
    for e in evs:
        owned = any((e.get("backlinks") or {}).values())
        dv = (e.get("date") or {}).get("dateval") or [0, 0, 0]
        empty = not (any(dv[:3]) or (e.get("date") or {}).get("text") or e.get("place") or e.get("description")
                     or e.get("citation_list") or e.get("note_list") or e.get("media_list"))
        if empty:
            report["empty_events"].append(e["gramps_id"])
        if owned:
            continue
        if e.get("citation_list") or e.get("media_list"):
            report["kept_events_with_sources"].append(e["gramps_id"])
            continue
        api.delete(f"/api/events/{e['handle']}")
        report["deleted_events"].append(f"{e['gramps_id']} {e.get('type')} {(e.get('description') or '')[:60]}")
        for nh in e.get("note_list") or []:
            n = api.get(f"/api/notes/{nh}?backlinks=1")
            if not any((n.get("backlinks") or {}).values()):
                api.delete(f"/api/notes/{nh}")
                report["deleted_notes"].append(n.get("gramps_id"))
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"orphan events deleted {len(report['deleted_events'])}, notes {len(report['deleted_notes'])}, "
          f"kept (have sources) {len(report['kept_events_with_sources'])}, empty events {len(report['empty_events'])}")
    if report["empty_events"]:
        print("EMPTY EVENTS (owned; not deleted):", report["empty_events"][:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

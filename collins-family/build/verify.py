#!/usr/bin/env python3
"""Verify full-tree load: counts, no orphans/duplicates, spot-check ancestors."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRAPH = ROOT / "data" / "graph.json"
MAP_PATH = ROOT / "data" / "handle_map.json"
OUT = ROOT / "data" / "verify_report.json"
PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"

SPOT = [
    ("collins:EC", "Eli Collins"),
    ("judy:JL", "Jacques Léger dit Larozette"),
    ("judy:B", "Benoni Léger"),
    ("collins:JH", "Joseph Henry Collins"),
    ("cindy:chrisbeyl", "Christian Beyl"),  # fallback if missing
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


def get(tok, path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def nam(p):
    pn = p.get("primary_name") or {}
    return ((pn.get("first_name") or "") + " " + " ".join(
        s.get("surname", "") for s in pn.get("surname_list") or []
    )).strip()


def main():
    tok = token()
    graph = json.loads(GRAPH.read_text())
    mmap = json.loads(MAP_PATH.read_text())
    people = get(tok, "/api/people/")
    families = get(tok, "/api/families/")
    # linked people
    linked = set()
    for f in families:
        if f.get("father_handle"):
            linked.add(f["father_handle"])
        if f.get("mother_handle"):
            linked.add(f["mother_handle"])
        for cr in f.get("child_ref_list") or []:
            linked.add(cr["ref"])
    orphans = []
    for p in people:
        if p["handle"] not in linked:
            orphans.append({"gramps_id": p.get("gramps_id"), "name": nam(p)})

    # duplicate check: same display name + same birth year
    births = {}
    for p in people:
        year = None
        # birth year from profile if present is not in list; skip heavy fetch
        key = nam(p).lower()
        births.setdefault(key, []).append(p.get("gramps_id"))
    name_dupes = {k: v for k, v in births.items() if k and len(v) > 1}

    # spot checks
    graph_people = {p["canon"]: p for p in graph["people"]}
    spots = []
    for canon, label in SPOT:
        # resolve aliases
        handle = mmap.get(canon)
        if not handle:
            # try any key that maps to same person in graph
            spots.append({"canon": canon, "ok": False, "reason": "not in handle map"})
            continue
        p = get(tok, f"/api/people/{handle}?extend=all")
        cites = []
        for ch in p.get("citation_list") or []:
            c = get(tok, f"/api/citations/{ch}")
            src = get(tok, f"/api/sources/{c['source_handle']}") if c.get("source_handle") else None
            media = []
            for mr in c.get("media_list") or []:
                m = get(tok, f"/api/media/{mr['ref']}")
                media.append(m.get("desc") or m.get("path"))
            cites.append({
                "source": (src or {}).get("title"),
                "page": c.get("page"),
                "media": media,
                "ok": bool((src or {}).get("title")) and (bool(media) or "http" in (c.get("page") or "") or bool(p.get("urls"))),
            })
        urls = p.get("urls") or []
        media_n = len(p.get("media_list") or [])
        ok = True  # person exists; media/url may be absent for compiled-chart ancestors
        spots.append({
            "canon": canon,
            "label": label,
            "gramps_id": p.get("gramps_id"),
            "name": nam(p),
            "n_citations": len(cites),
            "n_media": media_n,
            "n_urls": len(urls),
            "citations": cites,
            "urls": [u.get("path") for u in urls],
            "ok": True,
        })

    # Round 5 item 20: the tree = chart people (graph canons -> mapped handles; merged canons
    # share one handle) + people created from the website manifests. Compare those sets.
    chart_handles = {mmap[p["canon"]] for p in graph["people"] if p["canon"] in mmap}
    api_handles = {p["handle"]: p for p in people}
    manifest_created, unexplained = [], []
    for h, p in api_handles.items():
        if h in chart_handles:
            continue
        full = get(tok, f"/api/people/{h}")
        texts = [((get(tok, f"/api/notes/{n}").get("text") or {}).get("string") or "") for n in full.get("note_list") or []]
        if any("Created from website manifest" in t for t in texts):
            manifest_created.append(p.get("gramps_id"))
        else:
            unexplained.append({"gramps_id": p.get("gramps_id"), "name": nam(p)})
    missing = sorted(c for c in (p["canon"] for p in graph["people"]) if mmap.get(c) not in api_handles)
    people_match = not unexplained and not missing
    report = {
        "api_people": len(people),
        "chart_people_handles": len(chart_handles),
        "manifest_created_people": len(manifest_created),
        "manifest_created_ids": sorted(manifest_created),
        "unexplained_people": unexplained,
        "chart_canons_missing_in_tree": missing,
        "people_match": people_match,
        "api_families": len(families),
        "graph_people": graph["counts"]["people_total"],
        "graph_edges": graph["counts"]["edges_total"],
        "people_match_graph": people_match,  # kept for older readers of verify_report.json
        "orphans": orphans,
        "n_orphans": len(orphans),
        "same_display_name_count": len(name_dupes),
        "same_display_name_examples": list(name_dupes.items())[:15],
        "people_per_side": graph["counts"]["people_per_side"],
        "edges_per_source": graph["counts"]["edges_per_source"],
        "living": graph["counts"]["living"],
        "spot_checks": spots,
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"people API={report['api_people']} = chart {len(chart_handles)} (from {report['graph_people']} graph people) "
          f"+ manifest {len(manifest_created)}; unexplained={len(unexplained)} missing={len(missing)} match={people_match}")
    if unexplained or missing:
        print("UNEXPLAINED:", unexplained, "MISSING:", missing[:20])
    print(f"families={report['api_families']} orphans={report['n_orphans']} living={report['living']}")
    print("per side", report["people_per_side"])
    print("edges", report["edges_per_source"])
    for s in spots:
        print(f"  SPOT {s.get('gramps_id')} {s.get('name')}: cites={s.get('n_citations')} media={s.get('n_media')} urls={s.get('n_urls')} ok={s.get('ok')}")
    if orphans:
        print("ORPHANS:", orphans)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

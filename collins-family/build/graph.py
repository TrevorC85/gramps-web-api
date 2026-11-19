#!/usr/bin/env python3
"""Normalize extracted chart data into a merged person/family graph.

Merge only the known connecting identities (the 17 test people and Joy's
appearance on both the Collins and Koop charts). Within a single side,
people already have unique source keys. Facts are parsed from chart lines;
conflicts are recorded, not silently resolved.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_parse import map_confidence, norm_name, parse_fact_lines, split_person_name, normalize_place_parts, place_title

ROOT = Path(__file__).resolve().parent
IN = ROOT / "data" / "extracted.json"
OUT = ROOT / "data" / "graph.json"

# Existing Gramps IDs for the 17, mapped to source keys that must merge.
SEED_ALIASES = {
    "I0001": ["collins:T"],
    "I0002": ["collins:C"],
    "I0003": ["collins:J", "judy:J"],
    "I0004": ["collins:JW"],
    "I0005": ["collins:JOY", "koop:joy"],
    "I0006": ["judy:K"],
    "I0007": ["judy:1"],
    "I0008": ["cindy:cindy"],
    "I0009": ["cindy:mike"],
    "I0010": ["cindy:judy"],
    "I0011": ["cindy:donna"],
    "I0012": ["cindy:august"],
    "I0013": ["cindy:paul"],
    "I0014": ["cindy:johnjr"],
    "I0015": ["cindy:maryf"],
    "I0016": [],  # James (child) — not on the published charts
    "I0017": [],  # Paul (child)
}

# Additional same-person aliases across charts (not in the 17).
EXTRA_ALIASES = [
    # Same person, slightly different labels on the Collins pedigree vs family-group pages
    ["collins:ER", "collins:fam_m9"],  # Elizabeth Victoria "Lillian" Rice
    ["collins:TM", "collins:fam_m7"],  # Therese (Teresa) B. Mulligan
    ["collins:WC", "collins:fam_c5_0"],  # William J. Francis Collins
    ["collins:EC", "collins:fam_c0_5"],  # Eli Collins
    ["collins:fam_c3_0", "collins:fam_c4_2"],  # niece Ida Collins = Ida Philomena Collins (probable)
    # Round 5 (Trevor approved 18 Nov 2026). Survivors in Gramps: I0813 and I0797.
    # Ellen, daughter of Isaac Hargreaves x Rachel Smith (1841 Halifax, 1850/1860/1870 Philadelphia
    # households; FAMILY_RECORD 38.3/39.2/40.9) = Ellen Hargraves, wife of Eli Collins.
    ["collins:fam_c6_3", "collins:fam_m4"],
    # Hannah, daughter of Jonathan Collins (FS NFHY-G8F names father Jonathan; b. c1826 Rastrick,
    # d. 9 Jan 1911 Phila.) = Hannah Collins, wife of John Gledhill (m. 19 Feb 1849 Elland).
    ["collins:fam_c0_3", "collins:fam_m1"],
    # Round 5 item 19: Jacques Lussier x Catherine Clérice drawn twice (double descent; the chart's own
    # line "= Jacques Lussier on the Harel line"; FAMILY_RECORD 61.3). Survivors I0555 / I0556.
    ["judy:JLu_40", "judy:JLu_41"],
    ["judy:CCl_40", "judy:CCl_41"],
]

# Chart rows that are household members, not children. Dropped (child canon, parent canon).
# Ida Philomena Collins was a NIECE in the Loftus Sykes household in 1880 (FS MWJN-J2J);
# her parents are Eli Collins x Ellen Hargraves (1870 Hargraves household; FAMILY_RECORD 40.9).
DROP_EDGES = {
    ("collins:fam_c3_0", "collins:fam_f3"),
    ("collins:fam_c3_0", "collins:fam_m3"),
}

# Contemporary living people named as such on the charts, plus the existing 17.
KNOWN_LIVING_KEYS = {
    "collins:T", "collins:C", "collins:J", "collins:JOY",
    "judy:J", "judy:1",
    "koop:joy",
    "cindy:cindy", "cindy:mike", "cindy:judy", "cindy:donna",
}
# Living siblings of Chuck named on the Collins family-group page
LIVING_NAME_MARKERS = {
    "jack (clifford john)",
    "clifford john",
}


def merge_key_groups(extracted: dict) -> dict[str, str]:
    """Map every source key -> canon key."""
    all_keys = []
    for side in ("judy", "collins", "koop", "cindy"):
        for p in extracted[side]["people"]:
            all_keys.append(p["key"])
    key_to_canon: dict[str, str] = {k: k for k in all_keys}

    def union(a: str, b: str):
        ca, cb = key_to_canon[a], key_to_canon[b]
        if ca == cb:
            return
        # prefer existing I00xx as canon if we later assign it; for now pick stable min
        keep, drop = (ca, cb) if ca <= cb else (cb, ca)
        for k, v in list(key_to_canon.items()):
            if v == drop:
                key_to_canon[k] = keep

    for gid, keys in SEED_ALIASES.items():
        usable = [k for k in keys if k in key_to_canon]
        for i in range(1, len(usable)):
            union(usable[0], usable[i])
    for group in EXTRA_ALIASES:
        usable = [k for k in group if k in key_to_canon]
        for i in range(1, len(usable)):
            union(usable[0], usable[i])

    # Assign human canon ids: use existing I00xx when seeded, else first source key
    # Remap seeded groups to I00xx
    for gid, keys in SEED_ALIASES.items():
        members = [k for k in keys if k in key_to_canon]
        if not members:
            # I0016/I0017 have no chart key — create a canon later
            continue
        group = key_to_canon[members[0]]
        for k, v in list(key_to_canon.items()):
            if v == group:
                key_to_canon[k] = gid
    return key_to_canon


def pick_display_name(people: list[dict]) -> str:
    """Prefer a name with a surname when merging, unless the person is living."""
    # longest raw name often has more information
    return max((p["name"] for p in people), key=lambda n: (len(n.split()), len(n)))


def is_living(canon_keys: list[str], parsed: dict, display: str) -> bool:
    if parsed.get("living"):
        return True
    if any(k in KNOWN_LIVING_KEYS for k in canon_keys):
        return True
    # death event with a year => not living
    for ev in parsed.get("events") or []:
        if ev["type"] == "Death" and ev.get("date") and ev["date"].get("year"):
            return False
    nn = norm_name(display)
    if nn in LIVING_NAME_MARKERS or "living" in nn:
        return True
    # first-name-only contemporaries already covered by KNOWN_LIVING_KEYS
    return False


def main() -> int:
    extracted = json.loads(IN.read_text())
    key_to_canon = merge_key_groups(extracted)

    # index source people
    by_key = {}
    for side in ("judy", "collins", "koop", "cindy"):
        for p in extracted[side]["people"]:
            by_key[p["key"]] = p

    groups: dict[str, list[str]] = defaultdict(list)
    for k, c in key_to_canon.items():
        groups[c].append(k)

    # Add the two children who are not on the charts
    for gid, name, lines in (
        ("I0016", "James", ["living", "son of Trevor and Cindy (3-generation test; names only)"]),
        ("I0017", "Paul", ["living", "son of Trevor and Cindy (3-generation test; names only)"]),
    ):
        groups[gid] = groups.get(gid, [])
        # synthetic source person
        by_key[f"existing:{gid}"] = {
            "source": "existing",
            "key": f"existing:{gid}",
            "src_id": gid,
            "name": name,
            "lines": lines,
            "chart_note": "Present in the 3-generation test; not shown on the published charts.",
            "side": "connecting",
        }
        groups[gid].append(f"existing:{gid}")
        key_to_canon[f"existing:{gid}"] = gid

    people_out = []
    for canon, keys in groups.items():
        src_people = [by_key[k] for k in keys if k in by_key]
        if not src_people:
            continue
        display = pick_display_name(src_people)
        all_lines = []
        sides = []
        notes = []
        for p in src_people:
            sides.append(p.get("side") or p.get("source"))
            for ln in p.get("lines") or []:
                if ln not in all_lines:
                    all_lines.append(ln)
            if p.get("chart_note") and p["chart_note"] not in notes:
                notes.append(p["chart_note"])
        parsed = parse_fact_lines(all_lines)
        living = is_living(keys, parsed, display)
        # "4 living (1900)" is a census count, not a living-person flag.
        # Only treat a line as a living flag when "living" is not preceded by a number.
        if any(re.search(r"(?<!\d\s)(?<!\d)living\b", ln or "", re.I) and not re.search(r"\d\s+living\b", ln or "", re.I) for ln in all_lines):
            living = True
        name_parts = split_person_name(display)
        # Collect places from events
        places = []
        for ev in parsed["events"]:
            if ev.get("place_text"):
                parts = normalize_place_parts(ev["place_text"])
                ev["place_parts"] = parts
                ev["place_title"] = place_title(parts) if parts else ev["place_text"]
                if ev["place_title"] and ev["place_title"] not in places:
                    places.append(ev["place_title"])

        people_out.append({
            "canon": canon,
            "source_keys": keys,
            "sides": sorted(set(sides)),
            "display_name": display,
            "name_parts": name_parts,
            "living": living,
            "lines": all_lines,
            "events": parsed["events"],
            "notes": notes + parsed["notes"],
            "conflicts": parsed["conflicts"],
            "urls": parsed["urls"],
            "existing_gramps_id": canon if canon.startswith("I00") else None,
        })

    # Edges, remapped, deduped
    edges_out = []
    seen_e = set()
    for side in ("judy", "collins", "koop", "cindy"):
        for e in extracted[side]["edges"]:
            ck = key_to_canon.get(e["child"])
            pk = key_to_canon.get(e["parent"])
            if not ck or not pk or ck == pk or (ck, pk) in DROP_EDGES:
                continue
            tag, basis = map_confidence(e.get("confidence"), e.get("basis"), e.get("style"))
            sig = (ck, pk, tag, basis)
            if sig in seen_e:
                continue
            seen_e.add(sig)
            # if same child-parent already with different conf, keep both as notes later;
            # prefer the first, record conflict if tags differ
            edges_out.append({
                "child": ck,
                "parent": pk,
                "tag": tag,
                "basis": basis,
                "raw_confidence": e.get("confidence"),
                "raw_basis": e.get("basis"),
                "style": e.get("style") or "",
                "source": e.get("source"),
                "relation": e.get("relation") or "",
            })

    # Existing children James & Paul of Trevor x Cindy
    for child in ("I0016", "I0017"):
        for parent in ("I0001", "I0008"):
            edges_out.append({
                "child": child, "parent": parent,
                "tag": "confirmed (family)", "basis": "family (3-generation test; names only)",
                "raw_confidence": "confirmed", "raw_basis": "family",
                "style": "", "source": "existing",
            })

    # Chart-noted possible spouse who was not given a parent-child link (would otherwise be an orphan).
    family_hints = [
        {
            "partners": ["cindy:thomasfletcher", "cindy:margaretfletcher"],
            "tag": "needs confirmation",
            "basis": "1870 household (wife position); may be a relative, boarder, or first wife; maternity of John E. needs confirmation",
            "source": "cindy",
        }
    ]

    # Detect conflicting confidence on the same child-parent pair
    pair = defaultdict(list)
    for e in edges_out:
        pair[(e["child"], e["parent"])].append(e)
    for (c, p), elist in pair.items():
        tags = {e["tag"] for e in elist}
        if len(tags) > 1:
            note = f"Conflicting link confidence for {c} <- {p}: " + "; ".join(
                f"{e['tag']} ({e['basis'] or e['source']})" for e in elist
            )
            # attach to child
            for person in people_out:
                if person["canon"] == c:
                    person["conflicts"].append(note)

    # Families: group by {father, mother} among each child's parents.
    # We don't always know sex. Heuristic: for a child, if exactly 2 parents,
    # treat first as father and second as mother when the source listed father first
    # (judy/collins add() order is father then mother; cindy links are unordered!).
    # Safer: store parent pairs without inventing sex when unknown, but Gramps
    # families need father/mother handles. We'll assign later in load using
    # name heuristics (and existing 17 sexes).

    # Count unique people / edges per side (a person can belong to multiple sides)
    side_people = defaultdict(set)
    side_edges = defaultdict(int)
    for p in people_out:
        for s in p["sides"]:
            side_people[s].add(p["canon"])
    for e in edges_out:
        side_edges[e["source"]] += 1

    # Orphans: no edge as child or parent
    linked = set()
    for e in edges_out:
        linked.add(e["child"])
        linked.add(e["parent"])
    orphans = [p["canon"] for p in people_out if p["canon"] not in linked]

    graph = {
        "people": people_out,
        "edges": edges_out,
        "family_hints": family_hints,
        "counts": {
            "people_total": len(people_out),
            "edges_total": len(edges_out),
            "people_per_side": {s: len(v) for s, v in side_people.items()},
            "edges_per_source": dict(side_edges),
            "living": sum(1 for p in people_out if p["living"]),
            "orphans": orphans,
            "conflicts": sum(1 for p in people_out if p["conflicts"]),
        },
        "key_to_canon": key_to_canon,
    }
    OUT.write_text(json.dumps(graph, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT}")
    print("counts:", json.dumps(graph["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write /workspace/gramps-web/shared/people_index.csv (chmod 644) after every build.

Columns: gramps_id, side, name, birth, death, parents, spouses, living
Living people are listed by first name only (no surname).
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api  # noqa: E402

OUT = Path("/workspace/gramps-web/shared/people_index.csv")
GRAPH = Path("/workspace/gramps-web/build/data/graph.json")
MAP_PATH = Path("/workspace/gramps-web/build/data/handle_map.json")


def display_name(person: dict, living: bool) -> str:
    pn = person.get("primary_name") or {}
    first = (pn.get("first_name") or "").strip()
    # Prefer the given/call name; drop leaked surnames for living people.
    if living:
        # If first_name already holds "Charles \"Chip\" (Chuck)", keep it;
        # strip any surname that slipped onto the end.
        return first or "Unknown"
    surnames = " ".join(
        s.get("surname", "") for s in (pn.get("surname_list") or []) if s.get("surname")
    ).strip()
    suffix = (pn.get("suffix") or "").strip()
    parts = [p for p in (first, surnames, suffix) if p]
    return " ".join(parts).strip() or "Unknown"


def fmt_date(profile_event: dict | None) -> str:
    if not profile_event:
        return ""
    return (profile_event.get("date") or "").strip()


def person_label(gid: str, name: str, living: bool) -> str:
    if not gid:
        return ""
    return f"{gid} {name}".strip()


def main():
    api = Api()
    people = api.get("/api/people/?profile=self") or []
    families = api.get("/api/families/?profile=self") or []

    graph_by_gid = {}
    if GRAPH.exists():
        graph = json.loads(GRAPH.read_text())
        mmap = {}
        if MAP_PATH.exists():
            mmap = json.loads(MAP_PATH.read_text())
        # canon -> handle, invert via API handles
        handle_to_gid = {p["handle"]: p["gramps_id"] for p in people}
        for p in graph["people"]:
            gid = p.get("existing_gramps_id")
            if not gid:
                h = mmap.get(p["canon"])
                # mmap values are handles
                if h:
                    gid = handle_to_gid.get(h)
            if not gid and p["canon"].startswith("I") and p["canon"][1:].isdigit():
                gid = p["canon"]
            if gid:
                graph_by_gid[gid] = p

    by_handle = {p["handle"]: p for p in people}
    living_of = {}
    for p in people:
        g = graph_by_gid.get(p["gramps_id"])
        living = bool(p.get("private")) or bool(g and g.get("living"))
        # private is used for living people in this tree; keep graph living as source of truth when present
        if g is not None:
            living = bool(g.get("living"))
        living_of[p["handle"]] = living
        living_of[p["gramps_id"]] = living

    # birth/death from family profiles (covers everyone in a family)
    birth = {}
    death = {}

    def take_bd(prof_person: dict):
        if not prof_person:
            return
        gid = prof_person.get("gramps_id")
        if not gid:
            return
        b = fmt_date(prof_person.get("birth"))
        d = fmt_date(prof_person.get("death"))
        if b and gid not in birth:
            birth[gid] = b
        if d and gid not in death:
            death[gid] = d

    parents_of = {p["gramps_id"]: [] for p in people}  # list of (gid, name)
    spouses_of = {p["gramps_id"]: [] for p in people}

    def name_of(gid: str, fallback_profile=None) -> str:
        living = living_of.get(gid, False)
        if gid and any(x["gramps_id"] == gid for x in people):
            p = next(x for x in people if x["gramps_id"] == gid)
            return display_name(p, living)
        if fallback_profile:
            if living:
                return (fallback_profile.get("name_given") or "").strip()
            given = (fallback_profile.get("name_given") or "").strip()
            sur = (fallback_profile.get("name_surname") or "").strip()
            suf = (fallback_profile.get("name_suffix") or "").strip()
            return " ".join(x for x in (given, sur, suf) if x).strip()
        return ""

    for f in families:
        prof = f.get("profile") or {}
        take_bd(prof.get("father"))
        take_bd(prof.get("mother"))
        for ch in prof.get("children") or []:
            take_bd(ch)
        father = prof.get("father") or {}
        mother = prof.get("mother") or {}
        fg, mg = father.get("gramps_id"), mother.get("gramps_id")
        fn = name_of(fg, father) if fg else ""
        mn = name_of(mg, mother) if mg else ""
        # spouses
        if fg and mg:
            spouses_of.setdefault(fg, [])
            spouses_of.setdefault(mg, [])
            if not any(s[0] == mg for s in spouses_of[fg]):
                spouses_of[fg].append((mg, mn))
            if not any(s[0] == fg for s in spouses_of[mg]):
                spouses_of[mg].append((fg, fn))
        # parents of children
        for ch in prof.get("children") or []:
            cg = ch.get("gramps_id")
            if not cg:
                continue
            parents_of.setdefault(cg, [])
            if fg and not any(s[0] == fg for s in parents_of[cg]):
                parents_of[cg].append((fg, fn))
            if mg and not any(s[0] == mg for s in parents_of[cg]):
                parents_of[cg].append((mg, mn))

    # Fill any missing birth/death from person profile
    for p in people:
        prof = p.get("profile") or {}
        take_bd({"gramps_id": p["gramps_id"], "birth": prof.get("birth") or {}, "death": prof.get("death") or {}})

    rows = []
    for p in people:
        gid = p["gramps_id"]
        g = graph_by_gid.get(gid)
        sides = (g.get("sides") if g else None) or []
        side = ";".join(sides)
        living = bool(living_of.get(gid))
        name = display_name(p, living)
        par = "; ".join(f"{i} {n}".strip() for i, n in parents_of.get(gid) or [])
        spo = "; ".join(f"{i} {n}".strip() for i, n in spouses_of.get(gid) or [])
        rows.append({
            "gramps_id": gid,
            "side": side,
            "name": name,
            "birth": birth.get(gid, ""),
            "death": death.get(gid, ""),
            "parents": par,
            "spouses": spo,
            "living": "true" if living else "false",
        })

    # Sort by gramps_id numeric then lexical
    def sort_key(r):
        gid = r["gramps_id"]
        if gid[:1] == "I" and gid[1:].isdigit():
            return (0, int(gid[1:]), gid)
        return (1, 0, gid)

    rows.sort(key=sort_key)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["gramps_id", "side", "name", "birth", "death", "parents", "spouses", "living"],
            extrasaction="ignore",
        )
        w.writeheader()
        w.writerows(rows)
    os.chmod(OUT, 0o644)
    n_live = sum(1 for r in rows if r["living"] == "true")
    print(f"Wrote {OUT} ({len(rows)} people, {n_live} living, mode 644)")


if __name__ == "__main__":
    main()

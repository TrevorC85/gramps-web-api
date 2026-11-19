#!/usr/bin/env python3
"""Apply Genealogy Wife rulings of 18 Nov 2026 to the live tree (idempotent)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api, first_new_handle  # noqa: E402

RULING = (
    "Genealogy Wife ruling, 18 Nov 2026 (America/Chicago): "
)

TAG_SPEC = [
    ("confirmed (record)", "#1f5fa8"),
    ("confirmed (index)", "#0d47a6"),
    ("confirmed (family)", "#2e7d4f"),
    ("confirmed (chart)", "#3d6f9c"),
    ("probable (strong)", "#b35a00"),
    ("probable", "#d08a00"),
    ("needs confirmation", "#6b6b6b"),
    ("possible", "#8e44ad"),
]


def ensure_tags(api: Api) -> dict:
    tags = {t["name"]: t for t in (api.get("/api/tags/") or [])}
    for name, color in TAG_SPEC:
        if name in tags:
            continue
        resp = api.post("/api/tags/", {"_class": "Tag", "name": name, "color": color})
        h = first_new_handle(resp)
        tags[name] = {"handle": h, "name": name, "color": color}
        print("TAG created", name)
    return {n: t["handle"] for n, t in tags.items()}


def ensure_note(api: Api, text: str, typ="General", private=False) -> str:
    # reuse if an identical note already exists on... we just create; callers de-dupe by text on the object
    resp = api.post("/api/notes/", {
        "_class": "Note", "type": typ, "private": private, "format": 0,
        "text": {"string": text, "tags": []},
    })
    return first_new_handle(resp)


def has_note_starting(api: Api, obj, prefix: str) -> bool:
    for nh in obj.get("note_list") or []:
        n = api.get(f"/api/notes/{nh}")
        s = ((n.get("text") or {}).get("string") or "")
        if s.startswith(prefix) or prefix in s:
            return True
    return False


def set_family_tags(fam, handles, add=(), remove=()):
    tags = list(fam.get("tag_list") or [])
    changed = False
    for name in add:
        h = handles[name]
        if h not in tags:
            tags.append(h)
            changed = True
    for name in remove:
        h = handles.get(name)
        if h and h in tags:
            tags.remove(h)
            changed = True
    fam["tag_list"] = tags
    return changed


def main():
    api = Api()
    handles = ensure_tags(api)
    families = {f["gramps_id"]: f for f in (api.get("/api/families/") or [])}

    # F0004 Trevor x Cindy: 2010 marriage confirmed (record);
    # children confirmed (family) already.
    f = api.get(f"/api/families/{families['F0004']['handle']}")
    changed = set_family_tags(f, handles, add=["confirmed (record)", "confirmed (family)"])
    text = (RULING + "Cindy–Trevor 2010 marriage is confirmed (record) "
            "(Washington Digital Archives application, certificate, and statewide index).")
    if not has_note_starting(api, f, "Genealogy Wife ruling") or not any(
        text[:40] in ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
        for nh in f.get("note_list") or []
    ):
        # more precise: add if this exact ruling sentence is absent
        already = False
        for nh in f.get("note_list") or []:
            s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
            if "Cindy–Trevor 2010 marriage is confirmed (record)" in s:
                already = True
                break
        if not already:
            f.setdefault("note_list", []).append(ensure_note(api, text, typ="General", private=True))
            changed = True
    if changed:
        api.put(f"/api/families/{f['handle']}", f)
        print("UPDATED F0004 Trevor x Cindy")
    else:
        print("OK F0004")

    # F0005 Mike x Judy: 1988 marriage confirmed (record);
    # Cindy's parent links confirmed (family)
    f = api.get(f"/api/families/{families['F0005']['handle']}")
    changed = set_family_tags(f, handles, add=["confirmed (record)", "confirmed (family)"])
    text = (RULING + "Mike–Judy 17 March 1988 marriage is confirmed (record) "
            "(King County / Washington DSHS certificate). "
            "Cindy's links to Mike and Judy are confirmed (family).")
    already = False
    for nh in f.get("note_list") or []:
        s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
        if "Mike–Judy 17 March 1988 marriage is confirmed (record)" in s:
            already = True
            break
    if not already:
        f.setdefault("note_list", []).append(ensure_note(api, text, typ="General", private=True))
        changed = True
    # child-ref notes for Cindy
    people = {p["handle"]: p for p in (api.get("/api/people/?keys=handle,gramps_id") or [])}
    for cr in f.get("child_ref_list") or []:
        gid = (people.get(cr["ref"]) or {}).get("gramps_id")
        if gid != "I0008":
            continue
        cr_notes_ok = False
        for nh in cr.get("note_list") or []:
            s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
            if "Cindy's links to Mike and Judy are confirmed (family)" in s:
                cr_notes_ok = True
        if not cr_notes_ok:
            nh = ensure_note(
                api,
                "Parent-child link confidence:\n- father: confirmed (family) — "
                "Genealogy Wife ruling 18 Nov 2026: Cindy's links to Mike and Judy are confirmed (family)\n"
                "- mother: confirmed (family) — same ruling",
                typ="Link",
                private=True,
            )
            cr.setdefault("note_list", []).append(nh)
            changed = True
    if changed:
        api.put(f"/api/families/{f['handle']}", f)
        print("UPDATED F0005 Mike x Judy / Cindy")
    else:
        print("OK F0005")

    # F0006 August x Donna -> Mike: biological, probable (family knowledge, not proven)
    f = api.get(f"/api/families/{families['F0006']['handle']}")
    changed = set_family_tags(f, handles, add=["probable"], remove=["needs confirmation", "confirmed (record)"])
    for cr in f.get("child_ref_list") or []:
        gid = (people.get(cr["ref"]) or {}).get("gramps_id")
        if gid != "I0009":
            continue
        if cr.get("frel") != "Birth" or cr.get("mrel") != "Birth":
            cr["frel"] = "Birth"
            cr["mrel"] = "Birth"
            changed = True
        note_txt = (
            "Parent-child link confidence:\n"
            "- father: probable — Genealogy Wife ruling 18 Nov 2026: August J. Beyl is Mike's "
            "biological father; probable (family knowledge, not proven). Paternity has not been found in a record.\n"
            "- mother: confirmed (record) or confirmed (family) as previously documented."
        )
        has = False
        for nh in cr.get("note_list") or []:
            s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
            if "August J. Beyl is Mike's biological father" in s:
                has = True
        if not has:
            cr.setdefault("note_list", []).append(ensure_note(api, note_txt, typ="Link", private=True))
            changed = True
    fam_txt = (RULING + "August J. Beyl is Mike's biological father; confidence is probable "
               "(family knowledge, not proven). No marriage between August and Donna is documented.")
    has = False
    for nh in f.get("note_list") or []:
        s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
        if "August J. Beyl is Mike's biological father" in s:
            has = True
    if not has:
        f.setdefault("note_list", []).append(ensure_note(api, fam_txt, typ="General", private=True))
        changed = True
    if changed:
        api.put(f"/api/families/{f['handle']}", f)
        print("UPDATED F0006 August x Donna / Mike (biological, probable)")
    else:
        print("OK F0006")

    # F0007 Paul x Donna -> Mike: adoptive father
    f = api.get(f"/api/families/{families['F0007']['handle']}")
    changed = set_family_tags(f, handles, add=["confirmed (record)"])
    for cr in f.get("child_ref_list") or []:
        gid = (people.get(cr["ref"]) or {}).get("gramps_id")
        if gid != "I0009":
            continue
        if cr.get("frel") != "Adopted":
            cr["frel"] = "Adopted"
            changed = True
        if cr.get("mrel") != "Birth":
            cr["mrel"] = "Birth"
            changed = True
        note_txt = (
            "Parent-child link confidence:\n"
            "- father: confirmed (record) — Genealogy Wife ruling 18 Nov 2026: Paul E. Libengood "
            "is Mike's adoptive father (named on the 17 March 1988 marriage certificate).\n"
            "- mother: Birth (Donna)."
        )
        has = False
        for nh in cr.get("note_list") or []:
            s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
            if "Paul E. Libengood is Mike's adoptive father" in s:
                has = True
        if not has:
            cr.setdefault("note_list", []).append(ensure_note(api, note_txt, typ="Link", private=True))
            changed = True
    fam_txt = (RULING + "Paul E. Libengood is Mike's adoptive father.")
    has = False
    for nh in f.get("note_list") or []:
        s = ((api.get(f"/api/notes/{nh}").get("text") or {}).get("string") or "")
        if "Paul E. Libengood is Mike's adoptive father" in s:
            has = True
    if not has:
        f.setdefault("note_list", []).append(ensure_note(api, fam_txt, typ="General", private=True))
        changed = True
    if changed:
        api.put(f"/api/families/{f['handle']}", f)
        print("UPDATED F0007 Paul x Donna / Mike (adoptive)")
    else:
        print("OK F0007")


# Alternate names from Genealogy Wife's chat-message reconciliation
# (CINDY_FAMILY_RECORD.md, "Chat-message reconciliation", 18 Nov 2026).
# Record names stay primary; family names are added as "Also Known As".
# Deceased people only (living people stay first-name-only).
ALT_NAMES = [
    # (gramps_id, expected primary first name, first, surname, suffix)
    ("I0878", "Harry E.", "Harry James", "Townsend", ""),       # family name (15 Nov list)
    ("I0879", "Maude Rea", "Maude Rose", "McCracken", ""),      # family name (15 Nov list: "McCraken")
    ("I0885", "Sophia Jane", "Jennie", "Bollinger", ""),         # 1880 census "Jennie"
    ("I0885", "Sophia Jane", "Jeannette", "Bollinger", ""),      # probable formal form (Round 89)
    ("I0014", "John Donald", "John Donald", "Schneider", "Jr."),  # headstone / record name
]


def ensure_alt_names(api: Api) -> None:
    people = {p["gramps_id"]: p for p in (api.get("/api/people/?keys=handle,gramps_id") or [])}
    for gid, expect_first, first, surname, suffix in ALT_NAMES:
        if gid not in people:
            print("ALT-NAME skip (missing)", gid)
            continue
        person = api.get(f"/api/people/{people[gid]['handle']}")
        if (person.get("primary_name") or {}).get("first_name") != expect_first:
            print("ALT-NAME skip (unexpected primary name)", gid)
            continue
        have = {
            ((n.get("first_name") or ""), " ".join(s.get("surname", "") for s in n.get("surname_list") or []),
             (n.get("suffix") or ""))
            for n in person.get("alternate_names") or []
        }
        if (first, surname, suffix) in have:
            continue
        person.setdefault("alternate_names", []).append({
            "_class": "Name", "first_name": first, "suffix": suffix, "title": "", "call": "", "nick": "",
            "famnick": "", "type": "Also Known As", "private": False,
            "surname_list": [{"_class": "Surname", "surname": surname, "prefix": "", "primary": True,
                              "connector": "", "origintype": ""}],
            "citation_list": [], "note_list": [], "group_as": "", "sort_as": 0, "display_as": 0,
        })
        api.put(f"/api/people/{person['handle']}", person)
        print("ALT-NAME added", gid, first, surname, suffix)


if __name__ == "__main__":
    main()
    ensure_alt_names(Api())

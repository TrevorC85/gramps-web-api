#!/usr/bin/env python3
"""Ensure Families + Marriage events from notes, chart lines, family records,
and existing parent pairs. Do not invent spouses: only link a named spouse to
an existing person when name + context clearly fit; otherwise list candidates.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_api import Api, first_new_handle  # noqa: E402
from lib_parse import EVENT_PREFIX, split_fact_clauses, parse_date_fragment  # noqa: E402

GRAPH = Path("/workspace/gramps-web/build/data/graph.json")
OUT = Path("/workspace/gramps-web/build/data/marriage_report.json")
FAMILY_RECORDS = [
    Path("/workspace/genealogy/FAMILY_RECORD.md"),
    Path("/workspace/genealogy-cindy/CINDY_FAMILY_RECORD.md"),
]

NAME_HEAD = re.compile(
    r"^(?P<name>[A-ZÉÈÊËÎÏÔŒÜÁÀÂÄÇ][A-Za-zÉéÈèÊêËëÎîÏïÔôŒœÜüÁáÀàÂâÄäÇç .\"“”'\-]{0,50}?)(?=\s+\d|\s+abt\b|\s+about\b|\s+c(?:irca)?\.?\s|\s+in\s|\s*$|\s*,|\s+\()",
    re.U,
)


# Same person entered twice from different sources; kept as separate people on purpose
# (no merge), but the duplicate must never get its own marriage to the same spouse.
# I0824 "Ellen" (b. c1831, daughter of Isaac Hargreaves; collins:fam_c6_3) is the same woman as
# I0813 Ellen Hargraves, whose marriage to Eli Collins (I0768) is F0401.
# Merged into I0813 on 18 Nov 2026 (round 5, item 3); graph.py aliases the two chart keys.
KNOWN_DUPLICATE_PEOPLE: dict[str, str] = {}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def first_token(s: str) -> str:
    s = fold(s)
    # drop quoted nicknames for matching? keep first word
    s = re.sub(r"[\"“”'].*?[\"“”']", " ", s)
    parts = s.split()
    return parts[0] if parts else ""


def display_name(p: dict) -> str:
    pn = p.get("primary_name") or {}
    first = (pn.get("first_name") or "").strip()
    sur = " ".join(s.get("surname", "") for s in (pn.get("surname_list") or []) if s.get("surname"))
    return " ".join(x for x in (first, sur) if x).strip()


def given_name(p: dict) -> str:
    return ((p.get("primary_name") or {}).get("first_name") or "").strip()


def birth_year_of(p: dict) -> int | None:
    prof = p.get("profile") or {}
    d = (prof.get("birth") or {}).get("date") or ""
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", d)
    return int(m.group(1)) if m else None


def parse_marriage_clause(clause: str) -> dict | None:
    """Return {spouse_name, date, place, raw} or None if this is not a marriage clause."""
    raw = clause.strip().strip(" ,;")
    # ignore non-marriage 'm.' like 'from'
    m = re.match(
        r"^(?:(?:our line:\s*)?(?:m(?:\.|arr(?:ied|\.)?)\s+of\s+)?m(?:\.|arr(?:ied|\.)?)\s+|married\s+)(?:to\s+)?(?P<body>.+)$",
        raw,
        re.I,
    )
    if not m:
        return None
    body = m.group("body").strip()
    # "m. 1894 Phila" — no spouse
    spouse = ""
    rest = body
    # if body starts with a year or a day, no name
    if not re.match(r"^(?:\d|abt|about|circa|c\.|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)", body, re.I):
        hm = NAME_HEAD.match(body)
        if hm:
            spouse = hm.group("name").strip(" ,.")
            # reject non-names
            if fold(spouse) in {"in", "at", "the", "a", "an", "by", "of"}:
                spouse = ""
            else:
                rest = body[hm.end():].strip(" ,;")
    date = parse_date_fragment(rest) or parse_date_fragment(body)
    date_text = rest
    place = ""
    if date and date.get("year"):
        year = str(date["year"])
        idx = rest.find(year)
        if idx >= 0:
            after = rest[idx + len(year):].strip(" ,;:-")
            after = re.sub(r"^\((estimated|abt\.?|about|circa)\)\s*", "", after, flags=re.I)
            place_part, _, _c = after.partition("(")
            place = place_part.strip(" ,;:-")
            date_text = rest[: idx + len(year)].strip(" ,;:-")
    return {
        "spouse_name": spouse,
        "date": date,
        "date_text": (date_text or rest or "").strip(),
        "place": place,
        "raw": raw,
    }


def collect_mentions_from_texts(texts: list[str], src: str) -> list[dict]:
    mentions = []
    for text in texts:
        if not text:
            continue
        # also split chart lines already joined by |
        chunks = []
        for line in re.split(r"[\n|]", text):
            chunks.extend(split_fact_clauses(line))
        for clause in chunks:
            parsed = parse_marriage_clause(clause.strip())
            if parsed:
                parsed["source"] = src
                mentions.append(parsed)
    return mentions


def confidence_from_raw(raw: str) -> str:
    r = raw.lower()
    if "confirmed (record)" in r:
        return "confirmed (record)"
    if "confirmed (index)" in r or "confirmed (index)" in r:
        return "confirmed (index)"
    if re.search(r"\bconfirmed \(index\)|\bconfirmed \(indx\)|\bindex\)", r) and "confirmed" in r:
        return "confirmed (index)"
    if "confirmed (family)" in r:
        return "confirmed (family)"
    if "probable (strong)" in r or "p/v. strong" in r or "very strong" in r:
        return "probable (strong)"
    if "needs confirmation" in r:
        return "needs confirmation"
    if "possible" in r:
        return "possible"
    if "probable" in r or "prob." in r:
        return "probable"
    if re.search(r"\bconfirmed\b", r):
        return "confirmed (record)" if "cert" in r or "record" in r else "confirmed (index)"
    return "needs confirmation"


class Marriages:
    def __init__(self, api: Api):
        self.api = api
        self.people = api.get("/api/people/?profile=self") or []
        self.by_gid = {p["gramps_id"]: p for p in self.people}
        self.by_handle = {p["handle"]: p for p in self.people}
        self.families = api.get("/api/families/?profile=self") or []
        self.tags = {t["name"]: t["handle"] for t in (api.get("/api/tags/") or [])}
        self.spouses_of = defaultdict(set)  # handle -> set of spouse handles
        self.family_of_pair = {}
        for f in self.families:
            a, b = f.get("father_handle") or "", f.get("mother_handle") or ""
            if a and b:
                self.spouses_of[a].add(b)
                self.spouses_of[b].add(a)
                self.family_of_pair[frozenset({a, b})] = f
            elif a:
                self.spouses_of[a].add("")
            elif b:
                self.spouses_of[b].add("")
        self.stats = {
            "couples_already_present": 0,
            "families_created": 0,
            "marriage_events_added": 0,
            "marriage_events_with_dates": 0,
            "mentions_parsed": 0,
            "matched_to_existing": 0,
            "ambiguous": 0,
            "unmatched_mentions": 0,
        }
        self.ambiguous = []
        self.unmatched = []
        self.added = []

    def family_has_marriage(self, fam, year=None, typ="Marriage") -> bool:
        fam = self.api.get(f"/api/families/{fam['handle']}")
        for er in fam.get("event_ref_list") or []:
            ev = self.api.get(f"/api/events/{er['ref']}")
            if ev.get("type") != typ:
                continue
            if year is None:
                return True
            ey = (ev.get("date") or {}).get("year") or ((ev.get("date") or {}).get("dateval") or [0, 0, 0])[2]
            if ey == year or not ey:
                return True
        return False

    def ensure_note(self, text, typ="General", private=False):
        resp = self.api.post("/api/notes/", {
            "_class": "Note", "type": typ, "private": private, "format": 0,
            "text": {"string": text, "tags": []},
        })
        return first_new_handle(resp)

    def mentioned_name_fits(self, mention: dict, p1, p2) -> bool:
        name = fold(mention.get("spouse_name") or "")
        if not name:
            return True
        def ok(p):
            dn = fold(display_name(p))
            gn = fold(given_name(p))
            fn = first_token(name)
            return name in (dn, gn) or fn == first_token(given_name(p)) or (fn and fn in dn.split())
        return ok(p1) or ok(p2)

    def duplicate_couple(self, p1, p2):
        """Known duplicate people are kept unmerged, but never get a second marriage."""
        by_gid = {g: p["handle"] for g, p in self.by_gid.items()}
        for a, b in ((p1, p2), (p2, p1)):
            twin = KNOWN_DUPLICATE_PEOPLE.get(a["gramps_id"])
            if not twin:
                continue
            th = by_gid.get(twin)
            if th and frozenset({th, b["handle"]}) in self.family_of_pair:
                return (f"{a['gramps_id']} is a duplicate of {twin}, who is already married to "
                        f"{b['gramps_id']}; no second family created (people kept unmerged)")
        return None

    def ensure_couple(self, p1, p2, mention: dict, confidence: str):
        if mention.get("spouse_name") and not self.mentioned_name_fits(mention, p1, p2):
            # Do not hang a different person's marriage on this couple.
            self.unmatched.append({
                "person": p1["gramps_id"],
                "person_name": display_name(p1),
                "mention": mention.get("raw"),
                "spouse_name": mention.get("spouse_name"),
                "source": mention.get("source"),
                "reason": "named spouse does not match this couple; not applied",
            })
            self.stats["unmatched_mentions"] += 1
            return None
        key = frozenset({p1["handle"], p2["handle"]})
        created = False
        fam = self.family_of_pair.get(key)
        if not fam:
            dup = self.duplicate_couple(p1, p2)
            if dup:
                self.unmatched.append({
                    "person": p1["gramps_id"], "person_name": display_name(p1),
                    "mention": mention.get("raw"), "spouse_name": mention.get("spouse_name"),
                    "source": mention.get("source"), "reason": dup,
                })
                self.stats["skipped_known_duplicates"] = self.stats.get("skipped_known_duplicates", 0) + 1
                return None
        if fam:
            self.stats["couples_already_present"] += 1
        else:
            # seat father/mother by gender
            if p1.get("gender") == 1:
                father, mother = p1, p2
            elif p2.get("gender") == 1:
                father, mother = p2, p1
            elif p1.get("gender") == 0:  # Gramps: 0 female
                mother, father = p1, p2
            else:
                father, mother = p1, p2
            private = bool(p1.get("private") or p2.get("private"))
            payload = {
                "_class": "Family",
                "father_handle": father["handle"],
                "mother_handle": mother["handle"] if mother is not father else "",
                "type": "Married",
                "private": private,
                "child_ref_list": [],
                "event_ref_list": [],
                "note_list": [],
                "tag_list": [],
                "citation_list": [],
            }
            resp = self.api.post("/api/families/", payload)
            h = first_new_handle(resp)
            fam = self.api.get(f"/api/families/{h}")
            self.families.append(fam)
            self.family_of_pair[key] = fam
            self.spouses_of[p1["handle"]].add(p2["handle"])
            self.spouses_of[p2["handle"]].add(p1["handle"])
            for p in (p1, p2):
                obj = self.api.get(f"/api/people/{p['handle']}")
                if h not in (obj.get("family_list") or []):
                    obj.setdefault("family_list", []).append(h)
                    self.api.put(f"/api/people/{obj['handle']}", obj)
            self.stats["families_created"] += 1
            created = True
            print("FAMILY", fam.get("gramps_id"), p1["gramps_id"], p2["gramps_id"], mention.get("raw"))

        fam = self.api.get(f"/api/families/{fam['handle']}")
        # tag
        if confidence and self.tags.get(confidence) and self.tags[confidence] not in (fam.get("tag_list") or []):
            fam.setdefault("tag_list", []).append(self.tags[confidence])
            self.api.put(f"/api/families/{fam['handle']}", fam)
            fam = self.api.get(f"/api/families/{fam['handle']}")
        # note with source line
        raw = mention.get("raw") or ""
        src = mention.get("source") or ""
        note_txt = f"Marriage mention ({src}): {raw}"
        has = False
        for nh in fam.get("note_list") or []:
            n = self.api.get(f"/api/notes/{nh}")
            if raw and raw in ((n.get("text") or {}).get("string") or ""):
                has = True
        if raw and not has:
            private = bool(p1.get("private") or p2.get("private"))
            fam.setdefault("note_list", []).append(self.ensure_note(note_txt, typ="General", private=private))
            self.api.put(f"/api/families/{fam['handle']}", fam)
            fam = self.api.get(f"/api/families/{fam['handle']}")

        year = (mention.get("date") or {}).get("year") if mention.get("date") else None
        date_text = mention.get("date_text") or ""
        place = mention.get("place") or ""
        # Add a Marriage event when we have a stated date or place, and none exists for that year.
        if date_text or place or year:
            if not self.family_has_marriage(fam, year=year):
                private = bool(p1.get("private") or p2.get("private"))
                ev = {
                    "_class": "Event",
                    "type": "Marriage",
                    "description": raw,
                    "private": private,
                    "date": {
                        "_class": "Date", "calendar": 0,
                        "modifier": (mention.get("date") or {}).get("modifier") or 0,
                        "quality": (mention.get("date") or {}).get("quality") or 0,
                        "newyear": 0,
                        "dateval": (mention.get("date") or {}).get("dateval") or [0, 0, year or 0, False],
                        "text": date_text or raw,
                    },
                    "place": "",
                    "citation_list": [],
                    "note_list": [self.ensure_note(note_txt, typ="General", private=private)],
                }
                resp = self.api.post("/api/events/", ev)
                eh = first_new_handle(resp)
                try:
                    fam = self.api.get(f"/api/families/{fam['handle']}")
                    fam.setdefault("event_ref_list", []).append({
                        "ref": eh, "role": "Family", "private": private,
                        "note_list": [], "attribute_list": [], "citation_list": [],
                    })
                    self.api.put(f"/api/families/{fam['handle']}", fam)
                except Exception:
                    self.api.request("DELETE", f"/api/events/{eh}")  # never leave an orphan event
                    raise
                self.stats["marriage_events_added"] += 1
                if year or date_text:
                    self.stats["marriage_events_with_dates"] += 1
                self.added.append({
                    "family": fam.get("gramps_id"),
                    "a": p1["gramps_id"], "b": p2["gramps_id"],
                    "raw": raw, "year": year, "created_family": created,
                })
                print("MARRIAGE event", fam.get("gramps_id"), date_text or year, raw[:80])
        return fam

    def resolve_spouse(self, person, mention: dict):
        name = (mention.get("spouse_name") or "").strip()
        existing = [self.by_handle[h] for h in self.spouses_of.get(person["handle"], set()) if h and h in self.by_handle]
        if not name:
            # unnamed marriage: if this person already has exactly one spouse, attach the event there
            if len(existing) == 1:
                return existing[0], "existing unique spouse (unnamed mention)", None
            return None, None, "unnamed spouse and no unique existing partner"
        fn = first_token(name)
        full = fold(name)
        cands = []
        # existing spouse name match first
        for sp in existing:
            dn = fold(display_name(sp))
            gn = fold(given_name(sp))
            if full in (dn, gn) or fn == first_token(given_name(sp)) or fn in dn.split():
                cands.append(sp)
        if len(cands) == 1:
            return cands[0], "matched existing spouse", None
        if len(cands) > 1:
            return None, None, "ambiguous existing spouses: " + ",".join(p["gramps_id"] for p in cands)

        # search the tree, conservative filters
        py = birth_year_of(person)
        hit = []
        for p in self.people:
            if p["handle"] == person["handle"]:
                continue
            dn = fold(display_name(p))
            gn = fold(given_name(p))
            if not dn and not gn:
                continue
            ok = False
            if len(full.split()) >= 2 and (full == dn or dn.startswith(full + " ") or full in dn):
                ok = True
            elif fn and fn == first_token(given_name(p)):
                # first-name-only: only if unique after year/gender filter
                ok = True
                if len(full.split()) >= 2 and fold(name.split()[-1]) not in dn:
                    # last name given and doesn't match
                    ok = False
            if not ok:
                continue
            # gender: if both known and same, skip (not a hard rule for all cultures, but conservative)
            if person.get("gender") in (0, 1) and p.get("gender") in (0, 1) and person["gender"] == p["gender"]:  # 0 F, 1 M known
                continue
            qy = birth_year_of(p)
            if py and qy and abs(py - qy) > 40:
                continue
            hit.append(p)
        # tighten first-name-only hits
        if len(full.split()) < 2:
            # require unique first name among year-compatible people, or unique on same-ish generation
            if len(hit) > 1 and py:
                near = [p for p in hit if (birth_year_of(p) and abs(birth_year_of(p) - py) <= 20)]
                if len(near) == 1:
                    hit = near
        if len(hit) == 1:
            return hit[0], "unique name+context match", None
        if len(hit) > 1:
            return None, None, "ambiguous: " + ", ".join(
                f"{p['gramps_id']} {display_name(p)}" for p in hit[:8]
            )
        return None, None, "no matching person for spouse name"

    def handle_mention(self, person, mention: dict):
        self.stats["mentions_parsed"] += 1
        spouse, how, why = self.resolve_spouse(person, mention)
        conf = confidence_from_raw(mention.get("raw") or "")
        if spouse is None:
            rec = {
                "person": person["gramps_id"],
                "person_name": display_name(person),
                "mention": mention.get("raw"),
                "spouse_name": mention.get("spouse_name"),
                "source": mention.get("source"),
                "reason": why,
            }
            if why and why.startswith("ambiguous"):
                self.stats["ambiguous"] += 1
                rec["candidates"] = why
                self.ambiguous.append(rec)
            else:
                self.stats["unmatched_mentions"] += 1
                self.unmatched.append(rec)
            return
        self.stats["matched_to_existing"] += 1
        mention = dict(mention)
        mention["match_how"] = how
        self.ensure_couple(person, spouse, mention, conf)


def load_graph_texts():
    if not GRAPH.exists():
        return {}
    g = json.loads(GRAPH.read_text())
    mmap = {}
    mapp = Path("/workspace/gramps-web/build/data/handle_map.json")
    # we'll attach texts by existing_gramps_id / canon I00xx
    by = defaultdict(list)
    for p in g["people"]:
        gid = p.get("existing_gramps_id")
        if not gid and p["canon"].startswith("I") and p["canon"][1:].isdigit():
            gid = p["canon"]
        texts = list(p.get("lines") or []) + list(p.get("notes") or [])
        if gid:
            by[gid].extend(texts)
        by[p["canon"]].extend(texts)
    return by


def scan_family_records(by_gid_names: dict) -> list[tuple[str, dict]]:
    """Conservative: a marriage clause is attributed only when the person's
    full name is the grammatical subject of that same line (Name ... m. ...),
    not merely mentioned in a nearby paragraph (which caused false events)."""
    results = []
    # map folded full names of deceased people (surname required) to gids
    name_to_gids = defaultdict(list)
    for gid, rec in by_gid_names.items():
        if rec.get("private"):
            continue
        dn = fold(rec["name"])
        if len(dn.split()) < 2:
            continue
        name_to_gids[dn].append(gid)
    if not name_to_gids:
        return results
    # build regex of longer names first
    names = sorted(name_to_gids, key=len, reverse=True)
    # too many names to make one regex of 900; scan line-wise for 'm.' then search names
    for path in FAMILY_RECORDS:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not re.search(r"\bm(?:\.|arried)\s+", line, re.I):
                continue
            mentions = collect_mentions_from_texts([line], f"{path.name}:{i+1}")
            if not mentions:
                continue
            lf = fold(line)
            owners = []
            for dn, gids in name_to_gids.items():
                # subject of this line only, e.g. "- Clara, b. about 1860; m. John Shaw"
                if dn and dn in lf:
                    # name must appear before the marriage verb
                    if re.search(re.escape(dn) + r".{0,80}\bm(?:\.|arried)\b", lf):
                        owners.extend(gids)
            owners = list(dict.fromkeys(owners))
            if len(owners) == 1:
                for ment in mentions:
                    results.append((owners[0], ment))
    return results


def main():
    api = Api()
    m = Marriages(api)
    graph_texts = load_graph_texts()

    # People notes from the API (includes chart-line notes already loaded)
    # Use graph lines first (faster, structured), then API notes if not already used.
    seen_raw = set()
    for p in m.people:
        texts = list(graph_texts.get(p["gramps_id"]) or [])
        # API notes
        full = p
        # notes not included in list; fetch when graph is empty or always? always add API notes
        obj = api.get(f"/api/people/{p['handle']}")
        for nh in obj.get("note_list") or []:
            n = api.get(f"/api/notes/{nh}")
            texts.append(((n.get("text") or {}).get("string") or ""))
        mentions = collect_mentions_from_texts(texts, "notes/chart")
        for ment in mentions:
            key = (p["gramps_id"], ment["raw"])
            if key in seen_raw:
                continue
            seen_raw.add(key)
            m.handle_mention(p, ment)

    # Family-record supplement
    name_map = {gid: {"name": display_name(p), "private": p.get("private")} for gid, p in m.by_gid.items()}
    extra = scan_family_records(name_map)
    for gid, ment in extra:
        key = (gid, ment["raw"])
        if key in seen_raw:
            continue
        seen_raw.add(key)
        if gid in m.by_gid:
            m.handle_mention(m.by_gid[gid], ment)

    # Count existing marriage events with dates (after additions)
    dated = 0
    for f in (api.get("/api/families/") or []):
        for er in f.get("event_ref_list") or []:
            ev = api.get(f"/api/events/{er['ref']}")
            if ev.get("type") != "Marriage":
                continue
            year = (ev.get("date") or {}).get("year") or ((ev.get("date") or {}).get("dateval") or [0, 0, 0])[2]
            text = (ev.get("date") or {}).get("text") or ev.get("description") or ""
            if year or re.search(r"\d{4}", text):
                dated += 1
    m.stats["marriage_events_with_dates_total_in_tree"] = dated
    m.stats["families_total"] = len(api.get("/api/families/") or [])

    report = {
        "stats": m.stats,
        "added": m.added,
        "ambiguous_sample": m.ambiguous[:25],
        "unmatched_sample": m.unmatched[:25],
        "n_ambiguous": len(m.ambiguous),
        "n_unmatched": len(m.unmatched),
        "all_ambiguous": m.ambiguous,
        "all_unmatched": m.unmatched,
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(m.stats, indent=2))
    print(f"ambiguous {len(m.ambiguous)} unmatched {len(m.unmatched)} -> {OUT}")
    print("AMBIGUOUS SAMPLE:")
    for r in m.ambiguous[:12]:
        print(" ", r)
    print("UNMATCHED SAMPLE:")
    for r in m.unmatched[:12]:
        print(" ", r)


if __name__ == "__main__":
    main()

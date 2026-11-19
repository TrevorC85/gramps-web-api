#!/usr/bin/env python3
"""Idempotent API load of the merged graph into the local Gramps Web tree.

Extends the existing 17 people in place. Does not invent facts. Living people
are stored with a first name only and private=True (API, not GEDCOM RESN).
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent
GRAPH = ROOT / "data" / "graph.json"
MAP_PATH = ROOT / "data" / "handle_map.json"
PLACE_MAP = ROOT / "data" / "place_map.json"
LOG = ROOT / "data" / "load.log"
PASSFILE = Path("/workspace/gramps-web/.admin_password")
BASE = "http://127.0.0.1:5055"

RELATION_TO_GRAMPS = {
    "biological": "Birth",
    "adoptive": "Adopted",
    "step": "Stepchild",
    "foster": "Foster",
    "legal": "Legal",  # custom Gramps type; not one of the built-in ChildRef types
}

TAG_SPEC = [
    ("confirmed (record)", "#1f5fa8"),
    ("confirmed (index)", "#0d47a6"),
    ("confirmed (family)", "#2e7d4f"),
    ("confirmed (chart)", "#3d6f9c"),
    ("probable", "#d08a00"),
    ("probable (strong)", "#b35a00"),
    ("needs confirmation", "#6b6b6b"),
    ("possible", "#8e44ad"),
]

# Conservative given-name hints used only to seat parents in a Gramps family
# (father_handle vs mother_handle). A note is added when this is the only cue.
MALE_FIRST = {
    "james", "john", "joseph", "william", "charles", "george", "thomas", "henry",
    "edward", "robert", "michael", "paul", "david", "richard", "peter", "daniel",
    "samuel", "andrew", "benjamin", "jacob", "august", "mike", "trevor", "chuck",
    "jack", "kenneth", "leo", "eli", "jonathan", "francis", "patrick", "michael",
    "nicholas", "franz", "christian", "philip", "philipp", "gottlieb", "johannes",
    "jean", "jacques", "louis", "pierre", "francois", "françois", "antoine",
    "michel", "andre", "andré", "etienne", "étienne", "nicolas", "rene", "rené",
    "alexandre", "martin", "hugh", "isaac", "darius", "howard", "liness", "harry",
    "everett", "decatur", "seymour", "timothy", "ezra", "patrick", "toney",
    "eugene", "donald", "daniel", "andrew", "jacob", "chris", "christoph",
}
FEMALE_FIRST = {
    "mary", "marie", "margaret", "marge", "judy", "joy", "cindy", "donna",
    "elizabeth", "ann", "anne", "anna", "sarah", "susan", "susannah", "catherine",
    "catharine", "catharina", "katherine", "helen", "helena", "alice", "grace",
    "therese", "teresa", "bridget", "alphonsine", "lillian", "bertha", "eva",
    "barbara", "phoebe", "agnes", "oakie", "blanche", "hannah", "nancy", "maude",
    "harriett", "harriet", "christina", "effie", "delilah", "matilda", "amelia",
    "frankie", "leona", "loretta", "lois", "huldah", "polly", "abbie", "abbey",
    "francoise", "françoise", "marie-anne", "marie-madeleine", "helene", "hélène",
    "marguerite", "cecile", "cécile", "angelique", "angélique", "therese",
    "philipina", "regina", "lucia", "trine", "adelheidt",
}

# Added 18 Nov 2026 (round 5): unambiguous given names seen on unknown-sex people.
# Ambiguous ones (Claude, Marion, Dominique, Camille, Vital, Innocent, Sainte) left out.
MALE_FIRST |= {
    "jean", "jean-baptiste", "christophe", "guillaume", "philippe", "maurice", "marin",
    "raphael", "toussaint", "johann", "israel", "laurent", "noel", "julien", "leonard",
    "gilles", "hinrich", "benoni", "earl", "rowland", "zachary", "zacharie", "germain",
    "gabriel", "francois", "pierre", "jacques", "nicolas", "louis", "antoine", "etienne",
    "michel", "rene", "andre", "mathurin", "simon", "hubert", "denis", "martin", "jerome",
    "lawrence", "olivier", "ignace", "urbain", "raymond", "georges", "philibert", "eustache",
    "marc", "vincent", "abel", "hilaire", "auffray", "emery",
    "matthew", "edwin", "alfred", "wm", "jurgen", "cord", "carsten", "mangels",
}
FEMALE_FIRST |= {
    "jeanne", "madeleine", "elisabeth", "genevieve", "suzanne", "perrine", "sophia",
    "esther", "priscilla", "eleanor", "jacqueline", "charlotte", "michelle", "renee",
    "nicole", "rachel", "jane", "lydia", "margarethe", "adele", "ada", "angie", "martha",
    "hermine", "georgette", "mathurine", "guillemette", "francoise", "louise", "therese",
    "angelique", "marguerite", "agathe", "anne", "barbe", "cecile", "helene", "jeannette",
    "mercy", "cassandra", "ursule", "noelle", "isabelle", "susanna", "rebecca", "joan",
    "aimee", "denise", "claire", "florence", "antoinette", "sebastienne", "marthe", "vincente",
    "annie", "emily", "clara", "ida", "caroline", "ellen", "kathleen", "cathy", "teri",
    "magdalena", "margaretha", "katharina", "myrah", "ginney",
}


def _fold_first(first: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", first) if not unicodedata.combining(c))



def log(msg: str) -> None:
    line = msg
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_owner():
    user = pw = None
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and user is None:
            user = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and pw is None and user:
            pw = line.split("=", 1)[1].strip()
            break
    if not user or not pw:
        raise SystemExit("could not parse owner credentials")
    return user, pw


class Api:
    def __init__(self):
        self.token = None
        self.token_at = 0
        self.refresh()

    def refresh(self):
        user, pw = load_owner()
        body = json.dumps({"username": user, "password": pw}).encode()
        for attempt in range(8):
            req = urllib.request.Request(
                BASE + "/api/token/", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                self.token = data["access_token"]
                self.token_at = time.time()
                return
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(5 + attempt * 5)
                    continue
                raise SystemExit(f"token failed: {e.code}")
        raise SystemExit("token rate limited")

    def request(self, method, path, data=None, timeout=90):
        if time.time() - self.token_at > 600:
            self.refresh()
        url = BASE + path
        headers = {"Authorization": f"Bearer {self.token}"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code in (401, 422) and method != "POST" or e.code == 401:
                self.refresh()
                headers["Authorization"] = f"Bearer {self.token}"
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        raw = r.read()
                        return json.loads(raw) if raw else None
                except urllib.error.HTTPError as e2:
                    err = e2.read().decode("utf-8", "replace")
                    raise RuntimeError(f"{method} {path} -> {e2.code}: {err[:800]}") from e2
            err = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:800]}")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data):
        return self.request("POST", path, data)

    def put(self, path, data):
        from lib_api import guarded_put  # round 5: skip unchanged, retry transient failures
        return guarded_put(lambda: self.request("PUT", path, data), lambda: self.request("GET", path), path, data)


def first_new_handle(resp):
    if isinstance(resp, dict) and resp.get("handle"):
        return resp["handle"]
    if isinstance(resp, list):
        for it in resp:
            if isinstance(it, dict) and it.get("type") == "add":
                return it.get("handle") or (it.get("new") or {}).get("handle")
        for it in resp:
            if isinstance(it, dict) and it.get("handle"):
                return it["handle"]
    raise RuntimeError(f"no handle in response: {json.dumps(resp)[:500]}")


# Gramps Person sex codes (gramps.gen.lib.Person): FEMALE=0, MALE=1, UNKNOWN=2.
FEMALE, MALE, UNKNOWN = 0, 1, 2


# Families whose confidence tags are set by apply_cindy_rulings.py (Genealogy Wife rulings).
# load.py must not re-add the chart's edge tags there, or the two steps undo each other
# every build (round 5, item 5: F0006 August x Donna).
RULED_FAMILY_TAGS = {frozenset({"I0012", "I0011"})}


# Source-based rulings that the name rules get wrong.
SEX_OVERRIDES = {"judy:CN": FEMALE}  # Christian North, wife of Nicholas James (m. Apr 1678 Wimborne Minster)


def infer_sex(person: dict) -> int:
    """Gramps codes: 0 female, 1 male, 2 unknown (gramps.gen.lib.Person). Conservative."""
    if person.get("canon") in SEX_OVERRIDES:
        return SEX_OVERRIDES[person["canon"]]
    text = " ".join(person.get("lines") or []).lower()
    if person.get("living") and person["canon"] in {
        "I0001", "I0002", "I0009", "I0016", "I0017",
    }:
        return MALE
    if person.get("living") and person["canon"] in {
        "I0003", "I0005", "I0007", "I0008", "I0010", "I0011",
    }:
        return FEMALE
    if re_search(r"\b(née|nee|wife of|daughter of|dau\. of)\b", text):
        return FEMALE
    if re_search(r"\bson of\b|\bhusband of\b", text):
        return MALE
    if re_search(r"confirmed as .{0,40}father", text):
        return MALE
    if re_search(r"confirmed as .{0,40}mother", text):
        return FEMALE
    first = (person.get("name_parts") or {}).get("first") or ""
    first = _fold_first(first.split()[0].lower()).strip("?.,") if first else ""
    # hyphenated Marie-X is female
    if first.startswith("marie-") or first.startswith("anne-") or first.startswith("marie"):
        if first in MALE_FIRST:
            return MALE
        return FEMALE
    if first not in MALE_FIRST and first not in FEMALE_FIRST and "-" in first:
        head = first.split("-", 1)[0]  # Jean-Gabriel, Élisabeth-Ursule
        if head in MALE_FIRST and head not in FEMALE_FIRST:
            return MALE
        if head in FEMALE_FIRST and head not in MALE_FIRST:
            return FEMALE
    if first in MALE_FIRST and first not in FEMALE_FIRST:
        return MALE
    if first in FEMALE_FIRST and first not in MALE_FIRST:
        return FEMALE
    # Last resort: the Judy-side chart keys are Ahnentafel numbers relative to Judy
    # (judy:2 father, judy:3 mother, ...): even = male, odd = female. Used only when
    # nothing above decides (151 agree / 2 disagree with the name rules on 18 Nov 2026).
    nums = {int(k.split(":", 1)[1]) for k in [person.get("canon", "")] + list(person.get("source_keys") or [])
            if re.fullmatch(r"judy:\d+", k or "")}
    nums = {n for n in nums if n >= 2}
    if nums and len({n % 2 for n in nums}) == 1:
        return MALE if next(iter(nums)) % 2 == 0 else FEMALE
    return UNKNOWN


def re_search(pat, text):
    import re
    return re.search(pat, text, re.I)


class Loader:
    def __init__(self, api: Api, graph: dict):
        self.api = api
        self.graph = graph
        self.people = {p["canon"]: p for p in graph["people"]}
        self.map = {}
        if MAP_PATH.exists():
            self.map = json.loads(MAP_PATH.read_text())
        self.places = {}
        if PLACE_MAP.exists():
            self.places = json.loads(PLACE_MAP.read_text())
        self.tags = {}
        self.existing_by_gid = {}
        self.stats = defaultdict(int)

    def save_maps(self):
        MAP_PATH.write_text(json.dumps(self.map, indent=2))
        PLACE_MAP.write_text(json.dumps(self.places, indent=2))

    def ensure_tags(self):
        existing = self.api.get("/api/tags/") or []
        by_name = {t["name"]: t for t in existing}
        for name, color in TAG_SPEC:
            if name in by_name:
                self.tags[name] = by_name[name]["handle"]
                continue
            resp = self.api.post("/api/tags/", {"_class": "Tag", "name": name, "color": color})
            self.tags[name] = first_new_handle(resp)
            log(f"TAG {name}")
        # also index any others
        for t in self.api.get("/api/tags/") or []:
            self.tags[t["name"]] = t["handle"]

    def index_existing_people(self):
        # Index by Gramps ID only. Do not pre-fill handle_map, or ensure_person
        # would skip update_existing_person on the already-loaded 17 (and on reruns).
        for p in self.api.get("/api/people/") or []:
            self.existing_by_gid[p["gramps_id"]] = p

    def ensure_place(self, parts: list) -> str | None:
        if not parts:
            return None
        # parts: [(type, name), ...] small to large. We create large-to-small.
        parent = None
        handle = None
        chain = []
        for typ, name in reversed(parts):
            chain.append((typ, name))
            key = " > ".join(f"{t}:{n}" for t, n in chain)
            if key in self.places:
                handle = self.places[key]
                parent = handle
                continue
            payload = {
                "_class": "Place",
                "name": {"_class": "PlaceName", "value": name, "lang": ""},
                "place_type": typ,
                "placeref_list": ([{"ref": parent}] if parent else []),
                "title": ", ".join(n for _, n in reversed(chain)),
            }
            resp = self.api.post("/api/places/", payload)
            handle = first_new_handle(resp)
            self.places[key] = handle
            parent = handle
            self.stats["places"] += 1
        return handle

    def ensure_note(self, text: str, typ: str = "General", private: bool = False) -> str:
        # Idempotent: reuse an existing note with the same text and type, so a
        # rebuild does not post a fresh (orphaned) copy of every link note.
        if getattr(self, "_note_cache", None) is None:
            self._note_cache = {}
            for n in self.api.get("/api/notes/?keys=handle,text,type") or []:
                t = n.get("text")
                t = t.get("string") if isinstance(t, dict) else (t or "")
                ty = n.get("type")
                ty = ty.get("string") if isinstance(ty, dict) else ty
                self._note_cache.setdefault((t, ty), n["handle"])
        key = (text, typ)
        if key in self._note_cache:
            return self._note_cache[key]
        payload = {
            "_class": "Note",
            "type": typ,
            "private": private,
            "format": 0,
            "text": {"string": text, "tags": []},
        }
        h = first_new_handle(self.api.post("/api/notes/", payload))
        self._note_cache[key] = h
        return h

    def build_name(self, person: dict) -> dict:
        parts = person.get("name_parts") or {}
        living = person.get("living")
        first = parts.get("first") or person["display_name"]
        surname = "" if living else (parts.get("surname") or "")
        nick = ""
        if parts.get("nicknames"):
            nick = ", ".join(parts["nicknames"])
        # For living, keep nicknames in first name if they were part of the chart display
        # (Charles "Chip" (Chuck) already in first from split? split leaves them in nick)
        if living:
            # Use the chart first-name display without a surname.
            # Prefer the portion before any surname we stripped.
            raw = parts.get("raw") or person["display_name"]
            # drop a trailing surname if we parsed one and it appears at the end of raw
            first = raw
            if parts.get("surname") and raw.endswith(parts["surname"]):
                first = raw[: -len(parts["surname"])].strip(" ,")
            # Joy (née Gimple) — living, first name only: "Joy"
            if first.lower().startswith("joy"):
                first = "Joy"
            surname = ""
        surnames = []
        if surname:
            surnames.append({
                "_class": "Surname",
                "surname": surname,
                "primary": True,
                "prefix": "",
                "connector": "",
                "origintype": "Inherited",
            })
        return {
            "_class": "Name",
            "first_name": first,
            "surname_list": surnames,
            "suffix": "" if living else (parts.get("suffix") or ""),
            "nick": nick if not living else "",
            "type": "Birth Name",
        }

    def person_payload_new(self, person: dict) -> dict:
        sex = infer_sex(person)
        tags = []
        notes = []
        # person-level notes: leftover notes, conflicts
        texts = []
        if person.get("notes"):
            texts.extend(person["notes"])
        if person.get("conflicts"):
            texts.append("Conflicts / competing statements (not silently resolved):\n- " +
                         "\n- ".join(person["conflicts"]))
        if person.get("lines"):
            texts.append("Chart lines:\n- " + "\n- ".join(person["lines"]))
        sides = ", ".join(person.get("sides") or [])
        if sides:
            texts.append(f"Source side(s): {sides}. Keys: {', '.join(person.get('source_keys') or [])}.")
        if texts:
            notes.append(self.ensure_note("\n\n".join(texts), typ="General", private=bool(person.get("living"))))
        return {
            "_class": "Person",
            "primary_name": self.build_name(person),
            "gender": sex,
            "private": bool(person.get("living")),
            "note_list": notes,
            "tag_list": tags,
            "event_ref_list": [],
            "family_list": [],
            "parent_family_list": [],
            "urls": [],
            "citation_list": [],
            "media_list": [],
        }

    def update_existing_person(self, handle: str, person: dict):
        obj = self.api.get(f"/api/people/{handle}")
        # privacy + living name
        if person.get("living"):
            obj["private"] = True
            # do not overwrite a careful existing living name unless surname leaked
            pn = obj.get("primary_name") or {}
            surnames = [s.get("surname") for s in pn.get("surname_list") or [] if s.get("surname")]
            if surnames:
                obj["primary_name"] = self.build_name(person)
        # add a "full tree chart lines" note if not already present
        already = False
        for nh in obj.get("note_list") or []:
            n = self.api.get(f"/api/notes/{nh}")
            s = ((n.get("text") or {}).get("string") or "")
            if s.startswith("Chart lines:"):
                already = True
                break
        if not already and person.get("lines"):
            texts = ["Chart lines:\n- " + "\n- ".join(person["lines"])]
            if person.get("conflicts"):
                texts.append("Conflicts / competing statements (not silently resolved):\n- " +
                             "\n- ".join(person["conflicts"]))
            obj.setdefault("note_list", []).append(
                self.ensure_note("\n\n".join(texts), typ="General", private=bool(person.get("living")))
            )
        self.api.put(f"/api/people/{handle}", obj)
        self.stats["people_updated"] += 1

    def create_person(self, person: dict) -> str:
        payload = self.person_payload_new(person)
        resp = self.api.post("/api/people/", payload)
        handle = first_new_handle(resp)
        self.map[person["canon"]] = handle
        self.stats["people_created"] += 1
        return handle

    def ensure_person(self, person: dict) -> str:
        canon = person["canon"]
        if canon in self.map:
            self.update_existing_person(self.map[canon], person)
            return self.map[canon]
        if person.get("existing_gramps_id") and person["existing_gramps_id"] in self.existing_by_gid:
            h = self.existing_by_gid[person["existing_gramps_id"]]["handle"]
            self.map[canon] = h
            self.update_existing_person(h, person)
            return h
        # Also match a person already in the tree under this Gramps ID
        if canon in self.existing_by_gid:
            h = self.existing_by_gid[canon]["handle"]
            self.map[canon] = h
            self.update_existing_person(h, person)
            return h
        h = self.create_person(person)
        return h

    def gram_date(self, d: dict | None) -> dict | None:
        if not d:
            return None
        return {
            "_class": "Date",
            "calendar": 0,
            "modifier": d.get("modifier") or 0,
            "quality": d.get("quality") or 0,
            "newyear": 0,
            "dateval": d.get("dateval") or [0, 0, d.get("year") or 0, False],
            "text": d.get("text") or "",
        }

    def ensure_event_on_person(self, handle: str, ev: dict, private: bool):
        obj = self.api.get(f"/api/people/{handle}")
        # skip if same type + same year already present
        year = (ev.get("date") or {}).get("year")
        typ = ev["type"]
        for ref in obj.get("event_ref_list") or []:
            existing = self.api.get(f"/api/events/{ref['ref']}")
            if existing.get("type") != typ:
                continue
            ey = (existing.get("date") or {}).get("year") or (existing.get("date") or {}).get("dateval", [0, 0, 0])[2]
            if year and ey == year:
                return existing["handle"]
            if not year and not ey and typ == ev["type"]:
                return existing["handle"]
        # Round 5: one Birth/Death/Burial per person. A second chart line for the same vital
        # event stays in the person's "Chart lines" note instead of becoming a duplicate event.
        if typ in ("Birth", "Death", "Burial"):
            for ref in obj.get("event_ref_list") or []:
                if ref.get("role", "Primary") != "Primary":
                    continue
                existing = self.api.get(f"/api/events/{ref['ref']}")
                if existing.get("type") == typ:
                    return existing["handle"]
        # never create an empty event (no date, no place, no description)
        if not (ev.get("date") or ev.get("place_parts") or ev.get("place_text") or ev.get("description") or ev.get("raw")):
            return None
        place_h = None
        if ev.get("place_parts"):
            place_h = self.ensure_place(ev["place_parts"])
        elif ev.get("place_text"):
            place_h = self.ensure_place([("City", ev["place_text"])])
        payload = {
            "_class": "Event",
            "type": typ,
            "description": ev.get("description") or ev.get("raw") or "",
            "private": private,
            "date": self.gram_date(ev.get("date")) or {
                "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                "newyear": 0, "dateval": [0, 0, 0, False], "text": ev.get("raw") or "",
            },
            "place": place_h or "",
            "citation_list": [],
            "note_list": [],
            "media_list": [],
        }
        # if no date at all, still record the raw description
        eh = first_new_handle(self.api.post("/api/events/", payload))
        try:
            self._link_event(handle, eh, typ, private)
        except Exception:
            self.api.request("DELETE", f"/api/events/{eh}")  # never leave an orphan event behind
            raise
        self.stats["events"] += 1
        return eh

    def _link_event(self, handle: str, eh: str, typ: str, private: bool):
        obj = self.api.get(f"/api/people/{handle}")
        obj.setdefault("event_ref_list", []).append({
            "ref": eh, "role": "Primary", "private": private,
            "note_list": [], "attribute_list": [],
        })
        # birth/death indices
        if typ == "Birth" and obj.get("birth_ref_index", -1) < 0:
            obj["birth_ref_index"] = len(obj["event_ref_list"]) - 1
        if typ == "Death" and obj.get("death_ref_index", -1) < 0:
            obj["death_ref_index"] = len(obj["event_ref_list"]) - 1
        self.api.put(f"/api/people/{handle}", obj)

    def ensure_families(self):
        # group children by parent set
        parents_of = defaultdict(list)
        for e in self.graph["edges"]:
            parents_of[e["child"]].append(e)
        families = defaultdict(list)  # frozenset(parents) -> list of (child, edges)
        for child, elist in parents_of.items():
            parents = [e["parent"] for e in elist]
            if len(set(parents)) <= 2:
                families[frozenset(parents)].append((child, elist))
                continue
            # 3+ parents (e.g. biological + legal/adoptive): one family per
            # father + shared mother, never a single 3-parent group.
            sexes = {
                e["parent"]: infer_sex(self.people[e["parent"]])
                for e in elist if e["parent"] in self.people
            }
            mothers = [p for p, s in sexes.items() if s == FEMALE]
            fathers = [p for p, s in sexes.items() if s == MALE]
            leftovers = [p for p in dict.fromkeys(parents) if p not in mothers and p not in fathers]
            mother = mothers[0] if mothers else None
            partners = fathers or leftovers
            if mother and partners:
                log(f"NOTE: split {len(set(parents))} parents for {child} into {len(partners)} families")
                for f in partners:
                    sub = [e for e in elist if e["parent"] in (f, mother)]
                    families[frozenset([f, mother])].append((child, sub))
            else:
                log(f"NOTE: {len(set(parents))} parents for {child}; using first two only: {parents}")
                families[frozenset(list(dict.fromkeys(parents))[:2])].append((child, elist))

        # family hints (partners without a child)
        for hint in self.graph.get("family_hints") or []:
            pset = frozenset(hint["partners"])
            families.setdefault(pset, [])

        for pset, children in families.items():
            parents = list(pset)
            # assign father/mother
            father = mother = None
            sexes = {p: infer_sex(self.people[p]) for p in parents if p in self.people}
            males = [p for p, s in sexes.items() if s == MALE]
            females = [p for p, s in sexes.items() if s == FEMALE]
            unknown = [p for p in parents if sexes.get(p, UNKNOWN) == UNKNOWN]
            if len(parents) == 2:
                if len(males) == 1 and len(females) == 1:
                    father, mother = males[0], females[0]
                elif len(males) == 1 and len(unknown) == 1:
                    father, mother = males[0], unknown[0]
                elif len(females) == 1 and len(unknown) == 1:
                    father, mother = unknown[0], females[0]
                else:
                    # judy/collins source order: first parent of first child is father
                    if children:
                        ordered = []
                        for e in children[0][1]:
                            if e["parent"] not in ordered:
                                ordered.append(e["parent"])
                        if len(ordered) == 2 and all(e.get("source") in ("judy", "collins", "collins_fams", "koop") for e in children[0][1]):
                            father, mother = ordered[0], ordered[1]
                        else:
                            father, mother = parents[0], parents[1]
                    else:
                        father, mother = parents[0], parents[1]
            elif len(parents) == 1:
                p = parents[0]
                if sexes.get(p) == FEMALE:
                    mother = p
                else:
                    father = p
            else:
                # 3+ parents — note on the child, still make families for pairs if possible
                log(f"NOTE: {len(parents)} parents for a family group {parents}")
                if children:
                    for child, _ in children:
                        if child in self.people:
                            # already have conflicts maybe
                            pass
                # put first as father, second mother, skip extras into a note
                father = parents[0] if parents else None
                mother = parents[1] if len(parents) > 1 else None

            fh = self.map.get(father) if father else None
            mh = self.map.get(mother) if mother else None
            if not fh and not mh:
                continue

            # idempotent: look for existing family with same parents
            existing_fam = None
            # check parent's family_list
            probe = fh or mh
            if probe:
                pob = self.api.get(f"/api/people/{probe}")
                for famh in pob.get("family_list") or []:
                    fam = self.api.get(f"/api/families/{famh}")
                    if fam.get("father_handle") == (fh or "") and fam.get("mother_handle") == (mh or ""):
                        existing_fam = fam
                        break
                    # also match swapped
                    if fam.get("father_handle") == (mh or "") and fam.get("mother_handle") == (fh or ""):
                        existing_fam = fam
                        break

            child_refs = []
            fam_tags = set()
            fam_notes = []
            for child, elist in children:
                ch = self.map.get(child)
                if not ch:
                    continue
                # compose link note
                bits = []
                for e in elist:
                    role = "parent"
                    if e["parent"] == father:
                        role = "father"
                    elif e["parent"] == mother:
                        role = "mother"
                    bits.append(f"{role}: {e['tag']}" + (f" — {e['basis']}" if e["basis"] else ""))
                    if e["tag"] in self.tags:
                        fam_tags.add(self.tags[e["tag"]])
                note_h = None
                if bits:
                    note_h = self.ensure_note(
                        "Parent-child link confidence:\n- " + "\n- ".join(bits),
                        typ="Link",
                        private=bool(self.people.get(child, {}).get("living")),
                    )
                def parent_rel(which_parent: str) -> str:
                    for e in elist:
                        if e["parent"] != which_parent:
                            continue
                        rel = (e.get("relation") or "").strip().lower()
                        if rel in RELATION_TO_GRAMPS:
                            return RELATION_TO_GRAMPS[rel]
                        if e.get("style") == "adopt" or "adoptive" in (e.get("basis") or "").lower():
                            return "Adopted"
                    return "Birth"
                frel = parent_rel(father) if father else "Birth"
                mrel = parent_rel(mother) if mother else "Birth"
                cr = {
                    "ref": ch,
                    "frel": frel,
                    "mrel": mrel,
                    "private": bool(self.people.get(child, {}).get("living")),
                    "citation_list": [],
                    "note_list": [note_h] if note_h else [],
                }
                child_refs.append(cr)

            if existing_fam:
                fam = existing_fam
                have = {c["ref"] for c in fam.get("child_ref_list") or []}
                changed = False
                for cr in child_refs:
                    if cr["ref"] not in have:
                        fam.setdefault("child_ref_list", []).append(cr)
                        changed = True
                    else:
                        for existing_cr in fam.get("child_ref_list") or []:
                            if existing_cr["ref"] == cr["ref"] and (
                                existing_cr.get("frel") != cr["frel"] or existing_cr.get("mrel") != cr["mrel"]
                            ):
                                existing_cr["frel"] = cr["frel"]
                                existing_cr["mrel"] = cr["mrel"]
                                changed = True
                for t in ([] if pset in RULED_FAMILY_TAGS else fam_tags):
                    if t not in (fam.get("tag_list") or []):
                        fam.setdefault("tag_list", []).append(t)
                        changed = True
                if changed:
                    self.api.put(f"/api/families/{fam['handle']}", fam)
                    self.stats["families_updated"] += 1
                continue

            payload = {
                "_class": "Family",
                "father_handle": fh or "",
                "mother_handle": mh or "",
                "child_ref_list": child_refs,
                "type": "Married",
                "private": False,
                "tag_list": list(fam_tags),
                "note_list": fam_notes,
                "event_ref_list": [],
                "citation_list": [],
                "media_list": [],
            }
            # if either parent living, family can stay public but children already private
            resp = self.api.post("/api/families/", payload)
            fam_h = first_new_handle(resp)
            # link family onto parents and parent_family onto children
            for role_h in (fh, mh):
                if not role_h:
                    continue
                p = self.api.get(f"/api/people/{role_h}")
                if fam_h not in (p.get("family_list") or []):
                    p.setdefault("family_list", []).append(fam_h)
                    self.api.put(f"/api/people/{role_h}", p)
            for cr in child_refs:
                p = self.api.get(f"/api/people/{cr['ref']}")
                if fam_h not in (p.get("parent_family_list") or []):
                    p.setdefault("parent_family_list", []).append(fam_h)
                    self.api.put(f"/api/people/{cr['ref']}", p)
            self.stats["families_created"] += 1

    def add_person_urls(self, person: dict, handle: str):
        urls = person.get("urls") or []
        if not urls:
            return
        obj = self.api.get(f"/api/people/{handle}")
        have = {u.get("path") for u in obj.get("urls") or []}
        changed = False
        for u in urls:
            if u in have:
                continue
            obj.setdefault("urls", []).append({
                "type": "Web Home",
                "path": u,
                "desc": "URL from chart / family record line",
                "private": bool(person.get("living")),
            })
            changed = True
        if changed:
            self.api.put(f"/api/people/{handle}", obj)
            self.stats["urls"] += 1

    def run(self):
        LOG.write_text("")
        log("Loading tags and existing people…")
        self.ensure_tags()
        self.index_existing_people()
        people = self.graph["people"]
        log(f"Ensuring {len(people)} people…")
        for i, person in enumerate(people, 1):
            try:
                h = self.ensure_person(person)
                # events (skip empty)
                for ev in person.get("events") or []:
                    # skip marriage events here; they go on families when we have both spouses
                    if ev["type"] == "Marriage":
                        continue
                    self.ensure_event_on_person(h, ev, private=bool(person.get("living")))
                self.add_person_urls(person, h)
            except Exception as exc:
                log(f"ERROR person {person['canon']} {person['display_name']}: {exc}")
                self.stats["errors"] += 1
            if i % 50 == 0:
                self.save_maps()
                log(f"  …{i}/{len(people)} people ({self.stats['people_created']} created, {self.stats['people_updated']} updated, {self.stats['errors']} errors)")
        self.save_maps()
        log("Building families…")
        try:
            self.ensure_families()
        except Exception as exc:
            log(f"ERROR families: {exc}")
            self.stats["errors"] += 1
        self.save_maps()
        log("STATS " + json.dumps(dict(self.stats)))
        (ROOT / "data" / "load_stats.json").write_text(json.dumps(dict(self.stats), indent=2))
        return self.stats


def main() -> int:
    graph = json.loads(GRAPH.read_text())
    api = Api()
    loader = Loader(api, graph)
    loader.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

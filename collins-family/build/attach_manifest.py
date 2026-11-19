#!/usr/bin/env python3
"""Apply website_manifest.jsonl files to Gramps Web (local or AWS).

Reads:
  /workspace/genealogy/website_manifest.jsonl
  /workspace/genealogy-cindy/website_manifest.jsonl
Absent files are skipped. Replaces/delete are honored (last record of an id wins).
Unmatched entries are logged to /workspace/gramps-web/shared/manifest_unmatched.csv.

Base URL: GRAMPSWEB_URL (fallback GRAMPS_WEB_URL / GRAMPSWEB_BASE_URL, then
http://127.0.0.1:5055). Credentials: GRAMPSWEB_USERNAME / GRAMPSWEB_PASSWORD
or the local .admin_password file.
"""
from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_overrides
import media_prep
from lib_api import Api, first_new_handle  # noqa: E402

MANIFESTS = [
    Path("/workspace/genealogy/website_manifest.jsonl"),
    Path("/workspace/genealogy-cindy/website_manifest.jsonl"),
]
UNMATCHED = Path("/workspace/gramps-web/shared/manifest_unmatched.csv")
INDEX = Path("/workspace/gramps-web/shared/people_index.csv")
CINDY_RECORD = Path("/workspace/genealogy-cindy/CINDY_FAMILY_RECORD.md")

# Given-name equivalents used only when matching a named parent or stub.
GIVEN_ALIASES = {
    "heinrich": {"heinrich", "henry"}, "henry": {"heinrich", "henry"},
    "philipp": {"philipp", "phillip", "philip"},
    "phillip": {"philipp", "phillip", "philip"},
    "philip": {"philipp", "phillip", "philip"},
    "maria": {"maria", "mary"}, "mary": {"maria", "mary"},
    "mehetabel": {"mehetabel", "mehitable"}, "mehitable": {"mehetabel", "mehitable"},
    "asa": {"asa", "ase"}, "ase": {"asa", "ase"},
    "catherine": {"catherine", "catharine", "katherine", "katharine"},
    "katherine": {"catherine", "catharine", "katherine", "katharine"},
    "catharine": {"catherine", "catharine", "katherine", "katharine"},
}
FEMALE_GIVEN = {
    "mary", "marie", "maria", "margaret", "margaretha", "matilda", "violet",
    "helen", "oakie", "caroline", "katherine", "katharine", "catherine",
    "pearl", "christina", "sophia", "amelia", "barbara", "mehetabel", "mehitable",
    "philipina", "effie", "hannah", "ann", "anna", "jane", "clara", "ida", "ellen",
}
MALE_GIVEN = {
    "arthur", "heinrich", "henry", "alexander", "frank", "kenneth", "nathaniel",
    "michael", "roy", "elmer", "asa", "william", "jefferson", "miner", "jacob", "philipp",
    "phillip", "everett", "harry", "thomas", "nicholas", "liness", "darius", "james",
    "christopher", "john", "paul", "august",
}

CONFIDENCE = {
    "confirmed (record)",
    "confirmed (index)",
    "confirmed (family)",
    "confirmed (chart)",
    "probable (strong)",
    "probable",
    "needs confirmation",
    "possible",
}
CONF_TO_INT = {
    "confirmed (record)": 4,
    "confirmed (index)": 3,
    "confirmed (family)": 3,
    "confirmed (chart)": 3,
    "probable (strong)": 2,
    "probable": 2,
    "needs confirmation": 1,
    "possible": 1,
}
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
RELATION_TO_GRAMPS = {
    "biological": "Birth",
    "adoptive": "Adopted",
    "step": "Stepchild",
    "foster": "Foster",
    "legal": "Legal",
}
CHART_NAME_RE = re.compile(r"(tree|chart|pedigree|r18_|r32_|v6[0-9])", re.I)


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def birth_year(text: str) -> int | None:
    if not text:
        return None
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", text)
    return int(m.group(1)) if m else None


def load_jsonl(path: Path) -> list[dict]:
    """Append-only log: last record of an id wins; delete drops it."""
    by_id: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARN {path}:{lineno} JSON error: {e}")
                continue
            oid = obj.get("id")
            if not oid:
                print(f"WARN {path}:{lineno} missing id")
                continue
            if obj.get("delete") is True:
                by_id.pop(oid, None)
                continue
            obj["_source_file"] = str(path)
            obj["_lineno"] = lineno
            by_id[oid] = obj
    return list(by_id.values())


def mime_for(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
        ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(path.suffix.lower(), "application/octet-stream")


class Attacher:
    def __init__(self, api: Api):
        self.api = api
        self.unmatched: list[dict] = []
        self.stats = {"applied": 0, "skipped_existing": 0, "created_people": 0, "errors": 0}
        self.tags = self._ensure_tags()
        self.people = api.get("/api/people/?profile=self") or []
        self.by_gid = {p["gramps_id"]: p for p in self.people}
        self.by_handle = {p["handle"]: p for p in self.people}
        self.families = api.get("/api/families/?profile=self") or []
        self.sources_by_title = {s["title"]: s for s in (api.get("/api/sources/") or [])}
        self.media = api.get("/api/media/") or []
        self.media_by_desc = {(m.get("desc") or ""): m for m in self.media}
        self.citations = api.get("/api/citations/") or []
        self.index_rows = []
        if INDEX.exists():
            with INDEX.open(encoding="utf-8") as f:
                self.index_rows = list(csv.DictReader(f))
        self._cindy_lines = []
        if CINDY_RECORD.exists():
            self._cindy_lines = CINDY_RECORD.read_text(encoding="utf-8", errors="replace").splitlines()
        self.stats.update({"created_people_list": [], "media_added": 0, "urls_added": 0, "notes_added": 0})

    def _ensure_tags(self) -> dict:
        tags = {t["name"]: t["handle"] for t in (self.api.get("/api/tags/") or [])}
        for name, color in TAG_SPEC:
            if name in tags:
                continue
            resp = self.api.post("/api/tags/", {"_class": "Tag", "name": name, "color": color})
            tags[name] = first_new_handle(resp)
            print("TAG created", name)
        return tags

    def name_of(self, p: dict) -> str:
        pn = p.get("primary_name") or {}
        first = (pn.get("first_name") or "").strip()
        surnames = " ".join(s.get("surname", "") for s in (pn.get("surname_list") or []) if s.get("surname"))
        return " ".join(x for x in (first, surnames) if x).strip()

    def person_birth_year(self, p: dict) -> int | None:
        prof = p.get("profile") or {}
        return birth_year((prof.get("birth") or {}).get("date") or "")

    def _given_ok(self, a: str, b: str) -> bool:
        a = fold(a).split()[0] if a else ""
        b = fold(b).split()[0] if b else ""
        if not a or not b:
            return False
        return a == b or b in GIVEN_ALIASES.get(a, {a})

    def _is_living(self, p: dict) -> bool:
        if p.get("private"):
            return True
        row = next((r for r in self.index_rows if r["gramps_id"] == p.get("gramps_id")), None)
        return bool(row and row.get("living") == "true")

    def strong_name_match(self, token: str, p: dict, require_surname: bool = True) -> bool:
        """Match a query like 'Philipp Beyl' to an existing person. Never first-name-only
        against a living person, and never first-name-only when require_surname is set."""
        tok = fold(token)
        if not tok:
            return False
        parts = tok.split()
        living = self._is_living(p)
        pn = fold(self.name_of(p))
        given = fold((p.get("primary_name") or {}).get("first_name") or "")
        surnames = fold(" ".join(s.get("surname", "") for s in ((p.get("primary_name") or {}).get("surname_list") or []) if s.get("surname")))
        if living and require_surname:
            # living people have no surname in the tree; only match if the token is first-name only
            # and unique — caller must enforce uniqueness. Refuse first+last vs living first-name.
            if len(parts) >= 2:
                return False
            return tok == fold(given) or tok == pn
        if len(parts) == 1:
            if require_surname:
                return False
            return self._given_ok(parts[0], given) and not surnames
        # first + last (middle names optional)
        first, last = parts[0], parts[-1]
        if not self._given_ok(first, given.split()[0] if given else ""):
            return False
        if last and last in (surnames, pn):
            return True
        if surnames and (surnames.endswith(last) or last == surnames.split()[-1] if surnames else False):
            return True
        return False

    def match_person(self, field, entry_id: str):
        if field is None:
            return None, "missing person"
        if isinstance(field, str):
            if field in self.by_gid:
                return self.by_gid[field], None
            return None, f"unknown gramps_id {field}"
        if not isinstance(field, dict):
            return None, "person must be a gramps_id or {name, birth, parents}"
        name = (field.get("name") or "").strip()
        if not name:
            return None, "person.name missing"
        byear = birth_year(field.get("birth") or "")
        cands = []
        for p in self.people:
            if not self.strong_name_match(name, p, require_surname=len(fold(name).split()) >= 2):
                # allow exact folded full-name equality even if alias map missed
                if fold(name) != fold(self.name_of(p)):
                    continue
            py = self.person_birth_year(p)
            if byear and py and abs(byear - py) > 2:
                continue
            cands.append(p)
        if len(cands) == 1:
            return cands[0], None
        if len(cands) > 1:
            # if birth year unique among them, use it
            if byear:
                tight = [p for p in cands if self.person_birth_year(p) == byear]
                if len(tight) == 1:
                    return tight[0], None
            ids = ",".join(p["gramps_id"] for p in cands[:8])
            return None, f"ambiguous match ({ids})"
        return None, "no match"

    def resolve_parent(self, token: str, also: list) -> tuple:
        token = (token or "").strip()
        if not token:
            return None, "empty parent token"
        if token.startswith("I") and token[1:].isdigit() and token in self.by_gid:
            return self.by_gid[token], None
        also_hits = [p for p in also if self.strong_name_match(token, p, require_surname=True)]
        if len(also_hits) == 1:
            return also_hits[0], None
        if len(also_hits) > 1:
            ids = ",".join(p["gramps_id"] for p in also_hits)
            return None, f"ambiguous parent {token} in also_for ({ids})"
        hits = [p for p in self.people if self.strong_name_match(token, p, require_surname=True)]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            ids = ",".join(f"{p['gramps_id']} {self.name_of(p)}" for p in hits[:8])
            return None, f"ambiguous parent {token} ({ids})"
        return None, f"parent not in tree: {token}"

    def family_record_check(self, name: str, birth: str, parent_names: list[str]) -> str | None:
        """Return an error string if CINDY_FAMILY_RECORD.md does not support creating this stub."""
        if not self._cindy_lines:
            return "CINDY_FAMILY_RECORD.md not readable"
        needle = fold(name)
        if not needle:
            return "person.name missing"
        # require the surname (last token) plus given, to avoid 'William' / 'Henry' floods
        parts = needle.split()
        hits = []
        for i, line in enumerate(self._cindy_lines):
            fl = fold(line)
            if needle in fl:
                hits.append(i)
            elif len(parts) >= 2 and parts[0] in fl and parts[-1] in fl:
                # 'Heinrich Beyl' vs 'Henry Beyl'
                given_aliases = GIVEN_ALIASES.get(parts[0], {parts[0]})
                if any(g in fl.split() for g in given_aliases) and parts[-1] in fl:
                    hits.append(i)
        if not hits:
            return f"not found in CINDY_FAMILY_RECORD.md — not created"
        if not parent_names:
            return None  # caller already rejected empty parents
        parent_folds = [fold(x) for x in parent_names if x]
        # a supporting window: ~50 lines around a hit, or until a '### ' heading
        def window(idx):
            lo = idx
            while lo > 0 and not self._cindy_lines[lo].startswith("### "):
                lo -= 1
                if idx - lo > 80:
                    break
            hi = idx
            while hi < len(self._cindy_lines) - 1 and not self._cindy_lines[hi + 1].startswith("### "):
                hi += 1
                if hi - idx > 80:
                    break
            return fold(" ".join(self._cindy_lines[lo:hi + 1]))

        for idx in hits:
            w = window(idx)
            ok = True
            for pf in parent_folds:
                pp = pf.split()
                if pf in w:
                    continue
                # first+last present
                if len(pp) >= 2 and self._token_in(pp[0], w) and pp[-1] in w:
                    continue
                ok = False
                break
            if ok:
                return None
        return "parents not corroborated in CINDY_FAMILY_RECORD.md near this person"

    def _token_in(self, given: str, blob: str) -> bool:
        aliases = GIVEN_ALIASES.get(given, {given})
        words = set(blob.split())
        return any(a in words for a in aliases)

    def _parent_couple_ok(self, resolved: list) -> str | None:
        """Reject a stub whose resolved 'parents' are not a couple — e.g. a man and
        his own mother, which happened when a manifest named 'Christopher F. Wolford'
        for a child of the older Christian/Christopher Wolford."""
        if len(resolved) < 2:
            return None
        a, b = resolved[0], resolved[1]
        # already a known couple?
        if self.find_family(a["handle"], b["handle"]):
            return None
        # generation conflict: one is already a parent of the other
        def children_of(p):
            kids = set()
            obj = self.api.get(f"/api/people/{p['handle']}")
            for fh in (obj.get("family_list") or []):
                f = self.api.get(f"/api/families/{fh}")
                for cr in f.get("child_ref_list") or []:
                    kids.add(cr["ref"])
            return kids
        if b["handle"] in children_of(a):
            return (f"resolved parents are not a couple (generation conflict: "
                    f"{b['gramps_id']} {self.name_of(b)} is a child of "
                    f"{a['gramps_id']} {self.name_of(a)})")
        if a["handle"] in children_of(b):
            return (f"resolved parents are not a couple (generation conflict: "
                    f"{a['gramps_id']} {self.name_of(a)} is a child of "
                    f"{b['gramps_id']} {self.name_of(b)})")
        # not an existing couple and not a direct parent-child: allow, but only if
        # they are not already in a parent-child relationship. OK to proceed
        # (a one-off pair may be new).
        return None

    # --- root people (no parents listed / parents explicitly unknown) -------------
    # A stub may be created without parents only when the entry itself cites record
    # support (a record URL or file, confidence confirmed/probable, and a supported
    # fact other than just "other") AND the person is tied into the tree by another
    # live manifest stub that lists them as a parent. Parents are never invented:
    # a root person is created with no parent family at all.
    UNKNOWN_PARENT = {"", "unknown", "?", "parents unknown", "none"}

    def _clean_parents(self, field: dict) -> list:
        if field.get("parents_unknown"):
            return []
        return [p for p in (field.get("parents") or [])
                if str(p).strip() and fold(str(p)) not in self.UNKNOWN_PARENT]

    def _record_support(self, entry: dict) -> str | None:
        conf = (entry.get("confidence") or "").lower()
        if not (conf.startswith("confirmed") or conf.startswith("probable")):
            return f"confidence '{entry.get('confidence')}' is not record support"
        if not (entry.get("url") or entry.get("path")):
            return "no record URL or file"
        sup = [x for x in (entry.get("supports") or []) if x and x != "other"]
        if not sup:
            return "supports nothing but 'other'"
        return None

    def _named_in_title(self, name: str, entry: dict) -> bool:
        parts = fold(name).split()
        blob = fold(entry.get("title") or "")
        if not parts or not blob:
            return False
        return self._token_in(parts[0], blob) and parts[-1] in blob.split()

    def _create_root(self, name: str, birth: str, entry: dict, why: str):
        living = bool(entry.get("private"))
        parts = name.split()
        first = parts[0] if living else (" ".join(parts[:-1]) if len(parts) > 1 else name)
        surname = "" if living or len(parts) < 2 else parts[-1]
        gkey = fold(first).split()[0] if first else ""
        gender = 0 if gkey in FEMALE_GIVEN else 1 if gkey in MALE_GIVEN else 2  # Gramps: 0 F, 1 M, 2 unknown
        note = self.ensure_note(
            f"Created from website manifest {entry.get('id')} ({entry.get('_source_file')}) as a root person: "
            f"{why}. Identity as given: {json.dumps({'name': name, 'birth': birth}, ensure_ascii=False)}. "
            f"Parents unknown; none were invented.",
            typ="General", private=living,
        )
        payload = {
            "_class": "Person",
            "primary_name": {"_class": "Name", "first_name": first,
                             "surname_list": ([{"_class": "Surname", "surname": surname, "primary": True,
                                                "prefix": "", "connector": "", "origintype": "Inherited"}]
                                              if surname else []),
                             "suffix": "", "nick": "", "type": "Birth Name"},
            "gender": gender, "private": living, "note_list": [note],
        }
        h = first_new_handle(self.api.post("/api/people/", payload))
        p = self.api.get(f"/api/people/{h}?profile=self")
        self.people.append(p)
        self.by_gid[p["gramps_id"]] = p
        self.by_handle[h] = p
        self.stats["created_people"] += 1
        self.stats.setdefault("created_people_list", []).append({
            "gramps_id": p["gramps_id"], "name": first if living else name, "birth": birth,
            "parents": [], "from": entry.get("id"), "living": living, "root": True})
        print("CREATED ROOT PERSON", p["gramps_id"], first if living else name, "from", entry.get("id"))
        if birth:
            self._add_birth_event(p, birth, living)
        return p

    def maybe_create_person(self, field: dict, entry: dict):
        """Create a stub only when every listed parent uniquely resolves to an existing
        person and CINDY_FAMILY_RECORD.md corroborates the identity. Otherwise return
        (None, reason) for manifest_unmatched.csv."""
        name = (field.get("name") or "").strip()
        birth = (field.get("birth") or "").strip()
        parent_names = self._clean_parents(field)
        if not name:
            return None, "person.name missing"
        if not parent_names:
            sup_err = self._record_support(entry)
            if sup_err:
                return None, f"parents not given and no record support ({sup_err}) — not created"
            if fold(name) not in getattr(self, "stub_parent_names", set()):
                return None, ("parents not given — not created (a root person needs another manifest "
                              "entry that names them as a parent)")
            rec_err = self.family_record_check(name, birth, [])
            if rec_err:
                return None, rec_err
            p = self._create_root(name, birth, entry,
                                  f"record support ({entry.get('confidence')}; {entry.get('url') or entry.get('path')}); "
                                  f"named as a parent in another manifest entry")
            return p, None

        also = []
        for gid in entry.get("also_for") or []:
            if gid in self.by_gid:
                also.append(self.by_gid[gid])

        resolved = []
        for tok in parent_names:
            par, err = self.resolve_parent(tok, also)
            if par is None and (err or "").startswith("parent not in tree"):
                # The parent may be created as a root person when this entry's own
                # record names them (title) and gives record support.
                if (self._record_support(entry) is None and self._named_in_title(tok, entry)
                        and self.family_record_check(tok, "", []) is None):
                    par = self._create_root(tok, "", entry,
                                            f"named as a parent of {name} in the record this entry cites "
                                            f"({entry.get('url') or entry.get('path')})")
                    err = None
            if par is None:
                return None, err or f"parent does not resolve: {tok}"
            resolved.append(par)

        couple_err = self._parent_couple_ok(resolved)
        if couple_err:
            return None, couple_err

        rec_err = self.family_record_check(name, birth, parent_names)
        if rec_err:
            return None, rec_err

        living = bool(entry.get("private"))
        # first name only and private for living people
        parts = name.split()
        # Last token is the surname; all preceding tokens are given names.
        # (The previous split treated "Arthur Leroy Libengood" as given=Arthur,
        # surname="Leroy Libengood".)
        if living:
            first = parts[0] if parts else name
            surname = ""
        elif len(parts) > 1:
            first = " ".join(parts[:-1])
            surname = parts[-1]
        else:
            first = parts[0] if parts else name
            surname = ""
        given_key = fold(first).split()[0] if first else ""
        gender = 0 if given_key in FEMALE_GIVEN else 1 if given_key in MALE_GIVEN else 2  # Gramps: 0 F, 1 M, 2 unknown
        payload = {
            "_class": "Person",
            "primary_name": {
                "_class": "Name",
                "first_name": first if not living else first,
                "surname_list": ([{"_class": "Surname", "surname": surname, "primary": True,
                                   "prefix": "", "connector": "", "origintype": "Inherited"}]
                                 if surname else []),
                "suffix": "", "nick": "", "type": "Birth Name",
            },
            "gender": gender,
            "private": living,
            "note_list": [],
        }
        ident = {"name": name, "birth": birth, "parents": parent_names,
                 "resolved_parents": [p["gramps_id"] + " " + self.name_of(p) for p in resolved]}
        note = self.ensure_note(
            f"Created from website manifest {entry.get('id')} ({entry.get('_source_file')}). "
            f"Identity as given: {json.dumps(ident, ensure_ascii=False)}. "
            f"Validated against CINDY_FAMILY_RECORD.md; parents resolved to existing people.",
            typ="General", private=living,
        )
        payload["note_list"] = [note]
        resp = self.api.post("/api/people/", payload)
        h = first_new_handle(resp)
        p = self.api.get(f"/api/people/{h}?profile=self")
        self.people.append(p)
        self.by_gid[p["gramps_id"]] = p
        self.by_handle[h] = p
        self.stats["created_people"] += 1
        self.stats.setdefault("created_people_list", []).append({
            "gramps_id": p["gramps_id"], "name": name if not living else first,
            "birth": birth,
            "parents": [x["gramps_id"] + " " + self.name_of(x) for x in resolved],
            "from": entry.get("id"),
            "living": living,
        })
        print("CREATED PERSON", p["gramps_id"], name if not living else first, "from", entry.get("id"),
              "parents", [x["gramps_id"] for x in resolved])

        if birth:
            self._add_birth_event(p, birth, living)
        self._link_to_parents(p, resolved, living, entry)
        return p, None

    def _add_birth_event(self, person, birth, private):
        year = birth_year(birth)
        date = {
            "_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
            "newyear": 0, "dateval": [0, 0, year or 0, False], "text": birth,
            "year": year or 0,
        }
        # try to parse ISO yyyy-mm-dd
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", birth)
        if m:
            date["dateval"] = [int(m.group(3)), int(m.group(2)), int(m.group(1)), False]
            date["year"] = int(m.group(1))
            date["text"] = ""
        ev = {
            "_class": "Event", "type": "Birth", "description": "",
            "private": private, "date": date, "place": "",
            "citation_list": [], "note_list": [],
        }
        obj = self.api.get(f"/api/people/{person['handle']}")
        for r in obj.get("event_ref_list") or []:  # round 5: never a second Birth
            if self.api.get(f"/api/events/{r['ref']}").get("type") == "Birth":
                return
        resp = self.api.post("/api/events/", ev)
        eh = first_new_handle(resp)
        try:
            obj = self.api.get(f"/api/people/{person['handle']}")
            obj.setdefault("event_ref_list", []).append({
                "ref": eh, "role": "Primary", "private": private,
                "note_list": [], "attribute_list": [],
            })
            if obj.get("birth_ref_index", -1) < 0:
                obj["birth_ref_index"] = len(obj["event_ref_list"]) - 1
            self.api.put(f"/api/people/{obj['handle']}", obj)
        except Exception:
            self.api.request("DELETE", f"/api/events/{eh}")  # never leave an orphan event
            raise

    def _link_to_parents(self, child, parents, private, entry):
        """Add the new child to the existing parent couple's family, or a one-parent
        family if only one parent was listed. Do not invent a missing parent or
        retag the family's existing confidence."""
        if not parents:
            return
        if len(parents) >= 2:
            fam = self.find_family(parents[0]["handle"], parents[1]["handle"])
            if not fam:
                # do not invent a marriage; create a family to hold the child-parent links only
                fam = self._new_family(parents[0], parents[1], private)
        else:
            # one listed parent: use or make a single-parent family, do not guess the other spouse
            fam = None
            parent = parents[0]
            obj = self.api.get(f"/api/people/{parent['handle']}")
            singles = []
            for fh in obj.get("family_list") or []:
                f = self.api.get(f"/api/families/{fh}")
                father, mother = f.get("father_handle") or "", f.get("mother_handle") or ""
                if father == parent["handle"] and not mother:
                    singles.append(f)
                elif mother == parent["handle"] and not father:
                    singles.append(f)
            if len(singles) == 1:
                fam = singles[0]
            else:
                fam = self._new_family(parent, None, private)
        fam = self.api.get(f"/api/families/{fam['handle']}")
        have = {cr["ref"] for cr in (fam.get("child_ref_list") or [])}
        if child["handle"] not in have:
            frel = mrel = "Birth"
            rel = RELATION_TO_GRAMPS.get((entry.get("relation") or "biological").lower(), "Birth")
            # apply relation on the listed parent side(s)
            handles = {p["handle"] for p in parents}
            frel = rel if fam.get("father_handle") in handles else "Birth"
            mrel = rel if fam.get("mother_handle") in handles else "Birth"
            # if only one parent seated, put relation on that side
            if fam.get("father_handle") in handles and not fam.get("mother_handle"):
                frel, mrel = rel, "Birth"
            if fam.get("mother_handle") in handles and not fam.get("father_handle"):
                frel, mrel = "Birth", rel
            fam.setdefault("child_ref_list", []).append({
                "ref": child["handle"], "frel": frel, "mrel": mrel,
                "private": private, "citation_list": [], "note_list": [],
            })
            self.api.put(f"/api/families/{fam['handle']}", fam)
        # link parent_family onto the child
        ch = self.api.get(f"/api/people/{child['handle']}")
        if fam["handle"] not in (ch.get("parent_family_list") or []):
            ch.setdefault("parent_family_list", []).append(fam["handle"])
            self.api.put(f"/api/people/{ch['handle']}", ch)

    def _new_family(self, p1, p2, private):
        father = mother = None
        for p in (p1, p2):
            if not p:
                continue
            if p.get("gender") == 1 and father is None:
                father = p
            elif p.get("gender") == 0 and mother is None:
                mother = p
        if p1 and p2 and (father is None or mother is None):
            # fall back to order: first listed, then second
            if father is None:
                father = p1 if p1 is not mother else p2
            if mother is None:
                mother = p2 if p2 is not father else p1
        if p1 and not p2:
            if p1.get("gender") == 0:
                mother = p1
            else:
                father = p1
        payload = {
            "_class": "Family",
            "father_handle": (father or {}).get("handle") or "",
            "mother_handle": (mother or {}).get("handle") or "",
            "type": "Unknown",
            "private": private,
            "child_ref_list": [], "event_ref_list": [], "note_list": [],
            "tag_list": [], "citation_list": [],
        }
        resp = self.api.post("/api/families/", payload)
        h = first_new_handle(resp)
        fam = self.api.get(f"/api/families/{h}")
        self.families.append(fam)
        for p in (p1, p2):
            if not p:
                continue
            obj = self.api.get(f"/api/people/{p['handle']}")
            if h not in (obj.get("family_list") or []):
                obj.setdefault("family_list", []).append(h)
                self.api.put(f"/api/people/{obj['handle']}", obj)
        return fam

    def ensure_note(self, text: str, typ="General", private=False) -> str:
        resp = self.api.post("/api/notes/", {
            "_class": "Note", "type": typ, "private": private, "format": 0,
            "text": {"string": text, "tags": []},
        })
        return first_new_handle(resp)

    def person_has_note(self, person, text: str) -> bool:
        for nh in person.get("note_list") or []:
            n = self.api.get(f"/api/notes/{nh}")
            s = ((n.get("text") or {}).get("string") or "")
            if s.strip() == text.strip() or text.strip() in s:
                return True
        return False

    def ensure_source(self, title: str, author="", pubinfo="") -> str:
        title = title or "Untitled source"
        if title in self.sources_by_title:
            return self.sources_by_title[title]["handle"]
        resp = self.api.post("/api/sources/", {
            "_class": "Source", "title": title, "abbrev": title[:40],
            "author": author or "", "pubinfo": pubinfo or "",
        })
        h = first_new_handle(resp)
        self.sources_by_title[title] = {"handle": h, "title": title}
        return h

    def find_citation(self, source_h: str, page: str):
        for c in self.citations:
            if c.get("source_handle") == source_h and (c.get("page") or "") == (page or ""):
                return c
        return None

    def ensure_citation(self, source_h, page, confidence, private, media_handles, note_text=None):
        existing = self.find_citation(source_h, page)
        if existing:
            # add missing media / notes
            c = self.api.get(f"/api/citations/{existing['handle']}")
            changed = False
            have = {m["ref"] for m in (c.get("media_list") or [])}
            for mh in media_handles or []:
                if mh not in have:
                    c.setdefault("media_list", []).append({
                        "ref": mh, "rect": [], "private": private,
                        "citation_list": [], "note_list": [], "attribute_list": [],
                    })
                    changed = True
            if note_text:
                has = False
                for nh in c.get("note_list") or []:
                    n = self.api.get(f"/api/notes/{nh}")
                    if note_text in ((n.get("text") or {}).get("string") or ""):
                        has = True
                if not has:
                    c.setdefault("note_list", []).append(
                        self.ensure_note(note_text, typ="Citation", private=private)
                    )
                    changed = True
            if changed:
                self.api.put(f"/api/citations/{c['handle']}", c)
            return c["handle"]
        notes = []
        if note_text:
            notes.append(self.ensure_note(note_text, typ="Citation", private=private))
        payload = {
            "_class": "Citation",
            "source_handle": source_h,
            "page": page or "",
            "confidence": CONF_TO_INT.get(confidence or "", 2),
            "private": bool(private),
            "media_list": [{
                "ref": mh, "rect": [], "private": private,
                "citation_list": [], "note_list": [], "attribute_list": [],
            } for mh in (media_handles or [])],
            "note_list": notes,
        }
        resp = self.api.post("/api/citations/", payload)
        h = first_new_handle(resp)
        self.citations.append({"handle": h, "source_handle": source_h, "page": page or ""})
        return h

    def ensure_media(self, path: Path, desc: str, private: bool) -> str | None:
        # One media object per file. Match on the file itself, never on the title alone:
        # two different files can carry the same title (e.g. a record page and its image).
        rp = os.path.realpath(str(path))
        md5 = hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else None
        def originals(m):
            return [os.path.realpath(str(a.get("value") or "")) for a in (m.get("attribute_list") or [])
                    if a.get("type") == "Original file"]
        for m in self.media:
            if rp in originals(m):
                if path.exists():
                    media_prep.ensure_media_small(self.api, m["handle"], path)
                return m["handle"]
        for m in self.media:
            if md5 and m.get("checksum") == md5:
                return m["handle"]  # byte-identical file already in the library
        for m in self.media:
            if originals(m):
                continue  # belongs to another file
            mdesc = (m.get("desc") or "").split(" [web-ready copy; original:")[0]
            blob = ((m.get("desc") or "") + " " + (m.get("path") or "")).lower()
            if (desc and mdesc.lower() == desc.lower()) or (path.name and path.name.lower() in blob):
                if path.exists():
                    media_prep.ensure_media_small(self.api, m["handle"], path)
                return m["handle"]
        if not path.exists():
            return None
        # Files over 10 MiB are uploaded as a web-ready derivative (media_prep.py);
        # the original path is kept in the media attributes. No server patch needed.
        up, info = media_prep.prepare_upload(path)
        raw = up.read_bytes()
        resp = self.api.post("/api/media/", raw, content_type=mime_for(up), timeout=180)
        h = first_new_handle(resp)
        media = self.api.get(f"/api/media/{h}")
        media["desc"] = desc
        media["private"] = bool(private)
        media = media_prep.set_derivative_metadata(media, info)
        if not info:
            media.setdefault("attribute_list", []).append(
                media_prep.derivative_attributes({"original": str(path), "original_bytes": path.stat().st_size,
                                                  "derivative": str(path), "derivative_bytes": path.stat().st_size})[0])
        self.api.put(f"/api/media/{h}", media)
        self.media.append(media)
        self.media_by_desc[desc] = media
        return h

    def link_citation_to_person(self, person, cite_h, media_hs, urls, private, tag_name=None):
        p = self.api.get(f"/api/people/{person['handle']}")
        changed = False
        if cite_h and cite_h not in (p.get("citation_list") or []):
            p.setdefault("citation_list", []).append(cite_h)
            changed = True
        have_m = {m["ref"] for m in (p.get("media_list") or [])}
        have_paths = set()
        for mref in p.get("media_list") or []:
            try:
                mo = self.api.get(f"/api/media/{mref['ref']}")
                if mo.get("path"):
                    have_paths.add(mo["path"])
            except Exception:
                pass
        for mh in media_hs or []:
            already_path = False
            try:
                mo = self.api.get(f"/api/media/{mh}")
                already_path = bool(mo.get("path") and mo["path"] in have_paths)
            except Exception:
                mo = None
            if mh not in have_m and not already_path:
                p.setdefault("media_list", []).append({
                    "ref": mh, "rect": [], "private": private,
                    "citation_list": [], "note_list": [], "attribute_list": [],
                })
                changed = True
                if mo and mo.get("path"):
                    have_paths.add(mo["path"])
        if urls:
            have = {(u.get("path"), u.get("desc")) for u in (p.get("urls") or [])}
            for u in urls:
                key = (u.get("path"), u.get("desc"))
                if key not in have and u.get("path"):
                    p.setdefault("urls", []).append(u)
                    changed = True
        # Do not retag existing people with the entry's confidence (leave stored levels as-is).
        if changed:
            self.api.put(f"/api/people/{p['handle']}", p)
            self.by_gid[p["gramps_id"]] = p
            self.by_handle[p["handle"]] = p

    def attach_citation_to_supported_events(self, person, cite_h, supports: list[str]):
        types_wanted = set()
        for s in supports or []:
            head = s.split(":", 1)[0].lower()
            if head in ("birth", "death", "burial", "residence", "occupation", "marriage"):
                types_wanted.add(head.capitalize() if head != "occupation" else "Occupation")
        if not types_wanted:
            return
        p = self.api.get(f"/api/people/{person['handle']}")
        for er in p.get("event_ref_list") or []:
            ev = self.api.get(f"/api/events/{er['ref']}")
            typ = ev.get("type")
            if typ in types_wanted or (isinstance(typ, str) and typ.capitalize() in types_wanted):
                if cite_h not in (ev.get("citation_list") or []):
                    ev.setdefault("citation_list", []).append(cite_h)
                    self.api.put(f"/api/events/{ev['handle']}", ev)

    def find_family(self, a_handle, b_handle):
        for f in self.families:
            parents = {f.get("father_handle") or "", f.get("mother_handle") or ""}
            if a_handle in parents and b_handle in parents:
                return f
        return None

    def ensure_family(self, p1, p2, date_text="", place_text="", status="married",
                      confidence="", note_text="", private=False, relation_on_child=None):
        existing = self.find_family(p1["handle"], p2["handle"])
        sex = {p1["handle"]: p1.get("gender"), p2["handle"]: p2.get("gender")}
        father = mother = None
        if p1.get("gender") == 1:
            father, mother = p1, p2
        elif p2.get("gender") == 1:
            father, mother = p2, p1
        elif p1.get("gender") == 0:
            mother, father = p1, p2
        else:
            father, mother = p1, p2
        if existing:
            fam = self.api.get(f"/api/families/{existing['handle']}")
        else:
            payload = {
                "_class": "Family",
                "father_handle": father["handle"] if father else "",
                "mother_handle": mother["handle"] if mother else "",
                "type": "Divorced" if (status or "").lower() == "divorced" else "Married",
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
            # link onto spouses
            for p in (p1, p2):
                obj = self.api.get(f"/api/people/{p['handle']}")
                if h not in (obj.get("family_list") or []):
                    obj.setdefault("family_list", []).append(h)
                    self.api.put(f"/api/people/{obj['handle']}", obj)
            print("FAMILY created", fam.get("gramps_id"), p1.get("gramps_id"), p2.get("gramps_id"))

        if confidence and self.tags.get(confidence):
            if self.tags[confidence] not in (fam.get("tag_list") or []):
                fam.setdefault("tag_list", []).append(self.tags[confidence])
                self.api.put(f"/api/families/{fam['handle']}", fam)
                fam = self.api.get(f"/api/families/{fam['handle']}")

        if note_text:
            has = False
            for nh in fam.get("note_list") or []:
                n = self.api.get(f"/api/notes/{nh}")
                if note_text in ((n.get("text") or {}).get("string") or ""):
                    has = True
            if not has:
                fam.setdefault("note_list", []).append(
                    self.ensure_note(note_text, typ="General", private=private)
                )
                self.api.put(f"/api/families/{fam['handle']}", fam)
                fam = self.api.get(f"/api/families/{fam['handle']}")

        # marriage / divorce event
        ev_type = "Divorce" if (status or "").lower() == "divorced" else "Marriage"
        have = False
        year = birth_year(date_text or "")  # reuse year extractor
        for er in fam.get("event_ref_list") or []:
            ev = self.api.get(f"/api/events/{er['ref']}")
            if ev.get("type") != ev_type:
                continue
            ey = (ev.get("date") or {}).get("year") or ((ev.get("date") or {}).get("dateval") or [0, 0, 0])[2]
            if year and ey == year:
                have = True
                break
            if not year:
                have = True
                break
        if not have:
            date = {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0,
                    "newyear": 0, "dateval": [0, 0, year or 0, False], "text": date_text or ""}
            ev = {
                "_class": "Event", "type": ev_type, "description": date_text or "",
                "private": private, "date": date, "place": "",
                "citation_list": [], "note_list": [],
            }
            # if note_text, put a short event note
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
        return fam

    def apply_supports_parent(self, child, parent_gid, cite_h, relation, confidence, note_text, private):
        parent = self.by_gid.get(parent_gid)
        if not parent:
            return
        # find a family where parent is a parent and child is a child; else create
        fam = None
        child_full = self.api.get(f"/api/people/{child['handle']}")
        for fh in (child_full.get("parent_family_list") or []):
            f = self.api.get(f"/api/families/{fh}")
            if parent["handle"] in (f.get("father_handle"), f.get("mother_handle")):
                fam = f
                break
        if not fam:
            # don't invent a second parent
            return
        rel = RELATION_TO_GRAMPS.get((relation or "biological").lower(), "Birth")
        changed = False
        for cr in fam.get("child_ref_list") or []:
            if cr["ref"] != child["handle"]:
                continue
            if fam.get("father_handle") == parent["handle"] and cr.get("frel") != rel:
                cr["frel"] = rel
                changed = True
            if fam.get("mother_handle") == parent["handle"] and cr.get("mrel") != rel:
                cr["mrel"] = rel
                changed = True
            if cite_h and cite_h not in (cr.get("citation_list") or []):
                cr.setdefault("citation_list", []).append(cite_h)
                changed = True
        # Do not retag the family with the entry's confidence.
        if changed:
            self.api.put(f"/api/families/{fam['handle']}", fam)

    def apply_entry(self, entry: dict):
        eid = entry.get("id")
        kind = (entry.get("kind") or "").strip().lower()
        person_field = entry.get("person")
        person, err = self.match_person(person_field, eid)
        if person is None and isinstance(person_field, dict):
            created, cerr = self.maybe_create_person(person_field, entry)
            if created is not None:
                person, err = created, None
            else:
                err = cerr or err
        if person is None:
            self.unmatched.append({
                "id": eid, "source_file": entry.get("_source_file"),
                "kind": kind, "person": json.dumps(person_field, ensure_ascii=False),
                "reason": err or "unmatched",
                "title": entry.get("title") or entry.get("path") or entry.get("url") or "",
            })
            return

        also = []
        for gid in entry.get("also_for") or []:
            if gid in self.by_gid:
                also.append(self.by_gid[gid])
            else:
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"),
                    "kind": kind, "person": gid,
                    "reason": "also_for unknown gramps_id",
                    "title": entry.get("title") or "",
                })

        private = bool(entry.get("private")) or bool(person.get("private"))
        confidence = entry.get("confidence") or ""
        if confidence and confidence not in CONFIDENCE:
            print("WARN", eid, "unknown confidence", confidence)
        title = entry.get("title") or ""
        source_name = entry.get("source") or title or "Manifest source"
        date = entry.get("date") or ""
        url = entry.get("url") or ""
        source_log = entry.get("source_log") or ""
        supports = entry.get("supports") or []
        relation = entry.get("relation") or ""
        targets = [person] + also

        log_note = None
        if source_log:
            log_note = f"Research log: {source_log}"

        if kind == "note":
            text = (entry.get("text") or "").strip()
            if not text:
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                    "person": person["gramps_id"], "reason": "note missing text", "title": title,
                })
                return
            if entry.get("negative"):
                typ = "Research"
                # private-to-members research note, not a fact
                private = True
                text = "[Negative search — not a fact]\n" + text
            else:
                typ = "General"
            for p in targets:
                full = self.api.get(f"/api/people/{p['handle']}")
                if self.person_has_note(full, text if not entry.get("negative") else text.split("\n", 1)[-1][:80]):
                    self.stats["skipped_existing"] += 1
                    continue
                # tighter de-dupe for negative
                if self.person_has_note(full, text):
                    self.stats["skipped_existing"] += 1
                    continue
                full.setdefault("note_list", []).append(
                    self.ensure_note(text, typ=typ, private=private)
                )
                self.api.put(f"/api/people/{full['handle']}", full)
                self.stats["applied"] += 1
            return

        if kind == "spouse":
            sp = entry.get("spouse")
            sp_field = sp
            date_s, place_s, status = date, entry.get("place") or "", "married"
            if isinstance(sp, dict):
                sp_field = sp.get("gramps_id") or sp.get("person") or sp
                date_s = sp.get("date") or date_s
                place_s = sp.get("place") or place_s
                status = sp.get("status") or status
            spouse, serr = self.match_person(sp_field, eid)
            if spouse is None:
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                    "person": person["gramps_id"], "reason": f"spouse unmatched: {serr}",
                    "title": title or str(sp_field),
                })
                return
            note = title or (entry.get("text") or f"Manifest {eid}")
            if date_s or place_s:
                note = note + f" (date={date_s} place={place_s})"
            self.ensure_family(person, spouse, date_s, place_s, status, confidence, note, private)
            self.stats["applied"] += 1
            return

        if kind not in ("file", "url"):
            self.unmatched.append({
                "id": eid, "source_file": entry.get("_source_file"), "kind": kind or "(missing)",
                "person": person["gramps_id"], "reason": f"unknown kind {kind}",
                "title": title,
            })
            return

        media_hs = []
        if kind == "file":
            path = Path(entry.get("path") or "")
            if CHART_NAME_RE.search(path.name) and "census" not in path.name.lower():
                # still allow if title clearly isn't a generated chart; skip obvious charts
                if re.search(r"(family.?tree|pedigree|chart\.pdf|chart\.png)$", path.name, re.I):
                    self.unmatched.append({
                        "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                        "person": person["gramps_id"],
                        "reason": "skipped generated tree chart (not a record)",
                        "title": str(path),
                    })
                    return
            if not path or not path.exists():
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                    "person": person["gramps_id"],
                    "reason": f"missing file: {path}",
                    "title": title or str(path),
                })
                return
            desc = title or path.name
            try:
                mh = self.ensure_media(path, desc, private)
            except media_prep.OversizeError as exc:
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                    "person": person["gramps_id"], "reason": f"file over 10 MiB even after compression: {exc}",
                    "title": str(path),
                })
                return
            if not mh:
                self.unmatched.append({
                    "id": eid, "source_file": entry.get("_source_file"), "kind": kind,
                    "person": person["gramps_id"], "reason": "media upload failed",
                    "title": str(path),
                })
                return
            media_hs.append(mh)

        page = ""
        if kind == "file":
            page = Path(entry.get("path")).name
            if url:
                page = f"{page} {url}"
        elif url:
            page = url
        if date:
            page = (page + f" ({date})").strip()

        source_h = self.ensure_source(title or source_name, author=source_name, pubinfo=date)
        cite_h = self.ensure_citation(source_h, page, confidence, private, media_hs, note_text=log_note)

        urls = []
        if url:
            urls.append({
                "type": "Web Home",
                "path": url,
                "desc": title or source_name,
                "private": private,
            })

        already = True
        for p in targets:
            full = self.api.get(f"/api/people/{p['handle']}")
            if cite_h not in (full.get("citation_list") or []):
                already = False
            if url and not any((u.get("path") == url) for u in (full.get("urls") or [])):
                already = False
            self.link_citation_to_person(full, cite_h, media_hs, urls, private, confidence if confidence in self.tags else None)
            # "marriage" and "parent:" describe the entry's own person; an also_for person
            # (e.g. the groom's parents named on a marriage record) gets the record but
            # not those facts.
            is_primary = p["handle"] == targets[0]["handle"]
            own_supports = supports if is_primary else [
                x for x in (supports or [])
                if not (x.lower().startswith("marriage") or x.lower().startswith("parent:"))]
            self.attach_citation_to_supported_events(full, cite_h, own_supports)
            for s in own_supports or []:
                if s.lower().startswith("parent:"):
                    self.apply_supports_parent(
                        full, s.split(":", 1)[1].strip(), cite_h, relation, confidence, log_note, private
                    )
                if s.lower().startswith("marriage:"):
                    sp_gid = s.split(":", 1)[1].strip()
                    if sp_gid in self.by_gid:
                        fam = self.find_family(full["handle"], self.by_gid[sp_gid]["handle"])
                        if fam and cite_h not in (fam.get("citation_list") or []):
                            fam = self.api.get(f"/api/families/{fam['handle']}")
                            fam.setdefault("citation_list", []).append(cite_h)
                            if confidence and self.tags.get(confidence) and self.tags[confidence] not in (fam.get("tag_list") or []):
                                fam.setdefault("tag_list", []).append(self.tags[confidence])
                            self.api.put(f"/api/families/{fam['handle']}", fam)
        if already and kind == "url":
            self.stats["skipped_existing"] += 1
        else:
            self.stats["applied"] += 1

    def write_unmatched(self):
        UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
        with UNMATCHED.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "source_file", "kind", "person", "reason", "title"])
            w.writeheader()
            w.writerows(self.unmatched)
        os.chmod(UNMATCHED, 0o644)


def main():
    api = Api()
    att = Attacher(api)
    entries = []
    for path in MANIFESTS:
        if not path.exists():
            print(f"SKIP missing {path}")
            continue
        found = load_jsonl(path)
        print(f"Read {len(found)} live entries from {path}")
        entries.extend(found)
    overrides = media_overrides.load()
    att.stub_parent_names = {
        fold(str(x)) for e in entries if isinstance(e.get("person"), dict)
        for x in (e["person"].get("parents") or []) if str(x).strip()
    }
    for entry in entries:
        orig = entry
        entry, why = media_overrides.rewrite_entry(entry, overrides)
        if entry is None:
            att.unmatched.append({
                "id": orig.get("id"), "source_file": orig.get("_source_file"), "kind": orig.get("kind"),
                "person": json.dumps(orig.get("person"), ensure_ascii=False), "reason": why,
                "title": orig.get("title") or orig.get("path") or "",
            })
            continue
        try:
            att.apply_entry(entry)
        except Exception as e:
            att.stats["errors"] += 1
            att.unmatched.append({
                "id": entry.get("id"), "source_file": entry.get("_source_file"),
                "kind": entry.get("kind"), "person": json.dumps(entry.get("person"), ensure_ascii=False),
                "reason": f"error: {e}", "title": entry.get("title") or "",
            })
            print("ERROR", entry.get("id"), e)
    att.write_unmatched()
    print("manifest stats", att.stats, "unmatched", len(att.unmatched), "->", UNMATCHED)


if __name__ == "__main__":
    main()

#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

"""Show living people to restricted users by their first name only.

Collins family fork. With the `LIVING_FIRST_NAME_ONLY` setting, a user who
may not view private records sees each *private, living* person as a stub:
handle, Gramps ID, links to families, and the given name of the primary name
(without nicknames or other names kept in brackets or quotes in that field).
Everything else of the person is removed: surname, other names, gender,
events, dates, notes, citations, media, attributes, addresses, URLs,
associations, LDS ordinances and tags.

Families with a living parent are shown (private ones as stubs), so the tree
stays connected, but without their events, notes, citations, media,
attributes, tags or relationship type. Child references of living children,
and every child reference in a family with a living parent, lose their notes
and citations; living children's relation to their parents becomes the custom
relation type "Hidden".

Notes, events, citations and media reachable from what is removed are hidden
as primary objects too, also if something else visible refers to them, so
they can't be read through `/api/notes/` and the like. So is a source all of
whose citations are hidden this way, with its notes and media.

A person counts as living if the record is private and has no death, burial
or cremation event (as primary participant) and no death reference. Private
people who are dead stay hidden completely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from gramps.gen.db.dbconst import CLASS_TO_KEY_MAP
from gramps.gen.lib import Name
from gramps.gen.lib.json_utils import object_to_data, remove_object

from .sanitize import JsonValue, RawData, as_handle, as_list, as_object

# Gramps EventType values
DEATH_EVENT_TYPES = frozenset({13, 19, 24})  # death, burial, cremation
ROLE_PRIMARY = 1
GENDER_UNKNOWN = 2
CHILD_REF_CUSTOM = 7
# relation shown for a living child's links to its parents
HIDDEN_RELATION = "Hidden"
FAMILY_REL_UNKNOWN = 3
NAME_FORMAT_GIVEN = 4  # Name.FN

PERSON_KEY = CLASS_TO_KEY_MAP["Person"]
FAMILY_KEY = CLASS_TO_KEY_MAP["Family"]
# classes whose objects can be hidden because a living person refers to them
HIDEABLE = ("Note", "Event", "Citation", "Media", "Source")
# references that are followed when collecting what a living record refers to
REF_CLASSES = {"EventRef": "Event", "MediaRef": "Media"}
# keys never followed: links to other people/families, places, sources, repos
SKIP_KEYS = frozenset(
    {
        "family_list",
        "parent_family_list",
        "father_handle",
        "mother_handle",
        "child_ref_list",
        "place",
        "source_handle",
        "reporef_list",
        "placeref_list",
    }
)


@dataclass
class LivingRedaction:
    """What is shown as a stub or hidden for one database read."""

    people: frozenset[str] = frozenset()
    # families with a living father or mother
    families: frozenset[str] = frozenset()
    # class name -> handles of notes, events, ... hidden as primary objects
    hidden: dict[str, frozenset[str]] = field(default_factory=dict)

    def is_hidden(self, class_name: str, handle: Optional[str]) -> bool:
        return bool(handle) and handle in self.hidden.get(class_name, ())


def _collect(value: JsonValue, found: dict[str, set[str]]) -> None:
    """Collect handles of notes, citations, events and media under `value`."""
    if isinstance(value, list):
        for item in value:
            _collect(item, found)
        return
    if not isinstance(value, dict):
        return
    ref_class = REF_CLASSES.get(str(value.get("_class")))
    if ref_class and (handle := as_handle(value.get("ref"))):
        found[ref_class].add(handle)
    for key, item in value.items():
        if key in SKIP_KEYS:
            continue
        if key == "note_list" and isinstance(item, list):
            found["Note"].update(h for h in item if isinstance(h, str))
        elif key == "citation_list" and isinstance(item, list):
            found["Citation"].update(h for h in item if isinstance(h, str))
        else:
            _collect(item, found)


def _is_living(db, data: RawData) -> bool:
    if not data.get("private"):
        return False
    if isinstance(data.get("death_ref_index"), int) and data["death_ref_index"] >= 0:
        return False
    for ref in map(as_object, as_list(data.get("event_ref_list", []))):
        role = as_object(ref.get("role") or {}).get("value")
        if role != ROLE_PRIMARY:
            continue
        event = db._get_raw_data(CLASS_TO_KEY_MAP["Event"], as_handle(ref.get("ref")))
        if event and as_object(event.get("type") or {}).get("value") in DEATH_EVENT_TYPES:
            return False
    return True


def _expand(db, found: dict[str, set[str]], done: dict[str, set[str]]) -> None:
    """Add what the found events, citations and media refer to."""
    while True:
        todo = [
            (name, handle)
            for name in ("Event", "Citation", "Media")
            for handle in found[name] - done[name]
        ]
        if not todo:
            return
        for name, handle in todo:
            done[name].add(handle)
            data = db._get_raw_data(CLASS_TO_KEY_MAP[name], handle)
            if data is not None:
                _collect(data, found)


def compute_redaction(db) -> LivingRedaction:
    """Find the living people of the base database `db` and what to hide."""
    people = frozenset(
        handle
        for handle, data in db._iter_raw_data(PERSON_KEY)
        if data.get("private") and _is_living(db, data)
    )
    found: dict[str, set[str]] = {name: set() for name in HIDEABLE}
    for handle in people:
        _collect(db._get_raw_data(PERSON_KEY, handle), found)
    families = set()
    for handle, data in db._iter_raw_data(FAMILY_KEY):
        parents = {as_handle(data.get("father_handle")), as_handle(data.get("mother_handle"))}
        if parents & people:
            families.add(handle)
            _collect({k: v for k, v in data.items() if k != "child_ref_list"}, found)
            # every child reference of the family
            _collect(as_list(data.get("child_ref_list", [])), found)
        else:
            _collect(
                [
                    ref
                    for ref in as_list(data.get("child_ref_list", []))
                    if as_handle(as_object(ref).get("ref")) in people
                ],
                found,
            )
    # follow events, citations and media to their own notes, citations, media
    done: dict[str, set[str]] = {name: set() for name in HIDEABLE}
    _expand(db, found, done)
    # a source all of whose citations are hidden is about living people only
    # (its title would tell e.g. a marriage certificate and its year)
    citations_of: dict[str, list[str]] = {}
    for handle, data in db._iter_raw_data(CLASS_TO_KEY_MAP["Citation"]):
        source = as_handle(data.get("source_handle"))
        if source:
            citations_of.setdefault(source, []).append(handle)
    for source, citations in citations_of.items():
        if all(c in found["Citation"] for c in citations):
            found["Source"].add(source)
            data = db._get_raw_data(CLASS_TO_KEY_MAP["Source"], source)
            if data is not None:
                _collect(data, found)
    _expand(db, found, done)
    return LivingRedaction(
        people=people,
        families=frozenset(families),
        hidden={name: frozenset(handles) for name, handles in found.items()},
    )


def given_name_shown(first_name: str) -> str:
    """The given name without nicknames or other names kept in the same field,
    e.g. `Charles "Chip" (Chuck)` -> `Charles`, `Jack (Clifford John)` -> `Jack`."""
    return re.split(r'["(\[]', first_name, maxsplit=1)[0].strip()


def _first_name_only(name: RawData) -> RawData:
    stub = remove_object(object_to_data(Name()))
    stub["first_name"] = given_name_shown(str(name.get("first_name") or ""))
    # display and sort by the given name alone, not ", Given"
    stub["display_as"] = NAME_FORMAT_GIVEN
    stub["sort_as"] = NAME_FORMAT_GIVEN
    return stub


def person_stub(clean: RawData) -> RawData:
    """The raw data of a living person as shown to restricted users."""
    stub = dict(clean)
    stub.update(
        {
            "primary_name": _first_name_only(as_object(clean["primary_name"])),
            "alternate_names": [],
            "gender": GENDER_UNKNOWN,
            "event_ref_list": [],
            "birth_ref_index": -1,
            "death_ref_index": -1,
            "media_list": [],
            "address_list": [],
            "attribute_list": [],
            "urls": [],
            "lds_ord_list": [],
            "citation_list": [],
            "note_list": [],
            "person_ref_list": [],
            "tag_list": [],
            "change": 0,
        }
    )
    return stub


def family_stub(clean: RawData) -> RawData:
    """A family with a living parent, as shown to restricted users."""
    stub = dict(clean)
    stub.update(
        {
            "type": {"_class": "FamilyRelType", "value": FAMILY_REL_UNKNOWN, "string": ""},
            "event_ref_list": [],
            "media_list": [],
            "attribute_list": [],
            "lds_ord_list": [],
            "citation_list": [],
            "note_list": [],
            "tag_list": [],
            "change": 0,
        }
    )
    return stub


def strip_child_refs(
    clean: RawData, living: Iterable[str], all_refs: bool
) -> RawData:
    """Remove notes and citations of child references of living children (of
    all children if `all_refs`), and living children's relation types."""
    living = set(living)
    refs = []
    changed = False
    for ref in map(as_object, as_list(clean.get("child_ref_list", []))):
        child_living = as_handle(ref.get("ref")) in living
        if child_living or all_refs:
            ref = dict(ref, note_list=[], citation_list=[])
            if child_living:
                hidden = {
                    "_class": "ChildRefType",
                    "value": CHILD_REF_CUSTOM,
                    "string": HIDDEN_RELATION,
                }
                ref["frel"] = dict(hidden)
                ref["mrel"] = dict(hidden)
            changed = True
        refs.append(ref)
    if not changed:
        return clean
    return dict(clean, child_ref_list=refs)

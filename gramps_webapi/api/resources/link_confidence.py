#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

"""Child-parent link relation and confidence (Collins family tree lines).

GET /api/childref-confidence/?families=<handle>,<handle>,...
GET /api/childref-confidence/?person=<gramps id>&generations=<n>

For every child reference of the requested families (or of the families
followed by the all-parent-families ancestor walk from ``person``), returns
the relation of the child to each parent (frel/mrel) and a confidence level
per parent on the Gramps scale 0-4 (very low .. very high):

* ``citation``: the highest confidence of the citations on the child
  reference (applies to both parents, as Gramps child-reference citations
  are not per parent);
* ``note``: otherwise, the level parsed from a child-reference note of the
  form "Parent-child link confidence: - father: probable (strong) - ..."
  (the format our import pipeline writes); the free-text label is reduced to
  a short canonical word;
* ``null`` when neither exists.

The endpoint is read only. The database handle comes from ``get_db_handle()``,
so for users without the ViewPrivate permission private families, people,
child references, citations and notes are removed by Gramps' private proxy
exactly as in the other endpoints; nothing here reads the raw database.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from flask import Response, abort
from gramps.gen.const import GRAMPS_LOCALE as glocale
from gramps.gen.filters.rules import Rule
from webargs import fields, validate

from ..util import get_db_handle, use_args
from . import ProtectedResource
from .emit import GrampsJSONEncoder

_ = glocale.translation.gettext

MAX_FAMILIES = 250  # keeps the GET query below gunicorn's request-line limit
MAX_GENERATIONS = 30
PARENT_FAMILIES_PER_PERSON = 2  # birth/primary family + one more (4 parents)

LEVEL_NAMES = ["Very Low", "Low", "Normal", "High", "Very High"]

_ENTRY = re.compile(
    r"-\s*(father|mother|parent)\s*:\s*([^\n—-]+?)\s*(?:—|\n|-\s*(?:father|mother|parent)\s*:|$)",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def label_level(label: str) -> Optional[int]:
    """Map a confidence label from our notes to the Gramps 0-4 scale."""
    text = label.lower()
    if "confirmed" in text and "needs confirmation" not in text:
        if "record" in text or "image" in text:
            return 4
        return 3
    if "probable" in text:
        return 2
    if "possible" in text or "needs confirmation" in text:
        return 1
    if "weak" in text or "conjecture" in text or "unverified" in text:
        return 0
    return None


SHORT_LABELS = {
    4: "confirmed (record)",
    3: "confirmed",
    2: "probable",
    1: "possible",
    0: "weak",
}


def short_label(label: str) -> Optional[str]:
    """Reduce a long free-text confidence label to a short canonical word."""
    level = label_level(label)
    if level is None:
        return None
    if level == 1 and "needs confirmation" in label.lower():
        return "needs confirmation"
    return SHORT_LABELS[level]


def note_levels(texts: List[str]) -> Dict[str, Dict]:
    """Parse per-parent confidence from child-reference note texts."""
    found: Dict[str, Dict] = {}
    for text in texts:
        if "confidence" not in text.lower():
            continue
        for role, label in _ENTRY.findall(text):
            role = role.lower()
            level = label_level(label)
            if level is None or role in found:
                continue
            found[role] = {"level": level, "label": short_label(label)}
    result = {}
    for role in ("father", "mother"):
        entry = found.get(role) or found.get("parent")
        if entry:
            result[role] = entry
    return result


def _get(getter, handle):
    try:
        return getter(handle)
    except Exception:  # pylint: disable=broad-except
        return None


def ancestor_walk(
    db_handle,
    root_handle: str,
    generations: int,
    families_per_person: int = PARENT_FAMILIES_PER_PERSON,
) -> Tuple[Set[str], List[str]]:
    """Walk up the tree through the first ``families_per_person`` parent
    families of every person (not only the primary one).

    Returns the handles of the people reached (root included, ``generations``
    counts the root as generation 1, like IsLessThanNthGenerationAncestorOf)
    and the handles of the parent families followed, in visiting order.
    Uses only the given db handle, so private objects hidden by the proxy are
    neither returned nor followed.
    """
    people: Set[str] = set()
    families: List[str] = []
    seen_families: Set[str] = set()
    best_gen: Dict[str, int] = {}
    queue: List[Tuple[str, int]] = [(root_handle, 1)]
    while queue:
        handle, gen = queue.pop(0)
        if handle in best_gen and best_gen[handle] <= gen:
            continue
        best_gen[handle] = gen
        people.add(handle)
        if gen >= generations:
            continue
        person = _get(db_handle.get_person_from_handle, handle)
        if person is None:
            continue
        for family_handle in person.get_parent_family_handle_list()[
            :families_per_person
        ]:
            family = _get(db_handle.get_family_from_handle, family_handle)
            if family is None:
                continue
            if family_handle not in seen_families:
                seen_families.add(family_handle)
                families.append(family_handle)
            for parent in (family.get_father_handle(), family.get_mother_handle()):
                if parent:
                    queue.append((parent, gen + 1))
    return people, families


class IsLessThanNthGenerationAncestorOfAllFamilies(Rule):
    """Ancestors within N generations through all (up to two) parent families.

    Gramps' IsLessThanNthGenerationAncestorOf follows only the first parent
    family of each person, so adoptive/step/foster parents are never reached.
    """

    labels = [_("ID:"), _("Number of generations:")]
    name = _(
        "Ancestors of <person> not more than <N> generations away, "
        "through birth and other parent families"
    )
    category = _("Ancestral filters")
    description = _(
        "Matches people that are ancestors of a specified person not more "
        "than N generations away, following up to two parent families "
        "(e.g. birth and adoptive) of every person"
    )

    def prepare(self, db, user):
        self.selected_handles: Set[str] = set()
        person = db.get_person_from_gramps_id(self.list[0])
        if person:
            self.selected_handles, _families = ancestor_walk(
                db, person.handle, int(self.list[1])
            )

    def reset(self):
        self.selected_handles = set()

    def apply_to_one(self, db, person) -> bool:
        return person.handle in self.selected_handles


def _citation_info(db_handle, child_ref) -> List[Dict]:
    citations = []
    for citation_handle in child_ref.get_citation_list():
        citation = _get(db_handle.get_citation_from_handle, citation_handle)
        if citation is None:
            continue
        source = _get(db_handle.get_source_from_handle, citation.get_reference_handle())
        citations.append(
            {
                "handle": citation_handle,
                "gramps_id": citation.gramps_id,
                "confidence": citation.get_confidence_level(),
                "source_title": source.get_title() if source else None,
            }
        )
    return citations


def childref_links(db_handle, family_handle: str) -> List[Dict]:
    """Return the link records of one family (empty if hidden or missing)."""
    family = _get(db_handle.get_family_from_handle, family_handle)
    if family is None:
        return []
    out = []
    for child_ref in family.get_child_ref_list():
        citations = _citation_info(db_handle, child_ref)
        texts = []
        for note_handle in child_ref.get_note_list():
            note = _get(db_handle.get_note_from_handle, note_handle)
            if note is not None:
                texts.append(note.get())
        parsed = note_levels(texts)
        links = {}
        for role in ("father", "mother"):
            if citations:
                best = max(citations, key=lambda c: c["confidence"])
                level = best["confidence"]
                links[role] = {
                    "level": level,
                    "level_name": LEVEL_NAMES[level],
                    "source": "citation",
                    "label": best["source_title"] or best["gramps_id"],
                    "disagree": len({c["confidence"] for c in citations}) > 1,
                }
            elif role in parsed:
                level = parsed[role]["level"]
                links[role] = {
                    "level": level,
                    "level_name": LEVEL_NAMES[level],
                    "source": "note",
                    "label": parsed[role]["label"],
                    "disagree": False,
                }
            else:
                links[role] = {
                    "level": None,
                    "level_name": None,
                    "source": None,
                    "label": None,
                    "disagree": False,
                }
        # no relation/confidence for a parent slot that is empty or hidden
        for role, parent in (
            ("father", family.get_father_handle()),
            ("mother", family.get_mother_handle()),
        ):
            if not parent:
                links[role] = None
        out.append(
            {
                "family": family_handle,
                "child": child_ref.ref,
                "frel": (
                    str(child_ref.get_father_relation()) if links["father"] else None
                ),
                "mrel": (
                    str(child_ref.get_mother_relation()) if links["mother"] else None
                ),
                "citations": citations,
                "father": links["father"],
                "mother": links["mother"],
            }
        )
    return out


class ChildRefConfidenceResource(ProtectedResource, GrampsJSONEncoder):
    """Per-link relation and confidence for the child references of families."""

    @use_args(
        {
            "families": fields.Str(load_default=None),
            "person": fields.Str(load_default=None),
            "generations": fields.Integer(
                load_default=4, validate=validate.Range(min=1, max=MAX_GENERATIONS)
            ),
        },
        location="query",
    )
    def get(self, args: Dict) -> Response:
        """Return the child links of the requested families.

        Either ``families`` (comma-separated handles, at most 250) or
        ``person`` (a Gramps ID) with ``generations`` must be given.
        """
        db_handle = get_db_handle()
        if bool(args["families"]) == bool(args["person"]):
            abort(422)
        if args["families"]:
            handles = [h for h in args["families"].split(",") if h]
            if len(handles) > MAX_FAMILIES or not all(_HANDLE.match(h) for h in handles):
                abort(422)
            handles = list(dict.fromkeys(handles))
        else:
            person = _get(db_handle.get_person_from_gramps_id, args["person"])
            if person is None:
                abort(404)
            _people, handles = ancestor_walk(
                db_handle, person.handle, args["generations"]
            )
        out = []
        for family_handle in handles:
            out.extend(childref_links(db_handle, family_handle))
        return self.response(200, out)

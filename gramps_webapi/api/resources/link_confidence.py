#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

"""Child-parent link confidence (Collins family prototype).

GET /api/childref-confidence/?families=<handle>,<handle>,...

For every child reference of the requested families, returns the relation
of the child to each parent (frel/mrel) and a confidence level per parent on
the Gramps scale 0-4 (very low .. very high):

* ``citation``: the highest confidence of the citations on the child
  reference (applies to both parents, as Gramps child-reference citations
  are not per parent);
* ``note``: otherwise, the level parsed from a child-reference note of the
  form "Parent-child link confidence: - father: probable (strong) - ..."
  (the format our import pipeline writes);
* ``null`` when neither exists.

The database handle comes from ``get_db_handle()``, so for users without
the ViewPrivate permission private families, people, child references,
citations and notes are filtered out by Gramps' private proxy exactly as in
the other endpoints.
"""

import re
from typing import Dict, List, Optional

from flask import Response
from webargs import fields

from ..util import get_db_handle, use_args
from . import ProtectedResource
from .emit import GrampsJSONEncoder

MAX_FAMILIES = 500

_ENTRY = re.compile(
    r"-\s*(father|mother|parent)\s*:\s*([^\n—-]+?)\s*(?:—|\n|-\s*(?:father|mother|parent)\s*:|$)",
    re.IGNORECASE,
)


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
            found[role] = {"level": level, "label": label.strip()}
    result = {}
    for role in ("father", "mother"):
        entry = found.get(role) or found.get("parent")
        if entry:
            result[role] = entry
    return result


class ChildRefConfidenceResource(ProtectedResource, GrampsJSONEncoder):
    """Per-link relation and confidence for the child references of families."""

    @use_args(
        {"families": fields.Str(required=True)},
        location="query",
    )
    def get(self, args: Dict) -> Response:
        """Return the child links of the requested families."""
        db_handle = get_db_handle()
        handles = [h for h in args["families"].split(",") if h][:MAX_FAMILIES]
        out = []
        for family_handle in dict.fromkeys(handles):
            try:
                family = db_handle.get_family_from_handle(family_handle)
            except Exception:  # pylint: disable=broad-except
                family = None
            if family is None:
                continue
            for child_ref in family.get_child_ref_list():
                citations = []
                for citation_handle in child_ref.get_citation_list():
                    try:
                        citation = db_handle.get_citation_from_handle(citation_handle)
                    except Exception:  # pylint: disable=broad-except
                        citation = None
                    if citation is not None:
                        citations.append(
                            {
                                "handle": citation_handle,
                                "gramps_id": citation.gramps_id,
                                "confidence": citation.get_confidence_level(),
                            }
                        )
                texts = []
                for note_handle in child_ref.get_note_list():
                    try:
                        note = db_handle.get_note_from_handle(note_handle)
                    except Exception:  # pylint: disable=broad-except
                        note = None
                    if note is not None:
                        texts.append(note.get())
                parsed = note_levels(texts)
                links = {}
                for role in ("father", "mother"):
                    if citations:
                        best = max(c["confidence"] for c in citations)
                        links[role] = {"level": best, "source": "citation"}
                    elif role in parsed:
                        links[role] = {
                            "level": parsed[role]["level"],
                            "source": "note",
                            "label": parsed[role]["label"],
                        }
                    else:
                        links[role] = {"level": None, "source": None}
                out.append(
                    {
                        "family": family_handle,
                        "child": child_ref.ref,
                        "frel": str(child_ref.get_father_relation()),
                        "mrel": str(child_ref.get_mother_relation()),
                        "citations": citations,
                        "father": links["father"],
                        "mother": links["mother"],
                    }
                )
        return self.response(200, out)

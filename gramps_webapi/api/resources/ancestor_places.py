#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

"""Event places of a person and their ancestors (Collins family map).

GET /api/people/<handle>/ancestor-places/?generations=<n>&locale=<lang>

Walks up the tree from the person (generation 0) through the parent
families of every person reached (birth and up to one more family, as the
tree view's link lines do), up to ``generations`` generations back (parents
are generation 1; without ``generations``, as far as the tree goes, at most
``MAX_GENERATIONS``). People reached on more than one line are listed once,
at their nearest generation; loops in the data end the walk.

Returns the people reached, their events (events where they are the primary
participant, and the events of the parent families followed, i.e. the
marriages of the ancestral couples, plus the person's own family events),
the places of those events with their coordinates, and counts of what can't
be shown on a map (events without a place, places without coordinates).

Read only. The database handle comes from ``get_db_handle()``, so for users
without the ViewPrivate permission private people, families, events and
places are removed and living people are first-name stubs without events
(``LIVING_FIRST_NAME_ONLY``) exactly as in every other endpoint; nothing here
reads the raw database.
"""

from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from flask import Response, abort, jsonify
from gramps.gen.config import config
from gramps.gen.display.name import NameDisplay
from gramps.gen.display.place import PlaceDisplay
from gramps.gen.lib import EventRoleType
from gramps.gen.lib.json_utils import object_to_dict
from webargs import fields, validate

from ..util import get_db_handle, get_locale_for_language, use_args
from . import ProtectedResource
from .util import display_date, get_object_or_none, get_place_coordinates

MAX_GENERATIONS = 30
PARENT_FAMILIES_PER_PERSON = 2  # birth/primary family + one more, as the tree

_place_displayer = PlaceDisplay()


def ancestor_generations(
    db_handle,
    root_handle: str,
    generations: int,
    families_per_person: int = PARENT_FAMILIES_PER_PERSON,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Breadth-first walk up the tree from ``root_handle`` (generation 0).

    Returns ``{person handle: generation}`` (each person once, at the
    nearest generation) and ``{family handle: generation of its parents}``
    for the parent families followed. People and families hidden by the
    database handle (a privacy proxy) are neither returned nor followed.
    """
    people: Dict[str, int] = {root_handle: 0}
    families: Dict[str, int] = {}
    queue = deque([(root_handle, 0)])
    while queue:
        handle, gen = queue.popleft()
        if gen >= generations:
            continue
        person = get_object_or_none(db_handle, "person", handle)
        if person is None:
            continue
        for family_handle in person.get_parent_family_handle_list()[
            :families_per_person
        ]:
            if family_handle in families:
                continue
            family = get_object_or_none(db_handle, "family", family_handle)
            if family is None:
                continue
            families[family_handle] = gen + 1
            for parent in (family.get_father_handle(), family.get_mother_handle()):
                if parent and parent not in people:
                    if get_object_or_none(db_handle, "person", parent) is None:
                        continue
                    people[parent] = gen + 1
                    queue.append((parent, gen + 1))
    return people, families


def _year(date) -> Optional[int]:
    if date is None or date.is_empty() or not date.get_year_valid():
        return None
    year = date.get_year()
    return year or None


def ancestor_places(db_handle, root, generations: int, locale) -> Dict[str, Any]:
    """Collect the people, events and places for the response."""
    people_gen, families_gen = ancestor_generations(db_handle, root.handle, generations)
    name_displayer = NameDisplay(xlocale=locale)
    name_displayer.set_name_format(db_handle.name_formats)
    name_displayer.set_default_format(config.get("preferences.name-format"))

    people: Dict[str, Dict[str, Any]] = {}
    person_objs = {}
    for handle, gen in people_gen.items():
        person = get_object_or_none(db_handle, "person", handle)
        if person is None:
            continue
        person_objs[handle] = person
        people[handle] = {
            "handle": handle,
            "gramps_id": person.gramps_id,
            "name": name_displayer.display(person),
            "generation": gen,
        }

    # event handle -> list of (person handle) participants, generation
    participants: Dict[str, List[str]] = {}
    order: List[str] = []

    def add(event_handle: str, person_handles: List[str]):
        if event_handle not in participants:
            participants[event_handle] = []
            order.append(event_handle)
        for handle in person_handles:
            if handle in people and handle not in participants[event_handle]:
                participants[event_handle].append(handle)

    for handle, person in person_objs.items():
        for ref in person.get_event_ref_list():
            if ref.get_role() == EventRoleType.PRIMARY:
                add(ref.ref, [handle])
    # family events: the ancestral couples' families, and the root's own
    family_handles = list(families_gen)
    family_handles += [
        h
        for h in person_objs[root.handle].get_family_handle_list()
        if h not in families_gen
    ]
    for family_handle in family_handles:
        family = get_object_or_none(db_handle, "family", family_handle)
        if family is None:
            continue
        spouses = [
            h
            for h in (family.get_father_handle(), family.get_mother_handle())
            if h in people
        ]
        if not spouses:
            continue
        for ref in family.get_event_ref_list():
            if ref.get_role() == EventRoleType.FAMILY:
                add(ref.ref, spouses)

    events: List[Dict[str, Any]] = []
    places: Dict[str, Dict[str, Any]] = {}
    events_without_place = 0
    events_without_coordinates = 0
    for event_handle in order:
        who = participants[event_handle]
        event = get_object_or_none(db_handle, "event", event_handle)
        if event is None or not who:
            continue
        place_handle = event.get_place_handle() or None
        place = get_object_or_none(db_handle, "place", place_handle)
        if place is None:
            place_handle = None
            events_without_place += 1
        else:
            if place_handle not in places:
                lat, long = get_place_coordinates(place)
                places[place_handle] = {
                    "handle": place_handle,
                    "gramps_id": place.gramps_id,
                    "name": place.get_name().value,
                    "title": _place_displayer.display(db_handle, place),
                    "lat": lat,
                    "long": long,
                }
            if places[place_handle]["lat"] is None:
                events_without_coordinates += 1
        date = event.get_date_object()
        event_type = event.get_type()
        events.append(
            {
                "handle": event_handle,
                "gramps_id": event.gramps_id,
                "type": event_type.xml_str(),
                "type_label": locale.translation.sgettext(event_type.xml_str()),
                "date": object_to_dict(date),
                "date_str": display_date(date, locale),
                "year": _year(date),
                "place": place_handle,
                "people": who,
                "generation": min(people[h]["generation"] for h in who),
            }
        )

    places_without_coordinates = sum(1 for p in places.values() if p["lat"] is None)
    people_list = sorted(
        people.values(), key=lambda p: (p["generation"], p["name"], p["handle"])
    )
    return {
        "handle": root.handle,
        "gramps_id": root.gramps_id,
        "generations": generations,
        "max_generation": max((p["generation"] for p in people_list), default=0),
        "people": people_list,
        "events": events,
        "places": list(places.values()),
        "summary": {
            "people": len(people_list),
            "events": len(events),
            "events_without_place": events_without_place,
            "events_without_coordinates": events_without_coordinates,
            "places": len(places),
            "places_with_coordinates": len(places) - places_without_coordinates,
            "places_without_coordinates": places_without_coordinates,
        },
    }


class PersonAncestorPlacesResource(ProtectedResource):
    """Event places of a person and their ancestors, for the map."""

    @use_args(
        {
            "generations": fields.Integer(
                load_default=MAX_GENERATIONS,
                validate=validate.Range(min=0, max=MAX_GENERATIONS),
            ),
            "locale": fields.Str(load_default=None, validate=validate.Length(max=16)),
        },
        location="query",
    )
    def get(self, args: Dict, handle: str) -> Response:
        """Return the ancestors' events and their places."""
        db_handle = get_db_handle()
        person = get_object_or_none(db_handle, "person", handle)
        if person is None:
            abort(404)
        locale = get_locale_for_language(args["locale"], default=True)
        data = ancestor_places(db_handle, person, args["generations"], locale)
        # not GrampsJSONEncoder: it would turn null lat/long into 0
        return jsonify(data)

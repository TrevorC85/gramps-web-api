"""API tests for GET /api/people/<handle>/ancestor-places (Collins map).

Synthetic, clearly fictional tree (surname "Synthmap"), built here; never
imported anywhere else. Covers the walk (generation numbers, a person
reached on two lines, a loop in the data, an adoptive second family,
missing parents, the generations limit), the events and places returned,
the coordinates counts, and privacy for a guest with living people shown by
first name only (LIVING_FIRST_NAME_ONLY), as owner, member and guest.

    GGF Xaver + GGM Yvonne (gen 3)       F_X  (also: Ruth is a "parent" here: a loop)
        |                \\
    GF Gustav + GM Greta (gen 2, siblings: both children of F_X)    F_G
        |
    Dad Dieter + Mom Marta (gen 1)  F_P (marriage at Alpha)
        |   \\  Marta's parents: Lothar (private, living) + Hedda (private, dead)  F_M
    Ruth (gen 0), also adopted in F_A by Arno (gen 1, no mother)
    Ruth + Sam (not an ancestor) F_R, wedding at Beta
"""

import json
from unittest.mock import patch

import pytest
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import open_database
from gramps.gen.lib import (
    ChildRef,
    ChildRefType,
    Date,
    Event,
    EventRef,
    EventRoleType,
    EventType,
    Family,
    Name,
    Note,
    Person,
    Place,
    PlaceName,
    Surname,
)

from gramps_webapi.app import create_app
from gramps_webapi.auth import add_user, user_db
from gramps_webapi.auth.const import ROLE_GUEST, ROLE_MEMBER, ROLE_OWNER
from gramps_webapi.const import ENV_CONFIG_FILE, TEST_EXAMPLE_GRAMPS_AUTH_CONFIG
from gramps_webapi.dbmanager import WebDbManager

TREE_NAME = "ancestor_places_synthetic"
SURNAME = "Synthmap"
USERS = {
    "owner": ("ap_owner", "owner-pw", ROLE_OWNER),
    "member": ("ap_member", "member-pw", ROLE_MEMBER),
    "guest": ("ap_guest", "guest-pw", ROLE_GUEST),
}

PLACES = {
    # key: (name, lat, long, private)
    "alpha": ("Alphaville", "48.1", "11.5", False),
    "beta": ("Betatown", "-33.9", "18.4", False),
    "gamma": ("Gammaburg", "40.7", "-74.0", False),
    "delta": ("Deltaham", "", "", False),  # no coordinates
    "eps": ("Epsilon Springs", "51.5", "-0.1", False),
    "secret": ("Secretplace", "10.0", "10.0", True),  # private place
    "lothar": ("Lotharshome", "52.0", "13.0", False),  # only a living person's
    "hedda": ("Heddasgrave", "53.0", "14.0", False),  # only a private person's
    "burial": ("Privateburialfield", "54.0", "15.0", False),  # private event
}

PEOPLE = {
    # key: (gramps id, first name, gender, private)
    "ruth": ("SM0001", "Ruth", Person.FEMALE, False),
    "sam": ("SM0002", "Sam", Person.MALE, False),
    "dieter": ("SM0011", "Dieter", Person.MALE, False),
    "marta": ("SM0012", "Marta", Person.FEMALE, False),
    "arno": ("SM0013", "Arno", Person.MALE, False),
    "gustav": ("SM0021", "Gustav", Person.MALE, False),
    "greta": ("SM0022", "Greta", Person.FEMALE, False),
    "lothar": ("SM0023", 'Lothar "Lolo"', Person.MALE, True),  # private, living
    "hedda": ("SM0024", "Hedda", Person.FEMALE, True),  # private, dead
    "xaver": ("SM0031", "Xaver", Person.MALE, False),
    "yvonne": ("SM0032", "Yvonne", Person.FEMALE, False),
}

# person: [(event type, year, place key or None, private)]
EVENTS = {
    "ruth": [(EventType.BIRTH, 1950, "alpha", False)],
    "sam": [(EventType.BIRTH, 1949, "eps", False)],
    "dieter": [
        (EventType.BIRTH, 1920, "gamma", False),
        (EventType.DEATH, 1990, "delta", False),
        (EventType.BURIAL, 1990, "burial", True),
    ],
    "marta": [(EventType.BIRTH, 1922, None, False)],
    "arno": [(EventType.BAPTISM, 1915, "delta", False)],
    "gustav": [
        (EventType.BIRTH, 1890, "eps", False),
        (EventType.DEATH, 1960, "secret", False),
    ],
    "greta": [(EventType.BIRTH, 1892, "eps", False)],
    "lothar": [(EventType.BIRTH, 1900, "lothar", False)],
    "hedda": [
        (EventType.BIRTH, 1901, "hedda", False),
        (EventType.DEATH, 1970, "hedda", False),
    ],
    "xaver": [(EventType.BIRTH, 1860, "gamma", False)],
    "yvonne": [],
}

FAMILIES = {
    # key: (father, mother, children, (marriage year, place) or None, private)
    "F_R": ("sam", "ruth", [], (1975, "beta"), False),
    "F_P": ("dieter", "marta", ["ruth"], (1945, "alpha"), False),
    "F_A": ("arno", None, ["ruth"], None, False),
    "F_G": ("gustav", "greta", ["dieter"], (1915, "eps"), False),
    "F_M": ("lothar", "hedda", ["marta"], (1919, "hedda"), False),
    "F_X": ("xaver", "yvonne", ["gustav", "greta"], None, False),
    # a loop in the data: Ruth as a "parent" of Xaver
    "F_LOOP": ("sam", "ruth", ["xaver"], None, False),
}

LIVING_NOTE = "Synthmap living person's private note text"


def _add_event(db, trans, etype, year, place, private):
    event = Event()
    event.set_type(etype)
    event.set_date_object(Date(year))
    if place is not None:
        event.set_place_handle(place.handle)
    event.set_privacy(private)
    db.add_event(event, trans)
    return event


def build_tree(db):
    """Add the synthetic tree; returns {key: handle}."""
    h = {}
    with DbTxn("synthetic ancestor places tree (fictional)", db) as trans:
        places = {}
        for key, (name, lat, long, private) in PLACES.items():
            place = Place()
            pname = PlaceName()
            pname.set_value(name)
            place.set_name(pname)
            place.set_latitude(lat)
            place.set_longitude(long)
            place.set_privacy(private)
            db.add_place(place, trans)
            places[key] = place
            h["place:" + key] = place.handle
        people = {}
        for key, (gid, first, gender, private) in PEOPLE.items():
            person = Person()
            person.set_gramps_id(gid)
            name = Name()
            name.set_first_name(first)
            surname = Surname()
            surname.set_surname(SURNAME)
            name.add_surname(surname)
            person.set_primary_name(name)
            person.set_gender(gender)
            person.set_privacy(private)
            for etype, year, place_key, eprivate in EVENTS[key]:
                event = _add_event(
                    db,
                    trans,
                    etype,
                    year,
                    places[place_key] if place_key else None,
                    eprivate,
                )
                ref = EventRef()
                ref.ref = event.handle
                ref.set_role(EventRoleType.PRIMARY)
                person.add_event_ref(ref)
                if etype == EventType.BIRTH:
                    person.set_birth_ref(ref)
                h[f"event:{key}:{int(etype)}"] = event.handle
            if key == "lothar":
                note = Note(LIVING_NOTE)
                db.add_note(note, trans)
                person.add_note(note.handle)
            db.add_person(person, trans)
            people[key] = person
            h[key] = person.handle
        for key, (father, mother, children, marriage, private) in FAMILIES.items():
            family = Family()
            family.set_privacy(private)
            if father:
                family.set_father_handle(people[father].handle)
                people[father].add_family_handle(family.handle or "")
            if mother:
                family.set_mother_handle(people[mother].handle)
            for child in children:
                ref = ChildRef()
                ref.set_reference_handle(people[child].handle)
                if key == "F_A":
                    ref.set_father_relation(ChildRefType.ADOPTED)
                family.add_child_ref(ref)
            if marriage:
                year, place_key = marriage
                event = _add_event(
                    db, trans, EventType.MARRIAGE, year, places[place_key], False
                )
                ref = EventRef()
                ref.ref = event.handle
                ref.set_role(EventRoleType.FAMILY)
                family.add_event_ref(ref)
                h["event:" + key] = event.handle
            db.add_family(family, trans)
            h[key] = family.handle
            for parent in (father, mother):
                if parent:
                    people[parent].add_family_handle(family.handle)
            for child in children:
                people[child].add_parent_family_handle(family.handle)
        for person in people.values():
            person.set_family_handle_list(
                list(dict.fromkeys(person.get_family_handle_list()))
            )
            db.commit_person(person, trans)
    return h


@pytest.fixture(scope="module")
def env():
    """Create the tree, the app (first names for guests) and one header per role."""
    with patch.dict("os.environ", {ENV_CONFIG_FILE: TEST_EXAMPLE_GRAMPS_AUTH_CONFIG}):
        app = create_app(
            config={
                "TESTING": True,
                "RATELIMIT_ENABLED": False,
                "LIVING_FIRST_NAME_ONLY": True,
            },
            config_from_env=False,
        )
    with app.app_context():
        user_db.create_all()
        db_manager = WebDbManager(name=TREE_NAME, create_if_missing=True)
        tree = db_manager.dirname
        db = open_database(TREE_NAME, force_unlock=True)
        handles = build_tree(db)
        db.close()
        for name, password, role in USERS.values():
            add_user(name=name, password=password, role=role, tree=tree)
    client = app.test_client()
    headers = {}
    for key, (name, password, _role) in USERS.items():
        rv = client.post("/api/token/", json={"username": name, "password": password})
        assert rv.status_code == 200
        headers[key] = {"Authorization": f"Bearer {rv.json['access_token']}"}
    return client, headers, handles


def _get(env, role, key="ruth", query=""):
    client, headers, h = env
    rv = client.get(
        f"/api/people/{h[key]}/ancestor-places{query}", headers=headers[role]
    )
    return rv


def _ok(env, role, key="ruth", query=""):
    rv = _get(env, role, key, query)
    assert rv.status_code == 200, rv.data
    return rv.json


def _gens(env, data):
    _client, _headers, h = env
    names = {v: k for k, v in h.items()}
    return {names[p["handle"]]: p["generation"] for p in data["people"]}


# ------------------------------------------------------------------- walk


def test_requires_login(env):
    client, _headers, h = env
    assert client.get(f"/api/people/{h['ruth']}/ancestor-places").status_code == 401


def test_owner_all_generations(env):
    data = _ok(env, "owner")
    assert _gens(env, data) == {
        "ruth": 0,
        "dieter": 1,
        "marta": 1,
        "arno": 1,  # adoptive family followed too
        "gustav": 2,
        "greta": 2,
        "lothar": 2,
        "hedda": 2,
        "xaver": 3,  # reached through Gustav and Greta: listed once
        "yvonne": 3,
        "sam": 4,  # the loop: Sam is a "parent" of Xaver; Ruth is not repeated
    }
    assert data["max_generation"] == 4
    assert data["generations"] == 30
    assert len(data["people"]) == len({p["handle"] for p in data["people"]})


@pytest.mark.parametrize(
    "generations,expected",
    [
        (0, {"ruth"}),
        (1, {"ruth", "dieter", "marta", "arno"}),
        (2, {"ruth", "dieter", "marta", "arno", "gustav", "greta", "lothar", "hedda"}),
    ],
)
def test_generations_limit(env, generations, expected):
    data = _ok(env, "owner", query=f"?generations={generations}")
    assert set(_gens(env, data)) == expected


@pytest.mark.parametrize("value", ["31", "-1", "x"])
def test_generations_invalid(env, value):
    assert _get(env, "owner", query=f"?generations={value}").status_code == 422


def test_unknown_person(env):
    client, headers, _h = env
    rv = client.get(
        "/api/people/doesnotexist/ancestor-places", headers=headers["owner"]
    )
    assert rv.status_code == 404


def test_from_parent(env):
    data = _ok(env, "owner", key="dieter", query="?generations=1")
    assert _gens(env, data) == {"dieter": 0, "gustav": 1, "greta": 1}


# ----------------------------------------------------------------- events


def _event_map(env, data):
    _client, _headers, h = env
    names = {v: k for k, v in h.items()}
    return {names[e["handle"]]: e for e in data["events"]}


def test_owner_events_and_places(env):
    _client, _headers, h = env
    data = _ok(env, "owner", query="?generations=2")
    events = _event_map(env, data)
    birth = events[f"event:ruth:{int(EventType.BIRTH)}"]
    assert birth["type"] == "Birth"
    assert birth["type_label"] == "Birth"
    assert birth["year"] == 1950
    assert birth["date_str"] == "1950"
    assert birth["date"]["dateval"][2] == 1950
    assert birth["place"] == h["place:alpha"]
    assert birth["people"] == [h["ruth"]]
    assert birth["generation"] == 0
    # the parents' marriage: both parents, generation 1
    marriage = events["event:F_P"]
    assert marriage["type"] == "Marriage"
    assert set(marriage["people"]) == {h["dieter"], h["marta"]}
    assert marriage["generation"] == 1
    # Ruth's own wedding; Sam is no ancestor within 2 generations
    wedding = events["event:F_R"]
    assert wedding["people"] == [h["ruth"]]
    assert wedding["generation"] == 0
    # Sam's birth is not included (not an ancestor here)
    assert f"event:sam:{int(EventType.BIRTH)}" not in events
    # owner sees the private burial and private place
    assert f"event:dieter:{int(EventType.BURIAL)}" in events
    places = {p["handle"]: p for p in data["places"]}
    assert places[h["place:alpha"]]["lat"] == pytest.approx(48.1)
    assert places[h["place:alpha"]]["long"] == pytest.approx(11.5)
    assert places[h["place:alpha"]]["name"] == "Alphaville"
    assert places[h["place:delta"]]["lat"] is None
    assert places[h["place:delta"]]["long"] is None
    assert h["place:secret"] in places
    # every event place is listed, once
    assert {e["place"] for e in data["events"] if e["place"]} == set(places)


def test_owner_summary(env):
    data = _ok(env, "owner", query="?generations=2")
    s = data["summary"]
    assert s["people"] == 8
    # events: ruth birth, F_R, dieter 3, marta 1, arno 1, F_P, gustav 2, greta 1,
    # lothar 1, hedda 2, F_G, F_M
    assert s["events"] == 16
    assert s["events_without_place"] == 1  # Marta's birth
    assert s["events_without_coordinates"] == 2  # Dieter's death, Arno's baptism
    assert s["places_without_coordinates"] == 1  # Deltaham
    assert s["places"] == s["places_with_coordinates"] + 1
    assert s["places"] == len(data["places"])


def test_member_sees_everything(env):
    assert _ok(env, "member") == _ok(env, "owner")


# ---------------------------------------------------------------- privacy


def test_guest_living_first_name_only(env):
    _client, _headers, h = env
    data = _ok(env, "guest")
    gens = _gens(env, data)
    # the private, dead grandmother is hidden; the living grandfather is a stub
    assert "hedda" not in gens
    assert gens["lothar"] == 2
    lothar = next(p for p in data["people"] if p["handle"] == h["lothar"])
    assert lothar["name"] == "Lothar"
    events = _event_map(env, data)
    # no events of the living or the private person, no private event
    assert f"event:lothar:{int(EventType.BIRTH)}" not in events
    assert f"event:hedda:{int(EventType.BIRTH)}" not in events
    assert f"event:dieter:{int(EventType.BURIAL)}" not in events
    # a family with a living parent is a stub: its marriage is hidden
    assert "event:F_M" not in events
    for event in data["events"]:
        assert h["lothar"] not in event["people"]
        assert h["hedda"] not in event["people"]
    places = {p["handle"] for p in data["places"]}
    assert h["place:lothar"] not in places
    assert h["place:hedda"] not in places
    assert h["place:burial"] not in places
    assert h["place:secret"] not in places
    # Gustav's death is at a private place: shown without a place
    death = events[f"event:gustav:{int(EventType.DEATH)}"]
    assert death["place"] is None
    # the rest of the tree is still reached through the stub's family
    assert gens["xaver"] == 3


def test_guest_response_leaks_nothing(env):
    data = _ok(env, "guest")
    text = json.dumps(data)
    # stubs have no surname (the public people have one)
    lothar = [p for p in data["people"] if p["name"].startswith("Lothar")]
    assert lothar and all(SURNAME not in p["name"] for p in lothar)
    for word in (
        "Lolo",
        "Hedda",
        LIVING_NOTE,
        "Lotharshome",
        "Heddasgrave",
        "Privateburialfield",
        "Secretplace",
        "1900",
        "1901",
    ):
        assert word not in text, word


def test_guest_hidden_person_is_404(env):
    assert _get(env, "guest", key="hedda").status_code == 404


def test_guest_living_stub_root(env):
    data = _ok(env, "guest", key="lothar")
    _client, _headers, h = env
    assert all(h["lothar"] not in e["people"] for e in data["events"])
    assert "event:F_M" not in _event_map(env, data)
    assert data["people"][0]["name"] == "Lothar"

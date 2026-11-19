"""API tests for the child-link lines feature (Collins prototype).

Covers GET /api/childref-confidence/ and the person filter rule
IsLessThanNthGenerationAncestorOfAllFamilies on the synthetic, fictional
tree from tests/link_tree_data.py, as owner, member and guest.
"""

import json
import os
from unittest.mock import patch
from urllib.parse import quote

import pytest
from gramps.gen.db.utils import open_database

from gramps_webapi.app import create_app
from gramps_webapi.auth import add_user, user_db
from gramps_webapi.auth.const import ROLE_GUEST, ROLE_MEMBER, ROLE_OWNER
from gramps_webapi.const import ENV_CONFIG_FILE, TEST_EXAMPLE_GRAMPS_AUTH_CONFIG
from gramps_webapi.dbmanager import WebDbManager
from tests.link_tree_data import build_link_tree

TREE_NAME = "link_lines_synthetic"
USERS = {
    "owner": ("ll_owner", "owner-pw", ROLE_OWNER),
    "member": ("ll_member", "member-pw", ROLE_MEMBER),
    "guest": ("ll_guest", "guest-pw", ROLE_GUEST),
}
URL = "/api/childref-confidence/"


@pytest.fixture(scope="module")
def env():
    """Create the synthetic tree, the app and one header per role."""
    with patch.dict("os.environ", {ENV_CONFIG_FILE: TEST_EXAMPLE_GRAMPS_AUTH_CONFIG}):
        app = create_app(
            config={"TESTING": True, "RATELIMIT_ENABLED": False},
            config_from_env=False,
        )
    with app.app_context():
        user_db.create_all()
        db_manager = WebDbManager(name=TREE_NAME, create_if_missing=True)
        tree = db_manager.dirname
        db = open_database(TREE_NAME, force_unlock=True)
        ids = build_link_tree(db)
        db.close()
        for name, password, role in USERS.values():
            add_user(name=name, password=password, role=role, tree=tree)
    client = app.test_client()
    headers = {}
    for key, (name, password, _role) in USERS.items():
        rv = client.post("/api/token/", json={"username": name, "password": password})
        assert rv.status_code == 200
        headers[key] = {"Authorization": f"Bearer {rv.json['access_token']}"}
    return client, headers, ids


def _links(env, role, family_ids):
    client, headers, ids = env
    handles = ",".join(ids[f] for f in family_ids)
    rv = client.get(f"{URL}?families={handles}", headers=headers[role])
    assert rv.status_code == 200, rv.data
    by_family = {}
    for link in rv.json:
        fid = next(k for k, v in ids.items() if v == link["family"])
        by_family[fid] = link
    return by_family


def _ancestor_ids(env, role, gramps_id, generations, rule):
    client, headers, ids = env
    rules = json.dumps({"rules": [{"name": rule, "values": [gramps_id, generations]}]})
    rv = client.get(
        f"/api/people/?rules={quote(rules)}&keys=gramps_id", headers=headers[role]
    )
    assert rv.status_code == 200, rv.data
    return {p["gramps_id"] for p in rv.json}


# ---------------------------------------------------------------- relations

RELATIONS = {
    "F001": ("Birth", "Birth"),
    "F002": ("Adopted", "Stepchild"),
    "F003": ("Foster", "Foster"),
    "F004": ("Birth", "Birth"),
    "F005": ("Sponsored", "Unknown"),
    "F006": ("Guardianship", "Birth"),
    "F011": ("Birth", "Stepchild"),
}


@pytest.mark.parametrize("family,expected", sorted(RELATIONS.items()))
def test_every_relationship_type(env, family, expected):
    link = _links(env, "owner", [family])[family]
    assert (link["frel"], link["mrel"]) == expected


# --------------------------------------------------------------- confidence

LEVELS = {
    # family: (father level, mother level, source, short label)
    "F004": (0, 0, "citation", "Synthetic test source (fictional)"),
    "F007": (1, 1, "citation", "Synthetic test source (fictional)"),
    "F005": (2, 2, "citation", "Synthetic test source (fictional)"),
    "F003": (3, 3, "citation", "Synthetic test source (fictional)"),
    "F001": (4, 4, "citation", "Synthetic test source (fictional)"),
}
NAMES = ["Very Low", "Low", "Normal", "High", "Very High"]


@pytest.mark.parametrize("family,expected", sorted(LEVELS.items()))
def test_every_confidence_level(env, family, expected):
    link = _links(env, "owner", [family])[family]
    father, mother, source, label = expected
    assert link["father"]["level"] == father
    assert link["mother"]["level"] == mother
    assert link["father"]["level_name"] == NAMES[father]
    assert link["father"]["source"] == source
    assert link["father"]["label"] == label


def test_no_confidence(env):
    link = _links(env, "owner", ["F006"])["F006"]
    for role in ("father", "mother"):
        assert link[role] == {
            "level": None,
            "level_name": None,
            "source": None,
            "label": None,
            "disagree": False,
        }


def test_note_fallback_per_parent_with_short_labels(env):
    link = _links(env, "owner", ["F002"])["F002"]
    assert link["father"]["level"] == 2 and link["father"]["source"] == "note"
    assert link["father"]["label"] == "probable"
    assert link["mother"]["level"] == 1 and link["mother"]["label"] == "possible"
    assert link["citations"] == []


def test_disagreeing_citations_take_max_and_flag(env):
    link = _links(env, "owner", ["F010"])["F010"]
    assert link["father"]["level"] == 3
    assert link["father"]["disagree"] is True
    assert sorted(c["confidence"] for c in link["citations"]) == [1, 3]


def test_shared_parent_both_families(env):
    client, headers, ids = env
    links = _links(env, "owner", ["F010", "F011"])
    assert links["F010"]["mrel"] == "Birth"
    assert links["F011"]["mrel"] == "Stepchild"
    assert links["F011"]["father"]["level"] == 1


# ------------------------------------------------- all-parent-family walker

def test_rule_follows_second_family_but_not_third(env):
    got = _ancestor_ids(env, "owner", "ST0001", 3, "IsLessThanNthGenerationAncestorOfAllFamilies")
    assert got == {
        "ST0001", "ST0011", "ST0012", "ST0013", "ST0014",
        "ST0021", "ST0022", "ST0023", "ST0024", "ST0025", "ST0026", "ST0027", "ST0028",
    }
    stock = _ancestor_ids(env, "owner", "ST0001", 3, "IsLessThanNthGenerationAncestorOf")
    assert "ST0013" not in stock and stock < got


def test_rule_generation_limit(env):
    got = _ancestor_ids(env, "owner", "ST0001", 2, "IsLessThanNthGenerationAncestorOfAllFamilies")
    assert got == {"ST0001", "ST0011", "ST0012", "ST0013", "ST0014"}


def test_person_mode_returns_walked_families(env):
    client, headers, ids = env
    rv = client.get(f"{URL}?person=ST0001&generations=3", headers=headers["owner"])
    assert rv.status_code == 200
    fams = {next(k for k, v in ids.items() if v == l["family"]) for l in rv.json}
    assert fams == {"F001", "F002", "F004", "F005", "F006", "F007"}


# ------------------------------------------------------------------ privacy

@pytest.mark.parametrize("role", ["owner", "member"])
def test_view_private_roles_see_private_links(env, role):
    links = _links(env, role, ["F007", "F020", "F021", "F022"])
    assert set(links) == {"F007", "F020", "F021", "F022"}
    assert links["F007"]["frel"] == "Birth"
    assert links["F020"]["father"]["level"] == 4
    assert links["F020"]["father"]["source"] == "citation"
    got = _ancestor_ids(env, role, "ST0003", 2, "IsLessThanNthGenerationAncestorOfAllFamilies")
    assert {"ST0043", "ST0044"} <= got  # private family F021 is the 2nd family
    got = _ancestor_ids(env, role, "ST0001", 3, "IsLessThanNthGenerationAncestorOfAllFamilies")
    assert "ST0027" in got


def test_guest_private_family_and_child_ref_hidden(env):
    links = _links(env, "guest", ["F020", "F021", "F022"])
    assert "F021" not in links  # private family
    assert "F022" not in links  # private child ref
    # private citation hidden -> public note fallback, no citation details
    assert links["F020"]["citations"] == []
    assert links["F020"]["father"]["source"] == "note"
    assert links["F020"]["father"]["level"] == 2


def test_guest_private_parent_slot_is_empty(env):
    link = _links(env, "guest", ["F007"])["F007"]
    assert link["father"] is None and link["frel"] is None
    assert link["mother"]["level"] == 1


def test_guest_walk_stops_at_private(env):
    rule = "IsLessThanNthGenerationAncestorOfAllFamilies"
    got = _ancestor_ids(env, "guest", "ST0001", 3, rule)
    assert "ST0027" not in got and "ST0028" in got
    got = _ancestor_ids(env, "guest", "ST0003", 2, rule)
    assert got == {"ST0003", "ST0041", "ST0042"}


def test_guest_response_has_no_private_tokens(env):
    client, headers, ids = env
    for query in ("person=ST0003&generations=3", "person=ST0001&generations=3"):
        body = client.get(f"{URL}?{query}", headers=headers["guest"]).data.decode()
        for private in ("ST0027", "ST0043", "ST0044", "ST0045", "ST0046", "F021", "F022"):
            assert ids[private] not in body


def test_guest_cannot_resolve_private_person(env):
    client, headers, _ = env
    rv = client.get(f"{URL}?person=ST0027", headers=headers["guest"])
    assert rv.status_code == 404


# --------------------------------------------------------------- validation

def test_requires_token(env):
    client, _, ids = env
    assert client.get(f"{URL}?families={ids['F001']}").status_code == 401


@pytest.mark.parametrize(
    "query",
    ["", "?families=a&person=ST0001", "?families=bad%20handle", "?person=ST0001&generations=0",
     "?families=" + ",".join(f"h{i}" for i in range(251))],
)
def test_invalid_arguments(env, query):
    client, headers, _ = env
    assert client.get(URL + query, headers=headers["owner"]).status_code == 422


def test_unknown_family_is_skipped(env):
    client, headers, _ = env
    rv = client.get(f"{URL}?families=doesnotexist", headers=headers["owner"])
    assert rv.status_code == 200 and rv.json == []

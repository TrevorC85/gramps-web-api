#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#

"""Collins family fork: private living people as first-name stubs for guests."""

import pytest
from gramps.gen.db import DbTxn
from gramps.gen.db.utils import make_database
from gramps.gen.lib import (
    Attribute,
    AttributeType,
    ChildRef,
    ChildRefType,
    Citation,
    Date,
    Event,
    EventRef,
    EventType,
    Family,
    Media,
    MediaRef,
    Note,
    Person,
    Source,
    Surname,
)

from gramps_webapi.api.access import AccessPolicy, RestrictedDb
from gramps_webapi.api.access.living import given_name_shown

GUEST = AccessPolicy(view_private=False)
GUEST_FIRST_NAMES = AccessPolicy(view_private=False, living_first_name_only=True)
MEMBER = AccessPolicy(view_private=True, living_first_name_only=True)


def _person(db, trans, given, surname, private=False, gender=Person.MALE):
    person = Person()
    person.primary_name.set_first_name(given)
    sn = Surname()
    sn.set_surname(surname)
    person.primary_name.set_surname_list([sn])
    person.set_gender(gender)
    person.set_privacy(private)
    db.add_person(person, trans)
    return person


def _event(db, trans, person, etype, year, private=False):
    event = Event()
    event.set_type(etype)
    event.set_date_object(Date(year))
    event.set_privacy(private)
    db.add_event(event, trans)
    ref = EventRef()
    ref.ref = event.handle
    person.add_event_ref(ref)
    if etype == EventType.BIRTH:
        person.set_birth_ref(ref)
    return event


def _note(db, trans, text, private=False):
    note = Note(text)
    note.set_privacy(private)
    db.add_note(note, trans)
    return note


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    path = tmp_path_factory.mktemp("living")
    db = make_database("sqlite")
    db.load(str(path))
    h = {}
    with DbTxn("setup", db) as trans:
        dad = _person(db, trans, "Albert", "Oldname")  # dead, public
        _event(db, trans, dad, EventType.DEATH, 1990)
        living = _person(db, trans, 'Michael "Mike" (Mick)', "Livingname", private=True)
        _event(db, trans, living, EventType.BIRTH, 1958, private=True)
        public_note = _note(db, trans, "public note about the living one")
        living.add_note(public_note.handle)
        source = Source()
        source.set_title("Birth certificate of the living one 1958")
        db.add_source(source, trans)
        citation = Citation()
        citation.set_reference_handle(source.handle)
        db.add_citation(citation, trans)
        living.add_citation(citation.handle)
        media = Media()
        media.set_path("living.jpg")
        db.add_media(media, trans)
        mref = MediaRef()
        mref.ref = media.handle
        living.add_media_reference(mref)
        attr = Attribute()
        attr.set_type(AttributeType.OCCUPATION)
        attr.set_value("job")
        living.add_attribute(attr)
        db.commit_person(living, trans)
        db.commit_person(dad, trans)
        dead_private = _person(db, trans, "Dora", "Deadprivate", private=True)
        _event(db, trans, dead_private, EventType.BURIAL, 1950)
        db.commit_person(dead_private, trans)
        # public family of the dead father and the living child, adopted
        fam = Family()
        fam.set_father_handle(dad.handle)
        cref = ChildRef()
        cref.ref = living.handle
        cref.set_father_relation(ChildRefType.ADOPTED)
        cref_note = _note(db, trans, "Adoption confirmed by court record")
        cref.add_note(cref_note.handle)
        fam.add_child_ref(cref)
        fam_note = _note(db, trans, "family note naming the living one")
        fam.add_note(fam_note.handle)
        db.add_family(fam, trans)
        dad.add_family_handle(fam.handle)
        living.add_parent_family_handle(fam.handle)
        # private family of the living person and a dead spouse
        own = Family()
        own.set_father_handle(living.handle)
        own.set_privacy(True)
        marriage = Event()
        marriage.set_type(EventType.MARRIAGE)
        marriage.set_date_object(Date(1988))
        db.add_event(marriage, trans)
        mref2 = EventRef()
        mref2.ref = marriage.handle
        own.add_event_ref(mref2)
        db.add_family(own, trans)
        living.add_family_handle(own.handle)
        db.commit_person(dad, trans)
        db.commit_person(living, trans)
        # a public note of a dead person linking nowhere
        other_note = _note(db, trans, "unrelated public note")
        dad.add_note(other_note.handle)
        db.commit_person(dad, trans)
        h.update(
            dad=dad.handle, living=living.handle, dead_private=dead_private.handle,
            public_note=public_note.handle, cref_note=cref_note.handle,
            fam_note=fam_note.handle, other_note=other_note.handle,
            source=source.handle, citation=citation.handle, media=media.handle,
            fam=fam.handle, own=own.handle, marriage=marriage.handle,
        )
    yield db, h
    db.close()


def test_given_name_shown():
    assert given_name_shown('Charles "Chip" (Chuck)') == "Charles"
    assert given_name_shown("Jack (Clifford John)") == "Jack"
    assert given_name_shown("Mary Ann") == "Mary Ann"


def test_off_hides_living_completely(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST)
    assert rdb.get_person_from_handle(h["living"]) is None
    # unchanged upstream behaviour: a public note of a hidden person is visible
    assert rdb.get_note_from_handle(h["public_note"]) is not None


def test_stub(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST_FIRST_NAMES)
    person = rdb.get_person_from_handle(h["living"])
    assert person is not None
    assert person.primary_name.get_first_name() == "Michael"
    assert person.primary_name.get_surname() == ""
    assert person.get_gender() == Person.UNKNOWN
    for getter in (
        "get_event_ref_list", "get_note_list", "get_citation_list",
        "get_media_list", "get_attribute_list", "get_address_list",
        "get_alternate_names", "get_tag_list", "get_person_ref_list",
    ):
        assert getattr(person, getter)() == [], getter
    assert person.get_birth_ref() is None
    assert person.get_parent_family_handle_list() == [h["fam"]]
    assert person.get_family_handle_list() == [h["own"]]
    assert h["living"] in set(rdb.iter_person_handles())
    assert "Livingname" not in rdb.get_surname_list()


def test_dead_private_stays_hidden(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST_FIRST_NAMES)
    assert rdb.get_person_from_handle(h["dead_private"]) is None


def test_notes_and_records_hidden(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST_FIRST_NAMES)
    for name in ("public_note", "cref_note"):
        assert rdb.get_note_from_handle(h[name]) is None, name
    # notes of a family whose parents are not living stay (documented rule;
    # an open question for the tree owner)
    assert rdb.get_note_from_handle(h["fam_note"]) is not None
    assert h["public_note"] not in set(rdb.iter_note_handles())
    assert rdb.get_citation_from_handle(h["citation"]) is None
    assert rdb.get_source_from_handle(h["source"]) is None
    assert rdb.get_media_from_handle(h["media"]) is None
    assert rdb.get_event_from_handle(h["marriage"]) is None
    assert rdb.get_note_from_handle(h["other_note"]) is not None


def test_families(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST_FIRST_NAMES)
    fam = rdb.get_family_from_handle(h["fam"])
    assert fam.get_note_list() == [h["fam_note"]]
    (cref,) = fam.get_child_ref_list()
    assert cref.ref == h["living"]
    assert cref.get_note_list() == [] and cref.get_citation_list() == []
    assert cref.get_father_relation() == ChildRefType.UNKNOWN
    own = rdb.get_family_from_handle(h["own"])
    assert own is not None
    assert own.get_father_handle() == h["living"]
    assert own.get_event_ref_list() == []


def test_member_unchanged(tree):
    db, h = tree
    rdb = RestrictedDb(db, MEMBER)
    person = rdb.get_person_from_handle(h["living"])
    assert person.primary_name.get_surname() == "Livingname"
    assert person.get_note_list() == [h["public_note"]]
    assert rdb.get_note_from_handle(h["cref_note"]) is not None


def test_sorted_handles_fallback(tree):
    db, h = tree
    rdb = RestrictedDb(db, GUEST_FIRST_NAMES)
    assert rdb.get_sorted_handles("Person", [("surname", False)]) is None
    assert RestrictedDb(db, GUEST).policy.cache_key() == "00"
    assert rdb.policy.cache_key() == "01"

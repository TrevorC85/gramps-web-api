"""Synthetic, clearly fictional test tree for the child-link lines feature.

Every person is surnamed "Synthtest" and has the nickname "fictional", so this data can never be confused with the Collins family tree.
Used by the backend tests and by the local synthetic test instance
(collins-family/tools/make_synthetic_link_tree.py); never imported live.

Cases covered (child -> parent family: frel/mrel, confidence):

ST0001 Ava   F001 birth      Birth/Birth          citation Very High (4)
             F002 second     Adopted/Stepchild    note: father probable (2), mother possible (1)
             F003 third      Foster/Foster        citation High (3)   -> "more" indicator
ST0011 Bruno F004            Birth/Birth          citation Very Low (0)
ST0012 Bella F005            Sponsored/Unknown    citation Normal (2)
ST0013 Adam  F006            Guardianship (custom)/Birth, no confidence
ST0014 Stella F007           Birth/Birth          citation Low (1); father ST0027 is private
ST0002 Ben   F010            Birth/Birth          two citations High (3) + Low (1) -> 3, disagree
             F011            Birth/Stepchild      citation Low (1); father ST0031 shared with F010
ST0003 Cara  F020            Birth/Birth          private citation Very High (4) + public note "probable"
             F021 private family  Adopted/Adopted citation High (3)
             F022 private child ref Foster/Foster citation Normal (2)
"""

from gramps.gen.db import DbTxn
from gramps.gen.lib import (
    ChildRef,
    ChildRefType,
    Citation,
    Family,
    Name,
    Note,
    Person,
    Source,
    Surname,
)

SURNAME = "Synthtest"

PEOPLE = {
    # gramps_id: (first name, gender, private)
    "ST0001": ("Ava", Person.FEMALE, False),
    "ST0002": ("Ben", Person.MALE, False),
    "ST0003": ("Cara", Person.FEMALE, False),
    "ST0011": ("Bruno Birthfather", Person.MALE, False),
    "ST0012": ("Bella Birthmother", Person.FEMALE, False),
    "ST0013": ("Adam Adoptivefather", Person.MALE, False),
    "ST0014": ("Stella Stepmother", Person.FEMALE, False),
    "ST0015": ("Felix Fosterfather", Person.MALE, False),
    "ST0016": ("Fiona Fostermother", Person.FEMALE, False),
    "ST0021": ("Gus Grandfather", Person.MALE, False),
    "ST0022": ("Gina Grandmother", Person.FEMALE, False),
    "ST0023": ("Sid Sponsor", Person.MALE, False),
    "ST0024": ("Una Unknownrel", Person.FEMALE, False),
    "ST0025": ("Gary Guardian", Person.MALE, False),
    "ST0026": ("Nora Nocitation", Person.FEMALE, False),
    "ST0027": ("Percy Privatefather", Person.MALE, True),
    "ST0028": ("Polly Publicmother", Person.FEMALE, False),
    "ST0031": ("Sam Shareddad", Person.MALE, False),
    "ST0032": ("Beth Birthmom", Person.FEMALE, False),
    "ST0033": ("Sue Stepmom", Person.FEMALE, False),
    "ST0041": ("Carl Birthdad", Person.MALE, False),
    "ST0042": ("Clara Birthmom", Person.FEMALE, False),
    "ST0043": ("Hal Hiddenfamily", Person.MALE, False),
    "ST0044": ("Hana Hiddenfamily", Person.FEMALE, False),
    "ST0045": ("Rex Privateref", Person.MALE, False),
    "ST0046": ("Rita Privateref", Person.FEMALE, False),
}

B, A, S, F, SP, U = (
    ChildRefType.BIRTH,
    ChildRefType.ADOPTED,
    ChildRefType.STEPCHILD,
    ChildRefType.FOSTER,
    ChildRefType.SPONSORED,
    ChildRefType.UNKNOWN,
)
CUSTOM = (ChildRefType.CUSTOM, "Guardianship")

# family id: (father, mother, child, frel, mrel, citations [(level, private)],
#             note text or None, family private, child ref private)
FAMILIES = [
    ("F001", "ST0011", "ST0012", "ST0001", B, B, [(4, False)], None, False, False),
    ("F002", "ST0013", "ST0014", "ST0001", A, S, [],
     "Parent-child link confidence:\n- father: probable (strong) — fictional register entry\n"
     "- mother: possible — fictional census, needs a second record",
     False, False),
    ("F003", "ST0015", "ST0016", "ST0001", F, F, [(3, False)], None, False, False),
    ("F004", "ST0021", "ST0022", "ST0011", B, B, [(0, False)], None, False, False),
    ("F005", "ST0023", "ST0024", "ST0012", SP, U, [(2, False)], None, False, False),
    ("F006", "ST0025", "ST0026", "ST0013", CUSTOM, B, [], None, False, False),
    ("F007", "ST0027", "ST0028", "ST0014", B, B, [(1, False)], None, False, False),
    ("F010", "ST0031", "ST0032", "ST0002", B, B, [(3, False), (1, False)], None, False, False),
    ("F011", "ST0031", "ST0033", "ST0002", B, S, [(1, False)], None, False, False),
    ("F020", "ST0041", "ST0042", "ST0003", B, B, [(4, True)],
     "Parent-child link confidence: - parent: probable — fictional baptism", False, False),
    ("F021", "ST0043", "ST0044", "ST0003", A, A, [(3, False)], None, True, False),
    ("F022", "ST0045", "ST0046", "ST0003", F, F, [(2, False)], None, False, True),
]


def _name(first: str) -> Name:
    name = Name()
    name.set_first_name(first)
    name.set_nick_name("fictional")
    surname = Surname()
    surname.set_surname(SURNAME)
    name.add_surname(surname)
    return name


def build_link_tree(db) -> dict:
    """Add the synthetic link tree to an open, writable db.

    Returns {gramps_id: handle} for all people and families.
    """
    ids = {}
    with DbTxn("synthetic link test tree (fictional)", db) as trans:
        source = Source()
        source.set_title("Synthetic test source (fictional)")
        source.set_gramps_id("STS001")
        db.add_source(source, trans)
        people = {}
        for gid, (first, gender, private) in PEOPLE.items():
            person = Person()
            person.set_gramps_id(gid)
            person.set_primary_name(_name(first))
            person.set_gender(gender)
            person.set_privacy(private)
            db.add_person(person, trans)
            people[gid] = person
            ids[gid] = person.handle
        for n, (fid, fa, mo, child, frel, mrel, cits, note_text, fpriv, rpriv) in enumerate(
            FAMILIES
        ):
            family = Family()
            family.set_gramps_id(fid)
            family.set_privacy(fpriv)
            family.set_father_handle(people[fa].handle)
            family.set_mother_handle(people[mo].handle)
            child_ref = ChildRef()
            child_ref.set_reference_handle(people[child].handle)
            child_ref.set_father_relation(ChildRefType(frel))
            child_ref.set_mother_relation(ChildRefType(mrel))
            child_ref.set_privacy(rpriv)
            for m, (level, cpriv) in enumerate(cits):
                citation = Citation()
                citation.set_gramps_id(f"STC{n:02d}{m}")
                citation.set_reference_handle(source.handle)
                citation.set_confidence_level(level)
                citation.set_page(f"fictional page {fid}-{m}")
                citation.set_privacy(cpriv)
                db.add_citation(citation, trans)
                child_ref.add_citation(citation.handle)
            if note_text:
                note = Note()
                note.set_gramps_id(f"STN{n:02d}")
                note.set(note_text)
                db.add_note(note, trans)
                child_ref.add_note(note.handle)
            family.add_child_ref(child_ref)
            db.add_family(family, trans)
            ids[fid] = family.handle
            for gid in (fa, mo):
                people[gid].add_family_handle(family.handle)
                db.commit_person(people[gid], trans)
            people[child].add_parent_family_handle(family.handle)
            db.commit_person(people[child], trans)
        db.set_default_person_handle(people["ST0001"].handle)
    return ids

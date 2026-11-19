"""Unit tests for the child-link confidence note parser (Collins prototype)."""

from gramps_webapi.api.resources.link_confidence import label_level, note_levels


def test_label_levels():
    assert label_level("confirmed (record)") == 4
    assert label_level("confirmed (image)") == 4
    assert label_level("confirmed (chart)") == 3
    assert label_level("probable (strong)") == 2
    assert label_level("probable") == 2
    assert label_level("possible") == 1
    assert label_level("needs confirmation") == 1
    assert label_level("something else") is None


def test_note_levels_per_parent():
    text = (
        "Parent-child link confidence:\n"
        "- father: confirmed (record) — record (Gimpel/Koop chart)\n"
        "- mother: probable (strong) — v63 chart"
    )
    got = note_levels([text])
    assert got["father"] == {"level": 4, "label": "confirmed (record)"}
    assert got["mother"] == {"level": 2, "label": "probable (strong)"}


def test_note_levels_parent_fallback_and_other_notes():
    got = note_levels(["unrelated", "Parent-child link confidence: - parent: possible — index"])
    assert got["father"]["level"] == 1 and got["mother"]["level"] == 1

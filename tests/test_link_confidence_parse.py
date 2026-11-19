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
    assert got["mother"] == {"level": 2, "label": "probable"}


def test_note_levels_parent_fallback_and_other_notes():
    got = note_levels(["unrelated", "Parent-child link confidence: - parent: possible — index"])
    assert got["father"]["level"] == 1 and got["mother"]["level"] == 1


def test_short_labels():
    from gramps_webapi.api.resources.link_confidence import short_label

    assert short_label("confirmed (record) — record (Gimpel/Koop chart)") == "confirmed (record)"
    assert short_label("Confirmed by chart and obituary") == "confirmed"
    assert short_label("probable (strong) — v63 chart") == "probable"
    assert short_label("needs confirmation from a second record") == "needs confirmation"
    assert short_label("weak, family story") == "weak"
    assert short_label("no idea") is None


def test_note_levels_returns_short_labels():
    got = note_levels(["Parent-child link confidence: - father: probable (strong) — v63 chart"])
    assert got["father"]["label"] == "probable"

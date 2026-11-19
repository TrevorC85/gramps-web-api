"""Regression tests for the chart-line parser misses found in the 18 Nov 2026 audit
(un-prefixed date ranges, bracketed years, c/~ years, inner ', d.' clauses, junk places)."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lib_parse import parse_fact_lines, clean_place_text, normalize_place_parts  # noqa: E402

CASES = json.loads((HERE / "fixtures" / "parser_misses_2026-11-18.json").read_text())


class TestParserMisses(unittest.TestCase):
    def test_fixture_lines(self):
        for case in CASES:
            with self.subTest(line=case["line"]):
                evs = parse_fact_lines([case["line"]])["events"]
                got = [[e["type"], e["date"]["dateval"][:3] if e["date"] else None, e["place_text"]] for e in evs]
                self.assertEqual(got, case["events"])
                if "modifier" in case:
                    self.assertEqual(evs[0]["date"]["modifier"], case["modifier"])
                if "alt_year" in case:
                    self.assertEqual(evs[0]["date"]["alt_year"], case["alt_year"])

    def test_no_raw_text_dates(self):
        for case in CASES:
            for e in parse_fact_lines([case["line"]])["events"]:
                if e["date"]:
                    self.assertEqual(e["date"]["text"], "", case["line"])

    def test_junk_places_rejected(self):
        for junk in ["cert) or Wolf Island", "aged 81", "/45", "c1819", "son of Nicolas Roy",
                     "of paralysis", "bread server", "widow", "?", ")", "-1805", "4-5 years later"]:
            with self.subTest(junk=junk):
                self.assertIsNone(clean_place_text(junk)[0])

    def test_place_cuts(self):
        self.assertEqual(clean_place_text("Washington, aged 59")[0], "Washington")
        self.assertEqual(clean_place_text("Orwell, VT, age 96")[0], "Orwell, VT")
        self.assertEqual(clean_place_text("Philadelphia, 71")[0], "Philadelphia")
        self.assertEqual(clean_place_text("Pointe-du-Lac (FaG index)")[0], "Pointe-du-Lac")
        self.assertEqual(normalize_place_parts("Memramcook, aged 84"), [("City", "Memramcook")])


if __name__ == "__main__":
    unittest.main(verbosity=1)

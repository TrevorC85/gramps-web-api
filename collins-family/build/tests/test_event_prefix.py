"""EVENT_PREFIX must match only real event abbreviations/words (Trevor, 18 Nov 2026)."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lib_parse import EVENT_PREFIX, parse_fact_lines  # noqa: E402

JUNK = json.loads((HERE.parent / "audit" / "junk_events.json").read_text())
JUNK_DESCS = [j["description"] for j in JUNK]

REAL = [
    ("b. 8 Jan 1924, Springfield, MA", "Birth", 1924),
    ("d. 25 Jan 2007", "Death", 2007),
    ("bur. Elderton Cemetery", "Burial", None),
    ("bur. Vandergrift Cemetery (FaG index)", "Burial", None),
    ("born 12 Apr 1872, Crimea", "Birth", 1872),
    ("died 1949, Hazen, Mercer Co., ND", "Death", 1949),
    ("buried Forks-Zion, Gilpin Twp.", "Burial", None),
    ("bp. 3 Mar 1701, Port-Royal", "Baptism", 1701),
    ("bapt. 3 Mar 1701", "Baptism", 1701),
    ("B. 5 Aug 1878, Springfield, MA", "Birth", 1878),
    ("m. 1 Jan 1983, Massachusetts", "Marriage", 1983),
    ("m2. John Watson Martin (1839–1913)", "Marriage", None),
]


class TestEventPrefix(unittest.TestCase):
    def test_54_junk_descriptions(self):
        self.assertEqual(len(JUNK_DESCS), 54)
        for d in JUNK_DESCS:
            with self.subTest(d=d):
                self.assertIsNone(EVENT_PREFIX.match(d), d)
                self.assertEqual(parse_fact_lines([d])["events"], [], d)

    def test_other_ordinary_words(self):
        for d in ["Detroit household", "Dates 1795–1845 are tree-only", "Burial not found",
                  "Maiden name Libengood", "Mar 1859 – 8 Nov 1911", "blacksmith", "Bertha's 1928 death",
                  "baptism not found", "borne by", "diedrich", "dau. of X", "daus. Elizabeth", "Mrs. Hunger"]:
            with self.subTest(d=d):
                self.assertIsNone(EVENT_PREFIX.match(d), d)

    def test_real_event_lines(self):
        for line, typ, year in REAL:
            with self.subTest(line=line):
                self.assertIsNotNone(EVENT_PREFIX.match(line), line)
                evs = parse_fact_lines([line])["events"]
                self.assertEqual(len(evs), 1, line)
                self.assertEqual(evs[0]["type"], typ, line)
                if year:
                    self.assertEqual((evs[0]["date"] or {}).get("year"), year, line)


if __name__ == "__main__":
    unittest.main(verbosity=1)

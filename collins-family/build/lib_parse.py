"""Date, place, fact-line, and confidence helpers for the full-tree build."""
from __future__ import annotations

import re
from typing import Any

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

STATE_ABBR = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
    "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
    "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
    "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}

PROVINCE_ABBR = {
    "qc": "Québec", "que": "Québec", "quebec": "Québec", "québec": "Québec",
    "nb": "New Brunswick", "ns": "Nova Scotia", "on": "Ontario",
    "pe": "Prince Edward Island", "pei": "Prince Edward Island",
    "mb": "Manitoba", "sk": "Saskatchewan", "ab": "Alberta", "bc": "British Columbia",
}

PLACE_ALIASES = {
    "phila": "Philadelphia", "phila.": "Philadelphia", "philadelphia": "Philadelphia",
    "bklyn": "Brooklyn", "brooklyn": "Brooklyn",
    "manh": "Manhattan", "manh.": "Manhattan", "manhattan": "Manhattan",
    "kings": "Kings County",
    "n. brookfield": "North Brookfield",
    "st mary's": "St. Mary's Cemetery, Needham",
    "st. mary's": "St. Mary's Cemetery, Needham",
    "needham ma": "Needham",
}


def map_confidence(conf: str | None, basis: str | None = None, style: str | None = None) -> tuple[str, str]:
    """Return (canonical tag name, basis string)."""
    c = (conf or "").strip()
    b = (basis or "").strip()
    cl = c.lower()
    bl = b.lower()
    st = (style or "").lower()

    if st == "adopt":
        return "confirmed (record)", b or "legal / adoptive parent"
    if st == "fam":
        return "needs confirmation", b or "family knowledge (needs a record)"
    if st == "strong":
        return "probable (strong)", b
    if st == "need":
        return "needs confirmation", b
    if st == "prob":
        return "probable", b
    if st in ("conf", "confirmed"):
        # fall through to text mapping using basis
        cl = "confirmed"
        if not c:
            c = "confirmed"

    if "possible" in cl or cl == "possible":
        return "possible", b
    if cl.startswith("needs") or "needs confirmation" in cl:
        return "needs confirmation", b
    if cl.startswith("prob"):
        if "strong" in cl or "strong" in bl:
            return "probable (strong)", b
        return "probable", b
    if cl.startswith("confirm") or cl in ("conf", "c", "confirmed"):
        rec_tokens = (
            "record", "records", "index", "image", "certificate", "census",
            "baptism", "burial", "marriage", "vr ", "vr.", "fiche", "prdh",
            "fichier", "tanguay", "ofb", "ssdi", "draft",
        )
        if any(t in bl for t in rec_tokens) or "record" in cl:
            return "confirmed (record)", b
        if "family" in bl or "family" in cl:
            return "confirmed (family)", b
        if "chart" in bl or bl in ("tree", "joy's tree", "joy’s tree") or "tree" in bl:
            return "confirmed (chart)", b
        if not b:
            return "confirmed (family)", b
        return "confirmed (chart)", b
    if st == "ps":
        return "probable (strong)", b
    if st == "p":
        return "probable", b
    if st == "nc":
        return "needs confirmation", b
    if cl in ("", "none"):
        return "needs confirmation", b
    return c, b


def _month(tok: str) -> int | None:
    return MONTHS.get(tok.lower().rstrip("."))


YEAR_RE = r"(?:1[5-9]|20)\d{2}"
_QUAL = r"(?P<q>c\.|c(?=\d)|ca\.?|circa|abt\.?|about|~|bef\.?|before|aft\.?|after)?\s*"
_MON = r"(?P<mon>[A-Za-z]{3,9})\.?"
# Forms tried in order; every one must start the text (after an optional qualifier).
_DATE_FORMS = [
    # 29/30 Jan 1924 and "3 or 10 Nov 1953": uncertain day -> month + year only
    ("dd_or", re.compile(r"^" + _QUAL + r"\d{1,2}\s*(?:/|or)\s*\d{1,2}\s+" + _MON + r",?\s+(?P<y>" + YEAR_RE + r")(?!\d)", re.I)),
    ("dmy", re.compile(r"^" + _QUAL + r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(?P<y>" + YEAR_RE + r")(?!\d)", re.I)),
    ("mdy", re.compile(r"^" + _QUAL + _MON + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>" + YEAR_RE + r")(?!\d)", re.I)),
    ("my", re.compile(r"^" + _QUAL + _MON + r",?\s+(?P<y>" + YEAR_RE + r")(?!\d)", re.I)),
    ("iso", re.compile(r"^" + _QUAL + r"(?P<y>" + YEAR_RE + r")-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?!\d)", re.I)),
    ("y", re.compile(r"^" + _QUAL + r"(?P<y>" + YEAR_RE + r")(?!\d)", re.I)),
]
# 1744/45, 1747/8: double (Old Style / New Style) year tail
_DOUBLE_TAIL = re.compile(r"^\s*/\s*(?P<alt>\d{1,4})(?!\d)")
# c1864-65, about 1803–1805: year-range tail
_RANGE_TAIL = re.compile(r"^\s*[-–—]\s*(?P<y2>\d{2,4})(?!\d)(?!\s+[A-Za-z]{3,9}\.?\s+\d)")
_MODS = {"c": 3, "c.": 3, "ca": 3, "ca.": 3, "circa": 3, "abt": 3, "abt.": 3, "about": 3, "~": 3,
         "bef": 1, "bef.": 1, "before": 1, "aft": 2, "aft.": 2, "after": 2}


def strip_brackets(text: str) -> str:
    """Remove (bracketed commentary), including an unclosed trailing '(...'."""
    t = re.sub(r"\([^()]*\)", " ", text or "")
    t = re.sub(r"\([^()]*$", " ", t)
    t = t.replace(")", " ")
    return re.sub(r"\s+", " ", t).strip()


def lead_date(text: str) -> tuple[dict[str, Any] | None, int]:
    """Parse a date that STARTS the text (brackets already stripped).

    Returns (date dict, end offset) or (None, 0). Never invents day/month.
    """
    raw = (text or "").replace("–", "-").replace("—", "-")
    s = raw.lstrip()
    off = len(raw) - len(s)
    for name, rx in _DATE_FORMS:
        m = rx.match(s)
        if not m:
            continue
        gd = m.groupdict()
        mon = 0
        if gd.get("mon"):
            mon = _month(gd["mon"]) or 0
            if not mon:
                continue
        elif gd.get("m"):
            mon = int(gd["m"])
        day = int(gd["d"]) if gd.get("d") and name != "dd_or" else 0
        year = int(gd["y"])
        q = (gd.get("q") or "").lower().strip()
        modifier = _MODS.get(q, 0)
        quality = 0
        end = m.end()
        rest = s[end:]
        alt_year = None
        t = _DOUBLE_TAIL.match(rest)
        if t and name in ("y", "my", "dmy", "mdy"):
            suf = t.group("alt")
            if len(suf) < 4:
                alt_year = int(str(year)[: 4 - len(suf)] + suf)
            else:
                alt_year = int(suf)
            end += t.end()
        else:
            t = _RANGE_TAIL.match(rest)
            if t and name == "y":
                end += t.end()
                if not modifier:
                    modifier = 3  # a year range is "about" its first year
        if "?" in s[end:end + 1]:
            quality = 1
            end += 1
        if name == "dd_or":
            quality = 1
        return ({
            "calendar": 0, "modifier": modifier, "quality": quality, "newyear": 0,
            "dateval": [day, mon, year, False], "text": "", "year": year,
            "alt_year": alt_year, "original": s[:end].strip(),
        }, off + end)
    return None, 0


def parse_date_fragment(text: str) -> dict[str, Any] | None:
    """Parse a date-ish fragment. Bracketed commentary is ignored; the date must
    start the fragment (after c./ca./~/abt./bef./aft.). Returns None when no date
    starts the text. Does not invent day/month."""
    if not text:
        return None
    d, _ = lead_date(strip_brackets(text))
    return d


# Event prefixes on a fact fragment
# Only a real abbreviation with its period (b. bp. bapt. d. bur. m. m2. marr.) or the
# full word (born, baptized, died, buried, married) at a word boundary starts an event.
# Ordinary words that merely begin with those letters ("Daughter of ...", "dau. of ...",
# "Biological father", "blacksmith", "Detroit", "Burial not found", "Maiden name ...")
# are notes, not events. (Approved by Trevor, 18 Nov 2026: 54 such fake events removed.)
EVENT_PREFIX = re.compile(
    r"^(?P<kind>bur\.|buried\b|bapt\.|baptized\b|bp\.|b\.|born\b|d\.|died\b|m\d?\.|marr\.|married\b)\s*:?\s*",
    re.I,
)
# A second event inside one clause: "b. ~1779, d. 30 Jan 1848" / "b. 22 Mar 1822, bp. 28 Jul 1822"
_INNER_EVENT = re.compile(r",\s*(?=(?:bur\.|buried\b|bapt\.|baptized\b|bp\.|b\.|born\b|d\.|died\b)\s)", re.I)
# "2 Jul 1877, Canada – 8 Dec 1952, Seattle" / "1828–1914" (no b./d. prefix): birth – death
_RANGE_SPLIT = re.compile(r"\s*(?:–|—|\s-\s)\s*")
# Things after a place that are not part of it
_PLACE_CUT = re.compile(r",?\s*\b(?:aged?|age)\s+\d.*$|,?\s*\b(?:aged?|age)\s+(?:about\s+)?\d.*$", re.I)


def split_fact_clauses(line: str) -> list[str]:
    """Split a det/box line on semicolons, and before an inner ', d.' / ', bur.' / ', bp.'
    event, never inside brackets."""
    parts = []
    buf = []
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        split_here = False
        if depth == 0 and ch == ";":
            split_here = True
            skip = 1
        elif depth == 0 and ch == "," and _INNER_EVENT.match(line, i):
            split_here = True
            skip = 1
        if split_here:
            piece = "".join(buf).strip(" ,")
            if piece:
                parts.append(piece)
            buf = []
            i += skip
            continue
        buf.append(ch)
        i += 1
    piece = "".join(buf).strip(" ,")
    if piece:
        parts.append(piece)
    return parts


def clean_place_text(text: str) -> tuple[str | None, str]:
    """Return (place or None, leftover commentary).

    Cuts at '(' commentary, drops ages ('aged 59', 'age 6, ...') and rejects text that
    is not a place name (no capital letter, starts lowercase/punctuation/digit)."""
    t = (text or "").strip()
    leftover = []
    if "(" in t:
        t, _, c = t.partition("(")
        leftover.append(c.rstrip(")").strip())
    m = _PLACE_CUT.search(t)
    if m:
        leftover.append(t[m.start():].strip(" ,"))
        t = t[: m.start()]
    if "?" in t.strip(" ,;:-"):
        # an uncertain place ("Sorel?", "Wheaton, IL?") is not asserted as the event place;
        # the raw clause stays in the event description
        leftover.insert(0, t.strip(" ,;:-"))
        return None, "; ".join(x for x in leftover if x)
    t = t.strip(" ,;:-")
    # trailing ", 71" / ", 44" (an age) or ", widow" / ", infant" style descriptors
    m = re.search(r",\s*(\d{1,3}|widow|widower|infant|single|married|bread server|inmate)\s*$", t, re.I)
    if m:
        leftover.append(m.group(1))
        t = t[: m.start()].strip(" ,")
    # alternatives ("Winnipeg or Wolf Island", "Ballinliss/Killeavy") are not one place
    if re.search(r"\bor\b|/", t):
        leftover.insert(0, t)
        return None, "; ".join(x for x in leftover if x)
    if not t or not re.search(r"[A-ZÀ-ÖØ-Þ]", t) or not (t[0].isupper() or t[0] in "ÎÉÈÂ"):
        if t:
            leftover.insert(0, t)
        return None, "; ".join(x for x in leftover if x)
    return t, "; ".join(x for x in leftover if x)


def _split_date_place(rest: str, typ: str) -> tuple[dict[str, Any] | None, str | None, str]:
    """rest = clause text after the prefix. Returns (date, place, extra)."""
    plain = strip_brackets(rest)
    date, end = lead_date(plain)
    extra = ""
    if date is None and typ == "Marriage":
        # spouse name before the date: "Toney J. King, 25 Aug 1898, King Co."
        m = re.match(r"^([A-ZÀ-Þ][^\d]{1,60}?)[,\s]+(?=\d|c\d|~|abt)", plain)
        if m:
            d2, e2 = lead_date(plain[m.end():])
            if d2:
                extra = "Spouse mentioned: " + m.group(1).strip(" ,")
                date, end = d2, m.end() + e2
    if date is not None:
        tail = plain[end:]
    elif typ == "Marriage":
        return None, None, rest
    else:
        tail = plain
        # "bur. Aston Abbotts 8 Jan 1618/9": place first, date after it
        for mm in re.finditer(r"(?:(?<=\s)|(?<=,))(?=\d|c\d|~)", plain):
            d2, e2 = lead_date(plain[mm.start():])
            if d2 and (d2.get("dateval")[1] or not plain[mm.start() + e2:].strip(" ,;:.")):
                # a full date after the place, or a bare year that ends the clause
                date = d2
                tail = plain[: mm.start()] + " " + plain[mm.start() + e2:]
                break
    place, left = clean_place_text(tail.strip(" ,;:-"))
    # comments that were in brackets in the original clause
    for c in re.findall(r"\(([^()]*)\)", rest):
        left = (left + "; " if left else "") + c.strip()
    if left and not extra:
        extra = ""
    return date, place, extra


def parse_fact_lines(lines: list[str]) -> dict[str, Any]:
    """Turn chart det/box lines into structured events plus leftover notes and flags."""
    events: list[dict[str, Any]] = []
    notes: list[str] = []
    living = False
    conflicts: list[str] = []
    urls: list[str] = []

    blob = " | ".join(lines)
    # "4 living (1900)" is a census head-count, not a living-person flag.
    if re.search(r"(?<!\d )(?<!\d)\bliving\b", blob, re.I) and not re.search(r"\d\s+living\b", blob, re.I):
        living = True
    for u in re.findall(r"https?://[^\s\)\]\>\"']+", blob):
        urls.append(u.rstrip(".,;"))
    if re.search(r"\bconflict\b|\bvs\.?\b|superseded|NOT OURS|not ours|\(Joy's tree\)\s*/", blob, re.I):
        conflicts.append(blob)

    kind_map = {
        "b": "Birth", "born": "Birth",
        "bp": "Baptism", "bapt": "Baptism", "baptized": "Baptism",
        "d": "Death", "died": "Death",
        "m": "Marriage", "marr": "Marriage", "married": "Marriage",
        "bur": "Burial", "buried": "Burial",
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("~"):  # optional chart annotation
            notes.append(line.lstrip("~").strip())
            continue
        clauses = split_fact_clauses(line)
        # Un-prefixed "birth – death" range at the start of the line (root cause 1).
        if clauses and not EVENT_PREFIX.match(clauses[0]):
            first = clauses[0]
            halves = _RANGE_SPLIT.split(strip_brackets(first), maxsplit=1)
            if len(halves) == 2:
                bd, bend = lead_date(halves[0])
                dd, dend = lead_date(halves[1]) if halves[1].strip() else (None, 0)
                if bd is not None and (dd is not None or not halves[1].strip()):
                    bplace, _ = clean_place_text(halves[0][bend:].strip(" ,;:-"))
                    events.append({"type": "Birth", "date": bd, "place_text": bplace or "",
                                   "description": "", "raw": first, "form": "range"})
                    if dd is not None:
                        dplace, _ = clean_place_text(halves[1][dend:].strip(" ,;:-"))
                        events.append({"type": "Death", "date": dd, "place_text": dplace or "",
                                       "description": "", "raw": first, "form": "range"})
                    clauses = clauses[1:]
        for clause in clauses:
            if re.search(r"\bb\.\s+.+\s/\s+.*\bb\.", clause, re.I):
                conflicts.append(clause)
            m = EVENT_PREFIX.match(clause)
            if not m:
                notes.append(clause)
                continue
            raw_kind = m.group("kind").lower().rstrip(".")
            raw_kind = re.sub(r"\d$", "", raw_kind)
            typ = kind_map.get(raw_kind, "Event")
            rest = clause[m.end():].strip(" ,;.-")
            date, place, extra = _split_date_place(rest, typ)
            if raw_kind == "baptized" and date is None:
                place = None  # "baptized Johan Jacob": the baptismal name, not a place
            events.append({
                "type": typ,
                "date": date,
                "place_text": place or "",
                "description": extra,
                "raw": clause,
            })

    return {
        "events": events,
        "notes": notes,
        "living": living,
        "conflicts": conflicts,
        "urls": urls,
    }


def normalize_place_parts(text: str) -> list[tuple[str, str]]:
    """Return a list of (type, name) from smallest to largest (city -> ... -> country)."""
    if not text:
        return []
    t = text.strip()
    t = t.replace(";", ",").replace("—", ",").replace("–", "-")
    t = re.sub(r"\s+", " ", t)
    # strip commentary, ages and descriptors (root cause 3)
    cleaned, _ = clean_place_text(t)
    t = (cleaned or "").strip(" ,")
    if not t:
        return []

    # cemetery phrases: keep cemetery as a place, under the town if present
    parts_raw = [p.strip() for p in t.split(",") if p.strip()]
    names: list[str] = []
    for p in parts_raw:
        # expand aliases
        key = p.lower().rstrip(".")
        if key in PLACE_ALIASES:
            p = PLACE_ALIASES[key]
        elif key.endswith(" ma") and len(key) > 3:
            names.append(p[:-3].strip())
            names.append("Massachusetts")
            continue
        elif key.endswith(" pa") and len(key) > 3:
            names.append(p[:-3].strip())
            names.append("Pennsylvania")
            continue
        names.append(p)

    # expand trailing state/province abbreviations
    out: list[tuple[str, str]] = []
    expanded: list[str] = []
    for p in names:
        pl = p.lower().rstrip(".")
        if pl in STATE_ABBR:
            expanded.append(STATE_ABBR[pl])
        elif pl in PROVINCE_ABBR:
            expanded.append(PROVINCE_ABBR[pl])
        else:
            # "Springfield MA"
            m = re.match(r"^(.+?)\s+([A-Z]{2})$", p)
            if m and m.group(2).lower() in STATE_ABBR:
                expanded.append(m.group(1).strip())
                expanded.append(STATE_ABBR[m.group(2).lower()])
            elif m and m.group(2).lower() in PROVINCE_ABBR:
                expanded.append(m.group(1).strip())
                expanded.append(PROVINCE_ABBR[m.group(2).lower()])
            else:
                expanded.append(p)

    # classify from the right
    # country?
    countries = {
        "united states": "United States", "usa": "United States", "us": "United States",
        "u.s.": "United States", "u.s.a.": "United States", "america": "United States",
        "canada": "Canada", "england": "England", "scotland": "Scotland",
        "ireland": "Ireland", "france": "France", "germany": "Germany",
        "acadie": "Acadie", "acadia": "Acadie", "russia": "Russia",
        "crimea": "Crimea", "yorkshire": "England",  # Yorkshire is a county; handled below
    }

    english_counties = {"yorkshire", "lancashire", "halifax"}  # halifax might be a town
    # We'll just assign types simply.

    def typ_for(name: str, idx_from_right: int, total: int) -> str:
        nl = name.lower()
        if nl in countries or nl in ("united states", "canada", "england", "france", "germany", "ireland"):
            return "Country"
        if nl in STATE_ABBR.values() or nl.lower() in [v.lower() for v in STATE_ABBR.values()]:
            return "State"
        if nl in PROVINCE_ABBR.values() or "québec" in nl or nl == "quebec":
            return "State"
        if "county" in nl or nl.endswith(" co") or nl.endswith(" co."):
            return "County"
        if "cemetery" in nl or "church" in nl or "parish" in nl:
            return "Cemetery" if "cemetery" in nl else "Parish"
        if idx_from_right == 0 and typ_for.__defaults__ is None:
            pass
        if total >= 2 and idx_from_right == 0:
            # last component often state or country
            return typ_for(name, 1, total) if False else (
                "Country" if nl in countries else "State" if nl in [v.lower() for v in list(STATE_ABBR.values()) + list(PROVINCE_ABBR.values())] + ["england", "scotland"] else "City"
            )
        return "City"

    # Add implied country
    last = expanded[-1].lower() if expanded else ""
    implied = None
    us_states = {v.lower() for v in STATE_ABBR.values()}
    ca_prov = {v.lower() for v in PROVINCE_ABBR.values()} | {"québec", "quebec"}
    if last in us_states or last in ("pennsylvania", "massachusetts", "new york"):
        implied = ("Country", "United States")
    elif last in ca_prov or last in ("new brunswick", "québec", "quebec"):
        implied = ("Country", "Canada")
    elif last in ("england", "yorkshire"):
        if last == "yorkshire":
            # Yorkshire, England
            implied = ("Country", "England")
        else:
            implied = None
    elif last in ("ireland", "france", "germany", "scotland"):
        # already a country name
        pass

    typed: list[tuple[str, str]] = []
    n = len(expanded)
    for i, name in enumerate(expanded):
        idx_from_right = n - 1 - i
        nl = name.lower()
        if nl in countries:
            typ = "Country"
            name = countries[nl]
        elif nl in us_states or name in STATE_ABBR.values():
            typ = "State"
        elif nl in ca_prov:
            typ = "State"
        elif "county" in nl or re.search(r"\bco\.?$", nl):
            typ = "County"
        elif "cemetery" in nl:
            typ = "Cemetery"
        elif "parish" in nl or "church" in nl:
            typ = "Parish"
        elif nl == "yorkshire":
            typ = "County"
        else:
            typ = "City"
        typed.append((typ, name))

    if implied:
        already = any(n.lower() == implied[1].lower() for _, n in typed)
        if not already:
            typed.append(implied)

    # unique adjacent
    cleaned: list[tuple[str, str]] = []
    for typ, name in typed:
        name = re.sub(r"\s+", " ", name).strip(" ,")
        if not name:
            continue
        if cleaned and cleaned[-1][1].lower() == name.lower():
            continue
        cleaned.append((typ, name))
    return cleaned


def place_title(parts: list[tuple[str, str]]) -> str:
    # smallest to largest already; title is "City, State, Country" skipping empties
    return ", ".join(name for _, name in parts)


def norm_name(name: str) -> str:
    s = name.lower()
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r'["\']', "", s)
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    s = s.replace("à", "a").replace("á", "a").replace("â", "a")
    s = s.replace("ô", "o").replace("ö", "o").replace("ü", "u").replace("ç", "c")
    s = s.replace("í", "i").replace("ï", "i")
    return s.strip()


def split_person_name(display: str) -> dict[str, Any]:
    """Split a chart display name into first, surname, nicknames, suffix.

    Does not invent a surname when the chart shows a first name only.
    """
    name = display.strip()
    name = name.replace("“", '"').replace("”", '"').replace("’", "'")
    # suffix Jr./Sr.
    suffix = ""
    m = re.search(r",?\s+(Jr\.?|Sr\.?|III|II|IV)$", name, re.I)
    if m:
        suffix = m.group(1)
        name = name[: m.start()].strip()
    nick = []
    # Charles "Chip" (Chuck)
    for q in re.findall(r'"([^"]+)"', name):
        nick.append(q)
    for q in re.findall(r"\(([^)]+)\)", name):
        # skip things that look like alternate surnames? keep as nick/aka
        if re.search(r"\d", q):
            continue
        nick.append(q.strip())
    # remove quotes and parens for core
    core = re.sub(r'"[^"]+"', " ", name)
    core = re.sub(r"\([^)]*\)", " ", core)
    core = re.sub(r"\s+", " ", core).strip(" ,")
    # dit / née
    maiden = ""
    m = re.search(r"\bn[eé]e\s+(.+)$", core, re.I)
    if m:
        maiden = m.group(1).strip()
        core = core[: m.start()].strip(" ,")
    particles = r"(?:dit|dite|de|du|des|van|von|da|di|le|la|st\.?|saint)\s+"
    # last token is surname unless single token
    tokens = core.split()
    if not tokens:
        return {"first": display.strip(), "surname": "", "nicknames": nick, "suffix": suffix, "maiden": maiden, "raw": display}
    if len(tokens) == 1:
        return {"first": tokens[0], "surname": "", "nicknames": nick, "suffix": suffix, "maiden": maiden, "raw": display}
    # If a particle precedes last, keep it with surname
    if len(tokens) >= 3 and tokens[-2].lower().rstrip(".") in {
        "dit", "dite", "de", "du", "des", "van", "von", "da", "di", "le", "la", "st", "st.", "saint", "o", "o'",
    }:
        surname = tokens[-2] + " " + tokens[-1]
        first = " ".join(tokens[:-2])
    else:
        surname = tokens[-1]
        first = " ".join(tokens[:-1])
    return {"first": first, "surname": surname, "nicknames": nick, "suffix": suffix, "maiden": maiden, "raw": display}

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


def parse_date_fragment(text: str) -> dict[str, Any] | None:
    """Parse a date-ish fragment into a Gramps-like date dict plus flags.

    Returns None if no year can be found. Does not invent day/month.
    """
    if not text:
        return None
    raw = text.strip()
    raw = raw.replace("–", "-").replace("—", "-")
    quality = 0  # 0 regular, 1 estimated
    modifier = 0  # 0 none, 3 about
    lower = raw.lower()
    if re.search(r"\b(c\.|ca\.|circa|abt\.?|about|~|approx)\b", lower):
        modifier = 3
        quality = 1
    if "?" in raw:
        quality = 1

    # year only
    m = re.search(r"\b((?:1[5-9]|20)\d{2})\b", raw)
    if not m:
        return None
    year = int(m.group(1))

    # double year 1747/8 or 1726/7
    alt_year = None
    m2 = re.search(r"\b((?:1[5-9]|20)\d{2})\s*/\s*(\d{1,2})\b", raw)
    if m2:
        y2 = int(m2.group(1)[:2] + m2.group(2).zfill(2) if len(m2.group(2)) <= 2 else m2.group(2))
        # 1747/8 -> 1748; 1726/7 -> 1727
        suffix = m2.group(2)
        if len(suffix) == 1:
            alt_year = int(str(year)[:3] + suffix)
        elif len(suffix) == 2:
            alt_year = int(str(year)[:2] + suffix)
        # store as text date spanning the two years
        return {
            "calendar": 0, "modifier": 0, "quality": quality, "newyear": 0,
            "dateval": [0, 0, year, False],
            "text": raw.strip(),
            "year": year,
            "alt_year": alt_year,
            "original": raw.strip(),
        }

    day = 0
    month = 0
    # 10 Mar 1928 / 10 March 1928
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\s+((?:1[5-9]|20)\d{2})\b",
        raw,
    )
    if m and _month(m.group(2)):
        day, month, year = int(m.group(1)), _month(m.group(2)), int(m.group(3))
    else:
        # Mar 10, 1928 / March 10, 1928
        m = re.search(
            r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:1[5-9]|20)\d{2})\b",
            raw,
        )
        if m and _month(m.group(1)):
            month, day, year = _month(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            # Mar 1928 / February 1870
            m = re.search(r"\b([A-Za-z]{3,9})\.?\s+((?:1[5-9]|20)\d{2})\b", raw)
            if m and _month(m.group(1)):
                month, year = _month(m.group(1)), int(m.group(2))
            else:
                # 1928-03-10
                m = re.search(r"\b((?:1[5-9]|20)\d{2})-(\d{1,2})-(\d{1,2})\b", raw)
                if m:
                    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    # 29/30 Jan 1924 — range / uncertain day; keep as text
    if re.search(r"\b\d{1,2}\s*/\s*\d{1,2}\s+[A-Za-z]", raw):
        return {
            "calendar": 0, "modifier": modifier, "quality": quality, "newyear": 0,
            "dateval": [0, month, year, False] if month else [0, 0, year, False],
            "text": raw.strip(),
            "year": year,
            "original": raw.strip(),
            "uncertain_day": True,
        }

    return {
        "calendar": 0,
        "modifier": modifier,
        "quality": quality,
        "newyear": 0,
        "dateval": [day, month, year, False],
        "text": "",
        "year": year,
        "original": raw.strip(),
    }


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


def split_fact_clauses(line: str) -> list[str]:
    """Split a det/box line on semicolons and some ' / ' conflict markers kept together."""
    parts = []
    buf = []
    depth = 0
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            piece = "".join(buf).strip(" ,")
            if piece:
                parts.append(piece)
            buf = []
        else:
            buf.append(ch)
    piece = "".join(buf).strip(" ,")
    if piece:
        parts.append(piece)
    return parts


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
        "b": "Birth", "b.": "Birth", "born": "Birth",
        "bp": "Baptism", "bp.": "Baptism", "bapt": "Baptism", "bapt.": "Baptism", "baptized": "Baptism",
        "d": "Death", "d.": "Death", "died": "Death",
        "m": "Marriage", "m.": "Marriage", "marr": "Marriage", "marr.": "Marriage", "married": "Marriage",
        "bur": "Burial", "bur.": "Burial", "buried": "Burial",
    }

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("~"):  # optional chart annotation
            notes.append(line.lstrip("~").strip())
            continue
        for clause in split_fact_clauses(line):
            # slash-separated alternative births: keep both as notes/conflict if two b.
            if re.search(r"\bb\.\s+.+\s/\s+.*\bb\.", clause, re.I):
                conflicts.append(clause)
            m = EVENT_PREFIX.match(clause)
            if not m:
                # may still contain a date after "d " etc already missed
                notes.append(clause)
                continue
            raw_kind = m.group("kind").lower().rstrip(".")
            rest = clause[m.end():].strip(" ,;.-")
            typ = kind_map.get(raw_kind) or kind_map.get(raw_kind.rstrip("."))
            if typ is None:
                # "b" only
                typ = {"b": "Birth", "d": "Death", "m": "Marriage"}.get(raw_kind[:1], "Event")
            # Split date-ish head from place / comment
            date = parse_date_fragment(rest)
            place = None
            extra = ""
            if date:
                # try to peel place after the year
                year = str(date["year"])
                idx = rest.find(year)
                after = rest[idx + len(year):].strip(" ,;:-")
                after = re.sub(r"^\((estimated|abt\.?|about|circa)\)\s*", "", after, flags=re.I)
                # drop a leading punct
                # place until '(' commentary
                place_part, _, comment = after.partition("(")
                place_part = place_part.strip(" ,;:-")
                # marriage "Judy 1 Jan 1983, Massachusetts" — spouse name before date
                if typ == "Marriage":
                    before = rest[: rest.find(year) if year in rest else 0]
                    # spouse words before the date token
                    spouse_m = re.match(r"^([A-Z][A-Za-zÉéÈèÊêËëÎîÏïÔôŒœÜü .\"“”'\-]{1,40})\s+\d", rest)
                    if spouse_m and not EVENT_PREFIX.match(spouse_m.group(1)):
                        extra = "Spouse mentioned: " + spouse_m.group(1).strip()
                if place_part and not re.match(r"^(aged|age|prob|possible|needs)", place_part, re.I):
                    place = place_part
                if comment:
                    notes.append(comment.rstrip(")"))
            else:
                # no year; maybe a place only or a name ("m. Hélène 18 Aug 1862" if year parse failed)
                date = parse_date_fragment(clause)
                place = rest if not date else None
                if typ == "Marriage" and rest:
                    extra = rest
            ev = {
                "type": typ,
                "date": date,
                "place_text": place or "",
                "description": extra,
                "raw": clause,
            }
            events.append(ev)

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
    # strip trailing commentary
    t = re.sub(r"\s*\(.*\)$", "", t).strip(" ,")
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

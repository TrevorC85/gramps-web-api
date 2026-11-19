#!/usr/bin/env python3
"""Tree-wide cleanup (Trevor approved 18 Nov 2026, card t62s221).

Idempotent. Uses only the chart lines already stored in each person's "Chart lines" note,
the existing event descriptions (raw chart clauses) and records already read; never invents
facts. Usage:
    migrate_tree_cleanup_2026-11-19.py --base http://127.0.0.1:5055 [--dry-run] --log out.json
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib_parse import (EVENT_PREFIX, parse_fact_lines, normalize_place_parts, clean_place_text,
                       lead_date, strip_brackets)
import place_gazetteer as G

PASSFILE = Path("/workspace/gramps-web/.admin_password")
EMPTY_DATE = {"_class": "Date", "calendar": 0, "modifier": 0, "quality": 0, "newyear": 0,
              "dateval": [0, 0, 0, False], "text": ""}


class Api:
    def __init__(self, base, dry):
        self.base, self.dry, self.tok, self.t0 = base.rstrip("/"), dry, None, 0
        self.writes = 0

    def login(self):
        u = p = None
        for l in PASSFILE.read_text().splitlines():
            l = l.strip()
            if l.startswith("username=") and u is None: u = l[9:]
            elif l.startswith("password=") and p is None and u: p = l[9:]
        self.tok = self._raw("POST", "/api/token/", {"username": u, "password": p}, auth=False)["access_token"]
        self.t0 = time.time()

    def _raw(self, m, path, body=None, auth=True, ctype="application/json", data=None):
        r = urllib.request.Request(self.base + path, method=m,
                                   data=data if data is not None else (json.dumps(body).encode() if body is not None else None))
        r.add_header("Content-Type", ctype)
        if auth: r.add_header("Authorization", "Bearer " + self.tok)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(r, timeout=180) as x:
                    d = x.read(); return json.loads(d) if d else None
            except urllib.error.HTTPError as e:
                if e.code in (502, 503, 504) and attempt < 3:
                    time.sleep(5 * (attempt + 1)); continue
                raise SystemExit(f"{m} {path} -> {e.code} {e.read()[:400]}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3: time.sleep(5 * (attempt + 1)); continue
                raise

    def call(self, m, path, body=None, **kw):
        if self.tok is None or time.time() - self.t0 > 600: self.login()
        if m != "GET":
            self.writes += 1
            if self.dry:
                return [{"type": "add", "handle": f"DRY{self.writes}", "_class": "x"}] if m == "POST" else None
        return self._raw(m, path, body, **kw)

    def get(self, p): return self.call("GET", p)
    def put(self, p, b): return self.call("PUT", p, b)
    def post(self, p, b): return self.call("POST", p, b)
    def delete(self, p): return self.call("DELETE", p)


def new_handle(resp):
    for it in resp if isinstance(resp, list) else [resp]:
        if isinstance(it, dict) and it.get("type") == "add":
            return it.get("handle") or (it.get("new") or {}).get("handle")
    raise RuntimeError(f"no handle in {str(resp)[:300]}")


def gdate(d):
    if not d: return dict(EMPTY_DATE)
    return {"_class": "Date", "calendar": 0, "modifier": d.get("modifier", 0), "quality": d.get("quality", 0),
            "newyear": 0, "dateval": list(d["dateval"][:3]) + [False], "text": ""}


def same_date(a, b):
    a = a or EMPTY_DATE; b = b or EMPTY_DATE
    return (list(a.get("dateval") or [0, 0, 0, False])[:3] == list(b.get("dateval") or [0, 0, 0, False])[:3]
            and (a.get("text") or "") == (b.get("text") or "")
            and a.get("modifier", 0) == b.get("modifier", 0) and a.get("quality", 0) == b.get("quality", 0))


YEAR = re.compile(r"(?<!\d)((?:1[5-9]|20)\d{2})(?!\d)")


class Migration:
    def __init__(self, api: Api):
        self.api = api
        self.created = set()
        self.log = {k: [] for k in ["date_set", "date_cleared", "date_text_dropped", "odd_date_to_desc",
                                    "event_place_changed", "event_created", "place_created", "place_updated",
                                    "place_deleted", "flag_place", "flag_event", "notes", "citations", "skipped"]}

    # ---------------- data
    def load(self):
        g = self.api.get
        self.people = g("/api/people/?pagesize=5000")
        self.events = {e["handle"]: e for e in g("/api/events/?pagesize=5000")}
        self.places = {p["handle"]: p for p in g("/api/places/?pagesize=5000")}
        self.notes = {n["handle"]: n for n in g("/api/notes/?pagesize=10000")}
        self.families = g("/api/families/?pagesize=5000")
        self.owner = {}
        for p in self.people:
            for r in p["event_ref_list"]: self.owner.setdefault(r["ref"], ("person", p))
        for f in self.families:
            for r in f["event_ref_list"]: self.owner.setdefault(r["ref"], ("family", f))

    def chain(self, h):
        out, seen = [], set()
        while h and h in self.places and h not in seen:
            seen.add(h); p = self.places[h]; out.append(p)
            h = p["placeref_list"][0]["ref"] if p["placeref_list"] else None
        return out

    def place_is_junk(self, h):
        for p in self.chain(h):
            n = p["name"]["value"]
            if (re.match(r"^b?ur\.\s", n) or clean_place_text(n)[0] != n.strip(" ,;:-") or YEAR.search(n)
                    or re.search(r"[()/?]", n) or (re.search(r"\d", n.split(",")[0]) and not re.search(r"\b(Lot|Ward)\b|Hülseberg", n))):
                return True
        return False

    def chart_lines(self, person):
        lines = []
        for nh in person["note_list"]:
            t = (self.notes.get(nh) or {}).get("text", {}).get("string", "")
            if "Chart lines:" in t:
                for l in t.split("Chart lines:", 1)[1].splitlines():
                    l = l.strip()
                    if l.startswith("- ") and l[2:] not in lines: lines.append(l[2:])
        return lines

    # ---------------- places
    def canon_path(self, text, ev_type="", hints=None):
        """-> (path largest->smallest [(type,name)], flags)"""
        flags = []
        hints = {k: v for k, v in (hints or {}).items() if v and v != "City"}
        parts = normalize_place_parts(text)  # smallest -> largest
        named = []
        for typ, name in parts:
            c, _ = clean_place_text(name)
            if not c: continue
            name = G.ALIASES.get(c.lower().rstrip("."), G.ALIASES.get(c.lower(), c))
            name = re.sub(r"\s+Co\.?$", " County", name)
            name = re.sub(r"\s+Twp\.?$", " Township", name)
            name = G.ALIASES.get(name.lower(), name)
            if name == name.upper() and len(name) > 3: name = name.title()
            named.append(name)
        if not named: return [], ["no usable place text"]
        country = state = county = None
        local = []
        states = [n for n in named if n in G.US_STATES or n in G.CA_PROVINCES]
        state_pick = states[-1] if states else None  # the largest (last) state name is the state
        for n in named:
            if n in G.COUNTRIES and country is None: country = n
            elif n == state_pick and state is None: state = n
            elif county is None and (re.search(r"\bCounty\b|^County ", n) or G.LEVEL_OF.get(n, hints.get(n)) in ("County", "Department", "District")): county = n
            else: local.append(n)
        # "Washington, IA" is Washington County, Iowa (Trevor, 18 Nov 2026)
        if state == "Iowa" and "Washington" in local:
            local.remove("Washington"); county = "Washington County"
        if len(local) > 1:
            local = [n for n in local if n not in G.DROP_IF_TOWN_KNOWN] or local
        if local and local[-1] in G.PARENT_TOWN:
            local.append(G.PARENT_TOWN[local[-1]])
        if state and not country:
            country = "United States" if state in G.US_STATES else "Canada"
        top_local = local[-1] if local else None
        if not country and not state:
            key = top_local or county
            u = G.UNQUALIFIED.get(key) if key else None
            if u:
                country, state, ucounty = u
                county = county or ucounty
        if country and not state and top_local in G.UNQUALIFIED and G.UNQUALIFIED[top_local][0] == country:
            _, state, ucounty = G.UNQUALIFIED[top_local]; county = county or ucounty
        if state and not county and top_local:
            county = G.COUNTY.get((top_local, state))
            u = G.UNQUALIFIED.get(top_local)
            if not county and u and u[1] == state: county = u[2]
        if county and not country and county in G.UNQUALIFIED:
            country, state, _ = G.UNQUALIFIED[county]
        # "Moxee, Yakima": the second name is the county, not a city
        if county and len(local) >= 2 and local[-1] + " County" == county and not re.search(r"cemetery|calvary", local[0], re.I):
            local = local[:-1]
        # levels
        path = []
        if country: path.append(("Country", country))
        if state: path.append(("Province" if country == "Canada" else "State", state))
        if county:
            ctyp = G.LEVEL_OF.get(county, hints.get(county, "Department" if country == "France" else "County"))
            path.append((ctyp, county))
        loc = list(reversed(local))  # largest -> smallest
        for i, n in enumerate(loc):
            if n in G.LEVEL_OF: typ = G.LEVEL_OF[n]
            elif n in hints: typ = hints[n]
            elif re.search(r"cemetery|cem\.?$|memorial park", n, re.I): typ = "Cemetery"
            elif re.search(r"township|twp", n, re.I): typ = "Township"
            elif re.search(r"chapel|church|parish|baptist|presbyterian", n, re.I): typ = "Parish"
            elif ev_type == "Burial" and i == len(loc) - 1 and len(loc) >= 2: typ = "Cemetery"
            else: typ = "City"
            path.append((typ, n))
        loc = [n for n in loc if n not in G.LEVEL_OF and hints.get(n) not in ("Parish",) and n != "London"]
        if not country: flags.append("no country/state (not certain from the text)")
        elif not loc and not state and country in ("United States", "Canada") and not county: pass
        elif country in ("United States", "Canada") and not state: flags.append("no state/province")
        elif loc and not county and country in ("United States",) and (loc[0], state) not in G.NO_COUNTY_OK:
            flags.append("no county (not certain)")
        elif loc and not county and country in ("Canada", "France", "England", "Ireland", "Germany"):
            flags.append("no county/district (not certain)")
        return path, flags

    def find_child(self, parent, name, typ):
        cands = [p for p in self.places.values()
                 if p["name"]["value"].lower() == name.lower()
                 and [r["ref"] for r in p["placeref_list"]][:1] == ([parent] if parent else [])]
        if not cands: return None
        cands.sort(key=lambda p: (p["place_type"] != typ, p["gramps_id"]))
        return cands[0]

    def ensure_path(self, path):
        parent = None
        names = []
        for typ, name in path:
            names.insert(0, name)
            title = ", ".join(names)
            p = self.find_child(parent, name, typ)
            if p is None:
                obj = {"_class": "Place", "name": {"_class": "PlaceName", "value": name, "lang": "", "date": dict(EMPTY_DATE)},
                       "place_type": typ, "title": title,
                       "placeref_list": [{"_class": "PlaceRef", "ref": parent, "date": dict(EMPTY_DATE)}] if parent else [],
                       "alt_names": [], "alt_loc": [], "urls": [], "media_list": [], "citation_list": [], "note_list": [],
                       "tag_list": [], "private": False, "code": "", "lat": "", "long": ""}
                h = new_handle(self.api.post("/api/places/", obj))
                obj["handle"] = h; obj["gramps_id"] = h if self.api.dry else self.api.get(f"/api/places/{h}")["gramps_id"]
                self.places[h] = obj
                self.log["place_created"].append({"gramps_id": obj["gramps_id"], "title": title, "type": typ})
                p = obj
            elif p["place_type"] != typ or p.get("title") != title:
                full = self.api.get(f"/api/places/{p['handle']}") if not str(p["handle"]).startswith("DRY") else p
                before = (full["place_type"], full.get("title"))
                full["place_type"] = typ; full["title"] = title
                self.api.put(f"/api/places/{p['handle']}", full)
                self.places[p["handle"]] = full; p = full
                self.log["place_updated"].append({"gramps_id": p["gramps_id"], "before": before, "after": (typ, title)})
            parent = p["handle"]
        return parent

    # ---------------- events
    def chart_parse_for_event(self, ev):
        """Parse the event's own raw chart clause (its description). Returns parsed event of the
        same type or None when the description is not a chart clause."""
        desc = (ev.get("description") or "").strip()
        if not desc: return None
        evs = parse_fact_lines([desc])["events"]
        if not evs: return None
        if not (EVENT_PREFIX.match(desc) or evs[0].get("form") == "range"): return None
        same = [e for e in evs if e["type"] == ev["type"]]
        if not same and ev["type"] == "Birth" and len(evs) == 1 and evs[0]["type"] == "Baptism" \
                and re.match(r"^(baptized|bapt\.|bp\.)", desc, re.I):
            # old "b" prefix bug made "baptized X" a Birth; the clause is a baptism
            evs[0]["retype"] = "Baptism"
            return evs[0]
        return same[0] if same else None

    def fix_event(self, ev):
        h = ev["handle"]
        full = None
        changed = []
        parsed = self.chart_parse_for_event(ev)
        d = ev.get("date") or dict(EMPTY_DATE)
        dv = (d.get("dateval") or [0, 0, 0, False])
        cur_year = dv[2] if len(dv) > 2 else 0
        text = d.get("text") or ""
        new_date = None
        if parsed is not None:
            pd = parsed["date"]
            desc = ev.get("description") or ""
            outside = strip_brackets(desc)
            if text and not any(dv[:3]):
                new_date = gdate(pd); changed.append(("date_set" if pd else "date_cleared", text))
            elif not cur_year and pd:
                new_date = gdate(pd); changed.append(("date_set", "no year"))
            elif cur_year and pd and abs(cur_year - pd["year"]) > 1:
                new_date = gdate(pd); changed.append(("date_set", f"wrong year {cur_year}->{pd['year']}"))
            elif cur_year and str(cur_year) not in outside and str(cur_year) in desc:
                if pd:
                    new_date = gdate(pd); changed.append(("date_set", f"bracket year {cur_year}->{pd['year']}"))
                else:
                    self.log["flag_event"].append({"event": ev.get("gramps_id", ev["handle"]), "why": f"year {cur_year} only in a bracketed comment; no other date in the clause; left for Trevor", "desc": desc})
        elif text and not any(dv[:3]):
            # family/odd events: a non-date text -> try to parse; else move it into the description
            pd, _ = lead_date(strip_brackets(text))
            if pd:
                new_date = gdate(pd); changed.append(("date_set", text))
            else:
                new_date = dict(EMPTY_DATE); changed.append(("odd_date_to_desc", text))
        if (new_date is None or not any(new_date["dateval"][:3])) and not cur_year and ev["type"] in ("Birth", "Death") \
                and not (parsed and parsed.get("retype")):
            # the event clause has no date, but the person's own chart life-span line gives one
            kind, owner = self.owner.get(h, (None, None))
            if kind == "person":
                rng = [e for e in parse_fact_lines(self.chart_lines(owner))["events"]
                       if e.get("form") == "range" and e["type"] == ev["type"] and e["date"]]
                if len(rng) == 1:
                    new_date = gdate(rng[0]["date"])
                    changed = [c for c in changed if c[0] != "date_cleared"]
                    changed.append(("date_set", "no year; from life-span line " + rng[0]["raw"]))
        if new_date is None and text and any(dv[:3]):
            new_date = dict(d); new_date["text"] = ""; changed.append(("date_text_dropped", text))
        if new_date is not None and same_date(new_date, d):
            new_date = None; changed = [c for c in changed if c[0] not in ("date_set", "date_cleared", "date_text_dropped")]
        # ---- place
        new_place = None
        cur = ev.get("place") or ""
        junk = bool(cur) and self.place_is_junk(cur)
        if parsed is not None and cur and not parsed["place_text"] and re.match(r"^baptized\b", parsed["raw"], re.I):
            new_place = ""  # "baptized Johan Jacob": a baptismal name, not a place
        elif parsed is not None and (junk or not cur):
            ptxt = parsed["place_text"]
            if ptxt:
                path, flags = self.canon_path(ptxt, ev["type"])
                new_place = self.ensure_path(path) if path else ""
                if flags: self.log["flag_place"].append({"event": ev.get("gramps_id", ev["handle"]), "text": ptxt, "path": [n for _, n in path], "flags": flags})
            elif junk or re.match(r"^baptized\b", parsed["raw"], re.I):
                new_place = ""
        elif cur and not junk:
            chain = self.chain(cur)
            names = [p["name"]["value"] for p in chain]
            path, flags = self.canon_path(", ".join(names), ev["type"],
                                          {p["name"]["value"]: p.get("place_type") for p in chain})
            if path:
                new_place = self.ensure_path(path)
                if flags: self.log["flag_place"].append({"event": ev.get("gramps_id", ev["handle"]), "text": ", ".join(names), "path": [n for _, n in path], "flags": flags})
        elif junk:
            # junk place but description is not a chart clause: keep, flag
            self.log["flag_event"].append({"event": ev.get("gramps_id", ev["handle"]), "why": "junk place but description is not a chart clause", "place": [p["name"]["value"] for p in self.chain(cur)]})
        if new_place is not None and new_place == cur: new_place = None
        retype = parsed.get("retype") if parsed else None
        if new_date is None and new_place is None and not retype: return
        full = self.api.get(f"/api/events/{h}")
        if retype:
            full["type"] = retype
            self.log.setdefault("event_retyped", []).append({"event": ev.get("gramps_id", ev["handle"]), "from": ev["type"], "to": retype, "handle": h})
            kind, owner = self.owner.get(h, (None, None))
            if kind == "person":
                po = self.api.get(f"/api/people/{owner['handle']}")
                idx = po.get("birth_ref_index", -1)
                if 0 <= idx < len(po["event_ref_list"]) and po["event_ref_list"][idx]["ref"] == h:
                    po["birth_ref_index"] = -1
                    self.api.put(f"/api/people/{po['handle']}", po)
        if new_date is not None:
            full["date"] = new_date
            for kind, why in changed:
                if kind == "odd_date_to_desc" and why not in (full.get("description") or ""):
                    full["description"] = ((full.get("description") or "") + "; " + why).strip("; ")
                self.log[kind].append({"event": ev.get("gramps_id", ev["handle"]), "type": ev["type"], "why": why,
                                       "new": new_date["dateval"][:3], "desc": ev.get("description")})
        if new_place is not None:
            self.log["event_place_changed"].append({"event": ev.get("gramps_id", ev["handle"]), "from": [p["name"]["value"] for p in self.chain(cur)],
                                                    "to": [p["name"]["value"] for p in self.chain(new_place)]})
            full["place"] = new_place
        self.api.put(f"/api/events/{h}", full)
        self.events[h] = full

    def add_missing_events(self, person):
        lines = self.chart_lines(person)
        if not lines: return
        have = {}
        for r in person["event_ref_list"]:
            e = self.events.get(r["ref"])
            if e and r.get("role", "Primary") == "Primary": have.setdefault(e["type"], e)
        parsed = parse_fact_lines(lines)["events"]
        todo = []
        for typ in ("Birth", "Baptism", "Death", "Burial"):
            if typ in have: continue
            cands = [e for e in parsed if e["type"] == typ and (e["date"] or e["place_text"])]
            if cands: todo.append(cands[0])
        if not todo: return
        full = self.api.get(f"/api/people/{person['handle']}")
        for pe in todo:
            place = ""
            if pe["place_text"]:
                path, flags = self.canon_path(pe["place_text"], pe["type"])
                place = self.ensure_path(path) if path else ""
                if flags: self.log["flag_place"].append({"person": person["gramps_id"], "text": pe["place_text"], "path": [n for _, n in path], "flags": flags})
            cits = []
            if person["gramps_id"] == "I0955" and pe["type"] == "Birth":
                cits = [self.cit_handle("C0405")]  # headstone: "TONEY J. Nov. 9, 1871"
            obj = {"_class": "Event", "type": pe["type"], "date": gdate(pe["date"]), "place": place,
                   "description": pe["raw"], "citation_list": cits, "note_list": [], "media_list": [],
                   "attribute_list": [], "tag_list": [], "private": bool(person["private"])}
            h = new_handle(self.api.post("/api/events/", obj))
            obj["handle"] = h; self.events[h] = obj; self.created.add(h)
            full["event_ref_list"].append({"_class": "EventRef", "ref": h, "role": "Primary", "private": bool(person["private"]),
                                           "note_list": [], "attribute_list": [], "citation_list": []})
            idx = len(full["event_ref_list"]) - 1
            if pe["type"] == "Birth" and full.get("birth_ref_index", -1) < 0: full["birth_ref_index"] = idx
            if pe["type"] == "Death" and full.get("death_ref_index", -1) < 0: full["death_ref_index"] = idx
            gid = h if self.api.dry else self.api.get(f"/api/events/{h}")["gramps_id"]
            self.log["event_created"].append({"event": gid, "person": person["gramps_id"], "type": pe["type"],
                                              "date": pe["date"]["dateval"][:3] if pe["date"] else None,
                                              "place": pe["place_text"], "raw": pe["raw"], "private": person["private"]})
            if person["private"] and pe["type"] in ("Death", "Burial"):
                self.log["flag_event"].append({"event": gid, "why": "death/burial added for a person marked private; review privacy"})
        self.api.put(f"/api/people/{person['handle']}", full)

    def cit_handle(self, gid):
        return self.api.get(f"/api/citations/?gramps_id={gid}")[0]["handle"]

    # ---------------- orphans
    def delete_orphans(self):
        while True:
            used = {e.get("place") for e in self.events.values() if e.get("place")}
            parents = {r["ref"] for p in self.places.values() for r in p["placeref_list"]}
            orphans = [p for h, p in self.places.items() if h not in used and h not in parents]
            orphans = [p for p in orphans if not (p["place_type"] == "Country" and p["name"]["value"] in G.COUNTRIES and False)]
            if not orphans: return
            for p in orphans:
                if not str(p["handle"]).startswith("DRY"):
                    bl = self.api.get(f"/api/places/{p['handle']}?backlinks=true").get("backlinks") or {}
                    if any(v for v in bl.values()):
                        self.log["skipped"].append({"place": p["gramps_id"], "why": f"still has backlinks {list(bl)}"})
                        self.places.pop(p["handle"]); continue
                self.api.delete(f"/api/places/{p['handle']}")
                self.log["place_deleted"].append({"gramps_id": p["gramps_id"], "name": p["name"]["value"]})
                self.places.pop(p["handle"])

    # ---------------- special items
    def dedupe_events(self):
        """exact duplicates on one person (same type, description, date, place), the extra one
        carrying no citations or notes: an artefact of the old Birth->Baptism mis-typing"""
        self.log.setdefault("event_deleted_duplicate", [])
        for p in self.people:
            if self.api.dry: break
            seen = set(); dup = False
            for r in p["event_ref_list"]:
                e = self.events.get(r["ref"])
                if not e: continue
                k = (e["type"], (e.get("description") or "").strip(), tuple(e["date"]["dateval"][:3]))
                dup = dup or (k in seen and bool(k[1])); seen.add(k)
            if not dup: continue
            full = self.api.get(f"/api/people/{p['handle']}")
            groups = {}
            for r in full["event_ref_list"]:
                e = self.api.get(f"/api/events/{r['ref']}")
                k = (e["type"], (e.get("description") or "").strip(), tuple(e["date"]["dateval"][:3]), e.get("place") or "")
                groups.setdefault(k, []).append(e)
            drop = []
            for k, evs in groups.items():
                if len(evs) < 2 or not k[1]: continue
                evs.sort(key=lambda e: (-(len(e["citation_list"]) + len(e["note_list"])), e["gramps_id"]))
                drop += [e for e in evs[1:] if not e["citation_list"] and not e["note_list"] and not e["media_list"]]
            if not drop: continue
            dh = {e["handle"] for e in drop}
            keep_b = full["event_ref_list"][full["birth_ref_index"]]["ref"] if full.get("birth_ref_index", -1) >= 0 else None
            keep_d = full["event_ref_list"][full["death_ref_index"]]["ref"] if full.get("death_ref_index", -1) >= 0 else None
            full["event_ref_list"] = [r for r in full["event_ref_list"] if r["ref"] not in dh]
            refs = [r["ref"] for r in full["event_ref_list"]]
            full["birth_ref_index"] = refs.index(keep_b) if keep_b in refs else -1
            full["death_ref_index"] = refs.index(keep_d) if keep_d in refs else -1
            self.api.put(f"/api/people/{full['handle']}", full)
            for e in drop:
                bl = self.api.get(f"/api/events/{e['handle']}?backlinks=true").get("backlinks") or {}
                if not any(bl.values()):
                    self.api.delete(f"/api/events/{e['handle']}")
                self.log["event_deleted_duplicate"].append({"event": e["gramps_id"], "person": full["gramps_id"], "type": e["type"], "desc": e["description"]})

    def frankie_notes(self):
        ppl = [p for p in self.people if p["gramps_id"] == "I0956"]
        if not ppl: return
        p = self.api.get(f"/api/people/{ppl[0]['handle']}")
        chart = [self.notes[h] for h in p["note_list"] if h in self.notes and "Chart lines:" in self.notes[h]["text"]["string"]]
        main = next((n for n in chart if n["gramps_id"] == "N1033"), None)
        if main:
            t = main["text"]["string"]
            new = t.replace("\n\n1898 return\n\nconfirmed\n\n", "\n\n")
            if new != t:
                full = self.api.get(f"/api/notes/{main['handle']}")
                full["text"]["string"] = new
                self.api.put(f"/api/notes/{main['handle']}", full)
                self.log["notes"].append({"note": "N1033", "action": "removed stray lines '1898 return' / 'confirmed'"})
        for dup in [n for n in chart if n is not main and main is not None]:
            lines_dup = dup["text"]["string"].split("Chart lines:", 1)[1].strip()
            if lines_dup and lines_dup in main["text"]["string"]:
                p["note_list"] = [h for h in p["note_list"] if h != dup["handle"]]
                self.api.put(f"/api/people/{p['handle']}", p)
                bl = self.api.get(f"/api/notes/{dup['handle']}?backlinks=true").get("backlinks") or {}
                if not any(v for k, v in bl.items() if k != "person") and not any(h != p["handle"] for h in bl.get("person", [])):
                    self.api.delete(f"/api/notes/{dup['handle']}")
                self.log["notes"].append({"note": dup["gramps_id"], "action": "duplicate chart-lines note merged into N1033 and deleted"})

    def arthur_findagrave(self, media_path):
        title = "Find a Grave memorial 150656819, CPL Arthur Leroy Libengood (memorial page)"
        if [s for s in self.api.get("/api/sources/?pagesize=5000") if s["title"] == title]:
            return
        ppl = [p for p in self.people if p["gramps_id"] == "I0997"]
        if not ppl: return
        # media: text extract image of the saved page (the raw HTML is kept on the box only)
        data = Path(media_path).read_bytes()
        if self.api.dry:
            mh = "DRYMEDIA"
        else:
            if self.api.tok is None or time.time() - self.api.t0 > 600: self.api.login()
            mh = new_handle(self.api._raw("POST", "/api/media/", data=data, ctype="image/png"))
            m = self.api.get(f"/api/media/{mh}")
            m["desc"] = "Find a Grave memorial 150656819, Arthur Leroy Libengood: text extract of the page fetched 18 Nov 2026"
            self.api.put(f"/api/media/{mh}", m)
        note_txt = ("Find a Grave memorial 150656819, https://www.findagrave.com/memorial/150656819/arthur-leroy-libengood "
                    "(page fetched 18 Nov 2026). Birth: 25 Dec 1931, Kiskiminetas Township, Armstrong County, Pennsylvania, USA. "
                    "Death: 18 Dec 1985 (aged 53), Apollo, Armstrong County, Pennsylvania, USA. Burial: Greenwood Memorial Park, "
                    "Lower Burrell, Westmoreland County, Pennsylvania, USA. Family members listed: parents Everett H. Libengood "
                    "(1905–1986) and Marie Lillian Hartman Libengood (1907–1987); spouse Nancy J. Hollis Libengood (1934–2023); "
                    "siblings William E. Libengood Sr (1930–2002), Paul E. Libengood (1934–2022), Dorothy Marie Libengood (1941–1941). "
                    "Created by LAB, 12 Aug 2015.")
        nh = new_handle(self.api.post("/api/notes/", {"_class": "Note", "type": "Citation", "private": False, "format": 0,
                                                       "text": {"_class": "StyledText", "string": note_txt, "tags": []}, "tag_list": []}))
        sh = new_handle(self.api.post("/api/sources/", {"_class": "Source", "title": title, "author": "Find a Grave",
                                                          "pubinfo": "https://www.findagrave.com/memorial/150656819/arthur-leroy-libengood",
                                                          "abbrev": "", "note_list": [], "media_list": [], "reporef_list": [],
                                                          "attribute_list": [], "tag_list": [], "private": False}))
        ch = new_handle(self.api.post("/api/citations/", {"_class": "Citation", "source_handle": sh,
                                                           "page": "https://www.findagrave.com/memorial/150656819/arthur-leroy-libengood (fetched 18 Nov 2026)",
                                                           "confidence": 3, "date": dict(EMPTY_DATE), "note_list": [nh],
                                                           "media_list": [{"_class": "MediaRef", "ref": mh, "rect": [], "private": False,
                                                                           "attribute_list": [], "citation_list": [], "note_list": []}],
                                                           "attribute_list": [], "tag_list": [], "private": False}))
        p = self.api.get(f"/api/people/{ppl[0]['handle']}")
        if ch not in p["citation_list"]: p["citation_list"].append(ch)
        if mh not in [m["ref"] for m in p["media_list"]]:
            p["media_list"].append({"_class": "MediaRef", "ref": mh, "rect": [], "private": False,
                                    "attribute_list": [], "citation_list": [], "note_list": []})
        self.api.put(f"/api/people/{p['handle']}", p)
        n = 0
        for r in p["event_ref_list"]:
            e = self.api.get(f"/api/events/{r['ref']}")
            if e["type"] in ("Birth", "Death", "Burial") and ch not in e["citation_list"]:
                e["citation_list"].append(ch); self.api.put(f"/api/events/{e['handle']}", e); n += 1
        self.log["citations"].append({"person": "I0997", "source": title, "events_cited": n})

    def run(self, media_path):
        self.load()
        for p in self.people:
            self.add_missing_events(p)
        for ev in list(self.events.values()):
            if str(ev["handle"]).startswith("DRY") or ev["handle"] in self.created: continue
            self.fix_event(ev)
        # a Birth retyped to Baptism above can leave a chart birth to add
        for r in self.log.get("event_retyped", []):
            kind, owner = self.owner.get(r["handle"], (None, None))
            if kind == "person" and not self.api.dry:
                self.add_missing_events(self.api.get(f"/api/people/{owner['handle']}"))
        self.dedupe_events()
        self.delete_orphans()
        self.frankie_notes()
        self.arthur_findagrave(media_path)
        return self.log


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log", required=True)
    ap.add_argument("--media", default="/workspace/genealogy-cindy/arthur_leroy_libengood_findagrave_150656819_extract_2026-11-18.png")
    a = ap.parse_args()
    api = Api(a.base, a.dry_run)
    m = Migration(api)
    log = m.run(a.media)
    log["writes"] = api.writes
    Path(a.log).write_text(json.dumps(log, indent=1, ensure_ascii=False))
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in log.items()}, indent=1))

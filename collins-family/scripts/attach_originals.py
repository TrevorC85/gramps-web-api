#!/usr/bin/env python3
"""Attach confirmed original records and source URLs for the 17 test people."""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:5055"
PASSFILE = Path("/workspace/gramps-web/.admin_password")
REPORT = Path("/workspace/gramps-web/attach_originals_report.json")
VERY_HIGH, HIGH, NORMAL, LOW = 4, 3, 2, 1
LIVING_IDS = {
    "I0001", "I0002", "I0003", "I0005", "I0007", "I0008",
    "I0009", "I0010", "I0011", "I0016", "I0017",
}


def load_owner():
    username = password = None
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and username is None:
            username = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and password is None and username:
            password = line.split("=", 1)[1].strip()
            break
    if not username or not password:
        raise SystemExit("could not parse owner credentials")
    return username, password


def get_token():
    user, pw = load_owner()
    body = json.dumps({"username": user, "password": pw}).encode()
    for attempt in range(8):
        req = urllib.request.Request(
            BASE + "/api/token/",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["access_token"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + attempt * 5)
                continue
            raise SystemExit(f"token failed: {e.code} {e.read()[:200]}")
    raise SystemExit("token rate limited")


class Api:
    def __init__(self, jwt: str):
        self.jwt = jwt

    def request(self, method: str, path: str, data=None, content_type="application/json"):
        url = BASE + path
        headers = {"Authorization": f"Bearer {self.jwt}"}
        body = None
        if data is not None:
            if isinstance(data, (bytes, bytearray)):
                body = data
                headers["Content-Type"] = content_type
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:1200]}")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None, content_type="application/json"):
        return self.request("POST", path, data=data, content_type=content_type)

    def put(self, path, data):
        return self.request("PUT", path, data=data)


def first_new_handle(resp):
    if isinstance(resp, dict) and resp.get("handle"):
        return resp["handle"]
    if isinstance(resp, list):
        for it in resp:
            if isinstance(it, dict) and it.get("type") == "add":
                return it.get("handle") or (it.get("new") or {}).get("handle")
        for it in resp:
            if isinstance(it, dict) and it.get("handle"):
                return it["handle"]
    raise RuntimeError(f"no handle in response: {json.dumps(resp)[:600]}")


def mime_for(path: Path):
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "application/octet-stream")


def note_payload(text, typ="Citation", private=False):
    return {
        "_class": "Note",
        "type": typ,
        "private": private,
        "format": 0,
        "text": {"string": text, "tags": []},
    }


def url_obj(path, desc, private=False, typ="Web Home"):
    return {"type": typ, "path": path, "desc": desc, "private": private}


def media_ref(handle, private=False):
    return {
        "ref": handle,
        "rect": [],
        "private": private,
        "citation_list": [],
        "note_list": [],
        "attribute_list": [],
    }


def unique_append(lst, item):
    if item not in lst:
        lst.append(item)
    return lst


class Attacher:
    def __init__(self, api: Api):
        self.api = api
        self.sources_by_title = {}
        self.media_by_desc = {}
        self.people = {}  # gid -> full object (refreshed as needed)
        self.events = {}
        self.families = {}
        self.report = {"people": {}, "unmatched_or_excluded": [], "sources_created": [], "errors": []}

    def refresh_indexes(self):
        for s in self.api.get("/api/sources/"):
            self.sources_by_title[s["title"]] = s
        for m in self.api.get("/api/media/"):
            if m.get("desc"):
                self.media_by_desc[m["desc"]] = m
        for p in self.api.get("/api/people/"):
            self.people[p["gramps_id"]] = p
        for e in self.api.get("/api/events/"):
            self.events[e["handle"]] = e
        for f in self.api.get("/api/families/"):
            self.families[f["gramps_id"]] = f

    def get_person(self, gid, fresh=False):
        if fresh or gid not in self.people:
            # find handle
            if gid in self.people:
                h = self.people[gid]["handle"]
            else:
                self.refresh_indexes()
                h = self.people[gid]["handle"]
            self.people[gid] = self.api.get(f"/api/people/{h}")
        return self.people[gid]

    def get_family(self, fid, fresh=False):
        if fresh or fid not in self.families:
            if fid in self.families:
                h = self.families[fid]["handle"]
            else:
                self.refresh_indexes()
                h = self.families[fid]["handle"]
            self.families[fid] = self.api.get(f"/api/families/{h}")
        return self.families[fid]

    def get_event(self, handle, fresh=False):
        if fresh or handle not in self.events:
            self.events[handle] = self.api.get(f"/api/events/{handle}")
        return self.events[handle]

    def person_event(self, gid, typ):
        p = self.get_person(gid)
        for ref in p.get("event_ref_list") or []:
            ev = self.get_event(ref["ref"])
            if ev.get("type") == typ:
                return ev
        return None

    def family_event(self, fid, typ):
        f = self.get_family(fid)
        for ref in f.get("event_ref_list") or []:
            ev = self.get_event(ref["ref"])
            if ev.get("type") == typ:
                return ev
        return None

    def ensure_source(self, title, author="", pubinfo="", abbrev="", private=False):
        if title in self.sources_by_title:
            return self.sources_by_title[title]["handle"]
        payload = {
            "_class": "Source",
            "title": title,
            "author": author,
            "pubinfo": pubinfo,
            "abbrev": abbrev or title[:40],
            "private": private,
        }
        resp = self.api.post("/api/sources/", payload)
        h = first_new_handle(resp)
        self.sources_by_title[title] = {"handle": h, "title": title}
        self.report["sources_created"].append(title)
        print("SOURCE", title, h)
        return h

    def ensure_note(self, text, typ="Citation", private=False):
        resp = self.api.post("/api/notes/", note_payload(text, typ, private))
        return first_new_handle(resp)

    def ensure_media(self, path: Path, desc: str, private=False):
        if desc in self.media_by_desc:
            h = self.media_by_desc[desc]["handle"]
            # still ensure private/desc
            obj = self.api.get(f"/api/media/{h}")
            if obj.get("desc") != desc or bool(obj.get("private")) != bool(private):
                obj["desc"] = desc
                obj["private"] = private
                self.api.put(f"/api/media/{h}", obj)
            return h
        raw = path.read_bytes()
        mime = mime_for(path)
        resp = self.api.post("/api/media/", raw, content_type=mime)
        h = first_new_handle(resp)
        obj = self.api.get(f"/api/media/{h}")
        obj["desc"] = desc
        obj["private"] = private
        self.api.put(f"/api/media/{h}", obj)
        self.media_by_desc[desc] = {"handle": h, "desc": desc}
        print("MEDIA", desc, h, path.name)
        return h

    def ensure_citation(self, source_handle, page, confidence, private, media_handles, note_text=None):
        """Create a new citation (citations are cheap; skip exact page+source dupes)."""
        # de-dupe by source+page
        for c in self.api.get("/api/citations/"):
            if c.get("source_handle") == source_handle and c.get("page") == page:
                return c["handle"]
        note_list = []
        if note_text:
            note_list.append(self.ensure_note(note_text, typ="Citation", private=private))
        payload = {
            "_class": "Citation",
            "source_handle": source_handle,
            "page": page,
            "confidence": confidence,
            "private": private,
            "media_list": [media_ref(h, private) for h in media_handles],
            "note_list": note_list,
        }
        resp = self.api.post("/api/citations/", payload)
        h = first_new_handle(resp)
        print("CITE", page[:80], h)
        return h

    def _put_obj(self, kind, obj):
        h = obj["handle"]
        self.api.put(f"/api/{kind}/{h}", obj)
        # refresh cache
        fresh = self.api.get(f"/api/{kind}/{h}")
        if kind == "people":
            self.people[fresh["gramps_id"]] = fresh
        elif kind == "families":
            self.families[fresh["gramps_id"]] = fresh
        elif kind == "events":
            self.events[fresh["handle"]] = fresh
        elif kind == "sources":
            self.sources_by_title[fresh["title"]] = fresh
        return fresh

    def link_citation_to_person(self, gid, cite_h, media_handles=None, urls=None, private=False):
        p = self.get_person(gid, fresh=True)
        unique_append(p.setdefault("citation_list", []), cite_h)
        if media_handles:
            existing = {m["ref"] for m in p.get("media_list") or []}
            for mh in media_handles:
                if mh not in existing:
                    p.setdefault("media_list", []).append(media_ref(mh, private=private or gid in LIVING_IDS))
        if urls:
            have = {(u.get("path"), u.get("desc")) for u in p.get("urls") or []}
            for u in urls:
                key = (u["path"], u.get("desc"))
                if key not in have:
                    u = dict(u)
                    if gid in LIVING_IDS:
                        u["private"] = True
                    p.setdefault("urls", []).append(u)
        self._put_obj("people", p)
        rec = self.report["people"].setdefault(gid, {"citations": [], "media": [], "urls": []})
        if cite_h not in rec["citations"]:
            rec["citations"].append(cite_h)
        for mh in media_handles or []:
            if mh not in rec["media"]:
                rec["media"].append(mh)
        for u in urls or []:
            rec["urls"].append({"desc": u.get("desc"), "path": u.get("path")})

    def link_citation_to_event(self, ev, cite_h, media_handles=None, private=False):
        ev = self.get_event(ev["handle"], fresh=True)
        unique_append(ev.setdefault("citation_list", []), cite_h)
        if media_handles:
            existing = {m["ref"] for m in ev.get("media_list") or []}
            for mh in media_handles:
                if mh not in existing:
                    ev.setdefault("media_list", []).append(media_ref(mh, private))
        self._put_obj("events", ev)

    def link_citation_to_family(self, fid, cite_h, media_handles=None, private=False):
        f = self.get_family(fid, fresh=True)
        unique_append(f.setdefault("citation_list", []), cite_h)
        if media_handles:
            existing = {m["ref"] for m in f.get("media_list") or []}
            for mh in media_handles:
                if mh not in existing:
                    f.setdefault("media_list", []).append(media_ref(mh, private))
        self._put_obj("families", f)

    def link_citation_to_child_ref(self, fid, child_gid, cite_h):
        f = self.get_family(fid, fresh=True)
        child_h = self.get_person(child_gid)["handle"]
        changed = False
        for cr in f.get("child_ref_list") or []:
            if cr.get("ref") == child_h:
                unique_append(cr.setdefault("citation_list", []), cite_h)
                changed = True
        if changed:
            self._put_obj("families", f)

    def link_media_to_source(self, source_h, media_handles, private=False):
        s = self.api.get(f"/api/sources/{source_h}")
        existing = {m["ref"] for m in s.get("media_list") or []}
        for mh in media_handles:
            if mh not in existing:
                s.setdefault("media_list", []).append(media_ref(mh, private))
        self._put_obj("sources", s)


def attach_all(a: Attacher):
    # Convenience wrappers
    def src(*args, **kw):
        return a.ensure_source(*args, **kw)

    def media(path, desc, private=False):
        return a.ensure_media(Path(path), desc, private)

    def cite(source_h, page, confidence, private, media_hs, note=None):
        return a.ensure_citation(source_h, page, confidence, private, media_hs, note)

    def add_to(gids, cite_h, media_hs=None, urls=None, events=None, family=None, family_event_type=None, child=None, private=False):
        for gid in gids:
            a.link_citation_to_person(gid, cite_h, media_hs, urls, private=private or gid in LIVING_IDS)
            if events:
                for typ in events:
                    ev = a.person_event(gid, typ)
                    if ev:
                        a.link_citation_to_event(ev, cite_h, media_hs, private=private or ev.get("private") or gid in LIVING_IDS)
        if family:
            a.link_citation_to_family(family, cite_h, media_hs, private=private)
            if family_event_type:
                ev = a.family_event(family, family_event_type)
                if ev:
                    a.link_citation_to_event(ev, cite_h, media_hs, private=private)
            if child:
                a.link_citation_to_child_ref(family, child, cite_h)

    # ---------------------------------------------------------------------
    # 1. James W. Collins — Find a Grave headstones
    # ---------------------------------------------------------------------
    s = src(
        "Find a Grave memorial 180761608, James W. Collins, St. Mary's Cemetery, Needham, Massachusetts",
        author="Find a Grave (photos by Paul Newcomb; memorial created by GrannyT, 26 Jun 2017)",
        pubinfo="Memorial 180761608; St. Mary's Cemetery, Needham, Massachusetts, Section J",
        abbrev="FaG 180761608 James W. Collins",
    )
    m_stone = media(
        "/workspace/genealogy/branches_r1/fg/jw_collins_stone_180761608_1498515172.jpg",
        "James W. Collins family stone, St. Mary's Cemetery, Needham, Massachusetts (Find a Grave photo 1498515172, Paul Newcomb)",
    )
    m_navy = media(
        "/workspace/genealogy/branches_r1/fg/jw_collins_navy_marker_180761608_1498515209.jpg",
        "James W. Collins bronze VA marker, St. Mary's Cemetery, Needham, Massachusetts (Find a Grave photo 1498515209, Paul Newcomb)",
    )
    a.link_media_to_source(s, [m_stone, m_navy])
    url = url_obj(
        "https://www.findagrave.com/memorial/180761608/james-w-collins",
        "Find a Grave memorial 180761608, James W. Collins",
    )
    c = cite(
        s,
        "Memorial 180761608; St. Mary's Cemetery, Needham, Massachusetts, Section J. Family stone: “Collins / JAMES W. / MAR. 10, 1928 – MAR. 24, 1985”. VA marker: “JAMES W COLLINS / S1 US NAVY / WORLD WAR II / MAR 10 1928 + MAR 24 1985”. https://www.findagrave.com/memorial/180761608/james-w-collins",
        VERY_HIGH,
        False,
        [m_stone, m_navy],
        note="Original headstone photographs saved from Find a Grave memorial 180761608 (photos by Paul Newcomb). Record URL: https://www.findagrave.com/memorial/180761608/james-w-collins",
    )
    add_to(["I0004"], c, [m_stone, m_navy], urls=[url], events=["Birth", "Death", "Burial"])

    # ---------------------------------------------------------------------
    # 2. Kenneth × Marge — Morning Union wedding announcement, 4 Mar 1953
    # ---------------------------------------------------------------------
    s = src(
        "The Morning Union (Springfield, Massachusetts), wedding announcement of Kenneth Earl Leger and Margaret Christine, 4 March 1953",
        author="The Morning Union",
        pubinfo="Springfield, Massachusetts, 4 March 1953; Newspapers.com clip id 1068531518 (clip no longer loads; a saved upscaled copy of the page is retained)",
        abbrev="Morning Union 4 Mar 1953 Leger wedding",
        private=True,
    )
    m_wed = media(
        "/workspace/genealogy/moms_wedding_announcement_up4.png",
        "The Morning Union, 4 March 1953, wedding announcement page (saved upscaled Newspapers.com clip 1068531518; the online clip no longer loads)",
        private=True,
    )
    a.link_media_to_source(s, [m_wed], private=True)
    c = cite(
        s,
        "Issue of 4 March 1953; Newspapers.com clip id 1068531518 (no longer loads). Saved file: moms_wedding_announcement_up4.png. The free saved page is a full newspaper sheet and is hard to read in places.",
        HIGH,
        True,
        [m_wed],
        note="Wedding announcement of Kenneth Earl Leger and Margaret Christine (“Marge”), The Morning Union, 4 March 1953. The Newspapers.com clip (id 1068531518) is recorded as no longer loading; this is the saved upscaled copy cited in FAMILY_RECORD.md. Living spouse: treat as private.",
    )
    add_to(["I0006", "I0007"], c, [m_wed], family="F0003", family_event_type="Marriage", private=True)

    # ---------------------------------------------------------------------
    # 3. Kenneth Earl Leger — Legacy Remembers obituary (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "Legacy Remembers obituary, Kenneth Earl Leger, 2007",
        author="Legacy Remembers (as cited in FAMILY_RECORD.md)",
        pubinfo="Published 27–28 January 2007; https://www.legacy.com/us/obituaries/legacyremembers/kenneth-leger-obituary?id=25691796",
        abbrev="Legacy obituary Kenneth Earl Leger 2007",
    )
    url = url_obj(
        "https://www.legacy.com/us/obituaries/legacyremembers/kenneth-leger-obituary?id=25691796",
        "Legacy Remembers obituary, Kenneth Earl Leger, 2007",
    )
    c = cite(
        s,
        "Legacy Remembers, published 27–28 January 2007; death Thursday 25 January 2007. https://www.legacy.com/us/obituaries/legacyremembers/kenneth-leger-obituary?id=25691796",
        HIGH,
        False,
        [],
        note="No original obituary image or clipping file was found on the box; this citation is the Legacy Remembers URL recorded in FAMILY_RECORD.md. Research notes exist as kenneth_leger_obituary_2007_notes.md but are not an original record image.",
    )
    add_to(["I0006"], c, urls=[url], events=["Death"])

    # ---------------------------------------------------------------------
    # 4. 1988 King County marriage certificate, Mike and Judy (two pages)
    # ---------------------------------------------------------------------
    s = src(
        "Washington State marriage certificate, Michael Floyd Libengood and Judy Eileen Schneider, 17 March 1988, King County",
        author="State of Washington; King County; Washington Department of Social and Health Services, Vital Records",
        pubinfo="King County Marriage Records, 1855–2017, reference kingcoarchmc880323_07547; DSHS Certificate of Marriage MA_1988004590 (C 04590). License 12 February 1988; ceremony 17 March 1988.",
        abbrev="WA marriage cert Libengood–Schneider 1988",
        private=True,
    )
    m1988a = media(
        "/workspace/genealogy-cindy/libengood_schneider_marriage_1988_king_wadigitalarchives_1.png",
        "King County, Washington, marriage certificate (series A, ceremony page), Michael F. Libengood and Judy E. Schneider, 17 March 1988",
        private=True,
    )
    m1988b = media(
        "/workspace/genealogy-cindy/libengood_schneider_marriage_1988_king_wadigitalarchives_2.png",
        "Washington DSHS Certificate of Marriage MA_1988004590 (C 04590), Michael Floyd Libengood and Judy Eileen Schneider, filed March 1988 (names parents and birth dates)",
        private=True,
    )
    a.link_media_to_source(s, [m1988a, m1988b], private=True)
    urls_1988 = [
        url_obj("https://digitalarchives.wa.gov/Record/View/46F85E6D5E191F5DA33231052EA54548", "King County marriage index/record, Michael F. Libengood and Judy E. Schneider, 17 March 1988 (kingcoarchmc880323_07547)", private=True),
        url_obj("https://digitalarchives.wa.gov/DigitalObject/Download/fb321df0-a618-4d3d-b79a-e2998467e3ba", "Digital object: King County 1988 marriage certificate image (ceremony page)", private=True),
        url_obj("https://digitalarchives.wa.gov/Record/View/E1DD24DEECD4039479AA51EEACB64B04", "Washington DOH marriage record, Michael F. Libengood and Judy E. Schneider, 17 March 1988 (MA_1988004590)", private=True),
        url_obj("https://digitalarchives.wa.gov/DigitalObject/Download/9c086c5f-f1d8-4d7f-8461-3e84d25f813d", "Digital object: DSHS Certificate of Marriage C 04590 image", private=True),
    ]
    c = cite(
        s,
        "King County ref kingcoarchmc880323_07547 (series A 880323 7547); DSHS MA_1988004590 / C 04590. License 12 Feb 1988; ceremony 17 Mar 1988, King County. Record views: https://digitalarchives.wa.gov/Record/View/46F85E6D5E191F5DA33231052EA54548 and https://digitalarchives.wa.gov/Record/View/E1DD24DEECD4039479AA51EEACB64B04 . Images: https://digitalarchives.wa.gov/DigitalObject/Download/fb321df0-a618-4d3d-b79a-e2998467e3ba and https://digitalarchives.wa.gov/DigitalObject/Download/9c086c5f-f1d8-4d7f-8461-3e84d25f813d",
        VERY_HIGH,
        True,
        [m1988a, m1988b],
        note="Saved screenshots of the Washington Digital Archives certificate images, confirmed against CINDY_FAMILY_RECORD.md. The certificate names the parties, the 17 March 1988 King County ceremony, birth dates and places, and parents: Paul E. Libengood and Donna G. Townsend (groom); John Schneider and Mary Fletcher (bride). Living people’s address details are on the image and are not transcribed here. Treat as private.",
    )
    add_to(
        ["I0009", "I0010", "I0013", "I0011", "I0014", "I0015"],
        c,
        [m1988a, m1988b],
        urls=urls_1988,
        events=["Birth"],  # cert supports Mike and Judy (and parents named)
        family="F0005",
        family_event_type="Marriage",
        private=True,
    )
    # Parent-child links named on the certificate
    a.link_citation_to_child_ref("F0007", "I0009", c)  # Mike, child of Paul × Donna
    a.link_citation_to_child_ref("F0008", "I0010", c)  # Judy, child of John × Mary
    # Also attach to the parents' family objects
    a.link_citation_to_family("F0007", c, [m1988b], private=True)
    a.link_citation_to_family("F0008", c, [m1988b], private=True)

    # ---------------------------------------------------------------------
    # 5. 1995 King County divorce index (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "Washington Department of Health divorce index, Michael F. Libengood and Judy E. Schneider, 16 March 1995, King County",
        author="Washington State Archives, Digital Archives (Department of Health divorce index)",
        pubinfo="Decree date 16 March 1995; decree type CertificateOfDissolution; county of decree King; reference DI_1995003501",
        abbrev="WA divorce index Libengood–Schneider 1995",
        private=True,
    )
    url = url_obj(
        "https://digitalarchives.wa.gov/Record/View/3E57CF5816A9AEACDAE9F02A1965BB0E",
        "Washington divorce index, Michael F. Libengood and Judy E. Schneider, 16 March 1995 (DI_1995003501)",
        private=True,
    )
    c = cite(
        s,
        "CertificateOfDissolution, King County, 16 March 1995, reference DI_1995003501. https://digitalarchives.wa.gov/Record/View/3E57CF5816A9AEACDAE9F02A1965BB0E",
        VERY_HIGH,
        True,
        [],
        note="Index entry only; no decree image was found on the box. URL from CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0009", "I0010"], c, urls=[url], family="F0005", family_event_type="Divorce", private=True)

    # ---------------------------------------------------------------------
    # 6. 2010 King County marriage, Trevor and Cindy (URLs only; images N/A)
    # ---------------------------------------------------------------------
    s = src(
        "Washington State Digital Archives, King County marriage records of Trevor James Collins and Cindy Michelle Libengood, 2010",
        author="Washington State Archives, Digital Archives",
        pubinfo="Application recorded 24 August 2010, reference 20100824710010-0; certificate recorded 22 September 2010, reference 20100922750020-0; Department of Health Marriage Index, 1969–2022, statewide, year 2010. Images not available online (index shows N/A).",
        abbrev="WA marriage Collins–Libengood 2010",
        private=True,
    )
    urls_2010 = [
        url_obj("https://digitalarchives.wa.gov/Record/View/9990C3422F6F850484B94E5D09B682F2", "King County marriage application, Trevor James Collins and Cindy Michelle Libengood, recorded 24 August 2010 (20100824710010-0)", private=True),
        url_obj("https://digitalarchives.wa.gov/Record/View/25805D44D4A350668628563898204DEF", "King County marriage certificate, Trevor James Collins and Cindy Michelle Libengood, recorded 22 September 2010 (20100922750020-0)", private=True),
        url_obj("https://digitalarchives.wa.gov/Record/View/9AC89AE284DFB8E470FE98E0BF9C2208", "Washington DOH Marriage Index, 2010, Trevor J. Collins and Cindy M. Libengood", private=True),
    ]
    c = cite(
        s,
        "Application 20100824710010-0, recorded 24 Aug 2010: https://digitalarchives.wa.gov/Record/View/9990C3422F6F850484B94E5D09B682F2 ; certificate 20100922750020-0, recorded 22 Sep 2010: https://digitalarchives.wa.gov/Record/View/25805D44D4A350668628563898204DEF ; statewide index: https://digitalarchives.wa.gov/Record/View/9AC89AE284DFB8E470FE98E0BF9C2208 . Images not available online (N/A).",
        VERY_HIGH,
        True,
        [],
        note="Three Digital Archives record-view URLs from CINDY_FAMILY_RECORD.md. The index notes that images are not available online. No certificate image file was found on the box.",
    )
    add_to(["I0001", "I0008"], c, urls=urls_2010, family="F0004", family_event_type="Marriage", private=True)

    # ---------------------------------------------------------------------
    # 7. 1948 King County marriage, J. Donald Schneider and Mary Margaret Fletcher
    # ---------------------------------------------------------------------
    s = src(
        "King County, Washington, marriage certificate, J. Donald Schneider and Mary Margaret Fletcher, 6 March 1948",
        author="State of Washington, County of King",
        pubinfo="King County Marriage Records, 1855–2017, reference kingcoarchmcvol167_1154; license dated 8 May 1947; ceremony 6 March 1948",
        abbrev="WA marriage Schneider–Fletcher 1948",
    )
    m1948 = media(
        "/workspace/genealogy-cindy/schneider_fletcher_marriage_1948_king_wadigitalarchives.png",
        "King County, Washington, marriage certificate, J. Donald Schneider and Mary Margaret Fletcher, 6 March 1948 (kingcoarchmcvol167_1154)",
    )
    a.link_media_to_source(s, [m1948])
    url = url_obj(
        "https://digitalarchives.wa.gov/Record/View/1FD9A0137BB70607D658FF01976D04C0",
        "King County marriage record, J. Donald Schneider and Mary Margaret Fletcher, 6 March 1948 (kingcoarchmcvol167_1154)",
    )
    c = cite(
        s,
        "Reference kingcoarchmcvol167_1154; license 8 May 1947; ceremony 6 March 1948, King County, Washington. https://digitalarchives.wa.gov/Record/View/1FD9A0137BB70607D658FF01976D04C0",
        VERY_HIGH,
        False,
        [m1948],
        note="Saved screenshot of the Washington Digital Archives certificate image, confirmed against CINDY_FAMILY_RECORD.md. Parties: J. Donald Schneider and Mary Margaret Fletcher.",
    )
    add_to(["I0014", "I0015"], c, [m1948], urls=[url], family="F0008", family_event_type="Marriage")

    # ---------------------------------------------------------------------
    # 8. John Donald Schneider Jr. headstone, Tahoma National Cemetery
    # ---------------------------------------------------------------------
    s = src(
        "Find a Grave memorial 72654689, headstone of John Donald Schneider Jr., Tahoma National Cemetery",
        author="Find a Grave (headstone photograph by Gerald Makosky, 18 September 2015; memorial created by International Wargraves Photography Project, 3 July 2011)",
        pubinfo="Memorial 72654689; Tahoma National Cemetery, Kent, Washington, Section H Row D Site 112",
        abbrev="FaG 72654689 John Donald Schneider Jr.",
    )
    m_hs = media(
        "/workspace/genealogy-cindy/john_donald_schneider_jr_headstone_2009_tahoma_findagrave.jpg",
        "Headstone of John Donald Schneider Jr., Tahoma National Cemetery (Find a Grave photo by Gerald Makosky, 18 Sep 2015)",
    )
    a.link_media_to_source(s, [m_hs])
    urls = [
        url_obj("https://www.findagrave.com/memorial/72654689/john-donald-schneider", "Find a Grave memorial 72654689, John Donald Schneider Jr."),
        url_obj("https://images.findagrave.com/photos/2015/260/72654689_1442584349.jpg", "Find a Grave headstone photograph of John Donald Schneider Jr. (Gerald Makosky, 18 Sep 2015)"),
    ]
    c = cite(
        s,
        "Memorial 72654689; Tahoma National Cemetery, Kent, Washington, Section H Row D Site 112. Inscription: “SCHNEIDER / JOHN / DONALD JR / AS / US NAVY / WORLD WAR II / 1917 2009 / REST IN PEACE / YOUR OFF AND / RUNNING”. https://www.findagrave.com/memorial/72654689/john-donald-schneider",
        VERY_HIGH,
        False,
        [m_hs],
        note="Local copy of the Find a Grave headstone photograph cited in CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0014"], c, [m_hs], urls=urls, events=["Birth", "Death", "Burial"])

    # ---------------------------------------------------------------------
    # 9. WA death index, John Donald Schneider Jr., 14 Dec 2009 (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "Washington Department of Health Death Index, John D. Schneider, 14 December 2009, King County",
        author="Washington State Archives, Digital Archives",
        pubinfo="Person JOHN D SCHNEIDER; death county King; residence King; death date 12/14/2009; age 92; reference DB16E489-5465-2645-A40D-B9C800F262A2",
        abbrev="WA death index John D. Schneider 2009",
    )
    url = url_obj(
        "https://digitalarchives.wa.gov/Record/View/489DA0D7EDFE452BCD4DD737D9DF48E4",
        "Washington death index, John D. Schneider, 14 December 2009, King County",
    )
    c = cite(
        s,
        "JOHN D SCHNEIDER, death 14 December 2009, King County, age 92, residence King County. No image. https://digitalarchives.wa.gov/Record/View/489DA0D7EDFE452BCD4DD737D9DF48E4",
        VERY_HIGH,
        False,
        [],
        note="Index entry only (no image), from CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0014"], c, urls=[url], events=["Death"])

    # ---------------------------------------------------------------------
    # 10. Mary Margaret Fletcher 1926 birth register (FamilySearch index page)
    # ---------------------------------------------------------------------
    s = src(
        "Washington, County Birth Registers, 1873–1965, FamilySearch index, Mary Margaret Fletcher, 5 May 1926",
        author="FamilySearch, citing Washington, County Birth Registers, 1873–1965",
        pubinfo="FamilySearch ark:/61903/1:1:D8RN-MD6Z (accessed citation dated Fri Mar 08 12:41:56 UTC 2024 on the record card)",
        abbrev="FS birth register Mary Margaret Fletcher 1926",
    )
    m_b = media(
        "/workspace/genealogy-cindy/mary-margaret-fletcher-1926-birth-record-familysearch.png",
        "FamilySearch record page, Washington County Birth Registers, Mary Margaret Fletcher, 5 May 1926 (ark 1:1:D8RN-MD6Z). A thumbnail of the register page is visible; the saved screenshot is the index card, not an enlargement of the register image.",
    )
    a.link_media_to_source(s, [m_b])
    url = url_obj(
        "https://www.familysearch.org/ark:/61903/1:1:D8RN-MD6Z",
        "FamilySearch ark 1:1:D8RN-MD6Z, Mary Margaret Fletcher, birth 5 May 1926",
    )
    c = cite(
        s,
        "“Washington, County Birth Registers, 1873–1965,” FamilySearch (https://www.familysearch.org/ark:/61903/1:1:D8RN-MD6Z : Fri Mar 08 12:41:56 UTC 2024). Name Mary Margaret Fletcher; female; birth 5 May 1926, Washington, United States; father Daniel Martin Fletcher; mother Loraito Anna Squier (index card; the register image itself was not enlarged in the saved screenshot).",
        HIGH,
        False,
        [m_b],
        note="Saved FamilySearch index-card screenshot cited in CINDY_FAMILY_RECORD.md. Parents’ names and the date are taken from the index, not from a line-by-line reading of the register image.",
    )
    add_to(["I0015"], c, [m_b], urls=[url], events=["Birth"])

    # ---------------------------------------------------------------------
    # 11. Mary M. Schneider 2010 Kitsap death index — unconfirmed identification
    # ---------------------------------------------------------------------
    s = src(
        "Washington Department of Health Death Index, Mary M. Schneider, 23 March 2010, Kitsap County (identification as Mary Margaret Fletcher Schneider not confirmed)",
        author="Washington State Archives, Digital Archives; SortedByName Death Master File compilation as cited",
        pubinfo="Death and residence Kitsap County; age 83; reference 0FCADF75-E0F7-7240-8735-B29AE7B41623. Identification of this index entry as Mary Margaret (Fletcher) Schneider remains unconfirmed in CINDY_FAMILY_RECORD.md.",
        abbrev="WA death index Mary M. Schneider 2010 (unconfirmed)",
    )
    urls = [
        url_obj("https://digitalarchives.wa.gov/Record/View/CE1AC71DE61F60C65A883358EBEA5EC5", "Washington death index, MARY M SCHNEIDER, 23 March 2010, Kitsap County (identification unconfirmed)"),
        url_obj("https://sortedbyname.com/letter_s/schneider/marym_schneider_1.html", "SortedByName / Death Master File compilation, Mary M. Schneider, born 5 May 1926, died 23 March 2010"),
    ]
    c = cite(
        s,
        "MARY M SCHNEIDER, died 23 March 2010, age 83, death and residence Kitsap County (no image): https://digitalarchives.wa.gov/Record/View/CE1AC71DE61F60C65A883358EBEA5EC5 . SortedByName compilation of the Death Master File: https://sortedbyname.com/letter_s/schneider/marym_schneider_1.html . CINDY_FAMILY_RECORD.md: this death is not yet confirmed as hers (Kitsap vs. a Seattle family is the main caution).",
        LOW,
        False,
        [],
        note="Recorded because the family record cites these URLs as a possible death, explicitly marked unconfirmed / needs confirmation. No original death-certificate image was found.",
    )
    add_to(["I0015"], c, urls=urls, events=["Death"])

    # ---------------------------------------------------------------------
    # 12. 1920 census, Seattle, Donald and Leona Schneider household (John as son)
    # ---------------------------------------------------------------------
    s = src(
        "1920 U.S. census, Seattle, King County, Washington, Donald and Leona Schneider household",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="Seattle, ED 110, household 165, sheet 7A (T625); DGS 004968991, image 570. FamilySearch ark:/61903/1:1:MHN5-9MD",
        abbrev="1920 census Seattle Schneider household",
    )
    m1920 = media(
        "/workspace/genealogy-cindy/schneider_donald_leona_1920_census_seattle_familysearch.png",
        "FamilySearch record page, 1920 U.S. census, Seattle, Leona Schneider (Donald and Leona Schneider household, ED 110, sheet 7A)",
    )
    a.link_media_to_source(s, [m1920])
    url = url_obj(
        "https://www.familysearch.org/ark:/61903/1:1:MHN5-9MD",
        "FamilySearch ark 1:1:MHN5-9MD, 1920 census, Seattle, Donald and Leona Schneider household",
    )
    c = cite(
        s,
        "Seattle, ED 110, household 165, sheet 7A (T625), DGS 004968991, image 570. https://www.familysearch.org/ark:/61903/1:1:MHN5-9MD . Household as abstracted in CINDY_FAMILY_RECORD.md: Donald Schneider, head, 24, born Iowa; Leona, wife, 20, born Washington; Donald, son, 2 years 6 months, born Washington. The saved screenshot is the FamilySearch card for Leona Schneider in this household.",
        HIGH,
        False,
        [m1920],
        note="Screenshot of the FamilySearch record page cited in CINDY_FAMILY_RECORD.md for the 1920 household that includes son Donald, age 2 years 6 months (John Donald Schneider Jr.).",
    )
    add_to(["I0014"], c, [m1920], urls=[url], events=["Birth"])

    # ---------------------------------------------------------------------
    # 13. 1930 census, Seattle, John D. Snyder in the Toney J. King household (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "1930 U.S. census, Seattle, King County, Washington, John D. Snyder in the Toney J. King household",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="T626, sheet 16B, line 71; DGS 004955348, image 448. FamilySearch ark:/61903/1:1:XC3W-SVK",
        abbrev="1930 census Seattle John D. Snyder",
    )
    url = url_obj(
        "https://www.familysearch.org/ark:/61903/1:1:XC3W-SVK",
        "FamilySearch ark 1:1:XC3W-SVK, 1930 census, Seattle, John D. Snyder (age 12) in the Toney J. King household",
    )
    c = cite(
        s,
        "Seattle (T626, sheet 16B, line 71), DGS 004955348, image 448. https://www.familysearch.org/ark:/61903/1:1:XC3W-SVK . Household as abstracted in CINDY_FAMILY_RECORD.md: Toney J King, head, 58, born Michigan; Frankie A, wife, 52, born Canada; Leona K Snyder, daughter, 30, born Washington; John D Snyder, 12, born Washington. No screenshot of this schedule was found on the box.",
        HIGH,
        False,
        [],
        note="URL-only citation from CINDY_FAMILY_RECORD.md; no local image of this 1930 schedule was found.",
    )
    add_to(["I0014"], c, urls=[url], events=["Birth"])

    # ---------------------------------------------------------------------
    # 14. 1940 census, Seattle, J Donald Schneider in the Toney J. King household
    # ---------------------------------------------------------------------
    s = src(
        "1940 U.S. census, Seattle, King County, Washington, J Donald Schneider in the Toney J. King household",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="Seattle Election Precinct, King County, Washington; ED 40-35, sheet 10B, line 50 (T627); Digital Folder 005460467, Image 65. FamilySearch ark:/61903/1:1:K939-5RP",
        abbrev="1940 census Seattle J Donald Schneider",
    )
    m1940j = media(
        "/workspace/genealogy-cindy/schneider_j_donald_king_1940_census_seattle_familysearch.png",
        "FamilySearch record page, 1940 U.S. census, Seattle, J Donald Schneider, grandson, age 22, in the Toney J. King household (ark 1:1:K939-5RP)",
    )
    a.link_media_to_source(s, [m1940j])
    urls = [
        url_obj("https://www.familysearch.org/ark:/61903/1:1:K939-5RP", "FamilySearch ark 1:1:K939-5RP, 1940 census, J Donald Schneider in the Toney J. King household"),
        url_obj("https://www.familysearch.org/ark:/61903/3:1:3QS7-89M1-SG8", "FamilySearch image, 1940 census, Seattle ED 40-35, sheet 10B"),
    ]
    c = cite(
        s,
        "Seattle Election Precinct, King, Washington; ED 40-35, sheet 10B, line 50 (T627); Digital Folder 005460467, Image 65. https://www.familysearch.org/ark:/61903/1:1:K939-5RP ; image https://www.familysearch.org/ark:/61903/3:1:3QS7-89M1-SG8 . Household: Toney J King, head, 68, born Michigan; Frankie A King, wife, 62, born Canada; J Donald Schneider, grandson, 22, born Washington. Identification as John Donald Schneider Jr. is probable per CINDY_FAMILY_RECORD.md.",
        HIGH,
        False,
        [m1940j],
        note="Saved FamilySearch record-page screenshot cited in CINDY_FAMILY_RECORD.md. The identification of this 22-year-old grandson as John Donald Schneider Jr. is labeled probable in the family record.",
    )
    add_to(["I0014"], c, [m1940j], urls=urls, events=["Birth"])

    # ---------------------------------------------------------------------
    # 15. 1940 census, Weirton, Dona Townsend in the Howard Townsend household
    # ---------------------------------------------------------------------
    s = src(
        "1940 U.S. census, Weirton, Hancock County, West Virginia, Dona Townsend in the Howard Townsend household",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="Butler Magisterial District (Weirton), Hancock County, West Virginia; ED 15-17, sheet 8B, line 47 (T627); DGS 005462133, image 408. FamilySearch ark:/61903/1:1:K7C4-P97 (related personal ark …K7C4-P94 also cited)",
        abbrev="1940 census Weirton Dona Townsend",
        private=True,
    )
    m_dona = media(
        "/workspace/genealogy-cindy/Dona_Townsend_1940_census_family.png",
        "FamilySearch record page, 1940 U.S. census, Dona Townsend, daughter, age 3, born Ohio, in the Howard and Blanche Townsend household, Weirton, West Virginia",
        private=True,
    )
    m_how = media(
        "/workspace/genealogy-cindy/townsend_howard_blanche_1940_census_weirton_wv_familysearch.png",
        "FamilySearch screenshot, 1940 U.S. census, Howard and Blanche Townsend household, Weirton, West Virginia (includes daughter Dona, age 3)",
        private=True,
    )
    a.link_media_to_source(s, [m_dona, m_how], private=True)
    urls = [
        url_obj("https://www.familysearch.org/ark:/61903/1:1:K7C4-P97", "FamilySearch ark 1:1:K7C4-P97, 1940 census, Howard Townsend household, Weirton, West Virginia", private=True),
        url_obj("https://www.familysearch.org/ark:/61903/1:1:K7C4-P94", "FamilySearch ark 1:1:K7C4-P94, 1940 census, Dona Townsend in the Howard Townsend household (related personal ark)", private=True),
    ]
    c = cite(
        s,
        "Butler Magisterial District (Weirton), Hancock County, West Virginia; ED 15-17, sheet 8B, line 47 (T627); DGS 005462133, image 408. 1935 residence Steubenville, Ohio. https://www.familysearch.org/ark:/61903/1:1:K7C4-P97 (household) and https://www.familysearch.org/ark:/61903/1:1:K7C4-P94 (Dona’s personal ark, later citation). Household: Howard Townsend, head, 29, born Indiana; Blanche, wife, 28, born Pennsylvania; Richard, son, 8, born Pennsylvania; “Dona”, daughter, 3, born Ohio.",
        VERY_HIGH,
        True,
        [m_dona, m_how],
        note="Saved FamilySearch screenshots cited in CINDY_FAMILY_RECORD.md. This is a childhood census of a living person and is marked private.",
    )
    add_to(["I0011"], c, [m_dona, m_how], urls=urls, events=["Birth"], private=True)

    # ---------------------------------------------------------------------
    # 16. 1950 census, Apollo, Donna L. Townsend (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "1950 U.S. census, Apollo, Armstrong County, Pennsylvania, Donna L. Townsend in the Howard R. Townsend household",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="Apollo, Armstrong County, Pennsylvania, 5 April 1950; ED 3-3, page 9; DGS 108899509, image 11. FamilySearch ark:/61903/1:1:6XBV-Q6ZK (related personal ark …6XBV-Q6ZL also cited)",
        abbrev="1950 census Apollo Donna L. Townsend",
        private=True,
    )
    urls = [
        url_obj("https://www.familysearch.org/ark:/61903/1:1:6XBV-Q6ZK", "FamilySearch ark 1:1:6XBV-Q6ZK, 1950 census, Apollo, Howard R. Townsend household", private=True),
        url_obj("https://www.familysearch.org/ark:/61903/1:1:6XBV-Q6ZL", "FamilySearch ark 1:1:6XBV-Q6ZL, 1950 census, Donna L. Townsend in the Howard R. Townsend household (related personal ark)", private=True),
    ]
    c = cite(
        s,
        "Apollo, Armstrong County, Pennsylvania, 5 April 1950; ED 3-3, page 9; DGS 108899509, image 11. https://www.familysearch.org/ark:/61903/1:1:6XBV-Q6ZK (household; later citation also uses …6XBV-Q6ZL). Household as abstracted in CINDY_FAMILY_RECORD.md: Howard R Townsend, head, 39, born Indiana; Blanche, wife, 37, born Pa.; Joan E, daughter, born Pa.; Donna L, daughter, 13, born Ohio. No local screenshot of this 1950 schedule was found.",
        VERY_HIGH,
        True,
        [],
        note="URL-only citation from CINDY_FAMILY_RECORD.md. This is a childhood census of a living person and is marked private.",
    )
    add_to(["I0011"], c, urls=urls, events=["Birth"], private=True)

    # ---------------------------------------------------------------------
    # 17. 1950 census, Delta, Colorado, August Beyl (ward, age 13)
    # ---------------------------------------------------------------------
    s = src(
        "1950 U.S. census, Delta, Colorado, August Beyl, age 13, born North Dakota",
        author="U.S. Bureau of the Census; FamilySearch",
        pubinfo="Delta, Colorado; enumeration date 8 May 1950; enumeration district 15-20; line 1, page 73. FamilySearch ark:/61903/1:1:6FM6-PJDX. This is the same-name candidate August Jacob Beyl documented in the tree; it does not by itself prove the parent-child link to Mike.",
        abbrev="1950 census Delta August Beyl",
    )
    m_rec = media(
        "/workspace/genealogy-cindy/beyl_august_1950_census_record_familysearch.png",
        "FamilySearch record page, 1950 U.S. census, August Beyl, age 13, born North Dakota, ward, Delta, Colorado (ark 1:1:6FM6-PJDX)",
    )
    m_row = media(
        "/workspace/genealogy-cindy/beyl_august_1950_census_row_crop.png",
        "1950 U.S. census, Delta, Colorado, cropped line for Beyl, August, ward, age 13, born North Dakota",
    )
    m_sheet = media(
        "/workspace/genealogy-cindy/beyl_august_1950_census_cedaredge_sheet73_ward.png",
        "1950 U.S. census, Delta / Cedaredge, Colorado, sheet 73, including the August Beyl ward line",
    )
    a.link_media_to_source(s, [m_rec, m_row, m_sheet])
    url = url_obj(
        "https://www.familysearch.org/ark:/61903/1:1:6FM6-PJDX",
        "FamilySearch ark 1:1:6FM6-PJDX, 1950 census, August Beyl, Delta, Colorado",
    )
    c = cite(
        s,
        "Delta, Colorado; 8 May 1950; ED 15-20; line 1, page 73. https://www.familysearch.org/ark:/61903/1:1:6FM6-PJDX . August Beyl, male, age 13, born 1937 (estimated), North Dakota, single, relationship: ward. This is a record of the same-name candidate August Jacob Beyl; CINDY_FAMILY_RECORD.md does not treat it as proof that he is Mike’s father.",
        HIGH,
        False,
        [m_rec, m_row, m_sheet],
        note="FamilySearch record page, row crop of the original schedule line, and sheet 73, all cited or saved in connection with the August Jacob Beyl research in CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0012"], c, [m_rec, m_row, m_sheet], urls=[url], events=["Birth"])

    # ---------------------------------------------------------------------
    # 18. Family photograph, Mr. and Mrs. A.J. Beyl (probable Donna)
    # ---------------------------------------------------------------------
    s = src(
        "Family photograph of Mr. and Mrs. A.J. Beyl (family-provided; woman identified as Donna, probable)",
        author="Family photograph provided by Trevor, 15 November 2026",
        pubinfo="Handwritten caption at the bottom: “Mr. & Mrs. A.J. Beyl”. Identification of the woman as Donna and of a mid-1950s marriage is labeled probable in CINDY_FAMILY_RECORD.md; a civil marriage record is still needed.",
        abbrev="Family photo Mr. and Mrs. A.J. Beyl",
        private=True,
    )
    m_full = media(
        "/workspace/genealogy-cindy/beyl_august_donna_uniform_photo_full_family.jpg",
        "Family photograph, captioned “Mr. & Mrs. A.J. Beyl”, mid-1950s uniforms (full frame as provided)",
        private=True,
    )
    m_close = media(
        "/workspace/genealogy-cindy/beyl_august_donna_uniform_photo_closeup_family.jpg",
        "Family photograph, captioned “Mr. & Mrs. A.J. Beyl”, mid-1950s uniforms (close-up)",
        private=True,
    )
    a.link_media_to_source(s, [m_full, m_close], private=True)
    c = cite(
        s,
        "Family photograph provided 15 November 2026; handwritten caption “Mr. & Mrs. A.J. Beyl”. CINDY_FAMILY_RECORD.md: probable identification of the couple as August J. Beyl and Donna, and probable evidence of a mid-1950s marriage; a civil marriage record, and a record naming him as Mike’s father, are still needed.",
        NORMAL,
        True,
        [m_full, m_close],
        note="Original family photograph (full frame and close-up). The living woman is treated as private. This is attached to August and Donna and to their family as probable marriage evidence; it is not used as proof of Mike’s paternity.",
    )
    add_to(["I0012", "I0011"], c, [m_full, m_close], family="F0006", private=True)

    # ---------------------------------------------------------------------
    # 19. Find a Grave, August Jacob Beyl (URL only; no headstone photo)
    # ---------------------------------------------------------------------
    s = src(
        "Find a Grave memorial 266070060, August Jacob Beyl (cremated; no headstone photograph)",
        author="Find a Grave (memorial created 5 March 2024; no family links or biography on the memorial as cited)",
        pubinfo="Memorial 266070060; birth Beulah, Mercer County, North Dakota, 8 April 1937; death Shelbyville, Bedford County, Tennessee, 20 February 2011; remains cremated",
        abbrev="FaG 266070060 August Jacob Beyl",
    )
    url = url_obj(
        "https://www.findagrave.com/memorial/266070060/august-jacob-beyl",
        "Find a Grave memorial 266070060, August Jacob Beyl",
    )
    c = cite(
        s,
        "Memorial 266070060; 8 April 1937, Beulah, Mercer County, North Dakota – 20 February 2011, Shelbyville, Bedford County, Tennessee; cremated; no headstone photograph. https://www.findagrave.com/memorial/266070060/august-jacob-beyl",
        HIGH,
        False,
        [],
        note="URL only. The memorial has no headstone photograph. This is the same-name candidate documented in CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0012"], c, urls=[url], events=["Birth", "Death"])

    # ---------------------------------------------------------------------
    # 20. SortedByName / Death Master File, August J. Beyl (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "SortedByName / Death Master File compilation, August J. Beyl, 1937–2011",
        author="SortedByName compilation of the Social Security Death Master File, as cited in CINDY_FAMILY_RECORD.md",
        pubinfo="https://sortedbyname.com/letter_b/beyl/index_1.html",
        abbrev="SortedByName August J. Beyl",
    )
    url = url_obj(
        "https://sortedbyname.com/letter_b/beyl/index_1.html",
        "SortedByName / Death Master File compilation, August J. Beyl",
    )
    c = cite(
        s,
        "SortedByName compilation: “BEYL, AUGUST J … was born 8 April 1937, and died 20 February 2011.” Also a Bedford County, Tennessee, residence/death line at age 73. https://sortedbyname.com/letter_b/beyl/index_1.html . The Social Security number itself is not recorded in the family record and is not copied here.",
        HIGH,
        False,
        [],
        note="Compiled Death Master File entry as quoted in CINDY_FAMILY_RECORD.md. No original SSA document is on the box.",
    )
    add_to(["I0012"], c, urls=[url], events=["Birth", "Death"])

    # ---------------------------------------------------------------------
    # 21. Doak-Howell Funeral Home notice, August Jacob Beyl, 2011 (URLs only)
    # ---------------------------------------------------------------------
    s = src(
        "Doak-Howell Funeral Home notice, August Jacob Beyl, 2011",
        author="Doak-Howell Funeral Home (Doak Funeral Home and Cremation Services), Shelbyville, Tennessee",
        pubinfo="https://www.doakfh.com/obituaries/August-Jacob-Beyl?obId=47765858 (live page described as a short name-and-dates display behind a bot wall; full text preserved at the Internet Archive capture cited in CINDY_FAMILY_RECORD.md)",
        abbrev="Doak-Howell notice August Jacob Beyl 2011",
    )
    urls = [
        url_obj("https://www.doakfh.com/obituaries/August-Jacob-Beyl?obId=47765858", "Doak-Howell Funeral Home notice, August Jacob Beyl (obId 47765858)"),
        url_obj("https://web.archive.org/web/20260811021749/https://www.doakfh.com/obituaries/August-Jacob-Beyl?obId=47765858", "Internet Archive capture of the Doak-Howell notice for August Jacob Beyl"),
    ]
    c = cite(
        s,
        "Doak-Howell Funeral Home, Shelbyville: https://www.doakfh.com/obituaries/August-Jacob-Beyl?obId=47765858 . Archived full text: https://web.archive.org/web/20260811021749/https://www.doakfh.com/obituaries/August-Jacob-Beyl?obId=47765858 . The notice as quoted in CINDY_FAMILY_RECORD.md gives age 73, of Shelbyville, death early Sunday morning at Heritage Medical Center, cremation, native of Beulah Mercer, N. Dakota, son of the late Jacob Beyl and Helen Mettler. It does not mention Pennsylvania, Townsend, Donna, or Michael.",
        HIGH,
        False,
        [],
        note="URL and archived URL only; no local image of the funeral-home page was saved as an original file.",
    )
    add_to(["I0012"], c, urls=[url for url in urls], events=["Death", "Birth"])

    # ---------------------------------------------------------------------
    # 22. Curran-Shaffer funeral-home obituary, Paul E. Libengood, 2022 (URL only)
    # ---------------------------------------------------------------------
    s = src(
        "Curran-Shaffer Funeral Home obituary, Paul E. Libengood, 2022",
        author="Curran-Shaffer Funeral Home and Crematory, Inc., Apollo, Pennsylvania",
        pubinfo="https://www.curranfuneralhome.com/obituaries/Paul-E-Libengood?obId=23912122 . Identification of this obituary couple with Mike’s legal father and mother is labeled probable / needs confirmation in CINDY_FAMILY_RECORD.md (the obituary does not name a son Michael, Mike, or Floyd).",
        abbrev="Curran-Shaffer obituary Paul E. Libengood 2022",
    )
    url = url_obj(
        "https://www.curranfuneralhome.com/obituaries/Paul-E-Libengood?obId=23912122",
        "Curran-Shaffer Funeral Home obituary, Paul E. Libengood (obId 23912122)",
    )
    c = cite(
        s,
        "https://www.curranfuneralhome.com/obituaries/Paul-E-Libengood?obId=23912122 . As quoted in CINDY_FAMILY_RECORD.md: born 16 March 1934 in Kiski Township, son of the late Everett H. Libengood and Marie L. (Hartman) Libengood; died 2 February 2022; survived by his wife of 57 years, Donna L. (Townsend) Libengood. Identifying this couple as Mike’s legal parents remains probable / needs confirmation because the obituary does not name Michael/Mike/Floyd.",
        NORMAL,
        False,
        [],
        note="URL only; no local image or PDF of the funeral-home page was found. Confidence is normal (probable identification) per CINDY_FAMILY_RECORD.md.",
    )
    add_to(["I0013"], c, urls=[url], events=["Birth", "Death"])
    # The obituary also names Donna L. (Townsend) Libengood as surviving wife — attach to Donna and their marriage family with the same caveat
    add_to(["I0011"], c, urls=[url], family="F0007", family_event_type="Marriage", private=True)


def verify(a: Attacher):
    a.refresh_indexes()
    results = {}
    for gid, p in sorted(a.people.items()):
        p = a.get_person(gid, fresh=True)
        cites = []
        for ch in p.get("citation_list") or []:
            c = a.api.get(f"/api/citations/{ch}?extend=source_handle,media_list")
            src = None
            if c.get("extended") and c["extended"].get("source"):
                src = c["extended"]["source"]
            else:
                src = a.api.get(f"/api/sources/{c['source_handle']}")
            media_descs = []
            for mr in c.get("media_list") or []:
                m = a.api.get(f"/api/media/{mr['ref']}")
                media_descs.append(m.get("desc") or m.get("path"))
            notes = []
            for nh in c.get("note_list") or []:
                n = a.api.get(f"/api/notes/{nh}")
                notes.append((n.get("text") or {}).get("string", "")[:200])
            cites.append({
                "citation": ch,
                "page": c.get("page"),
                "source_title": src.get("title") if src else None,
                "media": media_descs,
                "has_url_in_page": "http" in (c.get("page") or ""),
                "notes_have_url": any("http" in x for x in notes),
                "private": c.get("private"),
                "confidence": c.get("confidence"),
            })
        person_media = []
        for mr in p.get("media_list") or []:
            m = a.api.get(f"/api/media/{mr['ref']}")
            person_media.append({"desc": m.get("desc"), "private": m.get("private"), "mime": m.get("mime")})
        results[gid] = {
            "name": " ".join(filter(None, [
                (p.get("primary_name") or {}).get("first_name"),
                " ".join(s.get("surname", "") for s in (p.get("primary_name") or {}).get("surname_list") or []),
            ])),
            "private": p.get("private"),
            "n_citations": len(cites),
            "n_media": len(person_media),
            "n_urls": len(p.get("urls") or []),
            "urls": [{"desc": u.get("desc"), "path": u.get("path"), "private": u.get("private")} for u in p.get("urls") or []],
            "citations": cites,
            "media": person_media,
        }
        # check resolve
        bad = [c for c in cites if not c["source_title"] or (not c["media"] and not c["has_url_in_page"])]
        status = "OK" if cites and not bad else ("NO_SOURCES" if not cites else "SOME_UNRESOLVED")
        print(f"VERIFY {gid} {results[gid]['name']}: cites={len(cites)} media={len(person_media)} urls={len(p.get('urls') or [])} {status}")
        for c in cites:
            flag = "ok" if c["source_title"] and (c["media"] or c["has_url_in_page"]) else "UNRESOLVED"
            print(f"    [{flag}] {c['source_title']}")
            print(f"         media={len(c['media'])} url_in_page={c['has_url_in_page']} priv={c['private']}")
    REPORT.write_text(json.dumps(results, indent=2))
    print("WROTE", REPORT)
    return results


def main():
    api = Api(get_token())
    a = Attacher(api)
    a.refresh_indexes()
    print("baseline sources", len(a.sources_by_title), "media", len(a.media_by_desc), "people", len(a.people))
    attach_all(a)
    print("---- VERIFY ----")
    verify(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

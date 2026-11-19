"""Media audit overrides shared by the build scripts.

Source of truth: /workspace/gramps-web/shared/audit_mismatches.csv (written by the
18 Nov 2026 media audit; append rows to extend it). Keyed by the original file path:
  action=unlinked        wrong_person must not get this file
  action=relinked        wrong_person (optional) loses it, right_person gets it
  action=blocked-create  wrong_person "new:<name>": never create/match the manifest's
                         new-person dict for this file
Optional corrected_title / corrected_date / corrected_url replace the manifest values;
drop_supports=yes stops fact-level citations (birth/death/marriage/parent) that the
audit found the file does not support.
"""
from __future__ import annotations

import copy
import csv
import os
from pathlib import Path

CSV = Path(os.environ.get("GRAMPSWEB_AUDIT_CSV", "/workspace/gramps-web/shared/audit_mismatches.csv"))


def load() -> dict:
    out: dict[str, dict] = {}
    if not CSV.exists():
        return out
    with CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = os.path.realpath(row["file"])
            o = out.setdefault(key, {"drop": set(), "add": [], "block_new": False, "title": "",
                                     "date": "", "url": "", "drop_supports": False, "media_id": row.get("media_id", "")})
            act = (row.get("action") or "").strip()
            wp = (row.get("wrong_person") or "").strip()
            rp = (row.get("right_person") or "").strip()
            if act == "blocked-create" or wp.startswith("new:"):
                o["block_new"] = True
            elif wp:
                o["drop"].add(wp)
            if act == "relinked" and rp and rp not in o["add"]:
                o["add"].append(rp)
            for k, col in (("title", "corrected_title"), ("date", "corrected_date"), ("url", "corrected_url")):
                if row.get(col):
                    o[k] = row[col]
            if (row.get("drop_supports") or "").lower() in ("yes", "true", "1"):
                o["drop_supports"] = True
    return out


def for_path(overrides: dict, path: str | None):
    if not path:
        return None
    return overrides.get(os.path.realpath(path))


def rewrite_entry(entry: dict, overrides: dict):
    """Return (entry, None) possibly rewritten, or (None, reason) when the audit removed
    every person this file was attached to."""
    if (entry.get("kind") or "").lower() != "file":
        return entry, None
    o = for_path(overrides, entry.get("path"))
    if not o:
        return entry, None
    if entry.get("replaces"):
        # The bot has rewritten this entry after the audit (it agreed with the rows and
        # fixed people, titles, dates and URLs itself). Its version wins; the audit only
        # keeps guarding against re-linking the people it found wrong.
        o = {"drop": o["drop"] - set(o["add"]), "add": [], "block_new": False, "title": "",
             "date": "", "url": "", "drop_supports": False}
    e = copy.deepcopy(entry)
    person = e.get("person")
    targets = []
    if isinstance(person, dict):
        if not o["block_new"]:
            targets.append(person)
    elif person and person not in o["drop"]:
        targets.append(person)
    for g in e.get("also_for") or []:
        if g not in o["drop"] and g not in targets:
            targets.append(g)
    for g in o["add"]:
        if g not in targets:
            targets.append(g)
    if not targets:
        return None, "removed by media audit (shared/audit_mismatches.csv)"
    e["person"] = targets[0]
    e["also_for"] = [t for t in targets[1:] if isinstance(t, str)]
    if o["title"]:
        e["title"] = o["title"]
    if o["date"]:
        e["date"] = o["date"]
    if o["url"]:
        e["url"] = o["url"]
    sup = e.get("supports") or []
    if o["add"]:
        sup = []  # relinked: attach to the people only, assert no facts
    else:
        sup = [s for s in sup if s.split(":", 1)[-1].strip() not in o["drop"]]
    if o["drop_supports"] and (o["title"] or o["add"]):
        sup = []
    e["supports"] = sup
    e["_audit_override"] = True
    return e, None


def person_blocked(overrides: dict, path: str, gid: str) -> bool:
    o = for_path(overrides, path)
    return bool(o and gid in o["drop"] and gid not in o["add"])

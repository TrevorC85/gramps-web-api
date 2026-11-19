#!/usr/bin/env python3
"""Seed website_manifest.jsonl on both sides from attachments already in the tree.

Engineering ids are prefixed eng-YYYY-MM-DD-NNN. Existing eng- seed lines are
replaced in place (the files stay append-only for the research bots; this
rewriter only touches the engineering seed block between markers).
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

GEN = Path("/workspace/genealogy/website_manifest.jsonl")
WIFE = Path("/workspace/genealogy-cindy/website_manifest.jsonl")
ATTACH_PY = Path("/workspace/gramps-web/scripts/attach_originals.py")
ALLOW = Path("/workspace/gramps-web/build/attach_records.py")
BEGIN = "# --- eng-seed begin (written by the Gramps Web build; do not edit by hand) ---"
END = "# --- eng-seed end ---"
TODAY = "2026-11-18"


def parse_attach_originals():
    text = ATTACH_PY.read_text()
    start = text.index("def attach_all")
    end = text.index("def verify")
    body = text[start:end]
    parts = re.split(r"\n    # -{10,}\n    # ([^\n]+)\n    # -{10,}", body)
    blocks = []
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        chunk = parts[i + 1] if i + 1 < len(parts) else ""
        srcs = re.findall(r'src\(\s*"([^"]+)"', chunk)
        paths = re.findall(r'media\(\s*"([^"]+)"\s*,\s*"([^"]+)"', chunk)
        urls = re.findall(r'url_obj\(\s*"([^"]+)"\s*,\s*"([^"]+)"', chunk)
        gids = []
        for m in re.findall(r"add_to\(\s*(\[[^\]]+\])", chunk):
            gids.extend(re.findall(r'"(I\d+)"', m))
        # unique, keep order
        seen = set()
        uniq = []
        for g in gids:
            if g not in seen:
                seen.add(g)
                uniq.append(g)
        priv = bool(re.search(r"private\s*=\s*True", chunk))
        conf = "confirmed (record)"
        if re.search(r"unconfirmed|not confirmed|not yet confirmed", header + chunk, re.I):
            conf = "needs confirmation"
        elif re.search(r"probable", header + chunk, re.I):
            conf = "probable"
        elif re.search(r"index", header + chunk, re.I) and "certificate" not in header.lower():
            conf = "confirmed (index)"
        supports = []
        hl = header.lower()
        if "birth" in hl:
            supports.append("birth")
        if "death" in hl or "obituar" in hl:
            supports.append("death")
        if "headstone" in hl or "grave" in hl or "burial" in hl or "cemetery" in hl:
            supports.append("burial")
        if "marriage" in hl or "wedding" in hl:
            if len(uniq) >= 2:
                supports.append(f"marriage:{uniq[1]}")
            else:
                supports.append("other")
        if "divorce" in hl:
            supports.append("other")
        if "census" in hl:
            supports.append("residence")
        if not supports:
            supports = ["other"]
        blocks.append({
            "header": header, "srcs": srcs, "paths": paths, "urls": urls,
            "gids": uniq, "private": priv, "confidence": conf, "supports": supports,
        })
    return blocks


def parse_allowlist():
    """attach_records.py ALLOWLIST tuples."""
    text = ALLOW.read_text() if ALLOW.exists() else ""
    # ("canon", "/path", "title", "desc")
    items = re.findall(
        r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
        text,
    )
    return items


def side_for(paths, gids):
    if any("/genealogy-cindy/" in p for p, *_ in ([(x[0],) for x in paths] if paths and isinstance(paths[0], tuple) else [])):
        return "wife"
    # path list may be tuples
    for item in paths:
        p = item[0] if isinstance(item, tuple) else item
        if "/genealogy-cindy/" in p:
            return "wife"
        if "/genealogy/" in p and "/genealogy-cindy/" not in p:
            return "gen"
    # URL-only: cindy people I0008-I0015 plus later cindy I09xx are wife;
    # Trevor I0001 2010 marriage stays on gen (primary of the pair in this seed).
    cindyish = {"I0008", "I0009", "I0010", "I0011", "I0012", "I0013", "I0014", "I0015"}
    if gids and all(g in cindyish or g.startswith("I09") or g.startswith("I08") for g in gids if g != "I0001"):
        if "I0001" in gids:
            return "gen"
        return "wife"
    return "gen"


def source_repo(title, url=""):
    blob = (title + " " + url).lower()
    if "familysearch" in blob:
        return "FamilySearch"
    if "find a grave" in blob or "findagrave" in blob:
        return "Find a Grave"
    if "digitalarchives.wa.gov" in blob or "washington" in blob and "digital" in blob:
        return "Washington State Digital Archives"
    if "legacy.com" in blob:
        return "Legacy.com"
    if "newspapers.com" in blob or "morning union" in blob:
        return "Newspapers.com / The Morning Union"
    if "sortedbyname" in blob:
        return "SortedByName"
    if "doakfh" in blob:
        return "Doak-Howell Funeral Home"
    if "curranfuneralhome" in blob:
        return "Curran-Shaffer Funeral Home"
    return ""


def entries_from_blocks(blocks):
    out_gen, out_wife = [], []
    n = 0
    for b in blocks:
        gids = b["gids"]
        if not gids:
            continue
        primary, also = gids[0], gids[1:]
        src_title = (b["srcs"][0] if b["srcs"] else b["header"])
        # one entry per file (url may ride along on the first file)
        urls = list(b["urls"])
        if b["paths"]:
            for i, (path, desc) in enumerate(b["paths"]):
                n += 1
                eid = f"eng-{TODAY}-{n:03d}"
                url = urls[0][0] if (i == 0 and urls) else (urls[i][0] if i < len(urls) else "")
                rec = {
                    "id": eid,
                    "person": primary,
                    "kind": "file",
                    "path": path,
                    "title": desc or src_title,
                    "source": source_repo(src_title, url) or source_repo(desc, url),
                    "confidence": b["confidence"],
                    "supports": b["supports"],
                    "private": b["private"],
                }
                if url:
                    rec["url"] = url
                if also:
                    rec["also_for"] = also
                (out_wife if side_for(b["paths"], gids) == "wife" else out_gen).append(rec)
            # leftover URLs not paired with a file
            for j, (url, udesc) in enumerate(urls):
                if j < len(b["paths"]) and j == 0:
                    continue
                if j < len(b["paths"]):
                    continue
                n += 1
                eid = f"eng-{TODAY}-{n:03d}"
                rec = {
                    "id": eid,
                    "person": primary,
                    "kind": "url",
                    "url": url,
                    "title": udesc or src_title,
                    "source": source_repo(udesc, url) or source_repo(src_title, url),
                    "confidence": b["confidence"],
                    "supports": b["supports"],
                    "private": b["private"],
                }
                if also:
                    rec["also_for"] = also
                (out_wife if side_for(b["paths"], gids) == "wife" else out_gen).append(rec)
        else:
            for url, udesc in urls:
                n += 1
                eid = f"eng-{TODAY}-{n:03d}"
                rec = {
                    "id": eid,
                    "person": primary,
                    "kind": "url",
                    "url": url,
                    "title": udesc or src_title,
                    "source": source_repo(udesc, url) or source_repo(src_title, url),
                    "confidence": b["confidence"],
                    "supports": b["supports"],
                    "private": b["private"],
                }
                if also:
                    rec["also_for"] = also
                (out_wife if side_for([], gids) == "wife" else out_gen).append(rec)
    return out_gen, out_wife, n


# canon from attach_records allowlist -> gramps_id (from handle map / known seeds)
CANON_TO_GID = {
    "collins:EC": "I0768",
    "judy:JL": "I0758",
    "judy:B": "I0230",
    "cindy:johnjr": "I0014",
}


def entries_from_allowlist(start_n):
    extra = []
    n = start_n
    for canon, path, title, desc in parse_allowlist():
        gid = CANON_TO_GID.get(canon)
        if not gid:
            continue
        if not Path(path).exists():
            continue
        n += 1
        rec = {
            "id": f"eng-{TODAY}-{n:03d}",
            "person": gid,
            "kind": "file",
            "path": path,
            "title": title,
            "source": source_repo(title, path),
            "confidence": "confirmed (record)",
            "supports": (
                ["burial"] if "headstone" in title.lower() or "grave" in title.lower()
                else ["residence"] if "census" in title.lower()
                else ["birth"] if "baptis" in title.lower()
                else ["other"]
            ),
            "private": False,
        }
        extra.append((gid, rec))
    gen, wife = [], []
    for gid, rec in extra:
        (wife if gid.startswith("I00") and gid >= "I0008" and gid <= "I0015" else gen).append(rec)
    return gen, wife, n


def write_seed(path: Path, entries: list[dict]):
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    # keep any lines outside the eng-seed block (bot-appended research entries)
    if BEGIN in existing and END in existing:
        before = existing[: existing.index(BEGIN)]
        after = existing[existing.index(END) + len(END):]
        after = after.lstrip("\n")
    else:
        before, after = existing, ""
        if before and not before.endswith("\n"):
            before += "\n"
    block = BEGIN + "\n"
    block += f"# Seeded {TODAY} from attachments already in the local Gramps Web tree.\n"
    block += "# Re-run seed_manifests.py / run.py to refresh this block only.\n"
    for rec in entries:
        block += json.dumps(rec, ensure_ascii=False, separators=(",", ": ")) + "\n"
    block += END + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before + block + after, encoding="utf-8")
    path.chmod(0o644)


def main():
    blocks = parse_attach_originals()
    gen, wife, n = entries_from_blocks(blocks)
    g2, w2, n = entries_from_allowlist(n)
    gen.extend(g2)
    wife.extend(w2)
    # Confirmed marriages already attached (so an AWS rebuild from manifests
    # can recreate the families without a second manual pass).
    spouses = [
        ("gen", {
            "person": "I0001", "kind": "spouse",
            "spouse": {"gramps_id": "I0008", "date": "2010", "place": "King County, Washington", "status": "married"},
            "title": "Marriage of Trevor and Cindy, 2010, King County, Washington",
            "confidence": "confirmed (record)", "private": True,
            "also_for": ["I0008"],
        }),
        ("gen", {
            "person": "I0006", "kind": "spouse",
            "spouse": {"gramps_id": "I0007", "date": "1953", "place": "Massachusetts (Morning Union announced 4 March 1953)", "status": "married"},
            "title": "Marriage of Kenneth Earl Leger and Marge, announced 4 March 1953",
            "confidence": "confirmed (record)", "private": True,
            "also_for": ["I0007"],
        }),
        ("wife", {
            "person": "I0009", "kind": "spouse",
            "spouse": {"gramps_id": "I0010", "date": "1988-03-17", "place": "King County, Washington", "status": "divorced"},
            "title": "Marriage of Mike and Judy, 17 March 1988, King County; divorced 16 March 1995",
            "confidence": "confirmed (record)", "private": True,
            "also_for": ["I0010"],
        }),
        ("wife", {
            "person": "I0014", "kind": "spouse",
            "spouse": {"gramps_id": "I0015", "date": "1948-03-06", "place": "King County, Washington", "status": "married"},
            "title": "Marriage of John Donald Schneider Jr. and Mary Margaret Fletcher, 6 March 1948",
            "confidence": "confirmed (record)", "private": False,
            "also_for": ["I0015"],
        }),
    ]
    for side, rec in spouses:
        n += 1
        rec = dict(rec)
        rec["id"] = f"eng-{TODAY}-{n:03d}"
        (wife if side == "wife" else gen).append(rec)
    write_seed(GEN, gen)
    write_seed(WIFE, wife)
    print(f"Seeded {GEN} ({len(gen)} entries) and {WIFE} ({len(wife)} entries); last id n={n}")


if __name__ == "__main__":
    main()

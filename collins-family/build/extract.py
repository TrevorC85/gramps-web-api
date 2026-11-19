#!/usr/bin/env python3
"""Extract people, parent-child edges, and chart facts from the Genealogy and
Genealogy Wife source files into build/data/extracted.json.

Does not invent people or facts. Chart display lines are kept verbatim.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "extracted.json"
GEN = Path("/workspace/genealogy")
CINDY = Path("/workspace/genealogy-cindy")


def _exec_until(src: str, cut_markers: list[str]) -> dict:
    cut = len(src)
    for m in cut_markers:
        i = src.find(m)
        if i != -1:
            cut = min(cut, i)
    ns: dict = {"__name__": "extract_data"}
    exec(src[:cut], ns)  # noqa: S102 — local trusted chart scripts
    return ns


def extract_judy() -> dict:
    src = (GEN / "make_judy_tree_r18.py").read_text()
    ns = _exec_until(src, ["\ndef height", "\nLCOL ="])
    names = ns["names"]
    parents = ns["parents"]
    det = ns.get("det") or {}
    notes = ns.get("notes") or {}
    people = []
    for key, name in names.items():
        people.append({
            "source": "judy",
            "key": f"judy:{key}",
            "src_id": str(key),
            "name": name,
            "lines": list(det.get(key) or []),
            "chart_note": notes.get(key) or "",
            "side": "judy",
        })
    edges = []
    for child, plist in parents.items():
        if f"judy:{child}" not in {p["key"] for p in people} and child not in names:
            # parent-listed child missing a name entry — skip but record
            continue
        for parent, conf, basis in plist:
            if parent not in names:
                continue
            edges.append({
                "child": f"judy:{child}",
                "parent": f"judy:{parent}",
                "confidence": conf,
                "basis": basis,
                "source": "judy",
            })
    return {"people": people, "edges": edges}


def extract_collins() -> dict:
    src = (GEN / "make_collins_chart.py").read_text()
    # Include FAMS, stop before layout that needs `pages`
    ns = _exec_until(src, ["\nFAM_LIMIT", "\nfam_pages", "\n# ---------------------------------------------------------------- drawing"])
    names = ns["names"]
    parents = ns["parents"]
    det = ns.get("det") or {}
    fams = ns.get("FAMS") or []
    people = []
    seen = set()
    for key, name in names.items():
        people.append({
            "source": "collins",
            "key": f"collins:{key}",
            "src_id": str(key),
            "name": name,
            "lines": list(det.get(key) or []),
            "chart_note": "",
            "side": "collins",
        })
        seen.add(name.strip().lower())

    edges = []
    for child, plist in parents.items():
        for parent, conf, basis in plist:
            if parent not in names or child not in names:
                continue
            edges.append({
                "child": f"collins:{child}",
                "parent": f"collins:{parent}",
                "confidence": conf,
                "basis": basis,
                "source": "collins",
            })

    # Collateral people from family-group pages
    def ensure_person(display: str, lines: list[str], extra_id: str) -> str:
        # reuse pedigree person if the display name matches
        for p in people:
            if p["name"].strip().lower() == display.strip().lower():
                # merge extra lines
                for ln in lines:
                    if ln not in p["lines"]:
                        p["lines"].append(ln)
                return p["key"]
        slug = "fam_" + extra_id
        key = f"collins:{slug}"
        people.append({
            "source": "collins",
            "key": key,
            "src_id": slug,
            "name": display,
            "lines": list(lines or []),
            "chart_note": "collateral (family-group page)",
            "side": "collins",
        })
        return key

    for fi, fam in enumerate(fams):
        # (title, father_tuple, mother_tuple, marriage_str, children)
        if not isinstance(fam, (list, tuple)) or len(fam) < 5:
            continue
        title, fat, mot, marr, children = fam[0], fam[1], fam[2], fam[3], fam[4]
        fk = mk = None
        if isinstance(fat, tuple) and fat:
            fk = ensure_person(fat[0], fat[1] if len(fat) > 1 else [], f"f{fi}")
        if isinstance(mot, tuple) and mot:
            mk = ensure_person(mot[0], mot[1] if len(mot) > 1 else [], f"m{fi}")
        # marriage fact on both
        if isinstance(marr, str) and marr.strip() and "not found" not in marr.lower():
            for k in (fk, mk):
                if not k:
                    continue
                for p in people:
                    if p["key"] == k and marr not in p["lines"]:
                        p["lines"].append(marr)
        for ci, ch in enumerate(children or []):
            if not isinstance(ch, (list, tuple)) or not ch:
                continue
            cname = ch[0]
            clines = ch[1] if len(ch) > 1 else []
            conf = ch[2] if len(ch) > 2 else "confirmed"
            basis = ch[3] if len(ch) > 3 else ""
            ck = ensure_person(cname, clines, f"c{fi}_{ci}")
            if fk:
                edges.append({
                    "child": ck, "parent": fk,
                    "confidence": conf, "basis": basis,
                    "source": "collins_fams",
                })
            if mk:
                edges.append({
                    "child": ck, "parent": mk,
                    "confidence": conf, "basis": basis,
                    "source": "collins_fams",
                })
    return {"people": people, "edges": edges, "fams": len(fams)}


def extract_koop() -> dict:
    src = (GEN / "make_koop_heesemann_chart.py").read_text()
    # The N = { ... } dict is the data. Exec until W,H= assignment after N.
    ns = _exec_until(src, ["\nW,H=", "\nW, H ="])
    N = ns["N"]
    label_conf = {
        "C": ("confirmed", "record (Gimpel/Koop chart)"),
        "PS": ("probable (strong)", "Gimpel/Koop chart"),
        "P": ("probable", "Gimpel/Koop chart"),
        "NC": ("needs confirmation", "Gimpel/Koop chart"),
    }
    people = []
    edges = []
    for key, (lines, lab, pars) in N.items():
        name = lines[0]
        rest = lines[1:]
        people.append({
            "source": "koop",
            "key": f"koop:{key}",
            "src_id": key,
            "name": name,
            "lines": rest,
            "chart_note": "",
            "side": "koop",
            "box_label": lab,
        })
        conf, basis = label_conf.get(lab, ("needs confirmation", "Gimpel/Koop chart"))
        for p in pars:
            if not p:
                continue
            edges.append({
                "child": f"koop:{key}",
                "parent": f"koop:{p}",
                "confidence": conf,
                "basis": basis,
                "source": "koop",
            })
    return {"people": people, "edges": edges}


def extract_cindy() -> dict:
    src = (CINDY / "make_tree.py").read_text()
    tree = ast.parse(src)
    boxes: dict[str, dict] = {}

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "box":
                try:
                    args = [ast.literal_eval(a) for a in node.args]
                    kw = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
                    key = args[0]
                    name = args[3]
                    lines = args[4]
                    kind = args[5] if len(args) > 5 else kw.get("kind", "conf")
                    # page-2 cindy box is the same person; keep first, merge lines
                    if key in boxes:
                        for ln in lines:
                            if ln not in boxes[key]["lines"]:
                                boxes[key]["lines"].append(ln)
                    else:
                        boxes[key] = {
                            "source": "cindy",
                            "key": f"cindy:{key}",
                            "src_id": key,
                            "name": name,
                            "lines": list(lines),
                            "chart_note": "",
                            "side": "cindy",
                            "kind": kind,
                        }
                except Exception:
                    pass
            self.generic_visit(node)

    V().visit(tree)

    # Link lists are `for item in [ (...), ... ]: link(*item)`
    edges = []
    style_map = {
        "conf": ("confirmed", "record or as tagged on the v64 chart"),
        "strong": ("probable (strong)", "v64 chart"),
        "prob": ("probable", "v64 chart"),
        "need": ("needs confirmation", "v64 chart"),
        "fam": ("needs confirmation", "family knowledge (needs a record)"),
        "adopt": ("confirmed", "legal / adoptive parent (1988 certificate)"),
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # for item in [ ... ]:
        it = node.iter
        if not isinstance(it, ast.List):
            continue
        for elt in it.elts:
            if not isinstance(elt, ast.Tuple) or len(elt.elts) < 2:
                continue
            try:
                vals = [ast.literal_eval(e) for e in elt.elts]
            except Exception:
                continue
            if not (isinstance(vals[0], str) and isinstance(vals[1], str)):
                continue
            child, parent = vals[0], vals[1]
            style = vals[2] if len(vals) > 2 and isinstance(vals[2], str) else "conf"
            if child not in boxes or parent not in boxes:
                continue
            conf, basis = style_map.get(style, (style, "v64 chart"))
            # Genealogy Wife rulings (18 Nov 2026) supersede the chart style
            # for these specific links.
            pair = (child, parent)
            if pair in (("cindy", "mike"), ("cindy", "judy")):
                conf, basis = ("confirmed (family)",
                               "Genealogy Wife ruling 18 Nov 2026: Cindy's links to Mike and Judy are confirmed (family)")
            elif pair == ("mike", "august"):
                conf, basis = ("probable",
                               "Genealogy Wife ruling 18 Nov 2026: August J. Beyl is Mike's biological father; probable (family knowledge, not proven)")
            elif pair == ("mike", "paul"):
                conf, basis = ("confirmed (record)",
                               "Genealogy Wife ruling 18 Nov 2026: Paul E. Libengood is Mike's adoptive father (1988 certificate)")
            edges.append({
                "child": f"cindy:{child}",
                "parent": f"cindy:{parent}",
                "confidence": conf,
                "basis": basis,
                "style": style,
                "source": "cindy",
                "relation": (
                    "adoptive" if pair == ("mike", "paul") else
                    "biological"
                ),
            })

    return {"people": list(boxes.values()), "edges": edges}


def main() -> int:
    judy = extract_judy()
    collins = extract_collins()
    koop = extract_koop()
    cindy = extract_cindy()
    out = {
        "judy": {"n_people": len(judy["people"]), "n_edges": len(judy["edges"]), **judy},
        "collins": {"n_people": len(collins["people"]), "n_edges": len(collins["edges"]), **collins},
        "koop": {"n_people": len(koop["people"]), "n_edges": len(koop["edges"]), **koop},
        "cindy": {"n_people": len(cindy["people"]), "n_edges": len(cindy["edges"]), **cindy},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT}")
    for side in ("judy", "collins", "koop", "cindy"):
        print(f"  {side}: {out[side]['n_people']} people, {out[side]['n_edges']} parent-child edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

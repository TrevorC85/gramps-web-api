#!/usr/bin/env python3
"""Drop top-level GEDCOM records (NOTE/SOUR/OBJE/REPO) that no remaining INDI/FAM reaches.
Gramps' private/living proxies drop excluded people but still write their stand-alone notes,
sources and media; this removes those orphans so a reviewed export carries nothing about them."""
import re, sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src, encoding="utf-8").read().split("\n")
recs, cur = [], None
for ln in lines:
    if ln.startswith("0 "):
        cur = [ln]; recs.append(cur)
    elif cur is not None:
        cur.append(ln)
def xref(r):
    m = re.match(r"0 (@[^@]+@) (\w+)", r[0]); return (m.group(1), m.group(2)) if m else (None, None)
byid = {xref(r)[0]: r for r in recs if xref(r)[0]}
ptr = re.compile(r"^\d+ \w+ (@[^@]+@)\s*$")
keep = set(); todo = [x for x, r in byid.items() if xref(r)[1] in ("INDI", "FAM", "SUBM")]
while todo:
    x = todo.pop()
    if x in keep or x not in byid: continue
    keep.add(x)
    for ln in byid[x][1:]:
        m = ptr.match(ln)
        if m: todo.append(m.group(1))
out, dropped = [], {}
for r in recs:
    x, t = xref(r)
    if x and x not in keep:
        dropped[t] = dropped.get(t, 0) + 1; continue
    out.extend(r)
open(dst, "w", encoding="utf-8").write("\n".join(out))
print("dropped", dropped)

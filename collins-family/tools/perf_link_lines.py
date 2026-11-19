"""Performance check of the link-lines API on a local instance (read only).

  python3 perf_link_lines.py BASE_URL CREDENTIALS_JSON USER ROOT_ID [ROOT_ID...]

Logs in as USER (password from the JSON file, never printed) and times, per
root person: the people request of the ancestor chart with the stock rule
and with IsLessThanNthGenerationAncestorOfAllFamilies (4 and 30
generations), and GET /api/childref-confidence/ in person mode; then all
families in batches of 250. Prints a JSON report.
"""

import json
import statistics
import sys
import time
import urllib.parse
import urllib.request

base, cred_file, user, *roots = sys.argv[1:]
assert base.startswith("http://127.0.0.1:"), "local instances only"
password = json.load(open(cred_file))[user]


def call(method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    start = time.perf_counter()
    with urllib.request.urlopen(req, data, timeout=300) as resp:
        payload = resp.read()
    return time.perf_counter() - start, json.loads(payload)


_, tok = call("POST", "/api/token/", {"username": user, "password": password})
token = tok["access_token"]


def timed(path, n=3):
    times, result = [], None
    for _ in range(n):
        t, result = call("GET", path, token=token)
        times.append(t)
    # first call (cold: no request cache) and median of the repeats
    return {"first_ms": round(times[0] * 1000), "median_ms": round(statistics.median(times) * 1000)}, result


report = {}
extend = "event_ref_list,primary_parent_family,family_list,parent_family_list"
for root in roots:
    entry = {}
    for rule, gens in (
        ("IsLessThanNthGenerationAncestorOf", 4),
        ("IsLessThanNthGenerationAncestorOfAllFamilies", 4),
        ("IsLessThanNthGenerationAncestorOfAllFamilies", 30),
    ):
        rules = json.dumps({"rules": [{"name": rule, "values": [root, gens]}]})
        ms, people = timed(
            f"/api/people/?rules={urllib.parse.quote(rules)}&profile=self&extend={extend}"
        )
        entry[f"people {rule} {gens}"] = {**ms, "people": len(people)}
    for gens in (4, 30):
        ms, links = timed(f"/api/childref-confidence/?person={root}&generations={gens}")
        entry[f"confidence person mode {gens}"] = {**ms, "links": len(links)}
    report[root] = entry

_, families = call("GET", "/api/families/?keys=handle", token=token)
handles = [f["handle"] for f in families]
start = time.perf_counter()
links = 0
for i in range(0, len(handles), 250):
    _, batch = call(
        "GET", "/api/childref-confidence/?families=" + ",".join(handles[i : i + 250]), token=token
    )
    links += len(batch)
report["all families"] = {
    "families": len(handles),
    "links": links,
    "requests": (len(handles) + 249) // 250,
    "ms": round((time.perf_counter() - start) * 1000),
}
print(json.dumps(report, indent=1))

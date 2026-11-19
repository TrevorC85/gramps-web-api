#!/usr/bin/env python3
"""Full-tree build entry point. Idempotent. Re-run after the genealogy bots add people
or append website_manifest.jsonl entries.

  python3 /workspace/gramps-web/build/run.py
  python3 /workspace/gramps-web/build/run.py --skip-extract
  python3 /workspace/gramps-web/build/run.py --screenshots

Requires Gramps Web at $GRAMPSWEB_URL (default http://127.0.0.1:5055).
See /workspace/gramps-web/build/README.md. Does not deploy to AWS.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def run(script: str):
    path = ROOT / script
    print(f"\n=== {script} ===", flush=True)
    subprocess.check_call([PY, "-u", str(path)], cwd=str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-extract", action="store_true",
                    help="Reuse existing data/extracted.json")
    ap.add_argument("--skip-load", action="store_true",
                    help="Reuse the current Gramps tree; attach/index/verify only")
    ap.add_argument("--screenshots", action="store_true",
                    help="Capture /workspace/gramps-web/screens/full_*.png (needs Playwright)")
    args = ap.parse_args()
    t0 = time.time()
    failed = ROOT / "data" / "failed_saves.jsonl"
    failed.unlink(missing_ok=True)  # round 5: saves that still fail after retries land here
    (ROOT / "data" / "save_stats.jsonl").unlink(missing_ok=True)

    if not args.skip_extract:
        run("extract.py")
        run("graph.py")
    if not args.skip_load:
        run("load.py")
        run("repair_families.py")
        run("repair_events.py")
        run("apply_cindy_rulings.py")
        # Publish the person index as soon as people exist, so other bots can use it
        # while later attach/marriage steps are still running.
        run("people_index.py")
    run("seed_manifests.py")
    run("attach_records.py")
    run("attach_manifest.py")      # uploads >10 MiB files as web-ready copies (media_prep.py)
    run("dedupe_media.py")         # one media object per identical file
    run("apply_media_audit.py")    # keeps shared/audit_mismatches.csv fixes in force
    run("ensure_marriages.py")
    run("record_links.py")         # one "Record links" note per person
    run("prune_orphans.py")        # no ownerless events left behind
    run("people_index.py")
    run("verify.py")
    # Round 5: rerun, once, every step that had a save fail after its retries (steps are idempotent)
    if failed.exists():
        steps = list(dict.fromkeys(json.loads(l)["script"] for l in failed.read_text().splitlines() if l.strip()))
        print(f"\n=== rerunning steps with failed saves: {steps} ===", flush=True)
        failed.rename(failed.with_suffix(".first_pass.jsonl"))
        for step in steps:
            if (ROOT / step).exists():
                run(step)
        run("verify.py")
        if failed.exists():
            print("STILL FAILING after rerun:", failed.read_text(), flush=True)
    run("export_gedcom.py")
    if args.screenshots:
        run("screenshots.py")
    stats = {}
    sf = ROOT / "data" / "save_stats.jsonl"
    if sf.exists():
        for l in sf.read_text().splitlines():
            for k, v in json.loads(l).items():
                if isinstance(v, int):
                    stats[k] = stats.get(k, 0) + v
    print(f"\nSaves: {stats}")
    print(f"Build time: {(time.time() - t0) / 60:.1f} min")
    print("\nBuild finished. See data/verify_report.json, shared/people_index.csv, and README.md.")


if __name__ == "__main__":
    main()

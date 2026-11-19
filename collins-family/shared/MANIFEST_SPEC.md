# Website attachment manifest (shared by Genealogy, Genealogy Wife, Engineering)

Goal: record once, at research time, which person each image/file/link/note belongs to.
The website build (`/workspace/gramps-web/build/run.py`) reads these manifests and attaches
everything to Gramps Web, locally now and on AWS later. Nobody re-attaches by hand.

## Files
- Trevor's side (Genealogy bot):        /workspace/genealogy/website_manifest.jsonl
- Cindy's side (Genealogy Wife bot):    /workspace/genealogy-cindy/website_manifest.jsonl
- Person lookup (written by the build): /workspace/gramps-web/shared/people_index.csv
  columns: gramps_id, side, name, birth, death, parents, spouses, living

One JSON object per line, append-only. To correct an entry, append a new one with the same
`id` and `"replaces": true`; to drop one, append `{"id": ..., "delete": true}`.

## Entry fields
- id          (required) unique, e.g. "gen-2026-11-18-001" / "wife-2026-11-18-001"
- person      (required) gramps_id from people_index.csv (e.g. "I0768"). If the person is
              new and has no id yet, use {"name": "...", "birth": "...", "parents": ["..."]}
              and the build will match or create it.
- kind        (required) "file" | "url" | "note" | "spouse"
- path        for kind=file: absolute path on the box (scan, photo, PDF)
- url         for kind=url (or alongside a file, the page it came from)
- title       proper record title, e.g. "1860 U.S. census, Needham, MA, Eli Collins household"
- text        for kind=note: the note text (markdown ok)
- source      source/repository name (FamilySearch, Find a Grave, newspaper, family)
- date        record date if known
- supports    list of facts it proves: "birth", "death", "burial", "marriage:<gramps_id>",
              "parent:<gramps_id>", "residence", "occupation", "other"
- confidence  "confirmed (record)" | "confirmed (index)" | "confirmed (family)" | "probable (strong)" | "probable" | "needs confirmation" | "possible"
- relation    for parent links: "biological" | "adoptive" | "step" | "foster" | "legal" (default biological)
- also_for    optional list of other gramps_ids the same file belongs to (household census, wedding)
- source_log  optional path to the bot's dated research log entry for this find
- negative    for kind=note: true when it records a search that found nothing (kept as a research note)
- private     true for anything about a living person
- spouse      for kind=spouse: gramps_id of the spouse, plus optional "date", "place",
              "status" (married | divorced | widowed)

Rules: only content-confirmed matches; never a generated tree chart as a record; no invented facts.

Privacy: people_index.csv and the site show living people by first name only.

## Notes added 18 Nov 2026 (build side)

- Files over 10 MiB are fine to list; the build uploads a web-ready copy (under 10 MiB) and keeps your original untouched. Very large multi-page PDFs that cannot get under 10 MiB are reported in `manifest_unmatched.csv`; attach a page extract instead.
- Before adding an entry, check `shared/audit_mismatches.csv`. Entries that link a file to a person listed there as wrong are rewritten or skipped by the build.
- `url`, `title` and `date` feed each person's "Record links" note, so give the record's own URL and date where you can.

## Root people (added 18 Nov 2026, 5 PM CST)
- A `person` stub with `"parents": []` (or `["unknown"]`, or `"parents_unknown": true`) is created as a root person (no parents, none invented) only when the entry cites record support (confidence confirmed/probable, a `url` or `path`, and a `supports` fact other than "other"), the name is in CINDY_FAMILY_RECORD.md, and another live stub names this person as a parent.
- A listed parent who is not in the tree is created as a root person only when the entry's own record title names them and the entry has record support.
- `marriage` and `parent:` supports apply to the entry's main person only, not to `also_for` people.

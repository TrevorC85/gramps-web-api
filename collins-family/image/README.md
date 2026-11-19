# Our Gramps Web image (tree link lines) — local only, not deployed

`Dockerfile` builds Gramps Web 26.11.2 with our two forks:

- API: `TrevorC85/gramps-web-api`, branch `tree-link-confidence-proto` (endpoint
  `/api/childref-confidence/`, filter rule `IsLessThanNthGenerationAncestorOfAllFamilies`).
- Frontend: `gramps-web` 26.11.2 + branch `collins-tree-proto` (styled tree link lines).

Multi-stage: Node builds the frontend, a Debian trixie stage builds the Python venv pinned to
`constraints-26.11.2.txt` (`pip freeze` of the official image), the final stage is
`debian:trixie-slim` with runtime packages only. Same Gramps 6.0, same user-DB migrations
(alembic head `b6f1d3a9c2e4`, identical files) as the official image, so switching either way
needs no data migration.

Left out compared with the official image (~1.84 GB vs ~2.81 GB): AI chat / semantic search
(torch, sentence-transformers), the PostgreSQL client libraries, CJK fonts and OCR languages
other than English. The Collins site uses none of them.

## Build

```
collins-family/image/build.sh <backend-commit> <frontend-commit>
# e.g. build.sh tree-link-confidence-proto collins-tree-proto
```

Only committed code goes in (`git archive`). Tag: `grampsweb-collins:26.11.2-<be7>-<fe7>`;
labels `org.collins.backend-sha` / `org.collins.frontend-sha`. Needs the classic builder
(`DOCKER_BUILDKIT=0` is set) and network access for npm, pip and apt.

## Ship (draft, needs approval)

See "Our own image" in `../aws/README.md`: `docker save | gzip`, sha256 and image ID, upload to
the stack bucket, `update.sh image use …`, `update.sh image rollback`.

## Local rehearsal

```
collins-family/image/rehearse-local.sh grampsweb-collins:26.11.2-<be7>-<fe7>
```

Copies the local site's databases (SQLite online backup, read only; media mounted read only)
to `/workspace/gramps-web-proto/rehearsal`, runs them on 127.0.0.1:5059 and goes through:
start on the official image → refuse a tarball with a wrong sha256 → switch to our image →
an image that does not start is switched back automatically → roll back → restore drill from
the backup → stop. Every step checks the running image, the env file and the people/family
counts. Log: `rehearsal/rehearsal.log`. Never touches the local site (5055) or AWS.

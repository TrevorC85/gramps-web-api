# Collins Family — local Gramps Web

Local, login-required Gramps Web instance for the family tree. It is shaped like
the planned single-server AWS target (app + Celery worker + Redis/Valkey,
SQLite tree and media on disk). HTTPS via Caddy is for AWS only; on this box
the app is at **http://127.0.0.1:5055/**.

This setup follows the official Docker Compose example from
[Deploy with Docker](https://www.grampsweb.org/install_setup/deployment/)
(compose file: [docker-compose-base](https://github.com/gramps-project/gramps-web-docs/blob/main/examples/docker-compose-base/docker-compose.yml),
verified 18 Nov 2026), with these local changes:

- Host port **5055** instead of 80
- Bind mounts under `./data/` instead of named volumes (easier backups)
- `GRAMPSWEB_SECRET_KEY` in `.env`
- Registration disabled (`GRAMPSWEB_REGISTRATION_DISABLED=True` and the
  frontend register link hidden)
- Tree name **Collins Family**
- `network_mode: host` on this box, because Docker bridge
  inter-container traffic timed out here. Redis/Valkey is bound to
  `127.0.0.1` only. A normal AWS VM can use the official bridge network.

## Versions (as installed 18 Nov 2026, America/Chicago)

- Gramps Web API **3.25.2** (`ghcr.io/gramps-project/grampsweb:latest`, image
  created 9 Nov 2026)
- Gramps **6.0.8**
- Valkey **8.1.11** (`docker.io/valkey/valkey:8-alpine`)
- Docker Engine **29.10.0** (official static binaries) and Compose **v5.8.0**

Debian `apt` on this box could not refresh package indexes (HTTP 500 from the
egress proxy for `InRelease`). Docker was therefore installed from the official
static binaries at `https://download.docker.com/linux/static/stable/x86_64/`,
and `iptables` was installed from downloaded Debian `.deb` files so the
daemon could start. The helper `scripts/ensure-dockerd.sh` starts `dockerd`
if it is not running.

## Start and stop

```bash
cd /workspace/gramps-web
./scripts/start.sh          # starts dockerd if needed, then the stack
# or:  ./scripts/ensure-dockerd.sh && docker compose up -d

./scripts/stop.sh           # docker compose down; data is kept
# or:  docker compose down
```

Then open http://127.0.0.1:5055/. The site requires a login. Public
registration is disabled.

Accounts (username `trevor` = Owner, plus test Member and Contributor) and
their passwords are in:

```
/workspace/gramps-web/.admin_password
```

That file is mode 600. Do not commit it or paste the passwords into chat or
tickets. `.env` (mode 600) holds `GRAMPSWEB_SECRET_KEY`.

## Backup and restore

The family tree is SQLite under `data/grampsdb/`. Photos are in `data/media/`.
The user/login database is `data/users/users.sqlite`.

```bash
./scripts/backup.sh
# writes backups/gramps-web-YYYY-MM-DD_HHMMSS.tgz
# add --with-passwords as the second argument to include .admin_password
```

The default archive includes `data/` (except tmp/cache/thumbnails),
`docker-compose.yml`, `.env`, `frontend-config.js`, and `test_3gen.ged`.

To restore: stop the stack, unpack the archive over `/workspace/gramps-web`,
then start again.

```bash
./scripts/stop.sh
tar -C /workspace/gramps-web -xzf backups/gramps-web-DATE.tgz
./scripts/start.sh
```

## Test data

`test_3gen.ged` is a GEDCOM 5.5.1 extract of the first three generations
(Trevor’s side + Cindy’s side, linked through Trevor, Cindy, James, and Paul).
Facts come only from `/workspace/genealogy` and `/workspace/genealogy-cindy`.
Living people are shown by first name only and marked private. Parent-child
confidence is stored as notes and as Gramps tags (`confirmed (record)`,
`confirmed (family)`, `probable`, `needs confirmation`).

A test photo (a crop of the 18 Nov 2026 Collins paternal chart, page 1) is
attached to Trevor.

## Layout

```
docker-compose.yml      stack definition
.env                    SECRET_KEY (600)
.admin_password         local accounts (600)
frontend-config.js      hides the register link
test_3gen.ged           3-generation import file
data/grampsdb/          Gramps SQLite tree
data/users/             login database
data/media/             uploaded photos
data/redis/             Valkey data
screens/                UI screenshots from the local setup
scripts/start.sh
scripts/stop.sh
scripts/backup.sh
scripts/ensure-dockerd.sh
```

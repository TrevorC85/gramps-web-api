"""Create the synthetic (fictional) link-lines test tree and its users.

Run inside a Gramps Web container whose /root/.gramps/grampsdb and
/app/users are bind-mounted to an EMPTY local test data directory (never the
live or the real local data), with tests/link_tree_data.py on PYTHONPATH (not the tests package, whose
__init__ redirects the database path to a temporary directory):

  docker run --rm -e PYTHONPATH=/lt \
    -v $FORK/tests/link_tree_data.py:/lt/link_tree_data.py:ro \
    -v $FORK/collins-family/tools:/tools:ro \
    -v $DATA/grampsdb:/root/.gramps/grampsdb -v $DATA/users:/app/users \
    -v $DATA/secrets:/secrets --entrypoint python3 IMAGE \
    /tools/make_synthetic_link_tree.py

Users synth_owner / synth_member / synth_guest get random passwords written
to /secrets/users.json (mode 600); nothing is printed.
"""

import json
import os
import secrets
import subprocess
import sys

from gramps.gen.config import set as setconfig
from gramps.gen.db.utils import open_database

from gramps_webapi.app import create_app
from gramps_webapi.auth import add_user
from gramps_webapi.auth.const import ROLE_GUEST, ROLE_MEMBER, ROLE_OWNER
from gramps_webapi.dbmanager import WebDbManager
from link_tree_data import build_link_tree  # tests/link_tree_data.py

TREE = "SYNTHETIC LINK TEST (fictional)"
GUARD = "/root/.gramps/grampsdb"

if any(not name.startswith(".") for name in os.listdir(GUARD)):
    sys.exit(f"{GUARD} is not empty; refusing to touch an existing database")

setconfig("database.path", GUARD)
os.environ.setdefault("GRAMPSWEB_TREE", TREE)
os.environ.setdefault("GRAMPSWEB_SECRET_KEY", secrets.token_urlsafe(32))
# the user database is created by the same migrations as in the entrypoint
subprocess.run(
    [sys.executable, "-m", "gramps_webapi", "user", "migrate"], cwd="/app/src", check=True
)
app = create_app(config_from_env=True)
with app.app_context():
    manager = WebDbManager(name=TREE, create_if_missing=True)
    db = open_database(TREE, force_unlock=True)
    ids = build_link_tree(db)
    db.close()
    users = {}
    for name, role in (
        ("synth_owner", ROLE_OWNER),
        ("synth_member", ROLE_MEMBER),
        ("synth_guest", ROLE_GUEST),
    ):
        password = secrets.token_urlsafe(18)
        add_user(name=name, password=password, role=role, tree=manager.dirname)
        users[name] = password
path = "/secrets/users.json"
with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(users, f)
print(f"created tree {TREE!r} ({len(ids)} objects) in {manager.path} and 3 users")

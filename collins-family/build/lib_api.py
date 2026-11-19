"""Shared Gramps Web API client. Base URL from GRAMPSWEB_URL (fallback GRAMPS_WEB_URL,
then http://127.0.0.1:5055). Credentials from GRAMPSWEB_USERNAME / GRAMPSWEB_PASSWORD
or the local /workspace/gramps-web/.admin_password file."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PASSFILE = Path("/workspace/gramps-web/.admin_password")


def base_url() -> str:
    return (
        os.environ.get("GRAMPSWEB_URL")
        or os.environ.get("GRAMPS_WEB_URL")
        or os.environ.get("GRAMPSWEB_BASE_URL")
        or "http://127.0.0.1:5055"
    ).rstrip("/")


def load_credentials() -> tuple[str, str]:
    user = os.environ.get("GRAMPSWEB_USERNAME")
    pw = os.environ.get("GRAMPSWEB_PASSWORD")
    if user and pw:
        return user, pw
    user = pw = None
    if not PASSFILE.exists():
        raise SystemExit(
            "No GRAMPSWEB_USERNAME/GRAMPSWEB_PASSWORD and no local .admin_password"
        )
    for line in PASSFILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("username=") and user is None:
            user = line.split("=", 1)[1].strip()
        elif line.startswith("password=") and pw is None and user:
            pw = line.split("=", 1)[1].strip()
            break
    if not user or not pw:
        raise SystemExit("could not parse Gramps Web credentials")
    return user, pw


def first_new_handle(resp):
    if isinstance(resp, dict) and resp.get("handle"):
        return resp["handle"]
    if isinstance(resp, list):
        for it in resp:
            if isinstance(it, dict) and it.get("type") == "add":
                return it.get("handle") or (it.get("new") or {}).get("handle")
        for it in resp:
            if isinstance(it, dict) and it.get("handle"):
                return it["handle"]
    raise RuntimeError(f"no handle in response: {json.dumps(resp)[:600]}")


class Api:
    def __init__(self, base: str | None = None):
        self.base = (base or base_url()).rstrip("/")
        self.token = None
        self.token_at = 0
        self.refresh()

    def refresh(self):
        user, pw = load_credentials()
        body = json.dumps({"username": user, "password": pw}).encode()
        for attempt in range(8):
            req = urllib.request.Request(
                self.base + "/api/token/",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                self.token = data["access_token"]
                self.token_at = time.time()
                return
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(5 + attempt * 5)
                    continue
                raise SystemExit(f"token failed: {e.code}")
        raise SystemExit("token rate limited")

    def request(self, method, path, data=None, content_type=None, timeout=90):
        if time.time() - self.token_at > 600:
            self.refresh()
        headers = {"Authorization": f"Bearer {self.token}"}
        body = None
        if data is not None:
            if isinstance(data, (bytes, bytearray)):
                body = data
                headers["Content-Type"] = content_type or "application/octet-stream"
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if not raw:
                    return None
                ctype = r.headers.get("Content-Type") or ""
                if "json" in ctype or raw[:1] in (b"{", b"["):
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:1200]}")

    def get(self, path):
        # GETs are safe to repeat: retry a transient timeout or dropped connection.
        for attempt in range(3):
            try:
                return self.request("GET", path, timeout=180 if attempt else 90)
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

    def post(self, path, data=None, content_type=None, timeout=90):
        return self.request("POST", path, data=data, content_type=content_type, timeout=timeout)

    def put(self, path, data, timeout=90):
        return self.request("PUT", path, data=data, timeout=timeout)

    def delete(self, path):
        return self.request("DELETE", path)


# ---------------------------------------------------------------------------
# Round 5 (Trevor approved 18 Nov 2026), items 5 and 6: every build save goes through
# guarded_put. It skips a PUT when the stored object already equals the payload, and
# retries a transient failure (timeout, dropped connection, 5xx) with backoff 5/15/45 s,
# checking first whether the timed-out save actually landed. A save that still fails
# is appended to data/failed_saves.jsonl; run.py reruns those steps once at the end.
import atexit as _atexit
import socket as _socket
import sys as _sys

_ROOT = Path(__file__).resolve().parent
FAILED_SAVES = _ROOT / "data" / "failed_saves.jsonl"
SAVE_STATS_FILE = _ROOT / "data" / "save_stats.jsonl"
VOLATILE_KEYS = {"change", "profile", "backlinks", "extended", "formatted"}
SAVE_STATS = {"put_sent": 0, "put_skipped_unchanged": 0, "put_retried": 0, "put_failed": 0}
BACKOFF = (5, 15, 45)


def _same(cur, data) -> bool:
    if not isinstance(cur, dict) or not isinstance(data, dict):
        return False
    return all(cur.get(k) == v for k, v in data.items() if k not in VOLATILE_KEYS)


def is_transient(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, _socket.timeout, ConnectionError)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    if isinstance(exc, urllib.error.URLError):
        return True
    msg = str(exc)
    return "timed out" in msg or any(f"-> {c}" in msg for c in ("500", "502", "503", "504", "429"))


def guarded_put(send, fetch, path, data):
    """send() performs the PUT; fetch() GETs the stored object (None if unavailable)."""
    try:
        cur = fetch()
    except Exception:
        cur = None
    if cur is not None and _same(cur, data):
        SAVE_STATS["put_skipped_unchanged"] += 1
        return None
    for attempt in range(len(BACKOFF) + 1):
        try:
            r = send()
            SAVE_STATS["put_sent"] += 1
            return r
        except Exception as exc:  # noqa: BLE001
            if not is_transient(exc) or attempt == len(BACKOFF):
                SAVE_STATS["put_failed"] += 1
                FAILED_SAVES.parent.mkdir(parents=True, exist_ok=True)
                with FAILED_SAVES.open("a") as f:
                    f.write(json.dumps({"script": Path(_sys.argv[0]).name, "path": path,
                                        "error": str(exc)[:300], "at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
                raise
            SAVE_STATS["put_retried"] += 1
            time.sleep(BACKOFF[attempt])
            try:  # did the timed-out save land after all?
                if _same(fetch(), data):
                    SAVE_STATS["put_sent"] += 1
                    return None
            except Exception:
                pass


def _write_save_stats():
    if any(SAVE_STATS.values()):
        try:
            with SAVE_STATS_FILE.open("a") as f:
                f.write(json.dumps({"script": Path(_sys.argv[0]).name, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **SAVE_STATS}) + "\n")
        except Exception:
            pass


_atexit.register(_write_save_stats)


def _api_put(self, path, data, timeout=90):
    return guarded_put(lambda: self.request("PUT", path, data=data, timeout=timeout),
                       lambda: self.request("GET", path, timeout=timeout), path, data)


Api.put = _api_put

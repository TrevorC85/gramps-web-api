import sys, socket
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib_api


def test_skips_unchanged():
    calls = []
    r = lib_api.guarded_put(lambda: calls.append(1), lambda: {"a": 1, "change": 5}, "/x", {"a": 1, "change": 4})
    assert r is None and calls == []


def test_retries_timeout(monkeypatch):
    monkeypatch.setattr(lib_api, "BACKOFF", (0, 0, 0))
    state = {"n": 0, "stored": {"a": 0}}
    def send():
        state["n"] += 1
        if state["n"] < 3:
            raise socket.timeout("timed out")
        state["stored"] = {"a": 1}
        return "ok"
    assert lib_api.guarded_put(send, lambda: state["stored"], "/x", {"a": 1}) == "ok"
    assert state["n"] == 3


def test_timed_out_save_that_landed(monkeypatch):
    monkeypatch.setattr(lib_api, "BACKOFF", (0, 0, 0))
    state = {"stored": {"a": 0}, "n": 0}
    def send():
        state["n"] += 1
        state["stored"] = {"a": 1}
        raise socket.timeout("timed out")
    lib_api.guarded_put(send, lambda: state["stored"], "/x", {"a": 1})
    assert state["n"] == 1


def test_records_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(lib_api, "BACKOFF", (0,))
    monkeypatch.setattr(lib_api, "FAILED_SAVES", tmp_path / "f.jsonl")
    def send():
        raise socket.timeout("timed out")
    try:
        lib_api.guarded_put(send, lambda: {"a": 0}, "/x", {"a": 1})
    except socket.timeout:
        pass
    assert "/x" in (tmp_path / "f.jsonl").read_text()

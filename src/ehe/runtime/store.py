"""SQLite authoritative state plus recoverable JSON/JSONL projections."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from ..io import atomic_write, format_json, now
from ..project import output_path

TRANSITIONS = {
    "CREATED": {"PLANNED", "CANCELLED"},
    "PLANNED": {"WAITING_APPROVAL", "RUNNING", "CANCELLED"},
    "RUNNING": {"VERIFYING", "BLOCKED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "VERIFYING": {"PASSED", "FAILED", "REVISE", "PAUSED", "BLOCKED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "WAITING_APPROVAL": {"RUNNING", "PAUSED", "BLOCKED", "CANCELLED"},
    "REVISE": {"RUNNING", "PAUSED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "PAUSED": {"RUNNING", "BUDGET_EXHAUSTED", "CANCELLED"},
    "BLOCKED": {"RUNNING", "PAUSED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "FAILED": {"RUNNING", "PAUSED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "PASSED": {"RUNNING", "PAUSED", "BUDGET_EXHAUSTED"},
    "BUDGET_EXHAUSTED": set(),
    "CANCELLED": set(),
}


@contextmanager
def exclusive(root: Path):
    lock = output_path(root, "runtime.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("A runtime process already owns the project lock") from exc
        yield


class Store:
    def __init__(self, root: Path, *, create=False):
        self.root = root
        path = output_path(root, "runtime.sqlite3")
        if not create and not path.is_file():
            raise ValueError("No runtime state. Start with ehe loop run.")
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, payload TEXT NOT NULL);"
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def read(self):
        row = self.db.execute("SELECT payload FROM state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, state, event: str, details: dict | None = None, *, transition: str | None = None):
        prior = state["state"]
        if transition is not None:
            if transition not in TRANSITIONS[prior]:
                raise ValueError(f"Illegal runtime transition: {prior} -> {transition}")
            state["state"] = transition
        state["updated_at"] = now()
        entry = {"event": event, "at": state["updated_at"], "run_id": state["run_id"], "from": prior,
                 "state": state["state"], "details": details or {}}
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (format_json(state),))
            self.db.execute("INSERT INTO events(payload) VALUES (?)", (format_json(entry),))
        self.project_views(state)

    def project_views(self, state):
        events = []
        for seq, item in self.db.execute("SELECT seq, payload FROM events ORDER BY seq"):
            events.append(json.dumps({"seq": seq, **json.loads(item)}, ensure_ascii=False, allow_nan=False))
        atomic_write(output_path(self.root, "events.jsonl"), "\n".join(events) + "\n")
        atomic_write(output_path(self.root, "run.json"), format_json(state))
        manifest = [
            {"task": task, "input_fingerprint": record["fingerprint"],
             "files": record["result"].get("evidence", []),
             "execution": record["result"].get("execution", {}),
             "artifacts": record["result"].get("artifacts")}
            for task, record in state["results"].items()
        ]
        atomic_write(output_path(self.root, "artifacts.json"), format_json(manifest))

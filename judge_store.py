"""
Shared SQLite state for the Judge, the chat backend and the Judge Dashboard.

Replaces the old sessions.json / stats.json / blocked_users.json. All three
processes open the same data/aijudge.db (WAL mode: readers never block the
writer, and the writer never blocks readers), so this module is the only
thing that knows the schema. Logs and verdicts stay as per-exchange files.

Tables:
  blocked(session_id PK, blocked_at)   -- the blocklist; indexed point lookups
  sessions(session_id PK, data JSON)   -- per-session suspicion state
  kv(key PK, value JSON)               -- "stats", "fast_latency", "meta", ...

The public surface deliberately returns the same dict shapes the JSON files
held, so callers' logic (and the API responses built from it) is unchanged.
Stats read-modify-writes run inside BEGIN IMMEDIATE, so they are now safe
across processes, not just across asyncio tasks.

Only stdlib is used; this file must stay importable with no side effects
beyond what `Store()` does.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_NAME = "aijudge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocked (
    session_id TEXT PRIMARY KEY,
    blocked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def resolve_data_dir(repo_root):
    """Same anchoring rule everywhere: a relative AIJUDGE_DATA_DIR resolves
    against the repo root, never the process's cwd."""
    env = os.environ.get("AIJUDGE_DATA_DIR")
    return ((Path(repo_root) / env) if env else (Path(repo_root) / "data")).resolve()


def default_stats():
    return {
        "total_requests": 0,
        "verdict_counts": {"safe": 0, "suspicious": 0, "bad": 0},
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "judge_overhead_tokens": 0,
        "judge_prompt_tokens": 0,
        "judge_completion_tokens": 0,
        "judge_calls": 0,
        # How each exchange was resolved: blocked by a fast rule, fast rules
        # only (scored or clean — no AI), or triggered a slow LLM review.
        "judge_paths": {"fast_block": 0, "fast": 0, "slow": 0},
        # epoch-minute (str) -> {"chat": tokens, "judge": tokens}
        "token_buckets": {},
        # session id -> {"chat": tokens, "judge": tokens, "requests": n}
        "session_tokens": {},
        "unique_sessions": [],
        "recent_timestamps": [],
    }


def default_fast_latency():
    return {"checks": 0, "total_ms": 0.0, "max_ms": 0.0, "recent": []}


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / DB_NAME
        # isolation_level=None -> autocommit; transactions are explicit.
        # check_same_thread=False + a lock: FastAPI/TestClient may call from a
        # worker thread, and a transaction must not interleave with another.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript(SCHEMA)
        # session id -> last JSON written, so a persist only touches rows
        # that changed.
        self._saved_sessions = {}
        self._import_legacy_json()

    # --- transactions ---

    def _write(self, fn):
        """Run fn(db) inside BEGIN IMMEDIATE (takes the write lock up front,
        so a read-modify-write can't interleave with another process)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                result = fn(self._db)
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
            return result

    def _kv_get(self, key, default):
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _kv_put(db, key, value):
        db.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --- blocklist ---

    def is_blocked(self, session_id):
        """Indexed point lookup: cheap enough for the request path, and always
        current (a dashboard reset is visible on the very next call)."""
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM blocked WHERE session_id=?", (session_id,)
            ).fetchone() is not None

    def blocked_users(self):
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT session_id FROM blocked ORDER BY session_id")]

    def block(self, session_id):
        self._write(lambda db: db.execute(
            "INSERT OR IGNORE INTO blocked(session_id, blocked_at) VALUES(?, ?)",
            (session_id, time.time()),
        ))

    def clear_blocklist(self):
        """Empty the blocklist atomically and return what was cleared. Does
        not touch sessions, so scores and watermarks survive."""
        def run(db):
            cleared = [r[0] for r in db.execute("SELECT session_id FROM blocked ORDER BY session_id")]
            db.execute("DELETE FROM blocked")
            return cleared
        return self._write(run)

    # --- sessions ---

    def load_sessions(self):
        """(sessions dict, fast_latency dict) for the Judge's in-memory copy."""
        sessions = {}
        with self._lock:
            rows = self._db.execute("SELECT session_id, data FROM sessions").fetchall()
        for sid, data in rows:
            try:
                sessions[sid] = json.loads(data)
                self._saved_sessions[sid] = data
            except json.JSONDecodeError:
                continue
        return sessions, {**default_fast_latency(), **self._kv_get("fast_latency", {})}

    def save_sessions(self, sessions, fast_latency, slow_review_threshold):
        """Judge only. Upserts just the sessions whose state changed since the
        last save, plus the global latency block, in one transaction."""
        changed = []
        for sid, s in sessions.items():
            blob = json.dumps(s)
            if self._saved_sessions.get(sid) != blob:
                changed.append((sid, blob))
        now = time.time()

        def run(db):
            db.executemany(
                "INSERT INTO sessions(session_id, data, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                [(sid, blob, now) for sid, blob in changed],
            )
            self._kv_put(db, "fast_latency", fast_latency)
            self._kv_put(db, "meta", {"slow_review_threshold": slow_review_threshold, "updated_at": now})
        self._write(run)
        self._saved_sessions.update(changed)

    def get_session(self, session_id):
        with self._lock:
            row = self._db.execute("SELECT data FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return {}

    def read_sessions_payload(self):
        """Reader view, same shape sessions.json had:
        {"slow_review_threshold", "updated_at", "sessions", "fast_latency"}."""
        sessions, fast_latency = self.load_sessions()
        return {**self._kv_get("meta", {}), "sessions": sessions, "fast_latency": fast_latency}

    # --- stats ---

    def read_stats(self):
        return {**default_stats(), **self._kv_get("stats", {})}

    def update_stats(self, mutate):
        """Read-modify-write the stats document under the write lock."""
        def run(db):
            row = db.execute("SELECT value FROM kv WHERE key='stats'").fetchone()
            try:
                stats = json.loads(row[0]) if row else default_stats()
            except json.JSONDecodeError:
                stats = default_stats()
            mutate(stats)
            self._kv_put(db, "stats", stats)
        self._write(run)

    # --- one-time import of the pre-SQLite JSON files ---

    def _import_legacy_json(self):
        legacy = {
            "blocked": self.data_dir / "blocked_users.json",
            "sessions": self.data_dir / "sessions.json",
            "stats": self.data_dir / "stats.json",
        }
        if not any(p.exists() for p in legacy.values()):
            return

        def load(path):
            try:
                return json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                return None

        def run(db):
            # Decided inside the write lock, so concurrent processes starting
            # together import exactly once.
            if db.execute("SELECT 1 FROM kv WHERE key='legacy_imported'").fetchone():
                return False
            now = time.time()
            blocked = load(legacy["blocked"])
            if isinstance(blocked, list):
                db.executemany(
                    "INSERT OR IGNORE INTO blocked(session_id, blocked_at) VALUES(?, ?)",
                    [(str(s), now) for s in blocked],
                )
            sessions = load(legacy["sessions"])
            if isinstance(sessions, dict):
                for sid, s in (sessions.get("sessions") or {}).items():
                    db.execute(
                        "INSERT OR IGNORE INTO sessions(session_id, data, updated_at) VALUES(?, ?, ?)",
                        (sid, json.dumps(s), now),
                    )
                if sessions.get("fast_latency"):
                    self._kv_put(db, "fast_latency", sessions["fast_latency"])
                if sessions.get("slow_review_threshold") is not None:
                    self._kv_put(db, "meta", {
                        "slow_review_threshold": sessions["slow_review_threshold"],
                        "updated_at": sessions.get("updated_at", now),
                    })
            stats = load(legacy["stats"])
            if isinstance(stats, dict):
                self._kv_put(db, "stats", {**default_stats(), **stats})
            self._kv_put(db, "legacy_imported", now)
            return True

        if self._write(run):
            for p in legacy.values():
                try:
                    if p.exists():
                        p.replace(p.with_name(p.name + ".migrated"))
                except OSError:
                    pass  # the kv flag already prevents a re-import

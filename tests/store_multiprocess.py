"""Exercises judge_store.Store from several processes (not assertions-by-CI;
exits non-zero on failure). Run from the repo root:
    python tests/store_multiprocess.py
"""
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import judge_store  # noqa: E402

N_PROCS, N_UPDATES = 4, 200


def open_store(data_dir):
    judge_store.Store(data_dir)


def stats_worker(data_dir):
    store = judge_store.Store(data_dir)
    for _ in range(N_UPDATES):
        store.update_stats(lambda s: s.__setitem__("total_requests", s["total_requests"] + 1))


def reader_worker(data_dir, stop_at, out):
    store = judge_store.Store(data_dir)
    n = 0
    while time.time() < stop_at:
        store.read_stats()
        store.blocked_users()
        store.read_sessions_payload()
        n += 1
    out.put(n)


def main():
    tmp = Path(tempfile.mkdtemp())
    # legacy JSON present before first open -> imported once
    (tmp / "blocked_users.json").write_text(json.dumps(["old-blocked"]))
    (tmp / "sessions.json").write_text(json.dumps({
        "slow_review_threshold": 7, "updated_at": 1.0,
        "sessions": {"old-sess": {"score": 5, "reviewed_score": 2}},
        "fast_latency": {"checks": 3, "total_ms": 1.5, "max_ms": 1.0, "recent": [0.5]}}))
    (tmp / "stats.json").write_text(json.dumps({"total_requests": 10, "unique_sessions": ["old-sess"]}))

    # several processes start together: import must happen exactly once
    ps = [mp.Process(target=open_store, args=(tmp,)) for _ in range(N_PROCS)]
    [p.start() for p in ps]
    [p.join() for p in ps]
    store = judge_store.Store(tmp)
    assert store.blocked_users() == ["old-blocked"], store.blocked_users()
    assert store.read_stats()["total_requests"] == 10
    payload = store.read_sessions_payload()
    assert payload["sessions"]["old-sess"]["score"] == 5 and payload["slow_review_threshold"] == 7
    assert payload["fast_latency"]["checks"] == 3
    assert (tmp / "stats.json.migrated").exists() and not (tmp / "stats.json").exists()

    # concurrent stats read-modify-writes from N processes lose no updates,
    # while readers hammer the db
    stop_at = time.time() + 8
    q = mp.Queue()
    readers = [mp.Process(target=reader_worker, args=(tmp, stop_at, q)) for _ in range(2)]
    writers = [mp.Process(target=stats_worker, args=(tmp,)) for _ in range(N_PROCS)]
    [p.start() for p in readers + writers]
    [p.join() for p in writers]
    total = store.read_stats()["total_requests"]
    assert total == 10 + N_PROCS * N_UPDATES, total

    # block in one process -> visible to another immediately; reset keeps sessions
    judge, dash = judge_store.Store(tmp), judge_store.Store(tmp)
    sessions = {"s1": {"score": 3}}
    judge.save_sessions(sessions, judge_store.default_fast_latency(), 9)
    judge.block("s1")
    assert dash.is_blocked("s1")
    assert dash.get_session("s1")["score"] == 3
    assert dash.clear_blocklist() == ["old-blocked", "s1"]
    assert not judge.is_blocked("s1")
    assert dash.get_session("s1")["score"] == 3  # reset does not touch sessions

    # save_sessions only rewrites changed rows
    sessions["s1"]["score"] = 4
    judge.save_sessions(sessions, judge_store.default_fast_latency(), 9)
    assert dash.get_session("s1")["score"] == 4

    [p.join() for p in readers]
    print("reader iterations:", [q.get() for _ in readers])

    t = time.perf_counter()
    for _ in range(2000):
        judge.is_blocked("s1")
    print("is_blocked avg us: %.1f" % ((time.perf_counter() - t) / 2000 * 1e6))
    print("OK")


if __name__ == "__main__":
    main()

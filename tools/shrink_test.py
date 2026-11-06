"""The brief's shrink test (Phase 7): 1,000,000 puts, then 1,000,000 deletes, compaction.

Usage: python3 tools/shrink_test.py
Writes through the engine API in BATCHES of 10,000 operations (100 batches of
puts, then 100 batches of deletes) to save time — not single puts. Prints the
file size after the puts, after the deletes and after compaction, then checks
that the database is empty, reopens it, and puts one key.
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kv import check, open_db  # noqa: E402


def main():
    work = os.path.join(ROOT, ".torture-work", "shrink")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    path = os.path.join(work, "db.kv")
    n = 1_000_000
    t0 = time.perf_counter()
    db, _ = open_db(path)
    for start in range(0, n, 10_000):
        db.batch([("put", b"key%07d" % i, b"val%07d" % i) for i in range(start, start + 10_000)])
    after_puts = os.path.getsize(path)
    keys_after_puts = len(db)
    for start in range(0, n, 10_000):
        db.batch([("del", b"key%07d" % i, None) for i in range(start, start + 10_000)])
    after_deletes = os.path.getsize(path)
    keys_after_deletes = len(db)
    t1 = time.perf_counter()
    before, after = db.compact()
    t2 = time.perf_counter()
    keys_after_compact = len(db)
    db.close()
    print("captured", time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("built with: batches of 10,000 operations through the engine API (not single puts)")
    print("after 1,000,000 puts:    {:>12,} bytes, {:,} keys".format(after_puts, keys_after_puts))
    print("after 1,000,000 deletes: {:>12,} bytes, {:,} keys".format(after_deletes, keys_after_deletes))
    print("after compaction:        {:>12,} bytes, {:,} keys  (compact() returned {:,} -> {:,})".format(
        os.path.getsize(path), keys_after_compact, before, after))
    print("time: writes {:.1f} s, compaction {:.2f} s".format(t1 - t0, t2 - t1))
    print("check after compaction:", check(path))
    db, report = open_db(path)
    print("reopen: keys={} snapshot={}".format(report.keys, report.snapshot))
    db.put(b"new", b"1")
    db.close()
    db, _ = open_db(path)
    print("a new put after reopen works:", db.get(b"new") == b"1", "; keys now", len(db))
    db.close()
    shutil.rmtree(work, ignore_errors=True)
    return 0 if keys_after_compact == 0 and after == 16 else 1


if __name__ == "__main__":
    sys.exit(main())

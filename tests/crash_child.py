"""Child process for tests/test_crash.py. Never exits by itself: it is always hard-killed.

Usage: python3 -m tests.crash_child <db path> <scenario>
With KVDB_PAUSE_AT set, the engine blocks at that pause point and prints
"PAUSE <name>" to stderr. If the scenario finishes, it prints "DONE" and blocks.
"""
import sys
import threading

from src.kv import open_db

BIG = b"N" * 1_048_576


def main():
    path, scenario = sys.argv[1], sys.argv[2]
    db, _ = open_db(path)                         # recovery.* and create.* pause points fire here
    if scenario == "put":
        db.put(b"k", b"new")
    elif scenario == "delete":
        db.delete(b"k")
    elif scenario == "bigput":
        db.put(b"k", BIG)
    elif scenario == "batch10":
        db.batch([("put", b"b%d" % i, b"new%d" % i) for i in range(10)])
    elif scenario == "batch10000":
        db.batch([("put", b"bk%05d" % i, b"v%05d" % i) for i in range(10_000)])
    elif scenario == "returned":                  # A5: killed right after put returned success
        db.put(b"k", b"new")
        sys.stderr.write("RETURNED\n")
        sys.stderr.flush()
    elif scenario == "close":
        db.close()
    elif scenario == "snapshot":                  # 60 puts with a snapshot every 50 transactions
        db.close()
        db, _ = open_db(path, snapshot_every=50)
        for i in range(60):
            db.put(b"s%d" % i, b"v%d" % i)
    elif scenario != "open":
        raise SystemExit("unknown scenario " + scenario)
    sys.stderr.write("DONE\n")
    sys.stderr.flush()
    threading.Event().wait()


if __name__ == "__main__":
    main()

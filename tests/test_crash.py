"""Targeted hard-kill tests (Phase 3). These are TARGETED results, not random ones.

Each test starts tests/crash_child.py in its own process with KVDB_PAUSE_AT set,
waits until the child prints "PAUSE <name>" on stderr, hard-kills it with
Popen.kill() (SIGKILL on macOS/Linux), then reopens the file in this process and
checks the outcome the brief's scenario demands.
"""
import os
import queue
import subprocess
import sys
import threading
import unittest

from src.kv import check, layout, open_db
from tests.kvtest_util import ROOT, WorkDir, read_bytes, write_bytes

TIMEOUT = 60


def kill_at(path, scenario, pause_at, expect_line=None):
    """Run the child until it prints `expect_line` (default 'PAUSE <pause_at>'), then hard-kill it."""
    want = expect_line or "PAUSE " + pause_at
    env = dict(os.environ, KVDB_PAUSE_AT=pause_at or "")
    proc = subprocess.Popen([sys.executable, "-m", "tests.crash_child", path, scenario],
                            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    lines = queue.Queue()

    def reader():
        for raw in proc.stderr:
            lines.put(raw.decode("utf-8", "replace").rstrip("\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    seen = []
    try:
        while True:
            line = lines.get(timeout=TIMEOUT)
            if line is None:
                raise AssertionError("child ended before '{}': {}".format(want, seen))
            seen.append(line)
            if line == want:
                break
            if line == "DONE":
                raise AssertionError("child finished without reaching '{}'".format(want))
    finally:
        proc.kill()                      # the hard kill
        proc.wait(timeout=TIMEOUT)
        proc.stderr.close()
    if os.name != "nt":
        assert proc.returncode == -9, proc.returncode      # really SIGKILL
    return proc.returncode


def make_db(path, **values):
    with open_db(path)[0] as db:
        for k, v in values.items():
            db.put(k.encode(), v)


class WriteKillTest(unittest.TestCase):
    """A1-A6, B1-B4: kill a write at each pause point of CLAUDE.md section 9."""

    PUT_EXPECT = [                                  # pause point -> value of k after reopen
        ("write.before-body", b"old"),              # A1
        ("write.mid-body", b"old"),                 # A2
        ("write.after-body", b"old"),               # A3 (body written, no marker)
        ("write.after-body-sync", b"old"),          # A3
        ("write.after-marker", b"new"),             # A3/B3: marker in the OS page cache = committed
        ("write.after-commit-sync", b"new"),        # A4: MUST be present
        ("write.after-index", b"new"),              # A4/A5
    ]

    def reopen_and_check(self, path):
        db, report = open_db(path)
        self.addCleanup(db.close)
        self.assertEqual(check(path), (True, "OK"))
        return db, report

    def test_put(self):
        for pause_at, expect in self.PUT_EXPECT:
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                make_db(path, k=b"old", other=b"keep")
                kill_at(path, "put", pause_at)
                db, _ = self.reopen_and_check(path)
                self.assertEqual(db.get(b"k"), expect)
                self.assertEqual(db.get(b"other"), b"keep")
                db.put(b"after", b"1")                           # usable after the crash

    def test_put_killed_after_it_returned(self):                 # A5
        with WorkDir() as w:
            path = w.path("db.kv")
            make_db(path, k=b"old")
            kill_at(path, "returned", "", expect_line="RETURNED")
            db, _ = self.reopen_and_check(path)
            self.assertEqual(db.get(b"k"), b"new")

    def test_delete(self):                                       # A6
        for pause_at, expect in self.PUT_EXPECT:
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                make_db(path, k=b"old")
                kill_at(path, "delete", pause_at)
                db, _ = self.reopen_and_check(path)
                self.assertEqual(db.get(b"k"), None if expect == b"new" else b"old")

    def test_one_megabyte_value(self):                           # A7
        for pause_at, expect_big in (("write.mid-body", False), ("write.after-body", False),
                                     ("write.after-marker", True), ("write.after-commit-sync", True)):
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                make_db(path, k=b"old")
                kill_at(path, "bigput", pause_at)
                db, _ = self.reopen_and_check(path)
                self.assertEqual(db.get(b"k"), b"N" * 1_048_576 if expect_big else b"old")

    def test_batch_of_ten(self):                                 # B1, B2, B3, B4
        for pause_at, expect in self.PUT_EXPECT:
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                make_db(path, other=b"keep")
                kill_at(path, "batch10", pause_at)
                db, _ = self.reopen_and_check(path)
                got = [db.get(b"b%d" % i) for i in range(10)]
                if expect == b"new":
                    self.assertEqual(got, [b"new%d" % i for i in range(10)])
                else:
                    self.assertEqual(got, [None] * 10)
                self.assertEqual(db.get(b"other"), b"keep")

    def test_batch_of_ten_thousand_killed_halfway(self):         # B6 (correctness only; timing is separate)
        with WorkDir() as w:
            path = w.path("db.kv")
            make_db(path, other=b"keep")
            size_before = os.path.getsize(path)
            kill_at(path, "batch10000", "write.mid-body")
            size_killed = os.path.getsize(path)
            self.assertGreater(size_killed, size_before)              # half a batch really is on disk
            db, report = self.reopen_and_check(path)
            self.assertEqual(len(db), 1)                              # only 'other': none of the batch
            self.assertEqual(db.get(b"bk00000"), None)
            self.assertEqual(report.bytes_cut, size_killed - size_before)
            self.assertEqual(os.path.getsize(path), size_before)


class OpenKillTest(unittest.TestCase):
    """create.*, recovery.* and close pause points (C1, C2, C6, F2)."""

    def torn_file(self, path):
        make_db(path, a=b"1", b=b"2")
        good = read_bytes(path)
        write_bytes(path, good + layout.encode_txn([(1, b"c", b"3")])[:9])    # a torn third txn
        return good

    def test_create_mid_header(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            kill_at(path, "open", "create.mid-header")
            self.assertEqual(os.path.getsize(path), 8)                       # a header prefix
            self.assertEqual(check(path), (False, "INTERRUPTED_CREATE (8 bytes)"))
            with open_db(path)[0] as db:
                self.assertEqual(len(db), 0)
                db.put(b"k", b"v")
            self.assertEqual(check(path), (True, "OK"))

    def test_kill_during_recovery_twice(self):                              # C1, C2, F2
        for pause_at in ("recovery.replay", "recovery.before-cut", "recovery.after-cut"):
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                good = self.torn_file(path)
                kill_at(path, "open", pause_at)                              # kill during recovery
                kill_at(path, "open", "recovery.replay")                     # restart, kill during recovery again
                db, report = open_db(path)
                self.assertEqual((db.get(b"a"), db.get(b"b"), db.get(b"c")), (b"1", b"2", None))
                db.close()
                self.assertEqual(read_bytes(path), good)                     # same state every time
                self.assertEqual(check(path), (True, "OK"))

    def test_kill_before_trailer_restore(self):                             # F4 + C2
        with WorkDir() as w:
            path = w.path("db.kv")
            make_db(path, a=b"1", b=b"2")
            good = read_bytes(path)
            write_bytes(path, good[:-1])
            kill_at(path, "open", "recovery.before-trailer")
            kill_at(path, "open", "recovery.before-trailer")
            db, report = open_db(path)
            self.assertTrue(report.trailer_restored)
            self.assertEqual((db.get(b"a"), db.get(b"b")), (b"1", b"2"))
            db.close()
            self.assertEqual(read_bytes(path), good)

    def test_kill_during_close(self):                                       # C6
        with WorkDir() as w:
            path = w.path("db.kv")
            make_db(path, a=b"1")
            before = read_bytes(path)
            kill_at(path, "close", "close")
            self.assertEqual(read_bytes(path), before)                       # close wrote nothing
            with open_db(path)[0] as db:
                self.assertEqual(db.get(b"a"), b"1")


if __name__ == "__main__":
    unittest.main()

"""Tests for the database lock <path>.lock (Phase 6, team decision): one open at a time."""
import os
import queue
import subprocess
import sys
import threading
import unittest

from src.kv import DatabaseBusy, InvalidInput, check, open_db
from tests.kvtest_util import ROOT, WorkDir, read_bytes

TIMEOUT = 60


class PausedChild:
    """tests/crash_child.py stopped at a pause point and kept ALIVE (holding the lock)."""

    def __init__(self, path, scenario, pause_at):
        env = dict(os.environ, KVDB_PAUSE_AT=pause_at)
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.crash_child", path, scenario], cwd=ROOT,
                                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(l) for l in self.proc.stderr] and lines.put(None),
                         daemon=True).start()
        while True:
            line = lines.get(timeout=TIMEOUT)
            if line is None or line.decode().strip() == "PAUSE " + pause_at:
                break
        assert line is not None, "child ended before the pause point"

    def kill(self):
        self.proc.kill()
        self.proc.wait(timeout=TIMEOUT)
        self.proc.stderr.close()


class LockTest(unittest.TestCase):
    def test_second_open_in_the_same_process_is_refused(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path)
            db.put(b"a", b"1")
            before = read_bytes(path)
            with self.assertRaises(DatabaseBusy):
                open_db(path)
            self.assertEqual(read_bytes(path), before)
            db.close()
            with open_db(path)[0] as again:                     # released by close
                self.assertEqual(again.get(b"a"), b"1")

    def test_a_failed_open_releases_the_lock(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            open_db(path, max_bytes=1000)[0].close()
            with self.assertRaises(InvalidInput):
                open_db(path, max_bytes=2000)                   # refused after taking the lock
            open_db(path)[0].close()                            # not left locked

    def test_open_while_another_process_is_mid_write_does_not_cut_its_write(self):
        """BREAKING.md weakness 4, reproduced: without the lock this open would treat the
        other process's half-written transaction as a torn tail and truncate it."""
        with WorkDir() as w:
            path = w.path("db.kv")
            with open_db(path)[0] as db:
                db.put(b"k", b"old")
            committed = os.path.getsize(path)
            child = PausedChild(path, "put", "write.after-body")   # body on disk, no marker yet
            try:
                in_flight = read_bytes(path)
                self.assertGreater(len(in_flight), committed)
                with self.assertRaises(DatabaseBusy):
                    open_db(path)                                    # would run recovery
                self.assertEqual(read_bytes(path), in_flight)        # nothing cut
            finally:
                child.kill()                                         # SIGKILL drops the lock
            db, report = open_db(path)                              # now recovery may run
            self.assertEqual(report.bytes_cut, len(in_flight) - committed)
            self.assertEqual(db.get(b"k"), b"old")
            db.close()
            self.assertEqual(check(path), (True, "OK"))

    def test_cli_command_while_serve_runs_gets_err_database_busy(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            env = dict(os.environ)
            env.pop("KVDB_PAUSE_AT", None)
            kvdb = os.path.join(ROOT, "kvdb.py")
            serve = subprocess.Popen([sys.executable, kvdb, "serve", path], env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:                                          # never spin on EOF
                    line = serve.stderr.readline()
                    self.assertTrue(line, "serve exited before READY")
                    if line.startswith(b"READY"):
                        break
                serve.stdin.write(b"1 put hasib 20\n")
                serve.stdin.flush()
                self.assertEqual(serve.stdout.readline(), b"ACK 1\n")
                r = subprocess.run([sys.executable, kvdb, "get", path, "hasib"], env=env,
                                   capture_output=True, timeout=TIMEOUT)
                self.assertEqual(r.returncode, 3)
                self.assertTrue(r.stdout.startswith(b"ERR DatabaseBusy"), r.stdout)
                serve.stdin.write(b"2 put ali 30\n")                 # serve is not disturbed
                serve.stdin.flush()
                self.assertEqual(serve.stdout.readline(), b"ACK 2\n")
            finally:
                serve.kill()
                serve.wait(timeout=TIMEOUT)
                for f in (serve.stdin, serve.stdout, serve.stderr):
                    f.close()
            r = subprocess.run([sys.executable, kvdb, "get", path, "ali"], env=env, capture_output=True,
                               timeout=TIMEOUT)
            self.assertEqual(r.stdout, b"30\n")


if __name__ == "__main__":
    unittest.main()

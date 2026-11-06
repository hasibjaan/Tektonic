"""Tests for kvdb.py, the eight commands and serve (Phase 4)."""
import os
import signal
import subprocess
import sys
import threading
import unittest

from tests.kvtest_util import ROOT, WorkDir, read_bytes
from tools import readme_commands

KVDB = os.path.join(ROOT, "kvdb.py")


def kvdb(*args, cwd=None, stdin=None):
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    r = subprocess.run([sys.executable, KVDB, *args], cwd=cwd, input=stdin, env=env,
                       capture_output=True, timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


class Serve:
    """A running `kvdb.py serve` with binary pipes and a watchdog."""

    def __init__(self, path, env_extra=None):
        env = dict(os.environ)
        env.pop("KVDB_PAUSE_AT", None)
        env.update(env_extra or {})
        self.proc = subprocess.Popen([sys.executable, KVDB, "serve", path], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.watchdog = threading.Timer(120, self.proc.kill)
        self.watchdog.start()
        first = self.proc.stderr.readline().decode().split()
        assert first[0] == "PID", first
        self.pid = int(first[1])
        ready = self.proc.stderr.readline().decode()
        assert ready.startswith("READY "), ready

    def send(self, text):
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()

    def line(self):
        return self.proc.stdout.readline().decode().rstrip("\n")

    def stop(self):
        self.watchdog.cancel()
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except OSError:
                pass


class ReadmeExamplesTest(unittest.TestCase):
    def test_every_readme_example_matches_a_real_run(self):
        n, problems = readme_commands.compare()
        self.assertGreater(n, 20)
        self.assertEqual(problems, [], "\n".join(problems))


class ServeTest(unittest.TestCase):
    def test_ack_then_sigkill_by_pid_then_reopen(self):
        """The Phase 4 manual kill test, automated: ACK, kill -9 <PID>, reopen, get."""
        with WorkDir() as w:
            path = w.path("demo.kv")
            s = Serve(path)
            try:
                s.send("1 put hasib 20\n")
                self.assertEqual(s.line(), "ACK 1")
                os.kill(s.pid, signal.SIGKILL)            # the hard kill, by the PID serve printed
                s.proc.wait(timeout=30)
                self.assertEqual(s.proc.returncode, -signal.SIGKILL)
            finally:
                s.stop()
            self.assertEqual(kvdb("get", path, "hasib")[:2], (0, "20\n"))
            self.assertEqual(kvdb("check", path)[:2], (0, "OK\n"))

    def test_many_concurrent_requests_all_answered(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            s = Serve(path)
            try:
                s.send("".join("w1-{0} put k{0} v{0}\n".format(i) for i in range(200)))
                acks = {s.line() for _ in range(200)}
                self.assertEqual(acks, {"ACK w1-{}".format(i) for i in range(200)})
                s.send("".join("g{0} get k{0}\n".format(i) for i in range(0, 200, 7)))
                vals = {s.line() for _ in range(0, 200, 7)}
                self.assertEqual(vals, {"VAL g{0} v{0}".format(i) for i in range(0, 200, 7)})
                s.proc.stdin.close()                       # EOF: serve finishes and exits 0
                self.assertEqual(s.proc.wait(timeout=60), 0)
            finally:
                s.stop()

    def test_batch_and_errors(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            self.assertEqual(kvdb("open", path, "--max-bytes", "200")[0], 0)
            s = Serve(path)
            try:
                s.send("b1 batch 2\nput x 1\ndel x\n")
                self.assertEqual(s.line(), "ACK b1")
                s.send("b2 batch 2\nput y 1\nfrob\n")                 # bad line: whole batch refused
                self.assertTrue(s.line().startswith("ERR b2 BadToken"))
                s.send("c1 put big " + "x" * 300 + "\n")               # over the cap
                self.assertTrue(s.line().startswith("ERR c1 SizeCapReached"))
                s.send("g1 get y\ng2 get x\n")
                self.assertEqual(sorted([s.line(), s.line()]), ["NOTFOUND g1", "NOTFOUND g2"])
            finally:
                s.stop()

    def test_pause_point_in_serve_then_kill(self):                 # A4 through serve
        with WorkDir() as w:
            path = w.path("db.kv")
            s = Serve(path, {"KVDB_PAUSE_AT": "write.after-commit-sync"})
            try:
                s.send("1 put hasib 20\n")
                self.assertEqual(s.proc.stderr.readline().decode().strip(), "PAUSE write.after-commit-sync")
                s.proc.kill()                                          # no ACK was sent
                s.proc.wait(timeout=30)
                self.assertEqual(s.proc.stdout.read(), b"")
            finally:
                s.stop()
            self.assertEqual(kvdb("get", path, "hasib")[1], "20\n")   # durable anyway: A4


class CommandTest(unittest.TestCase):
    def test_one_megabyte_value_through_file(self):
        with WorkDir() as w:
            path, big, too_big = w.path("db.kv"), w.path("big.bin"), w.path("too-big.bin")
            with open(big, "wb") as f:
                f.write(b"A" * 1_048_576)
            with open(too_big, "wb") as f:
                f.write(b"A" * 1_048_577)
            self.assertEqual(kvdb("put", path, "big", "file:" + big)[:2], (0, "OK\n"))
            code, out, _ = kvdb("get", path, "big")
            self.assertEqual((code, len(out)), (0, 1_048_577))           # value + newline
            code, out, _ = kvdb("put", path, "big2", "file:" + too_big)
            self.assertEqual(code, 3)
            self.assertTrue(out.startswith("ERR BadToken"))

    def test_bad_batch_file_changes_nothing(self):
        with WorkDir() as w:
            path, ops = w.path("db.kv"), w.path("ops.txt")
            kvdb("put", path, "a", "1")
            before = read_bytes(path)
            with open(ops, "wb") as f:
                f.write(b"put b 2\nput c\n")
            code, out, _ = kvdb("batch", path, ops)
            self.assertEqual(code, 3)
            self.assertTrue(out.startswith("ERR BadToken: line 2"))
            self.assertEqual(read_bytes(path), before)

    def test_check_exit_codes_and_read_only(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            self.assertEqual(kvdb("check", path)[:2], (1, "NO_FILE\n"))
            kvdb("put", path, "a", "1")
            data = read_bytes(path)
            with open(path, "wb") as f:
                f.write(data[:-3])
            code, out, _ = kvdb("check", path)
            self.assertEqual(code, 1)
            self.assertTrue(out.startswith("TORN_TAIL at 16"))
            self.assertEqual(read_bytes(path), data[:-3])                # check never repairs

    def test_hex_keys_and_values_round_trip(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            self.assertEqual(kvdb("put", path, "hex:00ff20", "hex:0a0d00")[:2], (0, "OK\n"))
            self.assertEqual(kvdb("get", path, "hex:00ff20")[1], "hex:0a0d00\n")
            self.assertEqual(kvdb("dump", path, "--hex")[1], "hex:00ff20 hex:0a0d00\n")
            self.assertEqual(kvdb("put", path, "k", "hex:abc")[0], 3)    # odd number of digits

    def test_usage_error_is_exit_2(self):
        self.assertEqual(kvdb()[0], 2)
        self.assertEqual(kvdb("frobnicate", "x")[0], 2)


if __name__ == "__main__":
    unittest.main()

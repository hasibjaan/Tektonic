"""The torture tool itself (Fix 1): it must not PASS a run that tested nothing, and it
must FAIL on any reply it does not understand — never crash or ignore it."""
import os
import subprocess
import sys
import unittest

from tests.kvtest_util import ROOT, WorkDir


def torture(*args, fake_mode=None, workdir):
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    if fake_mode:
        env["FAKE_KVDB_MODE"] = fake_mode
    cmd = [sys.executable, os.path.join(ROOT, "torture.py"), "--workdir", workdir,
           "--failures-dir", os.path.join(workdir, "failures"), *args]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


FAKE = ["--kvdb", os.path.join(ROOT, "tester", "fake_kvdb.py")]


class TesterTest(unittest.TestCase):
    def test_silent_engine_is_inconclusive(self):
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "11", *FAKE, fake_mode="silent", workdir=w.dir)
            self.assertEqual(code, 3, out)
            self.assertIn("INCONCLUSIVE", out)
            self.assertNotIn("RESULT: PASS", out)

    def test_ack_but_never_store_fails(self):
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "12", *FAKE, fake_mode="ack", workdir=w.dir)
            self.assertEqual(code, 1, out)
            self.assertIn("FAIL at iteration 1", out)

    def test_garbage_output_is_a_protocol_failure(self):
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "13", *FAKE, fake_mode="garbage", workdir=w.dir)
            self.assertEqual(code, 1, out)
            self.assertIn("PROTOCOL FAILURE: reply line 'GARBAGE OUTPUT'", out)

    def test_err_with_unknown_id_fails_cleanly(self):
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "14", *FAKE, fake_mode="bad-err-id", workdir=w.dir)
            self.assertEqual(code, 1, out)
            self.assertIn("PROTOCOL FAILURE", out)
            self.assertNotIn("Traceback", out)                      # no thread crashed

    def test_real_engine_passes_with_coverage(self):
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "15", workdir=w.dir)
            self.assertEqual(code, 0, out)
            self.assertIn("RESULT: PASS", out)
            self.assertRegex(out, r"coverage: \d+ ACKs \(minimum 20\); ACKed by type: put \d+, del \d+, batch \d+")


class ReplyParserTest(unittest.TestCase):
    def test_strict_reply_parsing(self):
        from tester.modes import ProtocolError, parse_reply
        self.assertEqual(parse_reply("ACK w1-5"), ("ACK", "w1-5", 1, ""))
        self.assertEqual(parse_reply("ERR w2-9 SizeCapReached: full")[0:3], ("ERR", "w2-9", 2))
        self.assertEqual(parse_reply("VAL w3-1 hex:00")[3], "hex:00")
        for bad in ("GARBAGE OUTPUT", "ACK", "ACK w1-5 extra", "ERR w1-5", "ERR x nope", "ACK w1-1x",
                    "ACK w" + "9" * 5000 + "-1", "VAL w1-1", "OK w1-1", ""):
            with self.subTest(line=bad[:30]):
                with self.assertRaises(ProtocolError):
                    parse_reply(bad)


class ServeBoundTest(unittest.TestCase):
    def test_batch_count_with_thousands_of_digits_is_a_clean_err(self):
        with WorkDir() as w:
            env = dict(os.environ)
            env.pop("KVDB_PAUSE_AT", None)
            line = b"1 batch " + b"9" * 5000 + b"\n2 put a 1\n"
            r = subprocess.run([sys.executable, os.path.join(ROOT, "kvdb.py"), "serve", w.path("db.kv")],
                               input=line, env=env, capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = r.stdout.decode().splitlines()
            self.assertIn("ERR 1 BadToken: batch needs a count from 1 to 10000", out)
            self.assertIn("ACK 2", out)                             # serve kept working
            self.assertNotIn(b"Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()

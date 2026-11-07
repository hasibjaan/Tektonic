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


def read(path):
    with open(path, "rb") as f:
        return f.read()


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


class FailureEvidenceTest(unittest.TestCase):
    """Fix 7: a failure folder holds enough to look at the bug and judge it again."""

    def test_failure_folder_and_replay(self):
        import json
        with WorkDir() as w:
            code, out = torture("--iterations", "3", "--seed", "16", *FAKE, fake_mode="ack", workdir=w.dir)
            self.assertEqual(code, 1, out)
            folder = os.path.join(w.dir, "failures", "seed-16-iter-1")
            for name in ("meta.json", "oracle-state.json", "events.log", "oracle.log", "trace.log", "report.txt"):
                self.assertTrue(os.path.exists(os.path.join(folder, name)), name)
            with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual((meta["seed"], meta["iteration"], meta["mode"]), (16, 1, "default"))
            for field in ("engine_commit", "python", "platform", "problems"):
                self.assertTrue(meta[field], field)
            self.assertIn("kill_delay_ms", meta["kill"])
            with open(os.path.join(folder, "events.log"), "rb") as f:
                events = f.read()
            self.assertIn(b" SEND w", events)                       # the full requests
            self.assertIn(b" RECV ACK w", events)                   # and the replies
            self.assertIn("replay: python3 torture.py --replay", out)
            # the lie is still a lie when judged again, with the fake or with the real engine
            for engine in (FAKE, []):
                with self.subTest(engine=engine[1:] or "kvdb.py"):
                    code, out = torture("--replay", folder, *engine, workdir=w.dir)
                    self.assertEqual(code, 1, out)
                    self.assertIn("REPRODUCED", out)

    def test_temporary_files_are_copied_before_recovery(self):
        from tester.modes import copy_before_recovery
        with WorkDir() as w:
            for name, data in (("db.kv", b"KVDB"), ("db.kv.compact.tmp", b"half"), ("db.kv.lock", b"")):
                with open(w.path(name), "wb") as f:
                    f.write(data)
            after_kill, side = copy_before_recovery(w.dir, w.path("db.kv"))
            self.assertEqual(read(after_kill), b"KVDB")
            self.assertEqual([os.path.basename(s) for s in side], ["db.kv.compact.tmp.after-kill"])
            self.assertEqual(read(side[0]), b"half")

    def test_event_log_keeps_the_most_recent_requests_in_full(self):
        import hashlib
        from tester.workload import EventLog
        e = EventLog()
        e.FULL_BYTES = 100
        reqs = [b"w1-%d put k hex:%s\n" % (i, b"ab" * 20) for i in range(5)]   # about 52 bytes each
        for r in reqs:
            e.send(r)
        e.recv("ACK w1-4")
        items = e.snapshot()
        self.assertEqual([k for _, k, _ in items], [b"SEND-SUMMARY"] * 4 + [b"SEND", b"RECV"])
        self.assertEqual(items[4][2], reqs[4])                     # the last request, in full
        self.assertIn(hashlib.sha256(reqs[0]).hexdigest().encode(), items[0][2])
        self.assertEqual(items[5][2], b"ACK w1-4\n")

    def test_oracle_state_round_trips_through_json(self):
        import json
        from tester.oracle import Oracle, OverlapOracle, oracle_from_json, oracle_to_json
        with WorkDir() as w:
            o = Oracle(w.path("a.log"), {1: [b"k1", b"k2"], 2: [b"k3"]})
            o.sent(1, "w1-1", [("put", b"k1", b"\x00v")], "put k1")
            o.acked(1, "w1-1")
            o.sent(1, "w1-2", [("put", b"k2", b""), ("del", b"k1", None)], "batch 2")
            back = oracle_from_json(json.loads(json.dumps(oracle_to_json(o))), w.path("b.log"))
            self.assertEqual(back.models[1].state, {b"k1": b"\x00v"})
            self.assertEqual(back.models[1].pending, o.models[1].pending)
            self.assertEqual(back.verify({b"k2": b""}), [])         # the batch applied as a whole
            o.log.close()
            back.log.close()
            v = OverlapOracle(w.path("c.log"), [1, 2], [b"s1"])
            v.sent(1, "w1-1", [("put", b"s1", b"x")], "put s1")
            back = oracle_from_json(json.loads(json.dumps(oracle_to_json(v))), w.path("d.log"))
            self.assertEqual(back.ops, v.ops)
            self.assertNotEqual(back.verify({b"s1": b"y"}), [])     # a value nobody wrote
            v.log.close()
            back.log.close()


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

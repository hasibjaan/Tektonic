"""Tests for the torture tool's oracle (Phase 5), with hand-made dumps — no engine involved."""
import os
import unittest

from tester.oracle import Oracle
from tests.kvtest_util import WorkDir

K = {1: [b"w1k0", b"w1k1"], 2: [b"w2k0"]}


def oracle(w):
    return Oracle(w.path("oracle.log"), K)


def put(k, v):
    return ("put", k, v)


class OracleTest(unittest.TestCase):
    def test_acked_state_must_match_exactly(self):
        with WorkDir() as w:
            o = oracle(w)
            o.sent(1, "w1-1", [put(b"w1k0", b"a")], "put")
            o.acked(1, "w1-1")
            self.assertEqual(o.verify({b"w1k0": b"a"}), [])
            self.assertTrue(o.verify({}))                                    # ACKed put missing

    def test_unanswered_request_may_be_present_or_absent_but_whole(self):
        with WorkDir() as w:
            batch = [put(b"w1k0", b"x"), put(b"w1k1", b"y")]
            for dump, ok in (({}, True), ({b"w1k0": b"x", b"w1k1": b"y"}, True),
                             ({b"w1k0": b"x"}, False), ({b"w1k1": b"y"}, False)):
                o = Oracle(os.path.join(w.dir, "o%d.log" % len(dump)), K)
                o.sent(1, "w1-1", batch, "batch")
                problems = o.verify(dump)
                self.assertEqual(problems == [], ok, (dump, problems))
                if not ok:
                    self.assertTrue(any("partly applied" in p or "w1-1" in p for p in problems), problems)

    def test_err_means_absent(self):
        with WorkDir() as w:
            o = oracle(w)
            o.sent(1, "w1-1", [put(b"w1k0", b"a")], "put")
            o.erred(1, "w1-1", "SizeCapReached: full")
            self.assertTrue(o.verify({b"w1k0": b"a"}))                       # ERR'd write is present: FAIL

    def test_unknown_key_and_unknown_value(self):
        with WorkDir() as w:
            o = oracle(w)
            self.assertTrue(any("never written" in p for p in o.verify({b"other": b"1"})))
            o2 = Oracle(w.path("o2.log"), K)
            o2.sent(2, "w2-1", [put(b"w2k0", b"real")], "put")
            o2.acked(2, "w2-1")
            self.assertTrue(o2.verify({b"w2k0": b"mixed"}))                 # a value nobody sent

    def test_pass_narrows_but_never_copies(self):
        with WorkDir() as w:
            o = oracle(w)
            o.sent(1, "w1-1", [put(b"w1k0", b"a")], "put")
            self.assertEqual(o.verify({b"w1k0": b"a"}), [])                  # decided: applied
            self.assertEqual(o.models[1].state, {b"w1k0": b"a"})
            self.assertIsNone(o.models[1].pending)
            self.assertTrue(o.verify({}))                                    # now it must stay
            o2 = Oracle(w.path("o2.log"), K)
            o2.sent(1, "w1-1", [put(b"w1k0", b"a")], "put")
            self.assertEqual(o2.verify({}), [])                              # decided: absent
            self.assertTrue(o2.verify({b"w1k0": b"a"}))                      # cannot appear later

    def test_ack_for_a_request_not_in_flight_is_an_error(self):
        with WorkDir() as w:
            o = oracle(w)
            with self.assertRaises(AssertionError):
                o.acked(1, "w1-9")
            o.sent(1, "w1-1", [put(b"w1k0", b"a")], "put")
            with self.assertRaises(AssertionError):
                o.sent(1, "w1-2", [put(b"w1k0", b"b")], "put")               # two in flight for one writer

    def test_log_records_send_before_reply(self):
        with WorkDir() as w:
            o = oracle(w)
            o.sent(1, "w1-1", [put(b"w1k0", b"a")], "put w1k0")
            o.acked(1, "w1-1")
            with open(w.path("oracle.log"), encoding="utf-8") as f:
                self.assertEqual(f.read().splitlines(), ["SEND w1-1 put w1k0", "ACK w1-1"])


if __name__ == "__main__":
    unittest.main()

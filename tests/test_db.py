"""Engine tests in one process (Phase 3): cuts, threads, refusals, rollback, check."""
import os
import threading
import unittest

from src.kv import (CorruptFile, DatabaseFailed, InvalidInput, NotOurFile, SizeCapReached,
                    check, layout, open_db)
from tests.kvtest_util import WorkDir, read_bytes, write_bytes


def opened(path, max_bytes=0):
    return open_db(path, max_bytes)[0]


def three_puts(path):
    """A file with hasib=20, ali=30, sara=40 as three single-put transactions.
    Returns the offset where the third transaction starts."""
    with opened(path) as db:
        db.put(b"hasib", b"20")
        db.put(b"ali", b"30")
        third = os.path.getsize(path)
        db.put(b"sara", b"40")
    return third


class FiveCutTest(unittest.TestCase):
    """Phase 3 test 1: cut inside the third transaction's header, key, value,
    marker, and only the trailer byte (F4)."""

    def test_five_cuts(self):
        with WorkDir() as w:
            original = w.path("orig.kv")
            third = three_puts(original)
            full = read_bytes(original)
            # third txn: header 3 @+0, op header 7 @+3, key 'sara' 4 @+10, value '40' 2 @+14,
            # marker 16 @+16, trailer 1 @+32  (CLAUDE.md section 8)
            cuts = {"header": third + 2, "key": third + 12, "value": third + 15,
                    "marker": third + 20, "trailer only": len(full) - 1}
            for name, cut in cuts.items():
                with self.subTest(cut=name):
                    path = w.path(name.replace(" ", "-") + ".kv")
                    write_bytes(path, full[:cut])
                    db, report = open_db(path)
                    self.assertEqual(db.get(b"hasib"), b"20")
                    self.assertEqual(db.get(b"ali"), b"30")
                    if name == "trailer only":
                        self.assertEqual(db.get(b"sara"), b"40")          # F4: nothing lost
                        self.assertTrue(report.trailer_restored)
                        self.assertEqual(report.bytes_cut, 0)
                    else:
                        self.assertIsNone(db.get(b"sara"))                 # torn: not applied
                        self.assertEqual(report.bytes_cut, cut - third)
                    db.put(b"new", b"1")
                    db.close()
                    self.assertEqual(check(path), (True, "OK"))
                    with opened(path) as db:
                        self.assertEqual(db.get(b"new"), b"1")
                        self.assertEqual(db.get(b"hasib"), b"20")
                        self.assertEqual(db.get(b"sara"), b"40" if name == "trailer only" else None)


class BatchCutTest(unittest.TestCase):
    """Phase 3 test 2: a batch cut at every byte position is none or all, never some."""

    def every_cut(self, ops, keys):
        with WorkDir() as w:
            base = w.path("base.kv")
            with opened(base) as db:
                db.put(b"before", b"x")
            start = os.path.getsize(base)
            with opened(base) as db:
                db.batch(ops)
            full = read_bytes(base)
            for cut in range(start, len(full) + 1):
                path = w.path("cut.kv")
                write_bytes(path, full[:cut])
                with opened(path) as db:
                    seen = [db.get(k) is not None for k in keys]
                    self.assertEqual(db.get(b"before"), b"x")
                self.assertIn(seen, ([False] * len(keys), [True] * len(keys)), "cut at {}".format(cut))
                # all present only once the marker is complete
                self.assertEqual(all(seen), cut >= len(full) - 1, "cut at {}".format(cut))
                os.remove(path)

    def test_two_op_batch(self):
        self.every_cut([("put", b"hasib", b"20"), ("put", b"ali", b"30")], [b"hasib", b"ali"])

    def test_ten_op_batch(self):                                           # B1, B2
        keys = [b"b%d" % i for i in range(10)]
        self.every_cut([("put", k, b"v" + k) for k in keys], keys)


class SemanticsTest(unittest.TestCase):
    def test_put_then_delete_in_one_batch(self):                           # Phase 3 test 3, B5
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                db.put(b"x", b"0")
                db.batch([("put", b"x", b"1"), ("del", b"x", None)])
                self.assertIsNone(db.get(b"x"))
                db.batch([("del", b"y", None), ("put", b"y", b"2")])
                self.assertEqual(db.get(b"y"), b"2")
            with opened(path) as db:
                self.assertIsNone(db.get(b"x"))
                self.assertEqual(db.get(b"y"), b"2")

    def test_delete_missing_key_succeeds(self):
        with WorkDir() as w:
            with opened(w.path("db.kv")) as db:
                db.delete(b"never-written")
                self.assertIsNone(db.get(b"never-written"))

    def test_empty_value_is_not_missing(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                db.put(b"e", b"")
                self.assertEqual(db.get(b"e"), b"")
            with opened(path) as db:
                self.assertEqual(db.get(b"e"), b"")
                self.assertIsNone(db.get(b"missing"))

    def test_large_value_round_trip(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            big = os.urandom(layout.MAX_VALUE)
            with opened(path) as db:
                db.put(b"big", big)
            with opened(path) as db:
                self.assertEqual(db.get(b"big"), big)

    def test_invalid_input_writes_nothing(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                db.put(b"a", b"1")
                size = os.path.getsize(path)
                for bad in ([(1, "str-key", b"v")], [("put", b"", b"v")], [("put", b"k", b"v" * (layout.MAX_VALUE + 1))],
                            [], [("put", b"ok", b"v"), ("put", b"k" * 1025, b"v")], [("frob", b"k", b"v")]):
                    with self.assertRaises(InvalidInput):
                        db.batch(bad)
                with self.assertRaises(InvalidInput):
                    db.put("hasib", b"20")
                with self.assertRaises(InvalidInput):
                    db.get("hasib")
                self.assertEqual(os.path.getsize(path), size)
                self.assertEqual(db.get(b"a"), b"1")


class ThreadTest(unittest.TestCase):
    def test_two_threads_different_keys(self):                             # Phase 3 test 4
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                def writer(t):
                    for i in range(1000):
                        db.put(b"t%d-%d" % (t, i), b"v%d-%d" % (t, i))
                threads = [threading.Thread(target=writer, args=(t,)) for t in (1, 2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            with opened(path) as db:
                self.assertEqual(len(db), 2000)
                for t in (1, 2):
                    for i in range(1000):
                        self.assertEqual(db.get(b"t%d-%d" % (t, i)), b"v%d-%d" % (t, i))

    def test_two_threads_same_key(self):                                   # Phase 3 test 5
        with WorkDir() as w:
            path = w.path("db.kv")
            written = {b"A" * 300 + b"%d" % i for i in range(200)} | {b"B" * 500 + b"%d" % i for i in range(200)}
            with opened(path) as db:
                def writer(letter, n):
                    for i in range(200):
                        db.put(b"same", letter * n + b"%d" % i)
                threads = [threading.Thread(target=writer, args=a) for a in ((b"A", 300), (b"B", 500))]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                live = db.get(b"same")
            self.assertIn(live, written)
            with opened(path) as db:
                self.assertEqual(db.get(b"same"), live)

    def test_reader_never_sees_inside_a_batch(self):                       # Phase 3 test 6, D4
        with WorkDir() as w:
            with opened(w.path("db.kv")) as db:
                stop = threading.Event()
                seen = set()

                def reader():
                    while not stop.is_set():
                        seen.add(db.get(b"s"))

                readers = [threading.Thread(target=reader) for _ in range(3)]
                for r in readers:
                    r.start()
                for i in range(300):
                    db.batch([("put", b"s", b"FORBIDDEN"), ("put", b"s", b"final")])
                stop.set()
                for r in readers:
                    r.join()
            self.assertNotIn(b"FORBIDDEN", seen)
            self.assertIn(b"final", seen)


class OpenTest(unittest.TestCase):
    def test_interrupted_creation(self):                                   # Phase 3 test 7
        with WorkDir() as w:
            for n in (0, 5, 8, 15):
                path = w.path("short%d.kv" % n)
                write_bytes(path, layout.encode_header(0)[:n])
                self.assertEqual(check(path), (False, "INTERRUPTED_CREATE ({} bytes)".format(n)))
                db, report = open_db(path)
                self.assertEqual((len(db), report.created), (0, True))
                db.put(b"k", b"v")
                db.close()
                self.assertEqual(check(path), (True, "OK"))

    def test_not_our_file_is_left_untouched(self):
        with WorkDir() as w:
            good = layout.encode_header(0) + layout.encode_txn([(1, b"k", b"v")])
            cases = {"text.kv": b"hello worl",                              # short, not a prefix
                     "magic.kv": b"XXXX" + good[4:],
                     "version.kv": good[:4] + b"\x02\x00" + good[6:],
                     "flags.kv": good[:6] + b"\x01\x00" + good[8:]}
            for name, data in cases.items():
                with self.subTest(case=name):
                    path = w.path(name)
                    write_bytes(path, data)
                    with self.assertRaises(NotOurFile):
                        open_db(path)
                    self.assertEqual(read_bytes(path), data)
                    self.assertTrue(check(path)[1].startswith("NOT_OUR_FILE"))

    def test_corrupt_file_is_refused_and_untouched(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                db.put(b"a", b"1")
                db.put(b"b", b"2")
            data = bytearray(read_bytes(path))
            data[16 + 10] ^= 0xFF                      # first key byte: crc32 no longer matches
            write_bytes(path, bytes(data))
            with self.assertRaises(CorruptFile):
                open_db(path)
            self.assertEqual(read_bytes(path), bytes(data))
            ok, reason = check(path)
            self.assertFalse(ok)
            # txn 1 = put a 1: body at 16-27 (3 + 7 + 1 + 1 bytes), marker at 28, crc32 field at 28 + 12 = 40
            self.assertTrue(reason.startswith("CORRUPT at 40:"), reason)

    def test_wrong_byte_after_marker_is_corrupt(self):                   # decision 2: refuse
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path) as db:
                db.put(b"a", b"1")
            data = bytearray(read_bytes(path))
            data[-1] = 0xA1
            write_bytes(path, bytes(data) + layout.encode_txn([(1, b"b", b"2")]))
            with self.assertRaises(CorruptFile):
                open_db(path)

    def test_check_never_modifies(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            three = three_puts(path)
            full = read_bytes(path)
            for cut, expect in ((len(full), "OK"), (len(full) - 1, "MISSING_TRAILER at {}".format(len(full) - 1)),
                                (three + 5, "TORN_TAIL at {}, 5 bytes".format(three))):
                write_bytes(path, full[:cut])
                before = (read_bytes(path), os.stat(path).st_mtime_ns)
                self.assertEqual(check(path)[1], expect)
                self.assertEqual((read_bytes(path), os.stat(path).st_mtime_ns), before)
            self.assertEqual(check(w.path("absent.kv")), (False, "NO_FILE"))

    def test_recovery_is_repeatable(self):                                # F2 in one process
        with WorkDir() as w:
            path = w.path("db.kv")
            three = three_puts(path)
            write_bytes(path, read_bytes(path)[:three + 9])
            open_db(path)[0].close()
            once = read_bytes(path)
            open_db(path)[0].close()
            self.assertEqual(read_bytes(path), once)
            self.assertEqual(len(once), three)


class CapTest(unittest.TestCase):
    def test_put_and_batch_over_the_cap_change_nothing(self):             # E1, E2 (no crash)
        with WorkDir() as w:
            path = w.path("db.kv")
            with opened(path, max_bytes=16 + 34 + 33) as db:                # room for 2 small puts
                db.put(b"hasib", b"20")                                      # 34 bytes
                db.put(b"ali", b"300")                                       # 33 bytes: exactly full
                self.assertEqual(os.path.getsize(path), 16 + 34 + 33)        # appended, NOT compacted:
                # a write that fits exactly must not trigger E5 (the cap test is end + size > max_bytes)
                before = read_bytes(path)
                # Since Phase 7 (E5) a write that does not fit first tries compaction; these do not
                # fit even compacted (one batch: 16 + 20 + 14 + 13 + ...), so they are refused:
                with self.assertRaises(SizeCapReached):
                    db.put(b"x", b"v" * 40)                                  # compacted: 111 > 83
                with self.assertRaises(SizeCapReached):
                    db.batch([("put", b"y", b"1" * 20), ("put", b"z", b"2" * 20)])   # 119 > 83
                self.assertEqual(read_bytes(path), before)                   # refused = file unchanged
                self.assertEqual((db.get(b"hasib"), db.get(b"y")), (b"20", None))
            with opened(path) as db:                                       # cap survives a reopen
                with self.assertRaises(SizeCapReached):
                    db.put(b"x", b"v" * 40)

    def test_cap_is_fixed_at_creation(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            opened(path, max_bytes=1000).close()
            with self.assertRaises(InvalidInput):
                open_db(path, max_bytes=2000)
            opened(path, max_bytes=1000).close()
            with self.assertRaises(InvalidInput):
                open_db(w.path("tiny.kv"), max_bytes=10)
            self.assertFalse(os.path.exists(w.path("tiny.kv")))


class RollbackTest(unittest.TestCase):
    def test_failed_write_is_rolled_back(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db = opened(path)
            db.put(b"a", b"1")
            size = os.path.getsize(path)
            real_write = db._w.write
            calls = []

            def failing_write(data):
                calls.append(1)
                if len(calls) == 2:                        # after the txn header went out
                    raise OSError(28, "No space left on device (injected)")
                return real_write(data)

            db._w.write = failing_write
            with self.assertRaises(OSError):
                db.put(b"b", b"2")
            db._w.write = real_write
            self.assertEqual(os.path.getsize(path), size)  # truncated back to E
            db.put(b"c", b"3")                             # still usable
            db.close()
            with opened(path) as db:
                self.assertEqual((db.get(b"a"), db.get(b"b"), db.get(b"c")), (b"1", None, b"3"))

    def test_failed_rollback_marks_the_database_failed(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db = opened(path)
            db.put(b"a", b"1")

            def broken(*args):
                raise OSError(5, "I/O error (injected)")

            db._w.write = broken
            db._w.truncate = broken
            with self.assertRaises(DatabaseFailed):
                db.put(b"b", b"2")
            with self.assertRaises(DatabaseFailed):
                db.put(b"c", b"3")                         # every later write refused
            self.assertEqual(db.get(b"a"), b"1")           # reads still work
            db.close()
            with opened(path) as db:                       # a reopen recovers
                self.assertEqual(db.get(b"a"), b"1")
                db.put(b"d", b"4")


if __name__ == "__main__":
    unittest.main()

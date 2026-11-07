"""Compaction (CLAUDE.md section 11) and the size cap, E1-E5 (Phase 7)."""
import os
import random
import re
import unittest

from src.kv import CompactionFailed, SizeCapReached, check, open_db, recovery
from tests.kvtest_util import WorkDir, read_bytes, write_bytes
from tests.test_crash import kill_at

TMP = recovery.COMPACT_TMP_SUFFIX


def fill(path, n=300, rounds=4, max_bytes=0):
    """n keys, each overwritten `rounds` times (so there is garbage to remove)."""
    db, _ = open_db(path, max_bytes=max_bytes)
    for r in range(rounds):
        for i in range(n):
            db.put(b"k%04d" % i, b"r%d-%d" % (r, i))
    db.delete(b"k0000")
    state = {k: db.get(k) for k in db.keys()}
    db.close()
    return state


def state_of(path):
    with open_db(path)[0] as db:
        return {k: db.get(k) for k in db.keys()}


class CompactTest(unittest.TestCase):
    def test_keeps_exactly_the_live_state(self):
        rng = random.Random(77)
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path)
            for _ in range(3000):
                k = b"k%d" % rng.randrange(200)
                if rng.random() < 0.75:
                    db.put(k, os.urandom(rng.randrange(0, 50)))
                else:
                    db.delete(k)
            before_state = {k: db.get(k) for k in db.keys()}
            size_before, size_after = db.compact()
            self.assertLess(size_after, size_before)
            self.assertEqual({k: db.get(k) for k in db.keys()}, before_state)   # same handles, new file
            db.put(b"new", b"1")
            db.close()
            self.assertEqual(check(path), (True, "OK"))
            self.assertFalse(os.path.exists(path + TMP))
            before_state[b"new"] = b"1"
            self.assertEqual(state_of(path), before_state)

    def test_empty_database_compacts_to_a_header(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path)
            db.put(b"a", b"1")
            db.delete(b"a")
            self.assertEqual(db.compact()[1], 16)
            db.close()
            self.assertEqual(state_of(path), {})

    def test_large_values_are_split_into_batches(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path)
            big = {b"b%d" % i: os.urandom(1_048_576) for i in range(70)}   # 70 MB > one 64 MiB batch
            for k, v in big.items():
                db.put(k, v)
            db.compact()
            db.close()
            scan, index, _ = recovery.read_index(path)            # the file itself
            self.assertEqual(scan.kind, recovery.CLEAN)
            self.assertEqual(scan.transactions, 2)                 # 64 MiB limit: 64 values, then 6
            with open_db(path)[0] as db:
                self.assertTrue(all(db.get(k) == v for k, v in big.items()))


class CompactKillTest(unittest.TestCase):
    """C3, C4, C5: a hard kill (SIGKILL) at each compaction pause point."""

    def test_kill_at_every_compaction_pause_point(self):
        for point in ("compact.mid-write", "compact.before-swap", "compact.after-swap", "compact.before-cleanup"):
            with self.subTest(point=point), WorkDir() as w:
                path = w.path("db.kv")
                state = fill(path)
                original = read_bytes(path)
                kill_at(path, "compact", point)
                swapped = point in ("compact.after-swap", "compact.before-cleanup")
                self.assertEqual(os.path.exists(path + TMP), not swapped)   # temp file until the swap
                if swapped:
                    self.assertLess(os.path.getsize(path), len(original))       # the new file is in place
                else:
                    self.assertEqual(read_bytes(path), original)               # old file untouched (C3)
                self.assertEqual(state_of(path), state)                         # all data intact either way
                self.assertFalse(os.path.exists(path + TMP))                    # leftover removed at open
                with open_db(path)[0] as db:
                    db.put(b"after", b"1")
                state[b"after"] = b"1"
                self.assertEqual(state_of(path), state)
                self.assertEqual(check(path), (True, "OK"))

    def test_stale_complete_temp_file_is_never_trusted(self):
        with WorkDir() as w:
            path, other = w.path("db.kv"), w.path("other.kv")
            state = fill(path, n=20)
            fill(other, n=5)
            write_bytes(path + TMP, read_bytes(other))       # a complete, VALID database file
            self.assertEqual(state_of(path), state)          # the original wins
            self.assertFalse(os.path.exists(path + TMP))


class CompactFailureTest(unittest.TestCase):
    def test_disk_full_during_write_leaves_the_original_working(self):     # E4 (real disk full)
        with WorkDir() as w:
            path = w.path("db.kv")
            state = fill(path, n=50)
            db, _ = open_db(path)
            original = read_bytes(path)
            real_open = open
            calls = []

            class Full:
                def __init__(self, f):
                    self.f = f

                def write(self, data):
                    calls.append(1)
                    if len(calls) > 3:
                        raise OSError(28, "No space left on device (injected)")
                    return self.f.write(data)

                def __getattr__(self, name):
                    return getattr(self.f, name)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return self.f.__exit__(*a)

            import builtins
            from src.kv import db as dbmod
            dbmod.open = lambda p, mode="r", **kw: Full(real_open(p, mode, **kw)) if p.endswith(TMP) else real_open(p, mode, **kw)
            try:
                with self.assertRaises(CompactionFailed):
                    db.compact()
            finally:
                del dbmod.open
            self.assertFalse(os.path.exists(path + TMP))
            self.assertEqual(read_bytes(path), original)
            self.assertEqual({k: db.get(k) for k in db.keys()}, state)   # still usable
            db.put(b"after", b"1")
            db.close()
            state[b"after"] = b"1"
            self.assertEqual(state_of(path), state)

    def test_a_new_file_that_fails_validation_is_not_used(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            state = fill(path, n=30)
            db, _ = open_db(path)
            original = read_bytes(path)
            real = recovery.read_index

            def wrong(p):
                scan, index, size = real(p)
                index.pop(next(iter(index)))                 # pretend a key went missing
                return scan, index, size

            recovery.read_index = wrong
            try:
                with self.assertRaises(CompactionFailed):
                    db.compact()
            finally:
                recovery.read_index = real
            self.assertEqual(read_bytes(path), original)
            self.assertFalse(os.path.exists(path + TMP))
            db.close()
            self.assertEqual(state_of(path), state)


class CapTest(unittest.TestCase):
    """E1-E5. A put costs 27 + key + value bytes (README "On-disk format")."""

    def test_e1_single_put_over_the_cap(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, max_bytes=16 + 3 * 37)     # room for 3 puts of key 5 + value 5
            for i in range(3):
                db.put(b"key%02d" % i, b"value")
            before = read_bytes(path)
            # Even compacted (one batch: 16 + 20 + 3*17 + 7+5+100 = 199 bytes) this does not fit:
            with self.assertRaises(SizeCapReached):
                db.put(b"key99", b"v" * 100)
            self.assertEqual(read_bytes(path), before)
            self.assertEqual(db.get(b"key00"), b"value")      # still readable, data intact
            db.close()

    def test_e2_batch_over_the_cap(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, max_bytes=200)
            db.put(b"a", b"1")
            before = read_bytes(path)
            with self.assertRaises(SizeCapReached):
                db.batch([("put", b"b%d" % i, b"x" * 20) for i in range(10)])
            self.assertEqual(read_bytes(path), before)
            self.assertEqual([db.get(b"b%d" % i) for i in range(10)], [None] * 10)
            db.close()

    def test_e3_cap_reached_then_killed(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            open_db(path, max_bytes=2000)[0].close()
            kill_at(path, "fill", "", expect_line="DONE")     # wrote until refused, then SIGKILL
            db, report = open_db(path)
            n = len(db)
            self.assertGreater(n, 10)
            self.assertTrue(all(db.get(b"f%06d" % i) == b"x" * 20 for i in range(n)))
            self.assertLessEqual(os.path.getsize(path), 2000)
            db.close()
            self.assertEqual(check(path), (True, "OK"))

    def test_e4_compaction_at_the_cap(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, max_bytes=3000)
            i = 0
            while True:                                       # overwrite 5 keys until the file is full
                try:
                    if os.path.getsize(path) > 3000 - 40:
                        break
                    db.put(b"k%d" % (i % 5), b"v%d" % i)
                except SizeCapReached:
                    break
                i += 1
            state = {k: db.get(k) for k in db.keys()}
            db.compact()                                      # completes
            self.assertEqual({k: db.get(k) for k in db.keys()}, state)
            db.close()
            self.assertEqual(state_of(path), state)
            open_db(path, max_bytes=3000)[0].close()          # the compacted file kept max_bytes = 3000

    def test_e5_deletes_free_space_and_writes_succeed_again(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            cap = 16 + 20 * 47                                 # 20 puts of key 10 + value 10
            db, _ = open_db(path, max_bytes=cap)
            for i in range(20):
                db.put(b"key%07d" % i, b"val%07d" % i)
            big = b"B" * 400
            with self.assertRaises(SizeCapReached):           # even compacted: 16 + 20 + 20*27 + 417 = 993 > 956
                db.put(b"big0000000", big)
            for i in range(10):
                db.delete(b"key%07d" % i)                    # the first one does not fit: E5 compacts with it
            self.assertLessEqual(os.path.getsize(path), cap)
            db.put(b"big0000000", big)                        # succeeds now, no manual step
            for i in range(3):
                db.put(b"new%07d" % i, b"val%07d" % i)
            expected = {b"key%07d" % i: b"val%07d" % i for i in range(10, 20)}
            expected[b"big0000000"] = big
            expected.update({b"new%07d" % i: b"val%07d" % i for i in range(3)})
            self.assertEqual({k: db.get(k) for k in db.keys()}, expected)
            db.close()
            self.assertEqual(state_of(path), expected)
            self.assertLessEqual(os.path.getsize(path), cap)


class E5FailureAfterSwapTest(unittest.TestCase):
    """Final fix round, Fix 9: on the E5 path os.replace is the write's commit point.
    A failure AFTER it returns DatabaseFailed although the write IS on disk (allowed by
    rule 2: no success was returned); a failure AT it leaves the old file and no write.
    Errors are injected with unittest.mock: a real failing folder fsync was not produced."""

    def _full(self, path):
        cap = 16 + 20 * 47                                     # exactly 20 puts of key 10 + value 10
        db, _ = open_db(path, max_bytes=cap)
        for i in range(20):
            db.put(b"key%07d" % i, b"val%07d" % i)
        return db

    def test_folder_fsync_fails_after_the_swap(self):
        from unittest import mock
        from src.kv import DatabaseFailed
        with WorkDir() as w:
            path = w.path("db.kv")
            db = self._full(path)
            with mock.patch.object(recovery, "fsync_dir", side_effect=OSError(5, "injected")):
                with self.assertRaises(DatabaseFailed):
                    db.put(b"key0000003", b"NEW0000003")      # does not fit: E5 compacts WITH it
            with self.assertRaises(DatabaseFailed):
                db.put(b"key0000004", b"x")                  # refused until reopen
            db.close()
            self.assertFalse(os.path.exists(path + TMP))
            self.assertEqual(check(path), (True, "OK"))
            after = state_of(path)
            self.assertEqual(after[b"key0000003"], b"NEW0000003")   # the change IS on disk
            self.assertEqual(len(after), 20)

    def test_the_swap_itself_fails(self):
        from unittest import mock
        from src.kv import DatabaseFailed
        with WorkDir() as w:
            path = w.path("db.kv")
            db = self._full(path)
            before = read_bytes(path)
            with mock.patch("src.kv.db.os.replace", side_effect=OSError(5, "injected")):
                with self.assertRaises(DatabaseFailed):
                    db.put(b"key0000003", b"NEW0000003")
            db.close()
            self.assertEqual(read_bytes(path), before)             # the original, byte for byte
            self.assertEqual(state_of(path)[b"key0000003"], b"val0000003")   # the write is absent


class ShrinkTest(unittest.TestCase):
    def test_puts_then_deletes_then_compact_is_empty(self):          # the brief's shrink test, small
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path)
            db.batch([("put", b"k%05d" % i, b"v%05d" % i) for i in range(10_000)])
            db.batch([("del", b"k%05d" % i, None) for i in range(10_000)])
            full = os.path.getsize(path)
            self.assertEqual(db.compact(), (full, 16))
            db.close()
            with open_db(path)[0] as db:
                self.assertEqual(len(db), 0)
                db.put(b"k", b"v")
            self.assertEqual(state_of(path), {b"k": b"v"})


if __name__ == "__main__":
    unittest.main()

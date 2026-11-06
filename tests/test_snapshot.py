"""Tests for the index snapshot <path>.idx (Phase 6, step d). The snapshot is only a cache:
with or without it, open must produce exactly the same index."""
import os
import random
import shutil
import unittest

from src.kv import open_db, recovery, snapshot
from tests.kvtest_util import WorkDir, read_bytes, write_bytes
from tests.test_crash import kill_at


def full_replay_index(path):
    """The index a full log replay gives, ignoring any snapshot (snapshot file moved away)."""
    idx = snapshot.path_for(path)
    moved = idx + ".away"
    if os.path.exists(idx):
        os.replace(idx, moved)
    try:
        return recovery.recover(path).index
    finally:
        if os.path.exists(moved):
            os.replace(moved, idx)


class SnapshotTest(unittest.TestCase):
    def test_written_and_used(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=100)
            for i in range(250):
                db.put(b"k%d" % i, b"v%d" % i)
            db.close()
            self.assertTrue(os.path.exists(snapshot.path_for(path)))
            db, report = open_db(path)
            self.assertTrue(report.snapshot)
            self.assertEqual([db.get(b"k%d" % i) for i in range(250)], [b"v%d" % i for i in range(250)])
            db.close()

    def test_equivalent_to_full_replay_with_random_workload(self):
        rng = random.Random(1234)
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=37)
            for _ in range(1500):
                k = b"k%d" % rng.randrange(60)
                r = rng.random()
                if r < 0.5:
                    db.put(k, b"v%d" % rng.randrange(10**6))
                elif r < 0.7:
                    db.delete(k)
                else:
                    ops = []
                    for _ in range(rng.randint(2, 6)):
                        key = b"k%d" % rng.randrange(60)
                        ops.append(("put", key, b"b%d" % rng.randrange(10**6)) if rng.random() < 0.7
                                   else ("del", key, None))
                    db.batch(ops)
            live = {k: db.get(k) for k in db.keys()}
            db.close()
            rec = recovery.recover(path)
            self.assertTrue(rec.report.snapshot)
            self.assertLess(rec.replayed, 1500)                       # really skipped part of the log
            self.assertEqual(rec.index, full_replay_index(path))      # same index either way
            with open_db(path)[0] as db:
                self.assertEqual({k: db.get(k) for k in db.keys()}, live)

    def test_stale_snapshot_then_more_commits_including_deletes(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=10)
            for i in range(10):
                db.put(b"k%d" % i, b"old")                            # snapshot after the 10th
            db.close()
            covered = recovery.recover(path).end
            db, _ = open_db(path, snapshot_every=0)                   # no new snapshots
            db.delete(b"k3")
            db.put(b"k4", b"new")
            db.batch([("put", b"k5", b"x"), ("del", b"k5", None)])
            db.close()
            rec = recovery.recover(path)
            self.assertTrue(rec.report.snapshot)
            self.assertEqual(rec.replayed, 3)                         # only what came after the snapshot
            self.assertGreater(rec.end, covered)
            self.assertEqual(rec.index, full_replay_index(path))
            with open_db(path)[0] as db:
                self.assertEqual((db.get(b"k3"), db.get(b"k4"), db.get(b"k5"), db.get(b"k6")),
                                 (None, b"new", None, b"old"))

    def test_snapshot_of_another_file_is_ignored(self):
        with WorkDir() as w:
            a, b = w.path("a.kv"), w.path("b.kv")
            for path, value in ((a, b"A"), (b, b"B")):
                db, _ = open_db(path, snapshot_every=5)
                for i in range(7):
                    db.put(b"k%d" % i, value + b"%d" % i)
                db.close()
            shutil.copyfile(snapshot.path_for(a), snapshot.path_for(b))   # wrong cache next to b
            db, report = open_db(b)
            self.assertFalse(report.snapshot)
            self.assertEqual(db.get(b"k1"), b"B1")
            db.close()

    def test_damaged_snapshot_is_ignored(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=5)
            for i in range(6):
                db.put(b"k%d" % i, b"v%d" % i)
            db.close()
            idx = snapshot.path_for(path)
            data = bytearray(read_bytes(idx))
            for cut, flip in ((len(data) // 2, True), (len(data) - 3, False), (10, False)):
                broken = bytearray(data)
                if flip:
                    broken[cut] ^= 0xFF
                else:
                    broken = broken[:cut]
                write_bytes(idx, bytes(broken))
                db, report = open_db(path)
                self.assertFalse(report.snapshot)
                self.assertEqual(db.get(b"k5"), b"v5")
                db.close()

    def test_every_flipped_byte_still_gives_the_true_index(self):
        """A damaged snapshot must never change the index: each flip is rejected (CRC and
        the other checks) and the full replay is used instead."""
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=5)
            for i in range(6):
                db.put(b"key%d" % i, b"value%d" % i)
            db.close()
            truth = full_replay_index(path)
            idx = snapshot.path_for(path)
            data = read_bytes(idx)
            for i in range(len(data)):
                broken = bytearray(data)
                broken[i] ^= 0x01
                write_bytes(idx, bytes(broken))
                rec = recovery.recover(path)
                self.assertEqual(rec.index, truth, "flipped byte {} of {}".format(i, len(data)))
                self.assertFalse(rec.report.snapshot, "flipped byte {} was accepted".format(i))

    def test_torn_tail_after_the_snapshot_is_cut(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=5)
            for i in range(5):
                db.put(b"k%d" % i, b"v%d" % i)
            db.close()
            good = read_bytes(path)
            write_bytes(path, good + b"\xa1\x01\x00\x01\x02")          # half a transaction header + op
            db, report = open_db(path)
            self.assertTrue(report.snapshot)
            self.assertEqual(report.bytes_cut, 5)
            db.close()
            self.assertEqual(read_bytes(path), good)

    def test_failed_snapshot_never_fails_the_write(self):
        with WorkDir() as w:
            path = w.path("db.kv")
            db, _ = open_db(path, snapshot_every=3)
            real = snapshot.os.replace

            def broken(*a):
                raise OSError(28, "No space left on device (injected)")

            snapshot.os.replace = broken
            try:
                for i in range(5):
                    db.put(b"k%d" % i, b"v%d" % i)                   # no exception
            finally:
                snapshot.os.replace = real
            self.assertIn("No space left", db.snapshot_error)
            self.assertFalse(os.path.exists(path + snapshot.TMP_SUFFIX))
            db.close()
            with open_db(path)[0] as db:
                self.assertEqual(db.get(b"k4"), b"v4")

    def test_hard_kill_during_snapshot_write(self):
        for pause_at in ("snapshot.before-swap", "snapshot.after-swap"):
            with self.subTest(pause=pause_at), WorkDir() as w:
                path = w.path("db.kv")
                open_db(path)[0].close()
                kill_at(path, "snapshot", pause_at)                  # killed at the 50th put's snapshot
                if pause_at == "snapshot.before-swap":
                    self.assertTrue(os.path.exists(path + snapshot.TMP_SUFFIX))
                db, report = open_db(path)
                self.assertFalse(os.path.exists(path + snapshot.TMP_SUFFIX))   # leftover removed
                self.assertEqual(report.snapshot, pause_at == "snapshot.after-swap")
                self.assertEqual(len(db), 50)                         # all 50 committed puts
                self.assertEqual(db.get(b"s49"), b"v49")
                db.close()


if __name__ == "__main__":
    unittest.main()

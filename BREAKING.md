# How we would break this

Claims cover **process death** (a hard kill) only. E = the end offset remembered before a transaction. Lines are for commit `df602f7`. More detail: `docs/breaking-long-form.md`.

## 1. Crash points

| # | Where | On disk if the process dies here | What open() does |
|---|---|---|---|
| 1 | `src/kv/recovery.py:218` `_create` | 8 bytes `4B 56 44 42 01 00 00 00` | Header prefix → fresh header. Empty database |
| 2 | `src/kv/db.py:180` `DB._commit` | Nothing new: file ends at E | Nothing to repair. Old value |
| 3 | `db.py:187` | E + the first `len(chunks) // 2` body chunks; no marker | TORN → `os.ftruncate(E)`, fsync. Old value |
| 4 | `db.py:189`, `:191` | Whole body; no marker | TORN → cut to E. Old value |
| 5 | `db.py:193` | Body, marker, trailer; 2nd fsync not done (in page cache) | Marker and crc32 match → applied (no ACK was sent: allowed) |
| 6 | `db.py:195`, `:202` | Committed, fsynced | Applied — **required by A4** |
| 7 | `db.py:338` `DB._rollback` | After a failed write: a torn tail; or, if only the 2nd fsync failed, a complete transaction; or already cut to E | Cut to E; or applies the complete one (allowed: no success was returned); or nothing |
| 8 | `recovery.py:271` `recover` → `scan` | Unchanged: the scan only reads | Same scan again, same result (C1) |
| 9 | `recovery.py:278`, `:281` | Torn tail there / already cut | Cut to the same ABSOLUTE offset / nothing (C2) |
| 10 | `recovery.py:284` | File ends right after a valid marker | Writes trailer `0xEE`. Nothing lost (F4) |
| 11 | `db.py:157` `DB.close` | Unchanged: close writes nothing | Normal open (C6) |
| 12 | `src/kv/snapshot.py:77` `write` | Committed log; `<path>.idx.tmp` complete | Deletes `.tmp`; old snapshot only if it matches the log |
| 13 | `db.py:259` `DB._compact_locked` | Original untouched; `<path>.compact.tmp` partial | Deletes the temp file; original (C3) |
| 14 | `db.py:288` | Original untouched; temp complete; old `.idx` deleted | Deletes the temp file; full replay (C3) |
| 15 | `db.py:290`, `:300` | `<path>` = new compacted file; no temp, no `.idx` | Full replay of the compacted file (C4, C5) |

Each row is the pause point at that line (row 7 has none). Rows 1–6 and 8–15 are hard-killed there by `tests/test_crash.py`, `test_snapshot.py`, `test_compact.py` and `torture --mode targeted` (`evidence/torture/run-targeted-3-seed-3131.txt`, 51/51); row 7 by injected write errors (`tests/test_db.py` `RollbackTest`).

## 2. The ordering argument

`DB._commit` writes the body at E with a `write()` loop, calls `os.fsync`, and only then writes the marker (`CMIT`, body_len, crc32) and the trailer, then `os.fsync` again, then updates the index and returns — serve sends ACK after that. Recovery applies a transaction only if its whole marker is present and matches its body, so a kill before that point leaves bytes that are cut, never half applied; a batch is one transaction. After a process kill the OS keeps every byte a finished `write()` handed it, so an ACKed write is on disk. A failed write is undone with `truncate(E)` + `os.fsync`. Recovery changes the file only with `os.ftruncate` to an absolute offset or by appending `0xEE`, so repeating it gives the same file. Compaction writes a separate file, `os.fsync`s and re-reads it, deletes the old snapshot, closes both handles and switches with one `os.replace` (atomic rename) — the original is never deleted first. We `fsync` the directory after the rename: a process kill does not need it, an OS crash would. Snapshots use the same temp-then-`os.replace` order and are checked against 17 bytes of the log before use. `open_db` takes `fcntl.flock` on `<path>.lock` before recovery, so no second process can cut a write in progress.

## 3. Known weaknesses

1. **No power-loss claim:** plain `os.fsync` on macOS does not make the drive store data in order (`evidence/fsync-macos.txt`); `F_FULLFSYNC` is not used.
2. **Python recovery speed depends on the snapshot:** replaying 1,000,000 single-put transactions takes 2.889 s; with the snapshot the slowest open was 1.253 s (limit 2 s), warm cache only — cold cache NOT TESTED (`evidence/recovery-benchmark.md`).
3. **Our tests cannot see a missing fsync:** it is the one planted bug that survives (`evidence/engine-mutation-check-phase7.txt`).
4. **Compaction stops the world,** and under a tight `--max-bytes` E5 may compact on nearly every write; compaction of 1,000,000 live keys is NOT measured.
5. **Platform and margins:** the lock and the directory fsync are macOS/Linux only (Windows NOT TESTED); `check` takes no lock; with a snapshot, open does not re-check bytes before its offset; the judge's 200-iteration run took 152.2 s of about 180 s.

## 4. What we tried that failed

1. **Recovery took 4.857 s for 1,000,000 keys.** Found by the Phase 3 timing (`29b5984`) and `torture --mode scale`. Fixed by a fast path in `recovery.scan` (2.889 s), the index snapshot (`3f54581`) and keys ÷ 8 (`293d216`): 1.253 s. Two other speed-ups did not help and were dropped.
2. **A second open could cut a write in progress.** Found by reading `recovery.recover` (written down in `5683dc4`), reproduced against a lock-free copy: 14 bytes cut (`evidence/weakness4-without-lock.txt`). Fixed by the `flock` lock (`293d216`).
3. **The tester crashed instead of failing** on a fake engine. Found by the fake-engine proof (`2ded156`, run 1 kept). Fixed in the first committed tester (`6111eb5`).
4. **Tests that could not see bugs,** found by planting each bug in a copy: trusting a length (`1466f12`), a skipped snapshot CRC (`3f54581`), a cap off-by-one and a stale snapshot hidden by E5 (`a2007f3`). Each now has a stronger test.
5. **The judge's 200-iteration run took 355.8 s** (`b1513cb`); with the snapshot 152.2 s (`504f994`), kills unchanged.
6. **Our tooling failed twice:** the mutation check hung (it did not copy `kvdb.py`; a test spun on EOF) and one set of runs never started (a shell word-splitting bug). Fixed in `a2007f3` (test) and `504f994` (tools); the runs were redone.

## 5. If we had 24 more hours

1. **Power loss:** an `F_FULLFSYNC` option, its measured cost, and real power-cut tests (VM hard reset).
2. **Compaction that does not stop the world:** copy live data without the lock, then catch up the newer transactions under it; measure with 1,000,000 live keys.
3. **Harder measurements:** cold-cache recovery, one 500-kill run in a single process, and Windows with a `msvcrt` lock.

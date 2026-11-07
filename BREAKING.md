# How we would break this

Claims cover **process death** (a hard kill) only. E = the end offset remembered before a transaction. Lines are for the code after the final fix round. More detail: `docs/breaking-long-form.md`.

## 1. Crash points

| # | Where | On disk if the process dies here | What open() does |
|---|---|---|---|
| 1 | `src/kv/recovery.py:216` `_create` | 8 bytes `4B 56 44 42 01 00 00 00` | Header prefix → fresh header. Empty database |
| 2 | `src/kv/db.py:177` `DB._commit` | Nothing new: file ends at E | Nothing to repair. Old value |
| 3 | `db.py:184` | E + the first `len(chunks) // 2` body chunks; no marker | TORN → `os.ftruncate(E)`, fsync. Old value |
| 4 | `db.py:186`, `:188` | Whole body; no marker | TORN → cut to E. Old value |
| 5 | `db.py:190` | Body, marker, trailer; 2nd fsync not done (in page cache) | Marker and crc32 match → applied (no ACK was sent: allowed) |
| 6 | `db.py:192`, `:199` | Committed, fsynced | Applied — **required by A4** |
| 7 | `db.py:313` `DB._rollback` | After a failed write: a torn tail; or, if only the 2nd fsync failed, a complete transaction; or already cut to E | Cut to E; or applies the complete one (allowed: no success was returned); or nothing |
| 8 | `recovery.py:266` `recover` → `scan` | Unchanged: the scan only reads | Same scan again, same result (C1) |
| 9 | `recovery.py:273`, `:276` | Torn tail there / already cut | Cut to the same ABSOLUTE offset / nothing (C2) |
| 10 | `recovery.py:279` | File ends right after a valid marker | Writes trailer `0xEE`. Nothing lost (F4) |
| 11 | `db.py:154` `DB.close` | Unchanged: close writes nothing | Normal open (C6) |
| 12 | `db.py:252` `DB._compact_locked` | Original untouched; `<path>.compact.tmp` partial | Deletes the temp file; original (C3) |
| 13 | `db.py:281` | Original untouched; temp complete and validated; both handles closed | Deletes the temp file; full replay of the original (C3) |
| 14 | `db.py:283`, `:292` | `<path>` = new compacted file; no temp file | Full replay of the compacted file (C4, C5) |

Each row is the pause point at that line (row 7 has none). Rows 1–6 and 8–14 are hard-killed there by `tests/test_crash.py`, `tests/test_compact.py` and `torture --mode targeted`; row 7 by injected write errors (`tests/test_db.py` `RollbackTest`).

## 2. The ordering argument

`DB._commit` writes the body at E with a `write()` loop, calls `os.fsync`, and only then writes the marker (`CMIT`, body_len, crc32) and the trailer, then `os.fsync` again, then updates the index and returns — serve sends ACK after that. Recovery applies a transaction only if its whole marker is present and matches its body, so a kill before that point leaves bytes that are cut, never half applied; a batch is one transaction. After a process kill the OS keeps every byte a finished `write()` handed it, so an ACKed write is on disk. A failed write is undone with `truncate(E)` + `os.fsync`. Recovery changes the file only with `os.ftruncate` to an absolute offset or by appending `0xEE`, so repeating it gives the same file. Compaction writes a separate file, `os.fsync`s and re-reads it, closes both handles and switches with one `os.replace` (atomic rename) — the original is never deleted first. We `fsync` the directory after the rename: a process kill does not need it, an OS crash would. `open_db` takes `fcntl.flock` on `<path>.lock` before recovery, so no second process can cut a write in progress.

**E5, the second commit route:** a write that does not fit under `max_bytes` is never appended. `DB._commit_by_compaction` writes a compacted file that already contains it, and the one `os.replace` is its commit point: before it, the old file and no write; after it, the new file with the write. If the folder fsync or the reopen fails after the swap, the database is marked failed and the caller gets `DatabaseFailed` although the write is on disk — allowed, because no success was returned (`tests/test_compact.py` `E5FailureAfterSwapTest`, injected errors only).

## 3. Known weaknesses

1. **No power-loss claim:** plain `os.fsync` on macOS does not make the drive store data in order (`evidence/fsync-macos.txt`); `F_FULLFSYNC` is not used.
2. **F3 is not met for single puts:** opening 1,000,000 single-put transactions takes **2.423 s** (limit 2 s); 1,000,000 keys written in batches take 1.354 s (`evidence/no-cache-timing.txt`). We removed the Phase 6 index cache that made it 1.25 s, because the brief allows only one data file (final fix round).
3. **Our tests cannot see a missing fsync:** it is the one planted bug that survives (`evidence/engine-mutation-check-phase7.txt`).
4. **Compaction stops the world,** and under a tight `--max-bytes` E5 may compact on nearly every write; compaction of 1,000,000 live keys is NOT measured.
5. **Platform and margins:** only macOS and Linux are supported — elsewhere `open` refuses with `UnsupportedPlatform`; only macOS was tested; `check` takes no lock.
6. **Torture time:** 200 default iterations take **119.2 s** Wi-Fi off, 127.1 s network on (181.1 s before Fix 5): inside the judge's ~180 s, at the edge of our 120 s target. Growing modes start a fresh database every N iterations (`--fresh-every`; `evidence/fix5-torture-timing.md`).

## 4. What we tried that failed

1. **Recovery took 4.857 s for 1,000,000 keys** (Phase 3 timing, `29b5984`). A fast path in `recovery.scan` made it 2.889 s, an index cache 1.253 s (`3f54581`, `293d216`); the cache was removed in the final fix round (one data file): 2.423 s.
2. **A second open could cut a write in progress.** Found by reading `recovery.recover` (written down in `5683dc4`), reproduced against a lock-free copy: 14 bytes cut (`evidence/weakness4-without-lock.txt`). Fixed by the `flock` lock (`293d216`).
3. **The tester crashed instead of failing** on a fake engine (`2ded156`, run kept). Fixed in `6111eb5`.
4. **Tests that could not see bugs,** found by planting bugs in a copy: a trusted length (`1466f12`), a skipped CRC in the removed cache, a cap off-by-one (`3f54581`, `a2007f3`). Each now has a stronger test.
5. **The judge's 200-iteration run took 355.8 s** (`b1513cb`), later 181.1 s; Fix 5 made it 126.2 s with the kills unchanged.
6. **Our tooling failed twice:** the mutation check hung (a test spun on EOF) and one set of runs never started (shell word splitting). Fixed in `a2007f3`, `504f994`; runs redone.
7. **Found by an independent audit (2026-11-07)** and reproduced by us before fixing (`evidence/tester-hardening.txt`): the tester gave PASS 5/5 to an engine that never answers and to one that prints garbage, and an `ERR` with a bad request id crashed its threads; `serve` itself crashed on a 5,000-digit batch count. Fixed with strict reply parsing, a coverage verdict (INCONCLUSIVE, exit 3) and length checks before `int()`. Further audit findings: `docs/breaking-long-form.md`.

## 5. If we had 24 more hours

1. **Power loss:** an `F_FULLFSYNC` option, its cost, and real power-cut tests.
2. **Compaction without stopping the world:** copy without the lock, catch up the tail under it.
3. **Harder tests:** cold-cache recovery, faster `large-values` torture, Windows.

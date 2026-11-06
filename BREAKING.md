# How we would break this

## 1. Crash points table

Phase 3 rows: every pause point that exists so far. "E" is the end offset remembered in step 2 (CLAUDE.md section 9), i.e. the end of the last trailer. Each row was checked by a targeted hard kill (SIGKILL) in `tests/test_crash.py`. Compaction rows come in Phase 7. Line numbers are added in Phase 8, once the code is stable.

| # | Pause point · file and function | What is on disk if the process dies here | What open() does | Test |
|---|---|---|---|---|
| 1 | `create.mid-header` · `src/kv/recovery.py` `_create` | 8 bytes: `4B 56 44 42 01 00 00 00` (magic, version, flags; no max_bytes yet) | Step 2: a header prefix, so it writes a fresh 16-byte header and fsyncs. Empty database | `OpenKillTest.test_create_mid_header` |
| 2 | `write.before-body` · `src/kv/db.py` `DB._commit`, after step 2 | Nothing new: the file ends at E | Scan ends CLEAN. Old value | `WriteKillTest.test_put` |
| 3 | `write.mid-body` · `DB._commit` step 3, between body chunks | E, then the first `len(chunks) // 2` body chunks, where the chunks are: the 3-byte transaction header, then per op one chunk "op header + key" and (if the value is not empty) one chunk "value". For a single put that is only the 3-byte transaction header; for a 10,000-op batch with values, the header plus 4,999 whole ops and the next op header + key. No marker | Scan: a field runs past the end → TORN at E → `os.ftruncate(E)`, fsync. Old value | `test_put`, `test_batch_of_ten`, `test_batch_of_ten_thousand_killed_halfway` |
| 4 | `write.after-body` · `DB._commit`, end of step 3 | The whole body after E; no marker | Marker cut → TORN at E → cut to E. Old value | `test_put`, `test_batch_of_ten` |
| 5 | `write.after-body-sync` · `DB._commit`, after step 4 | Same bytes as row 4, now fsynced | Same as row 4 | `test_put`, `test_batch_of_ten` |
| 6 | `write.after-marker` · `DB._commit`, after step 5 | Body + marker + trailer written; second fsync not done. After a process kill the bytes are still in the OS page cache | Marker complete and its CRC matches → transaction applied. New value (allowed: success was never returned) | `test_put`, `test_batch_of_ten`, `test_one_megabyte_value` |
| 7 | `write.after-commit-sync` · `DB._commit`, after step 6 | Same as row 6, fsynced | Applied. New value — **required by A4** | `test_put`, `test_delete` |
| 8 | `write.after-index` · `DB._commit`, after step 7 | Same as row 7 | Applied. New value | `test_put`, `test_batch_of_ten` |
| 9 | `recovery.replay` · `src/kv/recovery.py` `recover` → `scan`, after the first transaction | Unchanged: the scan only reads (mmap, read-only) | The next open repeats the same scan and reaches the same state | `OpenKillTest.test_kill_during_recovery_twice` |
| 10 | `recovery.before-cut` · `recover` step 6 | Unchanged: the torn tail is still there | Finds the same TORN offset and cuts to the same ABSOLUTE offset | `test_kill_during_recovery_twice` |
| 11 | `recovery.after-cut` · `recover` step 6, after `ftruncate` + fsync | Ends at the last good trailer | Scan ends CLEAN; nothing to repair | `test_kill_during_recovery_twice` |
| 12 | `recovery.before-trailer` · `recover` step 7 | Ends right after a valid marker (trailer missing) | NO_TRAILER again → writes `0xEE` at the end, fsyncs. Nothing lost | `OpenKillTest.test_kill_before_trailer_restore` |
| 13 | `close` · `src/kv/db.py` `DB.close`, before closing the handles | Unchanged: close writes nothing, every commit is already fsynced | Normal recovery. Data intact | `OpenKillTest.test_kill_during_close` |

## 2. The ordering argument

To be written from evidence.

## 3. Known weaknesses

1. **Power loss not claimed; on macOS `os.fsync` doesn't flush the drive cache.** We use plain `os.fsync` (CLAUDE.md section 9). The macOS `fsync(2)` man page on our demo laptop says fsync moves data to the drive, but "the drive itself may not physically write the data … and it may be written in an out-of-order sequence", and that this can happen "if the drive loses power or the OS crashes". So after a power loss or OS crash, a commit marker could be on the disk while part of its body is not. Our guarantee covers **process death only**, which is what the brief tests. The fix would be `fcntl(fd, F_FULLFSYNC)` (available here: `F_FULLFSYNC = 51`), at a cost in write speed. Evidence: `evidence/fsync-macos.txt`.

2. **Recovery is too slow for a million keys — measured, not met yet.** Rough estimate on our laptop (i5-8259U, 8 GB, Python 3.13.9, warm cache): opening 1,000,000 single-put transactions takes **4.5 s**, and 1,000,000 keys in 100 batches take **1.6 s**. The brief's limit is 2 s (F3, B6). Cause not yet profiled; the scan in `src/kv/recovery.py` `scan` makes one Python call to `layout.decode_txn` per transaction. Fix planned for Phase 6. Evidence: `evidence/recovery-early-estimate.md`.
3. **Our crash tests cannot see a missing fsync.** A process kill leaves the OS page cache intact, so deleting the commit fsync (step 6) makes no test fail (`evidence/engine-mutation-check.txt`, the one expected survivor). The fsyncs only matter for power loss or an OS crash, which we do not claim.

4. **Nothing stops two processes from opening the same file.** If `kvdb.py open` (or `get`, `dump` …) runs while `serve` is in the middle of a write, the second process sees a body without a marker, treats it as a torn tail and `os.ftruncate`s it. `serve` then writes its marker at its own position, past the new end; the gap is filled with zero bytes, and the next open finds `0x00` where a transaction type should be: CORRUPT, open refuses (invariant rule 5). *Reasoned from `src/kv/db.py` `DB._commit` and `src/kv/recovery.py` `recover`; not yet reproduced by a test.* A fix would be an exclusive `fcntl.flock` taken in `open_db` (macOS and Linux); not decided by the team yet. Until then: never run another `kvdb.py` command on a file while `serve` has it open.

Further weaknesses: to be written from evidence.

## 4. What we tried that failed

1. **Phase 2 — the cut tests could not see a "trust the length" bug.** *Symptom:* we planted a bug in an in-memory copy of `src/kv/layout.py` `decode_txn` so it no longer checked `val_len` against the bytes left. No test failed. Decode still returned INCOMPLETE, because the next check (the marker) caught it, but it reported an offset **past the end of the input**. *Found by:* mutation check, `evidence/layout-mutation-check.txt` run 1. *Fix:* `tests/test_layout.py` `CutTest` now also asserts that an INCOMPLETE offset never points past the input; `tools/mutation_check_layout.py` now catches all 6 planted bugs (run 2).

2. **Phase 3 — the first million-key timing missed the target.** *Symptom:* `python3 tools/recovery_timing.py` measured 4.527 s to open 1,000,000 single-put transactions (limit: 2 s). *Found by:* the Phase 3 early timing, `evidence/recovery-early-estimate.md`. *Fix:* not yet — Phase 6.

More entries: to be written from evidence.

## 5. If we had 24 more hours

To be written from evidence.

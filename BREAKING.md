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
| 14 | `snapshot.before-swap` · `src/kv/snapshot.py` `write`, called from `src/kv/db.py` `DB._snapshot` after a commit | Main file: the transaction is committed (steps 1–7 done). `<path>.idx.tmp` is complete and fsynced; `<path>.idx` is the previous snapshot or absent | Removes the `.tmp`. Uses the previous snapshot if it still matches the log, and replays everything after it; otherwise replays the whole log. Nothing lost | `tests/test_snapshot.py` `test_hard_kill_during_snapshot_write` |
| 15 | `snapshot.after-swap` · `snapshot.write`, after `os.replace`, before the folder fsync | Main file as row 14. `<path>.idx` is the new snapshot (after a process kill the rename is visible) | Loads the new snapshot and replays only what came after it | same test |

## 2. The ordering argument

To be written from evidence.

## 3. Known weaknesses

1. **Power loss not claimed; on macOS `os.fsync` doesn't flush the drive cache.** We use plain `os.fsync` (CLAUDE.md section 9). The macOS `fsync(2)` man page on our demo laptop says fsync moves data to the drive, but "the drive itself may not physically write the data … and it may be written in an out-of-order sequence", and that this can happen "if the drive loses power or the OS crashes". So after a power loss or OS crash, a commit marker could be on the disk while part of its body is not. Our guarantee covers **process death only**, which is what the brief tests. The fix would be `fcntl(fd, F_FULLFSYNC)` (available here: `F_FULLFSYNC = 51`), at a cost in write speed. Evidence: `evidence/fsync-macos.txt`.

2. **Recovery of a million keys depends on the index snapshot, and the margin is small.** The log replay alone is too slow in Python: 1,000,000 single-put transactions take 2.889 s to replay even with the Phase 6 fast path (a stripped loop alone takes 1.54 s on our i5-8259U). With the snapshot (`src/kv/snapshot.py`) open took 1.070 s on the serve-built dataset, but in the worst case the rule allows — the last snapshot 250,000 transactions old — it took up to **1.582 s** (limit 2 s). A slower laptop, a cold cache (not tested) or more keys would eat that margin. Evidence: `evidence/recovery-benchmark.md`.
3. **Our crash tests cannot see a missing fsync.** A process kill leaves the OS page cache intact, so deleting the commit fsync (step 6) makes no test fail (`evidence/engine-mutation-check.txt`, the one expected survivor). The fsyncs only matter for power loss or an OS crash, which we do not claim.

4. **Nothing stops two processes from opening the same file.** If `kvdb.py open` (or `get`, `dump` …) runs while `serve` is in the middle of a write, the second process sees a body without a marker, treats it as a torn tail and `os.ftruncate`s it. `serve` then writes its marker at its own position, past the new end; the gap is filled with zero bytes, and the next open finds `0x00` where a transaction type should be: CORRUPT, open refuses (invariant rule 5). *Reasoned from `src/kv/db.py` `DB._commit` and `src/kv/recovery.py` `recover`; not yet reproduced by a test.* A fix would be an exclusive `fcntl.flock` taken in `open_db` (macOS and Linux); not decided by the team yet. Until then: never run another `kvdb.py` command on a file while `serve` has it open.

5. **The tester compares states, so a lost write that a later write replaced is invisible.** With a planted bug that drops one ACKed write per serve process, the tester only failed at iteration 8 (`evidence/torture/proof-planted-bugs.txt`): in iterations 1–7 the dropped write was overwritten by a later ACKed write before the kill. The invariant allows that end state, so this is a limit of state checking, not a missed failure — but it means one run can pass on top of a lost write.
6. **A seed does not reproduce thread timing.** `--seed` fixes every key, value, batch and kill delay, but which request is in flight at the kill depends on thread scheduling, so a failure may need a few runs with the same seed to come back.
7. **The judge's 200-iteration run fits in 3 minutes only just.** Before Phase 6, 200 iterations took 355.8 s because every iteration replayed the whole growing log four times (`evidence/torture/run-default-200-seed-777001.txt`). With the snapshot they took **177.0 s** (`run-default-200-seed-909090.txt`) — 3 s inside the judge's ~180 s, on our laptop with other apps open. Most of each iteration is now Python start-up for the four processes (serve, open, check, dump) and the kill delay itself; we will not shorten the kills or skip a verification step.
8. **With a snapshot, open does not re-check the bytes before E_c.** The checksums of transactions the snapshot covers were checked when they were first replayed, but a byte that rots later in that part of the file goes unnoticed by `open` (bit rot is outside the brief's tests, Group G). `kvdb.py check` still scans the whole file and would report it.
9. **A 1-byte cut right after a snapshot forces a full replay.** If the snapshot's E_c is the very end of the file and the file is then cut by 1 byte (F4), E_c is past the end, the snapshot is ignored, and open replays the whole log (correct, but slow again until the next snapshot).
10. **The snapshot uses extra disk space that the size cap does not count:** about 10 bytes plus the key per entry, plus a temporary copy while it is written.

Further weaknesses: to be written from evidence.

## 4. What we tried that failed

1. **Phase 2 — the cut tests could not see a "trust the length" bug.** *Symptom:* we planted a bug in an in-memory copy of `src/kv/layout.py` `decode_txn` so it no longer checked `val_len` against the bytes left. No test failed. Decode still returned INCOMPLETE, because the next check (the marker) caught it, but it reported an offset **past the end of the input**. *Found by:* mutation check, `evidence/layout-mutation-check.txt` run 1. *Fix:* `tests/test_layout.py` `CutTest` now also asserts that an INCOMPLETE offset never points past the input; `tools/mutation_check_layout.py` now catches all 6 planted bugs (run 2).

2. **Phase 3 — the first million-key timing missed the target.** *Symptom:* `python3 tools/recovery_timing.py` measured 4.527 s to open 1,000,000 single-put transactions (limit: 2 s). *Found by:* the Phase 3 early timing, `evidence/recovery-early-estimate.md`. *Fix:* not yet — Phase 6.

3. **Phase 5 — the tester crashed instead of failing on a fake engine.** *Symptom:* against `tester/fake_kvdb.py` (ACKs everything, stores nothing) `torture.py` stopped with a Python traceback (`FileNotFoundError` in `tester/modes.py` `run_random`): it copied the database file after the kill, and a broken engine never creates one. *Found by:* proof (a), `evidence/torture/proof-fake-serve-run1-tester-crashed.txt`. *Fix:* copy only if the file exists, then verify as usual — run 2 reports a proper FAIL with operation ids (`proof-fake-serve.txt`).
4. **Phase 5 — targeted mode never reached the `close` pause point.** *Symptom:* `--mode targeted` reported FAIL "serve exited before 'PAUSE close'". *Cause:* our tester started serve for that case without `KVDB_PAUSE_AT` set (the engine was fine). *Fix:* `tester/modes.py` `targeted_one` passes the pause point to `prefill`; 26/26 pass after it.

5. **Phase 6 — two recovery speed-ups that did not help.** *Tried:* (1) one `unpack_from` per transaction (marker + next header in one 27-byte read) plus building the index with one `dict.update(zip(...))`; (2) turning the garbage collector off during recovery. *Found by:* alternating timed runs on the 1,000,000-transaction file: (1) was not faster (2.59–2.72 s against 2.27–2.74 s for the simpler fast path) and (2) made no difference beyond the noise. *Decision:* both dropped; the simpler fast path stays. `evidence/recovery-benchmark.md`.

6. **Phase 6 — the first snapshot tests could not see a missing CRC check.** *Symptom:* a planted bug that skipped the snapshot's crc32 check in `src/kv/snapshot.py` `load` made no test fail. *Found by:* `tools/mutation_check_engine.py`, `evidence/engine-mutation-check-phase6-run1-survivor.txt`. *Cause:* the damaged-snapshot test flipped one byte and then looked at one key only. *Fix:* `tests/test_snapshot.py` `test_every_flipped_byte_still_gives_the_true_index` flips every byte of a snapshot in turn and requires the complete index to equal a full replay; the mutant is now caught (run 2, `evidence/engine-mutation-check-phase6.txt`).

More entries: to be written from evidence.

## 5. If we had 24 more hours

To be written from evidence.

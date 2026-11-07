# Phase 8 self-audit: documents checked against the code

**Method.** Six proof rows from ARCHITECTURE.md and four crash points from BREAKING.md, chosen at random:

```
python3 -c "import random; r = random.Random(20261106); \
rows = [g + str(i) for g, n in (('A',7),('B',6),('C',6),('D',4),('E',5),('F',4)) for i in range(1, n + 1)]; \
print(sorted(r.sample(rows, 6))); print(sorted(r.sample(range(1, 16), 4)))"
-> ['A1', 'A3', 'A4', 'A5', 'B1', 'D1']    [5, 6, 7, 14]
```

For each one, every claim (disk state, mechanism, file and function, line, test) was compared with the code at commit `df602f7` plus the Phase 8 document edits. The code was not changed.

## Results

| Item | Claim checked | Code read | Result |
|---|---|---|---|
| Row A1 | Disk: "file ends at the last trailer EE" | `src/kv/db.py:180` `pause("write.before-body")` is after step 2, before any write | **Mismatch 1 (wording):** for an empty database the file ends at the 16-byte header, not a trailer. Fixed: "file ends at E (the last trailer, or the header)" |
| Row A3 | Body complete, marker absent (old) / marker written (new); test names | `db.py:189-193`; `tests/test_crash.py` `PUT_EXPECT` | Match |
| Row A4 | Marker + crc32 match → applied; `ServeTest.test_pause_point_in_serve_then_kill` | `db.py:194-195`; `recovery.py` `scan`; `tests/test_cli.py` | Match |
| Row A5 | "serve sends ACK only after DB._commit returns, which is after step 6" | `kvdb.py` `Server.run_request`; `db.py` `_commit` | **Mismatch 2 (incomplete):** a write that does not fit under `max_bytes` returns through `_commit_by_compaction`; its commit point is the compaction's `os.replace` (step 7 of section 11), not step 6. Fixed in row A5 and in ARCHITECTURE Part 3 "Durability", which had the same gap |
| Row B1 | Verified by `WriteKillTest.test_batch_of_ten [write.mid-body]` for "3 complete ops" | `db.py:184-187`; `layout.encode_body`: 21 chunks, 10 written before the pause | **Mismatch 3 (overclaim):** that kill lands after 4 whole ops and the 5th op's header and key, not after 3. A cut right after op 3 is covered by `BatchCutTest.test_ten_op_batch` (every byte). Fixed: both stated precisely |
| Row D1 | Verified by seeds 60606 and 777001 | `evidence/torture/` | **Mismatch 4 (stale):** those runs used the Phase 5 code. Added the run on the current code (seed 1717, Phase 7, 200/200) |
| Crash 5 | `db.py:193`, body + marker + trailer, 2nd fsync not done | `db.py:192-193` | Match |
| Crash 6 | `db.py:195`, `:202`, committed | `db.py:194-195`, `:202` | Match |
| Crash 7 | `db.py:338` `DB._rollback`: "torn tail, or already cut to E … failed write absent" | `db.py:335-339`, and `_commit` steps 5-6 | **Mismatch 5 (wrong):** if only the SECOND fsync failed, the marker is already complete; a kill before `truncate` leaves a complete transaction, which the next open applies. Allowed (success was never returned, rule 2), but the row said "absent". Fixed |
| Crash 14 | `db.py:288`, original untouched, temp complete, old `.idx` deleted, full replay | `db.py:283-289` (steps 5-6 before the pause), `recovery.recover` | Match |

**Summary:** 10 items checked, 5 matched, 5 mismatched. All five were documentation errors (one wrong, one incomplete, one overclaim, one stale, one wording); no code was wrong. While fixing mismatch 2, the same gap was found and fixed in ARCHITECTURE.md Part 3 "Durability" (outside the sample).

# Phase 9 repeat of the self-audit (final code, commit `1ef9754`)

**New random sample**, seeded with the final commit hash so it cannot be picked by hand: `random.Random(0x1ef9754)`, same command as above → proof rows **A6, B2, B6, E1, E4, F1**, crash points **5, 8, 10, 14**.

| Item | Claim checked | Code read | Result |
|---|---|---|---|
| Row A6 | Delete uses the same write path; `WriteKillTest.test_delete` at all 7 write pause points | `db.py` `DB.delete` → `_commit`; `tests/test_crash.py` `PUT_EXPECT` (7 points) | Match |
| Row B2 | No complete marker → nothing applied; `test_batch_of_ten` [after-body, after-body-sync]; every-byte cut | `db.py:189-191`; `tests/test_db.py` `BatchCutTest` | Match |
| Row B6 | Mechanism said "Recovery under 2 s NOT measured" | `evidence/recovery-benchmark-keys8.txt` | **Mismatch 6 (stale):** it was measured in Phase 6 (1.073 s, and the row's own "Verified by" says so). Fixed |
| Row E1 | Refused before any byte is written, after checking the compacted size | `db.py:176-178` → `_commit_by_compaction` | Match |
| Row E4 | "a compacted file over max_bytes … aborts before the swap … temporary file deleted" | `db.py` `DB.compact` lines 146-147 | **Mismatch 7 (imprecise):** an over-cap result is refused BEFORE any temporary file exists (`CompactionFailed` from `compact`, `SizeCapReached` inside E5); only a write error deletes a temporary file. Fixed |
| Row F1 | "500 in one run: not run yet" | `evidence/final/torture-i500-s880011.txt` | **Mismatch 8 (stale):** the 500-kill run in one process now exists (PASS 500/500 in the clean clone). Updated |
| Crash 5 | `db.py:193` | `db.py:192-193` | Match |
| Crash 8 | `recovery.py:271`, the scan only reads | `recovery.py:271`, `_scan_fd` (read-only mmap) | Match (the temporary files are deleted before line 271; the data file is not changed) |
| Crash 10 | `recovery.py:284`, writes `0xEE` | `recovery.py:284-286` | Match |
| Crash 14 | `db.py:288` | `db.py:285-289` | Match |

**Summary:** 10 items, 7 match, 3 documentation mismatches fixed (two stale, one imprecise). No code was wrong.

---

# Final fix round self-audit (2026-11-07, Fix 9f)

**Part 1 — every proof row and crash point touched by Fixes 1–8.** Fix 2 removed the index cache, Fix 3 added `UnsupportedPlatform`, Fix 7 changed only the tester. Checked: all 14 crash points in BREAKING.md §1 (line numbers compared with `grep -n 'pause("'` on `src/kv/db.py` and `src/kv/recovery.py` at commit `8989220`), and ARCHITECTURE rows A7, B6, C3, C5, E4, E5 and F3 plus the text below the table.

**Part 2 — six rows at random:**

```
python3 -c "import random, re; rows = [l.split('|')[1].strip() for l in open('ARCHITECTURE.md', encoding='utf-8') if re.match(r'\| [A-G]\d+ \|', l)]; r = random.Random(20261107); print(len(rows), sorted(r.sample(rows, 6)))"
-> 32 ['A2', 'A5', 'C2', 'E1', 'E2', 'E3']
```

For each row, every named test was looked up (`grep -F "def <name>"` in the file that holds the class), four of them were run (`tests.test_crash.WriteKillTest.test_put`, `tests.test_db.FiveCutTest.test_five_cuts`, `tests.test_crash.OpenKillTest.test_kill_during_recovery_twice`, `tests.test_compact.CapTest.test_e2_batch_over_the_cap`: `Ran 4 tests ... OK`), and every evidence file was opened.

| Item | Claim checked | Code / file read | Result |
|---|---|---|---|
| BREAKING §1 rows 1–14 | pause point at each named line | `grep -n 'pause("'`: recovery.py 216, 266, 273, 276, 279; db.py 154, 177, 184, 186, 188, 190, 192, 199, 252, 281, 283, 292; `_rollback` truncate at db.py:313 | Match (all 14) |
| BREAKING §1 row 3 | "the first `len(chunks) // 2` body chunks" | `db.py:181-185` (`middle = len(chunks) // 2`, pause before chunk `middle`) | Match |
| Row A7 | `CutTest.test_every_cut_of_a_large_value` | `tests/test_layout.py` | **Mismatch 9 (name overclaims):** the test SAMPLES cuts (first 40 bytes, then every 997th, through a 100,000-byte value). Renamed `test_sampled_cuts_of_a_100000_byte_value` with a docstring; ARCHITECTURE row says "sampled" |
| Row B6 | "B6 itself not re-measured after the cache was removed" | `evidence/recovery-worst-case.txt` (Fix 4c) | **Mismatch 10 (stale):** measured, 2.481 s after single puts (NOT met), 1.438 s after batches. Fixed |
| Row C3 | `test_kill_at_every_compaction_pause_point`, `test_stale_complete_temp_file_is_never_trusted`, seeds 3131 / 7701 | `tests/test_compact.py`; `evidence/torture/run-compaction-100-seed-7701.txt` (PASS 100/100, 80/100 during a compaction) | Match. Note: the torture runs ran Phase 7 code (with the cache); final-code runs are in the final check |
| Row C5 | open deletes `<path>.compact.tmp` and an old `<path>.idx` | `recovery.py` `recover` step 3a; `tests/test_db.py` `OneDataFileTest` | Match |
| Row E4 | over-cap compaction refused before a temp file exists | `db.py` `_commit_by_compaction`, `DB.compact` | Match (fixed in Phase 8, unchanged) |
| Row E5 | os.replace is the commit point | `db.py` `_compact_locked` steps 6–9 | **Mismatch 11 (missing case):** a failure AFTER the swap returns `DatabaseFailed` although the write is on disk; no test and no document said so. Added `tests/test_compact.py` `E5FailureAfterSwapTest` (2 tests, injected errors) and text in README Errors, BREAKING §2 and this row |
| Row F3 | NOT MET for single puts, 2.423 s | `evidence/no-cache-timing.txt` | Match |
| Text under the table | "`DB._compact_locked`, `snapshot.write`"; "`tests/test_snapshot.py`"; header "(… compaction or snapshot)" | `src/kv/snapshot.py`, `tests/test_snapshot.py` were deleted in Fix 2 | **Mismatch 12 (stale):** three references to deleted code. Removed |
| README proof table | two rows send the reader to `tests/test_snapshot.py` | file deleted | **Mismatch 13 (stale):** rows kept as history, now say the file was deleted; a line above the table says phase rows ran older code |
| Row A2 | `WriteKillTest.test_put` [write.mid-body]; `FiveCutTest.test_five_cuts` | `tests/test_crash.py`, `tests/test_db.py`; both run: OK | Match |
| Row A5 | ACK only after `_commit` returns; E5 route; `test_put_killed_after_it_returned`, `ServeTest.test_ack_then_sigkill_by_pid_then_reopen` | `db.py` `_commit`; tests exist | Match |
| Row C2 | `OpenKillTest.test_kill_during_recovery_twice`, `test_kill_before_trailer_restore` | `tests/test_crash.py`; first one run: OK | Match |
| Row E1 | refused before any byte; disk-full seed 9901 with 449 refusals | `evidence/torture/run-disk-full-100-seed-9901.txt`: `PASS 100/100`, `SizeCapReached 449` | Match (Phase 7 code, see C3 note) |
| Row E2 | whole batch sized first; `CapTest.test_e2_batch_over_the_cap` | run: OK | Match |
| Row E3 | `CapTest.test_e3_cap_reached_then_killed` | `tests/test_compact.py` | Match |

**Summary:** 14 crash points and 16 rows or texts checked; 5 documentation mismatches (9–13), of which one was a missing test (11), now added. No engine code was wrong or changed.

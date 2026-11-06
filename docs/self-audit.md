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

# Architecture and robustness proof

## Part 1 — Diagram

Source: [diagrams/architecture.mmd](diagrams/architecture.mmd). Exported image: not yet created.

To be written from evidence.

## Part 2 — Scenario-to-mechanism proof table

The "Scenario" and "Expected after restart" columns are copied from the official brief, Section 5. The text was taken from the PDF; only line breaks and letter-spacing artefacts of the PDF text extraction were removed. Every other column stays "not verified" until a real file, function and test or run exist.

| ID | Scenario (brief §5) | Expected after restart (brief §5) | Disk state at the moment of the kill | Mechanism that handles it | Where in the code | Verified by |
|---|---|---|---|---|---|---|
| A1 | Kill before any bytes of a put reach the file | Key absent (or holds its previous value) | not verified | not verified | not verified | not verified |
| A2 | Kill after some bytes of the record are written but not all (torn write) | Key absent or previous value. The torn bytes are ignored, never returned as a value | not verified | not verified | not verified | not verified |
| A3 | Kill after the record is fully written but before fsync | Either outcome is acceptable, because success was not returned. Must not corrupt anything else | not verified | not verified | not verified | not verified |
| A4 | Kill after fsync returns but before put returns success to the caller | Key present with the new value. The data was durable, even though the caller never heard back | not verified | not verified | not verified | not verified |
| A5 | Kill immediately after put returns success | Key present with the new value. No exceptions | not verified | not verified | not verified | not verified |
| A6 | Kill during a delete | Key either still present with old value, or absent. Never a corrupted value | not verified | not verified | not verified | not verified |
| A7 | Kill during a put of a 1 MB value | Same rules as A1 to A5, with a large value spanning many disk blocks | not verified | not verified | not verified | not verified |
| B1 | Kill after 3 of 10 operations in a batch are written | None of the 10 are visible | not verified | not verified | not verified | not verified |
| B2 | Kill after all 10 are written but the commit marker is not | None of the 10 are visible | not verified | not verified | not verified | not verified |
| B3 | Kill after the commit marker is written but before fsync | Either all or none. Never some | not verified | not verified | not verified | not verified |
| B4 | Kill after the batch returns success | All 10 visible | not verified | not verified | not verified | not verified |
| B5 | A batch that puts key X and deletes key X in the same batch | Define the ordering in your README and honour it after a crash | not verified | not verified | not verified | not verified |
| B6 | A batch of 10,000 operations, killed halfway | None visible. Recovery still completes in under 2 seconds | not verified | not verified | not verified | not verified |
| C1 | Kill during open() while it is replaying the log | The next open() succeeds and reaches the same correct state | not verified | not verified | not verified | not verified |
| C2 | Kill during open() while it is truncating or repairing a torn tail | The next open() succeeds | not verified | not verified | not verified | not verified |
| C3 | Kill during compaction, before the new file replaces the old | Old file is still authoritative, all data intact | not verified | not verified | not verified | not verified |
| C4 | Kill during compaction, in the middle of the rename or swap | Exactly one of old or new is authoritative, both are complete, nothing is lost | not verified | not verified | not verified | not verified |
| C5 | Kill during compaction, after swap but before cleanup of temporary files | Data intact. Leftover temp files are cleaned up or ignored on the next open() | not verified | not verified | not verified | not verified |
| C6 | Kill during close() | Same as a kill at any other time. close() is not special | not verified | not verified | not verified | not verified |
| D1 | Two writers putting different keys, killed at random | Every acknowledged put from either writer is present | not verified | not verified | not verified | not verified |
| D2 | Two writers putting the same key, killed at random | The key holds a value that one of the writers actually wrote. Never a mix of two values | not verified | not verified | not verified | not verified |
| D3 | One writer in a long batch, another doing single puts, killed at random | Batch is all-or-nothing. Single puts that were acknowledged are present | not verified | not verified | not verified | not verified |
| D4 | A reader calling get during a batch on the same keys (no crash) | Reader sees the state before the batch or after it. Never in between | not verified | not verified | not verified | not verified |
| E1 | File size cap reached during a single put | put returns an error. Database still readable. Previous data intact | not verified | not verified | not verified | not verified |
| E2 | Cap reached halfway through a batch | Batch returns an error. None of its operations are visible | not verified | not verified | not verified | not verified |
| E3 | Cap reached, then process killed | Restart succeeds. All previously acknowledged data intact | not verified | not verified | not verified | not verified |
| E4 | Cap reached during compaction | Compaction fails or completes. Either way, no data lost and database still opens | not verified | not verified | not verified | not verified |
| E5 | After deletes free up space, compaction runs, writes succeed again | Database is usable again without manual intervention | not verified | not verified | not verified | not verified |
| F1 | 500 consecutive random kills and restarts | Invariant holds after every single one | not verified | not verified | not verified | not verified |
| F2 | Kill, restart, kill during recovery, restart, kill during recovery again | Invariant holds. Recovery is idempotent | not verified | not verified | not verified | not verified |
| F3 | 1,000,000 keys written, process killed, open() called | Recovery completes in under 2 seconds, all acknowledged keys present | not verified | not verified | not verified | not verified |
| F4 | Database file truncated by 1 byte at the end by hand (simulates a lost tail) | open() succeeds. At most the last unacknowledged record is lost | not verified | not verified | not verified | not verified |

## Part 3 — The three guarantees

### Durability

### Atomicity

### Recoverability

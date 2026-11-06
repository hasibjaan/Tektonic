# Recovery benchmark — one million keys (Phase 6)

**Status: the 2-second limit (F3, B6) is met on this laptop after step d (index snapshot).** Slowest open: **1.070 s** on the serve-built dataset; **1.582 s** in the constructed worst case (the last snapshot 250,000 transactions old). Without the snapshot it was 2.889 s, and 4.857 s with the Phase 3 code.

## Machine and setup

| Item | Value |
|---|---|
| CPU | Intel Core i5-8259U @ 2.30 GHz (4 cores, 8 threads) |
| RAM | 8 GB |
| Disk / filesystem | internal SSD (PCI-Express), APFS |
| OS | macOS 15.8.1 (x86_64) |
| Python | 3.13.9 (`/usr/local/bin/python3`) |
| Other load | the laptop was also running the Claude desktop app, VS Code and WindowServer; load average 3.1–6.3 during these runs, timings vary by about 20 % |
| Keys / values | 1,000,000 distinct keys `key0000000`…`key0999999` (10 bytes), values `val0000000`… (10 bytes) |
| How the dataset was built | **through `kvdb.py serve`, one transaction per key** (1,000,000 single puts, up to 256 in flight), then `serve` was hard-killed (SIGKILL). Took 418 s |
| File size | 47,000,016 bytes |
| What is timed | from starting `kvdb.py serve` until it prints `READY` (recovery done, accepting reads and writes) — includes Python start-up (about 0.1 s). `recovery_ms` is the engine's own time inside `open_db`. Contents are checked with `dump --hex` **outside** the timed part |
| Cache state | warm: the file had just been written or read (it is in the OS page cache). A cold-cache run is NOT TESTED (it needs `sudo purge`) |

## Sync speed (step 1)

`python3 tools/sync_speed.py 1000`: 1,000 single puts through serve, one at a time: **average 0.389 ms** (median 0.364 ms, slowest 9.968 ms). Projected for 1,000,000 single puts: 389 s = 6.5 minutes — short enough to build the dataset with single puts, as above.

## Results (steps 1–2)

| Code | Runs: serve start → READY (s) | Slowest | B6: 10,000-op batch killed at `write.mid-body`, then open | File |
|---|---|---|---|---|
| Baseline (Phase 3 code) | 4.857, 4.541, 4.600, 4.606, 4.477 | **4.857 s — FAIL** | 4.814 s, 85,002 bytes cut, none of the batch visible | `recovery-benchmark-baseline.txt` |
| After the fast path in `src/kv/recovery.py` `scan` | 2.889, 2.371, 2.401, 2.365, 2.345 | **2.889 s — FAIL** | 2.354 s, 85,002 bytes cut, none of the batch visible | `recovery-benchmark-after-fastpath.txt` |
| After step d: index snapshot `<path>.idx` (dataset REBUILT through serve, snapshots written along the way; `READY … snapshot=yes`) | 1.070, 0.981, 0.961, 0.967, 0.953 | **1.070 s — PASS** | 0.987 s, 85,002 bytes cut, none of the batch visible | `recovery-benchmark-after-snapshot.txt` |
| Worst case for the snapshot rule: the last snapshot covers 750,000 of the 1,000,000 transactions (250,000 replayed) | 1.300, 1.501, 1.582, 1.279, 1.236 | **1.582 s — PASS (over the 1.5 s margin in 2 of 5 runs)** | — | `recovery-benchmark-worst-case.txt` |

All 1,000,000 keys were correct after run 1 and run 5 of each series, and after the B6 open.

## What was tried (steps a–c)

1. **Profile** (cProfile, recovery of 1,000,000 single-put transactions): 10.1 s under the profiler; `layout.decode_txn` was called once per transaction and was the hotspot (5.5 s own time: a memoryview, a namedtuple, a list and three `unpack_from` per call). `zlib.crc32` was only 0.4 s.
2. **Fix 1 — fast path, kept:** `scan` parses a normal, valid transaction inline with two prebuilt `struct.Struct.unpack_from` calls on the mmap (header + first op header; marker + trailer), one `zlib.crc32` over the whole body span, and no per-transaction objects. Anything unusual still goes to `layout.decode_txn`. 4.86 s → 2.89 s.
3. **Fix 2 — one unpack per transaction + bulk dict build, rejected:** not faster in a fair alternating comparison (v1 2.27–2.74 s, v2 2.59–2.72 s) and more complex, so it was reverted.
4. **Garbage collector off during recovery: no effect** (2.79–3.28 s with it on or off, within the noise).
5. **The floor:** a stripped loop that only does the unavoidable work per transaction (two unpacks, one key slice, one dict insert, one crc32) already takes **1.54–1.59 s** for 1,000,000 transactions on this laptop. A per-transaction Python loop cannot get under the 1.5 s margin here; recovery must replay fewer transactions. That is step d (a checkpoint), which needs the team's approval first.
6. **Step d — index snapshot, approved and built:** `src/kv/snapshot.py`; written by `src/kv/db.py` `DB._snapshot` after a commit once the transactions since the last snapshot reach max(10,000, keys ÷ 4); loaded by `src/kv/recovery.py` `recover` only if it matches the log (CLAUDE.md section 8a). Loading 1,000,000 entries uses only C-level calls (a scratch prototype measured 0.616 s). The snapshot file for 1,000,000 keys is 18,940,873 bytes. The tail replayed after a snapshot is at most about keys ÷ 4 transactions — the worst case above.

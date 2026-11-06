# Recovery timing — ROUGH ESTIMATE (Phase 3)

**This is an early warning, not the Phase 6 benchmark.** Files are built directly with `src/kv/layout.py`, not through `serve`; each file is timed right after it was written, so it is in the OS page cache (warm cache). Command: `python3 tools/recovery_timing.py --write`.

- Machine: Intel(R) Core(TM) i5-8259U CPU @ 2.30GHz, 8 GB RAM, macOS-15.8.1-x86_64-i386-64bit-Mach-O, Python 3.13.9
- Disk: internal SSD, APFS (CLAUDE.md section 5)
- Run at: 2026-11-06 15:44:38 IST
- Keys: 1,000,000 distinct, 10 bytes each. Values: 10 bytes each. What is timed: the full `src.kv.open_db` call.

| Dataset | How it was built | File size (bytes) | Open runs (s) | Slowest (s) | Over 1.5 s? |
|---|---|---|---|---|---|
| A | 100 batches of 10,000 puts (built in 2.4 s) | 27,002,016 | 1.622, 1.577, 1.487 | **1.622** | **YES** |
| B | 1,000,000 single-put transactions (built in 4.9 s) | 47,000,016 | 4.527, 4.513, 4.498 | **4.527** | **YES** |
| C | dataset B + half of a 10,000-op batch at the end (B6); open cuts 130,010 bytes each run | 47,130,026 before the cut | 4.500, 4.635, 4.551 | **4.635** | **YES** |

Slowest open over all runs: **4.635 s** against the 2-second limit (F3, B6).

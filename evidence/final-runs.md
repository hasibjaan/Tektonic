# Final runs — Phase 9 (clean clone)

**Where:** a fresh `git clone https://github.com/hasibjaan/Tektonic.git` of commit **`1ef9754`** into a new folder outside the repository (`outcome/clean-clone-phase9/`), on the team's laptop: Intel i5-8259U, 8 GB RAM, macOS 15.8.1, Python 3.13.9, internal SSD (APFS), other apps open. Every command was run in the clone **exactly as the judge would type it** (`./torture …`), with seeds never used before. Raw outputs: `evidence/final/`.

**Network:** the runs in the tables below were made ONLINE (the clone came from GitHub). The **offline** run was made by a team member on 2026-11-07 with Wi-Fi switched off: `sh tools/phase9_offline.sh` (raw output `evidence/final/offline-clean-clone.txt`). It confirmed `github.com` was not reachable, then made a local clone of commit **`ab40ae2`** into a new folder:

| Step, Wi-Fi OFF | Result | Time |
|---|---|---|
| `python3 -m compileall -q .` (the README build command) | exit 0 | 0.42 s |
| `python3 tools/check_imports.py` | `OK` | 0.41 s |
| `python3 -m unittest discover -s tests` | Ran 119 tests, OK | 29.5 s |
| `./torture --iterations 200 --seed 990011` | **PASS 200/200**, a request in flight at 200/200 kills | 169.4 s |

## Build and checks in the clone

| Step | Result | Time |
|---|---|---|
| `python3 -m compileall -q .` (the README build command) | exit 0 | 0.53 s |
| `python3 tools/check_imports.py` | `OK` | 0.38 s |
| `python3 -m unittest discover -s tests -v` | Ran 119 tests, OK | 29.328 s |

## Random kills

| Command | Mode, iterations | Result | Wall time | Load avg at start | Notes | File |
|---|---|---|---|---|---|---|
| `./torture --iterations 500 --seed 880011` | default, 500 | **PASS 500/500** | 562.57 s | 3.00 | kills with at least one request in flight: 500/500 (100.0%) | `torture-i500-s880011.txt` |
| `./torture --iterations 200 --seed 880022` | default, 200 (the judge allows ~3 min) | **PASS 200/200** | 181.05 s | 3.67 | kills with at least one request in flight: 200/200 (100.0%) | `torture-i200-s880022.txt` |
| `./torture --iterations 100 --seed 880033 --mode large-values` | large-values, 100 | **PASS 100/100** | 2271.83 s | 2.72 | kills with at least one request in flight: 100/100 (100.0%) | `torture-i100-s880033-large-values.txt` |
| `./torture --iterations 100 --seed 880044 --mode compaction` | compaction, 100 | **PASS 100/100** | 96.93 s | 2.52 | kills with at least one request in flight: 100/100 (100.0%); kills that landed DURING a compaction: 88/100 | `torture-i100-s880044-compaction.txt` |
| `./torture --iterations 100 --seed 880055 --mode recovery-crash` | recovery-crash, 100 | **PASS 100/100** | 247.35 s | 2.98 | kills with at least one request in flight: 100/100 (100.0%); extra kills of a restarted serve: 35 before Python started main, 233 DURING recovery, 32 after READY | `torture-i100-s880055-recovery-crash.txt` |
| `./torture --iterations 100 --seed 880066 --mode large-batches` | large-batches, 100 | **PASS 100/100** | 403.03 s | 4.69 | kills with at least one request in flight: 100/100 (100.0%) | `torture-i100-s880066-large-batches.txt` |
| `./torture --iterations 100 --seed 880077 --mode disk-full` | disk-full, 100 | **PASS 100/100** | 67.19 s | 2.20 | kills with at least one request in flight: 100/100 (100.0%); ERR replies by reason: SizeCapReached 429 | `torture-i100-s880077-disk-full.txt` |
| `./torture --iterations 100 --seed 880099 --mode overlap` | overlap, 100 | **PASS 100/100** | 78.35 s | 2.58 | kills with at least one request in flight: 100/100 (100.0%) | `torture-i100-s880099-overlap.txt` |

- Every random run passed. The judge's 200-iteration run took **181.1 s**, at the edge of the ~3 minutes, with a load average of 3.7 from other apps.
- `large-values` took **38 minutes**: iteration time grew from 0.73 s to 86 s, because the database grew to 4.2 GB (1 MB values, no compaction in that mode), the tester copies it after every kill for failure evidence, and `check` reads all of it. Reported to the team (BREAKING.md weakness 6); not changed in Phase 9 (no new features).
- `large-batches` took **403 s** for 100 iterations (batches of 1,000–10,000 operations, so each iteration writes and re-reads much more than the default mode).
- `recovery-crash` was first cut off at iteration 9 when the Claude Code session ended (`torture-i100-s880055-recovery-crash-run1-cut-off-by-session-end.txt`, kept); the full run above is the rerun with the same seed.

## Targeted kills (pause points) — reported separately

| Command | Result | Wall time | File |
|---|---|---|---|
| `./torture --iterations 3 --seed 880088 --mode targeted` | **TARGETED PASS 51/51** — 17 pause points × 3 rounds; `snapshot.before-swap` and `snapshot.after-swap` are NOT TESTED by this mode (they need 10,000+ transactions) and are hard-killed in `tests/test_snapshot.py` instead | 29.37 s | `torture-i3-s880088-targeted.txt` |

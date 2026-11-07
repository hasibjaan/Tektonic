# Fix 5 — torture run time, before and after (final fix round, 2026-11-07)

Laptop: Intel i5-8259U, 8 GB, macOS 15.8.1, Python 3.13.9 (the demo laptop), other apps open.
The lines below are copied verbatim from the terminal output of each command.

## Profile of one iteration BEFORE Fix 5 (no index cache, one database for the whole run)

`python3 torture.py --iterations 40 --seed 515041`:
```
average iteration 0.80 s; projected 200 iterations: 160 s
time per iteration by phase: random kill delay (writes in flight) 196 ms, kvdb.py open 158 ms, kvdb.py check 145 ms, serve start until READY 141 ms, kvdb.py dump 134 ms, kill + drain + join writers 23 ms, copy of the database (failure evidence) 3 ms, oracle comparison 0 ms
```
Start-up split (`python3 -X importtime`, best of 10 process starts): the interpreter alone 42 ms (`-S` saves 5 ms, not used); `kvdb.py check` 94 ms; biggest imports `concurrent.futures` 13 ms (only `serve` needs it) and `argparse` 13 ms (required). Log replay grows every iteration (no cache).

## What changed (nothing in the kills or the checks)

1. `kvdb.py`: `concurrent.futures` is imported inside `serve` only.
2. `torture.py`: in modes whose file grows without limit, a fresh, empty database every N iterations — `default` and `overlap` 25, `large-batches` 10, `large-values` 5 (printed at the start of the run; `--fresh-every N` overrides, 0 = never). The oracle is reset to "empty" at the same moment (`Oracle.fresh_database`); it still never reads a dump to decide what is expected. `compaction`, `disk-full` and `recovery-crash` keep one database.
3. `tester/modes.py` `read_back`: `open` runs first and alone (it repairs); then `check` and `dump --hex`, which only read the file open has just made clean, run at the same time.

## Results

| Run | Before | After |
|---|---|---|
| `./torture --iterations 200` (default) | 181.05 s (clean clone, seed 880022) and 169.4 s (offline, seed 990011) | **139.78 s** after changes 1–2 (seed 515051), **126.19 s** after change 3 (seed 515052) — PASS 200/200, `RESULT: PASS` |
| `--mode large-values`, 100 iterations | 2,271.83 s (clean clone, seed 880033) | 20 iterations in **25.37 s** (seed 515061), average 1.26 s per iteration, i.e. about 126 s projected for 100 |
| `--mode recovery-crash`, 100 iterations | 247.35 s (seed 880055) | not changed (one database on purpose): 10 iterations 26.80 s, average 2.44 s |

After change 3 (`--iterations 200 --seed 515052`):
```
safety: PASS 200/200 (no broken promise found)
average iteration 0.63 s; projected 200 iterations: 126 s
time per iteration by phase: random kill delay (writes in flight) 216 ms, kvdb.py open 134 ms, kvdb.py check + dump (together) 131 ms, serve start until READY 118 ms, kill + drain + join writers 27 ms, copy of the database (failure evidence) 3 ms, oracle comparison 0 ms
coverage: 91616 ACKs (minimum 400); ACKed by type: put 45744, del 18579, batch 27293
RESULT: PASS
real 126.19
```

**Target "200 default iterations under 120 s": NOT met — 126.19 s.** What is left per iteration is the random kill delay (about 200 ms, the test itself) and three Python process starts of 118–134 ms each (interpreter + argparse + engine import), which we did not want to merge or skip.

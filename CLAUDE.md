# CLAUDE.md — Kill It and Prove It (Python)

> Standing instructions for Claude Code, loaded at the start of every session.
> Written by the team before the event as instructions only. It contains no code.

## 1. Mission

We are a three-person student team in a 24-hour hackathon. We build two programs, in Python, using **only the standard library**:

1. **The engine** — an embedded, single-process, crash-safe key-value database (`src/kv/`), with a command-line wrapper (`kvdb.py`).
2. **The torture tool** — an independent program (`torture.py` + `tester/`) that hard-kills the engine at random moments and checks that every promise was kept.

At the demo the judges hard-kill our engine hundreds of times, then point at any line of our code and ask what is on disk if the process dies there. **Correct and explainable beats clever.** A simple append-only log plus an in-memory dict is enough for full marks (brief, Section 15).

## 2. The invariant (brief, Section 4)

After a crash at any point, and after open finishes recovery:

1. Every operation that returned success before the crash is present — unless a later successful operation replaced it.
2. Every operation that had not returned success is fully present or fully absent.
3. A batch counts as one operation. It is never partly present.
4. No key ever holds a value that was never written to it.
5. Open never fails on a file our engine produced, however it was killed — including during recovery or compaction.

The scenario table is stricter than rule 2 in places. **A4: killed after the commit sync but before success is returned → the new value MUST be present.**

## 3. Sources of truth, in this order

1. Official clarifications from the judges, copied word for word into `docs/clarifications.md`.
2. The official brief, *Kill It and Prove It*, at `/Users/apple/Documents/Hackathons/srinathon 3.0/outcome/CLAUDE_CODE_MASTER_PROMPT_PYTHON.md`. Section numbers in this file refer to it.
3. This file.

If two of these disagree, stop and tell us. Never guess a rule.

## 4. Hard rules — breaking one can disqualify the team

- **Python standard library only.** Never run `pip install`. No `requirements.txt`. No outside package of any kind.
- **Banned even though they ship with Python:** `sqlite3`, `dbm`, `shelve`. They store keys and values for us, so the brief bans them (Section 3). Never import them, not even for a quick test.
- No database, storage engine, persistence library, ORM or key-value wrapper. No library that implements logging, journaling, B-trees or LSM trees.
- **No code copied** from any existing database project.
- **Offline.** Everything runs with no network.
- **The prompt log is untouchable.** Never edit, reorder, shorten or delete anything in `PROMPTS.md` or `prompts/`. Only the logging hook and humans add to them. The one exception is the Phase 9 merge, which copies entries byte for byte.
- **Git history is evidence.** No force-push. No amending or rebasing commits that are already pushed.
- **The tester is independent.** `torture.py` and `tester/` never import `src.kv`. The oracle records requests and ACKs itself and never reads the database to decide what is expected. After a restart it never copies state from a dump.
- **Real kills only.** `Popen.kill()` — a hard kill — after a random delay in milliseconds while writes are in flight. Never a graceful signal. Random mode never uses pause points.
- **Never fabricate.** No invented test output, timings, seeds, bugs, failed attempts or "verified" claims. Anything not actually run is written as `not verified` or `NOT TESTED`.
- **Never weaken a test or a workload to make it pass.** Report the failure instead.
- **Expiry is not implemented.** It is an oral design question at the demo.

## 5. Platform — fill in during Phase 0 from real commands

| Item | Value |
|---|---|
| Operating system | macOS 15.8.1 (build 24H32), Darwin 24.6.0, x86_64. Shell: zsh |
| Python command that works here | `python3` (`/usr/local/bin/python3`). `python` and `py` are not found |
| Python version | 3.13.9 |
| Disk and filesystem | internal SSD (PCI-Express), APFS, journaled; repository on `/System/Volumes/Data` |
| Hard kill by hand | `kill -9 <pid>` → exit status 137 (128+9). `Popen.kill()` → returncode -9 |
| Directory sync after a file swap | supported — `os.open(dir, os.O_RDONLY)` + `os.fsync` after `os.replace` returned OK — tested on: 2026-11-06, this laptop, `evidence/platform-probe.txt` |
| Replacing a file that is still open | works — `os.replace` succeeded with a write and a read handle open; the OLD read handle still read the old bytes, a fresh handle read the new ones, so handles must be reopened after a swap — tested on: 2026-11-06, this laptop, `evidence/platform-probe.txt` |

## 6. Repository layout

```
kvdb.py                 the CLI — python kvdb.py <command> ...
torture.py              the tester — python torture.py --iterations 500 --seed 1
src/
  __init__.py
  kv/                   THE ENGINE (package src.kv)
    __init__.py
    layout.py           byte format: encode and decode
    db.py               open, put, get, delete, batch, compact, close
    recovery.py         reading the file back after a crash; check
    pause.py            pause points
tester/                 THE TESTER'S INTERNALS — never imports src.kv
  __init__.py
  oracle.py  workload.py  modes.py
tools/
  promptlog.py          the hook that logs every Claude Code prompt
  check_imports.py      proves we use only allowed standard-library modules
tests/                  unittest tests: python -m unittest discover -s tests -v
CLAUDE.md  README.md  ARCHITECTURE.md  BREAKING.md  PROMPTS.md
prompts/                raw prompt logs, one file per person and tool
diagrams/               architecture.mmd (Mermaid source) and an exported image
evidence/               real outputs: hex dumps, benchmarks, torture runs, failures
docs/                   clarifications, photo of the paper design
rehearsal/              crash-here questions and answers (not committed)
```

- **Build — one command, from the repository root:** `python -m compileall -q .` — it checks every file for syntax errors and needs nothing installed.
- **Dependency proof:** `python tools/check_imports.py` must print `OK`.
- **Tests:** `python -m unittest discover -s tests -v`
- **Never name a file after a standard-library module** (`test.py`, `random.py`, `struct.py`, `queue.py`, `select.py`, `signal.py`, `io.py`, `types.py`, `code.py`). It silently replaces the real module.
- **Never create a top-level folder named `kvdb/` or `torture/`.** The brief's sample layout shows `torture/`; README explains why ours is `tester/` plus `torture.py`.
- Test databases and torture work folders live on the real disk inside the repository (`.torture-work/`), never in `/tmp`, which can be a RAM disk and hide sync bugs (brief, Section 15).

## 7. Engine contract (`src.kv`)

| Operation | Takes | Gives back |
|---|---|---|
| open | path, max bytes (0 = no cap) | the database object and a recovery report: key count, seconds taken, bytes cut, trailer restored |
| put | key (bytes), value (bytes) | nothing, or raises an error |
| get | key (bytes) | the value as bytes, or `None` when the key is missing |
| delete | key (bytes) | nothing, or raises an error |
| batch | ordered list of (put or delete, key, value) | nothing, or raises an error |
| compact | — | nothing, or raises an error |
| close | — | nothing |
| check | path | (ok, reason) — **read-only, never repairs** |

- Keys: 1 to 1,024 bytes. Values: 0 to 1 MB, where 1 MB = `[DECIDED IN PHASE 1]` bytes. Batches: 1 to 10,000 operations. At least 1,000,000 keys.
- **Inside the engine, keys and values are always `bytes`, never `str`.**
- An empty value (`b""`) is not a missing key (`None`). Deleting a missing key succeeds.
- Invalid input is rejected **before any byte is written**.
- A batch applies in list order. The last operation on a key wins.
- put, delete and batch all go through **one** transaction write path.
- Errors are raised as a small set of named exception classes (invalid input, size cap reached, not our file, database failed). README lists them.

## 8. On-disk format — FROZEN IN PHASE 1

`[Phase 1 writes the approved byte layout here. Until it does, write no engine code.]`

## 9. Write order — FROZEN IN PHASE 1

`[Phase 1 writes the approved numbered sequence here.]`

## 10. Recovery — FROZEN IN PHASE 1

`[Phase 1 writes the approved steps here.]`

## 11. Compaction order — FROZEN IN PHASE 7

`[Phase 7 writes it here before any compaction code.]`

## 12. File handles and locks

- **Write handle:** the data file opened in binary mode with `buffering=0`. Every write loops until all bytes are written, because an unbuffered write can be short. The writer always seeks to the tracked end offset before writing — never trusts the current position.
- **Read handle:** a second handle, binary, `buffering=0`, used only by get. Unbuffered so it can never return stale bytes after a rollback.
- **`write_lock`** (`threading.Lock`): held by every mutation from validation until the index is updated. Writers never overlap.
- **`index_lock`** (`threading.Lock`): a reader holds it across the dict lookup **and** the read from the read handle. A writer takes it only to apply an already-committed transaction to the dict. Compaction takes both locks.
- Result: a reader sees the state before a batch or after it, never in between, and readers never wait for a disk write or sync.
- Python's GIL does **not** make a multi-step write safe. The locks do.
- Fallback allowed by the brief if time is short: one global lock for everything. Phase 1 records which design we chose.

## 13. Pause points (targeted crash tests)

If the environment variable `KVDB_PAUSE_AT` equals a pause point's name, the engine writes `PAUSE <name>` to **stderr**, flushes it, and then blocks forever, so the torture tool can hard-kill it at exactly that step. With the variable unset there is no effect.

Names: `create.mid-header` · `write.before-body` · `write.mid-body` · `write.after-body` · `write.after-body-sync` · `write.after-marker` · `write.after-commit-sync` · `write.after-index` · `recovery.replay` · `recovery.before-cut` · `recovery.after-cut` · `recovery.before-trailer` · `compact.mid-write` · `compact.before-swap` · `compact.after-swap` · `compact.before-cleanup` · `close`

Every pause point appears in BREAKING.md's crash table with its file and function. Results from targeted runs are always reported separately from random runs.

## 14. CLI and serve protocol — shared by the engine and the tester

Changing anything here means telling both owners and updating README.md. Every command starts `python kvdb.py` (use the Python command recorded in section 5).

| Command | Prints |
|---|---|
| `open <path> [--max-bytes N]` | `keys=<n> recovery_ms=<n> bytes_cut=<n> trailer_restored=<yes|no>` |
| `put <path> <key> <value>` | `OK` or `ERR <reason>` |
| `get <path> <key>` | the value, or `NOTFOUND` |
| `del <path> <key>` | `OK` |
| `batch <path> <file>` | `OK` or `ERR <reason>` — one `put <k> <v>` or `del <k>` per line |
| `dump <path> [--hex]` | one `<key> <value>` per line, sorted by raw key bytes |
| `check <path>` | `OK` or a reason. **Never modifies the file.** |
| `serve <path>` | see below |

**Keys and values in arguments, batch files and serve lines:**

- plain text — no spaces, and not starting with `hex:` or `file:`
- `hex:<hex digits>` — any bytes, including the empty value (`hex:`)
- `file:<path>` — value read from a file. **A 1 MB value cannot fit on a command line**: Linux limits one argument to about 128 KB and Windows the whole command line to about 32,000 characters.

Printed values use plain text only when that is unambiguous, otherwise `hex:`. `dump --hex` always prints both fields in hex, so a stored value `NOTFOUND` can never be confused with a missing key.

**serve** reads one request per line from stdin (in binary):

```
<id> put <key> <value>
<id> del <key>
<id> get <key>
<id> batch <n>        then exactly n lines:  put <key> <value>  |  del <key>
```

and writes one line per result to stdout:

```
ACK <id>              only after the change is durable
VAL <id> <value>
NOTFOUND <id>
ERR <id> <reason>     nothing was changed
```

- stderr carries `PID <n>` at start, `PAUSE <name>` at pause points, and diagnostics — always flushed.
- `<id>` is any token without spaces, unique per request. The tester uses `w<writer>-<sequence>`.
- One thread reads complete requests. A `concurrent.futures.ThreadPoolExecutor` with at least two workers calls the engine concurrently. Writes to stdout happen under a lock and are **flushed after every line** — when stdout is a pipe, Python otherwise holds the ACKs back and the tester never sees them.

## 15. Python traps — check every change against this list

1. **Binary mode** for every data file: `'rb'`, `'r+b'`, `'wb'`. Text mode on Windows rewrites line endings and corrupts bytes.
2. **Flush before fsync.** A buffered Python file keeps bytes in its own buffer; `os.fsync` cannot see them until `flush()`. The data file uses `buffering=0` to avoid this, but any buffered file must be flushed first.
3. **Unbuffered writes can be short.** Loop until every byte is written, or treat it as a failure and roll back.
4. **`bytes`, never `str`**, inside the engine. Convert only at the command line.
5. **`struct` formats start with `<`** (little-endian, no hidden padding). Prebuild `struct.Struct` objects for speed.
6. **Check every length** against the limits **and** the bytes left in the file **before reading**. A damaged length could ask Python for gigabytes.
7. **`os.replace`, never `os.rename`**, for the compaction swap. `os.rename` fails on Windows when the target exists.
8. **On Windows, a file that is still open cannot be replaced.** Close both handles before `os.replace`, then reopen. Test it.
9. **Directory sync:** on Linux and macOS, open the directory with `os.open(dir, os.O_RDONLY)` and `os.fsync` it after the swap. On Windows this is not available — record it as a platform limit.
10. **Text files** (logs, README, prompt logs) are always opened with `encoding="utf-8"`. Windows otherwise uses a different default and breaks on non-English characters.
11. **Flush stdout after every ACK** (`print(..., flush=True)` or an explicit flush).
12. **Hard kill** = `Popen.kill()` — SIGKILL on Linux and macOS, TerminateProcess on Windows. Never `terminate()` on Linux and macOS (that is SIGTERM). **Ctrl+C is not a crash test.**
13. The tester starts the engine with `sys.executable`, so both run on the same Python.
14. **Randomness** comes from one `random.Random(seed)` per run, never the module-level functions.
15. **Recovery speed is the biggest Python risk.** A plain Python loop over a million records can easily exceed 2 seconds. Read large chunks, use `struct.Struct.unpack_from` on a `memoryview` or `mmap` (standard library, allowed) instead of slicing, and compute the CRC with `zlib.crc32` on whole spans. Measure early.

## 16. Evidence rules

- Every claim in README.md, ARCHITECTURE.md and BREAKING.md names a real file and function, and a test or run that exists.
- Raw outputs go to `evidence/`. Failures are kept, never deleted.
- `not verified` is allowed. A wrong claim costs more than a missing one (brief, Sections 8 and 9A).
- Claims are limited to **process death**, which is what the brief tests. Any claim about power loss needs separate evidence.

## 17. How every phase works

1. Read this file. Do only the phase we asked for.
2. Work in small steps. After each piece run `python -m compileall -q .`, `python tools/check_imports.py` and `python -m unittest discover -s tests -v`, and show the real output.
3. Explain what you wrote in simple English, in short sentences.
4. Finish every phase with:
   - the files changed;
   - the real test output;
   - the documents you updated **from evidence**;
   - three crash-here questions about the new code, with the answers written to `rehearsal/` and **not** shown in chat — we try first;
   - one proposed commit message per logical piece.
5. **STOP.** Do not start the next phase. Do not commit until a human types `commit`.
6. If a requirement cannot be met, name the scenario ID and the reason.

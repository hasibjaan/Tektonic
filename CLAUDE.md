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
    errors.py           the named error classes (added in Phase 3)
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

- Keys: 1 to 1,024 bytes. Values: 0 to 1 MB, where 1 MB = **1,048,576** bytes (decided in Phase 1). Batches: 1 to 10,000 operations. At least 1,000,000 keys.
- **Inside the engine, keys and values are always `bytes`, never `str`.**
- An empty value (`b""`) is not a missing key (`None`). Deleting a missing key succeeds.
- Invalid input is rejected **before any byte is written**.
- A batch applies in list order. The last operation on a key wins.
- put, delete and batch all go through **one** transaction write path.
- Errors are raised as a small set of named exception classes (invalid input, size cap reached, not our file, corrupt file, database busy, database failed). README lists them. "Corrupt file" was added in Phase 1: open refuses a file with a bad byte that a kill cannot produce (section 10).
- The size cap is fixed when the file is created. Opening an existing file with a different non-zero `--max-bytes` is rejected as invalid input; the file is not changed.

## 8. On-disk format — FROZEN IN PHASE 1

Approved by the team on 2026-11-06 (Part D reference design plus the Phase 1 decisions). All integers are **unsigned, little-endian**. The `struct` format is given for each part.

**File header — 16 bytes at offset 0** — `<4sHHQ`

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 4 | magic | `b"KVDB"` |
| 4 | 2 | version | `1` — any other value: refuse |
| 6 | 2 | flags | `0` — any other value: refuse |
| 8 | 8 | max_bytes | `0` = no cap; otherwise the cap on the **whole file size** in bytes (header included). Fixed at creation |

**Transactions** follow back to back from offset 16. Every put, delete and batch is exactly one transaction:

| Part | Size | Fields | `struct` |
|---|---|---|---|
| Transaction header | 3 | type u8 = `0xA1` · op_count u16, 1 to 10,000 | `<BH` |
| Each operation | 7 + key_len + val_len | op u8 (`1` = put, `2` = delete) · key_len u16, 1 to 1,024 · val_len u32, 0 to 1,048,576 (always 0 for delete) · key bytes · value bytes | `<BHI` |
| Commit marker | 16 | magic `b"CMIT"` · body_len u64 · crc32 u32 | `<4sQI` |
| Trailer | 1 | `0xEE` — a throwaway byte, so a 1-byte cut of the end (F4) loses nothing | `<B` |

- **body** = the transaction header plus every operation. `body_len` = the number of bytes in body. `crc32` = `zlib.crc32` over body. The checksum does not cover the marker or the trailer.
- **Byte cost:** put = 27 + key + value. Delete = 27 + key. Batch of n ops = 20 + 7n + Σkey + Σvalue. Empty database file = 16 bytes.
- The largest legal body is 3 + 10,000 × (7 + 1,024 + 1,048,576) = 10,496,070,003 bytes. That needs the u64 body_len. It must be **streamed**, never built in memory (8 GB RAM on this laptop).

**Index — in memory only, never on disk:** a dict from key (`bytes`) to one packed integer `(value_offset << 21) | value_length`. 21 bits, because the length can be 1,048,576 = 2²⁰. Delete removes the key. An empty value is present with length 0; a missing key is absent from the dict.

## 8a. Index snapshot `<path>.idx` — REMOVED IN THE FINAL FIX ROUND

Phase 6 added a sidecar index cache `<path>.idx`. It was **removed on 2026-11-07** (final fix round, Fix 2, team decision A): the brief allows exactly one data file plus a lock file and compaction temporary files, the cache was not on that list, and no judge had ruled on it. The authoritative state is now in exactly one data file. Open deletes a `<path>.idx` or `<path>.idx.tmp` left by an older version and never uses it. Measured cost (`evidence/no-cache-timing.txt`): opening 1,000,000 single-put transactions takes 2.423 s (over the 2 s limit, F3); 1,000,000 keys written in batches take 1.354 s.

## 9. Write order — FROZEN IN PHASE 1

One transaction write path for put, delete and batch. Pause points are shown in brackets.

```
take the database lock (section 12)
 1. validate the WHOLE request: bytes types, key 1–1,024, value ≤ 1,048,576,
    1–10,000 ops, delete has no value, database not failed, and when
    max_bytes > 0: end + transaction size ≤ max_bytes
    → any problem: raise, file untouched
 2. remember the end offset E (tracked, never the file position)  [write.before-body]
 3. seek to E; write the transaction header and every op, looping until all
    bytes are out, CRC built up as bytes are produced              [write.mid-body]
                                                                   [write.after-body]
 4. os.fsync                         ← the body is on disk          [write.after-body-sync]
 5. write the commit marker and the trailer (loop)                 [write.after-marker]
 6. os.fsync                         ← the transaction is committed [write.after-commit-sync]
 7. apply every op to the dict in list order; end = E + size       [write.after-index]
 8. release the lock and return success — serve prints ACK and flushes
on any error in 3–6: truncate to E, os.fsync, raise.
  If that truncate or fsync also fails: mark the database failed; every later
  write is refused until it is reopened.
```

**Sync call — decided in Phase 1 (option a):** every "os.fsync" above is plain `os.fsync`. We claim **process death only** (section 16). On macOS `os.fsync` moves data to the drive but does not make the drive write it to permanent storage or keep the order — that needs `fcntl.F_FULLFSYNC`, which we do not use. Power loss and OS crashes are therefore **not claimed**. Evidence: `evidence/fsync-macos.txt`. Recorded as a known weakness in BREAKING.md section 3.

## 10. Recovery — FROZEN IN PHASE 1

**Rule:** a kill only ever cuts the end of the file. So there are exactly two kinds of bad file:

- **TORN** — a field needs more bytes than the file has left. Repair it by cutting the tail.
- **CORRUPT** — any other bad byte, which a kill cannot produce. Open **refuses**, the file is left untouched, and nothing is written.

```
1. File missing → write the 16-byte header (max_bytes from the caller),
   loop, os.fsync the file, os.fsync the directory.          [create.mid-header]
2. File shorter than 16 bytes:
     its bytes are a prefix of b"KVDB" 01 00 00 00 (bytes 8+ are max_bytes, any value)
       → interrupted creation: write a fresh full header at offset 0, os.fsync
     otherwise → refuse (not our file), untouched
3. magic ≠ KVDB → refuse (not our file). version ≠ 1 or flags ≠ 0 → refuse. Untouched.
   A non-zero max_bytes from the caller that differs from the header → refuse (invalid input).
3a. Delete a leftover <path>.compact.tmp (section 11) and a <path>.idx / <path>.idx.tmp
    left by an older version (section 8a); neither is ever used. The index starts
    empty and p = 16.
4. Scan from p (mmap, prebuilt Struct.unpack_from, no slicing copies).
   At each transaction start:
     p == file size                              → clean end, stop
     fewer than 3 bytes left                      → TORN at p
     type ≠ 0xA1 or op_count not 1–10,000         → CORRUPT
     for each op:
       fewer than 7 bytes left                    → TORN at p
       op not 1/2, key_len not 1–1,024,
       val_len > 1,048,576, delete with val_len ≠ 0 → CORRUPT
       fewer than key_len + val_len bytes left    → TORN at p
       stage (op, key, value offset, value length) — NOT in the dict yet
     fewer than 16 bytes left for the marker      → TORN at p
     magic ≠ CMIT, body_len ≠ real body length,
     crc32 ≠ zlib.crc32(body)                     → CORRUPT
     apply the staged ops to the dict in order     [recovery.replay]
     next position q = end of the marker:
       q == file size → TRAILER MISSING (allowed only here, at the very end); stop
       byte at q == 0xEE → p = q + 1, continue
       anything else     → CORRUPT
5. CORRUPT anywhere → refuse to open (corrupt file), report the offset, write nothing.
6. TORN at p                                       [recovery.before-cut]
     → os.ftruncate to the ABSOLUTE offset p, os.fsync  [recovery.after-cut]
     (safe to repeat: a second run truncates to the same p)
7. TRAILER MISSING                                 [recovery.before-trailer]
     → write 0xEE at the end, os.fsync — BEFORE any new write is accepted.
   Steps 6 and 7 never both apply: a TORN stop is always right after a trailer.
8. end = file size. Report: key count, seconds, bytes cut, trailer restored yes/no.
```

**check** runs steps 1–4 **read-only**, never repairs, and prints exactly one of:

| File | check prints |
|---|---|
| clean | `OK` |
| torn tail | `TORN_TAIL at <offset>, <n> bytes` |
| last trailer missing | `MISSING_TRAILER at <offset>` |
| a bad byte a kill cannot produce | `CORRUPT at <offset>: <what>` |
| not our file (magic, version, flags, short non-prefix) | `NOT_OUR_FILE: <why>` |
| shorter than 16 bytes, but a prefix of a valid header | `INTERRUPTED_CREATE (<n> bytes)` |
| no file at the path | `NO_FILE` |

Exit codes for check are decided in Phase 4.

## 11. Compaction order — FROZEN IN PHASE 7

Approved by the team in Phase 7 (2026-11-06). `src/kv/db.py` `DB.compact` holds the global lock for the whole run.

```
 1. delete a leftover <path>.compact.tmp (never trusted)
 2. write <path>.compact.tmp in the SAME folder: 16-byte header with the SAME max_bytes,
    then every live key as ordinary put transactions in batches (at most 10,000 ops and
    64 MiB of values each), values read through the read handle in file order
                                                              [compact.mid-write] after the first batch
 3. flush, fsync, close the temporary file
 4. validate: a full read-only scan of it must end CLEAN with exactly the live keys,
    the same value lengths and the expected size — else delete it and raise
    CompactionFailed; the original is untouched
 5. (removed in the final fix round: there is no index snapshot any more)
 6. close BOTH live handles
                                                              [compact.before-swap]
 7. os.replace(<path>.compact.tmp, <path>)                    ← the only switch
                                                              [compact.after-swap]
 8. fsync the folder
 9. reopen both handles; index = the one from step 4; end = new size (checked with fstat)
                                                              [compact.before-cleanup]
10. release the lock
failure in 2–4: delete the temporary file, raise CompactionFailed, database still usable
failure in 6–9: mark the database failed (writes refused until reopen); the disk holds one
                complete database, old or new
```

- **At open** (`src/kv/recovery.py` `recover`): a leftover `<path>.compact.tmp` is deleted, never used.
- **E5:** when a write does not fit under `max_bytes`, compute the exact size of a compacted file WITH that write applied (from the index alone). If it fits, compact with the write included — step 7 is then that write's commit point — and return success. If not, raise `SizeCapReached`; nothing is written.
- A compacted file larger than `max_bytes` is a compaction failure (E4); the temporary file itself is not counted against the cap.

## 12. File handles and locks

- **Write handle:** the data file opened in binary mode with `buffering=0`. Every write loops until all bytes are written, because an unbuffered write can be short. The writer always seeks to the tracked end offset before writing — never trusts the current position.
- **Read handle:** a second handle, binary, `buffering=0`, used only by get. Unbuffered so it can never return stale bytes after a rollback.
- **Chosen in Phase 1: ONE global lock.** `lock` (`threading.Lock`) is held by put, delete and batch from validation until the index is updated, by get across the dict lookup **and** the read from the read handle, and by compact and close for their whole run. Nothing overlaps.
- Result: a reader sees the state before a batch or after it, never in between (D4), because get cannot run while a batch holds the lock. The cost, accepted by the team: a get waits behind any write and its two fsyncs.
- Python's GIL does **not** make a multi-step write safe. The lock does.
- **Database lock (Phase 6, team decision):** `src/kv/db.py` `open_db` takes an exclusive, non-blocking `fcntl.flock` on `<path>.lock` BEFORE recovery and holds it until `close`. A second open — another process or the same one — gets `DatabaseBusy` and changes nothing. The kernel drops the lock when the process dies, so a hard kill never leaves it locked. A separate lock file (not the data file) because the data file may not exist yet and Phase 7 compaction replaces it with `os.replace`. `check` takes no lock (it only reads). Where `fcntl` is missing (Windows), `open_db` raises `UnsupportedPlatform` instead of running unprotected (final fix round, Fix 3, team decision: the demo laptop is macOS).
- Not chosen: the two-lock design (`write_lock` plus `index_lock`), where readers never wait for a sync. If we switch to it, this section changes first.

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
| `compact <path>` (Phase 7) | `OK bytes_before=<n> bytes_after=<n>` |

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
<id> compact          (Phase 7) ACK <id> when compaction has finished
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
- Added in Phase 4 (both owners: read README "serve protocol" and "Commands"): stderr also carries `READY keys=<n> recovery_ms=<n> bytes_cut=<n> trailer_restored=<yes|no>` once recovery is done. Phase 7: serve prints `COMPACT begin` and `COMPACT end` on stderr around every compaction (asked for, or started by E5). (A ` snapshot=<yes|no>` field added to READY in Phase 6 was removed again with the cache in the final fix round.) Requests in flight run concurrently, so replies can come back in any order. `dump --hex` prints `hex:<key> hex:<value>` per line. Exit codes: 0 success (also NOTFOUND and check OK), 1 check found a problem, 2 usage error, 3 `ERR`.
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

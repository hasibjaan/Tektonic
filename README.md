# Kill It and Prove It — crash-safe key-value store (Python, standard library only)

## Build

From the repository root, with only Python 3.10 or newer installed (we use `python3`, version 3.13.9, on macOS 15.8.1):

```
python3 -m compileall -q .           # the build: checks every file for syntax errors
python3 tools/check_imports.py       # prints OK: every import is standard library or ours
python3 -m unittest discover -s tests -v
```

Nothing is installed; there is no `requirements.txt`. The launchers `./kvdb` and `./torture` (macOS and Linux) run `kvdb.py` and `torture.py` with `python3`. On Windows run `py kvdb.py ...` and `py torture.py ...` instead (not tested on Windows).

Note: the torture tool lives in `tester/` and `torture.py`, not in a `torture/` folder, so no folder clashes with a `./torture` launcher in the repository root.

## Requirements

Python 3.10 or newer (the import checker uses `sys.stdlib_module_names`). Standard library only — `tools/check_imports.py` proves it and refuses `sqlite3`, `dbm` and `shelve`.

## Commands

> Status: implemented in `kvdb.py` (Phase 4). Every example below is a **real run on our laptop**: `python3 tools/readme_commands.py --write` ran each `$` line in a fresh folder and pasted its output; `tests/test_cli.py` runs them again and fails if the output differs (only `recovery_ms` values are not compared).

| Command | Prints | Exit code |
|---|---|---|
| `./kvdb open <path> [--max-bytes N]` | `keys=<n> recovery_ms=<n> bytes_cut=<n> trailer_restored=<yes\|no>` | 0 |
| `./kvdb put <path> <key> <value>` | `OK` | 0 |
| `./kvdb get <path> <key>` | the value, or `NOTFOUND` | 0 |
| `./kvdb del <path> <key>` | `OK` (also for a missing key) | 0 |
| `./kvdb batch <path> <file>` | `OK` — the file has one `put <k> <v>` or `del <k>` per line; blank lines are skipped | 0 |
| `./kvdb dump <path> [--hex]` | one `<key> <value>` per line, sorted by raw key bytes; `--hex` prints both as `hex:` | 0 |
| `./kvdb check <path>` | `OK`, or one reason (see "Recovery algorithm"); **never changes the file** | 0 if `OK`, 1 otherwise |
| `./kvdb serve <path>` | see "serve protocol" | 0 when stdin closes |
| `./kvdb compact <path>` (Phase 7) | `OK bytes_before=<n> bytes_after=<n>` — rewrites the file with only the live keys | 0 |
| any command that the engine refuses, or a file that is not ours / corrupt | `ERR <ErrorClass>: <reason>` on stdout | 3 |
| wrong arguments | usage on stderr | 2 |

- **Keys and values:** plain text (no spaces, not starting with `hex:` or `file:`), `hex:<digits>` for any bytes (the empty value is `hex:`), or `file:<path>` (needed for a 1 MB value: it cannot fit on a command line). A value that starts with `-` must come after `--`, e.g. `./kvdb put db.kv -- k -5`, or be written as `hex:`.
- **Printed values** use plain text only when it is unambiguous: visible ASCII, no space, not starting with `hex:`/`file:`, and not the word `NOTFOUND`. Anything else is printed as `hex:`.
- Every command except `check` runs recovery first, so `get`, `dump` and the others may repair a torn tail, exactly like `open`.
- **One open at a time.** Every command except `check` first takes an exclusive lock on `<path>.lock` (`fcntl.flock`, macOS and Linux). While `serve` runs, `./kvdb get demo.kv k` prints `ERR DatabaseBusy: …` and exits 3 instead of running recovery under serve's feet (`tests/test_lock.py`). A hard kill releases the lock. `check` takes no lock: it never writes, but if it runs while serve is mid-write it may report that write's bytes as a torn tail.
- **Files next to a database `<path>`:** `<path>.lock` (the lock, empty), `<path>.idx` (index snapshot, a cache) and, only while one is being written, `<path>.idx.tmp` (snapshot) or `<path>.compact.tmp` (compaction). Open deletes both temporary files; it never uses them.

<!-- commands:begin -->
```console
$ ./kvdb open demo.kv
keys=0 recovery_ms=1 bytes_cut=0 trailer_restored=no
$ ./kvdb put demo.kv hasib 20
OK
$ ./kvdb get demo.kv hasib
20
$ ./kvdb get demo.kv nobody
NOTFOUND
$ ./kvdb put demo.kv empty hex:
OK
$ ./kvdb get demo.kv empty
hex:
$ ./kvdb put demo.kv tricky NOTFOUND
OK
$ ./kvdb get demo.kv tricky
hex:4e4f54464f554e44
$ printf 'put ali 30\nput x 1\ndel x\n' > ops.txt
$ ./kvdb batch demo.kv ops.txt
OK
$ ./kvdb get demo.kv x
NOTFOUND
$ ./kvdb del demo.kv hasib
OK
$ ./kvdb dump demo.kv
ali 30
empty hex:
tricky hex:4e4f54464f554e44
$ ./kvdb dump demo.kv --hex
hex:616c69 hex:3330
hex:656d707479 hex:
hex:747269636b79 hex:4e4f54464f554e44
$ ./kvdb check demo.kv
OK
$ ./kvdb put demo.kv "a b" 1; echo "exit=$?"
ERR BadToken: key b'a b' contains a space: use hex:
exit=3
$ printf '1 put hasib 21\n2 batch 2\nput a 1\nput b 2\n' | ./kvdb serve demo.kv 2>/dev/null | sort
ACK 1
ACK 2
$ printf 'g1 get hasib\n' | ./kvdb serve demo.kv 2>/dev/null
VAL g1 21
$ python3 -c "import os; os.truncate('demo.kv', os.path.getsize('demo.kv') - 1)"
$ ./kvdb check demo.kv; echo "exit=$?"
MISSING_TRAILER at 275
exit=1
$ ./kvdb open demo.kv
keys=6 recovery_ms=1 bytes_cut=0 trailer_restored=yes
$ ./kvdb check demo.kv
OK
$ ./kvdb open small.kv --max-bytes 100
keys=0 recovery_ms=1 bytes_cut=0 trailer_restored=no
$ ./kvdb put small.kv k1 0123456789
OK
$ ./kvdb put small.kv k2 0123456789
OK
$ ./kvdb put small.kv k3 0123456789; echo "exit=$?"
OK
exit=0
$ ./kvdb put small.kv k4 0123456789012345678901234567890123456789; echo "exit=$?"
ERR SizeCapReached: transaction needs 69 bytes; file is 93 of max_bytes 100; even compacted with it the file would be 142 bytes
exit=3
$ ls -l small.kv | awk '{print $5}'
93
$ printf 'not a database' > foreign.kv
$ ./kvdb get foreign.kv k; echo "exit=$?"
ERR NotOurFile: file has 14 bytes and is not the start of a KVDB header
exit=3
```
<!-- commands:end -->

## serve protocol

> Status: implemented in `kvdb.py` `Server` and `cmd_serve` (Phase 4); tested in `tests/test_cli.py` `ServeTest`, including ACK → `kill -9 <PID>` → reopen.

`kvdb serve <path>` reads one request per line from stdin (binary) and writes one line per result to stdout.

Requests:

```
<id> put <key> <value>
<id> del <key>
<id> get <key>
<id> batch <n>        then exactly n lines:  put <key> <value>  |  del <key>
<id> compact          (Phase 7) rewrite the file with only the live keys
```

Replies:

```
ACK <id>              only after the change is durable (after the commit fsync)
VAL <id> <value>
NOTFOUND <id>
ERR <id> <reason>     nothing was changed
```

- Keys and values: plain text (no spaces, not starting with `hex:` or `file:`), or `hex:<hex digits>` for any bytes including the empty value (`hex:`), or `file:<path>`. A 1 MB value cannot fit on a command line, so use `file:` for it.
- `<id>` is any token without spaces, unique per request. The torture tool uses `w<writer>-<sequence>`.
- stderr carries `PID <n>` at start, `PAUSE <name>` at pause points, and diagnostics, always flushed.
- One thread reads complete requests. A thread pool with at least two workers calls the engine. Every stdout line is written under a lock and flushed at once.
- stderr: `PID <n>` first (before recovery), then `READY keys=<n> recovery_ms=<n> bytes_cut=<n> trailer_restored=<yes|no>` once recovery has finished and requests are accepted.
- **Requests in flight run concurrently** (4 worker threads). Two requests sent without waiting can finish in either order, and their replies can come back in either order. A client that needs A before B must wait for A's ACK before sending B.
- A batch is read completely (its header line plus n lines) before anything is decided. If any of its lines is malformed, the whole batch gets `ERR` and nothing is written. If the count itself is not a number from 1 to 10,000, the request gets `ERR` and the following lines are read as requests of their own.
- `compact` is answered `ACK <id>` only after the swap and cleanup (CLAUDE.md section 11). Around every compaction — asked for, or started by a write that did not fit under `--max-bytes` (E5) — stderr carries `COMPACT begin` and `COMPACT end`.
- When stdin closes, serve finishes the requests it has, closes the database and exits with code 0.
- `ERR` means the engine raised and the transaction was rolled back, so it is absent. One rare exception: if the second fsync fails **and** the rollback then fails too, the reply is `ERR … DatabaseFailed` and the transaction may still be present after a reopen — allowed by invariant rule 2, because success was never returned.

## Torture tool

> Status: implemented in `torture.py` and `tester/` (Phase 5). It uses the engine **only** as a separate process (`kvdb.py serve`, `open`, `check`, `dump --hex`) and never imports it — `python3 tools/check_imports.py` proves that. Honesty note: on our team the same Claude Code session that wrote the engine also wrote the tester (we work on one laptop), so the tester's independence rests on the code boundary above, on using only the documented protocol, and on the proofs below — not on a different author.

```
./torture --iterations 500 --seed 12345                 # mode default
./torture --iterations 100 --seed 999 --mode large-values
./torture --iterations 3 --seed 5 --mode targeted        # every pause point, hard kill, verify
```

| Flag | Meaning |
|---|---|
| `--seed N` | **required**; every random choice comes from one `random.Random(N)` |
| `--iterations N` | kills to run (default 500); in `targeted` mode, rounds over all pause points |
| `--mode` | `default`; `large-values` (values up to 1,048,576 bytes); `large-batches` (1,000–10,000 ops); `compaction` (12 % of requests are `compact`, 800 keys per writer, counts kills that land DURING a compaction); `recovery-crash` (100,000 prefilled keys; after each kill, serve is restarted and killed again 3 times 60–220 ms after it starts, each kill classified as before start-up / during recovery / after READY); `disk-full` (`--max-bytes 4096`: the only ERR allowed is `SizeCapReached`, and the file must stay under the cap); `overlap` (all writers share 8 keys, single puts and deletes; see below); `targeted` (every pause point); `scale` (Phase 6) |
| `--writers N` | concurrent writer threads, at least 2 (default 3) |
| `--kvdb PATH` | the engine's command line (default `kvdb.py`) |
| `--workdir PATH` | work folder on the real disk (default `.torture-work/`); `/tmp` is refused |
| `--failures-dir PATH` | where the first failure is saved (default `evidence/failures/`) |

**One iteration:** start `kvdb.py serve` (same Python, `sys.executable`) → wait for `READY` → writer threads send puts, deletes and batches → after a random delay in milliseconds, `Popen.kill()` (SIGKILL) → read the complete reply lines still in the pipe → `open`, then `check` (must say `OK`), then `dump --hex` → compare the whole state. The database file is kept across iterations, so every iteration also re-recovers everything before it (F1).

**The oracle** (`tester/oracle.py`) owns a log (`oracle.log` in the work folder) where every request is written **before** it is sent and every reply when it arrives, flushed line by line. Each writer has its own keys and at most one request in flight, so after a kill each writer's keys must equal either "everything ACKed" or "everything ACKed plus its one unanswered request, as a whole" (a batch is one request). `ERR` means the request must be absent. Any key nobody wrote is a failure. Every value names its writer, sequence, op and iteration plus a random tail, so a torn or mixed value matches nothing. After a pass the oracle only records which of its own two states matched; it never copies a value from the dump.

**Output:** one line per iteration (`iter`, `seed`, `kill_ms`, `in_flight`, `acks`, `keys`, PASS/FAIL, seconds), then `PASS n/n`, the share of kills with a request in flight, and the projected time for 200 iterations. On the first failure it prints the operation ids and expected versus found, saves the database (after the kill and after open), `oracle.log` and `trace.log` to `evidence/failures/seed-<s>-iter-<i>/`, and exits 1.

**Strict replies (Phase 7):** any `ERR` that the mode does not allow — in every mode except `disk-full`, any `ERR` at all — fails the iteration.

**The overlap oracle** (`tester/oracle.py` `OverlapOracle`): every writer uses the same 8 keys, one put or delete per request, at most one request in flight per writer. The tester's own clock ticks when it sends a request and when a reply arrives. For each key the final value must come from a request that no later ACKed request could have replaced ("later" = sent after the first one's ACK arrived), or from an unanswered one (which may have run at any time after it was sent), or be the value from before the iteration if nothing on that key was ACKed. Values are unique per request, so a match names the request. Key by key is enough because each request touches one key; batches are not used in this mode (batch atomicity is checked by the other modes).

**What a seed reproduces:** the workload (every key, value and batch) and every kill delay. It cannot reproduce thread timing, so the exact request that is in flight at the kill can differ between two runs with the same seed.

**Proof that it catches bugs** (real runs, in `evidence/torture/`):

| Engine | Result | File |
|---|---|---|
| `tester/fake_kvdb.py`: ACKs everything, stores nothing | FAIL at iteration 1, names ACKed ids such as `w1-734` | `proof-fake-serve.txt` |
| planted: drops the 5th write of each serve process | FAIL at iteration 8, names `w3-913` (earlier drops were overwritten later, which the invariant allows) | `proof-planted-bugs.txt` |
| planted: writes only half of each batch | FAIL at iteration 1 | `proof-planted-bugs.txt` |
| planted: every put also writes an invented key | FAIL at iteration 1, `invented-w1k0 … was never written` | `proof-planted-bugs.txt` |
| the real engine, seed 60606 | PASS 50/50, request in flight at 50/50 kills, 50.4 s | `run-default-50-seed-60606.txt` |
| the real engine (Phase 5 code), seed 777001 | PASS 200/200, request in flight at 200/200 kills, **355.8 s** (over the judge's ~3 minutes) | `run-default-200-seed-777001.txt` |
| the real engine with the snapshot every keys ÷ 4, seed 909090 | PASS 200/200, request in flight at 200/200 kills, 177.0 s | `run-default-200-seed-909090.txt` |
| the real engine, final Phase 6 code (keys ÷ 8 + lock), seed 313131 | PASS 200/200, request in flight at 200/200 kills, 160.1 s | `run-default-200-seed-313131.txt` |
| **Phase 7 code**, `default`, seed 1717 | PASS 200/200, **152.2 s** | `run-default-200-seed-1717.txt` |
| Phase 7, `compaction`, seed 7701 | PASS 100/100; **80 of 100 kills landed during a compaction**; 81.4 s | `run-compaction-100-seed-7701.txt` |
| Phase 7, `recovery-crash`, seed 4201 | PASS 100/100; 300 extra kills of a restarted serve: **216 during recovery**, 20 before start-up, 64 after READY; 215.6 s | `run-recovery-crash-100-seed-4201.txt` |
| Phase 7, `disk-full`, seed 9901 | PASS 100/100; **449 clean `SizeCapReached` refusals**, file never over 4,096 bytes; 60.7 s | `run-disk-full-100-seed-9901.txt` |
| Phase 7, `overlap`, seed 5501 | PASS 100/100 (3 writers, 8 shared keys); 70.7 s | `run-overlap-100-seed-5501.txt` |
| Phase 7, `large-values` / `large-batches`, seeds 9902 / 4202 | PASS 20/20 each | `run-large-values-20-seed-9902.txt`, `run-large-batches-20-seed-4202.txt` |
| Phase 7, `targeted`, seed 3131 (TARGETED) | PASS 51/51 = 17 pause points × 3 rounds, now including the 4 `compact.*` points; `snapshot.*` are hard-killed in `tests/test_snapshot.py` instead | `run-targeted-3-seed-3131.txt` |
| the real engine after Phase 6, `--mode targeted`, seed 626262 (TARGETED) | PASS 26/26; `compact.*` and `snapshot.*` NOT TESTED here (snapshot kills are in `tests/test_snapshot.py`) | `run-targeted-2-seed-626262.txt` |
| the real engine, `--mode large-values`, seed 999111 | PASS 10/10 | `run-large-values-10-seed-999111.txt` |
| the real engine, `--mode large-batches`, seed 424299 | PASS 10/10; 23 batches of 1,000–10,000 ops in flight at a kill, none partly present | `run-large-batches-10-seed-424299.txt`, `in-flight-analysis.txt` |
| the real engine, `--mode targeted`, seed 515151 (TARGETED, not random) | PASS 39/39 = 13 pause points × 3 rounds; the 4 `compact.*` points NOT TESTED | `run-targeted-3-seed-515151.txt` |

## On-disk format

> Status: frozen in Phase 1 (CLAUDE.md section 8). Encoding and decoding are implemented in `src/kv/layout.py` (Phase 2), tested by `tests/test_layout.py`, and checked byte for byte against this section by `tools/hexdump_format.py` (`evidence/hexdump-format.txt`). Reading and writing real files is implemented in `src/kv/db.py` and `src/kv/recovery.py` (Phase 3).

All integers are **unsigned and little-endian**. A file is a 16-byte header followed by transactions back to back. Every put, delete and batch is exactly one transaction.

```
offset 0                16
       +----------------+-----------------------------+-----------------------------+---
       | header (16)    | transaction 1               | transaction 2               | ...
       +----------------+-----------------------------+-----------------------------+---

one transaction:
+--------+--------------------+-----+--------------------+------------------------+------+
| A1 | n | op | klen | vlen | key | value | ... (n ops) | CMIT | body_len | crc32 | EE  |
+--------+--------------------+-----+--------------------+------------------------+------+
| 1  | 2 | 1  |  2   |  4   | klen| vlen  |             |  4   |    8     |   4   |  1   |
|<---------------------------- body ----------------------->|<---- commit marker ---->|trailer
```

**File header (16 bytes)**

| Offset | Size | Type | Field | Value |
|---|---|---|---|---|
| 0 | 4 | bytes | magic | `4B 56 44 42` = `KVDB` |
| 4 | 2 | u16 | version | `1` |
| 6 | 2 | u16 | flags | `0` |
| 8 | 8 | u64 | max_bytes | `0` = no cap, otherwise the cap on the whole file size in bytes, header included. Fixed at creation |

**Transaction** (offsets relative to its first byte)

| Offset | Size | Type | Field | Value |
|---|---|---|---|---|
| 0 | 1 | u8 | type | `0xA1` |
| 1 | 2 | u16 | op_count | 1 to 10,000 |
| 3 | … | | operations | op_count operations, back to back |
| … | 16 | | commit marker | see below |
| … | 1 | u8 | trailer | `0xEE` |

**Operation**

| Offset | Size | Type | Field | Value |
|---|---|---|---|---|
| 0 | 1 | u8 | op | `1` = put, `2` = delete |
| 1 | 2 | u16 | key_len | 1 to 1,024 |
| 3 | 4 | u32 | val_len | 0 to 1,048,576; always 0 for delete |
| 7 | key_len | bytes | key | |
| 7 + key_len | val_len | bytes | value | |

**Commit marker (16 bytes)**

| Offset | Size | Type | Field | Value |
|---|---|---|---|---|
| 0 | 4 | bytes | magic | `43 4D 49 54` = `CMIT` |
| 4 | 8 | u64 | body_len | bytes in the body = transaction header + all operations |
| 12 | 4 | u32 | crc32 | `zlib.crc32` of the body (not of the marker or trailer). This is the standard CRC-32 used by zip and PNG (polynomial 0x04C11DB7 reflected, init and final XOR 0xFFFFFFFF); check value: CRC of `123456789` = `0xCBF43926` (verified with `zlib.crc32` on our laptop) |

**Sizes:** put = 27 + key + value bytes. Delete = 27 + key. Batch of n operations = 20 + 7n + Σkey + Σvalue. An empty database file is 16 bytes.

**Worked example (design):** a new file, then `put hasib 20`. The file is 50 bytes. Every byte below was checked in Phase 2 against the bytes `src/kv/layout.py` produces — see `evidence/hexdump-format.txt` (`python3 tools/hexdump_format.py`):

| File offset | Bytes | Meaning |
|---|---|---|
| 0–15 | `4B 56 44 42 01 00 00 00 00 00 00 00 00 00 00 00` | header: KVDB, version 1, flags 0, no cap |
| 16 | `A1` | transaction type |
| 17–18 | `01 00` | op_count = 1 |
| 19 | `01` | put |
| 20–21 | `05 00` | key_len = 5 |
| 22–25 | `02 00 00 00` | val_len = 2 |
| 26–30 | `68 61 73 69 62` | `hasib` |
| 31–32 | `32 30` | `20` |
| 33–36 | `43 4D 49 54` | `CMIT` |
| 37–44 | `11 00 00 00 00 00 00 00` | body_len = 17 (offsets 16–32) |
| 45–48 | `A1 3E 15 AF` | crc32 = 0xAF153EA1, of bytes 16–32 |
| 49 | `EE` | trailer |

**Batch ordering (B5):** operations apply in list order and the last operation on a key wins. `put x 1` then `del x` in one batch leaves `x` absent, before and after a crash.

**Index:** in memory only. A dict from key to `(value_offset << 21) | value_length`. Delete removes the key.

**Index snapshot `<path>.idx` (Phase 6)** — a sidecar file, only a cache, so a million keys open quickly. If it is missing, damaged or does not match the log, open ignores it and replays the whole log; the result is the same index either way (`tests/test_snapshot.py`). Written by `src/kv/snapshot.py` `write`, read by `load`. All integers little-endian:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic `KVIX` |
| 4 | 2 | version = 1 |
| 6 | 2 | flags = 0 |
| 8 | 8 | covered_end: the log offset E_c the snapshot is valid for |
| 16 | 8 | n: number of entries |
| 24 | 8 | total bytes of all keys |
| 32 | 17 | the 17 log bytes that end at E_c (that transaction's marker and trailer) |
| 49 | 2n | key lengths (u16) |
| 49 + 2n | 8n | locations (u64), as in the index |
| 49 + 10n | … | all keys back to back |
| end − 4 | 4 | crc32 of everything before it |

It is written after a commit, under the lock, once the transactions since the last snapshot reach max(10,000, keys ÷ 8): to `<path>.idx.tmp`, fsync, then `os.replace` onto `<path>.idx`, then fsync of the folder. A failed snapshot never fails the write. It does not count against `--max-bytes`.

## Recovery algorithm

> Status: frozen in Phase 1 (CLAUDE.md section 10), implemented in `src/kv/recovery.py` `recover`, `scan` and `check` (Phase 3). Tested by `tests/test_db.py` (cuts at every byte, corrupt and foreign files) and by targeted hard kills in `tests/test_crash.py`. Recovery of 1,000,000 keys is under 2 s with the index snapshot — see Measured results.

A kill only ever cuts the end of the file — it never writes wrong bytes. So open sorts every problem into one of two kinds. **Torn:** the file ends before a field is complete, so it cut off the tail. **Corrupt:** any other bad byte — something a kill cannot cause.

1. If the file is missing, open creates it: it writes the header and syncs the file and the directory.
2. If the file is shorter than the 16-byte header and its bytes match the start of a valid header, creation was interrupted, so open writes a fresh header. If they don't match, open refuses: it is not our file.
3. If the magic is not `KVDB`, or the version is not 1, or the flags are not 0, open refuses and leaves the file exactly as it is.
4. If `<path>.idx` is valid for this log (correct CRC, and the 17 log bytes at its covered offset E_c are the ones it stored), the index starts as that snapshot and open reads transactions from E_c; otherwise it starts empty and reads from byte 16. (A leftover `<path>.idx.tmp` from a killed snapshot write is deleted.) Open reads transactions from there. For each one it checks every length against the limits and against the bytes left in the file. It collects the operations on the side. Only when the commit marker is complete and its body length and checksum match does it apply them to the index, in order. A batch is therefore all or nothing.
5. After each marker it expects the trailer byte `EE`. If the file ends exactly at the marker, the transaction is still valid — only the trailer was lost (for example by a 1-byte cut of the file). Open writes the trailer back and syncs before it accepts any new write.
6. If the file ends in the middle of a transaction, open cuts the file back to the end of the last complete transaction — an absolute offset, so doing it twice gives the same result — and syncs.
7. Any other bad byte is corruption. Open refuses and writes nothing, rather than throw away committed transactions that come after it.
8. Open reports the key count, the time taken, the bytes cut and whether the trailer was restored.

`check` does steps 1–7 without writing anything, and prints `OK`, `TORN_TAIL at <offset>, <n> bytes`, `MISSING_TRAILER at <offset>`, `CORRUPT at <offset>: <what>`, `NOT_OUR_FILE: <why>`, `INTERRUPTED_CREATE (<n> bytes)` or `NO_FILE`.

## Compaction

> Status: implemented in `src/kv/db.py` `DB.compact` and `_compact_locked` (Phase 7), the order frozen in CLAUDE.md section 11. Tested by `tests/test_compact.py`, including a hard kill at each of its four pause points.

The log only grows, so `compact` rewrites it with only the live keys, under the database lock (reads and writes wait):

1. Delete a leftover `<path>.compact.tmp`.
2. Write `<path>.compact.tmp` in the same folder: a header with the same `max_bytes`, then every live key as ordinary put transactions in batches (at most 10,000 operations and 64 MiB of values each).
3. `fsync` it and close it.
4. Read it back with the normal recovery scan: it must be clean, with exactly the live keys and their lengths. Otherwise delete it and raise `CompactionFailed` — nothing changed.
5. Delete the old snapshot `<path>.idx`.
6. Close both handles.
7. `os.replace(<path>.compact.tmp, <path>)` — the one atomic switch. The original is never deleted first.
8. `fsync` the folder.
9. Reopen both handles; the index now points into the new file.
10. Write a fresh snapshot.

A kill before step 7 leaves the original complete (open deletes the temporary file); a kill after it leaves the complete new file. **E5:** when a write does not fit under `--max-bytes`, the engine first checks whether the file compacted *with that write* would fit; if so it compacts with it (step 7 is then the write's commit point), otherwise it refuses with `SizeCapReached` and writes nothing.

## Limits

> Status: frozen in Phase 1. Key, value and batch limits are enforced by `src/kv/layout.py` `validate_ops` (tested in `tests/test_layout.py`); the size cap by `src/kv/db.py` `DB._commit` (tested in `tests/test_db.py` `CapTest`, no crash). 1,000,000 keys open in about 1 s (Measured results).

| Item | Limit |
|---|---|
| Key | 1 to 1,024 bytes |
| Value | 0 to 1,048,576 bytes (1 MB = 1,048,576) |
| Operations in one batch | 1 to 10,000 |
| Keys in a database | at least 1,000,000 (no limit in the format) |
| Size cap | `--max-bytes N` when the file is created; counts the whole file, header included; fixed after creation. A compaction temporary file is not counted |
| Largest single transaction | 10,496,070,003 bytes of body |

## Errors

> Status: implemented in `src/kv/errors.py` (Phase 3); all classes derive from `KVError`.

| Error | Raised when | File changed? |
|---|---|---|
| `InvalidInput` | key, value or batch outside the limits; a `str` instead of `bytes`; a different `--max-bytes` on an existing file | no |
| `SizeCapReached` | the whole transaction does not fit under `max_bytes` — **even after compacting the file with it** (E5, Phase 7) | no |
| `NotOurFile` | wrong magic, version or flags, or a short file that is not the start of our header | no |
| `CorruptFile` | a bad byte that a kill cannot produce (section "Recovery algorithm", step 7) | no |
| `OSError` (from the OS) | a write or fsync failed and the rollback succeeded: the file is back at the end offset E | no |
| `CompactionFailed` | compaction stopped before its swap: a write error such as a full disk, the new file failed its check, or the result would exceed `max_bytes` | no — the temporary file is deleted, the database stays usable |
| `DatabaseBusy` | another open (another process, or the same process) already has the database: it holds `<path>.lock` | no |
| `DatabaseFailed` | a write failed **and** the rollback truncate or its fsync also failed; every later write is refused until reopen. Also raised for any operation on a closed database | may end in a torn tail (never a complete marker for the failed write — see below), which the next open cuts |

Why a failed write can never leave a complete marker that recovery would trust: the marker is written only after the body's fsync succeeded (CLAUDE.md section 9, steps 4–5). If the failure was in the marker write itself, the marker is incomplete and the tail is torn. If only the second fsync failed, the marker may be complete; then the rollback truncate removes it, and if that truncate also fails, the database is marked failed and the transaction **may survive** a later reopen even though the caller got an error. That is allowed by rule 2 of the invariant (an operation that did not return success may be fully present).

## Platform notes

To be written from evidence.

## Measured results

**Recovery time, 1,000,000 keys** (`evidence/recovery-benchmark.md`, Phase 6). Dataset built through `kvdb.py serve`, one transaction per key (10-byte keys, 10-byte values, 47,000,016 bytes), then `serve` hard-killed. Timed: start of `kvdb.py serve` until `READY`. Intel i5-8259U, 8 GB, macOS 15.8.1, Python 3.13.9, SSD/APFS, warm cache, other apps running.

| Code | Slowest of 5 opens | B6 (10,000-op batch killed halfway) |
|---|---|---|
| Phase 3 code | 4.857 s | 4.814 s |
| Phase 6 fast path, no snapshot | 2.889 s | 2.354 s |
| fast path + snapshot every keys ÷ 4 | 1.070 s (worst case 1.582 s) | 0.987 s |
| **current: fast path + snapshot every keys ÷ 8** | **1.253 s** (worst case **1.127 s**, snapshot 125,000 transactions old) | 1.073 s |

**The 2-second limit (F3, B6) is met on this laptop**, every run under 1.3 s (`evidence/recovery-benchmark-keys8.txt`). A durable single put takes 0.389 ms on average (`tools/sync_speed.py`).

**Shrink test** (`evidence/shrink-test.txt`, `python3 tools/shrink_test.py`; written in batches of 10,000 operations through the engine API, not single puts): after 1,000,000 puts the file was 27,002,016 bytes; after 1,000,000 deletes 44,004,016 bytes and 0 keys; after `compact` **16 bytes** (just the header). `check` said OK, the reopened database was empty, and a new put worked.

**Torture run time:** 200 iterations of `./torture` took **152.2 s** with the Phase 7 code (seed 1717, `evidence/torture/run-default-200-seed-1717.txt`), inside the judge's ~3 minutes; 160.1 s and 177.0 s with the Phase 6 code, and 355.8 s before Phase 6 (`run-default-200-seed-777001.txt`), when every iteration replayed the whole growing log four times. The kills were not changed. `--mode recovery-crash` is slower on purpose: 100 iterations took 215.6 s (100,000 prefilled keys, three extra kills per iteration)..
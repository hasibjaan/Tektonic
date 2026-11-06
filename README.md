# Kill It and Prove It — crash-safe key-value store (Python, standard library only)

## Build

To be written from evidence.

Note: the torture tool lives in `tester/` and `torture.py`, not in a `torture/` folder, so no folder clashes with a `./torture` launcher in the repository root.

## Requirements

To be written from evidence.

## Commands

To be written from evidence.

## serve protocol

> Status: design, frozen in Phase 1. **Not implemented yet** (Phase 4).

`kvdb serve <path>` reads one request per line from stdin (binary) and writes one line per result to stdout.

Requests:

```
<id> put <key> <value>
<id> del <key>
<id> get <key>
<id> batch <n>        then exactly n lines:  put <key> <value>  |  del <key>
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

## On-disk format

> Status: frozen in Phase 1 (CLAUDE.md section 8). Encoding and decoding are implemented in `src/kv/layout.py` (Phase 2), tested by `tests/test_layout.py`, and checked byte for byte against this section by `tools/hexdump_format.py` (`evidence/hexdump-format.txt`). **Reading and writing a real file is not implemented yet** (Phase 3).

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

## Recovery algorithm

> Status: design, frozen in Phase 1 (CLAUDE.md section 10). **Not implemented yet.**

A kill only ever cuts the end of the file — it never writes wrong bytes. So open sorts every problem into one of two kinds. **Torn:** the file ends before a field is complete, so it cut off the tail. **Corrupt:** any other bad byte — something a kill cannot cause.

1. If the file is missing, open creates it: it writes the header and syncs the file and the directory.
2. If the file is shorter than the 16-byte header and its bytes match the start of a valid header, creation was interrupted, so open writes a fresh header. If they don't match, open refuses: it is not our file.
3. If the magic is not `KVDB`, or the version is not 1, or the flags are not 0, open refuses and leaves the file exactly as it is.
4. Open reads transactions from byte 16. For each one it checks every length against the limits and against the bytes left in the file. It collects the operations on the side. Only when the commit marker is complete and its body length and checksum match does it apply them to the index, in order. A batch is therefore all or nothing.
5. After each marker it expects the trailer byte `EE`. If the file ends exactly at the marker, the transaction is still valid — only the trailer was lost (for example by a 1-byte cut of the file). Open writes the trailer back and syncs before it accepts any new write.
6. If the file ends in the middle of a transaction, open cuts the file back to the end of the last complete transaction — an absolute offset, so doing it twice gives the same result — and syncs.
7. Any other bad byte is corruption. Open refuses and writes nothing, rather than throw away committed transactions that come after it.
8. Open reports the key count, the time taken, the bytes cut and whether the trailer was restored.

`check` does steps 1–7 without writing anything, and prints `OK`, `TORN_TAIL at <offset>, <n> bytes`, `MISSING_TRAILER at <offset>`, `CORRUPT at <offset>: <what>`, `NOT_OUR_FILE: <why>`, `INTERRUPTED_CREATE (<n> bytes)` or `NO_FILE`.

## Limits

> Status: design, frozen in Phase 1. **Not implemented or tested yet.**

| Item | Limit |
|---|---|
| Key | 1 to 1,024 bytes |
| Value | 0 to 1,048,576 bytes (1 MB = 1,048,576) |
| Operations in one batch | 1 to 10,000 |
| Keys in a database | at least 1,000,000 (no limit in the format) |
| Size cap | `--max-bytes N` when the file is created; counts the whole file, header included; fixed after creation. A compaction temporary file is not counted |
| Largest single transaction | 10,496,070,003 bytes of body |

## Errors

> Status: design, frozen in Phase 1. Class names are planned, **not implemented yet**.

| Error | Raised when | File changed? |
|---|---|---|
| `InvalidInput` | key, value or batch outside the limits; a `str` instead of `bytes`; a different `--max-bytes` on an existing file | no |
| `SizeCapReached` | the whole transaction would push the file past `max_bytes` | no |
| `NotOurFile` | wrong magic, version or flags, or a short file that is not the start of our header | no |
| `CorruptFile` | a bad byte that a kill cannot produce (section "Recovery algorithm", step 7) | no |
| `DatabaseFailed` | a write failed **and** the rollback truncate or its fsync also failed; every later write is refused until reopen | may end in a torn tail (never a complete marker for the failed write — see below), which the next open cuts |

Why a failed write can never leave a complete marker that recovery would trust: the marker is written only after the body's fsync succeeded (CLAUDE.md section 9, steps 4–5). If the failure was in the marker write itself, the marker is incomplete and the tail is torn. If only the second fsync failed, the marker may be complete; then the rollback truncate removes it, and if that truncate also fails, the database is marked failed and the transaction **may survive** a later reopen even though the caller got an error. That is allowed by rule 2 of the invariant (an operation that did not return success may be fully present).

## Platform notes

To be written from evidence.

## Measured results

To be written from evidence.

"""Index snapshot: a sidecar file <path>.idx that lets open skip most of the log.

Approved in Phase 6 (step d). The main file's format does not change. The
snapshot is ONLY a cache: if it is missing, damaged, or does not match the log,
open ignores it and replays the whole log, exactly as before.

    <path>.idx, little-endian:
      offset 0   magic "KVIX" (4) . version u16 = 1 . flags u16 = 0
      offset 8   covered_end u64   the log offset E_c the snapshot is valid for
      offset 16  count u64         number of index entries n
      offset 24  key_bytes u64     total bytes of all keys
      offset 32  log_tail (17)     the 17 bytes of the log that end at E_c
                                   (the commit marker and trailer of the last
                                   transaction it covers)
      offset 49  key lengths       n x u16
                 locations         n x u64  (value_offset << 21) | value_length
                 keys              key_bytes, all keys back to back
      last 4     crc32 u32         zlib.crc32 of every byte before it

Loading uses only C-level calls (array, itertools.accumulate, map, zip), so a
million entries load in well under a second.
"""
import array
import collections
import itertools
import os
import struct
import sys
import zlib

from .pause import pause

HEADER = struct.Struct("<4sHHQQQ17s")       # 49 bytes
MAGIC = b"KVIX"
VERSION = 1
TAIL_SIZE = 17                               # commit marker (16) + trailer (1)
SUFFIX = ".idx"
TMP_SUFFIX = ".idx.tmp"

Loaded = collections.namedtuple("Loaded", "index covered_end reason")


def path_for(db_path):
    return db_path + SUFFIX


def _arrays_little_endian(*arrays):
    if sys.byteorder == "big":
        for a in arrays:
            a.byteswap()


def write(db_path, index, covered_end, log_tail):
    """Write a snapshot of `index` valid at log offset covered_end.

    Order: write <path>.idx.tmp, fsync, close, os.replace onto <path>.idx,
    fsync the folder. A kill at any point leaves either the old snapshot, the
    new one, or none — and a leftover .tmp that open removes.
    """
    keys = list(index)
    lengths = array.array("H", map(len, keys))
    locations = array.array("Q", index.values())
    _arrays_little_endian(lengths, locations)
    blob = b"".join(keys)
    head = HEADER.pack(MAGIC, VERSION, 0, covered_end, len(keys), len(blob), log_tail)
    parts = (head, lengths.tobytes(), locations.tobytes(), blob)
    crc = 0
    for part in parts:
        crc = zlib.crc32(part, crc)
    tmp = db_path + TMP_SUFFIX
    with open(tmp, "wb") as f:                       # buffered: flush before fsync
        for part in parts:
            f.write(part)
        f.write(struct.pack("<I", crc))
        f.flush()
        os.fsync(f.fileno())
    pause("snapshot.before-swap")
    os.replace(tmp, path_for(db_path))
    pause("snapshot.after-swap")
    if os.name != "nt":
        fd = os.open(os.path.dirname(os.path.abspath(db_path)), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def load(db_path, log_size, read_log):
    """Load <path>.idx if it is valid for this log. Returns Loaded(index or None, covered_end, reason).

    read_log(offset, n) must return n bytes of the main file. Valid means: the
    CRC matches, the header and sizes agree, covered_end is inside the log, and
    the 17 log bytes ending at covered_end equal the ones stored.
    """
    try:
        with open(path_for(db_path), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return Loaded(None, 0, "no snapshot")
    except OSError as exc:
        return Loaded(None, 0, "snapshot unreadable: {}".format(exc))
    if len(data) < HEADER.size + 4:
        return Loaded(None, 0, "snapshot too short")
    if zlib.crc32(memoryview(data)[:-4]) != struct.unpack_from("<I", data, len(data) - 4)[0]:
        return Loaded(None, 0, "snapshot crc32 does not match")
    magic, version, flags, covered_end, count, key_bytes, log_tail = HEADER.unpack_from(data, 0)
    if magic != MAGIC or version != VERSION or flags != 0:
        return Loaded(None, 0, "not a version-1 snapshot")
    if HEADER.size + 10 * count + key_bytes + 4 != len(data):
        return Loaded(None, 0, "snapshot sizes do not add up")
    if not (TAIL_SIZE + 16 <= covered_end <= log_size):
        return Loaded(None, 0, "snapshot covers {} bytes, the log has {}".format(covered_end, log_size))
    if read_log(covered_end - TAIL_SIZE, TAIL_SIZE) != log_tail:
        return Loaded(None, 0, "snapshot does not match the log at offset {}".format(covered_end))
    lengths, locations = array.array("H"), array.array("Q")
    at = HEADER.size
    lengths.frombytes(data[at:at + 2 * count])
    locations.frombytes(data[at + 2 * count:at + 10 * count])
    _arrays_little_endian(lengths, locations)
    if sum(lengths) != key_bytes:
        return Loaded(None, 0, "snapshot key lengths do not add up")
    if count and (max(locations) >> 21) > covered_end:
        return Loaded(None, 0, "snapshot points past the bytes it covers")
    blob = data[at + 10 * count:at + 10 * count + key_bytes]
    ends = list(itertools.accumulate(lengths, initial=0))
    keys = map(blob.__getitem__, map(slice, ends, ends[1:]))
    index = dict(zip(keys, locations.tolist()))
    if len(index) != count:
        return Loaded(None, 0, "snapshot has duplicate keys")
    return Loaded(index, covered_end, "")


def remove_leftover(db_path):
    """A .idx.tmp is a snapshot write that was killed: never trusted, removed."""
    try:
        os.remove(db_path + TMP_SUFFIX)
        return True
    except FileNotFoundError:
        return False


def remove(db_path):
    """Delete <path>.idx (compaction step 5: its offsets point into the old file)."""
    try:
        os.remove(path_for(db_path))
        return True
    except FileNotFoundError:
        return False

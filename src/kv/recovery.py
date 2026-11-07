"""Reading the file back after a crash (CLAUDE.md section 10), and check.

recover() may write, but only three small, repeatable repairs:
  - create the header of a missing file, or of an interrupted creation;
  - cut a torn tail to an ABSOLUTE offset (doing it twice gives the same file);
  - put back a missing final trailer byte.
Everything else it finds wrong is CORRUPT: it raises and writes nothing.

check() runs the same scan read-only and never repairs anything.
"""
import collections
import mmap
import os
import struct
import time
import zlib

from . import layout
from .errors import CorruptFile, InvalidInput, NotOurFile
from .pause import pause

# How a scan of the transactions ended.
CLEAN = "clean"            # the file ends exactly after a trailer (or after the header)
TORN = "torn"              # the file ends inside a transaction: offset = where it starts
NO_TRAILER = "no-trailer"  # the last transaction is whole but its trailer is cut: offset = file size
CORRUPT = "corrupt"        # a byte a kill cannot produce: offset = the bad field

Scan = collections.namedtuple("Scan", "kind offset reason transactions")
Report = collections.namedtuple("Report", "keys seconds bytes_cut trailer_restored created")
Recovered = collections.namedtuple("Recovered", "index end max_bytes report replayed")
# replayed: how many transactions were read from the log


# ---- small helpers on raw file descriptors ---------------------------------

def _read_all(fd, offset, n):
    os.lseek(fd, offset, os.SEEK_SET)
    parts, got = [], 0
    while got < n:
        b = os.read(fd, n - got)
        if not b:
            break
        parts.append(b)
        got += len(b)
    return b"".join(parts)


def _write_at(fd, offset, data):
    """Write every byte of data at offset; a short write loops, a zero write fails."""
    os.lseek(fd, offset, os.SEEK_SET)
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("write returned {}".format(n))
        view = view[n:]


def fsync_dir(path):
    """fsync the folder that holds path (Linux, macOS). Not available on Windows."""
    if os.name == "nt":
        return False
    fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


# ---- the scan (step 4) -----------------------------------------------------

# Fast path (Phase 6): one unpack reads the transaction header AND the first
# op header; one unpack reads the commit marker AND the trailer. Used only for
# the normal case; any check that fails hands the transaction to
# layout.decode_txn, which classifies it exactly (OK, NO_TRAILER, INCOMPLETE,
# CORRUPT). So the fast path can only ACCEPT what decode_txn would accept.
_HEAD_AND_OP = struct.Struct("<BHBHI")    # type, op_count, op, key_len, val_len  (3 + 7 = 10 bytes)
_MARK_AND_TRAILER = struct.Struct("<4sQIB")  # magic, body_len, crc32, trailer       (16 + 1 = 17 bytes)


def scan(buf, index, on_first=None, start=layout.HEADER_SIZE):
    """Walk every transaction from offset 16 of buf (the whole file).

    buf: bytes or mmap (slicing must give bytes). index: a dict to fill
    (key -> packed location), or None to only check. A transaction's ops reach
    the index only after its whole marker has been validated.
    on_first: called once after the first transaction is applied (pause point).
    """
    size = len(buf)
    view = memoryview(buf)
    p = start
    count = 0
    head_and_op = _HEAD_AND_OP.unpack_from
    op_header = layout.OP_HEADER.unpack_from
    mark_and_trailer = _MARK_AND_TRAILER.unpack_from
    crc32 = zlib.crc32
    bits = layout.LOCATION_BITS
    max_key, max_value, max_ops = layout.MAX_KEY, layout.MAX_VALUE, layout.MAX_OPS
    put, delete = layout.OP_PUT, layout.OP_DELETE
    try:
        while p < size:
            # -- fast path: a complete, valid transaction ---------------------------
            if p + 10 <= size:
                txn_type, n_ops, op, key_len, val_len = head_and_op(buf, p)
                if (txn_type == 0xA1 and 1 <= n_ops <= max_ops and 1 <= key_len <= max_key
                        and (op == put and val_len <= max_value or op == delete and val_len == 0)):
                    key_at = p + 10
                    end = key_at + key_len + val_len
                    staged = None
                    if end <= size:
                        if n_ops == 1:
                            staged = ((op, buf[key_at:key_at + key_len], key_at + key_len, val_len),)
                        else:
                            staged = [(op, buf[key_at:key_at + key_len], key_at + key_len, val_len)]
                            for _ in range(n_ops - 1):
                                if end + 7 > size:
                                    staged = None
                                    break
                                op, key_len, val_len = op_header(buf, end)
                                if not (1 <= key_len <= max_key
                                        and (op == put and val_len <= max_value or op == delete and val_len == 0)):
                                    staged = None
                                    break
                                key_at = end + 7
                                end = key_at + key_len + val_len
                                if end > size:
                                    staged = None
                                    break
                                staged.append((op, buf[key_at:key_at + key_len], key_at + key_len, val_len))
                    if staged is not None and end + 17 <= size:
                        magic, body_len, crc, trailer = mark_and_trailer(buf, end)
                        if (magic == b"CMIT" and body_len == end - p and trailer == 0xEE
                                and crc == crc32(view[p:end])):
                            if index is not None:
                                for op, key, value_offset, value_length in staged:   # list order: last op wins
                                    if op == put:
                                        index[key] = (value_offset << bits) | value_length
                                    else:
                                        index.pop(key, None)
                            count += 1
                            if count == 1 and on_first is not None:
                                on_first()
                            p = end + 17
                            continue
            # -- anything else: the exact rules of layout.decode_txn ----------------
            d = layout.decode_txn(view, p)
            if d.status == layout.OK or d.status == layout.NO_TRAILER:
                if index is not None:
                    for op, key, value_offset, value_length in d.ops:
                        if op == put:
                            index[key] = (value_offset << bits) | value_length
                        else:
                            index.pop(key, None)
                count += 1
                if count == 1 and on_first is not None:
                    on_first()
                if d.status == layout.NO_TRAILER:
                    return Scan(NO_TRAILER, d.offset, d.reason, count)   # only possible at the very end
                p = d.offset
            elif d.status == layout.INCOMPLETE:
                return Scan(TORN, p, d.reason, count)
            else:
                return Scan(CORRUPT, d.offset, d.reason, count)
        return Scan(CLEAN, p, "", count)
    finally:
        view.release()


def _scan_fd(fd, size, index, on_first=None, start=layout.HEADER_SIZE):
    """Scan an open file of `size` bytes through a read-only mmap, from offset start."""
    if start >= size:
        return Scan(CLEAN, size, "", 0)
    mm = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
    try:
        return scan(mm, index, on_first, start)   # slicing an mmap gives bytes: keys are bytes
    finally:
        mm.close()


COMPACT_TMP_SUFFIX = ".compact.tmp"


def _remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def read_index(path):
    """Read-only full scan of a whole file (used to validate a
    compacted file). Returns (scan, index, size); scan.kind is CLEAN for a good file."""
    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        header = layout.decode_header(_read_all(fd, 0, min(size, layout.HEADER_SIZE)))
        if header.status != layout.OK:
            return Scan(CORRUPT, 0, "header: " + header.reason, 0), None, size
        index = {}
        return _scan_fd(fd, size, index), index, size
    finally:
        os.close(fd)


# ---- open-time recovery (steps 1-8) ------------------------------------------

def _create(path, max_bytes):
    """Step 1: a missing file gets a header, then fsync of the file and its folder."""
    header = layout.encode_header(max_bytes)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        # Written in two halves only so the pause point can stop between them.
        # A kill there leaves an 8-byte header prefix, which step 2 repairs.
        _write_at(fd, 0, header[:8])
        pause("create.mid-header")
        _write_at(fd, 8, header[8:])
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(path)


def recover(path, max_bytes=0):
    """Bring the file at path to a clean state and rebuild the index.

    max_bytes: the cap for a NEW file; for an existing file it must be 0 or
    equal to the cap stored in its header. Returns Recovered(index, end,
    max_bytes, report). Raises NotOurFile, CorruptFile or InvalidInput without
    changing the file.
    """
    started = time.perf_counter()
    if type(max_bytes) is not int or not 0 <= max_bytes <= layout.MAX_U64:
        raise InvalidInput("max_bytes must be an integer from 0 to 2**64 - 1")
    if 0 < max_bytes < layout.HEADER_SIZE:
        raise InvalidInput("max_bytes must be 0 (no cap) or at least {}".format(layout.HEADER_SIZE))

    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        _create(path, max_bytes)                                            # step 1
        return Recovered({}, layout.HEADER_SIZE, max_bytes,
                         Report(0, time.perf_counter() - started, 0, False, True), 0)
    try:
        size = os.fstat(fd).st_size

        if size < layout.HEADER_SIZE:                                       # step 2
            if not layout.is_header_prefix(_read_all(fd, 0, size)):
                raise NotOurFile("file has {} bytes and is not the start of a KVDB header".format(size))
            _write_at(fd, 0, layout.encode_header(max_bytes))
            os.fsync(fd)
            return Recovered({}, layout.HEADER_SIZE, max_bytes,
                             Report(0, time.perf_counter() - started, 0, False, True), 0)

        header = layout.decode_header(_read_all(fd, 0, layout.HEADER_SIZE))  # step 3
        if header.status != layout.OK:
            raise NotOurFile(header.reason)
        if max_bytes and max_bytes != header.max_bytes:
            raise InvalidInput("the size cap is fixed at creation: this file has max_bytes={}".format(
                header.max_bytes))

        _remove_quietly(path + COMPACT_TMP_SUFFIX)                         # a killed compaction: never trusted
        for old in (path + ".idx", path + ".idx.tmp"):                     # an older version's index cache:
            _remove_quietly(old)                                           # never used, deleted (one data file)
        index = {}
        result = _scan_fd(fd, size, index, on_first=lambda: pause("recovery.replay"))  # step 4: the whole log

        if result.kind == CORRUPT:                                          # step 5
            raise CorruptFile(result.offset, result.reason)

        end, bytes_cut, restored = size, 0, False
        if result.kind == TORN:                                             # step 6
            pause("recovery.before-cut")
            os.ftruncate(fd, result.offset)
            os.fsync(fd)
            pause("recovery.after-cut")
            end, bytes_cut = result.offset, size - result.offset
        elif result.kind == NO_TRAILER:                                     # step 7
            pause("recovery.before-trailer")
            _write_at(fd, size, layout.TRAILER_BYTES)
            os.fsync(fd)
            end, restored = size + 1, True

        return Recovered(index, end, header.max_bytes,                      # step 8
                         Report(len(index), time.perf_counter() - started, bytes_cut, restored, False),
                         result.transactions)
    finally:
        os.close(fd)


# ---- check: read-only --------------------------------------------------------

def check(path):
    """Return (ok, reason). Never writes. reason is one of the strings in CLAUDE.md section 10."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False, "NO_FILE"
    try:
        size = os.fstat(fd).st_size
        if size < layout.HEADER_SIZE:
            if layout.is_header_prefix(_read_all(fd, 0, size)):
                return False, "INTERRUPTED_CREATE ({} bytes)".format(size)
            return False, "NOT_OUR_FILE: file has {} bytes and is not the start of a KVDB header".format(size)
        header = layout.decode_header(_read_all(fd, 0, layout.HEADER_SIZE))
        if header.status != layout.OK:
            return False, "NOT_OUR_FILE: " + header.reason
        result = _scan_fd(fd, size, None)
        if result.kind == CLEAN:
            return True, "OK"
        if result.kind == TORN:
            return False, "TORN_TAIL at {}, {} bytes".format(result.offset, size - result.offset)
        if result.kind == NO_TRAILER:
            return False, "MISSING_TRAILER at {}".format(result.offset)
        return False, "CORRUPT at {}: {}".format(result.offset, result.reason)
    finally:
        os.close(fd)

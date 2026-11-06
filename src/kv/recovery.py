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
import time

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
Recovered = collections.namedtuple("Recovered", "index end max_bytes report")


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

def scan(buf, index, on_first=None):
    """Walk every transaction from offset 16 of buf (the whole file).

    index: a dict to fill (key -> packed location), or None to only check.
    A transaction's ops reach the index only after layout.decode_txn has
    validated its whole marker — torn and corrupt ones never return ops.
    on_first: called once after the first transaction is applied (pause point).
    """
    size = len(buf)
    p = layout.HEADER_SIZE
    count = 0
    put = layout.OP_PUT
    bits = layout.LOCATION_BITS
    decode = layout.decode_txn
    while p < size:
        d = decode(buf, p)
        if d.status == layout.OK or d.status == layout.NO_TRAILER:
            if index is not None:
                for op, key, value_offset, value_length in d.ops:   # list order: last op wins
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


def _scan_fd(fd, size, index, on_first=None):
    """Scan an open file of `size` bytes through a read-only mmap."""
    if size == layout.HEADER_SIZE:
        return Scan(CLEAN, size, "", 0)
    mm = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
    view = memoryview(mm)
    try:
        return scan(view, index, on_first)
    finally:
        view.release()
        mm.close()


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
                         Report(0, time.perf_counter() - started, 0, False, True))
    try:
        size = os.fstat(fd).st_size

        if size < layout.HEADER_SIZE:                                       # step 2
            if not layout.is_header_prefix(_read_all(fd, 0, size)):
                raise NotOurFile("file has {} bytes and is not the start of a KVDB header".format(size))
            _write_at(fd, 0, layout.encode_header(max_bytes))
            os.fsync(fd)
            return Recovered({}, layout.HEADER_SIZE, max_bytes,
                             Report(0, time.perf_counter() - started, 0, False, True))

        header = layout.decode_header(_read_all(fd, 0, layout.HEADER_SIZE))  # step 3
        if header.status != layout.OK:
            raise NotOurFile(header.reason)
        if max_bytes and max_bytes != header.max_bytes:
            raise InvalidInput("the size cap is fixed at creation: this file has max_bytes={}".format(
                header.max_bytes))

        index = {}
        result = _scan_fd(fd, size, index, on_first=lambda: pause("recovery.replay"))  # step 4

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
                         Report(len(index), time.perf_counter() - started, bytes_cut, restored, False))
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

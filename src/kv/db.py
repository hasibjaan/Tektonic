"""The engine: open, put, get, delete, batch, close (CLAUDE.md sections 7, 9, 12).

put, delete and batch all go through ONE write path, DB._commit, which follows
the frozen write order in CLAUDE.md section 9 step by step. One global lock
(section 12) is held by every operation, so nothing ever overlaps.
"""
import os
import sys
import threading

try:
    import fcntl                      # macOS and Linux
except ImportError:                   # Windows: no fcntl -> open_db refuses (UnsupportedPlatform)
    fcntl = None

from . import layout
from . import recovery
from .errors import (CompactionFailed, DatabaseBusy, DatabaseFailed, InvalidInput, SizeCapReached,
                     UnsupportedPlatform)
from .pause import pause

PUT = layout.OP_PUT
DELETE = layout.OP_DELETE
_OP_NAMES = {PUT: PUT, DELETE: DELETE, "put": PUT, "del": DELETE, "delete": DELETE}
COMPACT_BATCH_OPS = 10_000             # compaction writes live keys as batches of at most
COMPACT_BATCH_BYTES = 64 * 1024 * 1024  # this many ops and this many value bytes


def open_db(path, max_bytes=0):
    """Open (or create) the database at path. Returns (db, report).

    report is recovery.Report(keys, seconds, bytes_cut, trailer_restored, created).
    """
    lock_fd = _take_lock(path)                   # BEFORE recovery: no other open can cut our writes
    try:
        rec = recovery.recover(path, max_bytes)
        return DB(path, rec, lock_fd), rec.report
    except BaseException:
        _release_lock(lock_fd)
        raise


def _take_lock(path):
    """Exclusive, non-blocking lock on <path>.lock. The kernel drops it when the
    process dies, so a SIGKILL never leaves the database locked."""
    if fcntl is None:
        # Never run unprotected: two opens of one file can each write at "their" end
        # and lose an acknowledged write (reproduced in tests/test_lock.py).
        raise UnsupportedPlatform("no supported OS lock on this platform ({}): the engine runs on "
                                  "macOS and Linux (fcntl.flock)".format(sys.platform))
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise DatabaseBusy("another open holds {}.lock; close it (or let it exit) first".format(path))
    except BaseException:
        os.close(fd)
        raise
    return fd


def _release_lock(fd):
    if fd is not None:
        os.close(fd)                             # closing the descriptor releases the flock


class DB:
    def __init__(self, path, rec, lock_fd=None):
        self.path = path
        self._lock_fd = lock_fd
        self.compact_listener = None             # optional callable("begin" | "end"), used by serve
        self._lock = threading.Lock()
        self._index = rec.index
        self._end = rec.end                 # the tracked end offset; the writer never trusts the file position
        self._max_bytes = rec.max_bytes
        self._failed = None                 # a reason string once the database has failed
        self._closed = False
        # Both handles are binary and unbuffered (CLAUDE.md section 12).
        self._w = open(path, "r+b", buffering=0)
        self._r = open(path, "rb", buffering=0)
        actual = os.fstat(self._w.fileno()).st_size
        if actual != self._end:
            self._w.close()
            self._r.close()
            raise DatabaseFailed("file is {} bytes after recovery, expected {}".format(actual, self._end))

    # ---- public operations ----------------------------------------------------

    def put(self, key, value):
        self._commit([(PUT, key, value)])

    def delete(self, key):
        """Delete key. Deleting a missing key succeeds (it still writes a transaction)."""
        self._commit([(DELETE, key, None)])

    def batch(self, ops):
        """ops: ordered list of (op, key, value); op is "put", "del"/"delete", PUT or DELETE.
        Applied in list order; the last operation on a key wins. All or nothing."""
        if not isinstance(ops, (list, tuple)):
            raise InvalidInput("a batch must be a list of (op, key, value)")
        norm = []
        for i, item in enumerate(ops):
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise InvalidInput("operation {}: must be (op, key, value)".format(i))
            op, key, value = item
            if type(op) is bool or op not in _OP_NAMES:
                raise InvalidInput("operation {}: op must be 'put' or 'del'".format(i))
            norm.append((_OP_NAMES[op], key, value))
        self._commit(norm)

    def get(self, key):
        """The value as bytes, or None when the key is missing. b"" is a value."""
        if type(key) is not bytes or not 1 <= len(key) <= layout.MAX_KEY:
            raise InvalidInput("key must be 1 to {} bytes".format(layout.MAX_KEY))
        with self._lock:
            self._check_usable(write=False)
            location = self._index.get(key)
            if location is None:
                return None
            offset, length = layout.unpack_location(location)
            return self._read_at(offset, length)

    def keys(self):
        """Every key, sorted by raw bytes (for dump)."""
        with self._lock:
            self._check_usable(write=False)
            return sorted(self._index)

    def __len__(self):
        with self._lock:
            return len(self._index)

    def compact(self):
        """Rewrite the file with only the live keys (CLAUDE.md section 11).
        Returns (bytes_before, bytes_after)."""
        with self._lock:
            self._check_usable(write=True)
            before = self._end
            entries = self._live_entries(())
            plan = _plan_batches(entries)
            expected = _planned_size(plan)
            if self._max_bytes and expected > self._max_bytes:
                raise CompactionFailed("compacted file would be {} bytes, max_bytes is {}".format(
                    expected, self._max_bytes))                       # E4: fail before writing anything
            self._compact_locked(entries, plan, expected)
            return before, self._end

    def close(self):
        """Close both handles. Writes nothing: every committed transaction is already fsynced."""
        with self._lock:
            if self._closed:
                return
            pause("close")
            self._closed = True
            self._w.close()
            self._r.close()
            _release_lock(self._lock_fd)
            self._lock_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the one write path (CLAUDE.md section 9) ------------------------------

    def _commit(self, ops):
        with self._lock:                                              # take the database lock
            self._check_usable(write=True)
            clean, size = layout.validate_ops(ops)                    # 1. validate the WHOLE request
            if self._max_bytes and self._end + size > self._max_bytes:
                self._commit_by_compaction(clean, size)              # E5: compact WITH this change, or refuse
                return
            start = self._end                                         # 2. remember the end offset E
            pause("write.before-body")
            chunks, body_len, crc = layout.encode_validated_body(clean)
            try:
                self._w.seek(start)                                   # 3. seek to E, write the body
                middle = len(chunks) // 2
                for i, chunk in enumerate(chunks):
                    if i == middle:
                        pause("write.mid-body")
                    self._write_all(chunk)
                pause("write.after-body")
                os.fsync(self._w.fileno())                            # 4. fsync: the body is on disk
                pause("write.after-body-sync")
                self._write_all(layout.encode_commit(body_len, crc))  # 5. marker + trailer
                pause("write.after-marker")
                os.fsync(self._w.fileno())                            # 6. fsync: COMMITTED
                pause("write.after-commit-sync")
            except BaseException as exc:
                if self._rollback(start, exc):
                    raise                                             # rolled back: nothing changed
                raise DatabaseFailed("write failed and rollback failed: " + self._failed) from exc
            self._apply(clean, start)                                 # 7. apply to the dict in list order
            self._end = start + size
            pause("write.after-index")
        # 8. the lock is released; returning is the success the caller (serve) ACKs

    # ---- compaction (CLAUDE.md section 11) ----------------------------------------

    def _live_entries(self, clean):
        """The live keys after applying `clean` (E5), sorted by their place in the file.
        Each entry: (key, value_offset or None, value_length, value bytes or None)."""
        bits, mask = layout.LOCATION_BITS, layout.LOCATION_MASK
        state = {key: (loc >> bits, loc & mask, None) for key, loc in self._index.items()}
        for op, key, value in clean:
            if op == PUT:
                state[key] = (None, len(value), value)
            else:
                state.pop(key, None)
        entries = [(key, off, length, value) for key, (off, length, value) in state.items()]
        entries.sort(key=lambda e: (e[1] is None, e[1] or 0))  # file order first, then new values
        return entries

    def _commit_by_compaction(self, clean, size):
        """E5: the write does not fit. Compact with it applied if that fits, else refuse.
        The os.replace in _compact_locked step 7 is then this write's commit point."""
        entries = self._live_entries(clean)
        plan = _plan_batches(entries)
        expected = _planned_size(plan)
        if expected > self._max_bytes:
            raise SizeCapReached("transaction needs {} bytes; file is {} of max_bytes {}; even compacted "
                                 "with it the file would be {} bytes".format(size, self._end, self._max_bytes, expected))
        self._compact_locked(entries, plan, expected)

    def _notify(self, event):
        if self.compact_listener is not None:
            try:
                self.compact_listener(event)
            except Exception:
                pass

    def _compact_locked(self, entries, plan, expected):
        """Steps 1-10 of CLAUDE.md section 11. The caller holds self._lock."""
        self._notify("begin")
        tmp = self.path + recovery.COMPACT_TMP_SUFFIX
        try:
            recovery._remove_quietly(tmp)                                     # 1. a leftover is never trusted
            with open(tmp, "xb", buffering=0) as f:                           # 2. same folder, own header
                _write_fully(f, layout.encode_header(self._max_bytes))
                for number, batch in enumerate(plan):
                    ops = [(PUT, key, value if value is not None else self._read_at(off, length))
                           for key, off, length, value in batch]
                    chunks, body_len, crc = layout.encode_validated_body(ops)
                    for chunk in chunks:
                        _write_fully(f, chunk)
                    _write_fully(f, layout.encode_commit(body_len, crc))
                    if number == 0:
                        pause("compact.mid-write")
                os.fsync(f.fileno())                                          # 3. on disk, then closed
            scan, new_index, size = recovery.read_index(tmp)                  # 4. validate by reading back
            problem = None
            if scan.kind != recovery.CLEAN:
                problem = "scan of the new file ended {} at {}: {}".format(scan.kind, scan.offset, scan.reason)
            elif size != expected:
                problem = "new file is {} bytes, expected {}".format(size, expected)
            elif len(new_index) != len(entries):
                problem = "new file has {} keys, expected {}".format(len(new_index), len(entries))
            else:
                mask = layout.LOCATION_MASK
                for key, off, length, value in entries:
                    loc = new_index.get(key)
                    if loc is None or (loc & mask) != length:
                        problem = "key {!r} is missing or has the wrong length in the new file".format(key[:40])
                        break
            if problem:
                raise CompactionFailed(problem)
        except BaseException as exc:
            recovery._remove_quietly(tmp)                                     # the original is untouched
            self._notify("end")
            if isinstance(exc, (CompactionFailed, KeyboardInterrupt, SystemExit)):
                raise
            raise CompactionFailed("compaction stopped before the swap: {!r}".format(exc)) from exc
        try:
            # 5. (removed in the final fix round: there is no index snapshot to delete)
            self._w.close()                                                   # 6. close BOTH handles
            self._r.close()
            pause("compact.before-swap")
            os.replace(tmp, self.path)                                        # 7. the only switch
            pause("compact.after-swap")
            recovery.fsync_dir(self.path)                                     # 8. the rename is durable
            self._w = open(self.path, "r+b", buffering=0)                     # 9. reopen, new offsets
            self._r = open(self.path, "rb", buffering=0)
            actual = os.fstat(self._w.fileno()).st_size
            if actual != size:
                raise DatabaseFailed("file is {} bytes after the swap, expected {}".format(actual, size))
            self._index = new_index
            self._end = size
            pause("compact.before-cleanup")
        except BaseException as exc:
            self._failed = "compaction failed after it closed the handles: {!r}".format(exc)
            self._broken = True
            self._notify("end")
            raise DatabaseFailed(self._failed) from exc
        self._notify("end")                                                   # 10. release the lock (caller)

    def _apply(self, clean, start):
        position = start + layout.TXN_HEADER.size
        for op, key, value in clean:
            position += layout.OP_HEADER.size + len(key)
            if op == PUT:
                self._index[key] = layout.pack_location(position, len(value))
            else:
                self._index.pop(key, None)
            position += len(value)

    def _rollback(self, start, exc):
        """Truncate back to E and fsync. On failure, mark the database failed."""
        try:
            self._w.truncate(start)
            os.fsync(self._w.fileno())
            return True
        except BaseException as second:
            self._failed = "{!r}; then rollback: {!r}".format(exc, second)
            return False

    # ---- helpers -----------------------------------------------------------------

    def _write_all(self, data):
        """An unbuffered write can be short: loop until every byte is out."""
        view = memoryview(data)
        while view:
            n = self._w.write(view)
            if not n:
                raise OSError("write returned {!r}".format(n))
            view = view[n:]

    def _read_at(self, offset, length):
        if length == 0:
            return b""
        self._r.seek(offset)
        parts, got = [], 0
        while got < length:
            b = self._r.read(length - got)
            if not b:
                raise DatabaseFailed("value at offset {} is cut short ({} of {} bytes)".format(offset, got, length))
            parts.append(b)
            got += len(b)
        return parts[0] if len(parts) == 1 else b"".join(parts)

    def _check_usable(self, write):
        if self._closed:
            raise DatabaseFailed("database is closed")
        if getattr(self, "_broken", False):
            raise DatabaseFailed("database handles were lost in a failed compaction; reopen it: " + self._failed)
        if write and self._failed:
            raise DatabaseFailed("database failed earlier and refuses writes until reopened: " + self._failed)


def _write_fully(f, data):
    """Unbuffered writes can be short: loop until every byte is out."""
    view = memoryview(data)
    while view:
        n = f.write(view)
        if not n:
            raise OSError("write returned {!r}".format(n))
        view = view[n:]


def _plan_batches(entries):
    """Split live entries into transactions of at most COMPACT_BATCH_OPS ops and
    COMPACT_BATCH_BYTES value bytes (at least one op each)."""
    plan, batch, value_bytes = [], [], 0
    for entry in entries:
        if batch and (len(batch) >= COMPACT_BATCH_OPS or value_bytes + entry[2] > COMPACT_BATCH_BYTES):
            plan.append(batch)
            batch, value_bytes = [], 0
        batch.append(entry)
        value_bytes += entry[2]
    if batch:
        plan.append(batch)
    return plan


def _planned_size(plan):
    """Exact size of the compacted file: header + per batch 20 + per op 7 + key + value."""
    return layout.HEADER_SIZE + sum(
        layout.TXN_OVERHEAD + sum(layout.OP_HEADER.size + len(key) + length for key, _, length, _ in batch)
        for batch in plan)

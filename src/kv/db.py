"""The engine: open, put, get, delete, batch, close (CLAUDE.md sections 7, 9, 12).

put, delete and batch all go through ONE write path, DB._commit, which follows
the frozen write order in CLAUDE.md section 9 step by step. One global lock
(section 12) is held by every operation, so nothing ever overlaps.
"""
import os
import threading

from . import layout
from . import recovery
from . import snapshot
from .errors import DatabaseFailed, InvalidInput, SizeCapReached
from .pause import pause

PUT = layout.OP_PUT
DELETE = layout.OP_DELETE
_OP_NAMES = {PUT: PUT, DELETE: DELETE, "put": PUT, "del": DELETE, "delete": DELETE}
SNAPSHOT_MIN = 10_000      # take an index snapshot after max(SNAPSHOT_MIN, keys // 4) transactions


def open_db(path, max_bytes=0, snapshot_every=SNAPSHOT_MIN):
    """Open (or create) the database at path. Returns (db, report).

    report is recovery.Report(keys, seconds, bytes_cut, trailer_restored, created, snapshot).
    snapshot_every: the minimum number of transactions between index snapshots
    (tests use a small number; 0 turns snapshots off).
    """
    rec = recovery.recover(path, max_bytes)
    return DB(path, rec, snapshot_every), rec.report


class DB:
    def __init__(self, path, rec, snapshot_every=SNAPSHOT_MIN):
        self.path = path
        self._snapshot_every = snapshot_every
        self._since_snapshot = rec.replayed      # transactions in the log that no snapshot covers yet
        self.snapshot_error = None               # last snapshot failure (the data itself is never affected)
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
        raise NotImplementedError("compaction is Phase 7; its order is not frozen yet (CLAUDE.md section 11)")

    def close(self):
        """Close both handles. Writes nothing: every committed transaction is already fsynced."""
        with self._lock:
            if self._closed:
                return
            pause("close")
            self._closed = True
            self._w.close()
            self._r.close()

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
                raise SizeCapReached("transaction needs {} bytes; file is {} of max_bytes {}".format(
                    size, self._end, self._max_bytes))
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
            self._since_snapshot += 1
            if self._snapshot_every and self._since_snapshot >= max(self._snapshot_every, len(self._index) // 4):
                self._snapshot(layout.encode_commit(body_len, crc))
        # 8. the lock is released; returning is the success the caller (serve) ACKs

    def _snapshot(self, log_tail):
        """After a COMMITTED transaction: save the index to <path>.idx (a cache).

        Runs under the lock, after step 7, so the index matches the log up to
        self._end exactly. A failure here never fails the write: the
        transaction is already durable; the snapshot is only a shortcut.
        """
        try:
            snapshot.write(self.path, self._index, self._end, log_tail)
            self.snapshot_error = None
        except OSError as exc:
            self.snapshot_error = repr(exc)
            snapshot.remove_leftover(self.path)
        self._since_snapshot = 0

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
        if write and self._failed:
            raise DatabaseFailed("database failed earlier and refuses writes until reopened: " + self._failed)

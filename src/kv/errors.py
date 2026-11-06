"""The named errors the engine raises on purpose (CLAUDE.md section 7, README "Errors")."""


class KVError(Exception):
    """Base class of every error the engine raises on purpose."""


class InvalidInput(KVError, ValueError):
    """The request breaks a limit or a type rule. Nothing was written."""


class SizeCapReached(KVError):
    """The whole transaction would push the file past max_bytes. Nothing was written."""


class NotOurFile(KVError):
    """Wrong magic, version or flags, or a short file that is not the start of
    our header. The file was not changed."""


class CorruptFile(KVError):
    """A byte that a kill cannot produce (CLAUDE.md section 10). The file was not changed."""

    def __init__(self, offset, reason):
        super().__init__("corrupt at offset {}: {}".format(offset, reason))
        self.offset = offset
        self.reason = reason


class DatabaseBusy(KVError):
    """Another open of this database (another process, or another open in this
    process) holds its lock <path>.lock. Nothing was read or changed."""


class CompactionFailed(KVError):
    """Compaction stopped before its swap (disk full, a failed check of the new
    file, or the result would not fit max_bytes). The temporary file was
    deleted; the database is unchanged and still usable."""


class DatabaseFailed(KVError):
    """A write failed AND its rollback failed, or the database is closed.
    Every later write is refused until the file is reopened."""

"""Pause points for targeted crash tests (CLAUDE.md section 13).

If the environment variable KVDB_PAUSE_AT equals a pause point's name, the
engine writes "PAUSE <name>" to stderr, flushes it, and blocks forever, so the
tester can hard-kill it at exactly that step. With the variable unset, pause()
does nothing. The variable is read on every call, so tests can set it late.
"""
import os
import sys
import threading

NAMES = frozenset([
    "create.mid-header",
    "write.before-body", "write.mid-body", "write.after-body", "write.after-body-sync",
    "write.after-marker", "write.after-commit-sync", "write.after-index",
    "recovery.replay", "recovery.before-cut", "recovery.after-cut", "recovery.before-trailer",
    "compact.mid-write", "compact.before-swap", "compact.after-swap", "compact.before-cleanup",
    "snapshot.before-swap", "snapshot.after-swap",
    "close",
])

_FOREVER = threading.Event()   # never set


def pause(name):
    """Block forever here if KVDB_PAUSE_AT == name. Otherwise return at once."""
    if name not in NAMES:
        raise ValueError("unknown pause point: " + name)   # a typo in our code, caught by tests
    if os.environ.get("KVDB_PAUSE_AT") == name:
        sys.stderr.write("PAUSE {}\n".format(name))
        sys.stderr.flush()
        _FOREVER.wait()

"""Shared helpers for the engine tests. Work folders live on the real disk in
.torture-work/ inside the repository, never in /tmp (CLAUDE.md section 6)."""
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".torture-work")


class WorkDir:
    """A fresh folder per test: `with WorkDir() as w: path = w.path("db.kv")`."""

    def __enter__(self):
        os.makedirs(WORK, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="test-", dir=WORK)
        return self

    def path(self, name):
        return os.path.join(self.dir, name)

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

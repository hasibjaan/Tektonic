"""Prove the engine tests can fail: plant one bug at a time in a COPY of the code.

Usage: python3 tools/mutation_check_engine.py
For each bug ("mutant"), src/ and tests/ are copied to .torture-work/mutant-*/,
one line is changed in the copy, and tests.test_db plus tests.test_crash run
there in a fresh process (the crash tests' child processes import the copy too).
The real source files are never changed.

Mutants marked expected_survivor=True are bugs that process-kill tests CANNOT
see (for example a missing fsync: after a process kill the OS still has the
bytes). They are listed to show that limit honestly.
Exit code 0 if every other mutant was caught.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, ".torture-work")

# (name, file, original text, replacement, expected_survivor)
MUTANTS = [
    ("recovery cuts one byte too little", "src/kv/recovery.py",
     "os.ftruncate(fd, result.offset)", "os.ftruncate(fd, result.offset + 1)", False),
    ("recovery does not restore the trailer", "src/kv/recovery.py",
     "_write_at(fd, size, layout.TRAILER_BYTES)", "None", False),
    ("recovery truncates instead of refusing corruption", "src/kv/recovery.py",
     "if result.kind == CORRUPT:", "if False:", False),
    ("writer trusts the file position (no seek to E)", "src/kv/db.py",
     "self._w.seek(start)", "None", False),
    ("rollback does not truncate", "src/kv/db.py",
     "self._w.truncate(start)", "None", False),
    ("size cap off by one", "src/kv/db.py",
     "self._end + size > self._max_bytes", "self._end + size >= self._max_bytes", False),
    ("index applied as put for a delete", "src/kv/db.py",
     "                self._index.pop(key, None)\n            position += len(value)",
     "                self._index[key] = layout.pack_location(position, 0)\n            position += len(value)", False),
    ("commit fsync removed (step 6)", "src/kv/db.py",
     "os.fsync(self._w.fileno())                            # 6.", "None  # 6.", True),
]


def run_mutant(name, rel, old, new):
    os.makedirs(WORK, exist_ok=True)
    copy = tempfile.mkdtemp(prefix="mutant-", dir=WORK)
    try:
        for folder in ("src", "tests"):
            shutil.copytree(os.path.join(ROOT, folder), os.path.join(copy, folder),
                            ignore=shutil.ignore_patterns("__pycache__"))
        target = os.path.join(copy, rel)
        with open(target, encoding="utf-8") as f:
            code = f.read()
        if code.count(old) != 1:
            return None, "original text not found exactly once"
        with open(target, "w", encoding="utf-8") as f:
            f.write(code.replace(old, new))
        env = dict(os.environ)
        env.pop("KVDB_PAUSE_AT", None)
        result = subprocess.run([sys.executable, "-m", "unittest", "tests.test_db", "tests.test_crash"],
                                cwd=copy, env=env, capture_output=True, text=True, timeout=900)
        last = [l for l in result.stderr.splitlines() if l.startswith(("OK", "FAILED"))]
        return result.returncode != 0, (last[-1] if last else result.stderr[-300:])
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def main():
    problems = 0
    for name, rel, old, new, expected_survivor in MUTANTS:
        caught, detail = run_mutant(name, rel, old, new)
        if caught is None:
            print("SKIPPED  {}: {}".format(name, detail))
            problems += 1
        elif caught:
            print("CAUGHT   {}: {}".format(name, detail))
        elif expected_survivor:
            print("SURVIVED {} (EXPECTED: a process kill cannot see this): {}".format(name, detail))
        else:
            print("SURVIVED {}: no test failed — a test is missing".format(name))
            problems += 1
    print("{} unexpected survivor(s) or skip(s)".format(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

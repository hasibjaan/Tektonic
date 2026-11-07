"""Prove the engine tests can fail: plant one bug at a time in a COPY of the code.

Usage: python3 tools/mutation_check_engine.py
For each bug ("mutant"), src/, tests/ and kvdb.py are copied to .torture-work/mutant-*/,
one line is changed in the copy, and tests.test_db, test_crash, test_lock and test_compact run
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
    ("fast path skips the crc32 check", "src/kv/recovery.py",
     "                                and crc == crc32(view[p:end])):", "                                ):", False),
    ("fast path skips the trailer check", "src/kv/recovery.py",
     "if (magic == b\"CMIT\" and body_len == end - p and trailer == 0xEE", "if (magic == b\"CMIT\" and body_len == end - p", False),
    ("no database lock", "src/kv/db.py",
     "        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", "        pass", False),
    ("compaction deletes the original before the swap", "src/kv/db.py",
     "            # 5. (removed in the final fix round: there is no index snapshot to delete)",
     "            os.remove(self.path)", False),
    ("compaction skips validating the new file", "src/kv/db.py",
     "            if problem:\n                raise CompactionFailed(problem)", "            if False:\n                raise CompactionFailed(problem)", False),
    ("compacted file loses max_bytes", "src/kv/db.py",
     "_write_fully(f, layout.encode_header(self._max_bytes))", "_write_fully(f, layout.encode_header(0))", False),
    ("E5 refuses instead of compacting", "src/kv/db.py",
     "                self._commit_by_compaction(clean, size)              # E5: compact WITH this change, or refuse",
     "                raise SizeCapReached('planted')", False),
    ("leftover .compact.tmp not removed at open", "src/kv/recovery.py",
     "        _remove_quietly(path + COMPACT_TMP_SUFFIX)                         # a killed compaction: never trusted", "        pass", False),
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
        shutil.copy(os.path.join(ROOT, "kvdb.py"), copy)          # tests/test_lock.py starts kvdb.py serve
        target = os.path.join(copy, rel)
        with open(target, encoding="utf-8") as f:
            code = f.read()
        if code.count(old) != 1:
            return None, "original text not found exactly once"
        with open(target, "w", encoding="utf-8") as f:
            f.write(code.replace(old, new))
        env = dict(os.environ)
        env.pop("KVDB_PAUSE_AT", None)
        try:
            result = subprocess.run([sys.executable, "-m", "unittest", "tests.test_db", "tests.test_crash",
                                     "tests.test_lock", "tests.test_compact"],
                                    cwd=copy, env=env, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired:
            return True, "TIMEOUT after 900 s (a test hung: counted as caught, but the hang is a test bug)"
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

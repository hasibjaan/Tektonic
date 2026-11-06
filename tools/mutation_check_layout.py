"""Prove the layout tests can fail: plant one bug at a time and run them.

Usage: python3 tools/mutation_check_layout.py
Each bug ("mutant") is applied to an IN-MEMORY copy of src/kv/layout.py; the
file on disk is never changed. A mutant that makes no test fail means a test
is missing. Exit code 0 if every mutant was caught, 1 otherwise.
"""
import io
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MUTANTS = {
    "skip the crc32 check":
        ("if crc != zlib.crc32(mv[start:body_end]):", "if False:"),
    "return ops when the marker is cut":
        ('return Decoded(INCOMPLETE, None, p, "commit marker is cut")',
         'return Decoded(INCOMPLETE, ops, p, "commit marker is cut")'),
    "trust val_len without checking the bytes left":
        ("if size - key_at < key_len + val_len:", "if size - key_at < key_len:"),
    "accept a missing trailer anywhere as OK":
        ('return Decoded(NO_TRAILER, ops, q, "trailer byte is missing")',
         'return Decoded(OK, ops, q, "")'),
    "skip the key length limit":
        ("if not 1 <= key_len <= MAX_KEY:", "if False:"),
    "skip the op_count limit in validation":
        ("if not 1 <= len(ops) <= MAX_OPS:", "if False:"),
}


def main():
    import src.kv as pkg
    with open(os.path.join(ROOT, "src", "kv", "layout.py"), encoding="utf-8") as f:
        code = f.read()
    survivors = 0
    for name, (old, new) in MUTANTS.items():
        if code.count(old) != 1:
            print("SKIPPED {}: the original line was not found exactly once".format(name))
            survivors += 1
            continue
        mod = types.ModuleType("src.kv.layout")
        exec(compile(code.replace(old, new), "mutant_layout.py", "exec"), mod.__dict__)
        sys.modules["src.kv.layout"] = mod
        pkg.layout = mod
        sys.modules.pop("tests.test_layout", None)
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_layout")
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        bad = result.failures + result.errors
        tests = sorted({t.id().split(".")[-1].split(" ")[0] for t, _ in bad})
        if bad:
            print("CAUGHT   {}: {} failing (sub)tests in {}".format(name, len(bad), ", ".join(tests)))
        else:
            print("SURVIVED {}: no test failed".format(name))
            survivors += 1
    print("{} of {} mutants caught".format(len(MUTANTS) - survivors, len(MUTANTS)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())

"""Prove the torture tool catches real engine bugs (Phase 5, proof b).

Usage: python3 tools/planted_bugs.py [--seed N] [--iterations N]
For each bug, kvdb.py and src/ are copied to .torture-work/planted-<name>/,
ONE bug is planted in the copy's src/kv/db.py, and torture.py runs against the
copy (--kvdb). The real source is never changed. (The pack suggests a
throwaway git branch; a copy is the same test without touching git history.)
Every run must FAIL and name an operation id. Output goes to stdout; failure
folders go to evidence/torture/planted/<name>/.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANCHOR = "            clean, size = layout.validate_ops(ops)                    # 1. validate the WHOLE request\n"

BUGS = {
    "drop-acked-write": (
        "the 5th transaction of every serve process returns success without writing anything",
        "            self._planted = getattr(self, '_planted', 0) + 1\n"
        "            if self._planted == 5:\n"
        "                return                                    # PLANTED BUG: ACK without a write\n"),
    "half-batch": (
        "a batch is written with only its first half of operations",
        "            if len(clean) > 1:                              # PLANTED BUG: half a batch\n"
        "                clean = clean[:len(clean) // 2]\n"
        "                size = layout.TXN_OVERHEAD + sum(layout.OP_HEADER.size + len(k) + len(v) for _, k, v in clean)\n"),
    "invent-key": (
        "every single put also writes a key nobody sent",
        "            if len(clean) == 1 and clean[0][0] == PUT:     # PLANTED BUG: an invented key\n"
        "                extra = (PUT, b'invented-' + clean[0][1], b'never-sent')\n"
        "                clean = clean + [extra]\n"
        "                size += layout.OP_HEADER.size + len(extra[1]) + len(extra[2])\n"),
}


def main():
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 2718
    iterations = int(sys.argv[sys.argv.index("--iterations") + 1]) if "--iterations" in sys.argv else 20
    caught = 0
    for name, (what, code) in BUGS.items():
        copy = os.path.join(ROOT, ".torture-work", "planted-" + name)
        shutil.rmtree(copy, ignore_errors=True)
        os.makedirs(copy)
        shutil.copy(os.path.join(ROOT, "kvdb.py"), copy)
        shutil.copytree(os.path.join(ROOT, "src"), os.path.join(copy, "src"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        target = os.path.join(copy, "src", "kv", "db.py")
        with open(target, encoding="utf-8") as f:
            text = f.read()
        assert text.count(ANCHOR) == 1, "anchor line not found in src/kv/db.py"
        with open(target, "w", encoding="utf-8") as f:
            f.write(text.replace(ANCHOR, ANCHOR + code))
        cmd = [sys.executable, os.path.join(ROOT, "torture.py"), "--iterations", str(iterations),
               "--seed", str(seed), "--kvdb", os.path.join(copy, "kvdb.py"),
               "--failures-dir", os.path.join(ROOT, "evidence", "torture", "planted", name)]
        print("=" * 78)
        print("PLANTED BUG {}: {}".format(name, what))
        print("command: python3 torture.py --iterations {} --seed {} --kvdb .torture-work/planted-{}/kvdb.py".format(
            iterations, seed, name))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        lines = r.stdout.splitlines()
        fail_at = next((i for i, l in enumerate(lines) if l.startswith("FAIL at")), None)
        shown = lines[:fail_at + 12] if fail_at is not None else lines[-6:]
        for l in shown:
            print(l[:220])
        if fail_at is not None and len(lines) > fail_at + 12:
            print("  ... ({} more lines)".format(len(lines) - fail_at - 13))
            print(lines[-1])
        print("exit code {} -> {}".format(r.returncode, "CAUGHT" if r.returncode == 1 else "NOT CAUGHT"))
        caught += r.returncode == 1
        shutil.rmtree(copy, ignore_errors=True)
    print("=" * 78)
    print("{} of {} planted bugs caught".format(caught, len(BUGS)))
    return 0 if caught == len(BUGS) else 1


if __name__ == "__main__":
    sys.exit(main())

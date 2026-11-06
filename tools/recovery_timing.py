"""ROUGH recovery timing (Phase 3 early warning; Phase 6 does the real measurement).

Usage: python3 tools/recovery_timing.py [--write]
Builds database files in .torture-work/timing/ DIRECTLY with src/kv/layout.py
(no serve, no fsync per write — only the file contents matter for open), then
times the full public open, src.kv.open_db, several times. Contents are checked
OUTSIDE the timed part. --write also writes evidence/recovery-early-estimate.md.

Datasets (1,000,000 distinct keys, key b"key%07d" = 10 bytes, value b"val%07d" = 10 bytes):
  A  100 batches of 10,000 puts      (what the Phase 3 prompt asks for)
  B  1,000,000 single-put transactions (the slow case for scenario F3)
  C  dataset B plus half of a 10,000-op batch at the end (B6: torn, must be cut)
"""
import os
import platform
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kv import layout, open_db  # noqa: E402

N = 1_000_000
RUNS = 3
DIR = os.path.join(ROOT, ".torture-work", "timing")


def key(i):
    return b"key%07d" % i


def val(i):
    return b"val%07d" % i


def build(path, batch_size):
    with open(path, "wb") as f:
        f.write(layout.encode_header(0))
        buf = []
        for start in range(0, N, batch_size):
            ops = [(layout.OP_PUT, key(i), val(i)) for i in range(start, min(N, start + batch_size))]
            buf.append(layout.encode_txn(ops))
            if len(buf) >= 10_000:
                f.write(b"".join(buf))
                buf = []
        f.write(b"".join(buf))
        f.flush()
        os.fsync(f.fileno())


def torn_batch():
    ops = [(layout.OP_PUT, b"torn%05d" % i, b"x" * 10) for i in range(10_000)]
    full = layout.encode_txn(ops)
    return full[:len(full) // 2]


def timed_open(path):
    t0 = time.perf_counter()
    db, report = open_db(path)
    seconds = time.perf_counter() - t0
    return db, report, seconds


def verify(db):
    assert len(db) == N, len(db)
    for i in (0, 1, N // 2, N - 1):
        assert db.get(key(i)) == val(i), i
    assert db.get(b"torn00000") is None


def machine():
    def sysctl(name):
        try:
            return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True).stdout.strip()
        except OSError:
            return "unknown"
    ram = sysctl("hw.memsize")
    return {"cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
            "ram": "{:.0f} GB".format(int(ram) / 2**30) if ram.isdigit() else "unknown",
            "os": platform.platform(), "python": platform.python_version()}


def main():
    os.makedirs(DIR, exist_ok=True)
    lines = []
    out = lines.append
    m = machine()
    out("# Recovery timing — ROUGH ESTIMATE (Phase 3)")
    out("")
    out("**This is an early warning, not the Phase 6 benchmark.** Files are built directly with "
        "`src/kv/layout.py`, not through `serve`; each file is timed right after it was written, "
        "so it is in the OS page cache (warm cache). Command: `python3 tools/recovery_timing.py --write`.")
    out("")
    out("- Machine: {cpu}, {ram} RAM, {os}, Python {python}".format(**m))
    out("- Disk: internal SSD, APFS (CLAUDE.md section 5)")
    out("- Run at: {}".format(time.strftime("%Y-%m-%d %H:%M:%S %Z")))
    out("- Keys: 1,000,000 distinct, 10 bytes each. Values: 10 bytes each. What is timed: the full `src.kv.open_db` call.")
    out("")
    out("| Dataset | How it was built | File size (bytes) | Open runs (s) | Slowest (s) | Over 1.5 s? |")
    out("|---|---|---|---|---|---|")
    results = {}
    for name, batch_size, label in (("A", 10_000, "100 batches of 10,000 puts"),
                                    ("B", 1, "1,000,000 single-put transactions")):
        path = os.path.join(DIR, "dataset-%s.kv" % name)
        t0 = time.perf_counter()
        build(path, batch_size)
        build_s = time.perf_counter() - t0
        runs = []
        for _ in range(RUNS):
            db, report, seconds = timed_open(path)
            verify(db)                                     # outside the timed part
            db.close()
            runs.append(seconds)
        results[name] = (path, runs)
        out("| {} | {} (built in {:.1f} s) | {:,} | {} | **{:.3f}** | {} |".format(
            name, label, build_s, os.path.getsize(path), ", ".join("%.3f" % r for r in runs),
            max(runs), "**YES**" if max(runs) > 1.5 else "no"))
    path_b = results["B"][0]
    good_size = os.path.getsize(path_b)
    tail = torn_batch()
    runs, cuts = [], []
    for _ in range(RUNS):
        with open(path_b, "ab") as f:
            f.write(tail)
            f.flush()
            os.fsync(f.fileno())
        db, report, seconds = timed_open(path_b)
        verify(db)
        db.close()
        assert os.path.getsize(path_b) == good_size
        runs.append(seconds)
        cuts.append(report.bytes_cut)
    out("| C | dataset B + half of a 10,000-op batch at the end (B6); open cuts {:,} bytes each run | {:,} before the cut | {} | **{:.3f}** | {} |".format(
        cuts[0], good_size + len(tail), ", ".join("%.3f" % r for r in runs), max(runs),
        "**YES**" if max(runs) > 1.5 else "no"))
    out("")
    worst = max(max(r) for _, r in results.values())
    worst = max(worst, max(runs))
    out("Slowest open over all runs: **{:.3f} s** against the 2-second limit (F3, B6).".format(worst))
    text = "\n".join(lines) + "\n"
    print(text, end="")
    for name in ("A", "B"):
        os.remove(results[name][0])
    if "--write" in sys.argv:
        with open(os.path.join(ROOT, "evidence", "recovery-early-estimate.md"), "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Recovery time in every crash state (final fix round, Fix 4c). No index cache exists.

Usage: python3 tools/recovery_worst_case.py [runs]     (default 5)
Files are built directly with src/kv/layout.py (open time depends only on the bytes).
Base data: 1,000,000 keys key%07d -> val%07d, as single-put transactions ("single",
47 MB) or as 100 batches of 10,000 ("batched", 27 MB). Each case is a COPY of a base
file changed into one crash state; each run starts `kvdb.py serve` on a fresh copy
and times it until READY. Prints every run and the worst per case.
"""
import os
import platform
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kv import layout  # noqa: E402

N = 1_000_000
WORK = os.path.join(ROOT, ".torture-work", "worst-case")


def build(path, batch):
    with open(path, "wb") as f:
        f.write(layout.encode_header(0))
        buf = []
        for start in range(0, N, batch):
            buf.append(layout.encode_txn([(layout.OP_PUT, b"key%07d" % i, b"val%07d" % i)
                                          for i in range(start, min(N, start + batch))]))
            if len(buf) >= 10_000:
                f.write(b"".join(buf))
                buf = []
        f.write(b"".join(buf))


def append(path, data):
    with open(path, "ab") as f:
        f.write(data)


def torn_batch():
    full = layout.encode_txn([(layout.OP_PUT, b"torn%05d" % i, b"x" * 10) for i in range(10_000)])
    return full[:len(full) // 2]


def large_value_tail():
    return b"".join(layout.encode_txn([(layout.OP_PUT, b"big%03d" % i, os.urandom(1_048_576))])
                    for i in range(100))                       # 100 x 1 MB after the base data


def large_batch_tail():
    return b"".join(layout.encode_txn([(layout.OP_PUT, b"lb%02d-%05d" % (j, i), b"y" * 10) for i in range(10_000)])
                    for j in range(20))                        # 20 batches of 10,000 ops


def cases(base):
    """(name, function that turns a copy of the base file into the crash state)."""
    def f4_cut(p):
        with open(p, "r+b") as f:
            f.truncate(os.path.getsize(p) - 1)
    def compact_before_swap(p):
        shutil.copyfile(base["batched"], p + ".compact.tmp")   # a complete, valid temp file is left
    return [
        ("healthy, single puts", "single", lambda p: None),
        ("healthy, batched", "batched", lambda p: None),
        ("F4: 1-byte cut, single puts", "single", f4_cut),
        ("kill at compact.before-swap (original = single puts)", "single", compact_before_swap),
        ("B6: half a 10,000-op batch, single puts", "single", lambda p: append(p, torn_batch())),
        ("B6: half a 10,000-op batch, batched", "batched", lambda p: append(p, torn_batch())),
        ("tail of 100 x 1 MB values, batched", "batched", lambda p: append(p, large_value_tail())),
        ("tail of 20 batches of 10,000 ops, batched", "batched", lambda p: append(p, large_batch_tail())),
    ]


def ready_time(path):
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "kvdb.py"), "serve", path], env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while True:
        line = p.stderr.readline().decode()
        if line.startswith("READY") or not line:
            break
    seconds = time.perf_counter() - t0
    p.kill()
    p.wait()
    return seconds, line.strip()


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    print("captured", time.strftime("%Y-%m-%d %H:%M:%S %Z"), "|", platform.platform(), "| Python", platform.python_version())
    base = {"single": os.path.join(WORK, "base-single.kv"), "batched": os.path.join(WORK, "base-batched.kv")}
    build(base["single"], 1)
    build(base["batched"], 10_000)
    summary = []
    for name, which, make in cases(base):
        times, size, line = [], 0, ""
        for _ in range(runs):
            p = os.path.join(WORK, "case.kv")
            for leftover in (p, p + ".compact.tmp", p + ".lock"):
                if os.path.exists(leftover):
                    os.remove(leftover)
            shutil.copyfile(base[which], p)
            make(p)
            size = os.path.getsize(p)
            s, line = ready_time(p)
            times.append(s)
        summary.append((name, max(times)))
        print("{:<55} file {:>13,} bytes  runs {}  WORST {:.3f} s   ({})".format(
            name, size, " ".join("%.3f" % s for s in times), max(times), line))
    shutil.rmtree(WORK, ignore_errors=True)
    over = [n for n, s in summary if s >= 1.8]
    print("cases at or above 1.8 s: {}".format(", ".join(over) if over else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

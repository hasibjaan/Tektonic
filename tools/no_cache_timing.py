"""Open time WITHOUT the index snapshot (final fix round, Fix 2 measurement).

Usage: python3 tools/no_cache_timing.py [runs]     (default 5)
Builds two 1,000,000-key files DIRECTLY with src/kv/layout.py (the open time
depends only on the file's contents, not on how it was written):
  single   1,000,000 single-put transactions      (47,000,016 bytes)
  batched  100 batches of 10,000 puts             (27,002,016 bytes)
Keys key%07d (10 bytes), values val%07d (10 bytes). For each run the snapshot
<path>.idx is deleted first, then `kvdb.py serve` is started and timed until it
prints READY (recovery done, ready for reads and writes). Prints every run and
the worst. Warm cache (the file was just written or read).
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
        f.flush()
        os.fsync(f.fileno())


def open_time(path):
    for leftover in (path + ".idx", path + ".idx.tmp"):
        if os.path.exists(leftover):
            os.remove(leftover)
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
    work = os.path.join(ROOT, ".torture-work", "no-cache")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    print("captured", time.strftime("%Y-%m-%d %H:%M:%S %Z"), "|", platform.platform(), "| Python", platform.python_version())
    worst = {}
    for name, batch in (("single", 1), ("batched", 10_000)):
        path = os.path.join(work, name + ".kv")
        build(path, batch)
        print("{}: {:,} bytes".format(name, os.path.getsize(path)))
        times = []
        for r in range(1, runs + 1):
            s, ready = open_time(path)
            times.append(s)
            print("  run {}: serve start -> READY {:.3f} s   ({})".format(r, s, ready))
        worst[name] = max(times)
        print("  worst: {:.3f} s".format(worst[name]))
    shutil.rmtree(work, ignore_errors=True)
    print("WORST without the cache: single {:.3f} s, batched {:.3f} s (limit 2 s)".format(
        worst["single"], worst["batched"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

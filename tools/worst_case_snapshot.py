"""Worst case for the snapshot rule (Phase 6).

Usage: python3 tools/worst_case_snapshot.py   (after: torture.py --mode scale --keys 1000000)
The last snapshot covers only the first 750,000 of 1,000,000 single-put
transactions — the rule max(10,000, keys // 4) allows a tail of about 250,000
at 1,000,000 keys. Uses a COPY of the serve-built dataset in
.torture-work/scale-1000000-single/db.kv; the snapshot is made with
src/kv/snapshot.py write from the index of exactly the first 750,000
transactions. Then times `kvdb.py serve` start -> READY five times.
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kv import recovery, snapshot  # noqa: E402


def main():
    src = os.path.join(ROOT, ".torture-work", "scale-1000000-single", "db.kv")
    work = os.path.join(ROOT, ".torture-work", "worst-case")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    db = os.path.join(work, "db.kv")
    shutil.copyfile(src, db)
    txn = 47                                   # 27 + 10-byte key + 10-byte value
    covered = 16 + txn * 750_000
    assert os.path.getsize(db) == 16 + txn * 1_000_000, "expected the 1,000,000-key scale dataset"
    part = os.path.join(work, "first-750000.kv")
    with open(db, "rb") as f, open(part, "wb") as g:
        g.write(f.read(covered))
    index = recovery.recover(part).index
    with open(db, "rb") as f:
        f.seek(covered - 17)
        tail = f.read(17)
    snapshot.write(db, index, covered, tail)
    os.remove(part)
    print("captured", time.strftime("%Y-%m-%d %H:%M:%S %Z"))
    print("snapshot covers {:,} keys, E_c = {:,}; log end = {:,}".format(len(index), covered, os.path.getsize(db)))
    times = []
    for run in range(1, 6):
        t0 = time.perf_counter()
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "kvdb.py"), "serve", db],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        while True:
            line = p.stderr.readline().decode()
            if line.startswith("READY") or not line:
                break
        seconds = time.perf_counter() - t0
        p.kill()
        p.wait()
        times.append(seconds)
        print("run {}: serve start -> READY {:.3f} s   ({})".format(run, seconds, line.strip()))
    rec = recovery.recover(db)
    print("transactions replayed after the snapshot: {:,}; keys: {:,}".format(rec.replayed, len(rec.index)))
    print("slowest: {:.3f} s (limit 2 s)".format(max(times)))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

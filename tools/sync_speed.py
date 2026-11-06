"""How fast is one durable put on this laptop? (Phase 6, step 1)

Usage: python3 tools/sync_speed.py [N]      (default N = 1000)
Starts `kvdb.py serve` on a fresh file in .torture-work/, sends N single puts
ONE AT A TIME (each waits for its ACK, i.e. for both fsyncs), and reports the
average and the projection for 1,000,000 single puts. Uses only the serve
protocol, like the torture tool.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    work = os.path.join(ROOT, ".torture-work", "sync-speed")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "db.kv")
    if os.path.exists(db):
        os.remove(db)
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "kvdb.py"), "serve", db], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while not proc.stderr.readline().startswith(b"READY"):
        pass
    times = []
    for i in range(n):
        t0 = time.perf_counter()
        proc.stdin.write(b"p%d put key%07d val%07d\n" % (i, i, i))
        proc.stdin.flush()
        line = proc.stdout.readline()
        times.append(time.perf_counter() - t0)
        assert line == b"ACK p%d\n" % i, line
    proc.stdin.close()
    proc.wait()
    times.sort()
    avg = sum(times) / n
    print("single puts through serve, one at a time: {}".format(n))
    print("average {:.3f} ms, median {:.3f} ms, slowest {:.3f} ms".format(
        avg * 1000, times[n // 2] * 1000, times[-1] * 1000))
    total = avg * 1_000_000
    print("projected for 1,000,000 single puts: {:.0f} s = {:.1f} minutes".format(total, total / 60))
    print("file size after {} puts: {} bytes".format(n, os.path.getsize(db)))
    os.remove(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())

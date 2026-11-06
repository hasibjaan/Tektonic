"""The torture modes: start serve, hard-kill it, verify the whole state.

The engine is only ever used as a separate process through kvdb.py (CLAUDE.md
section 14). This package never imports the engine (tools/check_imports.py
proves it).
"""
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time

from tester.oracle import Oracle, short
from tester.workload import PROFILES, Profile, Writer, next_request, writer_keys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAUSE_POINTS = [                                        # CLAUDE.md section 13, in order
    "create.mid-header",
    "write.before-body", "write.mid-body", "write.after-body", "write.after-body-sync",
    "write.after-marker", "write.after-commit-sync", "write.after-index",
    "recovery.replay", "recovery.before-cut", "recovery.after-cut", "recovery.before-trailer",
    "compact.mid-write", "compact.before-swap", "compact.after-swap", "compact.before-cleanup",
    "snapshot.before-swap", "snapshot.after-swap",
    "close",
]
TIMEOUT = 120


class ChildGone(Exception):
    pass


class Child:
    """kvdb.py serve as a child process with binary pipes."""

    def __init__(self, kvdb, db, on_reply, pause_at=None):
        env = dict(os.environ)
        env.pop("KVDB_PAUSE_AT", None)
        if pause_at:
            env["KVDB_PAUSE_AT"] = pause_at
        self.proc = subprocess.Popen([sys.executable, kvdb, "serve", db], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.stdin_lock = threading.Lock()
        self.stderr_lines = []
        self.err_queue = queue.Queue()
        self.on_reply = on_reply
        self.readers = [threading.Thread(target=self._read_stderr, daemon=True),
                        threading.Thread(target=self._read_stdout, daemon=True)]
        for t in self.readers:
            t.start()

    def _read_stderr(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            self.stderr_lines.append(line)
            self.err_queue.put(line)
        self.err_queue.put(None)

    def _read_stdout(self):
        for raw in self.proc.stdout:
            if raw.endswith(b"\n"):                       # only complete lines count
                self.on_reply(raw[:-1].decode("utf-8", "replace"))

    def wait_stderr(self, prefix, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.err_queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise ChildGone("no '{}' on stderr after {} s".format(prefix, timeout))
            if line is None:
                raise ChildGone("serve exited before '{}': {}".format(prefix, self.stderr_lines[-5:]))
            if line.startswith(prefix):
                return line

    def send(self, data):
        with self.stdin_lock:                             # a request's lines stay together
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def close_stdin(self):
        with self.stdin_lock:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def kill(self):
        """The hard kill: Popen.kill() = SIGKILL on macOS/Linux. Then drain the pipes."""
        self.proc.kill()
        self.proc.wait(timeout=TIMEOUT)
        for t in self.readers:
            t.join(timeout=TIMEOUT)                       # complete ACK lines still in the pipe count
        for f in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except OSError:
                pass
        return self.proc.returncode


def run_cli(kvdb, *args):
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    r = subprocess.run([sys.executable, kvdb, *args], env=env, capture_output=True, timeout=TIMEOUT * 5)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def read_back(kvdb, db):
    """open, then check, then dump --hex. Returns (dump dict, None) or (None, problem)."""
    code, out, err = run_cli(kvdb, "open", db)
    if code != 0:
        return None, "open failed (exit {}): {} {}".format(code, out.strip(), err.strip()[-300:])
    code, out, err = run_cli(kvdb, "check", db)
    if code != 0 or out.strip() != "OK":
        return None, "check after open is not OK (exit {}): {}".format(code, out.strip())
    code, out, err = run_cli(kvdb, "dump", db, "--hex")
    if code != 0:
        return None, "dump --hex failed (exit {}): {}".format(code, out.strip()[:300])
    dump = {}
    for line in out.splitlines():
        parts = line.split(" ")
        if len(parts) != 2 or not all(p.startswith("hex:") for p in parts):
            return None, "dump --hex line not understood: {!r}".format(line[:100])
        key, value = bytes.fromhex(parts[0][4:]), bytes.fromhex(parts[1][4:])
        if key in dump:
            return None, "dump lists key {} twice".format(short(key))
        dump[key] = value
    return dump, None


def safe_workdir(path):
    real = os.path.realpath(path)
    for bad in ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders"):
        if real == bad or real.startswith(bad + "/"):
            raise SystemExit("refusing --workdir {}: it may be a RAM disk (CLAUDE.md section 6)".format(path))
    return real


class Run:
    """Files and output shared by every mode."""

    def __init__(self, args, label):
        self.args = args
        self.seed = args.seed
        self.kvdb = os.path.abspath(args.kvdb)
        base = safe_workdir(args.workdir)
        self.dir = os.path.join(base, "{}-seed-{}".format(label, args.seed))
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.trace_file = open(os.path.join(self.dir, "trace.log"), "a", encoding="utf-8")
        self.trace_lock = threading.Lock()
        self.failures_dir = os.path.abspath(args.failures_dir)
        self.protocol_errors = []

    def trace(self, text):
        with self.trace_lock:
            self.trace_file.write("{:.3f} {}\n".format(time.monotonic(), text))
            self.trace_file.flush()

    def save_failure(self, iteration, problems, db_after_kill, db, oracle_log):
        folder = os.path.join(self.failures_dir, "seed-{}-iter-{}".format(self.seed, iteration))
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        for src, name in ((db_after_kill, "db-after-kill.kv"), (db, "db-after-open.kv"),
                          (oracle_log, "oracle.log"), (os.path.join(self.dir, "trace.log"), "trace.log")):
            if src and os.path.exists(src):
                shutil.copyfile(src, os.path.join(folder, name))
        with open(os.path.join(folder, "report.txt"), "w", encoding="utf-8") as f:
            f.write("seed {} iteration {} mode {}\n".format(self.seed, iteration, self.args.mode))
            f.write("command: python3 torture.py {}\n".format(" ".join(sys.argv[1:])))
            f.write("\n".join(problems) + "\n")
        return folder


def fail(run, iteration, problems, db_after_kill, db, oracle_log):
    print("FAIL at iteration {} (seed {}, mode {}):".format(iteration, run.seed, run.args.mode))
    for p in problems:
        print("  " + p)
    folder = run.save_failure(iteration, problems, db_after_kill, db, oracle_log)
    print("saved: {}".format(os.path.relpath(folder, REPO)))
    return 1


def make_dispatch(oracle, writers_by_id, run):
    def dispatch(line):
        parts = line.split(" ", 2)
        if len(parts) < 2 or parts[0] not in ("ACK", "ERR", "VAL", "NOTFOUND"):
            run.trace("??? {}".format(line[:200]))
            return
        rid = parts[1]
        run.trace("RECV {}".format(line[:200]))
        try:
            wid = int(rid[1:rid.index("-")])
            if wid not in oracle.models:
                raise ValueError("reply for unknown request id {!r}".format(rid))
            if parts[0] == "ACK":
                oracle.acked(wid, rid)
            elif parts[0] == "ERR":
                oracle.erred(wid, rid, parts[2] if len(parts) > 2 else "")
        except Exception as exc:                          # a protocol break is a FAIL, never silent
            run.protocol_errors.append("reply {!r}: {}".format(line[:120], exc))
            return
        writer = writers_by_id.get(wid)
        if writer is not None:
            writer.replied.set()
    return dispatch


# ---- random kills: modes default, large-values, large-batches -------------------------

def run_random(args):
    profile = PROFILES[args.mode]
    profile.writers = args.writers
    run = Run(args, args.mode)
    db = os.path.join(run.dir, "db.kv")
    oracle_log = os.path.join(run.dir, "oracle.log")
    oracle = Oracle(oracle_log, writer_keys(profile))
    master = random.Random(args.seed)                     # the ONLY source of randomness
    seqs = {w: 0 for w in range(1, profile.writers + 1)}
    passes = with_inflight = 0
    times = []
    print("torture: mode {} seed {} iterations {} writers {} kvdb {}".format(
        args.mode, args.seed, args.iterations, profile.writers, os.path.relpath(run.kvdb, REPO)))
    for it in range(1, args.iterations + 1):
        started = time.monotonic()
        delay = master.uniform(*profile.delay_ms)
        rngs = {w: random.Random(master.getrandbits(64)) for w in seqs}
        writers_by_id = {}
        stop = threading.Event()
        oracle.note("ITERATION {} kill_delay_ms {:.1f}".format(it, delay))
        child = Child(run.kvdb, db, make_dispatch(oracle, writers_by_id, run))
        try:
            child.wait_stderr("READY")
        except ChildGone as exc:
            child.kill()
            return fail(run, it, ["serve did not start: {}".format(exc)], None, db, oracle_log)
        for w in seqs:
            writers_by_id[w] = Writer(w, rngs[w], profile, oracle, child, stop, it, seqs[w], run.trace)
        for wr in writers_by_id.values():
            wr.start()
        time.sleep(delay / 1000.0)
        code = child.kill()                               # the hard kill, writes in flight
        stop.set()
        for wr in writers_by_id.values():
            wr.join(timeout=TIMEOUT)
            seqs[wr.wid] = wr.seq
        in_flight = oracle.in_flight()
        with_inflight += in_flight > 0
        run.trace("KILL iteration {} exit {} in_flight {}".format(it, code, in_flight))
        after_kill = os.path.join(run.dir, "db-after-kill.kv")
        if os.path.exists(db):                            # a broken engine may never create the file
            shutil.copyfile(db, after_kill)
        else:
            after_kill = None
            run.trace("no database file after the kill")
        dump, problem = read_back(run.kvdb, db)
        problems = list(run.protocol_errors) or ([problem] if problem else oracle.verify(dump))
        seconds = time.monotonic() - started
        times.append(seconds)
        status = "FAIL" if problems else "PASS"
        print("iter {:>4} seed {} kill_ms {:>6.1f} in_flight {} acks {:>6} keys {:>4} {} ({:.2f} s)".format(
            it, args.seed, delay, in_flight, oracle.acks, len(dump) if dump is not None else -1, status, seconds))
        sys.stdout.flush()
        if code != -9 and os.name != "nt":
            problems = problems or ["the child was not killed by SIGKILL (exit {})".format(code)]
        if problems:
            return fail(run, it, problems, after_kill, db, oracle_log)
        passes += 1
    avg = sum(times) / len(times)
    print("PASS {}/{}".format(passes, args.iterations))
    print("kills with at least one request in flight: {}/{} ({:.1f}%)".format(
        with_inflight, args.iterations, 100.0 * with_inflight / args.iterations))
    print("average iteration {:.2f} s; projected 200 iterations: {:.0f} s".format(avg, 200 * avg))
    return 0


# ---- targeted kills at pause points ----------------------------------------------------

class Sync:
    """One request at a time from the main thread: send, then wait for a reply or a PAUSE."""

    def __init__(self, oracle, run):
        self.oracle, self.run = oracle, run
        self.replies = queue.Queue()

    def on_reply(self, line):
        parts = line.split(" ", 2)
        try:
            rid = parts[1]
            if parts[0] == "ACK":
                self.oracle.acked(1, rid)
            elif parts[0] == "ERR":
                self.oracle.erred(1, rid, parts[2] if len(parts) > 2 else "")
        except Exception as exc:
            self.run.protocol_errors.append("reply {!r}: {}".format(line[:120], exc))
        self.replies.put(line)


TARGET_EXPECT = {
    # pause point -> what the brief REQUIRES for the unanswered write (None = either is allowed)
    "write.after-commit-sync": "applied",   # A4: MUST be present
    "write.after-index": "applied",         # A4/A5: past the commit sync
}


def run_targeted(args):
    """Each pause point from CLAUDE.md section 13: run to it, hard-kill, verify."""
    run = Run(args, "targeted")
    profile = Profile("targeted", writers=1, keys_per_writer=8, batch_sizes=(10, 10))
    results = []
    print("torture: mode targeted seed {} rounds {} kvdb {}".format(
        args.seed, args.iterations, os.path.relpath(run.kvdb, REPO)))
    master = random.Random(args.seed)
    for rnd in range(1, args.iterations + 1):
        for point in PAUSE_POINTS:
            if point.startswith("compact."):
                results.append((rnd, point, "NOT TESTED", "compaction is not implemented yet (Phase 7)"))
                continue
            if point.startswith("snapshot."):
                results.append((rnd, point, "NOT TESTED",
                                "needs 10,000+ transactions; hard-killed in tests/test_snapshot.py instead"))
                continue
            outcome, detail = targeted_one(run, profile, point, random.Random(master.getrandbits(64)), rnd)
            results.append((rnd, point, outcome, detail))
            print("round {} {:<26} {:<10} {}".format(rnd, point, outcome, detail))
            sys.stdout.flush()
            if outcome == "FAIL":
                return 1
    tested = [r for r in results if r[2] != "NOT TESTED"]
    print("TARGETED PASS {}/{} (not tested: {})".format(
        sum(1 for r in tested if r[2] == "PASS"), len(tested),
        ", ".join(sorted({r[1] for r in results if r[2] == "NOT TESTED"})) or "none"))
    return 0


def targeted_one(run, profile, point, rng, rnd):
    folder = os.path.join(run.dir, "r{}-{}".format(rnd, point))
    os.makedirs(folder)
    db = os.path.join(folder, "db.kv")
    oracle_log = os.path.join(folder, "oracle.log")
    oracle = Oracle(oracle_log, writer_keys(profile))
    sync = Sync(oracle, run)
    seq = [0]

    def request(kind=None):
        while True:
            seq[0] += 1
            rid, data, effects, summary = next_request(rng, profile, 1, seq[0], rnd)
            if kind is None or summary.startswith(kind):
                return rid.decode(), data, effects, summary

    def serve(pause_at=None):
        return Child(run.kvdb, db, sync.on_reply, pause_at=pause_at)

    def prefill(n=4, pause_at=None):
        c = serve(pause_at)
        c.wait_stderr("READY")
        for _ in range(n):
            rid, data, effects, summary = request("put")
            oracle.sent(1, rid, effects, summary)
            c.send(data)
            sync.replies.get(timeout=TIMEOUT)
        return c

    try:
        if point == "create.mid-header":
            c = serve(point)
            c.wait_stderr("PAUSE " + point)
            c.kill()
        elif point.startswith("write."):
            prefill().kill()
            c = serve(point)
            c.wait_stderr("READY")
            rid, data, effects, summary = request("batch" if rnd % 2 == 0 else "put")
            oracle.sent(1, rid, effects, summary)
            c.send(data)
            c.wait_stderr("PAUSE " + point)
            c.kill()
        elif point in ("recovery.replay", "recovery.before-cut", "recovery.after-cut"):
            prefill().kill()
            c = serve("write.mid-body")                    # leave a torn tail on disk
            c.wait_stderr("READY")
            rid, data, effects, summary = request("batch")
            oracle.sent(1, rid, effects, summary)
            c.send(data)
            c.wait_stderr("PAUSE write.mid-body")
            c.kill()
            c = serve(point)                               # now kill recovery itself
            c.wait_stderr("PAUSE " + point)
            c.kill()
        elif point == "recovery.before-trailer":
            c = prefill()
            c.close_stdin()
            c.proc.wait(timeout=TIMEOUT)
            c.kill()
            size = os.path.getsize(db)
            with open(db, "r+b") as f:                     # F4: cut 1 byte off the end by hand
                f.truncate(size - 1)
            c = serve(point)
            c.wait_stderr("PAUSE " + point)
            c.kill()
        elif point == "close":
            c = prefill(pause_at="close")                  # the pause fires inside db.close()
            c.close_stdin()                                # serve closes the database on EOF
            c.wait_stderr("PAUSE close")
            c.kill()
        else:
            return "FAIL", "unknown pause point"
    except ChildGone as exc:
        return "FAIL", "pause point not reached: {}".format(exc)

    pending = oracle.models[1].pending
    dump, problem = read_back(run.kvdb, db)
    problems = list(run.protocol_errors) or ([problem] if problem else oracle.verify(dump))
    applied = None
    if not problems and pending is not None:
        with open(oracle_log, encoding="utf-8") as f:
            applied = "DECIDED {} applied".format(pending[0]) in f.read()
        need = TARGET_EXPECT.get(point)
        if need == "applied" and not applied:
            problems = ["{}: request {} must be present (scenario A4) but is absent".format(point, pending[0])]
    if problems:
        print("FAIL {} round {}:".format(point, rnd))
        for p in problems:
            print("  " + p)
        saved = run.save_failure("targeted-{}-{}".format(rnd, point), problems, None, db, oracle_log)
        print("saved: {}".format(os.path.relpath(saved, REPO)))
        return "FAIL", ""
    if pending is None:
        return "PASS", "no write in flight; all ACKed data intact"
    return "PASS", "unanswered {} -> {}".format(pending[0], "applied" if applied else "absent")


# ---- scale: one million keys, hard kill, time the full open (Phase 6) ----------------

def scale_key(i):
    return b"key%07d" % i


def scale_value(i):
    return b"val%07d" % i


def build_dataset(run, db, keys, build, window=256):
    """Write `keys` distinct keys through serve, then hard-kill it. Returns seconds."""
    acked = [0]
    slots = threading.Semaphore(window)
    done = threading.Event()
    errors = []

    def on_reply(line):
        if line.startswith("ACK "):
            acked[0] += 1
        else:
            errors.append(line)
            done.set()
        slots.release()
        if acked[0] == total:
            done.set()

    per = 10_000 if build == "batches" else 1
    total = (keys + per - 1) // per
    started = time.monotonic()
    child = Child(run.kvdb, db, on_reply)
    child.wait_stderr("READY")
    for n in range(total):
        slots.acquire()
        first = n * per
        if build == "batches":
            lines = [b"b%d batch %d\n" % (n, min(per, keys - first))]
            lines += [b"put %s %s\n" % (scale_key(i), scale_value(i)) for i in range(first, min(keys, first + per))]
            data = b"".join(lines)
        else:
            data = b"p%d put %s %s\n" % (n, scale_key(n), scale_value(n))
        if not child.send(data) or errors:
            break
        if n % 100_000 == 0 and n:
            print("  built {:,} of {:,} requests ({:.0f} s)".format(n, total, time.monotonic() - started))
            sys.stdout.flush()
    done.wait(TIMEOUT * 10)
    child.kill()                                          # hard kill, as the brief says
    if errors or acked[0] != total:
        raise SystemExit("building the dataset failed: {} of {} ACKed; {}".format(acked[0], total, errors[:3]))
    return time.monotonic() - started


def timed_open(run, db, pause_at=None):
    """Start serve and time it until READY. Returns (seconds, READY line). Then hard-kill it."""
    t0 = time.perf_counter()
    child = Child(run.kvdb, db, lambda line: None, pause_at=pause_at)
    ready = child.wait_stderr("READY", timeout=TIMEOUT * 10)
    seconds = time.perf_counter() - t0
    child.kill()
    return seconds, ready


def verify_dataset(run, db, keys, absent_prefix=None):
    """Outside the timed part: dump --hex must hold exactly the dataset."""
    code, out, err = run_cli(run.kvdb, "dump", db, "--hex")
    if code != 0:
        return "dump failed: {}".format(out[:200])
    lines = out.splitlines()
    if len(lines) != keys:
        return "dump has {:,} keys, expected {:,}".format(len(lines), keys)
    for i, line in enumerate(lines):                      # dump is sorted; key%07d sorts in order
        if line != "hex:{} hex:{}".format(scale_key(i).hex(), scale_value(i).hex()):
            return "line {} is {!r}, expected key{:07d}".format(i, line[:80], i)
    return None


def run_scale(args):
    run = Run(args, "scale") if not args.reuse else None
    base = safe_workdir(args.workdir)
    folder = os.path.join(base, "scale-{}-{}".format(args.keys, args.build))
    if run is None:
        class R:
            pass
        run = R()
        run.kvdb, run.seed, run.args = os.path.abspath(args.kvdb), args.seed, args
    os.makedirs(folder, exist_ok=True)
    db = os.path.join(folder, "db.kv")
    print("torture: mode scale keys {:,} build {} runs {} kvdb {}".format(
        args.keys, args.build, args.runs, os.path.relpath(run.kvdb, REPO)))
    if args.reuse and os.path.exists(db):
        print("reusing the dataset in {} ({:,} bytes)".format(os.path.relpath(db, REPO), os.path.getsize(db)))
    else:
        if os.path.exists(db):
            os.remove(db)
        seconds = build_dataset(run, db, args.keys, args.build)
        print("built {:,} keys through serve ({}) in {:.0f} s, then hard-killed serve".format(
            args.keys, "one transaction per key" if args.build == "single" else "batches of 10,000", seconds))
    print("file size: {:,} bytes".format(os.path.getsize(db)))
    times = []
    for r in range(1, args.runs + 1):
        seconds, ready = timed_open(run, db)
        times.append(seconds)
        print("run {}: serve start -> READY {:.3f} s   ({})".format(r, seconds, ready))
        sys.stdout.flush()
        if r in (1, args.runs):
            problem = verify_dataset(run, db, args.keys)
            print("  contents checked outside the timing: {}".format(problem or "all {:,} keys correct".format(args.keys)))
            if problem:
                return 1
    print("slowest of {} runs: {:.3f} s (limit 2 s) -> {}".format(
        len(times), max(times), "PASS" if max(times) < 2.0 else "FAIL"))
    # B6: a 10,000-operation batch killed halfway, then a timed open.
    child = Child(run.kvdb, db, lambda line: None, pause_at="write.mid-body")
    child.wait_stderr("READY", timeout=TIMEOUT * 10)
    batch = [b"t1 batch 10000\n"] + [b"put torn%05d x\n" % i for i in range(10_000)]
    child.send(b"".join(batch))
    child.wait_stderr("PAUSE write.mid-body", timeout=TIMEOUT)
    child.kill()
    size_torn = os.path.getsize(db)
    seconds, ready = timed_open(run, db)
    problem = verify_dataset(run, db, args.keys)
    print("B6: 10,000-op batch killed at write.mid-body ({:,} bytes on disk), open {:.3f} s ({}); {}".format(
        size_torn, seconds, ready, problem or "none of the batch visible, all {:,} keys correct".format(args.keys)))
    ok = max(times) < 2.0 and seconds < 2.0 and problem is None
    return 0 if ok else 1

"""Requests the torture tool sends, and the writer threads that send them.

Uses only the documented serve protocol (CLAUDE.md section 14, README "serve
protocol"). Every value describes itself — writer, sequence, op number and
iteration, then a random tail — so a value that is mixed, torn or invented
can never match what the oracle expects.
"""
import threading

MAX_VALUE = 1_048_576          # README "Limits"


class Profile:
    """The shape of the workload for one mode."""

    def __init__(self, name, writers=3, keys_per_writer=16, p_put=0.5, p_del=0.2,
                 batch_sizes=(2, 10), tail=(0, 40), p_big_value=0.0, delay_ms=(10, 400),
                 p_compact=0.0, prefill=0, max_bytes=0, allowed_errors=(), shared_keys=0,
                 restart_kills=0, restart_ms=(0, 0)):
        self.name = name
        self.writers = writers
        self.keys_per_writer = keys_per_writer
        self.p_put = p_put
        self.p_del = p_del
        self.batch_sizes = batch_sizes
        self.tail = tail
        self.p_big_value = p_big_value
        self.delay_ms = delay_ms
        self.p_compact = p_compact          # share of requests that are "compact"
        self.prefill = prefill              # keys written by writer 0 before the first kill
        self.max_bytes = max_bytes          # size cap given at creation (disk-full)
        self.allowed_errors = allowed_errors  # ERR reasons that are allowed (prefixes)
        self.shared_keys = shared_keys      # overlap: all writers use these keys
        self.restart_kills = restart_kills  # recovery-crash: extra kills of a restarted serve
        self.restart_ms = restart_ms        # ...after this many ms from its start


PROFILES = {
    "default": Profile("default"),
    "large-values": Profile("large-values", p_big_value=0.35, delay_ms=(20, 600)),
    "large-batches": Profile("large-batches", p_put=0.2, p_del=0.1, batch_sizes=(1000, 10_000),
                             keys_per_writer=64, delay_ms=(20, 800)),
    "compaction": Profile("compaction", keys_per_writer=800, p_put=0.45, p_del=0.15, p_compact=0.12,
                          batch_sizes=(10, 200), delay_ms=(50, 700)),
    "recovery-crash": Profile("recovery-crash", prefill=100_000, restart_kills=3, restart_ms=(60, 220)),
    "disk-full": Profile("disk-full", max_bytes=4 * 1024, tail=(0, 220), allowed_errors=("SizeCapReached",)),
    "overlap": Profile("overlap", shared_keys=8, p_put=0.7, p_del=0.3),
}


def key_of(wid, j):
    return b"w%dk%d" % (wid, j)


def writer_keys(profile):
    keys = {w: [key_of(w, j) for j in range(profile.keys_per_writer)]
            for w in range(1, profile.writers + 1)}
    if profile.prefill:
        keys[0] = [b"p%06d" % i for i in range(profile.prefill)]   # writer 0: the prefill
    return keys


def shared_key(j):
    return b"s%d" % j


def make_value(rng, profile, wid, seq, op, iteration):
    head = b"w%d.s%d.o%d.i%d." % (wid, seq, op, iteration)
    if profile.p_big_value and rng.random() < profile.p_big_value:
        size = MAX_VALUE if rng.random() < 0.5 else rng.randint(MAX_VALUE // 2, MAX_VALUE)
    else:
        size = len(head) + rng.randint(*profile.tail)
    fill = size - len(head)
    return head + rng.randbytes((fill + 1) // 2).hex().encode("ascii")[:fill]


def next_request(rng, profile, wid, seq, iteration):
    """(rid, request bytes, effects, summary) for writer wid's request number seq."""
    rid = b"w%d-%d" % (wid, seq)
    keys = profile.keys_per_writer
    if profile.p_compact and rng.random() < profile.p_compact:
        return rid, b"%s compact\n" % rid, [], "compact"
    if profile.shared_keys:                                        # overlap: single ops on shared keys
        k = shared_key(rng.randrange(profile.shared_keys))
        if rng.random() < profile.p_put / (profile.p_put + profile.p_del):
            v = make_value(rng, profile, wid, seq, 0, iteration)
            return rid, b"%s put %s %s\n" % (rid, k, v), [("put", k, v)], "put {}".format(k.decode())
        return rid, b"%s del %s\n" % (rid, k), [("del", k, None)], "del {}".format(k.decode())
    r = rng.random()
    if r < profile.p_put:
        k = key_of(wid, rng.randrange(keys))
        v = make_value(rng, profile, wid, seq, 0, iteration)
        return rid, b"%s put %s %s\n" % (rid, k, v), [("put", k, v)], "put {}".format(k.decode())
    if r < profile.p_put + profile.p_del:
        k = key_of(wid, rng.randrange(keys))
        return rid, b"%s del %s\n" % (rid, k), [("del", k, None)], "del {}".format(k.decode())
    n = rng.randint(*profile.batch_sizes)
    lines, effects = [b"%s batch %d\n" % (rid, n)], []
    for op in range(n):
        k = key_of(wid, rng.randrange(keys))
        if rng.random() < 0.8:
            v = make_value(rng, profile, wid, seq, op, iteration)
            lines.append(b"put %s %s\n" % (k, v))
            effects.append(("put", k, v))
        else:
            lines.append(b"del %s\n" % k)
            effects.append(("del", k, None))
    return rid, b"".join(lines), effects, "batch of {}".format(n)


class Writer(threading.Thread):
    """Sends one request, waits for its reply, sends the next — never two in flight."""

    def __init__(self, wid, rng, profile, oracle, child, stop, iteration, seq_start, trace, kinds=None):
        super().__init__(daemon=True, name="writer-%d" % wid)
        self.wid, self.rng, self.profile = wid, rng, profile
        self.oracle, self.child, self.stop = oracle, child, stop
        self.iteration, self.seq, self.trace = iteration, seq_start, trace
        self.replied = threading.Event()
        self.sent_count = 0
        self.kinds = kinds                  # request id -> type, for the coverage verdict
        self.error = None                   # a crash of this thread, as a traceback: a FAIL

    def run(self):
        try:
            self._loop()
        except BaseException:               # never die silently: the main thread reports it
            import traceback
            self.error = "writer {} thread crashed:\n{}".format(self.wid, traceback.format_exc())

    def _loop(self):
        while not self.stop.is_set():
            self.seq += 1
            rid, request, effects, summary = next_request(self.rng, self.profile, self.wid, self.seq, self.iteration)
            self.replied.clear()
            self.oracle.sent(self.wid, rid.decode(), effects, summary)   # logged BEFORE it is sent
            if self.kinds is not None:
                self.kinds[rid.decode()] = summary.split()[0]
            self.trace("SEND {} {} ({} bytes)".format(rid.decode(), summary, len(request)))
            if not self.child.send(request):
                return                                              # the engine is gone
            self.sent_count += 1
            while not self.replied.wait(0.02):
                if self.stop.is_set():
                    return                                          # killed while waiting: stays in flight

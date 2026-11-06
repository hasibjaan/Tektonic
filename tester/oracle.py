"""The oracle: the tester's own record of what was promised.

It never reads the database to decide what is expected. It learns only from
what the tester SENT and what the engine REPLIED (ACK / ERR), and it writes
both to its own log file, flushed line by line.

Model (Phase 5): every writer owns a separate set of keys and has at most one
request in flight. So for each writer the truth after a kill is one of two
states: everything it got an ACK for, or that plus its one unanswered request
(applied as a whole — a batch is one request). A dump must match one of the
two for every writer, and contain no key that nobody wrote.

After a pass the oracle only NARROWS its own possibilities: it records which of
its two candidate states matched. It never copies a value from the dump.
"""
import threading


def apply(state, effects):
    """A new dict: state with effects [("put"|"del", key, value)] applied in order."""
    new = dict(state)
    for kind, key, value in effects:
        if kind == "put":
            new[key] = value
        else:
            new.pop(key, None)
    return new


def short(value, limit=60):
    if value is None:
        return "<absent>"
    text = value[:limit].decode("ascii", "replace")
    return text + ("…(%d bytes)" % len(value) if len(value) > limit else "")


class WriterModel:
    def __init__(self, wid, keys):
        self.wid = wid
        self.keys = frozenset(keys)
        self.state = {}            # what the ACKs promise: key -> value
        self.last_op = {}          # key -> id of the last ACKed request that touched it
        self.pending = None        # (rid, effects) sent but not answered, or None


class Oracle:
    def __init__(self, log_path, writer_keys):
        self.log = open(log_path, "a", encoding="utf-8")
        self.models = {wid: WriterModel(wid, keys) for wid, keys in writer_keys.items()}
        self.all_keys = frozenset().union(*(m.keys for m in self.models.values()))
        self.lock = threading.Lock()
        self.acks = 0

    def note(self, text):
        self.log.write(text + "\n")
        self.log.flush()

    # ---- what happened during a run (called from the writer and reader threads) ----

    def sent(self, wid, rid, effects, summary):
        """Called BEFORE the request is written to the engine's stdin."""
        with self.lock:
            m = self.models[wid]
            if m.pending is not None:
                raise AssertionError("writer {} already has {} in flight".format(wid, m.pending[0]))
            m.pending = (rid, effects)
            self.note("SEND {} {}".format(rid, summary))

    def acked(self, wid, rid):
        with self.lock:
            m = self.models[wid]
            if m.pending is None or m.pending[0] != rid:
                self.note("UNEXPECTED-ACK {}".format(rid))
                raise AssertionError("ACK for {} which is not in flight".format(rid))
            m.state = apply(m.state, m.pending[1])
            for _, key, _ in m.pending[1]:
                m.last_op[key] = rid
            m.pending = None
            self.acks += 1
            self.note("ACK {}".format(rid))

    def erred(self, wid, rid, reason):
        with self.lock:
            m = self.models[wid]
            self.note("ERR {} {}".format(rid, reason))
            if m.pending is None or m.pending[0] != rid:
                raise AssertionError("ERR for {} which is not in flight".format(rid))
            if not reason.startswith("DatabaseFailed"):
                m.pending = None   # ERR = nothing changed: it must stay absent
            # DatabaseFailed: README says it may still be present, so it stays "either"

    def in_flight(self):
        with self.lock:
            return sum(1 for m in self.models.values() if m.pending is not None)

    def expected_keys(self):
        with self.lock:
            return sum(len(m.state) for m in self.models.values())

    # ---- the comparison after a kill ---------------------------------------------

    def verify(self, dump):
        """Compare the WHOLE dump. Returns a list of problem lines (empty = PASS).

        On a pass, each writer's unanswered request is decided (applied or not)
        by which of the oracle's own two states matched, and the oracle's state
        becomes that state.
        """
        problems = []
        with self.lock:
            for key in sorted(dump):
                if key not in self.all_keys:
                    problems.append("key {} = {} was never written by any writer".format(
                        short(key), short(dump[key])))
            decisions = []
            for wid, m in sorted(self.models.items()):
                found = {k: v for k, v in dump.items() if k in m.keys}
                options = [("without", None, m.state)]
                if m.pending is not None:
                    options.append(("with", m.pending[0], apply(m.state, m.pending[1])))
                matched = [o for o in options if o[2] == found]
                if not matched:
                    problems.extend(self._explain(m, found, options))
                else:
                    decisions.append((m, matched))
            if problems:
                return problems
            for m, matched in decisions:
                if m.pending is not None:
                    rid = m.pending[0]
                    if any(name == "with" for name, _, _ in matched) and len(matched) == 1:
                        m.state = matched[0][2]
                        for _, key, _ in m.pending[1]:
                            m.last_op[key] = rid
                        self.note("DECIDED {} applied (no ACK, but present as a whole)".format(rid))
                    elif len(matched) == 2:
                        self.note("DECIDED {} either (both states are equal)".format(rid))
                    else:
                        self.note("DECIDED {} absent".format(rid))
                    m.pending = None
            self.note("PASS")
        return problems

    def _explain(self, m, found, options):
        lines = ["writer {}: the dump matches none of the {} allowed state(s)".format(m.wid, len(options))]
        if m.pending is not None:
            lines.append("  unanswered request: {} (allowed: fully applied or fully absent)".format(m.pending[0]))
        else:
            lines.append("  no request in flight: the dump must equal the ACKed state exactly")
        base = options[0][2]
        with_pending = options[1][2] if len(options) > 1 else None
        for key in sorted(set(base) | set(found) | set(with_pending or {})):
            exp, got = base.get(key), found.get(key)
            alt = with_pending.get(key) if with_pending is not None else exp
            if got != exp and got != alt:
                ids = [i for i in (m.last_op.get(key), m.pending[0] if m.pending else None) if i]
                lines.append("  key {}: expected {}{} but found {}  (operation ids: {})".format(
                    short(key), short(exp),
                    "" if alt == exp else " or " + short(alt), short(got), ", ".join(ids) or "none"))
        if len(lines) == 2 and with_pending is not None:
            lines.append("  every key matches one of the two states, but not the SAME one: "
                         "request {} is partly applied".format(m.pending[0]))
        return lines


class OverlapOracle:
    """Oracle for the overlapping-key workload (Phase 7): every writer uses the same
    small key set; each request is ONE put or delete on one key; each writer has at
    most one request in flight.

    The tester's own clock ticks when a request is sent and when its reply arrives.
    Request B is definitely later than A if B was sent after A's ACK arrived. For
    each key, the final value must be the effect of a request that no later ACKed
    request could have replaced:
      - an ACKed request A is a candidate if no other ACKed request on that key was
        sent after A's ACK;
      - an unanswered request is a candidate (it may have been applied at any time
        after it was sent) — if chosen, it was applied;
      - the value from before this iteration is a candidate if no request on the key
        was ACKed in this iteration.
    Checking key by key is enough because every request touches one key
    (linearizability is a local property). Values are unique per request, so a
    match identifies the request. After a pass the oracle keeps that candidate's
    value — its own copy, never the dump's.
    """

    def __init__(self, log_path, writer_ids, keys):
        self.log = open(log_path, "a", encoding="utf-8")
        self.models = {w: None for w in writer_ids}      # the writer ids the dispatcher accepts
        self.all_keys = frozenset(keys)
        self.state = {}                                  # decided value per key before this iteration
        self.lock = threading.Lock()
        self.acks = 0
        self.clock = 0
        self.ops = {}                                    # rid -> request record, this iteration
        self.pending = {}                                # wid -> rid in flight

    def note(self, text):
        self.log.write(text + "\n")
        self.log.flush()

    def sent(self, wid, rid, effects, summary):
        with self.lock:
            if self.pending.get(wid) is not None:
                raise AssertionError("writer {} already has {} in flight".format(wid, self.pending[wid]))
            kind, key, value = effects[0]
            self.clock += 1
            self.ops[rid] = {"rid": rid, "kind": kind, "key": key, "value": value,
                             "sent": self.clock, "acked": None, "err": None}
            self.pending[wid] = rid
            self.note("SEND {} {} t={}".format(rid, summary, self.clock))

    def acked(self, wid, rid):
        with self.lock:
            if self.pending.get(wid) != rid:
                raise AssertionError("ACK for {} which is not in flight".format(rid))
            self.clock += 1
            self.ops[rid]["acked"] = self.clock
            self.pending[wid] = None
            self.acks += 1
            self.note("ACK {} t={}".format(rid, self.clock))

    def erred(self, wid, rid, reason):
        with self.lock:
            if self.pending.get(wid) != rid:
                raise AssertionError("ERR for {} which is not in flight".format(rid))
            self.ops[rid]["err"] = reason
            self.pending[wid] = None
            self.note("ERR {} {}".format(rid, reason))

    def in_flight(self):
        with self.lock:
            return sum(1 for r in self.pending.values() if r is not None)

    def verify(self, dump):
        problems = []
        with self.lock:
            for key in sorted(dump):
                if key not in self.all_keys:
                    problems.append("key {} = {} was never written by any writer".format(short(key), short(dump[key])))
            decided = {}
            for key in sorted(self.all_keys):
                ops = [o for o in self.ops.values() if o["key"] == key
                       and (o["err"] is None or o["err"].startswith("DatabaseFailed"))]
                acked = [o for o in ops if o["acked"] is not None]
                candidates = []
                for o in ops:
                    if o["acked"] is None or not any(o2 is not o and o2["sent"] > o["acked"] for o2 in acked):
                        candidates.append((o["rid"], o["value"] if o["kind"] == "put" else None))
                if not acked:
                    candidates.append(("<before this iteration>", self.state.get(key)))
                found = dump.get(key)
                match = [rid for rid, value in candidates if value == found]
                if not match:
                    problems.append("key {}: found {} but the only possible final values are: {}".format(
                        short(key), short(found),
                        "; ".join("{} -> {}".format(rid, short(v)) for rid, v in candidates) or "none"))
                else:
                    decided[key] = next(v for rid, v in candidates if v == found)
                    self.note("DECIDED {} = from {}".format(key.decode("ascii", "replace"), " or ".join(match)))
            if problems:
                return problems
            self.state = {k: v for k, v in decided.items() if v is not None}
            self.ops = {}
            self.pending = {w: None for w in self.pending}
            self.note("PASS")
        return problems

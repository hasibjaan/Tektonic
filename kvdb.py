"""kvdb.py — the command-line wrapper around the engine (CLAUDE.md section 14).

    python3 kvdb.py open  <path> [--max-bytes N]
    python3 kvdb.py put   <path> <key> <value>
    python3 kvdb.py get   <path> <key>
    python3 kvdb.py del   <path> <key>
    python3 kvdb.py batch <path> <file>        one "put <k> <v>" or "del <k>" per line
    python3 kvdb.py dump  <path> [--hex]
    python3 kvdb.py check <path>               read-only
    python3 kvdb.py serve <path>               requests on stdin, results on stdout

Keys and values: plain text (no spaces, not starting with "hex:" or "file:"),
"hex:<hex digits>" for any bytes (the empty value is "hex:"), or "file:<path>".

Exit codes: 0 success (also NOTFOUND, and check OK) · 1 check found a problem ·
2 usage error · 3 ERR (the engine refused the request, or the file is not
ours, corrupt or failed).

This is the only place where text is turned into bytes; the engine sees bytes only.
"""
import argparse
import concurrent.futures
import os
import re
import sys
import threading

from src.kv import KVError, check, layout, open_db

EXIT_OK, EXIT_CHECK_FAILED, EXIT_USAGE, EXIT_ERR = 0, 1, 2, 3
_HEX = re.compile(rb"\A(?:[0-9a-fA-F]{2})*\Z")
_PRINTABLE = re.compile(rb"\A[\x21-\x7e]+\Z")          # visible ASCII, no space


class BadToken(KVError):
    """A key, value or request line that cannot be turned into bytes."""


# ---- text <-> bytes -------------------------------------------------------------

def parse_token(raw, what="value"):
    """bytes from one token: plain, hex:<digits> or file:<path>. raw is bytes."""
    if raw.startswith(b"hex:"):
        digits = raw[4:]
        if not _HEX.match(digits):
            raise BadToken("{} {!r}: hex: needs an even number of hex digits".format(what, raw[:40]))
        return bytes.fromhex(digits.decode("ascii"))
    if raw.startswith(b"file:"):
        path = os.fsdecode(raw[5:])
        limit = layout.MAX_KEY if what == "key" else layout.MAX_VALUE
        try:
            size = os.path.getsize(path)
            if size > limit:
                raise BadToken("{} file {} has {} bytes, the limit is {}".format(what, path, size, limit))
            with open(path, "rb") as f:
                return f.read()
        except OSError as exc:
            raise BadToken("{} file {}: {}".format(what, path, exc.strerror or exc))
    if not raw:
        raise BadToken("empty {}: write the empty value as hex:".format(what))
    if b" " in raw:
        raise BadToken("{} {!r} contains a space: use hex:".format(what, raw[:40]))
    return raw


def arg_token(text, what):
    """A token from the command line (str) as bytes, exactly as the shell passed it."""
    return parse_token(os.fsencode(text), what)


def format_token(data):
    """Plain text only when that is unambiguous, otherwise hex:."""
    if (_PRINTABLE.match(data) and not data.startswith((b"hex:", b"file:"))
            and data != b"NOTFOUND"):
        return data.decode("ascii")
    return "hex:" + data.hex()


def one_line(exc):
    name = type(exc).__name__
    text = str(exc).replace("\n", " ").replace("\r", " ")
    return "{}: {}".format(name, text) if text else name


# ---- the eight commands -----------------------------------------------------------

def cmd_open(args):
    db, r = open_db(args.path, args.max_bytes)
    db.close()
    print("keys={} recovery_ms={} bytes_cut={} trailer_restored={}".format(
        r.keys, round(r.seconds * 1000), r.bytes_cut, "yes" if r.trailer_restored else "no"))
    return EXIT_OK


def cmd_put(args):
    key, value = arg_token(args.key, "key"), arg_token(args.value, "value")
    with open_db(args.path)[0] as db:
        db.put(key, value)
    print("OK")
    return EXIT_OK


def cmd_get(args):
    key = arg_token(args.key, "key")
    with open_db(args.path)[0] as db:
        value = db.get(key)
    print("NOTFOUND" if value is None else format_token(value))
    return EXIT_OK


def cmd_del(args):
    key = arg_token(args.key, "key")
    with open_db(args.path)[0] as db:
        db.delete(key)
    print("OK")
    return EXIT_OK


def parse_batch_line(line, number):
    """b"put <k> <v>" or b"del <k>" -> (op, key, value)."""
    parts = line.split(b" ")
    if len(parts) == 3 and parts[0] == b"put":
        return ("put", parse_token(parts[1], "key"), parse_token(parts[2], "value"))
    if len(parts) == 2 and parts[0] == b"del":
        return ("del", parse_token(parts[1], "key"), None)
    raise BadToken("line {}: expected 'put <key> <value>' or 'del <key>', got {!r}".format(number, line[:60]))


def cmd_batch(args):
    with open(args.file, "rb") as f:
        lines = f.read().split(b"\n")
    ops = []
    for number, line in enumerate(lines, 1):
        line = line.rstrip(b"\r")
        if line:                                       # blank lines are skipped
            ops.append(parse_batch_line(line, number))
    with open_db(args.path)[0] as db:
        db.batch(ops)
    print("OK")
    return EXIT_OK


def cmd_dump(args):
    out = sys.stdout
    with open_db(args.path)[0] as db:
        for key in db.keys():                          # sorted by raw key bytes
            value = db.get(key)
            if args.hex:
                out.write("hex:{} hex:{}\n".format(key.hex(), value.hex()))
            else:
                out.write("{} {}\n".format(format_token(key), format_token(value)))
    return EXIT_OK


def cmd_check(args):
    ok, reason = check(args.path)                      # read-only, never repairs
    print(reason)
    return EXIT_OK if ok else EXIT_CHECK_FAILED


# ---- serve ------------------------------------------------------------------------------

class Server:
    """One thread reads complete requests from stdin; a pool calls the engine."""

    def __init__(self, db, out):
        self.db = db
        self.out = out
        self.out_lock = threading.Lock()

    def reply(self, *parts):
        line = b" ".join(p if isinstance(p, bytes) else p.encode("utf-8") for p in parts) + b"\n"
        with self.out_lock:                            # whole lines only, flushed at once
            self.out.write(line)
            self.out.flush()

    def run_request(self, rid, kind, payload):
        try:
            if kind == b"put":
                self.db.put(*payload)
                self.reply(b"ACK", rid)                # only after put returned: durable
            elif kind == b"del":
                self.db.delete(payload)
                self.reply(b"ACK", rid)
            elif kind == b"batch":
                self.db.batch(payload)
                self.reply(b"ACK", rid)
            elif kind == b"get":
                value = self.db.get(payload)
                if value is None:
                    self.reply(b"NOTFOUND", rid)
                else:
                    self.reply(b"VAL", rid, format_token(value))
        except BaseException as exc:                   # ERR: the engine changed nothing
            self.reply(b"ERR", rid, one_line(exc))

    def serve(self, inp, workers=4):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            while True:
                line = inp.readline()
                if not line:
                    break                              # stdin closed: finish and exit
                line = line.rstrip(b"\n").rstrip(b"\r")
                if not line:
                    continue
                parts = line.split(b" ")
                rid = parts[0] or b"-"
                try:
                    kind, payload = self.parse(parts, inp)
                except BadToken as exc:
                    self.reply(b"ERR", rid, one_line(exc))
                    continue
                pool.submit(self.run_request, rid, kind, payload)
        finally:
            pool.shutdown(wait=True)

    def parse(self, parts, inp):
        if len(parts) < 2:
            raise BadToken("expected '<id> <command> ...'")
        cmd = parts[1]
        if cmd == b"put" and len(parts) == 4:
            return b"put", (parse_token(parts[2], "key"), parse_token(parts[3], "value"))
        if cmd in (b"del", b"get") and len(parts) == 3:
            return cmd, parse_token(parts[2], "key")
        if cmd == b"batch" and len(parts) == 3:
            if not parts[2].isdigit() or not 1 <= int(parts[2]) <= layout.MAX_OPS:
                # The count is unusable, so the following lines cannot be skipped safely:
                # they will be read as requests of their own (and most will get ERR).
                raise BadToken("batch needs a count from 1 to {}".format(layout.MAX_OPS))
            n = int(parts[2])
            lines = []
            for _ in range(n):                         # read the WHOLE batch before deciding
                more = inp.readline()
                if not more:
                    raise BadToken("stdin ended inside a batch")
                lines.append(more.rstrip(b"\n").rstrip(b"\r"))
            return b"batch", [parse_batch_line(l, i) for i, l in enumerate(lines, 1)]
        raise BadToken("unknown or malformed request")


def cmd_serve(args):
    err = sys.stderr
    err.write("PID {}\n".format(os.getpid()))
    err.flush()
    db, r = open_db(args.path)                         # recovery runs here (pause points too)
    err.write("READY keys={} recovery_ms={} bytes_cut={} trailer_restored={}\n".format(
        r.keys, round(r.seconds * 1000), r.bytes_cut, "yes" if r.trailer_restored else "no"))
    err.flush()
    try:
        Server(db, sys.stdout.buffer).serve(sys.stdin.buffer)
    finally:
        db.close()
    return EXIT_OK


# ---- argument parsing -------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="kvdb", description="Crash-safe key-value store.")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, func, *fields, help=None):
        s = sub.add_parser(name, help=help)
        s.add_argument("path")
        for f in fields:
            s.add_argument(f)
        s.set_defaults(func=func)
        return s

    o = add("open", cmd_open, help="open or create, run recovery, print key count and recovery time")
    o.add_argument("--max-bytes", type=int, default=0, help="size cap for a NEW file (0 = none)")
    add("put", cmd_put, "key", "value", help="put one key")
    add("get", cmd_get, "key", help="print the value, or NOTFOUND")
    add("del", cmd_del, "key", help="delete one key")
    add("batch", cmd_batch, "file", help="apply a batch file atomically")
    d = add("dump", cmd_dump, help="print every key and value, sorted by key")
    d.add_argument("--hex", action="store_true", help="print both fields as hex:")
    add("check", cmd_check, help="verify the file without changing it")
    add("serve", cmd_serve, help="long-running mode: requests on stdin, ACK on stdout")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (KVError, OSError) as exc:
        print("ERR " + one_line(exc))
        sys.stdout.flush()
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())

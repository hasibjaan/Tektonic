"""FAKE engines for proving the torture tool works. None of them stores anything.

    python3 torture.py --iterations 5 --seed 1 --kvdb tester/fake_kvdb.py

FAKE_KVDB_MODE chooses how serve lies (default "ack"):
    ack         ACK every put, del, batch and compact (the classic lie)
    silent      never reply at all: nothing ACKed, nothing stored
    garbage     reply "GARBAGE OUTPUT" to every request
    bad-err-id  reply "ERR <id>x nope" (an id that was never sent)

It speaks the same command line and serve protocol as kvdb.py, but it lies:
every put, del and batch is acknowledged, nothing is ever written, open reports
an empty database and dump prints nothing. A correct tester must FAIL on it.
"""
import os
import sys


def serve():
    sys.stderr.write("PID {}\nREADY keys=0 recovery_ms=0 bytes_cut=0 trailer_restored=no\n".format(os.getpid()))
    sys.stderr.flush()
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while True:
        line = inp.readline()
        if not line:
            return 0
        parts = line.rstrip(b"\n").split(b" ")
        if len(parts) >= 3 and parts[1] == b"batch":
            for _ in range(int(parts[2])):
                inp.readline()                       # swallow the batch lines
        mode = os.environ.get("FAKE_KVDB_MODE", "ack")
        if mode == "silent":
            continue                                 # read everything, answer nothing
        if mode == "garbage":
            out.write(b"GARBAGE OUTPUT\n")
        elif mode == "bad-err-id":
            out.write(b"ERR " + parts[0] + b"x nope\n")
        elif len(parts) >= 2 and parts[1] == b"get":
            out.write(b"NOTFOUND " + parts[0] + b"\n")
        elif len(parts) >= 2:
            out.write(b"ACK " + parts[0] + b"\n")    # the lie: "durable"
        out.flush()


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "serve":
        return serve()
    if command == "open":
        print("keys=0 recovery_ms=0 bytes_cut=0 trailer_restored=no")
    elif command == "check":
        print("OK")
    elif command == "dump":
        pass                                         # an empty database
    else:
        print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

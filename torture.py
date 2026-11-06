"""torture.py — hard-kill the engine at random moments and check every promise.

    python3 torture.py --iterations 500 --seed 12345
    python3 torture.py --iterations 100 --seed 999 --mode large-values
    python3 torture.py --iterations 3 --seed 5 --mode targeted
    python3 torture.py --seed 1 --mode scale --keys 1000000

The engine is used ONLY as a separate process, `kvdb.py serve`, started with
the same Python (sys.executable). Kills are Popen.kill() — SIGKILL on macOS and
Linux — after a random delay in milliseconds from random.Random(seed). This
program never imports the engine (tools/check_imports.py proves it).

Exit codes: 0 every iteration passed · 1 a failure (details saved under
evidence/failures/) · 2 usage error or a mode that is not implemented yet.
"""
import argparse
import os
import sys

from tester import modes

REPO = os.path.dirname(os.path.abspath(__file__))
RANDOM_MODES = ("default", "large-values", "large-batches")
LATER_MODES = {
    "recovery-crash": "Phase 7 (kill while open is running, repeatedly)",
    "compaction": "Phase 7 (the engine has no compaction yet)",
    "disk-full": "Phase 7 (small --max-bytes, clean errors)",
}


def build_parser():
    p = argparse.ArgumentParser(prog="torture", description="Hard-kill kvdb serve and verify every promise.")
    p.add_argument("--iterations", type=int, default=500, help="kills to run (targeted: rounds) [500]")
    p.add_argument("--seed", type=int, required=True, help="REQUIRED: the seed for every random choice")
    p.add_argument("--mode", default="default",
                   choices=list(RANDOM_MODES) + list(LATER_MODES) + ["targeted", "scale"])
    p.add_argument("--writers", type=int, default=3, help="concurrent writer threads, at least 2 [3]")
    p.add_argument("--kvdb", default=os.path.join(REPO, "kvdb.py"), help="path to kvdb.py")
    p.add_argument("--workdir", default=os.path.join(REPO, ".torture-work"),
                   help="work folder on the real disk, never /tmp [.torture-work]")
    p.add_argument("--keys", type=int, default=1_000_000, help="scale mode: distinct keys [1000000]")
    p.add_argument("--build", choices=["single", "batches"], default="single",
                   help="scale mode: one transaction per key, or batches of 10,000 [single]")
    p.add_argument("--runs", type=int, default=5, help="scale mode: timed opens [5]")
    p.add_argument("--reuse", action="store_true", help="scale mode: reuse the dataset already built")
    p.add_argument("--failures-dir", default=os.path.join(REPO, "evidence", "failures"),
                   help="where the first failure is saved [evidence/failures]")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.iterations < 1:
        print("--iterations must be at least 1", file=sys.stderr)
        return 2
    if args.writers < 2 and args.mode != "targeted":
        print("--writers must be at least 2", file=sys.stderr)
        return 2
    if args.mode in LATER_MODES:
        print("mode {} is not implemented yet: {}".format(args.mode, LATER_MODES[args.mode]), file=sys.stderr)
        return 2
    if args.mode == "targeted":
        return modes.run_targeted(args)
    if args.mode == "scale":
        return modes.run_scale(args)
    return modes.run_random(args)


if __name__ == "__main__":
    sys.exit(main())

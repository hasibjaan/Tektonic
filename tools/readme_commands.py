"""Keep README "Commands" honest: every example there is a real run.

Usage: python3 tools/readme_commands.py           compare README with a fresh run (exit 1 on a difference)
       python3 tools/readme_commands.py --write   run the commands and write their REAL output into README

The block between <!-- commands:begin --> and <!-- commands:end --> in README.md
holds lines "$ <command>" followed by output. Each command runs with /bin/sh in
a fresh folder in .torture-work/, in order, with stderr merged into stdout, and
"./kvdb" pointing at the launcher in the repository root. Timings
(recovery_ms=<n>) are not compared, because they change from run to run.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
BEGIN, END = "<!-- commands:begin -->", "<!-- commands:end -->"


def read_block():
    with open(README, encoding="utf-8") as f:
        text = f.read()
    head, rest = text.split(BEGIN, 1)
    block, tail = rest.split(END, 1)
    return text, head, block, tail


def parse(block):
    """[(command, expected_output_lines)] from the console block."""
    steps = []
    for line in block.splitlines():
        if line.startswith("$ "):
            steps.append((line[2:], []))
        elif steps and not line.startswith("```"):
            steps[-1][1].append(line)
    return [(cmd, "\n".join(out).rstrip("\n")) for cmd, out in steps]


def run_all(commands):
    os.makedirs(os.path.join(ROOT, ".torture-work"), exist_ok=True)
    work = tempfile.mkdtemp(prefix="readme-", dir=os.path.join(ROOT, ".torture-work"))
    launcher = os.path.join(ROOT, "kvdb")
    env = dict(os.environ)
    env.pop("KVDB_PAUSE_AT", None)
    outputs = []
    try:
        for cmd in commands:
            real = re.sub(r"(^|[\s|;(])\./kvdb\b", lambda m: m.group(1) + "'" + launcher + "'", cmd)
            r = subprocess.run(["/bin/sh", "-c", real], cwd=work, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            outputs.append(r.stdout.decode("utf-8", "replace").rstrip("\n"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return outputs


def normalise(text):
    return re.sub(r"recovery_ms=\d+", "recovery_ms=<n>", text)


def compare():
    _, _, block, _ = read_block()
    steps = parse(block)
    outputs = run_all([c for c, _ in steps])
    problems = []
    for (cmd, expected), real in zip(steps, outputs):
        if normalise(expected) != normalise(real):
            problems.append("$ {}\n  README: {!r}\n  real:   {!r}".format(cmd, expected, real))
    return len(steps), problems


def write():
    text, head, block, tail = read_block()
    steps = parse(block)
    outputs = run_all([c for c, _ in steps])
    lines = ["", "```console"]
    for (cmd, _), out in zip(steps, outputs):
        lines.append("$ " + cmd)
        if out:
            lines.append(out)
    lines += ["```", ""]
    with open(README, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + BEGIN + "\n".join(lines) + END + tail)
    return len(steps)


def main():
    if "--write" in sys.argv:
        print("wrote the real output of {} commands into README.md".format(write()))
        return 0
    n, problems = compare()
    for p in problems:
        print(p)
    print("{} commands, {} differences".format(n, len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

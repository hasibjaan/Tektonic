# evidence/

Raw outputs from real runs. Every claim in README.md, ARCHITECTURE.md and BREAKING.md that says something was measured or verified points to a file here.

What goes here:

- **Hex dumps** of real database files, with every field labelled (for example `hexdump-format.txt`), so the format in README.md can be checked byte for byte.
- **Benchmarks**: recovery timings, sync speed, file sizes, always with the machine, OS, Python version, disk, filesystem and how the dataset was built.
- **Torture runs**: the exact command, seed, mode and the full real output.
- **Failing seeds**: everything needed to replay a failure — seed, iteration, mode, the database copy, the oracle log, the operation trace — in `failures/seed-<s>-iter-<i>/`.
- **Platform probes**: real output of commands that filled CLAUDE.md section 5 (`platform-probe.txt`).

Rules:

- Outputs are pasted as produced. Never edited, never invented.
- Failures are kept, never deleted. They are evidence too.
- Each file says which command produced it and when.

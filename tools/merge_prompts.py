"""Phase 9: merge every prompt log in prompts/ into PROMPTS.md, in time order.

Usage: python3 tools/merge_prompts.py          (dry run: only counts)
       python3 tools/merge_prompts.py --write  (appends the missing entries)

Rules (CLAUDE.md section 4: the prompt log is untouchable, except this merge):
- Every entry is copied BYTE FOR BYTE: its heading line, its tilde fence, the
  prompt exactly as logged, and the closing fence.
- APPEND-ONLY: nothing already in PROMPTS.md is changed, reordered or removed.
  An entry already present (same bytes) is not copied again, so running this
  again later only adds the prompts logged since.
- The files in prompts/ are kept unchanged.
- It reports the number of entries in prompts/ and the number found in
  PROMPTS.md afterwards; they must be equal, or it exits 1.

An entry is: a line starting "### <YYYY-MM-DD HH:MM[:SS]> · <tool> · <name>",
then a fence line of five or more "~", then the prompt, then the same fence line.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_MD = os.path.join(ROOT, "PROMPTS.md")
SECTION = "\n\n## Merged prompt log (Phase 9)\n\nEvery entry below is copied byte for byte from the files in `prompts/` by `tools/merge_prompts.py`, in time order. The files in `prompts/` are kept unchanged.\n\n"
HEADING = re.compile(r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?) · ")


def entries_in(path):
    """[(timestamp, source, entry text)] in file order."""
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    found, i = [], 0
    while i < len(lines):
        m = HEADING.match(lines[i])
        if m and i + 1 < len(lines) and re.fullmatch(r"~{5,}", lines[i + 1]):
            fence = lines[i + 1]
            j = i + 2
            while j < len(lines) and lines[j] != fence:
                j += 1
            if j == len(lines):
                raise SystemExit("{}: entry at line {} has no closing fence".format(path, i + 1))
            found.append((m.group(1), os.path.relpath(path, ROOT), "\n".join(lines[i:j + 1]) + "\n"))
            i = j + 1
        else:
            i += 1
    return found


def main():
    write = "--write" in sys.argv
    all_entries = []
    for path in sorted(glob.glob(os.path.join(ROOT, "prompts", "*.md"))):
        all_entries.extend(entries_in(path))
    # time order; entries with the same timestamp keep their file order (sort is stable)
    all_entries.sort(key=lambda e: e[0])
    with open(PROMPTS_MD, encoding="utf-8", newline="") as f:
        current = f.read()
    missing = [e for e in all_entries if e[2] not in current]
    print("entries in prompts/: {} (from {} file(s))".format(
        len(all_entries), len({e[1] for e in all_entries})))
    print("already in PROMPTS.md: {}; to append: {}".format(len(all_entries) - len(missing), len(missing)))
    if write and missing:
        add = "" if SECTION.strip() in current else SECTION
        add += "".join(e[2] + "\n" for e in missing)
        with open(PROMPTS_MD, "a", encoding="utf-8", newline="") as f:
            f.write(add)
        with open(PROMPTS_MD, encoding="utf-8", newline="") as f:
            current = f.read()
    present = sum(1 for e in all_entries if e[2] in current)
    print("entries of prompts/ found in PROMPTS.md now: {} of {}".format(present, len(all_entries)))
    if write and present != len(all_entries):
        print("MISMATCH: not every entry was copied")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

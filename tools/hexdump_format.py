"""Annotated hex dump of the on-disk format, compared with README.md.

Usage: python3 tools/hexdump_format.py            (prints the report)
       python3 tools/hexdump_format.py --write    (also writes evidence/hexdump-format.txt)

The bytes are produced by src/kv/layout.py (the code under test). The labels
are NOT taken from layout.py: this tool walks the bytes with the field widths
written in README.md "On-disk format", typed out again here, and reads every
integer with int.from_bytes(..., "little"). So a mismatch between the code and
the README shows up as a wrong label or a failed README comparison.
Exit code 0 if everything matches, 1 otherwise.
"""
import os
import re
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.kv import layout  # noqa: E402  (the code under test)


def le(b):
    return int.from_bytes(b, "little")


def hexs(b):
    return " ".join("{:02X}".format(x) for x in b)


class Walker:
    """Reads fields in order and records one annotated line per field."""

    def __init__(self, data):
        self.data, self.pos, self.lines = data, 0, []

    def field(self, size, name, show=None):
        raw = self.data[self.pos:self.pos + size]
        if len(raw) != size:
            raise ValueError("file ends inside field {} at offset {}".format(name, self.pos))
        meaning = show(raw) if show else ""
        span = "{:>5}".format(self.pos) if size == 1 else "{:>2}-{:<2}".format(self.pos, self.pos + size - 1)
        shown = hexs(raw) if size <= 16 else hexs(raw[:16]) + " …"
        self.lines.append("  {:<7} {:>4}  {:<48}  {}{}".format(
            span, size, shown, name, ("  = " + meaning) if meaning else ""))
        self.pos += size
        return raw


def annotate(data):
    w = Walker(data)
    w.lines.append("  {:<7} {:>4}  {:<48}  {}".format("offset", "size", "bytes (hex)", "field"))
    w.lines.append("  -- file header (16) --")
    w.field(4, "magic", lambda b: repr(b))
    w.field(2, "version u16", lambda b: str(le(b)))
    w.field(2, "flags u16", lambda b: str(le(b)))
    w.field(8, "max_bytes u64", lambda b: "{} ({})".format(le(b), "no cap" if le(b) == 0 else "cap"))
    n_txn = 0
    while w.pos < len(data):
        n_txn += 1
        start = w.pos
        w.lines.append("  -- transaction {} (starts at {}) --".format(n_txn, start))
        w.field(1, "type u8", lambda b: "0x{:02X}".format(b[0]))
        count = le(w.field(2, "op_count u16", lambda b: str(le(b))))
        for i in range(count):
            op = w.field(1, "op {} u8".format(i + 1), lambda b: {1: "put", 2: "delete"}.get(b[0], "?"))[0]
            klen = le(w.field(2, "key_len u16", lambda b: str(le(b))))
            vlen = le(w.field(4, "val_len u32", lambda b: str(le(b))))
            w.field(klen, "key", lambda b: repr(b))
            if vlen:
                w.field(vlen, "value", lambda b: repr(b))
        body = data[start:w.pos]
        w.lines.append("  -- commit marker (16), body = offsets {}-{} = {} bytes --".format(start, w.pos - 1, len(body)))
        w.field(4, "marker magic", lambda b: repr(b))
        body_len = le(w.field(8, "body_len u64", lambda b: str(le(b))))
        crc = le(w.field(4, "crc32 u32", lambda b: "0x{:08X}".format(le(b))))
        w.field(1, "trailer", lambda b: "0x{:02X}".format(b[0]))
        ok = body_len == len(body) and crc == zlib.crc32(body)
        w.lines.append("  check: body_len {} real {}; crc32 of body recomputed = 0x{:08X} -> {}".format(
            body_len, len(body), zlib.crc32(body), "MATCH" if ok else "MISMATCH"))
        if not ok:
            raise ValueError("marker does not match its body")
    w.lines.append("  total: {} bytes".format(len(data)))
    return w.lines


def readme_example_rows():
    """Rows (first, last, hex-or-None) of README's 'Worked example' table."""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        text = f.read()
    part = text.split("**Worked example (design):**", 1)[1].split("\n\n**", 1)[0]
    rows = []
    for line in part.splitlines():
        m = re.match(r"\|\s*(\d+)(?:[–-](\d+))?\s*\|\s*([^|]*?)\s*\|", line)
        if not m:
            continue
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        cell = m.group(3).strip("` ")
        hex_bytes = bytes.fromhex(cell) if re.fullmatch(r"([0-9A-Fa-f]{2} ?)+", cell) else None
        rows.append((first, last, cell, hex_bytes))
    return rows, part


def compare_with_readme(data):
    rows, part = readme_example_rows()
    out, bad = [], 0
    m = re.search(r"The file is (\d+) bytes", part)
    stated = int(m.group(1)) if m else None
    out.append("  README says the file is {} bytes; real file is {} bytes -> {}".format(
        stated, len(data), "MATCH" if stated == len(data) else "MISMATCH"))
    bad += stated != len(data)
    for first, last, cell, expect in rows:
        real = data[first:last + 1]
        if expect is None:
            out.append("  {:>2}-{:<2} README shows '{}' (no bytes to compare); real {}".format(first, last, cell, hexs(real)))
            continue
        same = expect == real
        bad += not same
        out.append("  {:>2}-{:<2} README {:<48} real {:<48} {}".format(
            first, last, hexs(expect), hexs(real), "MATCH" if same else "MISMATCH"))
    return out, bad


def main():
    put_file = layout.encode_header(0) + layout.encode_txn([(layout.OP_PUT, b"hasib", b"20")])
    batch_file = layout.encode_header(0) + layout.encode_txn(
        [(layout.OP_PUT, b"hasib", b"20"), (layout.OP_PUT, b"ali", b"30")])
    report = ["# Annotated hex dump of the on-disk format (CLAUDE.md section 8)",
              "# Produced by: python3 tools/hexdump_format.py --write",
              "# Bytes from src/kv/layout.py (encode_header, encode_txn); labels from README field widths.",
              "# No database file is involved yet (Phase 2 has no file access): these are the exact bytes",
              "# a new file would hold after the operation.", ""]
    failures = 0
    for title, data in (("1. new file, then put hasib 20", put_file),
                        ("2. new file, then one batch: put hasib 20, put ali 30", batch_file)):
        report.append("## " + title)
        report.extend(annotate(data))
        for start in (16,):
            d = layout.decode_txn(data, start)
            report.append("  layout.decode_txn(data, 16) -> status {}, ends at {}".format(d.status, d.offset))
            failures += d.status != layout.OK
        report.append("")
    report.append("## 3. Byte-for-byte comparison with README.md 'Worked example' (example 1)")
    lines, bad = compare_with_readme(put_file)
    report.extend(lines)
    failures += bad
    batch_txn = len(batch_file) - 16
    report.append("  README says the batch hasib=20, ali=30 costs 46 bytes; real transaction is {} bytes -> {}".format(
        batch_txn, "MATCH" if batch_txn == 46 else "MISMATCH"))
    failures += batch_txn != 46
    report.append("")
    report.append("RESULT: {}".format("all fields match README" if not failures else "{} MISMATCH(ES)".format(failures)))
    text = "\n".join(report) + "\n"
    print(text, end="")
    if "--write" in sys.argv:
        with open(os.path.join(ROOT, "evidence", "hexdump-format.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

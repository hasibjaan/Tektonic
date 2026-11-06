"""The byte format of the database file: encode and decode. No file access here.

Frozen in CLAUDE.md section 8. Every integer is unsigned and little-endian.

    file        = header (16) + transaction + transaction + ...
    header      = magic "KVDB" (4) . version u16 = 1 . flags u16 = 0 . max_bytes u64
    transaction = body + commit marker (16) + trailer 0xEE (1)
    body        = type u8 = 0xA1 . op_count u16 . op . op . ...
    op          = op u8 (1 put, 2 delete) . key_len u16 . val_len u32 . key . value
    marker      = magic "CMIT" (4) . body_len u64 . crc32 u32 of the body

Decoding never trusts a length. Every length is checked against the limits and
against the bytes that are really there BEFORE anything is read. The answer is
always one of four kinds:

    OK          a whole, valid transaction, trailer included
    NO_TRAILER  a whole, valid transaction whose trailer byte is cut off (F4)
    INCOMPLETE  the input ends before some field is complete (a torn tail)
    CORRUPT     a byte that a kill cannot produce (CLAUDE.md section 10)

INCOMPLETE and CORRUPT never carry operations, so a broken transaction can
never be mistaken for a record.
"""
import collections
import struct
import zlib

from .errors import InvalidInput, KVError  # noqa: F401  (KVError re-exported for callers)

# ---- limits (CLAUDE.md section 7) -----------------------------------------
MAX_KEY = 1024
MAX_VALUE = 1_048_576          # 1 MB, decided in Phase 1
MAX_OPS = 10_000
MAX_U64 = (1 << 64) - 1

# ---- file header ----------------------------------------------------------
HEADER = struct.Struct("<4sHHQ")       # magic, version, flags, max_bytes
HEADER_SIZE = HEADER.size              # 16
MAGIC = b"KVDB"
VERSION = 1
FLAGS = 0

# ---- one transaction ------------------------------------------------------
TXN_HEADER = struct.Struct("<BH")      # type, op_count            -> 3 bytes
OP_HEADER = struct.Struct("<BHI")      # op, key_len, val_len      -> 7 bytes
MARKER = struct.Struct("<4sQI")        # magic, body_len, crc32    -> 16 bytes
TXN_TYPE = 0xA1
OP_PUT = 1
OP_DELETE = 2
MARKER_MAGIC = b"CMIT"
TRAILER = 0xEE
TRAILER_BYTES = bytes([TRAILER])
TXN_OVERHEAD = TXN_HEADER.size + MARKER.size + len(TRAILER_BYTES)   # 20

# ---- in-memory index entry (CLAUDE.md section 8) --------------------------
LOCATION_BITS = 21                     # a value length can be 1,048,576 = 2**20
LOCATION_MASK = (1 << LOCATION_BITS) - 1

# ---- decode results -------------------------------------------------------
OK = "ok"
NO_TRAILER = "no-trailer"
INCOMPLETE = "incomplete"
CORRUPT = "corrupt"
NOT_OURS = "not-ours"

# status: one of the constants above.
# ops:    for OK and NO_TRAILER, a list of (op, key, value_offset, value_length);
#         None otherwise. value_offset is an offset into the decoded buffer.
# offset: for OK and NO_TRAILER, the offset just past the transaction;
#         otherwise the offset of the field that is cut or bad.
# reason: a short explanation, empty for OK.
Decoded = collections.namedtuple("Decoded", "status ops offset reason")
Header = collections.namedtuple("Header", "status max_bytes reason")


# ---- encoding -------------------------------------------------------------

def validate_ops(ops):
    """Check a whole transaction before any byte is produced.

    ops is a list or tuple of (op, key, value) with op = OP_PUT or OP_DELETE.
    A delete's value must be None or b"". Returns (clean_ops, transaction_size)
    where clean_ops uses b"" for every delete value and transaction_size counts
    every byte: body, marker and trailer. Raises InvalidInput on any problem.
    """
    if not isinstance(ops, (list, tuple)):
        raise InvalidInput("a transaction must be a list of (op, key, value)")
    if not 1 <= len(ops) <= MAX_OPS:
        raise InvalidInput("a transaction needs 1 to {} operations, got {}".format(MAX_OPS, len(ops)))
    clean = []
    size = TXN_OVERHEAD
    for i, item in enumerate(ops):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise InvalidInput("operation {}: must be (op, key, value)".format(i))
        op, key, value = item
        if type(op) is not int or op not in (OP_PUT, OP_DELETE):
            raise InvalidInput("operation {}: op must be OP_PUT (1) or OP_DELETE (2)".format(i))
        if type(key) is not bytes:
            raise InvalidInput("operation {}: key must be bytes, not {}".format(i, type(key).__name__))
        if not 1 <= len(key) <= MAX_KEY:
            raise InvalidInput("operation {}: key must be 1 to {} bytes, got {}".format(i, MAX_KEY, len(key)))
        if op == OP_PUT:
            if type(value) is not bytes:
                raise InvalidInput("operation {}: value must be bytes, not {}".format(i, type(value).__name__))
            if len(value) > MAX_VALUE:
                raise InvalidInput("operation {}: value must be at most {} bytes, got {}".format(i, MAX_VALUE, len(value)))
        else:
            if value is None:
                value = b""
            elif type(value) is not bytes or value:
                raise InvalidInput("operation {}: a delete takes no value".format(i))
        size += OP_HEADER.size + len(key) + len(value)
        clean.append((op, key, value))
    return clean, size


def txn_size(ops):
    """Every byte one transaction will take on disk (used for the size cap)."""
    return validate_ops(ops)[1]


def encode_body(ops):
    """Encode the body (transaction header + operations) without copying values.

    Returns (chunks, body_len, crc). chunks is a list of bytes objects to be
    written in order; each value is the caller's own object, not a copy. The
    CRC is built up chunk by chunk, so a 10,000-operation batch of 1 MB values
    is never joined into one big buffer.
    """
    return encode_validated_body(validate_ops(ops)[0])


def encode_validated_body(clean):
    """encode_body for ops that already came out of validate_ops (no second check).

    The engine's write path calls validate_ops once, keeps the clean ops to work
    out each value's offset, and passes them here.
    """
    head = TXN_HEADER.pack(TXN_TYPE, len(clean))
    chunks = [head]
    crc = zlib.crc32(head)
    for op, key, value in clean:
        op_head = OP_HEADER.pack(op, len(key), len(value)) + key   # copies at most 7 + 1,024 bytes
        chunks.append(op_head)
        crc = zlib.crc32(op_head, crc)
        if value:
            chunks.append(value)
            crc = zlib.crc32(value, crc)
    body_len = sum(len(c) for c in chunks)
    return chunks, body_len, crc


def encode_commit(body_len, crc):
    """The 17 bytes written after the body's fsync: commit marker + trailer."""
    return MARKER.pack(MARKER_MAGIC, body_len, crc) + TRAILER_BYTES


def encode_txn(ops):
    """One whole transaction as a single bytes object (tests, hex dumps).

    The engine's write path uses encode_body + encode_commit instead, because
    it must fsync between them and must not copy large values.
    """
    chunks, body_len, crc = encode_body(ops)
    return b"".join(chunks) + encode_commit(body_len, crc)


def encode_header(max_bytes=0):
    """The 16-byte file header. max_bytes 0 means no size cap."""
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_U64:
        raise InvalidInput("max_bytes must be an integer from 0 to 2**64 - 1")
    return HEADER.pack(MAGIC, VERSION, FLAGS, max_bytes)


# ---- decoding -------------------------------------------------------------

def _view(buf):
    mv = memoryview(buf)
    if mv.format != "B" or mv.ndim != 1:
        mv = mv.cast("B")
    return mv


def decode_header(buf):
    """Read the file header at offset 0. Returns Header(status, max_bytes, reason).

    status is OK, INCOMPLETE (fewer than 16 bytes) or NOT_OURS (wrong magic,
    version or flags). It never says OK for a file that is not ours.
    """
    mv = _view(buf)
    if len(mv) < HEADER_SIZE:
        return Header(INCOMPLETE, None, "file has {} bytes, header needs {}".format(len(mv), HEADER_SIZE))
    magic, version, flags, max_bytes = HEADER.unpack_from(mv, 0)
    if magic != MAGIC:
        return Header(NOT_OURS, None, "magic is {!r}, expected {!r}".format(magic, MAGIC))
    if version != VERSION:
        return Header(NOT_OURS, None, "version is {}, expected {}".format(version, VERSION))
    if flags != FLAGS:
        return Header(NOT_OURS, None, "flags are {}, expected {}".format(flags, FLAGS))
    return Header(OK, max_bytes, "")


def is_header_prefix(buf):
    """True if buf is shorter than a header and could be the start of one.

    That is what an interrupted creation leaves behind. Bytes 0-7 must match
    "KVDB", version 1, flags 0; bytes 8-15 are max_bytes, so any value is fine.
    """
    mv = _view(buf)
    if len(mv) >= HEADER_SIZE:
        return False
    fixed = HEADER.pack(MAGIC, VERSION, FLAGS, 0)[:8]
    n = min(len(mv), 8)
    return bytes(mv[:n]) == fixed[:n]


def decode_txn(buf, pos=0):
    """Decode the transaction that starts at offset pos of buf.

    buf is bytes, bytearray, mmap or memoryview. Nothing is copied except the
    keys (at most 1,024 bytes each). Values are returned as (offset, length)
    and are not read: they are covered by one crc32 over the whole body span.
    Returns a Decoded tuple; see the module docstring for the four statuses.
    """
    mv = _view(buf)
    size = len(mv)
    start = pos

    if size - pos < TXN_HEADER.size:
        return Decoded(INCOMPLETE, None, pos, "transaction header is cut")
    txn_type, count = TXN_HEADER.unpack_from(mv, pos)
    if txn_type != TXN_TYPE:
        return Decoded(CORRUPT, None, pos, "transaction type is 0x{:02X}, expected 0xA1".format(txn_type))
    if not 1 <= count <= MAX_OPS:
        return Decoded(CORRUPT, None, pos + 1, "op_count is {}, must be 1 to {}".format(count, MAX_OPS))

    p = pos + TXN_HEADER.size
    ops = []
    for i in range(count):
        if size - p < OP_HEADER.size:
            return Decoded(INCOMPLETE, None, p, "header of operation {} is cut".format(i))
        op, key_len, val_len = OP_HEADER.unpack_from(mv, p)
        if op != OP_PUT and op != OP_DELETE:
            return Decoded(CORRUPT, None, p, "operation {}: op is {}, must be 1 or 2".format(i, op))
        if not 1 <= key_len <= MAX_KEY:
            return Decoded(CORRUPT, None, p + 1, "operation {}: key_len is {}".format(i, key_len))
        if val_len > MAX_VALUE:
            return Decoded(CORRUPT, None, p + 3, "operation {}: val_len is {}".format(i, val_len))
        if op == OP_DELETE and val_len:
            return Decoded(CORRUPT, None, p + 3, "operation {}: delete with val_len {}".format(i, val_len))
        key_at = p + OP_HEADER.size
        if size - key_at < key_len + val_len:
            return Decoded(INCOMPLETE, None, key_at, "key or value of operation {} is cut".format(i))
        ops.append((op, bytes(mv[key_at:key_at + key_len]), key_at + key_len, val_len))
        p = key_at + key_len + val_len

    body_end = p
    if size - p < MARKER.size:
        return Decoded(INCOMPLETE, None, p, "commit marker is cut")
    magic, body_len, crc = MARKER.unpack_from(mv, p)
    if magic != MARKER_MAGIC:
        return Decoded(CORRUPT, None, p, "marker magic is {!r}, expected {!r}".format(bytes(magic), MARKER_MAGIC))
    if body_len != body_end - start:
        return Decoded(CORRUPT, None, p + 4, "body_len is {}, body is {} bytes".format(body_len, body_end - start))
    if crc != zlib.crc32(mv[start:body_end]):
        return Decoded(CORRUPT, None, p + 12, "crc32 does not match the body")

    q = p + MARKER.size
    if q == size:
        return Decoded(NO_TRAILER, ops, q, "trailer byte is missing")
    if mv[q] != TRAILER:
        return Decoded(CORRUPT, None, q, "byte after the marker is 0x{:02X}, expected 0xEE".format(mv[q]))
    return Decoded(OK, ops, q + 1, "")


# ---- index entries ----------------------------------------------------------

def pack_location(value_offset, value_length):
    """One integer for the index: (value_offset << 21) | value_length."""
    return (value_offset << LOCATION_BITS) | value_length


def unpack_location(location):
    """Inverse of pack_location: returns (value_offset, value_length)."""
    return location >> LOCATION_BITS, location & LOCATION_MASK

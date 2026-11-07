"""Tests for src/kv/layout.py: the byte format (Phase 2). No file access."""
import struct
import unittest
import zlib

from src.kv import layout as L

PUT, DEL = L.OP_PUT, L.OP_DELETE


def roundtrip(ops):
    """Encode ops, decode them back, return (decoded, values-as-bytes)."""
    buf = L.encode_txn(ops)
    d = L.decode_txn(buf)
    values = [bytes(buf[off:off + n]) for _, _, off, n in d.ops] if d.ops else None
    return buf, d, values


class RoundTripTest(unittest.TestCase):
    def check(self, ops):
        buf, d, values = roundtrip(ops)
        self.assertEqual(d.status, L.OK, d.reason)
        self.assertEqual(d.offset, len(buf))
        self.assertEqual(len(buf), L.txn_size(ops))
        expect = [(op, k, v if v is not None else b"") for op, k, v in ops]
        self.assertEqual([(op, k, val) for (op, k, _, _), val in zip(d.ops, values)], expect)
        return buf

    def test_single_put(self):
        buf = self.check([(PUT, b"hasib", b"20")])
        self.assertEqual(len(buf), 34)                       # 27 + 5 + 2

    def test_delete(self):
        buf = self.check([(DEL, b"hasib", None)])
        self.assertEqual(len(buf), 32)                       # 27 + 5
        self.check([(DEL, b"hasib", b"")])                   # b"" is also accepted for a delete

    def test_two_op_batch(self):
        buf = self.check([(PUT, b"hasib", b"20"), (PUT, b"ali", b"30")])
        self.assertEqual(len(buf), 46)                       # 20 + 7*2 + 8 + 4

    def test_empty_value_is_a_value(self):
        buf, d, values = roundtrip([(PUT, b"k", b"")])
        self.assertEqual(d.status, L.OK)
        self.assertEqual(values, [b""])                      # present, length 0 — not missing

    def test_largest_key(self):
        self.check([(PUT, b"k" * 1024, b"v")])

    def test_largest_value(self):
        big = bytes(range(256)) * (L.MAX_VALUE // 256)
        self.assertEqual(len(big), 1_048_576)
        self.check([(PUT, b"big", big)])

    def test_largest_op_count(self):
        self.check([(PUT, b"k%d" % i, b"v") for i in range(L.MAX_OPS)])

    def test_put_then_delete_same_key_keeps_list_order(self):
        _, d, _ = roundtrip([(PUT, b"x", b"1"), (DEL, b"x", None)])
        self.assertEqual([op for op, *_ in d.ops], [PUT, DEL])

    def test_two_transactions_back_to_back(self):
        a = L.encode_txn([(PUT, b"a", b"1")])
        b = L.encode_txn([(DEL, b"a", None)])
        first = L.decode_txn(a + b)
        self.assertEqual(first.status, L.OK)
        second = L.decode_txn(a + b, first.offset)
        self.assertEqual((second.status, second.offset), (L.OK, len(a) + len(b)))

    def test_encode_body_does_not_copy_values(self):
        value = b"v" * 1000
        chunks, body_len, crc = L.encode_body([(PUT, b"k", value)])
        self.assertTrue(any(c is value for c in chunks))     # the very same object
        self.assertEqual(body_len, sum(map(len, chunks)))
        self.assertEqual(crc, zlib.crc32(b"".join(chunks)))  # built piece by piece, same result

    def test_location_packing(self):
        for off, n in ((0, 0), (16 + 10, 1_048_576), (10**12, 7)):
            self.assertEqual(L.unpack_location(L.pack_location(off, n)), (off, n))


class RejectionTest(unittest.TestCase):
    def rejects(self, ops):
        with self.assertRaises(L.InvalidInput):
            L.encode_txn(ops)
        with self.assertRaises(L.InvalidInput):
            L.encode_body(ops)

    def test_empty_key(self):
        self.rejects([(PUT, b"", b"v")])

    def test_key_one_byte_too_long(self):
        self.rejects([(PUT, b"k" * 1025, b"v")])

    def test_value_one_byte_too_long(self):
        self.rejects([(PUT, b"k", b"v" * (L.MAX_VALUE + 1))])

    def test_op_count_zero(self):
        self.rejects([])

    def test_op_count_too_many(self):
        self.rejects([(PUT, b"k", b"v")] * (L.MAX_OPS + 1))

    def test_str_instead_of_bytes(self):
        self.rejects([(PUT, "hasib", b"20")])
        self.rejects([(PUT, b"hasib", "20")])
        self.rejects([(PUT, bytearray(b"k"), b"v")])

    def test_bad_op_and_shape(self):
        self.rejects([(3, b"k", b"v")])
        self.rejects([(True, b"k", b"v")])                   # bool is not an op
        self.rejects([(PUT, b"k")])
        self.rejects([(DEL, b"k", b"not empty")])
        self.rejects((x for x in [(PUT, b"k", b"v")]))       # a generator is not a list

    def test_one_bad_op_rejects_the_whole_batch(self):
        self.rejects([(PUT, b"ok", b"v"), (PUT, b"", b"v")])

    def test_header_max_bytes(self):
        self.assertEqual(len(L.encode_header(0)), 16)
        for bad in (-1, 1 << 64, "100", True):
            with self.assertRaises(L.InvalidInput):
                L.encode_header(bad)


class CutTest(unittest.TestCase):
    """A kill only shortens the file. Every cut must be INCOMPLETE — except the
    cut of only the trailer byte, which the frozen design (CLAUDE.md section 10,
    scenario F4) treats as a whole transaction with its trailer missing."""

    CASES = {
        "put": [(PUT, b"hasib", b"20")],
        "delete": [(DEL, b"hasib", None)],
        "batch": [(PUT, b"hasib", b"20"), (PUT, b"ali", b"30")],
        "put-then-delete": [(PUT, b"x", b"1"), (DEL, b"x", None)],
        "empty-value": [(PUT, b"k", b"")],
    }

    def test_every_cut(self):
        for name, ops in self.CASES.items():
            full = L.encode_txn(ops)
            for cut in range(len(full)):
                d = L.decode_txn(full[:cut])
                with self.subTest(case=name, cut=cut):
                    if cut == len(full) - 1:
                        self.assertEqual(d.status, L.NO_TRAILER)
                        self.assertEqual(d.offset, cut)
                        self.assertEqual(d.ops, L.decode_txn(full).ops)
                    else:
                        self.assertEqual(d.status, L.INCOMPLETE, d.reason)
                        self.assertIsNone(d.ops)                    # never a record
                        self.assertLessEqual(d.offset, cut)         # points inside the input, never past it

    def test_sampled_cuts_of_a_100000_byte_value(self):
        """SAMPLED, not every cut: every cut in the first 40 bytes (headers), then every
        997th byte through the 100,000-byte value, then the last torn length. Each one
        must be INCOMPLETE (torn), never a record. (Renamed in the final fix round: the
        old name said 'every cut'.)"""
        full = L.encode_txn([(PUT, b"big", b"x" * 100_000)])
        for cut in list(range(0, 40)) + list(range(40, len(full) - 1, 997)) + [len(full) - 2]:
            with self.subTest(cut=cut):
                d = L.decode_txn(full[:cut])
                self.assertEqual(d.status, L.INCOMPLETE)
                self.assertLessEqual(d.offset, cut)

    def test_cut_inside_second_transaction(self):
        a = L.encode_txn([(PUT, b"a", b"1")])
        b = L.encode_txn([(PUT, b"b", b"2")])
        for cut in range(len(b) - 1):
            d = L.decode_txn(a + b[:cut], len(a))
            self.assertEqual(d.status, L.INCOMPLETE)


class CorruptTest(unittest.TestCase):
    """Bytes a kill cannot produce must be CORRUPT, never a record."""

    def setUp(self):
        self.ops = [(PUT, b"hasib", b"20"), (PUT, b"ali", b"30")]
        self.full = L.encode_txn(self.ops)
        self.marker_at = len(self.full) - 17

    def corrupt(self, data, pos=0):
        d = L.decode_txn(bytes(data), pos)
        self.assertEqual(d.status, L.CORRUPT, d.reason)
        self.assertIsNone(d.ops)
        return d

    def flipped(self, index):
        data = bytearray(self.full)
        data[index] ^= 0xFF
        return data

    def test_flip_each_checksum_byte(self):
        for i in range(4):
            d = self.corrupt(self.flipped(self.marker_at + 12 + i))
            self.assertIn("crc32", d.reason)

    def test_flip_any_body_byte(self):
        for i in range(self.marker_at):
            with self.subTest(index=i):
                d = L.decode_txn(bytes(self.flipped(i)))
                self.assertIn(d.status, (L.CORRUPT, L.INCOMPLETE))   # a flipped length may point past the end
                self.assertIsNone(d.ops)

    def test_bad_marker_magic_body_len_and_trailer(self):
        self.corrupt(self.flipped(self.marker_at))                   # "CMIT"
        self.corrupt(self.flipped(self.marker_at + 4))               # body_len
        self.corrupt(self.flipped(len(self.full) - 1))               # trailer 0xEE

    def test_bad_header_fields(self):
        def txn(type_=0xA1, count=1, op=PUT, klen=1, vlen=1, tail=b"kv"):
            return struct.pack("<BH", type_, count) + struct.pack("<BHI", op, klen, vlen) + tail + b"\0" * 40
        self.corrupt(txn(type_=0xA2))
        self.corrupt(txn(count=0))
        self.corrupt(txn(count=10_001))
        self.corrupt(txn(op=3))
        self.corrupt(txn(klen=0))
        self.corrupt(txn(klen=1025))
        self.corrupt(txn(vlen=L.MAX_VALUE + 1))
        self.corrupt(txn(op=DEL, vlen=1))

    def test_huge_length_is_rejected_without_reading(self):
        # val_len = 4 GB - 1: must be CORRUPT from the limit check, never an attempt to read it.
        data = struct.pack("<BH", 0xA1, 1) + struct.pack("<BHI", PUT, 1, 0xFFFFFFFF) + b"k"
        self.corrupt(data)

    def test_valid_length_past_the_end_is_incomplete(self):
        data = struct.pack("<BH", 0xA1, 1) + struct.pack("<BHI", PUT, 1, L.MAX_VALUE) + b"k" + b"v" * 10
        self.assertEqual(L.decode_txn(data).status, L.INCOMPLETE)


class HeaderTest(unittest.TestCase):
    def test_header_round_trip(self):
        h = L.decode_header(L.encode_header(123456))
        self.assertEqual((h.status, h.max_bytes), (L.OK, 123456))

    def test_not_ours(self):
        good = L.encode_header(0)
        for i in range(8):                                   # magic, version, flags
            bad = bytearray(good)
            bad[i] ^= 0x01
            self.assertEqual(L.decode_header(bytes(bad)).status, L.NOT_OURS, i)

    def test_short_file_and_prefix(self):
        good = L.encode_header(0x1122334455667788)
        for n in range(16):
            self.assertEqual(L.decode_header(good[:n]).status, L.INCOMPLETE)
            self.assertTrue(L.is_header_prefix(good[:n]), n)
        self.assertFalse(L.is_header_prefix(good))           # 16 bytes is not a short file
        self.assertTrue(L.is_header_prefix(b"KVDB\x01\x00\x00\x00\x7f\x00"))
        self.assertFalse(L.is_header_prefix(b"hello worl"))
        self.assertFalse(L.is_header_prefix(b"KVDB\x02"))


if __name__ == "__main__":
    unittest.main()

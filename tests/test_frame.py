import unittest

from sonyeq.frame import END, ESC, START, Frame, FrameError, StreamDecoder, decode, encode


class FrameCodecTest(unittest.TestCase):
    def test_encode_layout(self):
        raw = encode(Frame(0x0C, 0x00, bytes((0x56, 0x00))))
        self.assertEqual(raw.hex(" "), "3e 0c 00 00 00 00 02 56 00 64 3c")

    def test_roundtrip(self):
        frame = Frame(0x0C, 0x01, bytes(range(0x30, 0x50)))
        self.assertEqual(decode(encode(frame)), frame)

    def test_marker_bytes_are_escaped(self):
        raw = encode(Frame(0x0C, 0x00, bytes((START, END, ESC))))
        self.assertNotIn(START, raw[1:-1])
        self.assertNotIn(END, raw[1:-1])
        self.assertIn(bytes((ESC, 0x2E)), raw)
        self.assertIn(bytes((ESC, 0x2C)), raw)
        self.assertIn(bytes((ESC, 0x2D)), raw)
        self.assertEqual(decode(raw).payload, bytes((START, END, ESC)))

    def test_bad_checksum_rejected(self):
        raw = bytearray(encode(Frame(0x0C, 0x00, b"\x56\x00")))
        raw[-2] ^= 0xFF
        with self.assertRaises(FrameError):
            decode(bytes(raw))

    def test_stream_split_across_reads(self):
        a = encode(Frame(0x01, 0x01, b""))
        b = encode(Frame(0x0C, 0x00, b"\x57\x00\x10"))
        dec = StreamDecoder()
        stream = b"\x00" + a + b
        frames = dec.feed(stream[:5]) + dec.feed(stream[5:])
        self.assertEqual([f.payload for f in frames], [b"", b"\x57\x00\x10"])


if __name__ == "__main__":
    unittest.main()

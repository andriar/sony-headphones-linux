"""Sony MDR frame codec.

Wire format (see PROTOCOL.md):
    0x3e | escape(type, seq, len:u32be, payload, checksum) | 0x3c
checksum = sum(type, seq, len bytes, payload) & 0xff.
Bytes 0x3c/0x3d/0x3e inside the frame body are sent as 0x3d followed by the byte with bit 0x10 cleared.
"""
from dataclasses import dataclass

START = 0x3E
END = 0x3C
ESC = 0x3D
ESC_MASK = 0x10

TYPE_ACK = 0x01
TYPE_DATA_MDR = 0x0C
TYPE_DATA_MDR_NO2 = 0x0E


class FrameError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    type: int
    seq: int
    payload: bytes


def _escape(body: bytes) -> bytes:
    out = bytearray()
    for b in body:
        if b in (START, END, ESC):
            out += bytes((ESC, b & ~ESC_MASK))
        else:
            out.append(b)
    return bytes(out)


def _unescape(body: bytes) -> bytes:
    out = bytearray()
    it = iter(body)
    for b in it:
        if b == ESC:
            nxt = next(it, None)
            if nxt is None:
                raise FrameError("escape byte at end of frame")
            out.append(nxt | ESC_MASK)
        else:
            out.append(b)
    return bytes(out)


def encode(frame: Frame) -> bytes:
    body = bytes((frame.type, frame.seq)) + len(frame.payload).to_bytes(4, "big") + frame.payload
    body += bytes((sum(body) & 0xFF,))
    return bytes((START,)) + _escape(body) + bytes((END,))


def decode(raw: bytes) -> Frame:
    if len(raw) < 2 or raw[0] != START or raw[-1] != END:
        raise FrameError(f"missing start/end marker: {raw.hex(' ')}")
    body = _unescape(raw[1:-1])
    if len(body) < 7:
        raise FrameError(f"frame too short: {body.hex(' ')}")
    *content, checksum = body
    if sum(content) & 0xFF != checksum:
        raise FrameError(f"bad checksum: {raw.hex(' ')}")
    length = int.from_bytes(body[2:6], "big")
    payload = body[6:-1]
    if len(payload) != length:
        raise FrameError(f"length {length} != payload {len(payload)}")
    return Frame(body[0], body[1], payload)


class StreamDecoder:
    """Splits a byte stream into frames; bytes outside start/end markers are dropped."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames = []
        while True:
            start = self._buf.find(START)
            if start < 0:
                self._buf.clear()
                return frames
            end = self._buf.find(END, start + 1)
            if end < 0:
                del self._buf[:start]
                return frames
            frames.append(decode(bytes(self._buf[start : end + 1])))
            del self._buf[: end + 1]


def ack_for(frame: Frame) -> Frame:
    return Frame(TYPE_ACK, 1 - frame.seq, b"")

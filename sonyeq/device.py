"""RFCOMM link to the headset's Sony control service ("Serial HPC")."""
import errno
import socket
import time

from .frame import TYPE_ACK, TYPE_DATA_MDR, Frame, StreamDecoder, ack_for, encode

SONY_HPC_UUID = "956c7b26-d49a-4ba8-b03f-b17d393cb6e2"
DEFAULT_CHANNEL = 9
CONNECT_ATTEMPTS = 5


class Link:
    def __init__(self, mac: str, channel: int = DEFAULT_CHANNEL, verbose: bool = False):
        self.mac, self.channel, self.verbose = mac, channel, verbose
        self.seq = 0
        self._dec = StreamDecoder()
        self._sock = None
        self.inbox: list[Frame] = []

    def __enter__(self):
        # The headset releases the RFCOMM channel a moment after the previous client closes;
        # connecting again too soon fails with EBUSY.
        for attempt in range(CONNECT_ATTEMPTS):
            self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self._sock.settimeout(5)
            try:
                self._sock.connect((self.mac, self.channel))
                return self
            except OSError as e:
                self._sock.close()
                if e.errno != errno.EBUSY or attempt == CONNECT_ATTEMPTS - 1:
                    raise
                time.sleep(0.5 * (attempt + 1))

    def __exit__(self, *exc):
        self._sock.close()

    def _log(self, tag, frame):
        if self.verbose:
            print(f"{tag} type={frame.type:02x} seq={frame.seq} payload={frame.payload.hex(' ')}")

    def _write(self, frame: Frame):
        self._log(">>", frame)
        self._sock.sendall(encode(frame))

    def _pump(self, timeout: float) -> list[Frame]:
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            return []
        frames = self._dec.feed(data)
        for f in frames:
            self._log("<<", f)
            if f.type != TYPE_ACK:
                self._write(ack_for(f))
        return frames

    def request(self, payload: bytes, expect_cmd: int | None = None, timeout: float = 3.0,
                data_type: int = TYPE_DATA_MDR) -> Frame | None:
        """Send one data frame, wait for its ACK, then for a reply whose first byte is expect_cmd."""
        self._write(Frame(data_type, self.seq, payload))
        acked = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for f in self._pump(0.3):
                if f.type == TYPE_ACK:
                    acked = True
                    self.seq = f.seq
                elif expect_cmd is not None and f.payload[:1] == bytes((expect_cmd,)):
                    return f
                else:
                    self.inbox.append(f)
            if acked and expect_cmd is None:
                return None
        if not acked:
            raise TimeoutError(f"no ACK for {payload.hex(' ')}")
        return None

    def drain(self, seconds: float) -> list[Frame]:
        end = time.monotonic() + seconds
        got = []
        while time.monotonic() < end:
            got += self._pump(0.3)
        return got

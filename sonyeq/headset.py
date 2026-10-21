"""High-level headset operations. Each call opens its own short RFCOMM session."""
import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import bluez, eq, ncasm, power
from .device import Link

CMD_CONNECT_GET_PROTOCOL_INFO = 0x00
CMD_CONNECT_RET_PROTOCOL_INFO = 0x01
PROBE_TIMEOUT = 1.5
LOCK_TIMEOUT = 20.0


class HeadsetError(RuntimeError):
    def __init__(self, message: str, disconnected: bool = False):
        super().__init__(message)
        self.disconnected = disconnected


def lock_path() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir(), "sonyeq.lock")


@contextmanager
def exclusive(timeout: float | None = None):
    """Hold the per-user headset lock, so the CLI, tray, app and shell extension take turns."""
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise HeadsetError("another program is still talking to the headset; try again") from None
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Caps:
    eq: eq.EqCaps | None
    ncasm: bool
    battery: bool


@dataclass(frozen=True)
class Snapshot:
    name: str
    caps: Caps
    eq: eq.EqState | None
    ncasm: ncasm.NcAsmState | None
    battery: power.Battery | None


class Headset:
    def __init__(self, mac: str | None = None, channel: int | None = None, verbose: bool = False):
        self.mac, self.verbose = mac, verbose
        self._fixed_channel = channel
        self.device: bluez.SonyDevice | None = None
        self._channel: int | None = None
        self._caps: Caps | None = None

    def _session(self) -> "_Session":
        try:
            dev = bluez.pick_device(self.mac)
        except bluez.DeviceError as e:
            raise HeadsetError(str(e), disconnected=True) from e
        if self.device is None or dev.mac != self.device.mac:
            self._channel, self._caps = None, None
        self.device = dev
        if self._channel is None:
            self._channel = self._fixed_channel or bluez.control_channel(dev.mac)
        return _Session(self, Link(dev.mac, self._channel, verbose=self.verbose))

    def caps(self) -> Caps:
        with self._session() as s:
            return s.caps

    def snapshot(self) -> Snapshot:
        with self._session() as s:
            c = s.caps
            return Snapshot(
                self.device.name, c,
                s.read_eq() if c.eq else None,
                s.read_ncasm() if c.ncasm else None,
                s.read_battery() if c.battery else None,
            )

    def read_eq(self) -> eq.EqState:
        with self._session() as s:
            return s.read_eq()

    def read_ncasm(self) -> ncasm.NcAsmState:
        with self._session() as s:
            return s.read_ncasm()

    def read_battery(self) -> power.Battery:
        with self._session() as s:
            return s.read_battery()

    def set_preset(self, preset_id: int) -> eq.EqState:
        with self._session() as s:
            c = s.require_eq()
            if preset_id not in c.presets:
                raise HeadsetError(f"preset 0x{preset_id:02x} is not offered by this headset")
            return s.apply_eq(eq.set_preset(preset_id))

    def set_levels(self, levels, slot: int = eq.CUSTOM_SLOTS["custom"]) -> eq.EqState:
        with self._session() as s:
            return s.apply_eq(eq.set_levels(s.require_eq(), levels, slot))

    def update_levels(self, changes: dict[str, int], slot: int = eq.CUSTOM_SLOTS["custom"]) -> eq.EqState:
        with self._session() as s:
            c = s.require_eq()
            unknown = set(changes) - set(c.slot_names)
            if unknown:
                raise ValueError(f"unknown slot(s) {', '.join(sorted(unknown))}; use {', '.join(c.slot_names)}")
            levels = dict(zip(c.slot_names, s.read_eq().levels))
            levels.update(changes)
            return s.apply_eq(eq.set_levels(c, [levels[n] for n in c.slot_names], slot))

    def set_ncasm(self, mode: str | None = None, level: int | None = None,
                  voice: bool | None = None) -> ncasm.NcAsmState:
        with self._session() as s:
            state = s.read_ncasm()
            if mode is not None:
                state = ncasm.with_mode(state, mode)
            if level is not None or voice is not None:
                # Level/voice only take effect in ambient mode; switch there so the change is audible.
                state = ncasm.NcAsmState(True, True,
                                         state.voice if voice is None else voice,
                                         state.level if level is None else level)
            return s.apply_ncasm(ncasm.set_param(state))


class _Session:
    def __init__(self, headset: Headset, link: Link):
        self.headset, self.link = headset, link
        self._lock = exclusive()

    def __enter__(self):
        self._lock.__enter__()
        try:
            try:
                self.link.__enter__()
            except OSError as e:
                raise HeadsetError(f"cannot open the control channel: {e}") from e
            try:
                self.ask(bytes((CMD_CONNECT_GET_PROTOCOL_INFO, 0x00)), CMD_CONNECT_RET_PROTOCOL_INFO)
            except HeadsetError:
                self.link.__exit__(None, None, None)
                raise
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.link.__exit__(*exc)
        finally:
            self._lock.__exit__(None, None, None)

    def ask(self, payload: bytes, reply_cmd: int, timeout: float = 3.0, optional: bool = False) -> bytes | None:
        try:
            reply = self.link.request(payload, expect_cmd=reply_cmd, timeout=timeout)
        except (OSError, TimeoutError) as e:
            raise HeadsetError(str(e)) from e
        if reply is None and not optional:
            raise HeadsetError(f"no reply to {payload.hex(' ')}")
        return reply.payload if reply else None

    @property
    def caps(self) -> Caps:
        if self.headset._caps is None:
            raw_eq = self.ask(eq.get_capability(), eq.CMD_RET_CAPABILITY, PROBE_TIMEOUT, optional=True)
            raw_nc = self.ask(ncasm.get_capability(), ncasm.CMD_RET_CAPABILITY, PROBE_TIMEOUT, optional=True)
            raw_bat = self.ask(power.get_battery(), power.CMD_RET_STATUS, PROBE_TIMEOUT, optional=True)
            self.headset._caps = Caps(eq.parse_capability(raw_eq) if raw_eq else None,
                                      raw_nc is not None, raw_bat is not None)
        return self.headset._caps

    def require_eq(self) -> eq.EqCaps:
        if self.caps.eq is None:
            raise HeadsetError("this headset does not report a supported equalizer")
        return self.caps.eq

    def read_eq(self) -> eq.EqState:
        return eq.parse_param(self.require_eq(), self.ask(eq.get_param(), eq.CMD_RET_PARAM))

    def read_ncasm(self) -> ncasm.NcAsmState:
        if not self.caps.ncasm:
            raise HeadsetError("noise control on this headset uses a layout that is not supported yet")
        return ncasm.parse_param(self.ask(ncasm.get_param(), ncasm.CMD_RET_PARAM))

    def read_battery(self) -> power.Battery:
        if not self.caps.battery:
            raise HeadsetError("this headset does not report a single battery level")
        return power.parse_battery(self.ask(power.get_battery(), power.CMD_RET_STATUS))

    def apply_eq(self, payload: bytes) -> eq.EqState:
        return eq.parse_param(self.require_eq(), self.ask(payload, eq.CMD_NTFY_PARAM))

    def apply_ncasm(self, payload: bytes) -> ncasm.NcAsmState:
        return ncasm.parse_param(self.ask(payload, ncasm.CMD_NTFY_PARAM))

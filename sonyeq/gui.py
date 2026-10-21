"""Pieces shared by the libadwaita app and the tray icon. Needs only GLib/Gio, never a GTK version."""
import queue
import sys
import threading
from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import bluez
from .headset import Headset, HeadsetError, Snapshot

APP_ID = "com.andriar.SonyHeadphones"
RECONNECT_DELAY_MS = 3000
MODE_LABELS = {"nc": "Noise canceling", "ambient": "Ambient sound", "off": "Noise control off"}
PRESET_LABELS = {
    "off": "Off", "bright": "Bright", "excited": "Excited", "mellow": "Mellow", "relaxed": "Relaxed",
    "vocal": "Vocal", "treble": "Treble boost", "bass": "Bass boost", "speech": "Speech",
    "custom": "Custom", "custom1": "Custom 1", "custom2": "Custom 2",
}


def slot_label(name: str) -> str:
    return "Clear\nBass" if name == "clear-bass" else name


@dataclass(frozen=True)
class Absence:
    """Why there is no headset to control right now.

    kind: "none" (no Sony headset paired), "unsupported" (only v1 models paired),
    "disconnected" (paired, turned off or out of range), "unreachable" (connected, but the
    control channel did not answer, usually because the phone app holds it).
    """
    kind: str
    message: str
    device: bluez.SonyDevice | None = None


def explain(err: HeadsetError, headset: Headset) -> Absence:
    """Turn a failed headset call into an Absence with enough detail for an empty state."""
    if not err.disconnected:
        return Absence("unreachable", str(err), headset.device)
    try:
        devices = bluez.sony_devices()
        if headset.mac:
            devices = [d for d in devices if d.mac == headset.mac.upper()]
    except bluez.DeviceError as e:
        return Absence("none", str(e))
    supported = [d for d in devices if d.v2]
    if not devices:
        return Absence("none", str(err))
    if not supported:
        return Absence("unsupported", str(err), devices[0])
    dev = next((d for d in supported if d.connected), supported[0])
    return Absence("unreachable" if dev.connected else "disconnected", str(err), dev)


def load(headset: Headset) -> Snapshot | Absence:
    """Read everything the UI shows; on failure say why instead of raising."""
    try:
        return headset.snapshot()
    except HeadsetError as e:
        return explain(e, headset)


class Worker:
    """Runs headset calls one at a time off the GTK thread; delivers results with GLib.idle_add."""

    def __init__(self, headset: Headset):
        self.headset = headset
        self.jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, fn, on_done, on_error):
        self.jobs.put((fn, on_done, on_error))

    def _run(self):
        while True:
            fn, on_done, on_error = self.jobs.get()
            try:
                result = fn(self.headset)
            except Exception as e:  # noqa: BLE001 - a dead worker would freeze the UI; report everything
                if not isinstance(e, (HeadsetError, ValueError, bluez.DeviceError)):
                    print(f"sonyeq: unexpected error: {e!r}", file=sys.stderr)
                GLib.idle_add(on_error, e)
            else:
                GLib.idle_add(on_done, result)


class BluezWatch:
    """Calls on_change(dbus_path, connected) whenever a BlueZ device connects or disconnects."""

    def __init__(self, on_change):
        self.on_change = on_change
        try:
            # Keep a reference: the subscription is dropped when the connection object is collected.
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        except GLib.Error:
            self._bus = None  # no system bus: callers fall back to polling
            return
        self._bus.signal_subscribe("org.bluez", "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                   None, "org.bluez.Device1", Gio.DBusSignalFlags.NONE, self._on_signal)

    def _on_signal(self, _conn, _sender, path, _iface, _signal, params):
        _, changed, _ = params.unpack()
        if "Connected" in changed:
            self.on_change(path, changed["Connected"])


def concerns(device: bluez.SonyDevice | None, dbus_path: str) -> bool:
    """True when a BlueZ signal for dbus_path may change what we show for device."""
    return device is None or dbus_path.endswith(device.dbus_path_suffix)

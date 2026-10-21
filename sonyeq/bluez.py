"""Find Sony headsets known to BlueZ and resolve their control channel.

Devices are read from BlueZ over D-Bus when PyGObject is available (this also works inside the
Flatpak sandbox); otherwise from `bluetoothctl`. The RFCOMM channel comes from `sdptool` when present.
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

UUID_V2 = "956c7b26-d49a-4ba8-b03f-b17d393cb6e2"  # "Serial HPC", MDR protocol v2 (verified on WH-1000XM5)
UUID_V1 = "96cc203e-5068-46ad-b32d-e316f5e069ba"  # older Sony control service, not supported yet
FALLBACK_CHANNEL = 9


class DeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SonyDevice:
    mac: str
    name: str
    connected: bool
    v2: bool
    v1: bool

    @property
    def dbus_path_suffix(self) -> str:
        return "dev_" + self.mac.replace(":", "_")


def _run(*cmd: str, timeout: float = 10) -> str:
    if not shutil.which(cmd[0]):
        raise DeviceError(f"{cmd[0]} not found; install bluez")
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired as e:
        raise DeviceError(f"{cmd[0]} did not answer within {timeout:g} s") from e


def device_info(mac: str) -> SonyDevice | None:
    out = _run("bluetoothctl", "info", mac)
    if "not available" in out or "Device " not in out:
        return None
    uuids = {u.lower() for u in re.findall(r"\(([0-9a-fA-F-]{36})\)", out)}
    name = re.search(r"^\s*(?:Alias|Name): (.+)$", out, re.M)
    return SonyDevice(
        mac=mac.upper(),
        name=name.group(1).strip() if name else mac,
        connected=bool(re.search(r"^\s*Connected: yes", out, re.M)),
        v2=UUID_V2 in uuids,
        v1=UUID_V1 in uuids,
    )


def _dbus_devices() -> list[SonyDevice] | None:
    """Paired devices from BlueZ's object manager, or None when D-Bus/PyGObject is unavailable."""
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        objects = bus.call_sync("org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                                None, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        return None
    found = []
    for interfaces in objects.values():
        d = interfaces.get("org.bluez.Device1")
        if not d or not d.get("Paired"):
            continue
        uuids = {u.lower() for u in d.get("UUIDs", [])}
        found.append(SonyDevice(mac=d["Address"].upper(), name=d.get("Alias") or d.get("Name") or d["Address"],
                                connected=bool(d.get("Connected")), v2=UUID_V2 in uuids, v1=UUID_V1 in uuids))
    return found


def _paired_devices() -> list[SonyDevice]:
    devices = _dbus_devices()
    if devices is not None:
        return devices
    out = _run("bluetoothctl", "devices", "Paired")
    return [d for mac in re.findall(r"^Device ([0-9A-F:]{17}) ", out, re.M) if (d := device_info(mac))]


def sony_devices() -> list[SonyDevice]:
    return [d for d in _paired_devices() if d.v2 or d.v1]


def pick_device(mac: str | None = None) -> SonyDevice:
    """Return the headset to talk to; raises DeviceError with a user-facing reason otherwise."""
    mac = mac or os.environ.get("SONYEQ_MAC")
    if mac:
        dev = next((d for d in _paired_devices() if d.mac == mac.upper()), None)
        if dev is None:
            raise DeviceError(f"{mac} is not known to BlueZ; pair it first")
    else:
        devices = sony_devices()
        supported = [d for d in devices if d.v2]
        if not devices:
            raise DeviceError("no paired Sony headset found; pair it in Bluetooth settings first")
        if not supported:
            names = ", ".join(d.name for d in devices)
            raise DeviceError(f"{names}: uses Sony's older control protocol, which is not supported yet")
        dev = next((d for d in supported if d.connected), supported[0])
    if not dev.v2:
        raise DeviceError(f"{dev.name}: uses Sony's older control protocol, which is not supported yet")
    if not dev.connected:
        raise DeviceError(f"{dev.name} is paired but not connected; turn it on or run: bluetoothctl connect {dev.mac}")
    return dev


def connect(mac: str) -> bool:
    """Ask BlueZ to connect a paired device; True once it reports success."""
    return "Connection successful" in _run("bluetoothctl", "connect", mac, timeout=25)


def control_channel(mac: str) -> int:
    """RFCOMM channel of the v2 control service, from SDP; falls back to 9 if sdptool is unavailable."""
    if not shutil.which("sdptool"):
        return FALLBACK_CHANNEL
    out = subprocess.run(["sdptool", "browse", mac], capture_output=True, text=True, timeout=20).stdout
    for record in out.split("Service Name:"):
        if UUID_V2 in record.lower():
            if m := re.search(r"Channel: (\d+)", record):
                return int(m.group(1))
    return FALLBACK_CHANNEL

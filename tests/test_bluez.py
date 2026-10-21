import unittest
from unittest import mock

from sonyeq import bluez

INFO_V2 = """Device AA:BB:CC:DD:EE:FF (public)
\tName: WH-1000XM5
\tAlias: WH-1000XM5
\tPaired: yes
\tConnected: {connected}
\tUUID: Vendor specific           (956c7b26-d49a-4ba8-b03f-b17d393cb6e2)
"""
INFO_V1 = """Device 11:22:33:44:55:66 (public)
\tAlias: WH-1000XM4
\tConnected: yes
\tUUID: Vendor specific           (96cc203e-5068-46ad-b32d-e316f5e069ba)
"""


def fake_run(devices, infos):
    def run(*cmd):
        if cmd[1] == "devices":
            return devices
        return infos.get(cmd[2], "Device not available")
    return run


class PickDeviceTest(unittest.TestCase):
    def pick(self, devices, infos, mac=None):
        with mock.patch.object(bluez, "_run", fake_run(devices, infos)), \
                mock.patch.object(bluez, "_dbus_devices", return_value=None), \
                mock.patch.dict("os.environ", {}, clear=True):
            return bluez.pick_device(mac)

    def test_connected_v2_headset(self):
        dev = self.pick("Device AA:BB:CC:DD:EE:FF WH-1000XM5\n",
                        {"AA:BB:CC:DD:EE:FF": INFO_V2.format(connected="yes")})
        self.assertEqual((dev.name, dev.connected, dev.v2), ("WH-1000XM5", True, True))

    def test_paired_but_disconnected(self):
        with self.assertRaisesRegex(bluez.DeviceError, "not connected"):
            self.pick("Device AA:BB:CC:DD:EE:FF WH-1000XM5\n",
                      {"AA:BB:CC:DD:EE:FF": INFO_V2.format(connected="no")})

    def test_only_older_protocol_device(self):
        with self.assertRaisesRegex(bluez.DeviceError, "older control protocol"):
            self.pick("Device 11:22:33:44:55:66 WH-1000XM4\n", {"11:22:33:44:55:66": INFO_V1})

    def test_no_sony_device(self):
        with self.assertRaisesRegex(bluez.DeviceError, "no paired Sony"):
            self.pick("Device 99:99:99:99:99:99 Mouse\n", {"99:99:99:99:99:99": "Device 99\n\tAlias: Mouse\n"})

    def test_prefers_connected_device(self):
        other = INFO_V2.format(connected="no").replace("AA:BB:CC:DD:EE:FF", "AA:AA:AA:AA:AA:AA")
        dev = self.pick("Device AA:AA:AA:AA:AA:AA WH-1000XM5\nDevice AA:BB:CC:DD:EE:FF WH-1000XM5\n",
                        {"AA:AA:AA:AA:AA:AA": other, "AA:BB:CC:DD:EE:FF": INFO_V2.format(connected="yes")})
        self.assertEqual(dev.mac, "AA:BB:CC:DD:EE:FF")


class DbusPathTest(unittest.TestCase):
    def test_dbus_devices_are_used_when_available(self):
        dev = bluez.SonyDevice("AA:BB:CC:DD:EE:FF", "WH-1000XM5", True, True, False)
        with mock.patch.object(bluez, "_dbus_devices", return_value=[dev]), \
                mock.patch.object(bluez, "_run", side_effect=AssertionError("bluetoothctl must not run")), \
                mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(bluez.pick_device(), dev)

    def test_explicit_mac_matches_case_insensitively(self):
        dev = bluez.SonyDevice("AA:BB:CC:DD:EE:FF", "WH-1000XM5", True, True, False)
        with mock.patch.object(bluez, "_dbus_devices", return_value=[dev]):
            self.assertEqual(bluez.pick_device("aa:bb:cc:dd:ee:ff"), dev)


if __name__ == "__main__":
    unittest.main()

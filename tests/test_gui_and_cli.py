import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from sonyeq import bluez, cli, headset
from sonyeq.headset import Headset, HeadsetError

try:
    from sonyeq import gui
except (ImportError, ValueError):  # no PyGObject: the CLI still works, skip the GUI helpers
    gui = None


def dev(mac="AA:BB:CC:DD:EE:FF", name="WH-1000XM5", connected=False, v2=True, v1=False):
    return bluez.SonyDevice(mac, name, connected, v2, v1)


@unittest.skipIf(gui is None, "PyGObject not installed")
class ExplainTest(unittest.TestCase):
    def explain(self, devices, err=HeadsetError("x", disconnected=True), mac=None):
        with mock.patch.object(bluez, "sony_devices", return_value=devices):
            return gui.explain(err, Headset(mac))

    def test_nothing_paired(self):
        self.assertEqual(self.explain([]).kind, "none")

    def test_only_older_models(self):
        a = self.explain([dev(name="WH-1000XM4", v2=False, v1=True)])
        self.assertEqual((a.kind, a.device.name), ("unsupported", "WH-1000XM4"))

    def test_paired_but_off(self):
        a = self.explain([dev()])
        self.assertEqual((a.kind, a.device.mac), ("disconnected", "AA:BB:CC:DD:EE:FF"))

    def test_connected_but_channel_failed(self):
        self.assertEqual(self.explain([dev(connected=True)]).kind, "unreachable")

    def test_command_failure_is_unreachable_without_asking_bluez(self):
        with mock.patch.object(bluez, "sony_devices", side_effect=AssertionError):
            self.assertEqual(gui.explain(HeadsetError("no reply"), Headset()).kind, "unreachable")

    def test_fixed_mac_that_bluez_forgot(self):
        self.assertEqual(self.explain([dev()], mac="11:22:33:44:55:66").kind, "none")

    def test_fixed_mac_picks_that_headset(self):
        a = self.explain([dev(connected=True), dev(mac="11:22:33:44:55:66", name="WF-1000XM5")],
                         mac="11:22:33:44:55:66")
        self.assertEqual((a.kind, a.device.name), ("disconnected", "WF-1000XM5"))

    def test_bluetoothctl_missing(self):
        with mock.patch.object(bluez, "sony_devices", side_effect=bluez.DeviceError("bluetoothctl not found")):
            a = gui.explain(HeadsetError("x", disconnected=True), Headset())
        self.assertEqual((a.kind, a.message), ("none", "bluetoothctl not found"))

    def test_concerns(self):
        d = dev()
        self.assertTrue(gui.concerns(d, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"))
        self.assertFalse(gui.concerns(d, "/org/bluez/hci0/dev_11_22_33_44_55_66"))
        self.assertTrue(gui.concerns(None, "/org/bluez/hci0/dev_11_22_33_44_55_66"))


class LockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.dir.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_second_holder_waits_then_gives_up(self):
        with headset.exclusive():
            with self.assertRaisesRegex(HeadsetError, "another program"):
                with headset.exclusive(timeout=0.2):
                    pass

    def test_released_after_use(self):
        with headset.exclusive():
            pass
        with headset.exclusive(timeout=0):
            pass

    def test_released_when_body_raises(self):
        with self.assertRaises(KeyError), headset.exclusive():
            raise KeyError
        with headset.exclusive(timeout=0):
            pass


class JsonCliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(list(argv))
            raise SystemExit(0)
        return ctx.exception.code, json.loads(out.getvalue())

    def test_error_is_json_with_disconnected_flag(self):
        err = HeadsetError("WH-1000XM5 is paired but not connected", disconnected=True)
        with mock.patch.object(Headset, "snapshot", side_effect=err):
            code, data = self.run_cli("--json", "status")
        self.assertEqual(code, 1)
        self.assertEqual(data, {"error": str(err), "disconnected": True})

    def test_anc_reports_new_state(self):
        from sonyeq.ncasm import NcAsmState
        with mock.patch.object(Headset, "set_ncasm", return_value=NcAsmState(True, True, False, 7)) as set_ncasm:
            code, data = self.run_cli("--json", "anc", "ambient")
        set_ncasm.assert_called_once_with("ambient", None, None)
        self.assertEqual((code, data), (0, {"mode": "ambient", "level": 7, "voice": False}))


if __name__ == "__main__":
    unittest.main()


class ConnectTest(unittest.TestCase):
    def test_connects_over_dbus(self):
        bus = mock.Mock()
        objects = {"/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {"org.bluez.Device1": {"Address": "AA:BB:CC:DD:EE:FF"}}}
        with mock.patch.object(bluez, "_bluez_objects", return_value=(bus, objects)):
            self.assertTrue(bluez.connect("aa:bb:cc:dd:ee:ff"))
        self.assertEqual(bus.call_sync.call_args.args[1:4],
                         ("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Connect"))

    def test_unknown_device(self):
        with mock.patch.object(bluez, "_bluez_objects", return_value=(mock.Mock(), {})), \
                self.assertRaisesRegex(bluez.DeviceError, "not known"):
            bluez.connect("AA:BB:CC:DD:EE:FF")

    def test_falls_back_to_bluetoothctl(self):
        with mock.patch.object(bluez, "_bluez_objects", return_value=None), \
                mock.patch.object(bluez, "_run", return_value="Attempting\nConnection successful\n") as run:
            self.assertTrue(bluez.connect("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(run.call_args.args, ("bluetoothctl", "connect", "AA:BB:CC:DD:EE:FF"))

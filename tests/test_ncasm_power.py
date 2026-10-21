import unittest

from sonyeq import ncasm, power


class NcAsmTest(unittest.TestCase):
    def test_parse_original_state(self):
        s = ncasm.parse_param(bytes.fromhex("67170101" "00000c"))
        self.assertEqual((s.mode, s.voice, s.level), ("nc", False, 12))

    def test_set_ambient_voice_matches_verified_frame(self):
        s = ncasm.NcAsmState(enabled=True, ambient=True, voice=True, level=10)
        self.assertEqual(ncasm.set_param(s).hex(" "), "68 17 01 01 01 01 0a")

    def test_off_keeps_ambient_settings(self):
        s = ncasm.with_mode(ncasm.NcAsmState(True, True, True, 7), "off")
        self.assertEqual(ncasm.set_param(s).hex(" "), "68 17 01 00 01 01 07")
        self.assertEqual(s.mode, "off")

    def test_level_range(self):
        with self.assertRaises(ValueError):
            ncasm.set_param(ncasm.NcAsmState(True, True, False, 21))
        with self.assertRaises(ValueError):
            ncasm.set_param(ncasm.NcAsmState(True, True, False, 0))

    def test_parse_notify(self):
        s = ncasm.parse_param(bytes.fromhex("69170101010014"))
        self.assertEqual((s.mode, s.level), ("ambient", 20))


class PowerTest(unittest.TestCase):
    def test_parse_battery(self):
        b = power.parse_battery(bytes.fromhex("23005700"))
        self.assertEqual((b.percent, b.charging), (87, "not charging"))


if __name__ == "__main__":
    unittest.main()

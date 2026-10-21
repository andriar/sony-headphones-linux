import unittest

from sonyeq import eq

XM5 = eq.parse_capability(bytes.fromhex(eq.XM5_CAPS_RAW.replace(" ", "")))


class EqCapsTest(unittest.TestCase):
    def test_xm5_capability(self):
        self.assertEqual((XM5.bands, XM5.steps, XM5.min_db, XM5.max_db), (6, 21, -10, 10))
        self.assertEqual(XM5.slot_names, ("clear-bass", "400", "1k", "2.5k", "6.3k", "16k"))
        self.assertEqual(XM5.preset_names[:3], ["off", "bright", "excited"])
        self.assertEqual(set(XM5.custom_slots), {"custom", "custom1", "custom2"})

    def test_generic_layouts(self):
        self.assertEqual(eq.EqCaps(5, 21, ()).slot_names, eq.FIVE_BANDS)
        self.assertEqual(eq.EqCaps(10, 13, ()).slot_names[0], "band1")
        self.assertEqual((eq.EqCaps(10, 13, ()).min_db, eq.EqCaps(10, 13, ()).max_db), (-6, 6))


class EqPayloadTest(unittest.TestCase):
    def test_set_preset_matches_verified_frame(self):
        self.assertEqual(eq.set_preset(eq.PRESETS["bright"]).hex(" "), "58 00 10 00")

    def test_set_levels_matches_verified_frame(self):
        self.assertEqual(eq.set_levels(XM5, [1, 2, 3, 4, 5, 6]).hex(" "), "58 00 a0 06 0b 0c 0d 0e 0f 10")

    def test_set_levels_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            eq.set_levels(XM5, [0, 0, 0, 0, 0, 11])

    def test_set_levels_rejects_wrong_count(self):
        with self.assertRaises(ValueError):
            eq.set_levels(XM5, [0, 0, 0, 0, 0])

    def test_set_levels_rejects_non_custom_slot(self):
        with self.assertRaises(ValueError):
            eq.set_levels(XM5, [0] * 6, slot=eq.PRESETS["bright"])

    def test_set_levels_rejects_slot_headset_lacks(self):
        no_user_slots = eq.EqCaps(6, 21, (0x00, 0xA0))
        with self.assertRaises(ValueError):
            eq.set_levels(no_user_slots, [0] * 6, slot=eq.CUSTOM_SLOTS["custom1"])

    def test_parse_param_custom1_readback(self):
        state = eq.parse_param(XM5, bytes.fromhex("5700a1060d050a0a0a0a"))
        self.assertEqual(state.preset_name, "custom1")
        self.assertEqual(state.levels, (3, -5, 0, 0, 0, 0))

    def test_parse_notify_bright(self):
        state = eq.parse_param(XM5, bytes.fromhex("590010060" "90a0f111113"))
        self.assertEqual(state.levels, (-1, 0, 5, 7, 7, 9))


if __name__ == "__main__":
    unittest.main()

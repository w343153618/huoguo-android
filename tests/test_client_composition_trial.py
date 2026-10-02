import unittest
from unittest.mock import patch
from scripts.probes.trial_client_composition import parse_disable_overlays, with_restored_flag


class ClientCompositionTrialCheck(unittest.TestCase):
    def test_fifth_word_and_not_address_or_other_flags(self):
        raw = "Result: Parcel(\t\n0x00000000: 00000001 00000000 00000001 00000000 '................'\n0x00000010: 00000000                            '....            ')\n"
        self.assertEqual(parse_disable_overlays(raw), 0)
        self.assertEqual(parse_disable_overlays(raw.replace("00000000                            '....", "00000001                            '....")), 1)

    def test_permission_error_is_not_off_setting(self):
        with self.assertRaises(ValueError):
            parse_disable_overlays('Result: Parcel(Error: 0xffffffffffffffff "Operation not permitted")')
        with self.assertRaises(ValueError):
            parse_disable_overlays("Result: Parcel(00000000)")

    def test_original_setting_restored_on_failed_experiment(self):
        with patch('scripts.probes.trial_client_composition.write_flag') as write:
            with self.assertRaises(RuntimeError):
                with_restored_flag('adb', 0, lambda: (_ for _ in ()).throw(RuntimeError('probe_failed')))
            write.assert_called_once_with('adb', 0)


if __name__ == '__main__':
    unittest.main()

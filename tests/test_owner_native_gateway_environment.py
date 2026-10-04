"""Pure fixed child environment; not actual private gateway execution."""
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.probes import owner_native_gateway_environment as environment


class EnvironmentChecks(unittest.TestCase):
    def setUp(self):
        self.home = Path('/Users/inert'); self.adb = self.home/'Library/Android/sdk/platform-tools/adb'
        self.original = dict(DIRECT_AUTH_FILE='/restricted/auth.json',
            DIRECT_CERT='/restricted/cert.pem', DIRECT_KEY='/restricted/key.pem',
            DIRECT_VIDEO_BACKEND='videotoolbox')

    def selected(self):
        return environment.select(self.home, self.adb, Path('/private/scope/state'),
                                  Path('/private/scope/evidence'), self.original)

    def test_inert_fixed_references_and_PATH_no_network_read_or_mutation(self):
        with patch.object(Path, 'open') as opened:
            result = self.selected(); opened.assert_not_called()
        self.assertEqual(result['DIRECT_AUTH_FILE'], self.original['DIRECT_AUTH_FILE'])
        self.assertEqual(result['DIRECT_EXTERNAL_VM'], '1')
        self.assertEqual(result['DIRECT_AVD'], 'RemoteAndroid17Compare')
        self.assertEqual(result['PATH'].split(':')[0], str(self.adb.parent))
        self.assertEqual(set(self.original), {'DIRECT_AUTH_FILE','DIRECT_CERT','DIRECT_KEY','DIRECT_VIDEO_BACKEND'})

    def test_inherited_Python_loader_proxy_and_DIRECT_overrides_are_not_inputs(self):
        import os
        with patch.dict(os.environ, PYTHONPATH='/foreign', DYLD_INSERT_LIBRARIES='/foreign',
                        PATH='/foreign', HTTPS_PROXY='http://foreign', DIRECT_RAW_QUEUE_POLICY='latest'):
            env = self.selected()
        for name in ('PYTHONPATH','PYTHONHOME','DYLD_INSERT_LIBRARIES','HTTPS_PROXY','DIRECT_RAW_QUEUE_POLICY'):
            self.assertNotIn(name, env)
        self.assertNotIn('/foreign', env['PATH'])

    def test_foreign_or_unclosed_original_environment_refused(self):
        for key, value in (('DIRECT_VIDEO_BACKEND','guest'), ('DIRECT_AUTH_FILE','relative'),
                           ('DIRECT_AUTH_FILE','/private/\0secret'), ('DIRECT_RAW_QUEUE_POLICY','latest')):
            old = self.original.copy(); self.original[key] = value
            with self.assertRaises(ValueError): self.selected()
            self.original = old

    def test_foreign_ADB_and_relative_scope_refused(self):
        for args in ((self.home, Path('/foreign/adb'), Path('/private/state'), Path('/private/evidence')),
                     (self.home, self.adb, Path('relative'), Path('/private/evidence'))):
            with self.assertRaises(ValueError): environment.select(*args,self.original)


if __name__ == '__main__': unittest.main()

"""Actual native mapping paths against pinned local FEC; no device/network/media test."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'experiments/moonlight-v2/transport/android-udp'
spec = importlib.util.spec_from_file_location('mapping_pinned_native_build', NATIVE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeMappingDiagnosticsChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = builder.default_source(os.environ)
        if not cls.source.is_dir() or not shutil.which('cc') or not shutil.which('c++'):
            raise unittest.SkipTest('Existing pinned local nanors and C/C++ compilers required; nothing downloaded')
        builder.verify_dependencies(cls.source, builder.load_lock())
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-mapping-offline-')
        cls.addClassCleanup(cls.folder.cleanup)
        temporary = Path(cls.folder.name)
        includes = ['-isystem', str(cls.source / 'nanors'), '-isystem', str(cls.source / 'nanors/deps/obl')]
        objects = []
        for index, source in enumerate(('rs.c', 'deps/obl/oblas_common.c', 'deps/obl/oblas_lite.c')):
            target = temporary / ('fec-' + str(index) + '.o')
            subprocess.run(['cc', '-O2', *includes, '-c', str(cls.source / 'nanors' / source), '-o', str(target)],
                           check=True, capture_output=True, timeout=30)
            objects.append(str(target))
        cls.binary = temporary / 'mapping-check'
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror', '-O2',
                        '-I', str(NATIVE), *includes, str(ROOT / 'tests/native/udp_mapping_diagnostics.cpp'),
                        *objects, '-o', str(cls.binary)], check=True, capture_output=True, timeout=30)

    def case(self, name):
        result = subprocess.run([str(self.binary), name], check=True, capture_output=True, text=True, timeout=5)
        value = json.loads(result.stdout)
        self.assertTrue(value['passed'])
        self.assertGreater(value['checks'], 20)
        self.assertEqual(value['case'], name)
        self.assertEqual(value['detail_values'], 292)
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['scope'], 'offline_actual_native_mapping_synthetic_bodies_no_phone_or_codec')

    def test_256_frame_boundary_and_settled_precedence(self):
        self.case('old')

    def test_same_header_mismatch_and_unextended_deadline(self):
        self.case('mismatch')

    def test_capacity_decision_context_and_non_destructive_snapshot(self):
        self.case('capacity')

    def test_core_dropped_adapter_slots_and_later_first_admitted_grant(self):
        self.case('lifecycle')

    def test_fixed_ring_cache_evictions_and_clear_coverage(self):
        self.case('bounded')

    def test_default_disabled_and_reenable_missing_coverage(self):
        self.case('disabled')

    def test_diagnostics_on_off_preserves_legacy_stats_and_bodies(self):
        self.case('unchanged')


if __name__ == '__main__':
    unittest.main()

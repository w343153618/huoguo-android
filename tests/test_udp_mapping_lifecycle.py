"""Actual native lifecycle contracts using existing pinned FEC; no devices or network."""
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
spec = importlib.util.spec_from_file_location('lifecycle_pinned_native_build', NATIVE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class NativeMappingLifecycleChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = builder.default_source(os.environ)
        if not cls.source.is_dir() or not shutil.which('cc') or not shutil.which('c++'):
            raise unittest.SkipTest('Existing pinned local nanors/C++ compiler required; nothing downloaded')
        builder.verify_dependencies(cls.source, builder.load_lock())
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-lifecycle-offline-')
        cls.addClassCleanup(cls.folder.cleanup)
        temporary = Path(cls.folder.name)
        includes = ['-isystem', str(cls.source / 'nanors'), '-isystem', str(cls.source / 'nanors/deps/obl')]
        objects = []
        for index, source in enumerate(('rs.c', 'deps/obl/oblas_common.c', 'deps/obl/oblas_lite.c')):
            target = temporary / ('fec-' + str(index) + '.o')
            subprocess.run(['cc', '-O2', *includes, '-c', str(cls.source / 'nanors' / source), '-o', str(target)],
                           check=True, capture_output=True, timeout=30)
            objects.append(str(target))
        cls.binary = temporary / 'lifecycle-check'
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror', '-O2',
                        '-I', str(NATIVE), *includes, str(ROOT / 'tests/native/udp_mapping_lifecycle.cpp'),
                        *objects, '-o', str(cls.binary)], check=True, capture_output=True, timeout=30)

    def case(self, selected):
        result = subprocess.run([str(self.binary), selected], check=True, capture_output=True, text=True, timeout=10)
        value = json.loads(result.stdout)
        self.assertTrue(value['passed'])
        self.assertGreater(value['checks'], 20)
        self.assertEqual(value['case'], selected)
        self.assertEqual(value['lifecycle_values'], 240)
        self.assertEqual(value['legacy_mapping_values'], 292)
        self.assertEqual(value['legacy_stats_values'], 21)
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['scope'], 'offline_actual_native_lifecycle_no_phone_network_or_codec')

    def test_four_actual_core_settlement_reasons_and_default_no_observer(self):
        self.case('core')

    def test_core_pending_complete_settled_and_delivered_advance_states(self):
        self.case('states')

    def test_exact_dropped_mapping_occupants_and_historical_decision_snapshot(self):
        self.case('capacity')

    def test_live_incomplete_core_slots_not_inferred_settled(self):
        self.case('pending')

    def test_79999_80000_and_short_legal_grants_keep_original_deadline(self):
        self.case('deadlines')

    def test_independent_fixed_rings_dedup_eviction_disable_and_reenable(self):
        self.case('bounded')

    def test_off_coverage_no_reconstructed_settle_time_and_legacy_switch_independence(self):
        self.case('off')

    def test_core_delivered_is_not_logical_parser_acceptance(self):
        self.case('logical')

    def test_existing_output_exception_before_settle_remains_visible(self):
        self.case('exception')

    def test_lifecycle_observation_preserves_actual_legacy_outputs_stats_and_events(self):
        self.case('unchanged')


if __name__ == '__main__':
    unittest.main()

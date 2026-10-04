"""Pure plan/pin fixtures; no device, signing key, filesystem or media access."""
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock, patch

from scripts.probes import owner_native_diagnostic_preflight as preflight
from udp_lan_sessions import UdpLanSessions, parse_udp_settings


def plan_input():
    return dict(schema=1, app_source_commit='f' * 40, app_sha256='a' * 64,
        app_version_code=40, jni_sha256='b' * 64, helper_sha256='c' * 64,
        signer_sha256=preflight.SIGNER, **preflight.PACKAGE_BINDINGS,
        network_scope='lan', node='m1',
        guest_serial='emulator-5556', guest_avd='RemoteAndroid17Compare',
        https_port=45560, udp_port=45963, process_max_seconds=600,
        sample_seconds=30)


class OwnerNativePreflightChecks(unittest.TestCase):
    def test_default_off_does_not_inspect_other_inputs(self):
        result = preflight.qualify(phone_readback=object())
        self.assertEqual(result['status'], 'off')
        self.assertIsNone(result['artifact_match'])
        self.assertFalse(result['diagnostic_events_requested'])

    def test_exact_synthetic_pins_match_only_never_grant_execution(self):
        plan = preflight.parse_plan(plan_input())
        result = preflight.qualify(plan, plan.artifacts(), caller_verified_readback=True)
        self.assertTrue(result['artifact_match'])
        self.assertEqual(result['status'], 'artifact_pins_match_only')
        for key in ('sample_eligible', 'diagnostic_events_requested',
                    'native_collection_verified', 'operator_permission_verified',
                    'numeric_export_schema_verified',
                    'current_App_attempt_verified', 'server_guest_lease_verified',
                    'private_evidence_verified', 'host_raw_trace_requested'):
            self.assertFalse(result[key])
        self.assertEqual((result['report_bytes_max'], result['export_rows_max'],
                          result['native_ring_capacity'], result['java_ring_capacity']),
                         (65536, 64, 256, 8192))
        self.assertNotIn('app_sha256', result)

    def test_unverified_default_does_not_infer_provenance_from_JSON(self):
        plan = preflight.parse_plan(plan_input())
        result = preflight.qualify(plan, {'caller_verified_readback': True})
        self.assertEqual(result['status'], 'readback_unverified')
        self.assertIsNone(result['artifact_match'])

    def test_readback_match_is_not_derived_from_version_or_helper_alone(self):
        plan = preflight.parse_plan(plan_input())
        for key, value in (('app_sha256', 'd' * 64), ('jni_sha256', 'd' * 64),
                           ('helper_sha256', 'd' * 64), ('app_version_code', 41)):
            pins = dict(plan.artifacts(), **{key: value})
            with self.subTest(key=key):
                result = preflight.qualify(plan, pins, caller_verified_readback=True)
                self.assertFalse(result['artifact_match'])
                self.assertEqual(result['status'], 'artifact_mismatch')

    def test_M5_Tailnet_NPS_public_and_wrong_guest_plans_are_rejected(self):
        for key, value in (('network_scope', 'nps_owner'), ('network_scope', 'tailnet'),
                           ('node', 'm5'), ('guest_serial', 'emulator-5558'),
                           ('guest_avd', 'RemoteAndroid17M5'), ('https_port', 49556),
                           ('udp_port', 15556), ('udp_port', 8025)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                preflight.parse_plan(dict(plan_input(), **{key: value}))

    def test_false_finite_budgets_and_excess_sample_rejected(self):
        for key, values in (('process_max_seconds', (None, True, 0, 29, 3601, '600', 600.0)),
                            ('sample_seconds', (None, True, 0, 31, '30', 30.0)),
                            ('https_port', (True, 45560.0, '45560')),
                            ('schema', (True, 1.0, 2))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    preflight.parse_plan(dict(plan_input(), **{key: value}))

    def test_missing_excess_and_HTTP_like_plan_fields_rejected(self):
        for value in (None, [], {}, dict(plan_input(), diagnostic_events=True),
                      dict(plan_input(), account='wyw'),
                      dict(plan_input(), diagnostic_events_requested=True)):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                preflight.parse_plan(value)
        value = plan_input(); del value['helper_sha256']
        with self.assertRaises(ValueError): preflight.parse_plan(value)

    def test_old_installed_and_public_App_cannot_qualify(self):
        for digest in preflight.OLD_APP_HASHES:
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, 'new_numeric_export'):
                preflight.parse_plan(dict(plan_input(), app_sha256=digest))
            plan = preflight.parse_plan(plan_input())
            with self.assertRaisesRegex(ValueError, 'new_numeric_export'):
                preflight.qualify(plan, dict(plan.artifacts(), app_sha256=digest),
                                  caller_verified_readback=True)

    def test_original_signer_and_digest_types_exact(self):
        for key, value in (('signer_sha256', 'd' * 64), ('app_sha256', 'A' * 64),
                           ('jni_sha256', 'x' * 64), ('helper_sha256', 'a' * 63),
                           ('app_source_commit', 'a' * 41), ('app_version_code', True),
                           ('app_version_code', 39)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight.parse_plan(dict(plan_input(), **{key: value}))

    def test_plan_cannot_bind_formal_App_or_another_instrumentation_target(self):
        for key in preflight.PACKAGE_BINDINGS:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'package_binding'):
                preflight.parse_plan(dict(plan_input(), **{key: 'local.remoteandroid.direct'}))

    def test_actual_phone_package_and_helper_target_are_required_not_hash_aliases(self):
        plan = preflight.parse_plan(plan_input())
        for key in preflight.PACKAGE_BINDINGS:
            pins = plan.artifacts(); del pins[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                preflight.qualify(plan, pins, caller_verified_readback=True)
            for value in (True, 'local.remoteandroid.direct'):
                pins = dict(plan.artifacts(), **{key: value})
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    preflight.qualify(plan, pins, caller_verified_readback=True)

    def test_closed_actual_readback_and_boolean_qualification_required(self):
        plan = preflight.parse_plan(plan_input())
        for value in (None, {}, dict(plan.artifacts(), permission=True)):
            with self.assertRaises(ValueError):
                preflight.qualify(plan, value, caller_verified_readback=True)
        for value in (1, 'true', None):
            with self.assertRaises(ValueError):
                preflight.qualify(plan, plan.artifacts(), caller_verified_readback=value)
        with self.assertRaises(ValueError):
            preflight.qualify(plan_input(), plan.artifacts(), caller_verified_readback=True)

    def test_caller_mutation_and_frozen_bypass_do_not_change_valid_bounds(self):
        value = plan_input(); plan = preflight.parse_plan(value)
        value['process_max_seconds'] = 0
        self.assertEqual(plan.process_max_seconds, 600)
        with self.assertRaises(FrozenInstanceError): plan.sample_seconds = 300
        object.__setattr__(plan, 'sample_seconds', 300)
        with self.assertRaises(ValueError): preflight.qualify(plan)

    def test_direct_Plan_constructor_cannot_bypass_validation(self):
        plan = preflight.parse_plan(plan_input())
        values = {key: getattr(plan, key) for key in preflight.Plan.__dataclass_fields__}
        with self.assertRaises(ValueError):
            preflight.Plan(**dict(values, process_max_seconds=0))

    def test_local_preflight_does_not_enable_existing_registry_HTTP_or_env(self):
        plan = preflight.parse_plan(plan_input())
        preflight.qualify(plan, plan.artifacts(), caller_verified_readback=True)
        registry, configs = UdpLanSessions('192.168.9.128'), []
        def factory(config):
            configs.append(config)
            return Mock()
        with patch.dict('os.environ', {'DIRECT_DIAGNOSTIC_EVENTS': 'true'}):
            descriptor = registry.create('wyw',
                {'diagnostic_events': True, 'max_fps': 30, 'buffer_ms': 80}, factory)
        self.assertFalse(descriptor['diagnostic_events'])
        self.assertFalse(configs[0]['diagnostic_events'])
        self.assertEqual((descriptor['fps'], descriptor['buffer_ms'],
                          descriptor['surface_submit_lead_ms']), (30, 80, 0))
        registry.cancel('wyw', descriptor['session'])

    def test_NPS_owner_HTTP_has_no_native_diagnostic_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'Closed NPS owner settings'):
            parse_udp_settings({'network_scope': 'nps_owner', 'node': 'm1',
                                'diagnostic_events': True})


if __name__ == '__main__':
    unittest.main()

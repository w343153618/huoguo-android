"""Inert, bounded artifact preflight for a future M1 LAN native-event trial.

The plan comes from a trusted local coordinator, never HTTP or an account name.
Matching metadata is neither operator permission nor an App/guest lease. This
module performs no reads or collection and is not wired into a live gateway.
"""
from dataclasses import dataclass
import re


SIGNER = '0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da'
OLD_APP_HASHES = frozenset((
    'a663c4d046f1048ae32b23875f8614039c73227a484835d6af024d71a8744f51',
    'd0437e51e8c2d0d27c89458b3a5e6467ee421f25337551d19ecc0992d18ecf08',
    '030126da900e16a11fb46dccbd1ed54edd5e0a7757a42503ca0ae40047c3d570',
))
DIGEST_FIELDS = frozenset(('app_sha256', 'jni_sha256', 'helper_sha256', 'signer_sha256'))
PACKAGE_BINDINGS = {
    'application_id': 'local.remoteandroid.direct.experiment',
    'helper_application_id': 'local.huoguo.lanuitest',
    'helper_target_package': 'local.remoteandroid.direct.experiment',
}
ARTIFACT_FIELDS = DIGEST_FIELDS | frozenset(('app_version_code',)) | frozenset(PACKAGE_BINDINGS)
PLAN_FIELDS = ARTIFACT_FIELDS | frozenset(('schema', 'app_source_commit',
    'network_scope', 'node', 'guest_serial', 'guest_avd', 'https_port',
    'udp_port', 'process_max_seconds', 'sample_seconds'))


def _closed(value, fields, error):
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ValueError(error)


def _integer(value, lower, upper, error):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(error)


def _digest(value, length, error):
    if type(value) is not str or len(value) != length or re.fullmatch('[0-9a-f]+', value) is None:
        raise ValueError(error)


def _artifacts(value):
    _closed(value, ARTIFACT_FIELDS, 'closed_artifact_pins_required')
    _integer(value['app_version_code'], 40, 2**31 - 1, 'candidate_version_required')
    for key in DIGEST_FIELDS:
        _digest(value[key], 64, 'artifact_digest_required')
    for key, expected in PACKAGE_BINDINGS.items():
        if type(value[key]) is not str or value[key] != expected:
            raise ValueError('exact_experiment_helper_package_binding_required')
    if value['signer_sha256'] != SIGNER:
        raise ValueError('original_signer_required')
    if value['app_sha256'] in OLD_APP_HASHES:
        raise ValueError('new_numeric_export_App_required')


@dataclass(frozen=True)
class Plan:
    """Validated expectations only; do not deserialize this as permission."""
    app_source_commit: str
    app_sha256: str
    app_version_code: int
    jni_sha256: str
    helper_sha256: str
    signer_sha256: str
    application_id: str
    helper_application_id: str
    helper_target_package: str
    process_max_seconds: int
    sample_seconds: int

    def __post_init__(self):
        # Direct construction cannot bypass the same type/pin bounds as parse.
        _digest(self.app_source_commit, 40, 'candidate_source_commit_required')
        _artifacts(self.artifacts())
        _integer(self.process_max_seconds, 30, 3600, 'finite_process_budget_required')
        _integer(self.sample_seconds, 1, 30, 'single_sample_bound_required')

    def artifacts(self):
        return {key: getattr(self, key) for key in ARTIFACT_FIELDS}


def parse_plan(value):
    """Closed local expectations; NPS/Tailnet/M5 and public ports are refused."""
    _closed(value, PLAN_FIELDS, 'closed_owner_native_plan_required')
    _integer(value['schema'], 1, 1, 'owner_native_plan_schema_required')
    for key, expected in (('network_scope', 'lan'), ('node', 'm1'),
                          ('guest_serial', 'emulator-5556'),
                          ('guest_avd', 'RemoteAndroid17Compare')):
        if type(value[key]) is not str or value[key] != expected:
            raise ValueError('explicit_M1_LAN_candidate_required')
    for key, expected in (('https_port', 45560), ('udp_port', 45963)):
        _integer(value[key], expected, expected, 'dedicated_candidate_ports_required')
    return Plan(**{key: value[key] for key in Plan.__dataclass_fields__})


def qualify(plan=None, phone_readback=None, *, caller_verified_readback=False):
    """Check supplied exact artifact pins without reading a device or a file.

    The caller must obtain actual package/APK/JNI/helper/signature reads and
    establish their provenance outside this function. No true field in JSON is
    accepted as that provenance. Fresh device/CPU/current Attempt, private
    evidence, formal protection and actual server admission remain separate.
    """
    if type(caller_verified_readback) is not bool:
        raise ValueError('explicit_readback_qualification_required')
    result = dict(schema=1, status='off', artifact_match=None,
        diagnostic_events_requested=False, native_collection_verified=False,
        numeric_export_schema_verified=False,
        operator_permission_verified=False, current_App_attempt_verified=False,
        server_guest_lease_verified=False, private_evidence_verified=False,
        sample_eligible=False, report_bytes_max=65536, export_rows_max=64,
        native_ring_capacity=256, java_ring_capacity=8192,
        host_raw_trace_requested=False)
    if plan is None:
        return result
    if type(plan) is not Plan:
        raise ValueError('parsed_local_plan_required')
    # Recheck even a frozen Plan: object.__setattr__ is not a security boundary.
    plan.__post_init__()
    if not caller_verified_readback:
        result['status'] = 'readback_unverified'
        return result
    _artifacts(phone_readback)
    result['artifact_match'] = phone_readback == plan.artifacts()
    result['status'] = 'artifact_pins_match_only' if result['artifact_match'] else 'artifact_mismatch'
    return result

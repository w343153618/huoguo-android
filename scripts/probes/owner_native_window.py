"""Inert selection/readback for one M1 LAN native-event observation.

Only a trusted local caller supplies Window; no CLI/HTTP/environment opt-in.
Plan/pin/JSON matches do not establish operator permission or a server lease.
This adapter never reads files, devices, passwords or starts a process itself.
"""
from dataclasses import dataclass

from scripts.probes.owner_native_diagnostic_preflight import Plan
from scripts.probes import native_frame_progress_analysis

APP_SHA256 = 'f97b3e3735eb729bf28c086c92cb9a4147093cd0ed05a3aeb456456c42419779'
JNI_SHA256 = '447f4928515aa851520ec02b2f2b2efe9cfea1dc475dc29d3413145611743dc5'
# Actual matching helper build; this pin does not authorize installation.
HELPER_SHA256 = '28db54c6582fa6c855cb20eeb2163b2aa9bcccf8d4d675d111a8c98f54a9e425'
MAX_NS = (1 << 63) - 1


@dataclass(frozen=True)
class Window:
    plan: Plan
    seconds: int = 5

    def __post_init__(self):
        if type(self.plan) is not Plan:
            raise ValueError('native_window_local_Plan_required')
        self.plan.__post_init__()
        if (self.plan.app_sha256 != APP_SHA256 or self.plan.jni_sha256 != JNI_SHA256
                or HELPER_SHA256 is None or self.plan.helper_sha256 != HELPER_SHA256):
            raise ValueError('native_window_matching_artifacts_required')
        if self.plan.sample_seconds != 30 or type(self.seconds) is not int or not 1 <= self.seconds <= 10:
            raise ValueError('native_window_duration_required')


def select(args, window=None):
    """Private Python call only; existing CLI saved-LAN remains refused."""
    if window is None:
        return False
    if type(window) is not Window:
        raise ValueError('native_window_local_selection_required')
    window.__post_init__()
    if (args.network_scope != 'lan' or args.node is not None or args.guest not in (None, 'emulator-5556')
            or not args.media_only or args.phone_only_sampler or args.credential_source != 'saved-ui'
            or args.credential_save != 'off' or args.v50_profile != 'on' or args.pcm_queue != 'off'
            or args.codec_startup != 'off' or args.surface_submit_lead_ms != 0
            or args.source_input != 'off' or args.owner_source_guard != 'off'
            or args.source_stats_window != 'off' or args.source_input_identity is not None
            or args.source_snapshot_deployment is not None or args.source_frame_deployment is not None):
        raise ValueError('native_window_exact_M1_saved_LAN_required')
    return True


def _ns(value):
    if type(value) is not int or not 0 < value <= MAX_NS:
        raise ValueError('native_window_phone_clock_required')
    return value


def helper_readback(value, window):
    """Numeric qualification only; independently owned Popen/Attempt still needed.

    A stalled worker/callback is retained, not relabeled zero network loss or
    rejected merely for lacking progress. Running/current-Attempt coverage and
    normal UI exit are separate required observations.
    """
    window.__post_init__()
    if type(value) is not dict or 'failure_class' in value:
        return False
    flags = ('requested_owner_native_window', 'native_window_current_attempt_observed',
        'native_window_diagnostic_events_observed', 'native_window_no_reconnect',
        'native_window_no_source_input', 'native_window_no_UiAutomation')
    if (any(value.get(k) is not True for k in flags)
            or value.get('native_window_server_atomic_hold_verified') is not False
            or value.get('native_window_is_presented_FPS') is not False
            or any(k in value for k in ('normal_UI_reconnected_received_media',
                'steady_media_started_ns', 'steady_sampler_completion_observed'))
            or type(value.get('requested_owner_native_window_seconds')) is not int
            or value['requested_owner_native_window_seconds'] != window.seconds):
        return False
    try:
        click, started, ended, lease = (_ns(value.get(k)) for k in (
            'native_window_click_ns', 'native_window_started_ns',
            'native_window_finished_ns', 'native_window_budget_end_ns'))
        if (type(value.get('native_window_descriptor_seconds')) is not int
                or value['native_window_descriptor_seconds'] != 30
                or type(value.get('native_window_close_margin_ns')) is not int
                or value['native_window_close_margin_ns'] != 8_000_000_000
                or not click <= started <= ended <= lease - 8_000_000_000
                or lease - click != 30_000_000_000 or ended - started < window.seconds * 1_000_000_000):
            return False
        rows = value.get('native_window_samples')
        if type(rows) is not list or not 2 <= len(rows) <= 44:
            return False
        previous = None
        for row in rows:
            if type(row) is not dict or set(row) != {'phone_ns', 'worker_received_frames', 'codec_callback_count'}:
                return False
            now = _ns(row['phone_ns'])
            if not started <= now <= ended:
                return False
            for k in ('worker_received_frames', 'codec_callback_count'):
                if type(row[k]) is not int or not 0 <= row[k] <= MAX_NS:
                    return False
            if previous is not None and (now <= previous['phone_ns'] or any(
                    row[k] < previous[k] for k in ('worker_received_frames', 'codec_callback_count'))):
                return False
            previous = row
        return rows[-1]['phone_ns'] == ended and rows[-1]['phone_ns'] - rows[0]['phone_ns'] >= window.seconds * 1_000_000_000
    except (ValueError, KeyError):
        return False


def numeric_report(value):
    """Validate existing numeric export without claiming JSON establishes identity."""
    if type(value) is not dict or type(value.get('audio_cleanup_confirmed')) is not int or value['audio_cleanup_confirmed'] != 1:
        raise ValueError('native_window_App_report_cleanup_unknown')
    rows = native_frame_progress_analysis.validate(value.get('native_frame_events_numeric'))
    if rows is None:
        raise ValueError('native_window_App_events_unavailable')
    return {'native_numeric_schema_verified': True, 'native_retained_rows': len(rows),
        'same_App_attempt_verified_by_JSON': False, 'physical_loss_or_presented_FPS_verified': False}

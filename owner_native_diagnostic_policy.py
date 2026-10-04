"""Trusted startup selection for an isolated M1 native-event trial, default OFF.

No CLI/HTTP/environment entry or device/file access is introduced here. A future
coordinator must separately qualify operator permission, private evidence, exact
phone artifacts/current Attempt and live admission before constructing a trial.
Artifact Plan metadata is not any of those permissions or a collection receipt.
"""
from scripts.probes.owner_native_diagnostic_preflight import Plan


GUEST_SERIAL = 'emulator-5556'
GUEST_AVD = 'RemoteAndroid17Compare'
FIXED_MEDIA = dict(max_size=960, fps=30, video_bit_rate=4_000_000,
    bitrate_mode='VBR', buffer_ms=80, surface_submit_lead_ms=0,
    audio_enabled=True, touch_enabled=True)


def validate_selection(plan, network_scope, peer_port):
    """Pure trusted constructor gate; None preserves every existing scope."""
    if plan is None:
        return None
    if type(plan) is not Plan:
        raise ValueError('local_native_diagnostic_Plan_required')
    plan.__post_init__()
    if (type(network_scope) is not str or network_scope != 'lan'
            or type(peer_port) is not int or peer_port != 45963):
        raise ValueError('isolated_M1_LAN_native_diagnostic_required')
    return plan


def select_options(options, plan):
    """Explicit trial-only selection; advertise a shortened bounded session."""
    if plan is None:
        return dict(options)
    validate_selection(plan, options.get('network_scope'), 45963)
    for key, expected in FIXED_MEDIA.items():
        if type(options.get(key)) is not type(expected) or options[key] != expected:
            raise ValueError('fixed_native_diagnostic_media_required')
    seconds = options.get('seconds')
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError('bounded_native_diagnostic_session_required')
    return dict(options, seconds=min(seconds, plan.sample_seconds))


def validate_worker(plan, config, guest_serial, guest_avd, *, capture_trace_dir,
                    raw_queue_policy, raw_submit_fps, enobufs_retry_enabled):
    """Fail before resources on descriptor/worker selection or guest mismatch."""
    enabled = config.get('diagnostic_events', False)
    if type(enabled) is not bool:
        raise ValueError('native_diagnostic_descriptor_boolean_required')
    if plan is None:
        if enabled:
            raise ValueError('native_diagnostic_requires_trusted_worker_selection')
        return
    validate_selection(plan, config.get('network_scope'), config.get('peer_port'))
    if (not enabled or guest_serial != GUEST_SERIAL or guest_avd != GUEST_AVD
            or config.get('guest_serial') != GUEST_SERIAL or config.get('guest_avd') != GUEST_AVD):
        raise ValueError('native_diagnostic_exact_M1_worker_required')
    selected = select_options(config, plan)
    if selected['seconds'] != config['seconds']:
        raise ValueError('native_diagnostic_worker_duration_mismatch')
    if (capture_trace_dir is not None or raw_queue_policy != 'fifo'
            or raw_submit_fps is not None or enobufs_retry_enabled):
        raise ValueError('native_diagnostic_requires_unchanged_host_policy')

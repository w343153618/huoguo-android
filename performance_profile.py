"""Opt-in guest drawing configuration, reapplied after each Android boot."""
import os
import threading

_lock = threading.Lock()
_applied = None


def apply_performance_profile(adb, environment=None):
    global _applied
    environment = os.environ if environment is None else environment
    rate = environment.get('DIRECT_REFRESH_RATE')
    renderer = environment.get('DIRECT_HWUI_RENDERER')
    if rate is None and renderer is None:
        return {'status': 'disabled'}
    if rate not in (None, '60', '120') or renderer not in (None, 'skiagl', 'skiavk'):
        raise ValueError('Unsupported guest drawing configuration')

    def call(*words):
        result = adb('shell', *words)
        return result.stdout.strip() if hasattr(result, 'stdout') else str(result).strip()

    with _lock:
        boot_id = call('cat', '/proc/sys/kernel/random/boot_id')
        signature = (boot_id, rate, renderer)
        if _applied == signature:
            return {'status': 'already_applied'}
        if renderer:
            call('su', '0', 'setprop', 'debug.hwui.renderer', renderer)
            if call('getprop', 'debug.hwui.renderer') != renderer:
                raise RuntimeError('Guest HWUI renderer was not applied')
        if rate:
            for key in ('peak_refresh_rate', 'min_refresh_rate'):
                call('settings', 'put', 'system', key, rate + '.0')
                if float(call('settings', 'get', 'system', key)) != float(rate):
                    raise RuntimeError('Guest refresh policy was not applied')
        _applied = signature
        return {'status': 'applied', 'refresh_policy_hz': rate, 'hwui_renderer': renderer}

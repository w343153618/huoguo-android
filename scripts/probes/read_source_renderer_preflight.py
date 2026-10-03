#!/usr/bin/env python3
"""Read-only source renderer/display metadata; never repairs or starts a stream.

Only the selected known emulator and YouTube package are queried. Subprocess
time/output bounds apply to each query and the entire sample. Raw dumps and
exception text are discarded; JSON contains fixed enums and bounded numbers.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


SERIALS = ('emulator-5556', 'emulator-5554')
PACKAGES = ('app.morphe.android.youtube', 'com.google.android.youtube')
RENDERERS = ('skiagl', 'skiavk')
RE_BACKENDS = ('skiagl', 'skiaglthreaded', 'skiavk', 'skiavkthreaded')
PIPELINES = {'skiagl': 'opengl', 'skiavk': 'vulkan'}


class ReadOnlyAdb:
    """Owns only bounded adb clients; timeout kills no device-side process."""
    def __init__(self, executable, serial, *, query_seconds=3.0, total_seconds=20.0):
        if serial not in SERIALS:
            raise ValueError('known_emulator_required')
        self.executable, self.serial = str(executable), serial
        self.query_seconds = query_seconds
        self.deadline = time.monotonic() + total_seconds

    def read(self, arguments, limit):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return 'sample_deadline', ''
        process = None
        try:
            process = subprocess.Popen([self.executable, '-s', self.serial, *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + min(self.query_seconds, remaining)
            data = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return 'query_timeout', ''
                    if not selector.select(remaining):
                        return 'query_timeout', ''
                    part = os.read(process.stdout.fileno(), min(8192, limit + 1 - len(data)))
                    if not part:
                        break
                    data.extend(part)
                    if len(data) > limit:
                        return 'output_limit', ''
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 'query_timeout', ''
            if process.wait(timeout=remaining) != 0:
                return 'command_failed', ''
            try:
                return 'ok', bytes(data).decode('utf-8').strip()
            except UnicodeDecodeError:
                return 'invalid_encoding', ''
        except subprocess.TimeoutExpired:
            return 'query_timeout', ''
        except OSError:
            return 'client_unavailable', ''
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()  # This probe's local adb client only.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                if process.stdout is not None:
                    process.stdout.close()


def property_enum(text, allowed, available):
    if not available:
        return 'unavailable'
    return text if text in allowed else ('empty' if not text else 'unrecognized')


def pids(text):
    if not text:
        return ()
    if re.fullmatch(r'[0-9]+(?:\s+[0-9]+){0,7}', text) is None:
        return None
    values = tuple(int(value) for value in text.split())
    return values if all(0 < value <= 4194304 for value in values) and len(set(values)) == len(values) else None


def dimensions(text):
    result = {}
    for name in ('Physical', 'Override'):
        matches = re.findall(r'^' + name + r' size: ([0-9]+)x([0-9]+)$', text, re.M)
        if len(matches) == 1:
            width, height = map(int, matches[0])
            if 1 <= width <= 16384 and 1 <= height <= 16384:
                result[name.lower()] = {'width': width, 'height': height}
    return result


def densities(text):
    result = {}
    for name in ('Physical', 'Override'):
        matches = re.findall(r'^' + name + r' density: ([0-9]+)$', text, re.M)
        if len(matches) == 1 and 64 <= int(matches[0]) <= 2000:
            result[name.lower()] = int(matches[0])
    return result


def active_mode(text):
    match = re.fullmatch(r'Active mode for display 0:\s*Mode ID: ([0-9]{1,5}), '
        r'Resolution: ([0-9]{1,5})x([0-9]{1,5}), Refresh Rate: ([0-9]+(?:\.[0-9]+)?) Hz', text)
    if match is None:
        return None
    mode_id, width, height = map(int, match.groups()[:3])
    hz = float(match[4])
    if width < 1 or height < 1 or width > 16384 or height > 16384 or not 1 <= hz <= 240:
        return None
    return {'id': mode_id, 'width': width, 'height': height, 'refresh_hz': hz}


def rotation(text):
    # Never infer display rotation from user_rotation: auto-rotation can differ.
    # Config summaries repeat mRotation=ROTATION_0. Use the actual numeric
    # DisplayRotation field in the unique display0 section, not overrideConfig.
    headers = list(re.finditer(r'^\s*Display: mDisplayId=([0-9]+)(?:\s|$)', text, re.M))
    display0 = [index for index, match in enumerate(headers) if match[1] == '0']
    if len(display0) != 1:
        return None
    index = display0[0]
    section = text[headers[index].end():headers[index+1].start() if index+1 < len(headers) else len(text)]
    values = re.findall(r'^\s*mRotation=([0-3])\s+mDeferredRotationPauseCount=[0-9]+\b', section, re.M)
    return int(values[0]) if len(values) == 1 else None


def pipeline(text, package, expected_pid):
    headers = re.findall(r'^\*\* Graphics info for pid ([0-9]+) \[([^\]\n]+)\] \*\*$', text, re.M)
    if headers != [(str(expected_pid), package)]:
        return 'unavailable'
    values = re.findall(r'^\s*Pipeline=Skia \((OpenGL|Vulkan)\)\s*$', text, re.M)
    return {'OpenGL': 'opengl', 'Vulkan': 'vulkan'}[values[0]] if len(values) == 1 else 'unavailable'


def collect(reader, serial, package, expected_renderer):
    if serial not in SERIALS or package not in PACKAGES or expected_renderer not in RENDERERS:
        raise ValueError('closed_source_selection_required')
    statuses = {}
    def query(name, words, limit=512):
        status, text = reader.read(tuple(words), limit)
        statuses[name] = status
        return text if status == 'ok' else ''

    state = query('device_state', ('get-state',))
    boot = query('boot_completed', ('shell', 'getprop', 'sys.boot_completed'))
    before_boot = query('boot_id_before', ('shell', 'cat', '/proc/sys/kernel/random/boot_id'))
    hwui = property_enum(query('hwui', ('shell', 'getprop', 'debug.hwui.renderer')), RENDERERS, statuses['hwui'] == 'ok')
    re_backend = property_enum(query('renderengine', ('shell', 'getprop', 'debug.renderengine.backend')), RE_BACKENDS, statuses['renderengine'] == 'ok')
    before = pids(query('pid_before', ('shell', 'pidof', package), 128))
    gfx = query('gfxinfo', ('shell', 'dumpsys', 'gfxinfo', package), 262144)
    after = pids(query('pid_after', ('shell', 'pidof', package), 128))
    size = dimensions(query('size', ('shell', 'wm', 'size')))
    density = densities(query('density', ('shell', 'wm', 'density')))
    mode = active_mode(query('active_mode', ('shell', 'cmd', 'display', 'get-active-mode', '0')))
    actual_rotation = rotation(query('rotation', ('shell', 'dumpsys', 'window', 'displays'), 65536))
    after_boot = query('boot_id_after', ('shell', 'cat', '/proc/sys/kernel/random/boot_id'))
    uuid = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    same_boot = bool(re.fullmatch(uuid, before_boot) and before_boot == after_boot)
    if not same_boot:
        process_status = 'boot_unverified_or_changed'
    elif statuses['pid_before'] != 'ok' or statuses['pid_after'] != 'ok':
        process_status = 'pid_query_unavailable'
    elif before is None or after is None:
        process_status = 'invalid_pid_readback'
    elif before != after:
        process_status = 'process_changed'
    elif not before:
        process_status = 'not_running'
    elif len(before) != 1:
        process_status = 'multiple_pids'
    else:
        process_status = 'single_pid_stable'
    actual_pipeline = pipeline(gfx, package, before[0]) if process_status == 'single_pid_stable' else 'unavailable'
    target_pipeline = PIPELINES[expected_renderer]
    known_hwui = hwui in RENDERERS
    known_pipeline = actual_pipeline != 'unavailable'
    flags = {
        'renderer_property_target_drift': hwui != expected_renderer if known_hwui else None,
        'target_vs_process_pipeline_mismatch': actual_pipeline != target_pipeline if known_pipeline else None,
        'property_vs_process_pipeline_mismatch': actual_pipeline != PIPELINES[hwui] if known_hwui and known_pipeline else None,
    }
    complete = (all(value == 'ok' for value in statuses.values()) and state == 'device'
        and boot == '1' and same_boot and known_hwui and re_backend in RE_BACKENDS
        and known_pipeline and 'physical' in size and 'physical' in density
        and mode is not None and actual_rotation is not None)
    return {
        'schema': 1, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': 'source_metadata_only_not_media_performance_or_visual_acceptance',
        'device_mutated': False, 'media_started': False, 'source_app_restarted': False,
        'serial': serial, 'source_package': package,
        'sample_status': 'complete' if complete else 'partial',
        'device_ready': state == 'device' and boot == '1', 'same_boot_verified': same_boot,
        'expected_hwui_policy': expected_renderer, 'hwui_property': hwui,
        'renderengine_property': re_backend, 'process_status': process_status,
        'source_pid': before[0] if process_status == 'single_pid_stable' else None,
        'source_pipeline': actual_pipeline, 'flags': flags,
        'size': size, 'density': density, 'active_mode_display0': mode,
        'display_rotation': actual_rotation, 'query_status': statuses,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', choices=SERIALS, required=True)
    parser.add_argument('--source-package', choices=PACKAGES, required=True)
    parser.add_argument('--expected-renderer', choices=RENDERERS, required=True,
                        help='Expected policy only; never setprop or restart an app')
    parser.add_argument('--adb', type=Path, default=Path.home()/'Library/Android/sdk/platform-tools/adb')
    parser.add_argument('--output', type=Path, help='Optional new sanitized JSON file; refuses overwrite')
    args = parser.parse_args()
    report = collect(ReadOnlyAdb(args.adb, args.serial), args.serial, args.source_package, args.expected_renderer)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as destination:
            destination.write(text)
    print(text, end='')
    return 0 if report['sample_status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())

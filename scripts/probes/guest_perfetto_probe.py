#!/usr/bin/env python3
"""Bounded guest timing trace; raw trace stays private, exported evidence is numeric."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

TARGET_PACKAGE = 'app.morphe.android.youtube'
VIDEO_TRACE_BYTES = 64 * 1024 * 1024
MAX_TRACE_BYTES = 128 * 1024 * 1024
ALLOWED_EVENTS = (
    'sched:sched_switch', 'sched:sched_waking', 'sched:sched_wakeup_new',
    'sched:sched_process_exit', 'sched:sched_process_free',
    'task:task_newtask', 'task:task_rename', 'power:cpu_frequency', 'power:cpu_idle')
ALLOWED_CATEGORIES = ('gfx', 'view', 'video')
ALLOWED_SOURCES = ('linux.ftrace', 'linux.process_stats',
                   'android.surfaceflinger.frametimeline',
                   'android.surfaceflinger.layers', 'android.surfaceflinger.transactions',
                   'android.surfaceflinger.frame')
ROLE_SQL = """CASE
 WHEN p.name='app.morphe.android.youtube' OR p.name GLOB 'app.morphe.android.youtube:*' THEN 1
 WHEN p.name LIKE '%surfaceflinger%' THEN 2
 WHEN p.name LIKE '%media.codec%' OR p.name LIKE '%codec2%' OR p.name LIKE '%media.c2%' THEN 3
 ELSE 0 END"""
THREADS_SQL = f"""SELECT t.utid, t.tid, p.pid, {ROLE_SQL} AS role_id
 FROM thread t JOIN process p ON t.upid=p.upid"""
ANALYSIS_SQL = f"""
SELECT start_ts AS trace_start_ns, end_ts AS trace_end_ns FROM trace_bounds;
SELECT ts AS clock_ts_ns, clock_id, clock_value AS clock_value_ns,
 snapshot_id AS clock_snapshot_id FROM clock_snapshot
 WHERE clock_id IN (1,3,6) ORDER BY snapshot_id,clock_id LIMIT 256;
WITH roles AS ({THREADS_SQL}), classified AS (
 SELECT s.id AS slice_id,s.ts AS ts_ns,s.dur AS dur_ns,r.role_id,r.utid,r.pid,r.tid,
 CASE
  WHEN lower(s.name) LIKE '%dequeuebuffer%' THEN 1
  WHEN lower(s.name) LIKE '%queuebuffer%' THEN 2
  WHEN lower(s.name) LIKE '%acquirebuffer%' THEN 3
  WHEN lower(s.name) LIKE '%latch%' THEN 4
  WHEN lower(s.name) LIKE '%present%' THEN 5
  WHEN lower(s.name) LIKE '%doframe%' THEN 6
  WHEN lower(s.name) LIKE '%decode%' THEN 7
  WHEN lower(s.name) LIKE '%outputbuffer%' THEN 8
  WHEN lower(s.name) LIKE '%inputbuffer%' THEN 9
  WHEN lower(s.name) LIKE '%wait%' THEN 10
  ELSE 0 END AS kind_id
 FROM slice s JOIN thread_track tt ON s.track_id=tt.id
 JOIN roles r ON tt.utid=r.utid
 WHERE r.role_id>0 AND s.name NOT GLOB 'CCodecBufferChannel::*'
)
SELECT slice_id,ts_ns,dur_ns,role_id,kind_id,utid,pid,tid FROM classified
 WHERE kind_id>0 ORDER BY ts_ns LIMIT 30000;
WITH roles AS ({THREADS_SQL})
SELECT s.id AS state_id,s.ts AS ts_ns,s.dur AS dur_ns,r.role_id,
 CASE WHEN s.state='Running' THEN 1 WHEN s.state IN ('R','R+') THEN 2
 WHEN s.state='S' THEN 3 WHEN s.state GLOB 'D*' THEN 4 ELSE 5 END AS state_kind_id,
 r.utid,r.pid,r.tid,s.io_wait FROM thread_state s JOIN roles r ON r.utid=s.utid
 WHERE r.role_id>0 AND s.dur>=10000000 ORDER BY s.ts;
WITH roles AS ({THREADS_SQL}), states AS (
 SELECT s.dur,r.role_id,
 CASE WHEN s.state='Running' THEN 1 WHEN s.state IN ('R','R+') THEN 2
 WHEN s.state='S' THEN 3 WHEN s.state GLOB 'D*' THEN 4 ELSE 5 END AS state_kind_id
 FROM thread_state s JOIN roles r ON r.utid=s.utid WHERE r.role_id>0 AND s.dur>=0
)
SELECT role_id,state_kind_id,COUNT(*) AS state_count,SUM(dur) AS total_duration_ns,
 MAX(dur) AS max_duration_ns,SUM(dur>=10000000) AS count_over_10ms,
 SUM(dur>=50000000) AS count_over_50ms,SUM(dur>=100000000) AS count_over_100ms
 FROM states GROUP BY role_id,state_kind_id;
WITH roles AS ({THREADS_SQL})
SELECT s.id AS codec_slice_id,s.ts AS ts_ns,s.dur AS dur_ns,r.utid,r.pid,r.tid,
 s.name AS codec_name FROM slice s JOIN thread_track tt ON s.track_id=tt.id
 JOIN roles r ON tt.utid=r.utid WHERE r.role_id>0
 AND s.name GLOB 'CCodecBufferChannel::*' ORDER BY s.ts LIMIT 30000;
SELECT CASE WHEN name LIKE '%overrun%' THEN 1 WHEN name LIKE '%lost%' THEN 2
 ELSE 3 END AS stat_kind_id,COUNT(*) AS stat_count,SUM(value) AS stat_value
 FROM stats WHERE value>0 AND (severity IN ('error','data_loss')
 OR name LIKE '%overrun%' OR name LIKE '%lost%') GROUP BY stat_kind_id;
"""
HEADERS = {
    ('trace_start_ns', 'trace_end_ns'): 'trace_bounds',
    ('clock_ts_ns', 'clock_id', 'clock_value_ns', 'clock_snapshot_id'): 'clock_snapshots',
    ('slice_id', 'ts_ns', 'dur_ns', 'role_id', 'kind_id', 'utid', 'pid', 'tid'): 'slices',
    ('state_id', 'ts_ns', 'dur_ns', 'role_id', 'state_kind_id', 'utid', 'pid', 'tid', 'io_wait'): 'thread_states',
    ('role_id', 'state_kind_id', 'state_count', 'total_duration_ns', 'max_duration_ns',
     'count_over_10ms', 'count_over_50ms', 'count_over_100ms'): 'thread_state_summary',
    ('codec_slice_id', 'ts_ns', 'dur_ns', 'utid', 'pid', 'tid', 'codec_name'): 'codec_stages',
    ('stat_kind_id', 'stat_count', 'stat_value'): 'trace_import_stats',
}
COMPONENTS = {'c2.goldfish.h264.decoder': 1, 'c2.goldfish.vp9.decoder': 2,
              'c2.goldfish.vp8.decoder': 3, 'c2.android.avc.decoder': 4,
              'c2.android.vp9.decoder': 5, 'c2.android.av1.decoder': 6}
CODEC_PTS = re.compile(r'^CCodecBufferChannel::(queue|onWorkDone)\('
                       r'(c2\.[a-z0-9.]{1,80}\.decoder)(?:#(\d{1,10}))?@ts=(-?\d{1,19})\)$')
CODEC_FIXED = re.compile(r'^CCodecBufferChannel::'
                         r'(renderOutputBuffer|sendOutputBuffers|handleWork|onWorkDone)-'
                         r'(c2\.[a-z0-9.]{1,80}\.decoder)(?:#(\d{1,10}))?$')
STAGES = {'queue': 1, 'onWorkDone': 2, 'renderOutputBuffer': 3,
          'sendOutputBuffers': 4, 'handleWork': 5, 'onWorkDoneScope': 6}


class ProbeError(Exception):
    def __init__(self, code):
        super().__init__('bounded_probe_error')
        self.code = code


def shell(adb, serial, command, timeout=10, limit=2 * 1024 * 1024):
    try:
        result = subprocess.run([str(adb), '-s', serial, 'shell', command],
                                input=None, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeError(1) from None
    if result.returncode or len(result.stdout) > limit:
        raise ProbeError(2)
    return result.stdout


def parse_categories(raw):
    categories = set(re.findall(rb'^[ \t]*([a-zA-Z0-9_]+)\s+-', raw, re.M))
    return [item for item in ALLOWED_CATEGORIES if item.encode() in categories]


def capabilities(adb, serial):
    # Only explicit, fixed source/category/event names leave this function.
    categories_raw = shell(adb, serial, 'atrace --list_categories')
    query = shell(adb, serial, 'perfetto --query')
    events = set(shell(adb, serial,
                       'cat /sys/kernel/tracing/available_events').decode('ascii', 'ignore').split())
    return {
        'sources': [item for item in ALLOWED_SOURCES if item.encode() in query],
        'categories': parse_categories(categories_raw),
        'events': [item for item in ALLOWED_EVENTS if item in events],
    }


def make_config(seconds, available, include_buffers=False, include_frames=False):
    if not 30 <= seconds <= 45:
        raise ProbeError(3)
    if 'linux.ftrace' not in available['sources'] or 'sched:sched_switch' not in available['events']:
        raise ProbeError(4)
    lines = ['buffers { size_kb: 32768 fill_policy: RING_BUFFER }',
             f'duration_ms: {int(seconds * 1000)}', 'write_into_file: true',
             'file_write_period_ms: 1000',
             f'max_file_size_bytes: {MAX_TRACE_BYTES if include_buffers else VIDEO_TRACE_BYTES}',
             'data_sources { config { name: "linux.ftrace" target_buffer: 0 ftrace_config {',
             'compact_sched { enabled: true }', 'drain_period_ms: 250']
    for item in available['events']:
        if item in ALLOWED_EVENTS:
            lines.append(f'ftrace_events: "{item.replace(":", "/")}"')
    for item in available['categories']:
        if item in ALLOWED_CATEGORIES:
            lines.append(f'atrace_categories: "{item}"')
    lines += [f'atrace_apps: "{TARGET_PACKAGE}"', '} } }']
    if 'linux.process_stats' in available['sources']:
        lines += ['data_sources { config { name: "linux.process_stats" process_stats_config {',
                  'scan_all_processes_on_start: true proc_stats_poll_ms: 1000', '} } }']
    if 'android.surfaceflinger.frametimeline' in available['sources']:
        lines.append('data_sources { config { name: "android.surfaceflinger.frametimeline" } }')
    if include_frames:
        if 'android.surfaceflinger.frame' not in available['sources']:
            raise ProbeError(32)
        lines.append('data_sources { config { name: "android.surfaceflinger.frame" } }')
    if include_buffers:
        if not {'android.surfaceflinger.layers', 'android.surfaceflinger.transactions'} <= set(available['sources']):
            raise ProbeError(31)
        lines += ['data_sources { config { name: "android.surfaceflinger.layers"',
                  'surfaceflinger_layers_config { mode: MODE_ACTIVE',
                  'trace_flags: TRACE_FLAG_BUFFERS trace_flags: TRACE_FLAG_COMPOSITION } } }',
                  'data_sources { config { name: "android.surfaceflinger.transactions"',
                  'surfaceflinger_transactions_config { mode: MODE_ACTIVE } } }']
    return ('\n'.join(lines) + '\n').encode('ascii')


def private_trace(path, max_bytes=VIDEO_TRACE_BYTES):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(Path('/private/tmp')) or not path.is_file():
        raise ProbeError(5)
    if max_bytes not in (VIDEO_TRACE_BYTES, MAX_TRACE_BYTES):
        raise ProbeError(5)
    if path.stat().st_mode & 0o077 or not 0 < path.stat().st_size <= max_bytes:
        raise ProbeError(5)
    return path


def owned_perfetto_cmdline(raw, remote_trace):
    parts = raw.rstrip(b'\0').split(b'\0')
    return (bool(parts) and Path(parts[0].decode('utf-8', 'ignore')).name == 'perfetto'
            and remote_trace.encode('ascii') in parts)


def cleanup_owned(adb, serial, pidfile, remote_trace, remove_trace=True):
    stopped, removed = False, False
    try:
        raw_pid = shell(adb, serial, f'cat {shlex.quote(pidfile)}', timeout=3, limit=128).strip()
        if re.fullmatch(rb'[1-9][0-9]{0,8}', raw_pid):
            pid = int(raw_pid)
            try:
                cmdline = shell(adb, serial, f'cat /proc/{pid}/cmdline', timeout=3, limit=8192)
                if owned_perfetto_cmdline(cmdline, remote_trace):
                    shell(adb, serial, f'kill -TERM {pid}', timeout=3, limit=128)
                    stopped = True
            except ProbeError:
                pass  # Usually the bounded producer has already exited.
    except ProbeError:
        pass
    try:
        shell(adb, serial, 'rm -f ' + shlex.quote(pidfile)
              + (' ' + shlex.quote(remote_trace) if remove_trace else ''),
              timeout=3, limit=128)
        removed = True
    except ProbeError:
        pass
    return {'owned_process_signaled': stopped, 'owned_guest_files_removed': removed and remove_trace,
            'owned_guest_trace_retained': not remove_trace}


def classify_diagnostics(raw):
    # Input is bounded in private memory. Only fixed reason flags are exported.
    value = raw.lower()
    return {
        'config_parse_failure': bool(re.search(rb'(invalid|failed|error).{0,80}(config|parse)', value)),
        'permission_failure': b'permission denied' in value,
        'storage_failure': b'no space left' in value,
        'write_failure': bool(re.search(rb'failed to (open|write)', value)),
        'size_limit_mentioned': b'max_file_size' in value or b'maximum file size' in value,
        'atrace_failure': bool(re.search(rb'atrace.{0,80}(failed|error|timeout)', value)),
    }


def capture(adb, serial, seconds, available, include_buffers=False, include_frames=False):
    config = make_config(seconds, available, include_buffers, include_frames)
    byte_limit = MAX_TRACE_BYTES if include_buffers else VIDEO_TRACE_BYTES
    private_dir = Path(tempfile.mkdtemp(prefix='huoguo-guest-perfetto-', dir='/private/tmp'))
    os.chmod(private_dir, 0o700)
    local_trace = private_dir / 'guest.pftrace'
    private_log = private_dir / 'capture-status.log'
    unique = uuid.uuid4().hex
    remote_trace = f'/data/misc/perfetto-traces/huoguo-guest-{unique}.pftrace'
    pidfile = f'/data/local/tmp/huoguo-guest-{unique}.pid'
    command = f'umask 077; echo $$ > {shlex.quote(pidfile)}; exec perfetto --txt -c - -o {shlex.quote(remote_trace)} --no-clobber'
    process, phase_id = None, 1
    diagnostic_fd = os.open(private_log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    diagnostics = os.fdopen(diagnostic_fd, 'wb')
    result = {'duration_requested_ms': int(seconds * 1000), 'error_code': 0,
              'host_start_monotonic_ns': time.monotonic_ns(), 'host_start_wall_ns': time.time_ns(),
              'source_content_fps_known': False, 'captured_categories': available['categories'],
              'surface_buffer_trace_requested': include_buffers,
              'graphics_frame_trace_requested': include_frames,
              'trace_byte_limit': byte_limit, 'duration_coverage_verified': False}
    try:
        process = subprocess.Popen([str(adb), '-s', serial, 'shell', '-T',
                                    shlex.join(['sh', '-c', command])],
                                   stdin=subprocess.PIPE, stdout=diagnostics,
                                   stderr=diagnostics)
        phase_id = 2
        process.communicate(input=config, timeout=seconds + 20)
        if process.returncode:
            raise ProbeError(6)
        phase_id = 3
        size_raw = shell(adb, serial, f'stat -c %s {shlex.quote(remote_trace)}', limit=128).strip()
        if not re.fullmatch(rb'[0-9]{1,9}', size_raw) or not 0 < int(size_raw) <= byte_limit:
            raise ProbeError(7)
        result['expected_trace_bytes'] = int(size_raw)
        result['trace_near_byte_limit'] = int(size_raw) >= byte_limit * 0.95
        phase_id = 4
        fd = os.open(local_trace, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            copy = subprocess.run([str(adb), '-s', serial, 'exec-out', 'cat', remote_trace],
                                  stdout=out, stderr=diagnostics, timeout=45)
        phase_id = 5
        if copy.returncode or local_trace.stat().st_size != int(size_raw):
            raise ProbeError(8)
        result.update(trace_bytes=local_trace.stat().st_size,
                      trace_sha256=hashlib.sha256(local_trace.read_bytes()).hexdigest())
    except subprocess.TimeoutExpired:
        result['error_code'] = 21 if phase_id == 2 else 22 if phase_id == 4 else 23
    except OSError:
        result['error_code'] = 24
    except ProbeError as error:
        result['error_code'] = error.code
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        result.update(host_end_monotonic_ns=time.monotonic_ns(), host_end_wall_ns=time.time_ns())
        result['failure_phase_id'] = phase_id if result['error_code'] else 0
        diagnostics.close()
        if private_log.exists():
            log_bytes = private_log.stat().st_size
            with private_log.open('rb') as source:
                source.seek(max(0, log_bytes-65536))
                bounded_log = source.read(65536)
            result['diagnostic_bytes'] = log_bytes
            result['diagnostic_reason_flags'] = classify_diagnostics(bounded_log)
        if local_trace.exists():
            result['local_trace_bytes'] = local_trace.stat().st_size
            result['trace_copy_complete'] = result['error_code'] == 0
            result['partial_trace_retained'] = result['error_code'] != 0
        result['cleanup'] = cleanup_owned(adb, serial, pidfile, remote_trace,
                                         remove_trace=result['error_code'] == 0)
    return local_trace if local_trace.exists() else None, result


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out, allow_nan=False, separators=(',', ':'))
        out.write('\n')


def parse_integer(value):
    if value in ('', '[NULL]', 'NULL'):
        return None
    if not re.fullmatch(r'-?\d{1,20}', value):
        raise ProbeError(13)
    parsed = int(value)
    if not -(1 << 63) <= parsed < (1 << 63):
        raise ProbeError(13)
    return parsed


def parse_codec_name(value):
    match = CODEC_PTS.fullmatch(value) or CODEC_FIXED.fullmatch(value)
    if not match or match[2] not in COMPONENTS:
        return None
    pts = parse_integer(match[4]) if match.re is CODEC_PTS else None
    stage = 'onWorkDoneScope' if match.re is CODEC_FIXED and match[1] == 'onWorkDone' else match[1]
    return {'stage_id': STAGES[stage], 'component_id': COMPONENTS[match[2]],
            'instance_id': parse_integer(match[3]) if match[3] is not None else None,
            'content_pts_us': pts}


def parse_results(raw):
    if len(raw) > 32 * 1024 * 1024:
        raise ProbeError(13)
    sections, rejected_codec_names = {}, 0
    # Perfetto prints each result table as CSV, separated by one blank line.
    for block in raw.decode('utf-8', 'strict').strip().split('\n\n'):
        if not block.strip():
            continue
        reader = csv.reader(io.StringIO(block))
        header = tuple(next(reader))
        name = HEADERS.get(header)
        if name is None or name in sections:
            raise ProbeError(13)
        rows = []
        for values in reader:
            if len(values) != len(header):
                raise ProbeError(13)
            row = {}
            for key, value in zip(header, values):
                if key == 'codec_name':
                    stage = parse_codec_name(value)
                    if stage is None:
                        rejected_codec_names += 1
                        row = None
                        break
                    row.update(stage)
                else:
                    row[key] = parse_integer(value)
            if row is not None:
                rows.append(row)
        sections[name] = rows
    if set(sections) != set(HEADERS.values()):
        raise ProbeError(13)
    sections['unrecognized_codec_slice_count'] = rejected_codec_names
    return sections


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0, 'p50_ms': None, 'p95_ms': None, 'p99_ms': None, 'max_ms': None}
    def quantile(q):
        return round(values[min(len(values)-1, int((len(values)-1)*q))]/1e6, 3)
    return {'count': len(values), 'p50_ms': quantile(.5), 'p95_ms': quantile(.95),
            'p99_ms': quantile(.99), 'max_ms': quantile(1),
            'over_50_ms': sum(item>50_000_000 for item in values),
            'over_100_ms': sum(item>100_000_000 for item in values)}


def codec_summary(rows):
    result = []
    # Codec instance numbers and media PTS can repeat in another process.
    # Keep rows without a PID in their own legacy/unknown identity bucket.
    identities = set((row.get('pid'), row['component_id'], row.get('instance_id')) for row in rows)
    for pid, component, instance in sorted(identities, key=lambda item: (
            -1 if item[0] is None else item[0], item[1],
            -1 if item[2] is None else item[2])):
        stages = [row for row in rows if row.get('pid') == pid
                  and row['component_id'] == component and row.get('instance_id') == instance]
        by_pts = {}
        for row in stages:
            if row['content_pts_us'] is not None:
                by_pts.setdefault(row['content_pts_us'], {}).setdefault(row['stage_id'], []).append(row)
        delays, duplicate_pts, queue_without_done, done_without_queue = [], 0, 0, 0
        for matches in by_pts.values():
            queued, done = matches.get(1, []), matches.get(2, [])
            if len(queued) == len(done) == 1:
                elapsed = done[0]['ts_ns'] - queued[0]['ts_ns']
                if elapsed >= 0:
                    delays.append(elapsed)
            elif queued and done:
                duplicate_pts += 1
            elif queued:
                queue_without_done += len(queued)
            elif done:
                done_without_queue += len(done)
        item = {'pid': pid, 'component_id': component, 'instance_id': instance,
                'queue_to_work_done': distribution(delays),
                'ambiguous_duplicate_pts': duplicate_pts, 'queue_without_done': queue_without_done,
                'done_without_queue': done_without_queue, 'stage_counts': {}, 'stage_return_gaps': {},
                'stage_duration_ms': {}, 'stage_completion_gaps': {}, 'stage_open_slice_counts': {}}
        for stage in STAGES.values():
            # Work-done slice entry is a callback marker. It is not end-to-end display latency.
            times = sorted(row['ts_ns'] for row in stages if row['stage_id'] == stage)
            item['stage_counts'][str(stage)] = len(times)
            item['stage_return_gaps'][str(stage)] = distribution([b-a for a,b in zip(times,times[1:])])
            durations = [row['dur_ns'] for row in stages if row['stage_id'] == stage and row.get('dur_ns', -1) >= 0]
            completions = sorted(row['ts_ns']+row['dur_ns'] for row in stages
                                 if row['stage_id'] == stage and row.get('dur_ns', -1) >= 0)
            item['stage_duration_ms'][str(stage)] = distribution(durations)
            item['stage_completion_gaps'][str(stage)] = distribution([b-a for a,b in zip(completions,completions[1:])])
            item['stage_open_slice_counts'][str(stage)] = sum(row.get('dur_ns', -1) < 0 for row in stages
                                                            if row['stage_id'] == stage)
        result.append(item)
    return result


def analyze(trace, processor, include_buffers=False):
    trace = private_trace(trace, MAX_TRACE_BYTES if include_buffers else VIDEO_TRACE_BYTES)
    try:
        response = subprocess.run([str(processor), 'query', '-f', '-', str(trace)],
                                  input=ANALYSIS_SQL.encode('ascii'), stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeError(14) from None
    if response.returncode:
        raise ProbeError(15)
    result = parse_results(response.stdout)
    result['codec_summary'] = codec_summary(result['codec_stages'])
    result['slice_rows_at_limit'] = len(result['slices']) >= 30000
    result['thread_state_rows_at_limit'] = False
    result['thread_state_export_complete_above_min'] = True
    result['codec_rows_at_limit'] = len(result['codec_stages']) >= 30000
    result['thread_state_min_duration_ns'] = 10000000
    result['frame_timeline_exported'] = False
    result['thread_state_role_legend'] = {'1': 'youtube_process', '2': 'surfaceflinger', '3': 'codec_service'}
    result['state_kind_legend'] = {'1': 'running', '2': 'runnable', '3': 'sleeping', '4': 'uninterruptible', '5': 'other'}
    result['codec_stage_legend'] = {str(value): key for key,value in STAGES.items()}
    result['codec_component_legend'] = {str(value): key for key,value in COMPONENTS.items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', type=Path, default=Path.home() / 'Library/Android/sdk/platform-tools/adb')
    parser.add_argument('--serial', default='emulator-5556')
    parser.add_argument('--seconds', type=float, default=35)
    parser.add_argument('--categories', default='gfx,view,video',
                        help='Fixed atrace categories, e.g. video; available choices gfx,view,video')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--capture-only', action='store_true', help='Save private trace without SQL analysis')
    parser.add_argument('--surface-buffers', action='store_true',
                        help='Add bounded active SurfaceFlinger layer/buffer and transaction traces')
    parser.add_argument('--graphics-frames', action='store_true',
                        help='Add supported graphics buffer frame events without full layer snapshots')
    parser.add_argument('--analyze-existing', type=Path)
    parser.add_argument('--trace-processor', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'emulator-[0-9]{1,5}', args.serial) or not 30 <= args.seconds <= 45:
        parser.error('Use a local emulator and a duration between 30 and 45 seconds')
    categories = args.categories.split(',')
    if not categories or len(categories) != len(set(categories)) or any(item not in ALLOWED_CATEGORIES for item in categories):
        parser.error('Use only gfx,view,video atrace categories')
    if not args.check and args.output is None:
        parser.error('A fresh sanitized --output path is required')
    report = {'schema': 1, 'source_content_fps_known': False,
              'trace_changes_player_or_vm_configuration': False}
    raw_path = None
    try:
        if args.output and args.output.exists():
            raise ProbeError(10)
        if args.analyze_existing:
            raw_path = private_trace(args.analyze_existing, MAX_TRACE_BYTES if args.surface_buffers else VIDEO_TRACE_BYTES)
        else:
            available = capabilities(args.adb, args.serial)
            available['categories'] = [item for item in available['categories'] if item in categories]
            report['capabilities'] = available
            if not args.check:
                raw_path, report['capture'] = capture(args.adb, args.serial, args.seconds, available,
                                                      args.surface_buffers, args.graphics_frames)
        if raw_path and not args.capture_only:
            if args.trace_processor is None:
                raise ProbeError(11)
            report['analysis'] = analyze(raw_path, args.trace_processor, args.surface_buffers)
        if args.output:
            write_json(args.output, report)
        status = {'ok': bool(args.check or raw_path) and report.get('capture', {}).get('error_code', 0) == 0, 'schema': 1,
                  'error_code': report.get('capture', {}).get('error_code', 0)}
        if raw_path:
            # Private location is printed for the operator, never saved in repository evidence.
            status['private_trace_path'] = str(raw_path)
        print(json.dumps(status, separators=(',', ':')))
        return 0 if status['ok'] else 1
    except ProbeError as error:
        print(json.dumps({'ok': False, 'error_code': error.code}, separators=(',', ':')))
        return 1
    except (OSError, ValueError):
        print('{"ok":false,"error_code":12}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

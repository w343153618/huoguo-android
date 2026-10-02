"""Bounded, account-scoped diagnostics on the existing authenticated TLS gateway."""
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import stat
import tempfile
import threading
import time
import uuid

MAX_BYTES = 128 * 1024
MAX_STAGES = 12
MAX_SAMPLES = 240
SOURCE_PACKAGE = 'local.remoteandroid.benchmark'
SOURCE_COMPONENT = SOURCE_PACKAGE + '/.DiagnosticSourceActivity'


class DiagnosticsError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def report_uuid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        raise DiagnosticsError(400, 'Invalid diagnostic identifier')
    return str(uuid.UUID(value))


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise DiagnosticsError(400, 'Invalid diagnostic fields')


def _text(value, limit, allow_empty=True):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value) or any(ord(c) < 32 for c in value):
        raise DiagnosticsError(400, 'Invalid diagnostic text')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise DiagnosticsError(400, 'Invalid diagnostic text')


def _number(value, low, high, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not low <= value <= high or not math.isfinite(value):
        raise DiagnosticsError(400, 'Invalid diagnostic metric')


def _boolean(value):
    if type(value) is not bool:
        raise DiagnosticsError(400, 'Invalid diagnostic flag')


def _enum(value, choices):
    if type(value) is not str or value not in choices:
        raise DiagnosticsError(400, 'Invalid diagnostic option')


def _timestamp(value):
    _text(value, 64, False)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise DiagnosticsError(400, 'Invalid diagnostic timestamp')


def validate_report(report):
    required = ('schema_version', 'client_report_id', 'created_at', 'app_version', 'device', 'network', 'mode', 'status', 'stages')
    _object(report, (*required, 'feedback', 'cleanup_ok', 'cleanup_error'), required)
    if type(report['schema_version']) is not int or report['schema_version'] != 1:
        raise DiagnosticsError(400, 'Unsupported diagnostic schema')
    report_uuid(report['client_report_id'])
    _timestamp(report['created_at'])
    _text(report['app_version'], 64, False)
    _enum(report['mode'], ('quick', 'full'))
    _enum(report['status'], ('completed', 'cancelled', 'failed'))
    if 'cleanup_ok' in report:
        _boolean(report['cleanup_ok'])
    if 'cleanup_error' in report:
        _text(report['cleanup_error'], 160)
    device = report['device']
    _object(device, ('manufacturer', 'model', 'android', 'sdk', 'media_volume', 'audio_gain', 'total_memory_mb', 'avc_hardware_advertised', 'hevc_hardware_advertised'), ('manufacturer', 'model', 'android', 'sdk'))
    for key in ('manufacturer', 'model', 'android'):
        _text(device[key], 128)
    _number(device['sdk'], 1, 100, True)
    for key, upper in (('media_volume', 1), ('audio_gain', 3), ('total_memory_mb', 131_072)):
        if key in device:
            _number(device[key], 0, upper)
    for key in ('avc_hardware_advertised', 'hevc_hardware_advertised'):
        if key in device:
            _boolean(device[key])
    network = report['network']
    _object(network, ('transport', 'label', 'vpn_present'), ('transport', 'label', 'vpn_present'))
    _text(network['transport'], 32)
    _text(network['label'], 180)
    _boolean(network['vpn_present'])
    stages = report['stages']
    if not isinstance(stages, list) or len(stages) > MAX_STAGES:
        raise DiagnosticsError(400, 'Too many diagnostic stages')
    core = ('label', 'max_size', 'bitrate', 'max_fps', 'mode', 'buffer_ms')
    integer_ranges = {
        'elapsed_ms': (0, 3_600_000), 'received_frames': (0, 1_000_000_000),
        'rendered_frames': (0, 1_000_000_000), 'video_bytes': (0, 1_000_000_000_000),
        'codec_callback_frames': (0, 1_000_000_000),
        'render_gap_count': (0, 1_000_000_000), 'discarded_frames': (0, 1_000_000_000),
        'thermal_start': (-1, 6), 'thermal_end': (-1, 6),
        'battery_start': (-1, 100), 'battery_end': (-1, 100),
        'accepted_bitrate': (0, 40_000_000), 'source_startup_ms': (0, 3_600_000),
        'actual_sample_ms': (1, 5000),
    }
    number_ranges = {
        'received_fps': (0, 1000), 'rendered_fps': (0, 1000), 'receive_mbps': (0, 10_000),
        'codec_callback_fps': (0, 1000),
        'max_render_gap_ms': (0, 3_600_000), 'rtt_p50_ms': (-1, 120_000), 'rtt_p95_ms': (-1, 120_000),
        'last_receive_ago_ms': (-1, 3_600_000), 'last_render_ago_ms': (-1, 3_600_000),
        'receive_interval_jitter_ms': (0, 3_600_000), 'render_interval_jitter_ms': (0, 3_600_000),
        'source_interval_jitter_ms': (0, 3_600_000),
        'battery_temp_start_c': (-20, 100), 'battery_temp_end_c': (-20, 100),
        'app_cpu_p50_percent': (0, 6400), 'app_cpu_p95_percent': (0, 6400),
        'client_pipeline_p50_ms': (0, 60_000), 'client_pipeline_p95_ms': (0, 60_000),
        'app_heap_peak_mb': (0, 16_384), 'native_heap_peak_mb': (0, 16_384),
    }
    flags = ('hardware_decoder', 'network_changed', 'vpn_present', 'adaptive_rejected', 'valid',
             'actual_display_fps_measured', 'actual_audio_video_skew_measured')
    timing_enums = {'codec_timing_basis': ('java_codec_callback_receipt',),
                    'vendor_timestamp_status': ('not_used_for_diagnostic_timing',)}
    texts = {'decoder_name': 160, 'error': 160, 'invalid_reason': 160, 'transport': 32}
    sample_ranges = {'app_cpu_percent': (0, 6400), 'java_heap_mb': (0, 16_384), 'native_heap_mb': (0, 16_384), 'signal_strength_dbm': (-150, 0)}
    for stage in stages:
        _object(stage, (*core, *integer_ranges, *number_ranges, *flags, *texts, *timing_enums, 'dimensions', 'samples'), core)
        _text(stage['label'], 120, False)
        _number(stage['max_size'], 128, 4096, True)
        _number(stage['bitrate'], 500_000, 40_000_000, True)
        _number(stage['max_fps'], 1, 120, True)
        _enum(stage['mode'], ('CBR', 'VBR', 'ADAPTIVE_VBR'))
        _number(stage['buffer_ms'], 0, 5000, True)
        for key, bounds in integer_ranges.items():
            if key in stage:
                _number(stage[key], *bounds, integer=True)
        for key, bounds in number_ranges.items():
            if key in stage:
                _number(stage[key], *bounds)
        for key in flags:
            if key in stage:
                _boolean(stage[key])
        for key in ('actual_display_fps_measured', 'actual_audio_video_skew_measured'):
            if stage.get(key) is True:
                raise DiagnosticsError(400, 'Codec callbacks cannot certify physical display or audio/video timing')
        for key, choices in timing_enums.items():
            if key in stage:
                _enum(stage[key], choices)
        for key, limit in texts.items():
            if key in stage:
                _text(stage[key], limit)
        if 'dimensions' in stage and (not isinstance(stage['dimensions'], str) or not re.fullmatch(r'\d{1,5}x\d{1,5}', stage['dimensions'])):
            raise DiagnosticsError(400, 'Invalid diagnostic dimensions')
        samples = stage.get('samples', [])
        if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
            raise DiagnosticsError(400, 'Too many diagnostic samples')
        for sample in samples:
            _object(sample, ('elapsed_ms', 'received_frames', 'rendered_frames', 'codec_callback_frames', 'video_bytes', 'rtt_ms', 'thermal_status', *sample_ranges), ('elapsed_ms',))
            for key in ('elapsed_ms', 'received_frames', 'rendered_frames', 'codec_callback_frames', 'video_bytes'):
                if key in sample:
                    _number(sample[key], *integer_ranges[key], integer=True)
            if 'rtt_ms' in sample:
                _number(sample['rtt_ms'], -1, 120_000)
            if 'thermal_status' in sample:
                _number(sample['thermal_status'], -1, 6, True)
            for key, bounds in sample_ranges.items():
                if key in sample:
                    _number(sample[key], *bounds)
    if 'feedback' in report:
        _object(report['feedback'], ('smoothness', 'audio'))
        if 'smoothness' in report['feedback']:
            _enum(report['feedback']['smoothness'], ('未填写', '顺畅', '偶尔卡顿', '很卡'))
        if 'audio' in report['feedback']:
            _enum(report['feedback']['audio'], ('未填写', '正常', '偏小', '断续'))
    return report


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('Non-finite number')


def decode_json(body):
    if len(body) > MAX_BYTES:
        raise DiagnosticsError(413, 'Diagnostic report exceeds 128 KiB')
    try:
        decoded = body.decode('utf-8')
    except UnicodeError:
        raise DiagnosticsError(400, 'Invalid diagnostic JSON')
    # The schema has at most six levels. Refuse excessive nesting before parsing it.
    depth, quoted, escaped = 0, False, False
    for character in decoded:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            if depth > 8:
                raise DiagnosticsError(400, 'Diagnostic JSON is too deeply nested')
        elif character in ']}':
            depth -= 1
    try:
        return json.loads(decoded, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise DiagnosticsError(400, 'Invalid diagnostic JSON')


def read_json(handler, limit=MAX_BYTES):
    if handler.headers.get('Transfer-Encoding'):
        raise DiagnosticsError(400, 'Content-Length is required')
    if hasattr(handler.headers, 'get_all') and len(handler.headers.get_all('Content-Length', [])) != 1:
        raise DiagnosticsError(400, 'Content-Length is required')
    try:
        length = int(handler.headers.get('Content-Length', '-1'))
    except ValueError:
        raise DiagnosticsError(400, 'Invalid diagnostic length')
    if length > limit:
        raise DiagnosticsError(413, 'Diagnostic request is too large')
    if length <= 0:
        raise DiagnosticsError(400, 'Invalid diagnostic length')
    try:
        handler.connection.settimeout(10)
        body = handler.rfile.read(length)
        if len(body) != length:
            raise DiagnosticsError(400, 'Diagnostic upload was interrupted')
    except OSError:
        raise DiagnosticsError(400, 'Diagnostic upload was interrupted')
    return decode_json(body)


class ReportStore:
    def __init__(self, directory, max_reports=40, max_account_bytes=4 * 1024 * 1024, retention_days=30):
        self.directory = pathlib.Path(directory)
        self.max_reports, self.max_account_bytes = max_reports, max_account_bytes
        self.retention_seconds = retention_days * 86400
        self.lock = threading.RLock()

    def _account_directory(self, account):
        if not isinstance(account, str):
            raise DiagnosticsError(401, 'Login required')
        directory = self.directory / hashlib.sha256(account.encode()).hexdigest()
        for path in (self.directory, directory):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise DiagnosticsError(503, 'Diagnostic storage unavailable')
            path.chmod(0o700)
        return directory

    @staticmethod
    def _read(path):
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES + 4096:
                    raise DiagnosticsError(503, 'Diagnostic storage unavailable')
                document = json.loads(source.read())
            if not isinstance(document, dict) or set(document) != {'report_id', 'created_at', 'report'}:
                raise DiagnosticsError(503, 'Diagnostic storage unavailable')
            report_uuid(document['report_id'])
            _timestamp(document['created_at'])
            validate_report(document['report'])
            return document
        except (ValueError, UnicodeError, RecursionError, DiagnosticsError):
            raise DiagnosticsError(503, 'Diagnostic storage unavailable')

    def _prune(self, directory, reserve_bytes=0, reserve_count=0):
        entries = []
        now = time.time()
        for path in directory.glob('*.json'):
            try:
                report_uuid(path.stem)
            except DiagnosticsError:
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or now - info.st_mtime > self.retention_seconds:
                path.unlink()
            else:
                entries.append((info.st_mtime, path, info.st_size))
        entries.sort(key=lambda entry: (entry[0], entry[1].name))
        total = sum(entry[2] for entry in entries)
        while entries and (len(entries) + reserve_count > self.max_reports or total + reserve_bytes > self.max_account_bytes):
            _, path, size = entries.pop(0)
            path.unlink()
            total -= size
        return entries

    @staticmethod
    def _receipt(document):
        return {'ok': True, 'report_id': document['report_id'], 'created_at': document['created_at']}

    def save(self, account, report):
        validate_report(report)
        try:
            encoded_report = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        except (ValueError, UnicodeError):
            raise DiagnosticsError(400, 'Invalid diagnostic report')
        if len(encoded_report) > MAX_BYTES:
            raise DiagnosticsError(413, 'Diagnostic report exceeds 128 KiB')
        with self.lock:
            directory = self._account_directory(account)
            entries = self._prune(directory)
            client_id = report_uuid(report['client_report_id'])
            for _, path, _ in entries:
                document = self._read(path)
                if report_uuid(document['report']['client_report_id']) == client_id:
                    if document['report'] != report:
                        raise DiagnosticsError(409, 'Diagnostic identifier was already used')
                    return self._receipt(document)
            report_id = str(uuid.uuid4())
            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
            document = {'report_id': report_id, 'created_at': created_at, 'report': report}
            payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            if len(payload) > self.max_account_bytes:
                raise DiagnosticsError(413, 'Diagnostic storage quota exceeded')
            self._prune(directory, len(payload), 1)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix='.report-', suffix='.tmp', dir=directory, delete=False) as source:
                    temporary = pathlib.Path(source.name)
                    os.fchmod(source.fileno(), 0o600)
                    source.write(payload)
                    source.flush()
                    os.fsync(source.fileno())
                os.replace(temporary, directory / (report_id + '.json'))
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return self._receipt(document)

    def listing(self, account):
        with self.lock:
            directory = self._account_directory(account)
            records = []
            for _, path, _ in reversed(self._prune(directory)):
                document = self._read(path)
                report = document['report']
                records.append({key: document[key] for key in ('report_id', 'created_at')} | {key: report[key] for key in ('client_report_id', 'app_version', 'mode', 'status')})
            return {'reports': records}

    def get(self, account, identifier):
        identifier = report_uuid(identifier)
        with self.lock:
            directory = self._account_directory(account)
            self._prune(directory)
            try:
                return self._read(directory / (identifier + '.json'))
            except FileNotFoundError:
                raise DiagnosticsError(404, 'Diagnostic report not found')


class DiagnosticSource:
    """A short lease can launch only the bundled synthetic scene, never arbitrary ADB."""
    def __init__(self, adb, ensure_android, lease_seconds=480, clock=time.monotonic, timer_factory=threading.Timer):
        self.adb, self.ensure_android = adb, ensure_android
        self.lease_seconds, self.clock, self.timer_factory = lease_seconds, clock, timer_factory
        self.lock = threading.RLock()
        self.owner = self.run_id = self.timer = None
        self.generation = 0
        self.deadline = 0

    def _clear(self):
        if self.timer:
            self.timer.cancel()
        self.owner = self.run_id = self.timer = None
        self.generation += 1
        self.deadline = 0

    def _expire(self, generation):
        with self.lock:
            if self.owner is None or generation != self.generation:
                return
            try:
                self.adb('shell', 'am', 'force-stop', SOURCE_PACKAGE)
            except Exception:
                pass
            finally:
                self._clear()

    def control(self, account, request):
        if not isinstance(account, str):
            raise DiagnosticsError(401, 'Login required')
        _object(request, ('action', 'run_id'), ('action', 'run_id'))
        _enum(request['action'], ('start', 'stop'))
        run_id = report_uuid(request['run_id'])
        with self.lock:
            if self.owner is not None and self.clock() >= self.deadline:
                self._expire(self.generation)
            if self.owner is not None and (self.owner != account or self.run_id != run_id):
                raise DiagnosticsError(409, 'Another diagnostic run is in progress')
            try:
                if request['action'] == 'stop':
                    if self.owner is not None:
                        self.adb('shell', 'am', 'force-stop', SOURCE_PACKAGE)
                        self._clear()
                    return {'ok': True, 'action': 'stop', 'run_id': run_id}
                self.ensure_android()
                installed = self.adb('shell', 'pm', 'path', SOURCE_PACKAGE).stdout.strip()
                if not installed.startswith('package:'):
                    raise DiagnosticsError(503, 'Diagnostic scene is not installed')
                started = self.adb('shell', 'am', 'start', '-S', '-n', SOURCE_COMPONENT, '--es', 'run_id', run_id)
                if 'Error:' in started.stdout or 'Exception' in started.stdout:
                    raise DiagnosticsError(503, 'Diagnostic scene unavailable; retry later')
                self._clear()
                self.owner, self.run_id = account, run_id
                self.deadline = self.clock() + self.lease_seconds
                generation = self.generation
                self.timer = self.timer_factory(self.lease_seconds, lambda: self._expire(generation))
                self.timer.daemon = True
                self.timer.start()
                return {'ok': True, 'action': 'start', 'run_id': run_id, 'lease_seconds': self.lease_seconds}
            except DiagnosticsError:
                raise
            except Exception:
                raise DiagnosticsError(503, 'Diagnostic scene unavailable; retry later')

    def shutdown(self):
        with self.lock:
            if self.owner is not None:
                try:
                    self.adb('shell', 'am', 'force-stop', SOURCE_PACKAGE)
                except Exception:
                    pass
                finally:
                    self._clear()

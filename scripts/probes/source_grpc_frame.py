"""Explicit single-frame M1 observer, separate from source input/accessibility.

Construction is inert. The normal authenticated-App driver owns permission and
marker provenance; this reader never establishes a lease from its descriptor.
Tokens and raw pixels stay in memory/private files, outside numeric reports.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import time
import types
import zlib

OWNER_UID = 501
PROTO = Path('/Users/wyw/Documents/ChatGPT/others/android-remote/m1-compare/hardware/proto')
DISCOVERY = Path('/Users/wyw/Library/Caches/TemporaryItems/avd/running')
EMULATORS = frozenset('/Users/wyw/Library/Android/sdk/emulator/qemu/darwin-aarch64/' + name
                     for name in ('qemu-system-aarch64', 'qemu-system-aarch64-headless'))
PINS = {
    'emulator_controller_pb2': '70749973805a66576eb18e693ee8d9c0c5680e132dd8d9ca7ee8fc7861c50a3b',
    'emulator_controller_pb2_grpc': '3eea8fd99e1cb255a43d9c4c2fa154431d8843834887c3b05a62f97b8bce16bb'}
MAX_BYTES = 12 * 1024 * 1024
WIDTH, HEIGHT = 1080, 1920
FIELDS = {'schema', 'emulator_pid', 'emulator_uid', 'emulator_lstart',
          'emulator_argv_sha256', 'output_png'}


class Rejected(ValueError):
    pass


def descriptor(value):
    if (type(value) is not dict or set(value) != FIELDS
            or value['schema'] != 'owner-source-frame-reader-v1'
            or type(value['emulator_pid']) is not int or not 2 <= value['emulator_pid'] <= 2147483647
            or type(value['emulator_uid']) is not int or value['emulator_uid'] != OWNER_UID
            or type(value['emulator_lstart']) is not str
            or re.fullmatch(r'[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}',
                            value['emulator_lstart']) is None
            or type(value['emulator_argv_sha256']) is not str
            or re.fullmatch('[0-9a-f]{64}', value['emulator_argv_sha256']) is None
            or type(value['output_png']) is not str):
        raise Rejected('source_frame_descriptor_rejected')
    path = Path(value['output_png'])
    if not path.is_absolute() or path.name != 'source-frame.png' or '..' in path.parts:
        raise Rejected('source_frame_descriptor_rejected')
    return dict(value)


def read_regular(path, limit, *, private=False):
    """Read one unchanged owned inode. No token/parser error includes file data."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != OWNER_UID
                or before.st_nlink != 1 or not 0 < before.st_size <= limit
                or private and stat.S_IMODE(before.st_mode) != 0o600):
            raise Rejected('source_frame_file_rejected')
        data = bytearray()
        while len(data) <= limit:
            part = os.read(fd, min(65536, limit + 1 - len(data)))
            if not part: break
            data.extend(part)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_nlink, s.st_mode,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise Rejected('source_frame_file_changed')
        return bytes(data), identity(before)
    finally:
        os.close(fd)


def read_deployment(path):
    raw, _ = read_regular(path, 4096, private=True)
    def closed(pairs):
        out = {}
        for k, v in pairs:
            if k in out: raise Rejected('source_frame_descriptor_rejected')
            out[k] = v
        return out
    try:
        return descriptor(json.loads(raw, object_pairs_hook=closed))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Rejected('source_frame_descriptor_rejected') from None


def process_identity(raw, pid):
    """Exact current Mac process bracket, no raw argv in the returned report."""
    if type(raw) is not str or len(raw.encode('utf-8')) > 16384:
        raise Rejected('source_frame_process_rejected')
    match = re.fullmatch(r'\s*([0-9]+)\s+([0-9]+)\s+([A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] '
                         r'[0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4})\s+([^\n]+)\n?', raw)
    if match is None or int(match[1]) != pid or int(match[2]) != OWNER_UID:
        raise Rejected('source_frame_process_rejected')
    try:
        argv = shlex.split(match[4])
    except ValueError:
        raise Rejected('source_frame_process_rejected') from None
    if not argv or argv[0] not in EMULATORS or argv.count('-grpc') != 1:
        raise Rejected('source_frame_process_rejected')
    avd_count = argv.count('-avd') + sum(arg.startswith('@') for arg in argv)
    if avd_count != 1:
        raise Rejected('source_frame_process_rejected')
    if '-avd' not in argv and '@RemoteAndroid17Compare' not in argv:
        raise Rejected('source_frame_process_rejected')
    for option, expected in (('-avd', 'RemoteAndroid17Compare'), ('-grpc', '8556')):
        if option == '-avd' and option not in argv: continue
        index = argv.index(option)
        if index + 1 >= len(argv) or argv[index + 1] != expected:
            raise Rejected('source_frame_process_rejected')
    return {'emulator_pid': int(match[1]), 'emulator_uid': int(match[2]),
            'emulator_lstart': match[3],
            'emulator_argv_sha256': hashlib.sha256(match[4].encode()).hexdigest()}


def discovery(raw):
    values = {}
    try:
        for line in raw.decode('ascii').splitlines():
            if not line.strip() or line.startswith(('#', ';')): continue
            if '=' not in line: raise ValueError()
            key, val = (s.strip() for s in line.split('=', 1))
            if not key or key in values: raise ValueError()
            values[key] = val
    except (ValueError, UnicodeError):
        raise Rejected('source_frame_discovery_rejected') from None
    token = values.get('grpc.token', '')
    if (values.get('avd.name') != 'RemoteAndroid17Compare' or values.get('grpc.port') != '8556'
            or not 1 <= len(token) <= 4096 or any(not 32 <= ord(c) <= 126 for c in token)):
        raise Rejected('source_frame_discovery_rejected')
    return token


def rgba_png(width, height, data):
    if (width, height) != (WIDTH, HEIGHT) or type(data) is not bytes or len(data) != WIDTH * HEIGHT * 4:
        raise Rejected('source_frame_RGBA_rejected')
    def chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body) & 0xffffffff)
    rows = b''.join(b'\0' + data[i:i + width * 4] for i in range(0, len(data), width * 4))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))
    if len(png) > MAX_BYTES: raise Rejected('source_frame_PNG_rejected')
    return png


class Reader:
    def __init__(self, deployment, *, clock=time.monotonic_ns):
        self.deployment = descriptor(deployment)
        self.clock, self.used = clock, False
        self.status = {'executed': False, 'rpc_calls': 0, 'stream_calls': 0,
                       'channel_closed': False, 'private_pixel_file_may_remain': False,
                       'source_input_sent': False, 'UI_runner_started': False,
                       'permission_established_by_descriptor': False}

    def _remaining(self, deadline, cap):
        left = (deadline - self.clock()) / 1e9
        if left <= 0: raise Rejected('source_frame_reader_budget')
        return min(left, cap)

    def _process(self, deadline):
        pid = self.deployment['emulator_pid']
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,uid=,lstart=,command='],
            capture_output=True, text=True, timeout=self._remaining(deadline, 1))
        if result.returncode != 0 or result.stderr:
            raise Rejected('source_frame_process_rejected')
        return process_identity(result.stdout, pid)

    def _proto(self):
        modules = {}
        try:
            # Verify both before executing either. Only the verified snapshots
            # are imported; a preloaded module cannot silently replace a pin.
            data = {name: read_regular(PROTO / (name + '.py'), 256 * 1024)[0] for name in PINS}
            if any(name in sys.modules or hashlib.sha256(data[name]).hexdigest() != pin
                   for name, pin in PINS.items()):
                raise Rejected('source_frame_proto_pin_rejected')
            for name in PINS:
                module = types.ModuleType(name)
                module.__file__, module.__package__ = str(PROTO / (name + '.py')), ''
                modules[name] = module
                sys.modules[name] = module
                exec(compile(data[name], module.__file__, 'exec'), module.__dict__)
            return modules
        except BaseException:
            for name, module in modules.items():
                if sys.modules.get(name) is module: sys.modules.pop(name)
            raise

    def observe(self, ready):
        if self.used: raise Rejected('source_frame_reader_no_retry')
        self.used = True
        if sys.platform != 'darwin' or os.getuid() != OWNER_UID:
            raise Rejected('source_frame_owner_platform_rejected')
        if (type(ready) is not dict or type(ready.get('image_width')) is not int
                or type(ready.get('image_height')) is not int
                or WIDTH * ready['image_height'] != HEIGHT * ready['image_width']):
            raise Rejected('source_frame_geometry_rejected')
        began = self.clock()
        deadline = began + 6_000_000_000
        channel = token = raw = image = png = None
        modules = {}
        folder_fd = None
        error = None
        try:
            before = self._process(deadline)
            if any(before[k] != self.deployment[k] for k in before):
                raise Rejected('source_frame_process_changed')
            output = Path(self.deployment['output_png'])
            folder_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(folder_fd)
            if info.st_uid != OWNER_UID or stat.S_IMODE(info.st_mode) != 0o700:
                raise Rejected('source_frame_output_directory_rejected')
            try:
                os.stat(output.name, dir_fd=folder_fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise Rejected('source_frame_output_preexisting')
            discovery_path = DISCOVERY / ('pid_%d.ini' % before['emulator_pid'])
            raw, inode = read_regular(discovery_path, 16384)
            token = discovery(raw)
            modules = self._proto()
            import grpc
            channel = grpc.insecure_channel('127.0.0.1:8556', options=[
                ('grpc.max_receive_message_length', MAX_BYTES), ('grpc.max_send_message_length', 1024 * 1024),
                ('grpc.enable_http_proxy', 0), ('grpc.enable_retries', 0)])
            grpc.channel_ready_future(channel).result(timeout=self._remaining(deadline, 1))
            pb, service = (modules[name] for name in PINS)
            request = pb.ImageFormat(format=pb.ImageFormat.RGBA8888, width=WIDTH, height=HEIGHT, display=0)
            self.status.update(executed=True, rpc_calls=1)
            image = service.EmulatorControllerStub(channel).getScreenshot(request,
                timeout=self._remaining(deadline, 3), metadata=(('authorization', 'Bearer ' + token),))
            if (image.format.format != pb.ImageFormat.RGBA8888
                    or image.format.width != WIDTH or image.format.height != HEIGHT
                    or len(image.image) != WIDTH * HEIGHT * 4):
                raise Rejected('source_frame_RGBA_rejected')
            after_raw, after_inode = read_regular(discovery_path, 16384)
            if raw != after_raw or inode != after_inode or self._process(deadline) != before:
                raise Rejected('source_frame_process_changed')
            png = rgba_png(WIDTH, HEIGHT, bytes(image.image))
            self._remaining(deadline, 1)
        except BaseException as failure:
            error = failure
        finally:
            if channel is not None:
                try:
                    channel.close()
                    self.status['channel_closed'] = True
                except BaseException as failure:
                    if error is None: error = failure
            for name, module in modules.items():
                if sys.modules.get(name) is module: sys.modules.pop(name)
            token = raw = image = None
        try:
            if error is not None:
                # No RpcError details, imported-code messages or credential data
                # leave this boundary. BaseException is not converted to success.
                raise Rejected('source_frame_reader_failed') from None
            self._remaining(deadline, 1)
            fd = os.open('source-frame.png', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=folder_fd)
            self.status['private_pixel_file_may_remain'] = True
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(png)
                while view:
                    self._remaining(deadline, 1)
                    count = os.write(fd, view)
                    if count <= 0: raise Rejected('source_frame_write_failed')
                    view = view[count:]
                os.fsync(fd)
                file_info = os.fstat(fd)
                named = os.stat('source-frame.png', dir_fd=folder_fd, follow_symlinks=False)
                parent = os.stat(Path(self.deployment['output_png']).parent, follow_symlinks=False)
                held = os.fstat(folder_fd)
                if ((named.st_dev, named.st_ino) != (file_info.st_dev, file_info.st_ino)
                        or (parent.st_dev, parent.st_ino) != (held.st_dev, held.st_ino)
                        or file_info.st_size != len(png) or file_info.st_nlink != 1):
                    raise Rejected('source_frame_output_changed')
            finally:
                os.close(fd)
            finished = self.clock()
            if not 0 <= finished - began <= 6_000_000_000:
                raise Rejected('source_frame_reader_budget')
            self.status['emulator_identity_bracket_verified'] = True
            return dict(width=WIDTH, height=HEIGHT, png_bytes=len(png),
                png_sha256=hashlib.sha256(png).hexdigest(), rpc_calls=1, stream_calls=0,
                grpc_channel_closed=True, elapsed_python_monotonic_ns=finished-began)
        finally:
            if folder_fd is not None: os.close(folder_fd)
            png = None

"""Authenticated photo/video exchange with public Android media only.

Transfers share the existing TLS endpoint; ADB remains on loopback. No root is needed.
"""
import base64
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
import urllib.parse

ROOTS = ('/sdcard/DCIM', '/sdcard/Pictures', '/sdcard/Movies', '/sdcard/Download/火锅互传')
UPLOAD_ROOT = ROOTS[-1]
EXTENSIONS = frozenset('jpg jpeg png webp gif heic heif avif bmp mp4 mov mkv webm 3gp m4v'.split())
MAX_BYTES = 1024 * 1024 * 1024
transfer_slots = threading.BoundedSemaphore(1)

class MediaError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

def media_path(path):
    if not isinstance(path, str) or '\x00' in path or '\n' in path or '\r' in path or len(path.encode()) > 2048:
        raise MediaError(400, 'Invalid media path')
    parts = pathlib.PurePosixPath(path).parts
    if '..' in parts or '.' in path.split('/') or not path.startswith('/'):
        raise MediaError(400, 'Invalid media path')
    if not any(path.startswith(root + '/') for root in ROOTS):
        raise MediaError(403, 'Only public photo/video folders can be accessed')
    if pathlib.PurePosixPath(path).suffix.lower().lstrip('.') not in EXTENSIONS:
        raise MediaError(403, 'Only photo/video files are supported')
    return path

def file_id(path):
    return base64.urlsafe_b64encode(path.encode()).rstrip(b'=').decode()

def decode_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,3000}', value):
        raise MediaError(400, 'Invalid media identifier')
    try:
        return media_path(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True).decode())
    except (ValueError, UnicodeError):
        raise MediaError(400, 'Invalid media identifier')

def upload_name(header):
    name = urllib.parse.unquote(header or '', errors='strict')
    if not name or name in ('.', '..') or '/' in name or '\\' in name or any(ord(c) < 32 for c in name) or len(name.encode()) > 180:
        raise MediaError(400, 'Invalid media filename')
    if pathlib.Path(name).suffix.lower().lstrip('.') not in EXTENSIONS:
        raise MediaError(415, 'Please select a photo or video')
    stem = pathlib.Path(name).stem[:60]
    return time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4) + '-' + stem + pathlib.Path(name).suffix.lower()

class MediaStore:
    def __init__(self, adb, serial, cache):
        self.command = [adb, '-s', serial]
        self.cache = pathlib.Path(cache)

    def shell(self, *args, timeout=30):
        result = subprocess.run(self.command + ['shell', shlex.join(args)], capture_output=True, timeout=timeout, check=True)
        return result.stdout.decode('utf-8', errors='strict').strip()

    def resolve(self, path):
        # Resolve symlinks on Android before every read. /sdcard resolves to emulated storage.
        path = media_path(path)
        actual = self.shell('readlink', '-f', path)
        storage = self.shell('readlink', '-f', '/sdcard')
        roots = [storage.rstrip('/') + root[len('/sdcard'):] for root in ROOTS]
        if not actual or not any(root and actual.startswith(root.rstrip('/') + '/') for root in roots):
            raise MediaError(403, 'Media path escapes its public folder')
        return actual

    def listing(self):
        self.shell('mkdir', '-p', UPLOAD_ROOT)
        result = subprocess.run(self.command + ['exec-out', shlex.join(['find', *ROOTS, '-type', 'f', '-print0'])], capture_output=True, timeout=30)
        # Missing optional media folders do not make the shared inbox unavailable.
        paths = []
        for raw in result.stdout.split(b'\0'):
            try:
                path = media_path(raw.decode())
                paths.append(path)
            except (MediaError, UnicodeError):
                continue
        # Bulk stat avoids one ADB process for every photograph.
        paths = sorted(set(paths))[:500]
        if not paths:
            return {'files': [], 'max_bytes': MAX_BYTES, 'folder': UPLOAD_ROOT, 'truncated': False}
        records = []
        for start in range(0, len(paths), 50):
            group = paths[start:start + 50]
            command = ['stat', '-c', '%s %Y', *group]
            lines = self.shell(*command).splitlines()
            if len(lines) != len(group):
                raise MediaError(503, 'Media changed during listing; refresh the list')
            for path, line in zip(group, lines):
                size, modified = map(int, line.split())
                if 0 < size <= MAX_BYTES:
                    records.append({'id': file_id(path), 'name': pathlib.PurePosixPath(path).name, 'folder': str(pathlib.PurePosixPath(path).parent), 'size': size, 'modified': modified})
        records.sort(key=lambda item: item['modified'], reverse=True)
        return {'files': records, 'max_bytes': MAX_BYTES, 'folder': UPLOAD_ROOT, 'truncated': len(paths) >= 500}

    def upload(self, handler):
        if handler.headers.get('Transfer-Encoding'):
            raise MediaError(400, 'Content-Length is required')
        try:
            length = int(handler.headers.get('Content-Length', '-1'))
        except ValueError:
            raise MediaError(400, 'Invalid upload length')
        if not 0 < length <= MAX_BYTES:
            raise MediaError(413, 'Photo/video must be between 1 byte and 1 GB')
        name = upload_name(handler.headers.get('X-File-Name'))
        destination = UPLOAD_ROOT + '/' + name
        if not transfer_slots.acquire(blocking=False):
            raise MediaError(409, 'Another file transfer is in progress')
        try:
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.shell('mkdir', '-p', UPLOAD_ROOT)
            self.resolve(destination)
            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(prefix='media-', dir=self.cache) as temp:
                os.chmod(temp.name, 0o600)
                handler.connection.settimeout(120)
                remaining = length
                while remaining:
                    chunk = handler.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise MediaError(400, 'Upload was interrupted; no media was imported')
                    temp.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                    handler._media_body_read = length - remaining
                temp.flush()
                remote_temp = UPLOAD_ROOT + '/.' + secrets.token_hex(16) + '.part'
                try:
                    subprocess.run(self.command + ['push', temp.name, remote_temp], capture_output=True, timeout=300, check=True)
                    self.shell('mv', remote_temp, destination)
                finally:
                    self.shell('rm', '-f', remote_temp)
            # MediaProvider performs a synchronous scan and returns the media URI.
            scanned = self.shell('content', 'call', '--uri', 'content://media', '--method', 'scan_file', '--arg', destination)
            return {'name': name, 'id': file_id(destination), 'size': length, 'sha256': digest.hexdigest(), 'folder': UPLOAD_ROOT, 'scanned': 'android.intent.extra.STREAM=' in scanned and 'STREAM=null' not in scanned}
        finally:
            transfer_slots.release()

    def download(self, handler, identifier):
        path = self.resolve(decode_id(identifier))
        size = int(self.shell('stat', '-c', '%s', path))
        if not 0 < size <= MAX_BYTES:
            raise MediaError(413, 'Media is empty or exceeds 1 GB')
        if not transfer_slots.acquire(blocking=False):
            raise MediaError(409, 'Another file transfer is in progress')
        proc = None
        try:
            proc = subprocess.Popen(self.command + ['exec-out', shlex.join(['cat', path])], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            handler.connection.settimeout(120)
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/octet-stream')
            handler.send_header('Content-Length', str(size))
            handler.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(pathlib.PurePosixPath(path).name))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            remaining = size
            while remaining:
                chunk = proc.stdout.read(min(65536, remaining))
                if not chunk:
                    raise OSError('Android media read interrupted')
                handler.wfile.write(chunk)
                remaining -= len(chunk)
            if proc.wait(timeout=10) != 0:
                raise OSError('Android media read failed')
        finally:
            handler.close_connection = True
            if proc:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            transfer_slots.release()

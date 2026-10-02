"""Fail closed before changing the source while a stream capture is active."""
import re
import subprocess


def active_source_capture(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 2*1024*1024:
        raise ValueError('bounded_process_query_required')
    return any(re.search(r'(?:^|\s)(?:[^\s]*/)?hardware_stream\.py(?:\s|$)', line)
               and re.search(r'(?:^|\s)--serial(?:\s+|=)emulator-5556(?:\s|$)', line)
               for line in raw.splitlines())


def require_no_source_capture():
    result = subprocess.run(['ps', '-axo', 'command='], capture_output=True, text=True, timeout=5)
    if result.returncode or active_source_capture(result.stdout):
        raise RuntimeError('active_or_unknown_source_capture_retained')

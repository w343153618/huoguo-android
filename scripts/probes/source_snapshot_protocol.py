"""Inert closed-path/status protocol for the standalone readonly UI runner.

This module creates no files/processes or device sessions. Trusted callers own
admission, deployment, exact metadata verification and actual runner quiescence.
Completed serialization is not source qualification or permission to inject.
"""
import re

NAMESPACE = re.compile(r'huoguo-source-ui-[0-9a-f]{24}')
RUNNER_CLASS = 'local.huoguo.sourceprobe.SourceSnapshot#testSnapshot'
MAX_XML_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 256


def namespace(nonce):
    if type(nonce) is not str or re.fullmatch(r'[0-9a-f]{24}', nonce) is None:
        raise ValueError('snapshot_nonce_rejected')
    return 'huoguo-source-ui-' + nonce


def relative_path(nonce):
    return namespace(nonce) + '/window.xml'


def runner_arguments(nonce):
    # A library value only, not a subprocess or a generic shell argument API.
    owned = namespace(nonce)
    return ('uiautomator', 'runtest', owned + '/snapshot.jar', '-c', RUNNER_CLASS,
            '-e', 'relative', relative_path(nonce))


def receipt(raw, *, expected_uid):
    """Closed numeric row emitted only after dump + metadata checks.

    Caller must compare parent/file device/inode/size with fresh lstat evidence.
    PID/start identify the runner, not the source App. This does NOT prove exit.
    """
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_RECEIPT_BYTES
            or type(expected_uid) is not int or not 0 <= expected_uid <= 2147483647
            or re.fullmatch(rb'1(?: [0-9]{1,19}){8}\n', raw) is None):
        raise ValueError('snapshot_receipt_rejected')
    values = list(map(int, raw[:-1].split()))
    _, pid, uid, start, parent_dev, parent_inode, file_dev, file_inode, size = values
    if (not 1 < pid <= 2147483647 or uid != expected_uid or start <= 0
            or parent_dev < 0 or file_dev < 0 or parent_inode <= 0 or file_inode <= 0
            or any(value > 9223372036854775807 for value in values)
            or not 0 < size < MAX_XML_BYTES):
        raise ValueError('snapshot_receipt_rejected')
    return {'runner_pid': pid, 'runner_uid': uid, 'runner_start_ticks': start,
            'parent_device': parent_dev, 'parent_inode': parent_inode,
            'file_device': file_dev, 'file_inode': file_inode, 'xml_bytes': size,
            'runner_exit_verified': False, 'source_identity_verified': False}


def runner_status(raw):
    """Require actual final legacy-runner success, not one completed file.

    Fixed class/test and exactly one test; unknown/framework error output fails
    conservatively. The complete bounded log is transient, never exported.
    """
    if type(raw) is not bytes or not 0 < len(raw) < 8192:
        return False
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        return False
    if any(token in text for token in ('FAILURES!!!', 'INSTRUMENTATION_FAILED',
            'INSTRUMENTATION_ABORTED', 'INSTRUMENTATION_RESULT: shortMsg=',
            'java.lang.', 'Exception', 'Error', '\x00')):
        return False
    if re.findall(r'^INSTRUMENTATION_CODE: (-?[0-9]+)$', text, re.M) != ['-1']:
        return False
    if re.findall(r'^INSTRUMENTATION_STATUS_CODE: (-?[0-9]+)$', text, re.M) != ['1', '0']:
        return False
    classes = re.findall(r'^INSTRUMENTATION_STATUS: class=(.+)$', text, re.M)
    tests = re.findall(r'^INSTRUMENTATION_STATUS: test=(.+)$', text, re.M)
    if classes not in (['local.huoguo.sourceprobe.SourceSnapshot'],
                       ['local.huoguo.sourceprobe.SourceSnapshot'] * 2):
        return False
    if tests != ['testSnapshot'] * len(classes):
        return False
    if any(v != '1' for v in re.findall(r'^INSTRUMENTATION_STATUS: numtests=(.+)$', text, re.M)):
        return False
    if re.findall(r'^OK \(([0-9]+) test\)$', text, re.M) != ['1']:
        return False
    return True

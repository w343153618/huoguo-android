"""Inert six-file normal-framework-return contract; old five-file binding stays.

Caller must pin the audited installed framework, JAR and owned execution.
A consistent numeric receipt is neither that authority nor global UI quiescence.
"""
import re
from scripts.probes import source_owned_snapshot_binding as previous
from scripts.probes import source_snapshot_protocol as snapshot

FILES = previous.FILES + ('retired',)
RUNNER = 'local.huoguo.sourceprobe.RetirementRunner'


def runner_arguments(scope_nonce, jar_nonce):
    if scope_nonce == jar_nonce:
        raise ValueError('retirement_scope_collision')
    return ('uiautomator', 'runtest', snapshot.namespace(jar_nonce) + '/snapshot.jar',
            '-c', snapshot.RUNNER_CLASS, '-e', 'relative', snapshot.relative_path(scope_nonce),
            '-e', 'runner', RUNNER)


def receipt(raw, *, expected_uid):
    if (type(raw) is not bytes or not 0 < len(raw) <= 192
            or re.fullmatch(rb'1(?: [0-9]{1,19}){5} 1\n', raw) is None
            or type(expected_uid) is not int or not 0 <= expected_uid <= 2147483647):
        raise ValueError('retirement_receipt_rejected')
    _, pid, uid, start, dev, inode, _ = map(int, raw.split())
    if (not 1 < pid <= 2147483647 or uid != expected_uid or start <= 0 or inode <= 0
            or max(start, dev, inode) > previous.MAX_INTEGER):
        raise ValueError('retirement_receipt_rejected')
    return pid, uid, start, dev, inode


def bind(*, retired_raw, files, **args):
    if type(files) is not dict or set(files) != set(FILES):
        raise ValueError('retirement_binding_rejected')
    result = previous.bind(files={name: files[name] for name in previous.FILES}, **args)
    meta = previous._metadata(files['retired'])
    directory = previous._metadata(args['directory'])
    if (meta[0] != args['expected_uid'] or meta[1] != 0o100600 or meta[2] != directory[2]
            or meta[3] <= 0 or meta[3] in {directory[3], *(files[n][3] for n in previous.FILES)}
            or type(retired_raw) is not bytes or meta[4] != len(retired_raw) or meta[5] != 1):
        raise ValueError('retirement_binding_rejected')
    pid, uid, start, dev, inode = receipt(retired_raw, expected_uid=args['expected_uid'])
    completed = snapshot.receipt(args['completed_raw'], expected_uid=uid)
    if ((pid, start) != (completed['runner_pid'], completed['runner_start_ticks'])
            or (dev, inode) != directory[2:4]):
        raise ValueError('retirement_binding_rejected')
    result.update(schema='owned-source-snapshot-retirement-binding-v1',
                  six_file_metadata_consistent=True,
                  normal_framework_start_return_reported=True,
                  installed_framework_execution_verified=False)
    # Avoid implying that the legacy five-file metadata is the whole scope.
    del result['five_file_metadata_consistent']
    return result

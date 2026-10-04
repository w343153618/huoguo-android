"""Inert consistency binding for owned C wait and Java serialization receipts.

No filesystem/process/device operations. The caller must bind a real pinned
execution and retain its permission lease until independently verified cleanup.
Even consistent rows cannot prove actual ownership or UI-service quiescence.
"""
from scripts.probes import source_owned_runner_protocol as owner
from scripts.probes import source_snapshot_protocol as snapshot

FILES = ('started', 'waited', 'completed', 'runner.log', 'window.xml')
MAX_INTEGER = 9223372036854775807


def _metadata(value):
    if (type(value) is not tuple or len(value) != 6
            or any(type(v) is not int or not 0 <= v <= MAX_INTEGER for v in value)):
        raise ValueError('owned_snapshot_binding_rejected')
    return value


def bind(*, started_raw, waited_raw, completed_raw, log, xml,
         directory, files, expected_uid, expected_parent_pid,
         expected_parent_start_ticks):
    """Compare supplied observations, never promote them to an execution lease.

    Metadata tuples are UID, mode, device, inode, bytes, link count. Exact five
    files and parent identity are required. Raw XML remains caller-owned and is
    NOT parsed here; source/Stats/target qualification is an independent step.
    """
    expected = (expected_uid, expected_parent_pid, expected_parent_start_ticks)
    if (any(type(v) is not int or not 0 <= v <= MAX_INTEGER for v in expected)
            or not 0 <= expected_uid <= 2147483647
            or not 1 < expected_parent_pid <= 2147483647
            or expected_parent_start_ticks == 0):
        raise ValueError('owned_snapshot_binding_rejected')
    parent = _metadata(directory)
    if (parent[0] != expected_uid or parent[1] != 0o40700 or parent[3] == 0
            or type(files) is not dict or set(files) != set(FILES)):
        raise ValueError('owned_snapshot_binding_rejected')
    raw = (started_raw, waited_raw, completed_raw, log, xml)
    limits = (513, 513, 257, 8192, snapshot.MAX_XML_BYTES)
    seen = {parent[3]}
    for name, content, limit in zip(FILES, raw, limits):
        meta = _metadata(files[name])
        if (type(content) is not bytes or not 0 < len(content) < limit
                or meta[0] != expected_uid or meta[1] != 0o100600
                or meta[2] != parent[2] or meta[3] == 0 or meta[3] in seen
                or meta[4] != len(content) or meta[5] != 1):
            raise ValueError('owned_snapshot_binding_rejected')
        seen.add(meta[3])
    birth = owner.started(started_raw, expected_uid=expected_uid,
                          expected_device=parent[2], expected_inode=parent[3])
    final = owner.waited(waited_raw, birth)
    done = snapshot.receipt(completed_raw, expected_uid=expected_uid)
    if (birth['parent_pid'] != expected_parent_pid
            or birth['parent_start_ticks'] != expected_parent_start_ticks
            or not final['natural_zero_exit'] or not final['linux_start_ticks_available']
            or final['log_bytes'] != len(log) or not snapshot.runner_status(log)
            or done['runner_pid'] != birth['child_pid']
            or done['runner_start_ticks'] != birth['child_start_ticks']
            or (done['parent_device'], done['parent_inode']) != parent[2:4]
            or (done['file_device'], done['file_inode']) != files['window.xml'][2:4]
            or done['xml_bytes'] != len(xml)):
        raise ValueError('owned_snapshot_binding_rejected')
    return {
        'schema': 'owned-source-snapshot-binding-v1',
        'receipt_chain_consistent': True,
        'expected_parent_identity_matched': True,
        'sole_child_serialization_identity_matched': True,
        'main_child_natural_zero_wait_reported': True,
        'complete_runner_success_reported': True,
        'five_file_metadata_consistent': True,
        'xml_bytes': len(xml),
        'actual_execution_ownership_verified': False,
        'remote_UI_quiescence_verified': False,
        'owned_scope_removed_verified': False,
        'source_qualification_verified': False,
        'permission_lease_verified': False,
    }

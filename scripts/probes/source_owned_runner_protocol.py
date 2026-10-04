"""Inert numeric launcher receipts; file content alone never grants a lease.

These rows describe the actual fork parent's own wait. Caller separately pins
the binary, owns admission, binds real execution and verifies exact files. A
main-child wait does not prove framework/descendant/UI-service quiescence.
"""
import re

MAX_ROW_BYTES = 512
IDENTITY = ('parent_pid', 'parent_start_ticks', 'child_pid', 'uid', 'child_start_ticks', 'device', 'inode')
FINAL = ('reason', 'exit_kind', 'exit_value', 'term_sent', 'kill_sent',
         'child_reaped', 'log_eof', 'log_bytes', 'exec_gate_released',
         'ownership_error', 'elapsed_monotonic_ns')


def _row(raw, count):
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_ROW_BYTES
            or re.fullmatch(rb'1' + rb'(?: [0-9]{1,19})' * count + rb'\n', raw) is None):
        raise ValueError('owned_runner_receipt_rejected')
    values = tuple(map(int, raw.split()[1:]))
    if any(value > 9223372036854775807 for value in values):
        raise ValueError('owned_runner_receipt_rejected')
    return values


def started(raw, *, expected_uid, expected_device, expected_inode):
    expected = (expected_uid, expected_device, expected_inode)
    if any(type(v) is not int or not 0 <= v <= 9223372036854775807 for v in expected):
        raise ValueError('owned_runner_receipt_rejected')
    fields = dict(zip(IDENTITY, _row(raw, len(IDENTITY))))
    if (not 1 < fields['parent_pid'] <= 2147483647
            or not 1 < fields['child_pid'] <= 2147483647
            or fields['parent_pid'] == fields['child_pid']
            or fields['uid'] != expected_uid or fields['device'] != expected_device
            or fields['inode'] != expected_inode or expected_inode == 0):
        raise ValueError('owned_runner_receipt_rejected')
    return fields


def waited(raw, birth):
    if (type(birth) is not dict or set(birth) != set(IDENTITY)
            or any(type(v) is not int for v in birth.values())):
        raise ValueError('owned_runner_receipt_rejected')
    values = dict(zip((*IDENTITY, *FINAL), _row(raw, len(IDENTITY) + len(FINAL))))
    if any(values[key] != birth[key] for key in IDENTITY):
        raise ValueError('owned_runner_receipt_rejected')
    flags = ('term_sent', 'kill_sent', 'child_reaped', 'log_eof',
             'exec_gate_released', 'ownership_error')
    if (any(values[key] not in (0, 1) for key in flags)
            or values['reason'] not in range(5) or values['exit_kind'] not in (0, 1, 2)
            or values['log_bytes'] > 8191
            or values['child_reaped'] != int(values['exit_kind'] != 0)
            or values['kill_sent'] > values['term_sent']
            or (values['exit_kind'] == 0 and values['exit_value'] != 0)
            or (values['exit_kind'] == 1 and values['exit_value'] > 255)
            or (values['exit_kind'] == 2 and not 1 <= values['exit_value'] <= 127)
            or (values['reason'] == 0 and (values['term_sent'] or values['kill_sent']
                                          or values['ownership_error']))):
        raise ValueError('owned_runner_receipt_rejected')
    # Success projection remains distinct from mere exit or pipe closure.
    values['main_child_wait_confirmed'] = bool(values['child_reaped'])
    values['captured_output_closed'] = bool(values['log_eof'])
    values['natural_zero_exit'] = bool(values['reason'] == 0
        and values['exit_kind'] == 1 and values['exit_value'] == 0
        and values['exec_gate_released'] and values['log_eof']
        and not values['ownership_error'])
    values['linux_start_ticks_available'] = bool(values['parent_start_ticks'] and values['child_start_ticks'])
    values['remote_UI_quiescence_verified'] = False
    values['source_qualification_verified'] = False
    values['permission_lease_verified'] = False
    return values

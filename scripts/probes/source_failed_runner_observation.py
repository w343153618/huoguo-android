"""Explicit readonly lifetime observation after a failed owned runner command.

Missing XML/Java retirement must not hide an available C started/waited pair.
This path only reads a previously registered exact scope; it cannot start a UI
session, signal a PID, remove files, release authority or qualify a source.
Receipt consistency remains separate from actual execution and UI retirement.
"""
from scripts.probes import source_owned_snapshot_capture as prior
from scripts.probes import source_owned_runner_protocol as owner
from scripts.probes import source_owned_snapshot_binding as binding
from scripts.probes import source_snapshot_protocol as protocol
from scripts.probes.source_owned_scope_journal import ScopeJournal

META = b'__HG_FAILED_LIFETIME_META__\n'
SECTIONS = tuple(('\n__HG_FAILED_LIFETIME_%s__\n' % name).encode()
                 for name in ('STARTED', 'WAITED', 'END'))
FALSE_FLAGS = ('actual_execution_ownership_verified', 'remote_UI_quiescence_verified',
               'normal_framework_retirement_verified', 'permission_lease_verified',
               'owned_scope_removed_verified', 'source_qualification_verified')
TRUE_FLAGS = ('lifetime_receipt_consistent', 'expected_parent_identity_matched',
              'remote_scope_may_exist', 'lease_release_unaccepted')
NUMBERS = ('reason', 'exit_kind', 'exit_value', 'term_sent', 'kill_sent', 'child_reaped',
           'log_eof', 'log_bytes', 'exec_gate_released', 'ownership_error')


def collection_script(nonce, jar, native):
    scope = '/data/local/tmp/' + protocol.namespace(nonce)
    commands = [prior._pins(jar, native), f'd={scope}',
        '[ -d "$d" ] && [ ! -L "$d" ] || exit 86',
        'printf "__HG_FAILED_LIFETIME_META__\\n"',
        'stat -c \'%u %f %d %i %s %h\' "$d" || exit 87']
    for name in ('started', 'waited'):
        commands.extend((f'[ -f "$d/{name}" ] && [ ! -L "$d/{name}" ] || exit 88',
            f'[ "$(stat -c %s "$d/{name}")" -lt 513 ] || exit 89',
            f'stat -c \'%u %f %d %i %s %h\' "$d/{name}" || exit 90'))
    for name, marker in zip(('started', 'waited'), SECTIONS):
        label = marker.decode().replace('\n', '\\n')
        commands.extend((f'printf "{label}"', f'cat "$d/{name}" || exit 91'))
    label = SECTIONS[-1].decode().replace('\n', '\\n')
    commands.extend((f'printf "{label}"', prior._pins(jar, native)))
    return '; '.join(commands)


def observation(raw, *, uid, parent_pid, parent_start_ticks):
    if (type(raw) is not bytes or not 0 < len(raw) < 4096
            or not raw.startswith(META) or not raw.endswith(SECTIONS[-1])
            or any(raw.count(marker) != 1 for marker in (META, *SECTIONS))):
        raise ValueError('failed_runner_observation_rejected')
    # Reuse the bounded actual-exec parent identity contract, not a PID obtained
    # from ps, receipt files, a serialized previous attempt or an arbitrary JSON.
    if (type(uid) is not int or not 0 <= uid <= 2147483647
            or type(parent_pid) is not int or not 1 < parent_pid <= 2147483647
            or type(parent_start_ticks) is not int
            or not 0 < parent_start_ticks <= binding.MAX_INTEGER):
        raise ValueError('failed_runner_parent_identity_rejected')
    parts, rest = [], raw[len(META):]
    for marker in SECTIONS:
        part, rest = rest.split(marker, 1); parts.append(part)
    if rest: raise ValueError('failed_runner_observation_rejected')
    rows = parts[0].splitlines(keepends=True)
    if len(rows) != 3: raise ValueError('failed_runner_observation_rejected')
    directory, started_meta, waited_meta = (prior.legacy.metadata(row) for row in rows)
    if directory[0] != uid or directory[1] != 0o40700 or directory[3] <= 0:
        raise ValueError('failed_runner_scope_metadata_rejected')
    seen = {directory[3]}
    for meta, content in ((started_meta, parts[1]), (waited_meta, parts[2])):
        if (meta[0] != uid or meta[1] != 0o100600 or meta[2] != directory[2]
                or meta[3] <= 0 or meta[3] in seen or meta[4] != len(content)
                or not 0 < len(content) <= 512 or meta[5] != 1):
            raise ValueError('failed_runner_file_metadata_rejected')
        seen.add(meta[3])
    birth = owner.started(parts[1], expected_uid=uid,
        expected_device=directory[2], expected_inode=directory[3])
    final = owner.waited(parts[2], birth)
    if (birth['parent_pid'] != parent_pid or birth['parent_start_ticks'] != parent_start_ticks
            or birth['child_start_ticks'] <= 0):
        raise ValueError('failed_runner_parent_identity_rejected')
    # Natural0 without a qualified retirement chain is also only a lifetime
    # observation. Missing wait/EOF or ownership error cannot be promoted.
    return {'schema': 'failed-owned-runner-lifetime-v1',
            **{key: final[key] for key in NUMBERS},
            **{key: True for key in TRUE_FLAGS},
            **{key: False for key in FALSE_FLAGS}}


class FailedRunnerObservation:
    def __init__(self, jar, native, journal):
        prior._pins(jar, native)
        if type(journal) is not ScopeJournal:
            raise ValueError('failed_runner_existing_journal_required')
        self.jar, self.native, self.journal = jar, native, journal

    def inspect_registered(self, ticket, parent_header, command_info, reader,
                           assert_current_cleanup_authority):
        # The caller must retain its real cleanup authority separately. A JSON
        # or boolean is not authority, and lost authority prevents further I/O.
        if not callable(assert_current_cleanup_authority):
            raise ValueError('failed_runner_cleanup_authority_required')
        def check():
            if assert_current_cleanup_authority() is not None:
                raise ValueError('failed_runner_cleanup_authority_rejected')
        check()
        self.journal.assert_registered(ticket)
        if (type(command_info) is not dict
                or type(command_info.get('command_ok')) is not bool
                or command_info.get('child_reaped') is not True):
            raise ValueError('failed_runner_local_command_unconfirmed')
        # Nonzero remote launcher result is allowed here. Its independent
        # actual exec header is still mandatory before reading the two receipts.
        pid, start = prior.parent_identity(parent_header)
        check()
        raw, info = reader.read([collection_script(ticket.nonce, self.jar, self.native)], 4096)
        try:
            if info.get('command_ok') is not True or info.get('child_reaped') is not True:
                raise ValueError('failed_runner_observation_command_unconfirmed')
            result = observation(bytes(raw), uid=self.jar.uid,
                                 parent_pid=pid, parent_start_ticks=start)
            check()
            self.journal.record_failure_lifetime_consistency(ticket, result)
            return result
        finally:
            raw.clear()

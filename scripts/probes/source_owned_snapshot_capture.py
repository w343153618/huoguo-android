"""Default-inert owned snapshot observation; never accepts a source or releases a lease.

The caller supplies its CURRENT authority validator and bounded reader. Exact
scope is journaled before possible remote creation. Failures leave it retained.
The caller still owns remote UI-service cleanup and current source qualification.
"""
from dataclasses import dataclass
import re

from scripts.probes import source_snapshot_reader as legacy
from scripts.probes import source_snapshot_protocol as protocol
from scripts.probes import source_owned_snapshot_binding as binding
from scripts.probes.source_owned_scope_journal import ScopeJournal

PARENT = b'__HG_OWNED_PARENT__\n'
PARENT_END = b'__HG_OWNED_PARENT_END__\n'
META = b'__HG_OWNED_META__\n'
SECTIONS = tuple(('\n__HG_OWNED_%s__\n' % name).encode() for name in
                 ('STARTED', 'WAITED', 'COMPLETED', 'LOG', 'XML', 'END'))


@dataclass(frozen=True)
class DeployedNative:
    nonce: str
    sha256: str
    uid: int
    device: int
    inode: int
    size: int
    parent_device: int
    parent_inode: int

    def __post_init__(self):
        # Reuse the existing closed scalar validation, not its JAR path.
        legacy.DeployedJar(self.nonce, self.sha256, self.uid, self.device,
                           self.inode, self.size, self.parent_device, self.parent_inode)

    @property
    def path(self):
        return '/data/local/tmp/huoguo-source-native-' + self.nonce + '/source-owned-runner'


def _pins(jar, native):
    if type(jar) is not legacy.DeployedJar or type(native) is not DeployedNative or jar.uid != native.uid:
        raise ValueError('owned_snapshot_deployment_rejected')
    commands = []
    for item, mode in ((jar, '8180'), (native, '81c0')):
        path = item.path; parent = path.rsplit('/', 1)[0]
        parent_identity = f'{item.uid} 41c0 {item.parent_device} {item.parent_inode}'
        identity = f'{item.uid} {mode} {item.device} {item.inode} {item.size} 1'
        commands.extend((f'[ -d {parent} ] && [ ! -L {parent} ] || exit 80',
            f'[ "$(stat -c \'%u %f %d %i\' {parent})" = "{parent_identity}" ] || exit 81',
            f'[ -f {path} ] && [ ! -L {path} ] || exit 82',
            f'[ "$(stat -c \'%u %f %d %i %s %h\' {path})" = "{identity}" ] || exit 83',
            f'[ "$(sha256sum {path})" = "{item.sha256}  {path}" ] || exit 84'))
    return '; '.join(commands)


def launch_script(nonce, jar, native):
    protocol.namespace(nonce)
    pin = _pins(jar, native)
    if nonce in (jar.nonce, native.nonce):
        raise ValueError('owned_snapshot_namespace_collision')
    # The shell PID/start is emitted from the actual fixed exec context BEFORE
    # native fork/receipt files exist. exec preserves that PID/start identity.
    # All substitutions below are closed nonce/hash/integer fields; no payload,
    # source input, credentials, arbitrary argv or caller shell expression.
    inner = (pin + '; printf "__HG_OWNED_PARENT__\\n"; printf "%s\\n" "$$"; '
             'cat /proc/"$$"/stat || exit 85; printf "__HG_OWNED_PARENT_END__\\n"; '
             f'exec {native.path} --snapshot {nonce} {jar.nonce}')
    # Pin clauses contain literal single quotes for stat. Quote the entire
    # known generated script once, preserving those literal inner quotes.
    return "sh -c '" + inner.replace("'", "'\"'\"'") + "'"


def parent_identity(raw):
    if (type(raw) is not bytes or not 0 < len(raw) < 8192
            or not raw.startswith(PARENT) or not raw.endswith(PARENT_END)
            or raw.count(PARENT) != 1 or raw.count(PARENT_END) != 1):
        raise ValueError('owned_snapshot_exec_identity_rejected')
    words = raw[len(PARENT):-len(PARENT_END)].splitlines()
    if len(words) != 2 or re.fullmatch(rb'[1-9][0-9]{0,9}', words[0]) is None:
        raise ValueError('owned_snapshot_exec_identity_rejected')
    pid = int(words[0])
    match = re.fullmatch(rb'([1-9][0-9]{0,9}) \(sh\) ([^\r\n]+)', words[1])
    if match is None or int(match[1]) != pid or not 1 < pid <= 2147483647:
        raise ValueError('owned_snapshot_exec_identity_rejected')
    fields = match[2].split()
    if (len(fields) < 20 or fields[0] not in (b'R', b'S', b'D')
            or re.fullmatch(rb'[1-9][0-9]{0,18}', fields[19]) is None):
        raise ValueError('owned_snapshot_exec_identity_rejected')
    start = int(fields[19])
    if start > 9223372036854775807:
        raise ValueError('owned_snapshot_exec_identity_rejected')
    return pid, start


def collection_script(nonce, jar, native):
    scope = '/data/local/tmp/' + protocol.namespace(nonce)
    commands = [_pins(jar, native), f'd={scope}',
        '[ -d "$d" ] && [ ! -L "$d" ] || exit 86',
        'printf "__HG_OWNED_META__\\n"', 'stat -c \'%u %f %d %i %s %h\' "$d" || exit 87']
    for name, limit in zip(binding.FILES, (513, 513, 257, 8192, 1048576)):
        commands.extend((f'[ -f "$d/{name}" ] && [ ! -L "$d/{name}" ] || exit 88',
            f'[ "$(stat -c %s "$d/{name}")" -lt {limit} ] || exit 89',
            f'stat -c \'%u %f %d %i %s %h\' "$d/{name}" || exit 90'))
    for name, marker in zip(binding.FILES, SECTIONS):
        label = marker.decode().replace('\n', '\\n')
        commands.extend((f'printf "{label}"', f'cat "$d/{name}" || exit 91'))
    label = SECTIONS[-1].decode().replace('\n', '\\n')
    commands.extend((f'printf "{label}"', _pins(jar, native)))
    return '; '.join(commands)


def observation(raw, *, uid, parent_pid, parent_start_ticks):
    if (type(raw) is not bytes or not 0 < len(raw) < 1048576
            or not raw.startswith(META) or not raw.endswith(SECTIONS[-1])
            or any(raw.count(marker) != 1 for marker in (META, *SECTIONS))):
        raise ValueError('owned_snapshot_observation_rejected')
    parts = []; rest = raw[len(META):]
    for marker in SECTIONS:
        part, rest = rest.split(marker, 1); parts.append(part)
    if rest: raise ValueError('owned_snapshot_observation_rejected')
    meta_rows = parts[0].splitlines(keepends=True)
    if len(meta_rows) != 6: raise ValueError('owned_snapshot_observation_rejected')
    directory = legacy.metadata(meta_rows[0])
    files = dict(zip(binding.FILES, (legacy.metadata(row) for row in meta_rows[1:])))
    result = binding.bind(started_raw=parts[1], waited_raw=parts[2], completed_raw=parts[3],
        log=parts[4], xml=parts[5], directory=directory, files=files, expected_uid=uid,
        expected_parent_pid=parent_pid, expected_parent_start_ticks=parent_start_ticks)
    return result, bytearray(parts[5])


class OwnedSnapshotCapture:
    """Observation only, not a collector adapter; no implicit cleanup/lease release.

    Failure retains exact journal/scope. Successful observation still needs an
    independent UI-service cleanup witness, five-file removal and source bracket.
    Caller must not cancel/release its source authority on an unknown lifecycle.
    """
    def __init__(self, jar, native, journal):
        _pins(jar, native)  # Pure descriptor check, no I/O.
        if type(journal) is not ScopeJournal:
            raise ValueError('owned_snapshot_private_journal_required')
        self.jar, self.native, self.journal = jar, native, journal
        self.possibly_retained = []

    def capture(self, nonce, reader, assert_current_authority):
        # A trusted validator raises on lost authority, returns None on success.
        # A JSON/boolean value is not an authority validator or a permission grant.
        if not callable(assert_current_authority):
            raise ValueError('owned_snapshot_current_authority_required')
        def check():
            if assert_current_authority() is not None:
                raise ValueError('owned_snapshot_current_authority_rejected')
        command = launch_script(nonce, self.jar, self.native)
        check()
        ticket = self.journal.register(nonce)
        self.possibly_retained.append(ticket)
        self.journal.assert_registered(ticket)
        check()
        raw, info = reader.read([command], 8192)
        try:
            if not info['command_ok'] or not info['child_reaped']:
                raise ValueError('owned_snapshot_parent_command_unconfirmed')
            pid, start = parent_identity(bytes(raw))
        finally: raw.clear()
        check()
        raw, info = reader.read([collection_script(nonce, self.jar, self.native)], 1048576)
        xml = None
        try:
            if not info['command_ok'] or not info['child_reaped']:
                raise ValueError('owned_snapshot_observation_command_unconfirmed')
            result, xml = observation(bytes(raw), uid=self.jar.uid,
                                      parent_pid=pid, parent_start_ticks=start)
            check()
            self.journal.record_consistency(ticket, result)
            return xml, result
        except BaseException:
            if xml is not None: xml.clear()
            raise
        finally: raw.clear()

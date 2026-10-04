"""Explicit readonly snapshot adapter; construction/import performs no device I/O.

Caller supplies an already deployed, pinned owned JAR and holds either source
admission or its current authenticated App guest lease. This module never reads
credentials, deploys a JAR, sends input or relaxes an existing collector budget.
On any unconfirmed remote completion it retains the exact owned directory; the
old collector's fallback must not delete a directory beneath a live UI runner.
"""
from dataclasses import dataclass
import math
import re
import time

from scripts.probes import source_decoder_observation as decoder
from scripts.probes import source_stats_observation as stats
from scripts.probes import source_snapshot_protocol as protocol

DIR = b'__HG_SOURCE_DIR__\n'
RECEIPT = b'\n__HG_SOURCE_RECEIPT__\n'
LOG = b'\n__HG_SOURCE_LOG__\n'
XML = b'\n__HG_SOURCE_XML__\n'
END = b'\n__HG_SOURCE_END__\n'


@dataclass(frozen=True)
class DeployedJar:
    nonce: str
    sha256: str
    uid: int
    device: int
    inode: int
    size: int
    parent_device: int
    parent_inode: int

    def __post_init__(self):
        protocol.namespace(self.nonce)
        if (type(self.sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', self.sha256) is None
                or any(type(v) is not int or not 0 <= v <= 9223372036854775807
                       for v in (self.uid, self.device, self.inode, self.size,
                                 self.parent_device, self.parent_inode))
                or not 0 <= self.uid <= 2147483647 or self.inode == 0
                or self.parent_inode == 0 or not 0 < self.size < 1048576):
            raise ValueError('snapshot_deployed_jar_rejected')

    @property
    def path(self):
        return '/data/local/tmp/' + protocol.namespace(self.nonce) + '/snapshot.jar'


def metadata(raw):
    # uid mode dev inode size nlink; %f is the complete stat mode in hex.
    if (type(raw) is not bytes or len(raw) > 256
            or re.fullmatch(rb'[0-9]{1,10} [0-9a-f]{1,8}(?: [0-9]{1,19}){4}\n', raw) is None):
        raise ValueError('snapshot_metadata_rejected')
    words = raw.split()
    values = [int(words[0]), int(words[1], 16), *map(int, words[2:])]
    if any(value > 9223372036854775807 for value in values):
        raise ValueError('snapshot_metadata_rejected')
    return tuple(values)


def regular(meta, uid, *, limit=1048576):
    return (meta[0] == uid and meta[1] == 0o100600 and meta[3] > 0
            and 0 <= meta[4] < limit and meta[5] == 1)


def snapshot_script(nonce, deployed):
    protocol.namespace(nonce)
    if type(deployed) is not DeployedJar or nonce == deployed.nonce:
        raise ValueError('snapshot_deployed_namespace_collision')
    d = '/data/local/tmp/' + protocol.namespace(nonce)
    j = deployed.path
    # No trap: timeout does NOT prove the remote runner finished. Host validates
    # completion and exact runner PID/start before a separate owned cleanup.
    jar_meta = f'{deployed.uid} 8180 {deployed.device} {deployed.inode} {deployed.size} 1'
    jar_parent = '/data/local/tmp/' + protocol.namespace(deployed.nonce)
    parent_meta = f'{deployed.uid} 41c0 {deployed.parent_device} {deployed.parent_inode}'
    return (f'umask 077; j={j}; d={d}; jp={jar_parent}; '
            '[ -d "$jp" ] && [ ! -L "$jp" ] || exit 39; '
            f'[ "$(stat -c \'%u %f %d %i\' "$jp")" = "{parent_meta}" ] || exit 39; '
            '[ -f "$j" ] && [ ! -L "$j" ] || exit 40; '
            f'[ "$(stat -c \'%u %f %d %i %s %h\' "$j")" = "{jar_meta}" ] || exit 41; '
            f'[ "$(sha256sum "$j")" = "{deployed.sha256}  {j}" ] || exit 42; '
            'mkdir "$d" || exit 43; '
            'printf "__HG_SOURCE_DIR__\\n"; stat -c \'%u %f %d %i %s %h\' "$d" || exit 44; '
            f'uiautomator runtest {protocol.namespace(deployed.nonce)}/snapshot.jar '
            f'-c {protocol.RUNNER_CLASS} -e relative {protocol.relative_path(nonce)} '
            '>"$d/runner.log" 2>&1 || exit 45; '
            '[ -f "$j" ] && [ ! -L "$j" ] && [ -d "$jp" ] && [ ! -L "$jp" ] || exit 60; '
            f'[ "$(stat -c \'%u %f %d %i\' "$jp")" = "{parent_meta}" ] || exit 60; '
            f'[ "$(stat -c \'%u %f %d %i %s %h\' "$j")" = "{jar_meta}" ] || exit 61; '
            f'[ "$(sha256sum "$j")" = "{deployed.sha256}  {j}" ] || exit 62; '
            'for f in completed runner.log window.xml; do '
            '[ -f "$d/$f" ] && [ ! -L "$d/$f" ] || exit 46; done; '
            '[ "$(stat -c %s "$d/completed")" -le 256 ] && '
            '[ "$(stat -c %s "$d/runner.log")" -lt 8192 ] && '
            '[ "$(stat -c %s "$d/window.xml")" -lt 1048576 ] || exit 47; '
            'printf "\\n__HG_SOURCE_RECEIPT__\\n"; cat "$d/completed" || exit 48; '
            'printf "\\n__HG_SOURCE_LOG__\\n"; cat "$d/runner.log" || exit 49; '
            'printf "\\n__HG_SOURCE_XML__\\n"; cat "$d/window.xml" || exit 50; '
            'printf "\\n__HG_SOURCE_END__\\n"')


def parse_snapshot(raw, uid):
    if type(raw) is not bytes or not 0 < len(raw) < decoder.MAX_DUMP_BYTES:
        raise ValueError('snapshot_pipe_rejected')
    if (not raw.startswith(DIR) or not raw.endswith(END)
            or any(raw.count(marker) != 1 for marker in (DIR, RECEIPT, LOG, XML, END))):
        raise ValueError('snapshot_pipe_rejected')
    directory, rest = raw[len(DIR):-len(END)].split(RECEIPT)
    receipt, rest = rest.split(LOG)
    log, xml = rest.split(XML)
    parent = metadata(directory)
    done = protocol.receipt(receipt, expected_uid=uid)
    if (parent[0] != uid or parent[1] != 0o40700 or parent[3] <= 0
            or parent[2:4] != (done['parent_device'], done['parent_inode'])
            or len(xml) != done['xml_bytes'] or not protocol.runner_status(log)):
        raise ValueError('snapshot_completion_rejected')
    return parent, done, xml


def runner_query_script(done):
    pid = done['runner_pid']
    if type(pid) is not int or not 1 < pid <= 2147483647:
        raise ValueError('snapshot_runner_pid_rejected')
    # PID and start come from the closed authenticated-to-filesystem receipt.
    # A PID may have been reused: that fact proves only THIS prior runner exited.
    return (f'if [ ! -d /proc/{pid} ]; then printf "__HG_RUNNER_ABSENT__\\n"; '
            f'else cat /proc/{pid}/stat || exit 59; fi')


def runner_exited(raw, done):
    if raw == b'__HG_RUNNER_ABSENT__\n':
        return True
    if type(raw) is not bytes or len(raw) >= 8192:
        return False
    match = re.fullmatch(rb'([1-9][0-9]{0,9}) \([^\r\n]*\) ([^\r\n]+)\n?', raw)
    if not match or int(match[1]) != done['runner_pid']:
        return False
    fields = match[2].split()
    return (len(fields) >= 20 and re.fullmatch(rb'[0-9]{1,19}', fields[19]) is not None
            and int(fields[19]) != done['runner_start_ticks'])


def files_script(nonce):
    d = '/data/local/tmp/' + protocol.namespace(nonce)
    return ('d=' + d + '; for f in completed runner.log window.xml; do '
            '[ -f "$d/$f" ] && [ ! -L "$d/$f" ] || exit 51; '
            'stat -c \'%u %f %d %i %s %h\' "$d/$f" || exit 52; done')


def cleanup_script(nonce, parent, files):
    d = '/data/local/tmp/' + protocol.namespace(nonce)
    def checked(meta):
        if (type(meta) is not tuple or len(meta) != 6
                or any(type(v) is not int or not 0 <= v <= 9223372036854775807 for v in meta)):
            raise ValueError('snapshot_cleanup_metadata_rejected')
    checked(parent)
    if parent[1] != 0o40700 or parent[3] <= 0 or type(files) is not tuple or len(files) != 3:
        raise ValueError('snapshot_cleanup_metadata_rejected')
    for meta in files:
        checked(meta)
        if not regular(meta, parent[0]): raise ValueError('snapshot_cleanup_metadata_rejected')
    def line(meta): return ' '.join((str(meta[0]), format(meta[1], 'x'), *map(str, meta[2:])))
    parent_identity = ' '.join((str(parent[0]), format(parent[1], 'x'), *map(str, parent[2:4])))
    commands = ['d=' + d, '[ -d "$d" ] && [ ! -L "$d" ] || exit 53',
        '[ "$(stat -c \'%u %f %d %i\' "$d")" = "'+parent_identity+'" ] || exit 54']
    for name, meta in zip(('completed', 'runner.log', 'window.xml'), files):
        commands.extend([f'[ -f "$d/{name}" ] && [ ! -L "$d/{name}" ] || exit 55',
            f'[ "$(stat -c \'%u %f %d %i %s %h\' "$d/{name}")" = "{line(meta)}" ] || exit 56'])
    commands += ['rm -f "$d/completed" "$d/runner.log" "$d/window.xml" && rmdir "$d" || exit 57',
                 '[ ! -e "$d" ] || exit 58']
    return '; '.join(commands)


class DirectDumpReader:
    """Explicit factory adapter for existing Stats/target collectors only.

    Successful private raw receipt is translated to the old closed XML pipe
    after runner exit, matching file metadata and actual owned temp removal.
    Existing source identity/focus/owner/geometry checks run unchanged.
    """
    def __init__(self, adb, serial, timeout, deployed, *,
                 reader_factory=decoder.BoundedReader):
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 3 <= timeout <= 15):
            raise ValueError('snapshot_collector_budget_rejected')
        if type(deployed) is not DeployedJar:
            raise ValueError('snapshot_owned_deployment_required')
        self.reader = reader_factory(adb, serial, timeout)
        self.deployed, self.possibly_retained = deployed, []
        self.serialization_verified = False
        self.runner_exit_verified = False

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def _failed(self, info):
        result = dict(info, command_ok=False, error_code=8,
                      host_finished_monotonic_ns=time.monotonic_ns(),
                      snapshot_method='legacy_direct_dump',
                      snapshot_serialization_verified=self.serialization_verified,
                      snapshot_runner_exit_verified=self.runner_exit_verified,
                      snapshot_owned_scope_may_remain=bool(self.possibly_retained))
        return bytearray(), result

    def read(self, args, byte_limit=decoder.MAX_DUMP_BYTES):
        if len(args) != 1 or type(args[0]) is not str:
            return self.reader.read(args, byte_limit)
        match = re.search(r'/data/local/tmp/huoguo-source-stats-([0-9a-f]{24})', args[0])
        if match is None:
            return self.reader.read(args, byte_limit)
        nonce = match[1]
        if args != [stats.snapshot_script(nonce)]:
            # Never run the old collector's deletion against an unconfirmed job.
            return self._failed(decoder.command_info())
        self.serialization_verified = False
        self.runner_exit_verified = False
        # Even an empty/truncated ADB result does not prove mkdir/runner never
        # ran. Register the exact candidate scope before the owned command.
        self.possibly_retained.append(protocol.namespace(nonce))
        raw, info = self.reader.read([snapshot_script(nonce, self.deployed)], byte_limit)
        try:
            if not info['command_ok'] or not info['child_reaped']:
                return self._failed(info)
            parent, done, xml = parse_snapshot(bytes(raw), self.deployed.uid)
            self.serialization_verified = True
            check, exit_info = self.reader.read([runner_query_script(done)], 8192)
            try:
                exited = exit_info['command_ok'] and exit_info['child_reaped'] and runner_exited(bytes(check), done)
            finally: check.clear()
            if not exited: return self._failed(info)
            self.runner_exit_verified = True
            meta, meta_info = self.reader.read([files_script(nonce)], 1024)
            try:
                if not meta_info['command_ok'] or not meta_info['child_reaped']:
                    return self._failed(info)
                rows = bytes(meta).splitlines(keepends=True)
                if len(rows) != 3: return self._failed(info)
                files = tuple(metadata(row) for row in rows)
                if not all(regular(v, self.deployed.uid, limit=8192 if i == 1 else 1048576)
                           for i, v in enumerate(files)):
                    return self._failed(info)
                if files[2][2:5] != (done['file_device'], done['file_inode'], done['xml_bytes']):
                    return self._failed(info)
            finally: meta.clear()
            clean, clean_info = self.reader.read([cleanup_script(nonce, parent, files)], 1024)
            try:
                removed = clean_info['command_ok'] and clean_info['child_reaped'] and not clean
            finally: clean.clear()
            if not removed: return self._failed(info)
            self.possibly_retained.remove(protocol.namespace(nonce))
            pipe = bytearray(stats.UI_CREATED + xml + stats.UI_DONE)
            return pipe, dict(info, raw_bytes=len(pipe),
                host_finished_monotonic_ns=time.monotonic_ns(),
                snapshot_method='legacy_direct_dump', snapshot_serialization_verified=True,
                snapshot_runner_exit_verified=True, snapshot_owned_scope_may_remain=False)
        except (ValueError, TypeError):
            return self._failed(info)
        finally:
            raw.clear()

"""Default-OFF readonly receipts for the dedicated phone helper's private scope.

No installer, uninstall, deletion, credential reader, CLI or lease release is
provided. Only a reviewed root caller may use these observations with its actual
held mutation clients. A parsed receipt or matching APK is never permission or
ownership. Remote path/stat/hash brackets are not an atomic filesystem hold.
"""
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import stat
import time

from scripts.probes.owner_native_gateway_probes import Commands, ProbeError
from scripts.probes.owner_native_phone_probes import PHONE, HELPER, package_path
from scripts.probes.owner_native_window import Window


MAX_INT = (1 << 63) - 1
STAGE = re.compile(r'/data/local/tmp/huoguo-native-helper-[0-9a-f]{24}')


@dataclass(frozen=True)
class Node:
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    links: int
    size: int

    @property
    def identity(self):
        # Directory size can change when its one allowed entry is added/removed.
        return self.device, self.inode, self.uid, self.gid, self.mode, self.links


@dataclass(frozen=True)
class Snapshot:
    directory: Node | None
    apk: Node | None
    apk_sha256: str | None


@dataclass(frozen=True)
class InstalledHelper:
    path: str
    apk: Node
    sha256: str


def _node(line, tag):
    match = re.fullmatch(re.escape(tag) + r' ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9a-f]+) ([0-9]+) ([0-9]+)', line)
    if match is None:
        raise ProbeError('native_helper_scope_metadata_unknown')
    values = tuple(int(x, 16 if index == 4 else 10) for index, x in enumerate(match.groups()))
    if (any(x > MAX_INT for x in values) or values[1] == 0 or values[5] == 0
            or values[2] > 999999 or values[3] > 999999 or values[4] > 0xffff):
        raise ProbeError('native_helper_scope_metadata_unknown')
    return Node(*values)


def parse_snapshot(raw, helper_sha256):
    """Closed bounded text only; default performs no filesystem/device access."""
    if (type(raw) is not str or not raw.isascii() or len(raw) > 4096
            or type(helper_sha256) is not str or re.fullmatch('[0-9a-f]{64}', helper_sha256) is None):
        raise ProbeError('native_helper_scope_receipt_unknown')
    lines = raw.splitlines()
    if lines == ['HGHS1', 'absent', 'END']:
        return Snapshot(None, None, None)
    if len(lines) not in (5, 9) or lines[0] != 'HGHS1' or lines[-1] != 'END':
        raise ProbeError('native_helper_scope_receipt_unknown')
    directory = _node(lines[1], 'dir')
    if (directory.uid != 2000 or directory.gid != 2000 or directory.mode != stat.S_IFDIR | 0o700
            or not 1 <= directory.links <= 4):
        raise ProbeError('native_helper_scope_directory_unknown')
    if len(lines) == 5:
        if lines[2] != 'empty' or _node(lines[3], 'dir_end') != directory:
            raise ProbeError('native_helper_scope_bracket_changed')
        return Snapshot(directory, None, None)
    if lines[2] != 'entry owned.apk':
        raise ProbeError('native_helper_scope_foreign_entry')
    apk = _node(lines[3], 'file')
    if (apk.uid != 2000 or apk.gid != 2000 or apk.mode != stat.S_IFREG | 0o600
            or apk.links != 1 or not 0 < apk.size <= 131072 or apk.device != directory.device):
        raise ProbeError('native_helper_scope_APK_unknown')
    if lines[4] != 'sha ' + helper_sha256:
        raise ProbeError('native_helper_scope_APK_mismatch')
    if (_node(lines[5], 'file_end') != apk or lines[6] != 'entry_end owned.apk'
            or _node(lines[7], 'dir_end') != directory):
        raise ProbeError('native_helper_scope_bracket_changed')
    return Snapshot(directory, apk, helper_sha256)


def same_scope(first, last, *, file_required):
    """Compare captured identities; no path/PID adoption or mutation authority."""
    if type(first) is not Snapshot or type(last) is not Snapshot or type(file_required) is not bool:
        raise ValueError('native_helper_scope_actual_snapshots_required')
    if first.directory is None or last.directory is None or first.directory.identity != last.directory.identity:
        raise ProbeError('native_helper_scope_owned_directory_changed')
    if file_required and (first.apk is None or last.apk != first.apk or last.apk_sha256 != first.apk_sha256):
        raise ProbeError('native_helper_scope_owned_APK_changed')
    return True


def _stat(path, tag):
    # Android toybox stat defaults to lstat; there is deliberately no -L.
    return 'stat -c ' + shlex.quote(tag + ' %d %i %u %g %f %h %s') + ' ' + shlex.quote(path)


def scope_command(stage):
    if type(stage) is not str or STAGE.fullmatch(stage) is None:
        raise ValueError('native_helper_scope_closed_namespace_required')
    path = shlex.quote(stage)
    apk = stage + '/owned.apk'
    # One closed directory, no recursive walk, writes, secrets or fallback.
    # Unknown/extra entries fail before hash reads. The runner still bounds the
    # command; shell glob/stat/hash are not a hard filesystem wall-clock bound.
    return ('set -e; printf "HGHS1\n"; '
        'if [ ! -e ' + path + ' ] && [ ! -L ' + path + ' ]; then printf "absent\nEND\n"; exit 0; fi; '
        '[ ! -L ' + path + ' ] && [ -d ' + path + ' ] || exit 1; '
        + _stat(stage, 'dir') + '; '
        'entries=$(ls -A ' + path + '); '
        'if [ -z "$entries" ]; then [ -z "$(ls -A ' + path + ')" ] || exit 1; printf "empty\n"; '
        + _stat(stage, 'dir_end') + '; printf "END\n"; exit 0; fi; '
        '[ "$entries" = owned.apk ] || exit 1; '
        '[ ! -L ' + shlex.quote(apk) + ' ] && [ -f ' + shlex.quote(apk) + ' ] || exit 1; '
        'printf "entry owned.apk\n"; '
        + _stat(apk, 'file') + '; '
        'digest=$(sha256sum ' + shlex.quote(apk) + '); printf "sha %s\n" "${digest%% *}"; '
        + _stat(apk, 'file_end') + '; '
        '[ "$(ls -A ' + path + ')" = owned.apk ] || exit 1; printf "entry_end owned.apk\n"; '
        + _stat(stage, 'dir_end') + '; printf "END\n"')


class ScopeProbes:
    """Explicit finite readonly adapter. Construction and parsers are inert.

    This is not a remote openat/O_NOFOLLOW file descriptor or an atomic package
    hold. Local ADB reap, valid serialization and SHA matches never establish
    PM quiescence, installed-helper ownership or permission to unlink/uninstall.
    """
    def __init__(self, window, adb, stage, *, environment, commands=None):
        if (type(window) is not Window or not isinstance(adb, Path) or not adb.is_absolute()
                or type(stage) is not str or STAGE.fullmatch(stage) is None):
            raise ValueError('native_helper_scope_reviewed_objects_required')
        window.__post_init__()
        from scripts.probes.owner_native_gateway_environment import select
        # Same fixed selected child environment as the gateway/driver. The
        # caller supplies reviewed references, never an inherited environment.
        try:
            home = Path(environment['HOME'])
            reports = Path(environment['DIRECT_DIAGNOSTICS_DIR'])
            if reports.parts[-2:] != ('gateway', 'reports'):
                raise ValueError('reports')
            refs = {k: environment[k] for k in ('DIRECT_AUTH_FILE', 'DIRECT_CERT', 'DIRECT_KEY', 'DIRECT_VIDEO_BACKEND')}
            expected = select(home, adb, Path(environment['DIRECT_STATE_DIR']),
                reports.parents[1], refs)
        except (KeyError, TypeError, ValueError, IndexError):
            raise ValueError('native_helper_scope_exact_environment_required') from None
        if environment != expected:
            raise ValueError('native_helper_scope_exact_environment_required')
        self.window, self.adb, self.stage = window, adb, stage
        self.commands = Commands(env=environment) if commands is None else commands
        self.last_scope = self.last_installed = None

    def _run(self, words, end, *, root=False, allowed=(0,)):
        left = end - time.monotonic()
        if left <= 0:
            raise ProbeError('native_helper_scope_read_budget_expired')
        args = ('shell', 'su -c ' + shlex.quote(shlex.join(words))) if root else words
        return self.commands.run((str(self.adb), '-s', PHONE) + args,
            seconds=min(3, left), bound=4096, allowed=allowed)

    def _begin(self):
        end = time.monotonic() + 15
        if self._run(('get-state',), end).strip() != 'device':
            raise ProbeError('native_helper_scope_phone_unavailable')
        if self._run(('id', '-u'), end, root=True).strip() != '0':
            raise ProbeError('native_helper_scope_root_read_unverified')
        return end

    def _complete(self):
        if self.commands.pending or self.commands.started != self.commands.reaped:
            raise ProbeError('native_helper_scope_local_reap_unverified')

    def snapshot(self):
        self.last_scope = None
        end = self._begin()
        value = parse_snapshot(self._run(('sh', '-c', scope_command(self.stage)), end, root=True),
                               self.window.plan.helper_sha256)
        self._complete()
        self.last_scope = value
        return value

    def installed(self):
        """Exact user0 helper path/metadata/hash/path bracket, never ownership."""
        self.last_installed = None
        end = self._begin()
        args = ('shell', 'pm', 'path', '--user', '0', HELPER)
        path = package_path(self._run(args, end))
        first = _node(self._run(('stat', '-c', 'apk %d %i %u %g %f %h %s', path), end, root=True).strip(), 'apk')
        if (first.mode != stat.S_IFREG | 0o644 or first.uid not in (0, 1000)
                or first.gid not in (0, 1000) or first.links != 1 or not 0 < first.size <= 131072):
            raise ProbeError('native_helper_installed_metadata_unknown')
        if self._run(('sha256sum', path), end, root=True).strip() != self.window.plan.helper_sha256 + '  ' + path:
            raise ProbeError('native_helper_installed_APK_mismatch')
        last = _node(self._run(('stat', '-c', 'apk %d %i %u %g %f %h %s', path), end, root=True).strip(), 'apk')
        if first != last or package_path(self._run(args, end)) != path:
            raise ProbeError('native_helper_installed_bracket_changed')
        self._complete()
        value = InstalledHelper(path, first, self.window.plan.helper_sha256)
        self.last_installed = value
        return value


def held_driver_completed(coordinator):
    """Actual held coordinator Popen/EOF gate, independent of report JSON.

    Failure exits can still be observed safely; this does not say the remote
    instrumentation, PM, helper, App or source is quiescent. No signal, uninstall
    or reservation release occurs. Independent phone and scope reads remain
    required before the caller's owned-helper cleanup or final gateway finish.
    """
    from scripts.probes.owner_native_gateway_coordinator import Coordinator
    if type(coordinator) is not Coordinator or coordinator.driver is None:
        raise ProbeError('native_helper_actual_held_driver_required')
    child = coordinator.driver
    if type(child.poll()) is not int:
        raise ProbeError('native_helper_driver_exit_unknown')
    drains = coordinator.driver_drains
    end = time.monotonic() + 2
    for drain in drains:
        drain.thread.join(timeout=max(0, end - time.monotonic()))
    if len(drains) != 2 or any(d.thread.is_alive() or d.failed or d.overflow for d in drains):
        raise ProbeError('native_helper_driver_EOF_unknown')
    # Reading the actual returncode, not a JSON pid/exit/event projection.
    return dict(actual_driver_exit=child.returncode, actual_driver_both_pipes_EOF=True,
        driver_success=child.returncode == 0, remote_PM_or_helper_quiescence=False,
        scope_removal_or_uninstall_authorized=False, reservation_release_authorized=False)

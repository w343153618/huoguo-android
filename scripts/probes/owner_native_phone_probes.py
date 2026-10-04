"""Explicit readonly phone qualification for the private M1 native window.

Construction is inert. No store, password, installer, input, UiAutomation or
permission-by-JSON exists here. Exact installed APK bytes bind the previously
verified local signature/JNI; this is not a separate remote JNI extraction.
Normal saved-UI restore and current Attempt remain the matched helper's gates.
"""
from pathlib import Path
import re
import shlex
import time

from scripts.probes.owner_native_gateway_probes import Commands, ProbeError
from scripts.probes.owner_native_window import Window


PHONE = 'f7fc9469'
APP = 'local.remoteandroid.direct.experiment'
HELPER = 'local.huoguo.lanuitest'
INPUT = '/data/user/0/' + APP + '/files/udp-test-login.json'


def package_path(raw):
    if type(raw) is not str or len(raw) > 4096:
        raise ProbeError('native_phone_package_path_unknown')
    match = re.fullmatch(r'package:(/data/app/[A-Za-z0-9_/+~=.-]+/base[.]apk)\s*', raw)
    if match is None:
        raise ProbeError('native_phone_package_path_unknown')
    return match[1]


def package_version(raw, expected):
    if type(raw) is not str or len(raw) > 262144 or type(expected) is not int:
        raise ProbeError('native_phone_package_version_unknown')
    versions = re.findall(r'^\s*versionCode=([0-9]+)(?:\s.*)?$', raw, re.MULTILINE)
    names = re.findall(r'^\s*versionName=(\S+)\s*$', raw, re.MULTILINE)
    # Past/foreign/duplicate sections are unknown, never a permissive first hit.
    if versions != [str(expected)] or names != ['1.31-alpha.9']:
        raise ProbeError('native_phone_package_version_unknown')


def processes(raw):
    if type(raw) is not str or len(raw) > 1048576:
        raise ProbeError('native_phone_process_inventory_unknown')
    lines = raw.splitlines()
    if not lines or lines[0].split() != ['PID', 'UID', 'NAME'] or len(lines) < 2:
        raise ProbeError('native_phone_process_inventory_unknown')
    rows = {}
    for line in lines[1:]:
        # NAME is the final ps column, not a shell word. Actual Android kernel
        # threads include names such as "[irq/260-q6v5 wdog]". Keep the full
        # name and still refuse incomplete, oversized or control-bearing rows.
        match = re.fullmatch(r'\s*([0-9]+)\s+([0-9]+)\s+(\S(?:.*\S)?)\s*', line)
        if (match is None or len(line) > 4096
                or any(ord(c) < 32 or ord(c) == 127 for c in match[3])):
            raise ProbeError('native_phone_process_inventory_unknown')
        pid, uid, name = int(match[1]), int(match[2]), match[3]
        # The inventory covers every Android user. A real clone-profile UID
        # (99910267) exceeds a user0-only range; keep its full unsigned UID.
        # This does not change the separate user0 package/artifact qualification.
        if not 0 < pid <= 4194304 or not 0 <= uid < 4294967295 or pid in rows:
            raise ProbeError('native_phone_process_inventory_unknown')
        rows[pid] = (uid, name)
    return rows


def idle(rows):
    # Name prefixes cover secondary App/helper processes. An absent main pidof
    # alone would not cover these; neither check establishes an atomic hold.
    # A space-bearing name using a protected package prefix is ambiguous;
    # refusing it prevents the full-column parser from masking that process.
    return not any(name == p or name.startswith((p + ':', p + ' '))
                   for _, name in rows.values() for p in (APP, HELPER))


def cpu(raw):
    if type(raw) is not str or not 0 < len(raw) <= 4096:
        raise ProbeError('native_phone_CPU_unknown')
    result = {}
    for line in raw.splitlines():
        match = re.fullmatch(r'(policy[0-9]+) ([0-9]+) ([0-9]+) ([0-9]+)\s*', line)
        if match is None or match[1] in result:
            raise ProbeError('native_phone_CPU_unknown')
        values = tuple(map(int, match.group(2, 3, 4)))
        if not all(0 < value <= 10000000 for value in values) or values[0] > values[1]:
            raise ProbeError('native_phone_CPU_unknown')
        # Sequential current-frequency reads can exceed a changing cap. Keep
        # that observed value; never rewrite it or call endpoints CPU-matched.
        result[match[1]] = values
    if set(result) != {'policy0', 'policy2', 'policy5', 'policy7'}:
        raise ProbeError('native_phone_CPU_unknown')
    return result


CPU_READ = ('for p in /sys/devices/system/cpu/cpufreq/policy*; do '
            'printf "%s " "${p##*/}"; '
            'for n in scaling_min_freq scaling_max_freq scaling_cur_freq; do '
            'v=$(cat "$p/$n") || exit 1; printf "%s " "$v"; done; echo; done')


class PhoneProbes:
    """Bounded readonly callbacks; caller still owns install and permission.

    A successful snapshot means only actual device/artifact/process/settings
    readback. It neither owns a helper installation nor proves saved credentials,
    current App Attempt, server lease, source motion or native ART export.
    """
    def __init__(self, window, adb, commands=None):
        if type(window) is not Window or not isinstance(adb, Path) or not adb.is_absolute():
            raise ValueError('native_phone_reviewed_paths_required')
        window.__post_init__()
        self.window, self.adb = window, adb
        self.commands = Commands() if commands is None else commands
        self.last_snapshot = None

    def _run(self, words, end, *, root=False, bound=4096, allowed=(0,)):
        left = end - time.monotonic()
        if left <= 0:
            raise ProbeError('native_phone_read_budget_expired')
        # Every remote operation is a literal readonly command. Local ADB reap
        # is not claimed to be remote UiAutomation/installer quiescence.
        if root:
            args = ('shell', 'su -c ' + shlex.quote(shlex.join(words)))
        else:
            args = words
        return self.commands.run((str(self.adb), '-s', PHONE) + args,
            seconds=min(3, left), bound=bound, allowed=allowed)

    def _installed(self, package, digest, end):
        first = package_path(self._run(('shell', 'pm', 'path', '--user', '0', package), end))
        raw = self._run(('sha256sum', first), end, root=True)
        if raw.strip() != digest + '  ' + first:
            raise ProbeError('native_phone_installed_APK_mismatch')
        last = package_path(self._run(('shell', 'pm', 'path', '--user', '0', package), end))
        if first != last:
            raise ProbeError('native_phone_package_path_changed')

    def snapshot(self, *, helper_installed=False):
        if type(helper_installed) is not bool:
            raise ValueError('native_phone_explicit_helper_phase_required')
        self.last_snapshot = None
        end = time.monotonic() + 15
        if self._run(('get-state',), end).strip() != 'device':
            raise ProbeError('native_phone_unavailable')
        before = processes(self._run(('shell', 'ps', '-A', '-o', 'PID,UID,NAME'), end, bound=1048576))
        if not idle(before):
            raise ProbeError('native_phone_App_or_helper_active')
        if self._run(('id', '-u'), end, root=True).strip() != '0':
            raise ProbeError('native_phone_root_read_unverified')
        self._installed(APP, self.window.plan.app_sha256, end)
        package_version(self._run(('shell', 'dumpsys', 'package', APP), end, bound=262144),
                        self.window.plan.app_version_code)
        if helper_installed:
            self._installed(HELPER, self.window.plan.helper_sha256, end)
        elif self._run(('shell', 'pm', 'path', '--user', '0', HELPER), end, allowed=(0, 1)).strip():
            raise ProbeError('native_phone_preexisting_helper')
        # Exist-or-symlink only, never contents, no delete or private-file fallback.
        command = 'if [ -e ' + shlex.quote(INPUT) + ' ] || [ -L ' + shlex.quote(INPUT) + ' ]; then echo present; else echo absent; fi'
        if self._run(('sh', '-c', command), end, root=True).strip() != 'absent':
            raise ProbeError('native_phone_preexisting_private_input')
        profile = cpu(self._run(('sh', '-c', CPU_READ), end, root=True).strip())
        for setting in ('min_refresh_rate', 'peak_refresh_rate'):
            if self._run(('shell', 'settings', 'get', 'system', setting), end).strip() not in ('120', '120.0'):
                raise ProbeError('native_phone_global_refresh_unverified')
        after = processes(self._run(('shell', 'ps', '-A', '-o', 'PID,UID,NAME'), end, bound=1048576))
        if not idle(after):
            raise ProbeError('native_phone_App_or_helper_active')
        if self.commands.pending or self.commands.started != self.commands.reaped:
            raise ProbeError('native_phone_local_reap_unverified')
        self.last_snapshot = dict(actual_installed_APK_bytes_match=True,
            JNI_and_signer_bound_by_preverified_exact_APK=True,
            remote_JNI_or_signer_extracted=False, helper_installed=helper_installed,
            App_and_helper_processes_absent_in_both_reads=True,
            atomic_App_hold=False, private_input_absent=True,
            CPU_readback=profile, global_refresh_120=True,
            saved_UI_credential_verified=False, operator_permission=False,
            server_guest_lease=False, source_format_or_motion=False,
            ART_native_events_verified=False)
        return self.last_snapshot

    def absent_after_owned_cleanup(self):
        """No uninstall authority: actual owner must finish its helper first."""
        self.snapshot(helper_installed=False)
        return True

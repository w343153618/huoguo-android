"""Explicit bounded private-test marker I/O, never a credential/file-store reader.

The caller supplies a one-second rooted phone command runner. Publication uses
an owned temporary inode and non-replacing hard link. Cleanup checks the inode;
these shell checks are brackets, not atomic filesystem ownership guarantees.
"""
import re
import shlex

from scripts.probes.source_authenticated_driver import Rejected, names

PRIVATE = '/data/user/0/local.remoteandroid.direct.experiment/files/'
ALLOWED = frozenset(name for phase in (1, 2) for name in names(phase).values())
STAT = re.compile(r'(\d{1,20}):(\d{1,20}):(\d{1,7}):([0-7]{3,4}):(\d{1,10}):([0-9a-fA-F]{1,8})\n')


def metadata(raw, uid):
    match = STAT.fullmatch(raw)
    if match is None:
        raise Rejected('source_input_marker_metadata')
    device, inode, actual_uid, mode, size, bits = match.groups()
    value = tuple(map(int, (device, inode, actual_uid, size)))
    if (not 0 < value[1] < (1 << 64) or value[2] != uid
            or mode != '600' or int(bits, 16) & 0o170000 != 0o100000):
        raise Rejected('source_input_marker_metadata')
    return value


class PhoneMarkers:
    def __init__(self, execute, uid):
        if type(uid) is not int or not 10000 <= uid <= 999999:
            raise Rejected('source_input_marker_metadata')
        self.execute, self.uid, self.owned = execute, uid, {}

    def path(self, name):
        if name not in ALLOWED:
            raise Rejected('source_input_marker_selector')
        return PRIVATE + name

    def _run(self, command, *, missing=False):
        result = self.execute(command)
        if (type(result.returncode) is not int or type(result.stdout) is not str
                or type(result.stderr) is not str or len(result.stdout) > 8192):
            raise Rejected('source_input_marker_command')
        if missing and result.returncode == 44 and not result.stdout and not result.stderr:
            return None
        if result.returncode != 0 or result.stderr:
            raise Rejected('source_input_marker_command')
        return result.stdout

    def _stat(self, path):
        quoted = shlex.quote(path)
        result = self._run('if [ ! -e '+quoted+' ] && [ ! -L '+quoted+' ]; then exit 44; fi; '
            '[ ! -L '+quoted+' ] && stat -c %d:%i:%u:%a:%s:%f '+quoted, missing=True)
        return None if result is None else metadata(result, self.uid)

    def require_absent(self):
        for name in sorted(ALLOWED):
            if self._stat(self.path(name)) is not None:
                raise Rejected('source_input_preexisting_marker')

    def read(self, name, limit):
        if type(limit) is not int or not 1 <= limit <= 2048:
            raise Rejected('source_input_marker_bound')
        path = shlex.quote(self.path(name))
        result = self._run('if [ ! -e '+path+' ] && [ ! -L '+path+' ]; then exit 44; fi; '
            '[ ! -L '+path+' ] && stat -c %d:%i:%u:%a:%s:%f '+path+' || exit 45; '
            'dd if='+path+' bs=1 count='+str(limit+1)+' 2>/dev/null || exit 46; '
            'printf "\\n"; [ ! -L '+path+' ] && stat -c %d:%i:%u:%a:%s:%f '+path+' || exit 47',
            missing=True)
        if result is None:
            return None
        try:
            raw = result.encode('ascii')
            first, rest = raw.split(b'\n', 1)
            before = metadata(first.decode('ascii')+'\n', self.uid)
            size = before[3]
            if not 1 <= size <= limit:
                raise Rejected('source_input_marker_bound')
            body, footer = rest[:size], rest[size:]
            after = metadata(footer[1:].decode('ascii'), self.uid) if footer.startswith(b'\n') else None
            if len(body) != size or before != after:
                raise Rejected('source_input_marker_changed')
            return body
        except (UnicodeError, ValueError):
            raise Rejected('source_input_marker_changed') from None

    def publish(self, name, body):
        destination = self.path(name)
        if not name.endswith(('-command', '-verified')) or type(body) is not bytes or len(body) > 128:
            raise Rejected('source_input_marker_bound')
        if not re.fullmatch(rb'[0-9]+(?: [0-9]+){0,5}\n', body):
            raise Rejected('source_input_marker_bound')
        if destination in self.owned or self._stat(destination) is not None:
            raise Rejected('source_input_preexisting_marker')
        # mktemp returns one unpredictable0600 inode. Track it before writing.
        result = self._run('p=$(mktemp '+shlex.quote(PRIVATE+'udp-ui-source-driver-XXXXXXXXXXXX')
            +') || exit 48; chown '+str(self.uid)+':'+str(self.uid)+' "$p" && chmod 600 "$p" '
            '&& printf "%s\\n" "$p" && stat -c %d:%i:%u:%a:%s:%f "$p" || exit 49')
        rows = result.splitlines(keepends=True)
        if len(rows) != 2 or not re.fullmatch(re.escape(PRIVATE)+r'udp-ui-source-driver-[A-Za-z0-9]{12}\n', rows[0]):
            raise Rejected('source_input_marker_metadata')
        stage, original = rows[0][:-1], metadata(rows[1], self.uid)
        self.owned[stage] = original
        if original[3] != 0 or self._stat(stage) != original:
            raise Rejected('source_input_marker_changed')
        stage_q, dest_q = shlex.quote(stage), shlex.quote(destination)
        # Android restorecon may emit context-loading diagnostics on stderr
        # while returning0. Keep its exit status mandatory, and already own
        # the exact empty inode before attempting the label operation.
        self._run('restorecon '+stage_q+' 2>/dev/null')
        if self._stat(stage) != original:
            raise Rejected('source_input_marker_changed')
        # A failed/ambiguous publication still retains cleanup ownership only
        # for this exact inode. Never remove a replacement owner's marker.
        published = original[:3] + (len(body),)
        self.owned[stage] = published
        self.owned[destination] = published
        self._run('printf %s '+shlex.quote(body.decode('ascii'))+' > '+stage_q+' && '
            '[ ! -e '+dest_q+' ] && [ ! -L '+dest_q+' ] && ln -T '+stage_q+' '+dest_q
            +' && rm '+stage_q)

    def cleanup(self):
        failures = 0
        for path, expected in list(self.owned.items()):
            try:
                actual = self._stat(path)
                if actual is None:
                    del self.owned[path]
                    continue
                # Permit the original empty stage after a write failed before
                # its first byte; its dev/inode/UID still must be owned.
                if actual[:3] != expected[:3] or actual[3] not in (0, expected[3]):
                    raise Rejected('source_input_marker_changed')
                self._run('rm '+shlex.quote(path))
                if self._stat(path) is not None:
                    raise Rejected('source_input_marker_changed')
                del self.owned[path]
            except Exception:
                failures += 1
        return {'owned_marker_cleanup_confirmed': failures == 0 and not self.owned,
                'cleanup_failures': failures, 'filesystem_checks_atomic': False}

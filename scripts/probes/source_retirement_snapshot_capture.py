"""Explicit six-file observation only; no default adapter or permission release.

Normal-return consistency is not actual framework retirement. Failed scope stays
journaled, with no PID borrowing, remote deletion or implicit cleanup fallback.
"""
from scripts.probes import source_owned_snapshot_capture as prior
from scripts.probes import source_snapshot_protocol as protocol
from scripts.probes import source_snapshot_retirement as retirement

META = b'__HG_RETIREMENT_META__\n'
SECTIONS = tuple(('\n__HG_RETIREMENT_%s__\n' % name).encode() for name in
                 ('STARTED', 'WAITED', 'COMPLETED', 'LOG', 'XML', 'RETIRED', 'END'))


def launch_script(nonce, jar, native):
    # Prior closed descriptor/namespace/pin checks; exact fixed operation only.
    script = prior.launch_script(nonce, jar, native)
    old = f' --snapshot {nonce} {jar.nonce}'
    if script.count(old) != 1: raise ValueError('retirement_launch_rejected')
    return script.replace(old, f' --snapshot-retirement {nonce} {jar.nonce}', 1)


def collection_script(nonce, jar, native):
    scope = '/data/local/tmp/' + protocol.namespace(nonce)
    commands = [prior._pins(jar, native), f'd={scope}',
        '[ -d "$d" ] && [ ! -L "$d" ] || exit 86',
        'printf "__HG_RETIREMENT_META__\\n"', 'stat -c \'%u %f %d %i %s %h\' "$d" || exit 87']
    for name, limit in zip(retirement.FILES, (513, 513, 257, 8192, 1048576, 193)):
        commands.extend((f'[ -f "$d/{name}" ] && [ ! -L "$d/{name}" ] || exit 88',
            f'[ "$(stat -c %s "$d/{name}")" -lt {limit} ] || exit 89',
            f'stat -c \'%u %f %d %i %s %h\' "$d/{name}" || exit 90'))
    for name, marker in zip(retirement.FILES, SECTIONS):
        label = marker.decode().replace('\n', '\\n')
        commands.extend((f'printf "{label}"', f'cat "$d/{name}" || exit 91'))
    label = SECTIONS[-1].decode().replace('\n', '\\n')
    commands.extend((f'printf "{label}"', prior._pins(jar, native)))
    return '; '.join(commands)


def observation(raw, *, uid, parent_pid, parent_start_ticks):
    if (type(raw) is not bytes or not 0 < len(raw) < 1048576
            or not raw.startswith(META) or not raw.endswith(SECTIONS[-1])
            or any(raw.count(marker) != 1 for marker in (META, *SECTIONS))):
        raise ValueError('retirement_observation_rejected')
    parts = []; rest = raw[len(META):]
    for marker in SECTIONS:
        part, rest = rest.split(marker, 1); parts.append(part)
    if rest: raise ValueError('retirement_observation_rejected')
    rows = parts[0].splitlines(keepends=True)
    if len(rows) != 7: raise ValueError('retirement_observation_rejected')
    directory = prior.legacy.metadata(rows[0])
    files = dict(zip(retirement.FILES, (prior.legacy.metadata(row) for row in rows[1:])))
    result = retirement.bind(started_raw=parts[1], waited_raw=parts[2],
        completed_raw=parts[3], log=parts[4], xml=parts[5], retired_raw=parts[6],
        directory=directory, files=files, expected_uid=uid,
        expected_parent_pid=parent_pid, expected_parent_start_ticks=parent_start_ticks)
    return result, bytearray(parts[5])


class RetirementSnapshotCapture(prior.OwnedSnapshotCapture):
    """Must be explicitly constructed; old five-file factory stays unchanged."""
    def _launch(self, nonce):
        return launch_script(nonce, self.jar, self.native)

    def _collect(self, nonce):
        return collection_script(nonce, self.jar, self.native)

    def _observe(self, raw, pid, start):
        return observation(raw, uid=self.jar.uid, parent_pid=pid, parent_start_ticks=start)

    def _record(self, ticket, result):
        self.journal.record_retirement_consistency(ticket, result)

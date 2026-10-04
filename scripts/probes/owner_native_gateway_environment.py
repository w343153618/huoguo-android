"""Pure environment selection for an explicitly bound private M1 coordinator.

No reads, process, credential value or permission token. The caller must verify
the referenced original auth/certificate/key paths and actual dependency bytes.
An inherited PATH, Python module override, loader injection or DIRECT setting
cannot silently alter the fixed children. Existing production env is untouched.
"""
from pathlib import Path


def select(home, adb, state, evidence, original):
    if (any(not isinstance(p, Path) or not p.is_absolute() or '\0' in str(p)
            for p in (home, adb, state, evidence))
            or adb != home / 'Library/Android/sdk/platform-tools/adb'
            or type(original) is not dict
            or set(original) != {'DIRECT_AUTH_FILE', 'DIRECT_CERT', 'DIRECT_KEY', 'DIRECT_VIDEO_BACKEND'}
            or original['DIRECT_VIDEO_BACKEND'] != 'videotoolbox'
            or any(type(original[k]) is not str or not Path(original[k]).is_absolute()
                   or '\0' in original[k] for k in ('DIRECT_AUTH_FILE', 'DIRECT_CERT', 'DIRECT_KEY'))):
        raise ValueError('native_environment_reviewed_references_required')
    env = dict(original)
    env.update(HOME=str(home), PATH=str(adb.parent)+':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        LANG='en_US.UTF-8', DIRECT_SERIAL='emulator-5556', DIRECT_AVD='RemoteAndroid17Compare',
        DIRECT_EXTERNAL_VM='1', DIRECT_STATE_DIR=str(state),
        DIRECT_DIAGNOSTICS_DIR=str(evidence / 'gateway/reports'))
    return env

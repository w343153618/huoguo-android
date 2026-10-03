#!/usr/bin/env python3
"""Build the self-targeting headless lifecycle fixture; never operate a device."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'experiments/moonlight-v2/instrumentation-lifecycle'
PACKAGE = 'local.huoguo.instrumentationlifecyclefixture'
SIGNER = '0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da'
ANDROID = '{http://schemas.android.com/apk/res/android}'


def validate_manifest(path):
    manifest = ET.parse(path).getroot()
    if manifest.attrib.get('package') != PACKAGE:
        raise ValueError('fixture_package_required')
    if sorted(child.tag for child in manifest) != ['application', 'instrumentation', 'uses-sdk']:
        raise ValueError('fixture_closed_manifest_required')
    if len(manifest.findall('uses-permission')) or len(manifest.findall('permission')):
        raise ValueError('fixture_no_permissions')
    applications = manifest.findall('application')
    if len(applications) != 1:
        raise ValueError('fixture_application_required')
    application = applications[0]
    if application.attrib != {ANDROID+'debuggable': 'true', ANDROID+'allowBackup': 'false',
                             ANDROID+'hasCode': 'true', ANDROID+'usesCleartextTraffic': 'false'}:
        raise ValueError('fixture_application_attributes_required')
    if set(child.tag for child in application) != {'receiver'} or len(application) != 1:
        raise ValueError('fixture_only_receiver')
    receiver = application[0]
    if receiver.attrib.get(ANDROID+'name') != '.SnapshotReceiver' or receiver.attrib.get(ANDROID+'exported') != 'true' or len(receiver):
        raise ValueError('fixture_explicit_receiver_required')
    instruments = manifest.findall('instrumentation')
    if len(instruments) != 1 or instruments[0].attrib.get(ANDROID+'name') != '.LifecycleInstrumentation' or instruments[0].attrib.get(ANDROID+'targetPackage') != PACKAGE:
        raise ValueError('fixture_self_target_required')
    if any(ANDROID+'process' in element.attrib for element in manifest.iter()):
        raise ValueError('fixture_single_process_required')


def validate_output(output):
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError('fixture_apk_outside_source_required')
    if output.exists():
        raise ValueError('fixture_output_must_be_new')
    return output


def normalized_zip(path, dex):
    """Remove build-time ZIP timestamps; pinned tools/sources reproduce APK bytes."""
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if 'classes.dex' in files:
        raise ValueError('fixture_unexpected_dex')
    files['classes.dex'] = dex.read_bytes()
    with zipfile.ZipFile(path, 'w') as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, files[name])


def build(output):
    validate_manifest(SOURCE/'AndroidManifest.xml')
    output = validate_output(output)
    sdk = Path(os.environ.get('ANDROID_HOME', Path.home()/'Library/Android/sdk'))
    android = sdk/'platforms/android-37.0/android.jar'
    tools = sdk/'build-tools/36.0.0'
    java = Path(os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'))
    signer = Path.home()/'.android/debug.keystore'
    required = [android, java/'bin/javac', tools/'d8', tools/'aapt2', tools/'zipalign', tools/'apksigner', signer]
    if not all(path.is_file() for path in required):
        raise ValueError('existing_sdk_jdk_debug_signer_required_no_download')
    output.mkdir(mode=0o700)
    for directory in ('classes', 'dex'):
        (output/directory).mkdir(mode=0o700)
    sources = sorted(SOURCE.glob('*.java'))
    if len(sources) != 3:
        raise ValueError('fixture_exact_three_sources_required')

    def run(command):
        result = subprocess.run(list(map(str, command)), env=dict(os.environ, JAVA_HOME=str(java)),
            capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(Path(command[0]).name+': '+result.stderr[:4096])
        if len(result.stdout.encode()) > 65536 or len(result.stderr.encode()) > 65536:
            raise RuntimeError('fixture_tool_output_bound')
        return result.stdout

    run([java/'bin/javac', '-source', '8', '-target', '8', '-cp', android, '-d', output/'classes', *sources])
    run([tools/'d8', '--min-api', '30', '--lib', android, '--output', output/'dex', *sorted((output/'classes').rglob('*.class'))])
    run([tools/'aapt2', 'link', '-I', android, '--manifest', SOURCE/'AndroidManifest.xml', '-o', output/'unsigned.apk'])
    normalized_zip(output/'unsigned.apk', output/'dex/classes.dex')
    run([tools/'zipalign', '-f', '4', output/'unsigned.apk', output/'aligned.apk'])
    run([tools/'apksigner', 'sign', '--ks', signer, '--ks-pass', 'pass:android', '--v1-signing-enabled', 'false',
        '--out', output/'fixture.apk', output/'aligned.apk'])
    certs = run([tools/'apksigner', 'verify', '--verbose', '--print-certs', output/'fixture.apk'])
    fingerprints = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})', certs)
    if fingerprints != [SIGNER]:
        raise ValueError('fixture_original_debug_signer_required')
    badging = run([tools/'aapt2', 'dump', 'badging', output/'fixture.apk'])
    if "package: name='"+PACKAGE+"'" not in badging or 'uses-permission:' in badging or 'launchable-activity:' in badging:
        raise ValueError('fixture_packaged_headless_boundary_invalid')
    os.chmod(output/'fixture.apk', 0o600)
    receipt = {'schema_version': 1, 'scope': 'offline_headless_self_instrumentation_fixture_not_device_acceptance',
        'package': PACKAGE, 'component': PACKAGE+'/.LifecycleInstrumentation', 'receiver': PACKAGE+'/.SnapshotReceiver',
        'broadcast_action': PACKAGE+'.SNAPSHOT', 'apk': str(output/'fixture.apk'),
        'apk_sha256': hashlib.sha256((output/'fixture.apk').read_bytes()).hexdigest(),
        'signer_sha256': SIGNER, 'no_permissions': True, 'no_activity': True,
        'source_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [SOURCE/'AndroidManifest.xml', *sources]}}
    (output/'build-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    os.chmod(output/'build-receipt.json', 0o600)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    try:
        receipt = build(arguments.output)
    except (ValueError, RuntimeError) as failure:
        parser.exit(1, str(failure)+'\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()

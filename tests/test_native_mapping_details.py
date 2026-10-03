"""Actual Java schema and missing-symbol boundary, no phone or native .so load."""
from pathlib import Path
import importlib.util
import os
import platform
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
NATIVE = ROOT/'experiments/moonlight-v2/transport/android-udp'


class NativeMappingDetailsCheck(unittest.TestCase):
    def test_actual_java_fixed_schema_rejection_and_missing_jni(self):
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        with tempfile.TemporaryDirectory(prefix='huoguo-mapping-contract-') as folder:
            subprocess.run([javac, '-source', '8', '-target', '8', '-d', folder,
                str(ROOT/'experiments/nps-transport/phone/NativeUdpFec.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/NativeMappingDetailsProbe.java')],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder, 'local.remoteandroid.direct.NativeMappingDetailsProbe'],
                check=True, capture_output=True, text=True, timeout=10)
            self.assertIn('PASS', result.stdout)
            self.assertIn('missing JNI explicit', result.stdout)
            print(result.stdout.strip())

    def test_actual_host_jni_methods_and_owned_handle_lifecycle(self):
        # Only the existing pinned local dependency is read. This host build is
        # independent of Android packaging, signing, codecs and any live service.
        system = platform.system()
        if system not in ('Darwin', 'Linux'):
            self.skipTest('Owned host JNI fixture supports Darwin/Linux only')
        javac = str(JDK/'javac') if (JDK/'javac').exists() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').exists() else shutil.which('java')
        java_home = Path(os.environ.get('JAVA_HOME', str(JDK.parent)))
        if not (java_home/'include/jni.h').exists() or not shutil.which('cc') or not shutil.which('c++'):
            self.skipTest('Existing JDK JNI headers and C/C++ compilers required; nothing installed')
        spec = importlib.util.spec_from_file_location('mapping_jni_pinned_build', NATIVE/'build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        source = builder.default_source(os.environ)
        if not source.is_dir():
            self.skipTest('Existing pinned local nanors required; nothing downloaded')
        builder.verify_dependencies(source, builder.load_lock())
        with tempfile.TemporaryDirectory(prefix='huoguo-mapping-host-jni-') as folder:
            build = Path(folder)
            includes = ['-isystem', str(source/'nanors'), '-isystem', str(source/'nanors/deps/obl')]
            objects = []
            for index, relative in enumerate(('rs.c', 'deps/obl/oblas_common.c', 'deps/obl/oblas_lite.c')):
                target = build/('fec-'+str(index)+'.o')
                subprocess.run(['cc', '-O2', '-fPIC', *includes, '-c', str(source/'nanors'/relative), '-o', str(target)],
                    check=True, capture_output=True, timeout=30)
                objects.append(str(target))
            subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror', '-O2', '-fPIC',
                '-dynamiclib' if system == 'Darwin' else '-shared', '-pthread', '-I', str(NATIVE),
                '-I', str(java_home/'include'), '-I', str(java_home/'include'/system.lower()),
                *includes, str(NATIVE/'native_udp_fec.cpp'), *objects, '-o', str(build/'libhuoguo_udp_fec.so')],
                check=True, capture_output=True, timeout=30)
            subprocess.run([javac, '-source', '8', '-target', '8', '-d', folder,
                str(ROOT/'experiments/nps-transport/phone/NativeUdpFec.java'),
                str(ROOT/'tests/java/local/remoteandroid/direct/NativeMappingJniProbe.java')],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder, 'local.remoteandroid.direct.NativeMappingJniProbe', folder],
                check=True, capture_output=True, text=True, timeout=10)
            self.assertIn('actual host JNI mapping bridge', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()

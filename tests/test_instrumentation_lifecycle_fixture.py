"""Offline headless fixture contracts; no ADB, device, network or real App."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'experiments/moonlight-v2/instrumentation-lifecycle'
SPEC = importlib.util.spec_from_file_location('lifecycle_fixture_builder', ROOT/'scripts/probes/build_instrumentation_lifecycle_fixture.py')
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

STUBS = {
    'android/os/Process.java': 'package android.os;public final class Process {public static int myPid(){return 4242;}}',
    'android/os/SystemClock.java': 'package android.os;public final class SystemClock {public static long elapsedRealtimeNanos(){return System.nanoTime();}}',
    'android/os/Bundle.java': '''package android.os;public final class Bundle {
 public final java.util.Map<String,Object> values=new java.util.HashMap<>();
 public void putString(String k,String v){values.put(k,v);}public void putInt(String k,int v){values.put(k,v);}}''',
    'android/content/Context.java': '''package android.content;public final class Context {
 private final String name;private final ClassLoader loader;
 public Context(String n,ClassLoader l){name=n;loader=l;}public String getPackageName(){return name;}
 public ClassLoader getClassLoader(){return loader;}}''',
    'android/content/Intent.java': '''package android.content;public final class Intent {
 private final String action;public Intent(String a){action=a;}public String getAction(){return action;}}''',
    'android/content/BroadcastReceiver.java': '''package android.content;public abstract class BroadcastReceiver {
 public int resultCode;public String resultData;public void setResultCode(int c){resultCode=c;}
 public void setResultData(String d){resultData=d;}public abstract void onReceive(Context c,Intent i);}''',
    'android/app/Instrumentation.java': '''package android.app;import android.os.Bundle;import android.content.Context;
 public class Instrumentation {public Context target;public int code;public Bundle result;public boolean started;
 public void onCreate(Bundle a){}public void onStart(){}public void start(){started=true;}
 public Context getTargetContext(){return target;}public void finish(int c,Bundle r){code=c;result=r;}}''',
}
HARNESS = r'''
package local.huoguo.instrumentationlifecyclefixture;
import android.content.Context;import android.content.Intent;import android.os.Bundle;
public final class LifecycleFixtureCheck {
 static String packageName="local.huoguo.instrumentationlifecyclefixture";
 public static void main(String[] args)throws Exception {
  Context context=new Context(packageName,LifecycleFixtureCheck.class.getClassLoader());
  if(args[0].equals("receiver")){
   SnapshotReceiver receiver=new SnapshotReceiver();receiver.onReceive(context,new Intent(SnapshotReceiver.ACTION));
   if(receiver.resultCode!=-1)throw new AssertionError("receiver snapshot rejected");System.out.println(receiver.resultData);
   receiver.onReceive(context,new Intent(SnapshotReceiver.ACTION));System.out.println(receiver.resultData);
  }else if(args[0].equals("invalid")){
   for(Intent intent:new Intent[]{null,new Intent("other.action")}){
    SnapshotReceiver receiver=new SnapshotReceiver();receiver.onReceive(context,intent);
    if(receiver.resultCode!=0)throw new AssertionError("foreign action accepted");System.out.println(receiver.resultData);}
  }else if(args[0].equals("instrument")){
   System.out.println(ProcessSnapshot.snapshot());LifecycleInstrumentation instrument=new LifecycleInstrumentation();instrument.target=context;
   instrument.onCreate(new Bundle());if(!instrument.started)throw new AssertionError("start missing");instrument.onStart();
   if(instrument.code!=-1)throw new AssertionError("instrument rejected self");System.out.println(instrument.result.values.get("fixture_snapshot"));
  }else if(args[0].equals("loader")){
   java.net.URL url=new java.io.File(System.getProperty("java.class.path")).toURI().toURL();
   try(java.net.URLClassLoader targetLoader=new java.net.URLClassLoader(new java.net.URL[]{url},null)){
    Class<?> state=targetLoader.loadClass(packageName+".ProcessSnapshot");
    System.out.println((String)state.getMethod("snapshot").invoke(null));
    // Intentionally initialize a different static in the instrument's loader.
    System.out.println(ProcessSnapshot.snapshot());
    LifecycleInstrumentation instrument=new LifecycleInstrumentation();instrument.target=new Context(packageName,targetLoader);instrument.onStart();
    if(instrument.code!=-1)throw new AssertionError("target loader snapshot rejected");System.out.println(instrument.result.values.get("fixture_snapshot"));}
  }else if(args[0].equals("wrong_target")){
   LifecycleInstrumentation instrument=new LifecycleInstrumentation();instrument.target=new Context("different.package",context.getClassLoader());instrument.onStart();
   if(instrument.code!=0||!Integer.valueOf(1).equals(instrument.result.values.get("fixture_error_code"))
      ||instrument.result.values.containsKey("fixture_snapshot"))throw new AssertionError("foreign target accepted");System.out.println("PASS rejected");
  }else throw new AssertionError("unknown case");
 }
}
'''


class LifecycleFixtureChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        if not cls.java or not javac:
            raise RuntimeError('Existing JDK required for real lifecycle fixture sources')
        cls.directory = tempfile.TemporaryDirectory(prefix='huoguo-lifecycle-jvm-')
        cls.addClassCleanup(cls.directory.cleanup)
        folder = Path(cls.directory.name)
        paths = []
        for name, code in STUBS.items():
            path = folder/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code)
            paths.append(path)
        harness = folder/'LifecycleFixtureCheck.java'
        harness.write_text(HARNESS)
        result = subprocess.run([javac, '-d', str(folder), *map(str, paths), *map(str, sorted(SOURCE.glob('*.java'))), str(harness)],
            capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Actual fixture source compile failed:\n'+result.stderr)

    def run_case(self, name):
        result = subprocess.run([self.java, '-cp', self.directory.name,
            'local.huoguo.instrumentationlifecyclefixture.LifecycleFixtureCheck', name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result.stdout.splitlines()

    def parse_snapshot(self, line):
        self.assertLessEqual(len(line), 256)
        value = json.loads(line)
        self.assertEqual(set(value), {'schema_version', 'pid', 'nonce_ns', 'snapshot_ns'})
        self.assertTrue(all(type(item) is int for item in value.values()))
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['pid'], 4242)
        self.assertGreaterEqual(value['snapshot_ns'], value['nonce_ns'])
        return value

    def test_actual_receiver_returns_same_static_nonce_across_snapshots(self):
        first, second = map(self.parse_snapshot, self.run_case('receiver'))
        self.assertEqual(first['nonce_ns'], second['nonce_ns'])
        self.assertGreaterEqual(second['snapshot_ns'], first['snapshot_ns'])

    def test_receiver_rejects_missing_or_foreign_action_without_pid_nonce(self):
        for line in self.run_case('invalid'):
            self.assertEqual(json.loads(line), {'schema_version': 1, 'error_code': 1})

    def test_actual_self_instrumentation_reports_same_target_state(self):
        first, second = map(self.parse_snapshot, self.run_case('instrument'))
        self.assertEqual(first['nonce_ns'], second['nonce_ns'])
        self.assertGreaterEqual(second['snapshot_ns'], first['snapshot_ns'])

    def test_instrumentation_reads_target_loader_not_its_own_static_copy(self):
        target, other, observed = map(self.parse_snapshot, self.run_case('loader'))
        self.assertNotEqual(target['nonce_ns'], other['nonce_ns'])
        self.assertEqual(observed['nonce_ns'], target['nonce_ns'])

    def test_instrumentation_cannot_query_a_different_app_package(self):
        self.assertEqual(self.run_case('wrong_target'), ['PASS rejected'])

    def test_actual_manifest_is_headless_permissionless_and_single_process(self):
        BUILDER.validate_manifest(SOURCE/'AndroidManifest.xml')
        text = '\n'.join(path.read_text() for path in SOURCE.glob('*.java'))
        for forbidden in ('startActivity', 'getUiAutomation', 'Runtime.getRuntime', 'java.io.', 'java.net.', 'SharedPreferences', 'getFilesDir'):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('local.remoteandroid.direct', text)

    def test_builder_rejects_permissions_ui_foreign_target_process_or_filter(self):
        mutations = [
            lambda m: ET.SubElement(m, 'uses-permission', {BUILDER.ANDROID+'name': 'android.permission.INTERNET'}),
            lambda m: ET.SubElement(m.find('application'), 'activity', {BUILDER.ANDROID+'name': '.Bad'}),
            lambda m: m.find('instrumentation').set(BUILDER.ANDROID+'targetPackage', 'another.app'),
            lambda m: m.find('application/receiver').set(BUILDER.ANDROID+'process', ':extra'),
            lambda m: ET.SubElement(m.find('application/receiver'), 'intent-filter'),
        ]
        with tempfile.TemporaryDirectory(prefix='huoguo-lifecycle-manifest-') as directory:
            path = Path(directory)/'manifest.xml'
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    manifest = ET.parse(SOURCE/'AndroidManifest.xml').getroot()
                    mutation(manifest)
                    ET.ElementTree(manifest).write(path)
                    with self.assertRaises(ValueError):
                        BUILDER.validate_manifest(path)

    def test_builder_refuses_source_tree_or_existing_output(self):
        with self.assertRaisesRegex(ValueError, 'outside_source'):
            BUILDER.validate_output(ROOT/'docs/fixture-output')
        with tempfile.TemporaryDirectory(prefix='huoguo-lifecycle-existing-') as directory:
            with self.assertRaisesRegex(ValueError, 'must_be_new'):
                BUILDER.validate_output(Path(directory))

    def test_zip_normalization_does_not_change_payload_or_capture_time(self):
        import zipfile
        with tempfile.TemporaryDirectory(prefix='huoguo-lifecycle-zip-') as directory:
            path, dex = Path(directory)/'unsigned.apk', Path(directory)/'classes.dex'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('AndroidManifest.xml', b'owned fake manifest bytes')
            dex.write_bytes(b'owned fake DEX bytes')
            BUILDER.normalized_zip(path, dex)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.read('classes.dex'), dex.read_bytes())
                self.assertEqual(archive.read('AndroidManifest.xml'), b'owned fake manifest bytes')
                self.assertEqual([item.date_time for item in archive.infolist()], [(1980, 1, 1, 0, 0, 0)]*2)
            with self.assertRaisesRegex(ValueError, 'unexpected_dex'):
                BUILDER.normalized_zip(path, dex)


if __name__ == '__main__':
    unittest.main()

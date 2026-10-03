"""Actual Java source/policy checks; no APK build, Android device or HTTP traffic."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'app/src/main/java/local/remoteandroid/direct'
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
ANDROID=Path('/Users/wyw/Library/Android/sdk/platforms/android-37.0/android.jar')


class UpdateChannelPolicyChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        if not cls.javac or not cls.java:raise RuntimeError('Existing JDK required')
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-update-policy-')
        built=subprocess.run([cls.javac,'-d',cls.folder.name,str(PACKAGE/'UpdateChannelPolicy.java'),
            str(ROOT/'tests/java/local/remoteandroid/direct/UpdateChannelPolicyCheck.java')],capture_output=True,text=True,timeout=30)
        if built.returncode:raise AssertionError(built.stdout+built.stderr)

    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()

    def policy(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.UpdateChannelPolicyCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('actual production policy checks passed',result.stdout)

    def test_selected_installed_package_controls_update_and_cross_package_open(self):self.policy('actions')
    def test_channel_identity_and_fixed_manifest_urls(self):self.policy('channels')
    def test_manifest_versions_packages_hashes_sizes_and_legacy_stable(self):self.policy('manifest')
    def test_https_rejects_credentials_fragments_and_insecure_protocols(self):self.policy('https')
    def test_download_and_resume_archives_have_exact_original_single_signer(self):self.policy('archives')

    def test_actual_updater_source_compiles_against_existing_android_sdk(self):
        if not ANDROID.is_file():raise RuntimeError('Existing Android SDK API37 required for updater source compile')
        stubs={
            'MainActivity.java':'package local.remoteandroid.direct; final class MainActivity {static String message(Throwable t){return "error";}}',
            'BuildConfig.java':'package local.remoteandroid.direct; final class BuildConfig {static final String VERSION_NAME="1.31-alpha.6",UPDATE_MANIFEST_URL="https://146.56.249.175:15556/experimental/experiment.json";static final int VERSION_CODE=37;}',
            'R.java':'package local.remoteandroid.direct; final class R {static final class raw {static final int server_cert=1,server_cert_m5=2;}}',
        }
        with tempfile.TemporaryDirectory(prefix='huoguo-updater-source-') as folder:
            paths=[]
            for name,body in stubs.items():
                path=Path(folder)/name;path.write_text(body);paths.append(str(path))
            result=subprocess.run([self.javac,'-cp',str(ANDROID),'-d',folder,*paths,str(PACKAGE/'UpdateChannelPolicy.java'),str(PACKAGE/'UpdateOperationGate.java'),str(PACKAGE/'AppUpdater.java')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_package_visibility_has_only_explicit_stable_and_test_queries(self):
        manifest=ET.parse(ROOT/'app/src/main/AndroidManifest.xml').getroot()
        ns='{http://schemas.android.com/apk/res/android}'
        queries=manifest.findall('queries');self.assertEqual(len(queries),1)
        self.assertEqual({x.attrib[ns+'name'] for x in queries[0].findall('package')},{'local.remoteandroid.direct','local.remoteandroid.direct.experiment'})
        self.assertEqual(len(list(queries[0])),2)
        self.assertNotIn('android.permission.QUERY_ALL_PACKAGES',{x.attrib.get(ns+'name') for x in manifest.findall('uses-permission')})

    def test_updater_wiring_preserves_confirmation_and_pending_verification(self):
        source=(PACKAGE/'AppUpdater.java').read_text()
        self.assertIn('activity.runOnUiThread(()->chooseChannel(token))',source)
        self.assertIn('checkChannel(UpdateChannelPolicy.channelForPackage(activity.getPackageName()),false,0)',source)
        self.assertIn('url=manual?UpdateChannelPolicy.manifestUrl(channel):getUpdateUrl()',source)
        self.assertIn('所选通道：',source);self.assertIn('更新内容',source);self.assertIn('确认更新',source)
        self.assertIn('getLaunchIntentForPackage(target)',source)
        self.assertIn('validate(part, version,target)',source);self.assertIn('validate(file,expected,target)',source)
        self.assertIn('UpdateChannelPolicy.https(prefs.getString("pending_apk_url",""))',source)
        for key in ('pending_version','pending_hash','pending_size','pending_package','pending_channel','pending_apk_url'):
            self.assertGreaterEqual(source.count('"'+key+'"'),2)
        self.assertIn('MessageDigest.getInstance("SHA-256")',source)
        self.assertIn('fixedPrivateHost||configuredPrivateHost',source)
        self.assertIn('connection.setInstanceFollowRedirects(false)',source)
        self.assertIn('target = https(new URL(target, location).toString())',source)
        self.assertIn('AlertDialog presentUpdate(String name,String notes,boolean newer,Runnable confirmed)',source)
        self.assertNotIn('raw.githubusercontent.com',source)


if __name__=='__main__':unittest.main()

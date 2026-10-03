"""Actual pure Java public-profile/prefs checks, not phone/network acceptance."""
import hashlib
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
CONTRACT = ROOT / 'app/src/udp/java/local/remoteandroid/direct/LanUdpContract.java'
UI = ROOT / 'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'


def run_java(name, files):
    javac = str(JDK / 'javac') if (JDK / 'javac').is_file() else shutil.which('javac')
    java = str(JDK / 'java') if (JDK / 'java').is_file() else shutil.which('java')
    if not javac or not java:
        raise RuntimeError('Existing JDK required for owned actual Java fixtures')
    with tempfile.TemporaryDirectory(prefix='huoguo-public-profile-contract-') as folder:
        paths = []
        for path, contents in files:
            if contents is None:
                paths.append(str(path))
            else:
                target = Path(folder) / path
                target.write_text(contents)
                paths.append(str(target))
        compiled = subprocess.run([javac, '-d', folder, *paths], capture_output=True, text=True, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        return subprocess.run([java, '-cp', folder, name], capture_output=True, text=True, timeout=5)


class NpsUdpAppContractChecks(unittest.TestCase):
    def test_real_contract_enforces_fixed_m1_m5_nodes_and_control_media_roles(self):
        result = run_java('local.remoteandroid.direct.NpsUdpContractCheck', [
            (CONTRACT, None), (ROOT / 'tests/java/local/remoteandroid/direct/NpsUdpContractCheck.java', None)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'NPS owner UDP profile contract: [1-9][0-9]* passed')

    def test_selected_node_certificate_pins_match_existing_public_cert_resources(self):
        source = CONTRACT.read_text()
        pins = {}
        for node, filename in (('M1', 'server_cert.pem'), ('M5', 'server_cert_m5.pem')):
            expected = re.search(node + r'_CERT_SHA256="([0-9a-f]{64})"', source).group(1)
            pem = (ROOT / 'app/src/main/res/raw' / filename).read_text()
            self.assertNotIn('PRIVATE KEY', pem)
            pins[node] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            self.assertEqual(pins[node], expected)
        self.assertNotEqual(pins['M1'], pins['M5'])

    def test_actual_selection_and_typed_saved_preferences_preserve_legacy_indices(self):
        ui = UI.read_text()
        saved = '    private static int savedSelection' + ui.split('    private static int savedSelection', 1)[1].split('    private static boolean savedSound', 1)[0]
        selections = '    private static String selectedScope' + ui.split('    private static String selectedScope', 1)[1].split('    private static String savedAddress', 1)[0]
        source = r'''
package local.remoteandroid.direct;
import java.util.HashMap;
public final class UiSelectionsCheck {
    private static final class SharedPreferences {
        final HashMap<String,Object> values=new HashMap<>();
        int getInt(String key,int fallback){Object value=values.get(key);return value==null?fallback:(Integer)value;}
        String getString(String key,String fallback){Object value=values.get(key);return value==null?fallback:(String)value;}
    }
''' + saved + selections + r'''
    public static void main(String[] args)throws Exception{
        SharedPreferences saved=new SharedPreferences();
        if(savedSelection(saved,"scope",3,3)!=3)throw new AssertionError("fresh alpha6 not public M5");
        for(int i=0;i<4;i++){saved.values.put("scope",i);if(savedSelection(saved,"scope",3,3)!=i)throw new AssertionError("legacy valid preference reset");}
        for(Object bad:new Object[]{-1,4,true,"3",3.0}){saved.values.put("scope",bad);if(savedSelection(saved,"scope",3,3)!=3)throw new AssertionError("invalid selection accepted");}
        if(!selectedScope(0).equals("lan")||!selectedScope(1).equals("tailnet")||!selectedScope(2).equals("nps_owner")||!selectedScope(3).equals("nps_owner"))throw new AssertionError("scope positions drift");
        if(!selectedNode(0).isEmpty()||!selectedNode(1).isEmpty()||!selectedNode(2).equals("m1")||!selectedNode(3).equals("m5"))throw new AssertionError("node positions drift");
        if(!publicAddress(2).equals("146.56.249.175:49556")||!publicAddress(3).equals("146.56.249.175:49558"))throw new AssertionError("public control tuple drift");
        for(int i:new int[]{-1,0,1,4})try{publicAddress(i);throw new AssertionError("public profile guessed");}catch(IllegalArgumentException expected){}
        saved.values.put("username","huoguo");
        if(!savedText(saved,usernamePreference(3),defaultUsername(3),128).equals("huoguo"))throw new AssertionError("fresh public default changed");
        saved.values.put("nps_username","owner-explicit");
        if(!savedText(saved,usernamePreference(2),defaultUsername(2),128).equals("owner-explicit"))throw new AssertionError("explicit owner username lost");
        if(!savedText(saved,usernamePreference(0),defaultUsername(0),128).equals("huoguo"))throw new AssertionError("old LAN username lost");
        saved.values.put("nps_username",true);
        if(!savedText(saved,usernamePreference(3),defaultUsername(3),128).equals("huoguo"))throw new AssertionError("bad saved username accepted");
        System.out.println("PASS actual scope and saved preference methods");
    }
}
'''
        result = run_java('local.remoteandroid.direct.UiSelectionsCheck', [(CONTRACT, None), ('UiSelectionsCheck.java', source)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS actual scope and saved preference', result.stdout)

    def test_ui_separate_owner_credentials_and_attempt_pins_without_tcp_media_fallback(self):
        source = UI.read_text()
        self.assertIn('restoringFields=true;', source)
        self.assertIn('finally{restoringFields=false;}', source)
        self.assertIn('if(restoringFields)return;', source)
        self.assertIn('request.put("node",node)', source)
        self.assertIn('LanUdpContract.NPS_SCOPE.equals(attempt.networkScope)', source)
        self.assertIn('LanUdpContract.npsCertificateSha256(attempt.node)', source)
        self.assertIn('pinned=MessageDigest.isEqual(nodePin,leaf)', source)
        self.assertIn('socket.startHandshake()', source)
        self.assertIn('attempt.networkScope,attempt.node,attempt.surfaceSubmitLeadMs', source)
        self.assertIn('address.setEnabled(position<2)', source)
        self.assertIn('给火锅的安卓 · 测试版', source)
        self.assertIn('update.setText("检查更新")', source)
        self.assertNotIn('activity.session(', source)
        self.assertNotIn('activity.connect(', source)
        self.assertNotIn('.putString("password"', source)

    def test_alpha6_defaults_only_apply_to_isolated_udp_variant(self):
        source = (ROOT / 'app/build.gradle').read_text()
        self.assertIn('if (authenticatedLanUdp && experimentalVersionName == null && experimentalVersionCode == null)', source)
        self.assertIn("experimentalVersionName = '1.31-alpha.7'", source)
        self.assertIn("experimentalVersionCode = '38'", source)
        self.assertIn("versionCode 32", source)
        self.assertIn("versionName '1.31'", source)
        self.assertIn('authenticatedLanUdp requires isolated probeApplicationId', source)


if __name__ == '__main__':
    unittest.main()

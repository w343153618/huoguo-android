"""Execute the real Probe.parseSession against the App's endpoint contract.

Only the Android-independent parsing methods/nested Session are extracted.
No account, credential, phone, socket or running service is used by these tests.
"""
from pathlib import Path
import inspect
import shutil
import subprocess
import tempfile
import unittest

from udp_lan_sessions import UdpLanSessions

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

JSON_STUB = r'''
package org.json;
/** Typed fixture values only; production parseSession implementation is real. */
public final class JSONObject {
    private final java.util.Map<String,Object> values=new java.util.HashMap<>();
    public JSONObject put(String key,Object value){values.put(key,value);return this;}
    public Object get(String key){if(!values.containsKey(key))throw new IllegalArgumentException("missing");return values.get(key);}
    public String getString(String key){return (String)get(key);}
    public int getInt(String key){return ((Number)get(key)).intValue();}
    public double getDouble(String key){return ((Number)get(key)).doubleValue();}
    public boolean has(String key){return values.containsKey(key);}
    public String optString(String key,String fallback){return has(key)?getString(key):fallback;}
    public boolean optBoolean(String key,boolean fallback){return has(key)?(Boolean)get(key):fallback;}
}
'''

HARNESS = r'''
    private static JSONObject descriptor(int peerPort,int bindPort){
        return new JSONObject().put("key_b64",Base64.getEncoder().encodeToString(new byte[32]))
            .put("session_tag_hex","0000000000000001").put("peer_host","100.65.0.2")
            .put("network_scope","tailnet")
            .put("peer_port",peerPort).put("bind_port",bindPort).put("seconds",30)
            .put("fps",60).put("buffer_ms",80).put("video_release","scheduled")
            .put("async_video",true).put("display_hz",120).put("surface_submit_lead_ms",0);
    }
    private static void reject(int peerPort,int bindPort,boolean appMode)throws Exception{
        try{parseSession(descriptor(peerPort,bindPort),appMode);throw new AssertionError("mismatched endpoint accepted");}
        catch(IOException expected){if(!expected.getMessage().equals("session_options_invalid"))throw expected;}
    }
    public static void main(String[] args)throws Exception{
        if(LanUdpContract.UDP_PORT!=45963)throw new AssertionError("App high port changed; update all actual endpoints together");
        Session app=parseSession(descriptor(LanUdpContract.UDP_PORT,0),true);
        if(app.peerPort!=LanUdpContract.UDP_PORT||app.bindPort!=0)throw new AssertionError("App contract/parser drift");
        reject(15963,0,true);reject(15961,0,true);reject(45963,15960,true);
        Session legacy=parseSession(descriptor(15961,15960),false);
        if(legacy.peerPort!=15961||legacy.bindPort!=15960)throw new AssertionError("legacy probe changed");
        reject(45963,15960,false);reject(15961,0,false);
        for(String node:new String[]{"m1","m5"}){
            JSONObject publicDescriptor=descriptor(LanUdpContract.npsUdpPort(node),0)
                .put("peer_host",LanUdpContract.NPS_HOST).put("network_scope",LanUdpContract.NPS_SCOPE).put("node",node);
            Session publicSession=parseSession(publicDescriptor,true);
            if(publicSession.peerPort!=LanUdpContract.npsUdpPort(node))throw new AssertionError("public UDP parser mismatch");
            publicDescriptor.put("peer_port",node.equals("m1")?15558:15556);
            try{parseSession(publicDescriptor,true);throw new AssertionError("cross-node UDP accepted");}catch(IOException expected){}
            publicDescriptor.put("peer_port",LanUdpContract.npsUdpPort(node)).put("peer_host","127.0.0.1");
            try{parseSession(publicDescriptor,true);throw new AssertionError("internal loopback disclosed as media peer");}catch(IOException expected){}
        }
        for(String scope:new String[]{"lan","tailnet","nps_owner","public","owner_nps"}){
            JSONObject publicWithoutNode=descriptor(15558,0).put("peer_host",LanUdpContract.NPS_HOST).put("network_scope",scope);
            try{parseSession(publicWithoutNode,true);throw new AssertionError("implicit public scope accepted");}catch(IOException expected){}
        }
        System.out.println("PASS actual parseSession App45963/ephemeral and legacy15961/15960 are disjoint");
    }
'''


class UdpProbeAppSessionContractChecks(unittest.TestCase):
    def test_real_java_parser_accepts_current_app_and_rejects_stale_port(self):
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        if not javac or not java:
            raise RuntimeError('Existing JDK required for actual parseSession fixture')
        probe = (ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        controls = '    static int parseContentHintFps' + probe.split('    static int parseContentHintFps',1)[1].split('    private static void writeReport',1)[0]
        parsing = '    private static Session parseSession' + probe.split('    private static Session parseSession',1)[1].split('    static JSONObject stageSummary',1)[0]
        nested = '    private static final class Session' + probe.split('    private static final class Session',1)[1].rsplit('\n}',1)[0]
        with tempfile.TemporaryDirectory(prefix='huoguo-actual-session-parser-') as folder:
            folder = Path(folder)
            source = folder/'UdpVideoProbe.java'
            source.write_text('package local.remoteandroid.direct;\nimport java.io.IOException; import java.net.InetAddress; import java.util.Base64; import org.json.JSONObject;\n'
                              'public final class UdpVideoProbe {\n'+controls+parsing+nested+HARNESS+'\n}\n')
            json = folder/'JSONObject.java'
            json.write_text(JSON_STUB)
            built = subprocess.run([javac,'-d',str(folder),str(source),str(json),
                str(ROOT/'app/src/udp/java/local/remoteandroid/direct/LanUdpContract.java')],
                capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            tested = subprocess.run([java,'-cp',str(folder),'local.remoteandroid.direct.UdpVideoProbe'],
                capture_output=True,text=True,timeout=5)
            self.assertEqual(tested.returncode,0,tested.stdout+tested.stderr)
            self.assertIn('PASS actual parseSession',tested.stdout)

    def test_server_default_is_same_high_port_and_old_probe_is_explicitly_separate(self):
        default = inspect.signature(UdpLanSessions).parameters['peer_port'].default
        self.assertEqual(default,45963)
        gateway = (ROOT/'udp_lan_gateway.py').read_text()
        self.assertIn("parser.add_argument('--udp-port', type=int, default=45963)",gateway)


if __name__ == '__main__':
    unittest.main()

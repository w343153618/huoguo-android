"""Actual source and inert Android-API doubles; not a device/VPN/route acceptance."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
UDP=ROOT/'app/src/udp/java/local/remoteandroid/direct'
JDK=Path(os.environ.get('JAVA_HOME') or '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')/'bin'
SDK=Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    or str(Path.home()/'Library/Android/sdk'))
ANDROID=SDK/'platforms/android-37.0/android.jar'


def java_tools():
    if (JDK/'javac').is_file() and (JDK/'java').is_file():return str(JDK/'javac'),str(JDK/'java')
    javac,java=shutil.which('javac'),shutil.which('java')
    if not javac or not java:raise RuntimeError('Existing JDK required')
    return javac,java


class NpsPhysicalNetworkBindingChecks(unittest.TestCase):
    def test_actual_lease_selection_bind_loss_and_scope_with_inert_api_doubles(self):
        stubs={
            'android/content/Context.java':'''package android.content; import android.net.ConnectivityManager; public final class Context {public final ConnectivityManager manager;public Context(ConnectivityManager m){manager=m;}public <T>T getSystemService(Class<T> c){return c.cast(manager);}}''',
            'android/net/NetworkCapabilities.java':'''package android.net;import java.util.HashSet;public final class NetworkCapabilities {public static final int NET_CAPABILITY_INTERNET=12,NET_CAPABILITY_NOT_VPN=15,NET_CAPABILITY_VALIDATED=16,TRANSPORT_WIFI=1,TRANSPORT_CELLULAR=0,TRANSPORT_VPN=4;public final HashSet<Integer> caps=new HashSet<>(),transports=new HashSet<>();public boolean hasCapability(int v){return caps.contains(v);}public boolean hasTransport(int v){return transports.contains(v);}}''',
            'android/net/Network.java':'''package android.net;import java.io.IOException;import java.net.Socket;import java.net.DatagramSocket;public final class Network {private final long id;public int bindCalls;public boolean bindFailure;public Network(long id){this.id=id;}public long getNetworkHandle(){return id;}public void bindSocket(Socket s)throws IOException{bindCalls++;if(bindFailure)throw new IOException("fixture bind rejected");}public void bindSocket(DatagramSocket s)throws IOException{bindCalls++;if(bindFailure)throw new IOException("fixture bind rejected");}}''',
            'android/net/ConnectivityManager.java':'''package android.net;import java.util.IdentityHashMap;public final class ConnectivityManager {public Network active;public Network[] networks;public final IdentityHashMap<Network,NetworkCapabilities> caps=new IdentityHashMap<>();public final IdentityHashMap<Network,Object> links=new IdentityHashMap<>();public Network getActiveNetwork(){return active;}public Network[] getAllNetworks(){return networks;}public NetworkCapabilities getNetworkCapabilities(Network n){return caps.get(n);}public Object getLinkProperties(Network n){return links.get(n);}}''',
            'local/remoteandroid/direct/LeaseCheck.java':r'''
package local.remoteandroid.direct;
import android.content.Context;import android.net.*;import java.io.IOException;import java.net.Socket;
public final class LeaseCheck {
    interface Checked {void run()throws Exception;}
    static int checks;
    static void ok(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}
    static void reject(Checked c)throws Exception{checks++;try{c.run();throw new AssertionError("accepted "+checks);}catch(IOException expected){}}
    static Network add(ConnectivityManager m,long id,int transport,boolean notVpn){
        Network n=new Network(id);NetworkCapabilities c=new NetworkCapabilities();c.caps.add(NetworkCapabilities.NET_CAPABILITY_INTERNET);if(notVpn)c.caps.add(NetworkCapabilities.NET_CAPABILITY_NOT_VPN);c.transports.add(transport);m.caps.put(n,c);m.links.put(n,new Object());return n;
    }
    public static void main(String[] args)throws Exception{
        ConnectivityManager m=new ConnectivityManager();Network vpn=add(m,30,NetworkCapabilities.TRANSPORT_VPN,false),wifi=add(m,10,NetworkCapabilities.TRANSPORT_WIFI,true),cell=add(m,20,NetworkCapabilities.TRANSPORT_CELLULAR,true);m.active=vpn;m.networks=new Network[]{vpn,cell,wifi};
        NpsPhysicalNetwork lease=NpsPhysicalNetwork.select(new Context(m));ok(lease.handle()==10&&lease.transport()==1);
        try(Socket raw=new Socket()){lease.bind(raw);ok(wifi.bindCalls==1&&vpn.bindCalls==0&&cell.bindCalls==0);ok(lease.httpsBindings()==1&&lease.udpBindings()==0);}
        NpsPhysicalNetwork.validateScope("nps_owner",lease);checks++;
        NpsPhysicalNetwork.validateScope("tailnet",null);checks++;
        reject(()->NpsPhysicalNetwork.validateScope("nps_owner",null));reject(()->NpsPhysicalNetwork.validateScope("tailnet",lease));reject(()->NpsPhysicalNetwork.validateScope("unknown",null));
        wifi.bindFailure=true;try(Socket raw=new Socket()){reject(()->lease.bind(raw));}ok(wifi.bindCalls==2&&cell.bindCalls==0&&vpn.bindCalls==0);ok(lease.httpsBindings()==1&&lease.udpBindings()==0);
        m.caps.remove(wifi);reject(lease::requireUsable);try(Socket raw=new Socket()){reject(()->lease.bind(raw));}ok(wifi.bindCalls==2&&cell.bindCalls==0);
        NpsPhysicalNetwork renewed=NpsPhysicalNetwork.select(new Context(m));ok(renewed.handle()==20&&renewed.transport()==2);
        m.active=vpn;m.networks=new Network[]{vpn};reject(()->NpsPhysicalNetwork.select(new Context(m)));
        m.networks=null;reject(()->NpsPhysicalNetwork.select(new Context(m)));
        reject(()->NpsPhysicalNetwork.select(new Context(null)));
        System.out.println(checks+" actual lease checks passed; no connect/send/listen or process binding");
    }
}
'''
        }
        javac,java=java_tools()
        with tempfile.TemporaryDirectory(prefix='huoguo-nps-network-lease-') as folder:
            paths=[]
            for name,source in stubs.items():
                path=Path(folder)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source);paths.append(str(path))
            paths += [str(UDP/name) for name in ('LanUdpContract.java','NpsPhysicalNetworkPolicy.java','NpsPhysicalNetwork.java')]
            build=subprocess.run([javac,'-d',folder,*paths],capture_output=True,text=True,timeout=30)
            self.assertEqual(build.returncode,0,build.stdout+build.stderr)
            result=subprocess.run([java,'-cp',folder,'local.remoteandroid.direct.LeaseCheck'],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('actual lease checks passed',result.stdout)

    def test_all_actual_app_java_sources_compile_against_existing_api37_without_apk_build(self):
        if not ANDROID.is_file():raise RuntimeError('Existing Android SDK API37 required')
        javac,_=java_tools()
        stubs={
            'BuildConfig.java':'''package local.remoteandroid.direct;final class BuildConfig {static final String VERSION_NAME="1.31-alpha.7",APPLICATION_ID="local.remoteandroid.direct.experiment",UPDATE_MANIFEST_URL="https://146.56.249.175:15556/experimental/experiment.json",RELEASE_CHANNEL="experimental";static final int VERSION_CODE=38;static final boolean AUTHENTICATED_LAN_UDP=true;}''',
            'R.java':'''package local.remoteandroid.direct;final class R {static final class raw{static final int server_cert=1,server_cert_m5=2;}static final class string{static final int app_name=1;}static final class drawable{static final int ic_back=1,ic_home=2,ic_tasks=3,ic_rotate=4,ic_files=5,ic_disconnect=6,avatar_photo=7;}}''',
        }
        with tempfile.TemporaryDirectory(prefix='huoguo-nps-network-api37-') as folder:
            paths=[]
            for name,body in stubs.items():path=Path(folder)/name;path.write_text(body);paths.append(str(path))
            paths += [str(p) for p in (ROOT/'app/src/main/java').rglob('*.java')]
            paths += [str(p) for p in (ROOT/'app/src/udp/java').rglob('*.java')]
            generated=('UdpVideoProbe.java','UdpVideoSecurity.java','NativeUdpFec.java','UdpAudioAssembler.java',
                'UdpAudioReceiver.java','BoundedPcmQueue.java','UdpTouchControl.java','CodecFileProbe.java',
                'FramedH264Fixture.java','CodecStartupGate.java')
            paths += [str(ROOT/'experiments/nps-transport/phone'/name) for name in generated]
            paths += [str(ROOT/'experiments/moonlight-v2/authenticated-lan'/name)
                for name in ('LanUiAcceptance.java', 'OwnerSourceTap.java')]
            result=subprocess.run([javac,'-cp',str(ANDROID),'-d',folder,*paths],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_only_public_attempt_selects_and_shares_one_network_for_control_and_media(self):
        ui=(UDP/'AuthenticatedLanUdpUi.java').read_text()
        self.assertIn('physicalNetwork=LanUdpContract.NPS_SCOPE.equals(networkScope)?NpsPhysicalNetwork.select(activity):null',ui)
        self.assertIn('final NpsPhysicalNetwork physicalNetwork;',ui)
        self.assertIn('attempt.codecStartupReadyEnabled,attempt.physicalNetwork,',ui)
        self.assertIn('attempt.physicalNetwork.bind(raw)',ui)
        self.assertLess(ui.index('attempt.physicalNetwork.bind(raw)'),ui.index('raw.connect('))
        self.assertIn('createSocket(raw,endpoint.host,endpoint.port,true)',ui)
        self.assertIn('attempt.https=raw',ui);self.assertIn('Socket socket=attempt.https',ui)
        self.assertIn('socket.startHandshake()',ui);self.assertIn('LanUdpContract.npsCertificateSha256(attempt.node)',ui)
        self.assertIn('if(raw!=null)raw.close()',ui)

    def test_media_binding_precedes_local_bind_and_alive_checks_do_not_pick_new_network(self):
        source=(ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        self.assertIn('NpsPhysicalNetwork.validateScope(descriptor.getString("network_scope"),physicalNetwork)',source)
        self.assertIn('runner.appPhysicalNetwork=physicalNetwork',source)
        self.assertIn('physicalNetwork.httpsBindings()<1',source)
        self.assertIn('new String[]{"nps_physical_network_binding"',source)
        self.assertLess(source.index('appPhysicalNetwork.bind(socket)'),source.index('socket.bind(new InetSocketAddress(session.bindPort))'))
        self.assertIn('if(appPhysicalNetwork!=null)appPhysicalNetwork.requireUsable();sendPayload',source)
        self.assertIn('nextAlive=now+750_000_000L',source)
        self.assertNotIn('NpsPhysicalNetwork.select(',source)
        self.assertIn('.put("packet_route_verified",false)',source)
        self.assertIn('.put("domestic_country_verified",false)',source)

    def test_no_process_binding_vpn_logout_or_default_fallback(self):
        files=[UDP/'NpsPhysicalNetwork.java',UDP/'NpsPhysicalNetworkPolicy.java',UDP/'AuthenticatedLanUdpUi.java',ROOT/'experiments/nps-transport/phone/UdpVideoProbe.java']
        source='\n'.join(p.read_text() for p in files)
        for forbidden in ('bindProcessToNetwork','setProcessDefaultNetwork','VpnService.prepare','tailscale logout','stopService('):self.assertNotIn(forbidden,source)
        self.assertIn('network.bindSocket(socket);requireUsable()',source)
        self.assertIn('default_network_fallback',source)
        self.assertIn('未回退 TCP',source)


if __name__=='__main__':unittest.main()

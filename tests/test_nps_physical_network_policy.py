"""Run actual pure physical selection policy; no Android or sockets."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

class NpsPhysicalNetworkPolicyChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-physical-policy-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        source=r'''
package local.remoteandroid.direct;
import java.io.IOException;import java.util.*;
public final class PhysicalPolicyCheck{
 static int checks;static void ok(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}
 static NpsPhysicalNetworkPolicy.Candidate c(long id,boolean active,boolean connected,boolean internet,boolean notVpn,boolean vpn,boolean wifi,boolean cell,boolean validated){return new NpsPhysicalNetworkPolicy.Candidate(id,active,connected,internet,notVpn,vpn,wifi,cell,validated);}
 static void reject(NpsPhysicalNetworkPolicy.Candidate[] cs)throws Exception{try{NpsPhysicalNetworkPolicy.select(cs);throw new AssertionError("accepted");}catch(IOException expected){checks++;}}
 public static void main(String[] args)throws Exception{String mode=args[0];var wifi=c(10,false,true,true,true,false,true,false,false);var cell=c(20,false,true,true,true,false,false,true,true);
  if(mode.equals("active")){var activeCell=c(20,true,true,true,true,false,false,true,false);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{wifi,activeCell})==activeCell);var activeWifi=c(10,true,true,true,true,false,true,false,false);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{cell,activeWifi})==activeWifi);}
  else if(mode.equals("vpn")){var vpn=c(1,true,true,true,true,true,true,true,true);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{vpn,wifi,cell})==wifi);reject(new NpsPhysicalNetworkPolicy.Candidate[]{vpn});}
  else if(mode.equals("eligibility")){boolean[][] invalid={{false,true,true,false,true,false},{true,false,true,false,true,false},{true,true,false,false,true,false},{true,true,true,true,true,false},{true,true,true,false,false,false}};for(boolean[] b:invalid)reject(new NpsPhysicalNetworkPolicy.Candidate[]{c(1,true,b[0],b[1],b[2],b[3],b[4],b[5],true)});}
  else if(mode.equals("rank")){var validatedWifi=c(30,false,true,true,true,false,true,false,true);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{cell,wifi,validatedWifi})==validatedWifi);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{wifi,cell})==wifi);var rawCell=c(40,false,true,true,true,false,false,true,false);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{cell,rawCell})==cell);}
  else if(mode.equals("identity")){reject(null);reject(new NpsPhysicalNetworkPolicy.Candidate[]{});reject(new NpsPhysicalNetworkPolicy.Candidate[]{wifi,null});reject(new NpsPhysicalNetworkPolicy.Candidate[]{wifi,wifi});try{c(0,false,true,true,true,false,true,false,true);throw new AssertionError();}catch(IllegalArgumentException expected){checks++;}}
  else if(mode.equals("tie")){var lower=c(2,false,true,true,true,false,true,false,false);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{wifi,lower})==lower);ok(NpsPhysicalNetworkPolicy.select(new NpsPhysicalNetworkPolicy.Candidate[]{lower,wifi})==lower);}
  else throw new AssertionError("mode");System.out.println("actual policy checks passed "+checks);
 }
}'''
        path=Path(cls.folder.name)/'PhysicalPolicyCheck.java';path.write_text(source)
        result=subprocess.run([javac,'-d',cls.folder.name,str(ROOT/'app/src/udp/java/local/remoteandroid/direct/NpsPhysicalNetworkPolicy.java'),str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode:cls.folder.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def run_case(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.PhysicalPolicyCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('actual policy checks passed',result.stdout)
    def test_active_physical_wifi_or_cell_has_precedence(self):self.run_case('active')
    def test_vpn_is_excluded_even_when_it_reports_wifi_and_notvpn(self):self.run_case('vpn')
    def test_disconnected_nointernet_nonphysical_or_missing_notvpn_fail_closed(self):self.run_case('eligibility')
    def test_underlying_wifi_then_validated_cell_preference(self):self.run_case('rank')
    def test_null_duplicate_missing_and_invalid_identity_rejected(self):self.run_case('identity')
    def test_equal_rank_is_deterministic_without_mutating_input(self):self.run_case('tie')

if __name__=='__main__':unittest.main()

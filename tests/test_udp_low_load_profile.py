"""Execute beta profile/migration policy; this is not real V50 playback evidence."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path(os.environ.get('JAVA_HOME') or '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home') / 'bin'


def java_tools():
    if (JDK/'javac').is_file() and (JDK/'java').is_file():return str(JDK/'javac'),str(JDK/'java')
    javac,java=shutil.which('javac'),shutil.which('java')
    if not javac or not java:raise RuntimeError('Existing JDK required')
    return javac,java


class LowLoadProfileCheck(unittest.TestCase):
    def test_semantic_fps_migration_and_conservative_identity_hints(self):
        fixture = '''package local.remoteandroid.direct;
public final class ProfileCheck {
 static void ok(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  ok(UdpLowLoadProfile.fpsForIndex(0)==30);ok(UdpLowLoadProfile.fpsForIndex(1)==60);
  ok(UdpLowLoadProfile.indexForSaved(null,null)==0);ok(UdpLowLoadProfile.indexForSaved(null,0)==1);
  ok(UdpLowLoadProfile.indexForSaved(null,1)==0);ok(UdpLowLoadProfile.indexForSaved(null,2)==0);
  ok(UdpLowLoadProfile.indexForSaved(30,0)==0);ok(UdpLowLoadProfile.indexForSaved(60,1)==1);
  ok(UdpLowLoadProfile.indexForSaved(120,0)==0);ok(UdpLowLoadProfile.indexForSaved(999,0)==0);
  for(int index:new int[]{-1,2,3,Integer.MAX_VALUE}){try{UdpLowLoadProfile.fpsForIndex(index);throw new AssertionError();}catch(IllegalArgumentException expected){}}
  ok(UdpLowLoadProfile.displayHint(30)==60);ok(UdpLowLoadProfile.displayHint(60)==60);ok(UdpLowLoadProfile.displayHint(120)==120);
  ok(UdpLowLoadProfile.preferLowLoad("realme","真我 V50","","",""));
  ok(UdpLowLoadProfile.preferLowLoad("realme","RMX3783","mt6835","",""));
  ok(UdpLowLoadProfile.preferLowLoad("brand","model","","","MediaTek Dimensity 6100+"));
  ok(!UdpLowLoadProfile.preferLowLoad("realme","RMX3783","qcom","","Snapdragon"));
  ok(!UdpLowLoadProfile.preferLowLoad("OnePlus","PJD110","qcom","kalama","SM8650"));
  ok(!UdpLowLoadProfile.preferLowLoad(null,null,null,null,null));
  ok(!UdpLowLoadProfile.preferLowLoad("vendor","model","some-mt-string","",""));
  System.out.println("profile checks passed");
 }
}'''
        javac,java=java_tools()
        with tempfile.TemporaryDirectory(prefix='huoguo-low-load-') as folder:
            source = Path(folder)/'ProfileCheck.java'
            source.write_text(fixture)
            subprocess.run([javac, '-d', folder, source,
                ROOT/'app/src/udp/java/local/remoteandroid/direct/UdpLowLoadProfile.java'],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([java, '-cp', folder,
                'local.remoteandroid.direct.ProfileCheck'], check=True,
                capture_output=True, text=True, timeout=5)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()

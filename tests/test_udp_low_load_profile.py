"""Execute beta profile/migration policy; this is not real V50 playback evidence."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


class LowLoadProfileCheck(unittest.TestCase):
    def test_existing_fps_indices_and_conservative_identity_hints(self):
        fixture = '''package local.remoteandroid.direct;
public final class ProfileCheck {
 static void ok(boolean value){if(!value)throw new AssertionError();}
 public static void main(String[] args){
  ok(UdpLowLoadProfile.fpsForIndex(0)==60);ok(UdpLowLoadProfile.fpsForIndex(1)==120);ok(UdpLowLoadProfile.fpsForIndex(2)==30);
  for(int index:new int[]{-1,3,Integer.MAX_VALUE}){try{UdpLowLoadProfile.fpsForIndex(index);throw new AssertionError();}catch(IllegalArgumentException expected){}}
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
        with tempfile.TemporaryDirectory(prefix='huoguo-low-load-') as folder:
            source = Path(folder)/'ProfileCheck.java'
            source.write_text(fixture)
            subprocess.run([JDK/'javac', '-d', folder, source,
                ROOT/'app/src/udp/java/local/remoteandroid/direct/UdpLowLoadProfile.java'],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([JDK/'java', '-cp', folder,
                'local.remoteandroid.direct.ProfileCheck'], check=True,
                capture_output=True, text=True, timeout=5)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()

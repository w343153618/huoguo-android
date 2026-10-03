"""Actual PasswordStore AES-GCM behavior with inert Android/keystore doubles.

This executes real JCE AES-GCM and the existing production store. It is not an
Android hardware-keystore, GUI recreation or deployed credential acceptance.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
JDK=Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
UDP=ROOT/'app/src/udp/java/local/remoteandroid/direct'

class UdpPasswordSaveChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix='huoguo-password-store-')
        cls.java=str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac=str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        sources={
'android/content/SharedPreferences.java':'''package android.content;public interface SharedPreferences{String getString(String k,String fallback);Editor edit();interface Editor{Editor putString(String k,String v);Editor clear();boolean commit();}}''',
'android/content/Context.java':'''package android.content;import java.util.*;public final class Context{public static final int MODE_PRIVATE=0;public final Prefs prefs=new Prefs();public SharedPreferences getSharedPreferences(String name,int mode){if(!name.equals("saved_password")||mode!=MODE_PRIVATE)throw new AssertionError("private store");return prefs;}public static final class Prefs implements SharedPreferences{public final Map<String,String> values=new HashMap<>();public boolean fail;public String getString(String k,String fallback){return values.getOrDefault(k,fallback);}public Editor edit(){return new Edit(this);}static final class Edit implements Editor{final Prefs target;final Map<String,String> pending;Edit(Prefs p){target=p;pending=new HashMap<>(p.values);}public Editor putString(String k,String v){pending.put(k,v);return this;}public Editor clear(){pending.clear();return this;}public boolean commit(){if(target.fail)return false;target.values.clear();target.values.putAll(pending);return true;}}}}''',
'android/util/Base64.java':'''package android.util;public final class Base64{public static final int NO_WRAP=2;public static String encodeToString(byte[] b,int flags){return java.util.Base64.getEncoder().encodeToString(b);}public static byte[] decode(String s,int flags){return java.util.Base64.getDecoder().decode(s);}}''',
'android/security/keystore/KeyProperties.java':'''package android.security.keystore;public final class KeyProperties{public static final String KEY_ALGORITHM_AES="AES",BLOCK_MODE_GCM="GCM",ENCRYPTION_PADDING_NONE="NoPadding";public static final int PURPOSE_ENCRYPT=1,PURPOSE_DECRYPT=2;}''',
'android/security/keystore/KeyGenParameterSpec.java':'''package android.security.keystore;public final class KeyGenParameterSpec implements java.security.spec.AlgorithmParameterSpec{public final int bits;public KeyGenParameterSpec(int bits){this.bits=bits;}public static final class Builder{private int bits;public Builder(String alias,int purposes){if(!alias.equals("huoguo.saved-password.v1")||purposes!=3)throw new AssertionError();}public Builder setBlockModes(String s){if(!s.equals("GCM"))throw new AssertionError();return this;}public Builder setEncryptionPaddings(String s){if(!s.equals("NoPadding"))throw new AssertionError();return this;}public Builder setKeySize(int n){bits=n;return this;}public Builder setRandomizedEncryptionRequired(boolean v){if(!v)throw new AssertionError();return this;}public KeyGenParameterSpec build(){return new KeyGenParameterSpec(bits);}}}''',
'local/remoteandroid/direct/PasswordCryptoCheck.java':r'''
package local.remoteandroid.direct;
import android.content.Context;import java.security.*;import java.security.cert.Certificate;import java.io.*;import java.util.*;import javax.crypto.*;import java.security.spec.AlgorithmParameterSpec;
public final class PasswordCryptoCheck{
 static SecretKey stored;static boolean exists;static int checks;static final String secret="inert fixture only 123";static final String host="https://146.56.249.175:49558",user="huoguo";
 static void ok(boolean b){checks++;if(!b)throw new AssertionError("check "+checks);}interface Checked{void run()throws Exception;}static void reject(Checked c)throws Exception{checks++;try{c.run();throw new AssertionError("accepted");}catch(AssertionError fail){throw fail;}catch(Exception expected){}}
 public static final class Store extends KeyStoreSpi{
  public Key engineGetKey(String a,char[] p){return exists?stored:null;}public boolean engineContainsAlias(String a){return exists;}public void engineDeleteEntry(String a){exists=false;stored=null;}
  public Certificate[] engineGetCertificateChain(String a){return null;}public Certificate engineGetCertificate(String a){return null;}public Date engineGetCreationDate(String a){return null;}public void engineSetKeyEntry(String a,Key k,char[] p,Certificate[] c){throw new UnsupportedOperationException();}public void engineSetKeyEntry(String a,byte[] k,Certificate[] c){throw new UnsupportedOperationException();}public void engineSetCertificateEntry(String a,Certificate c){throw new UnsupportedOperationException();}public Enumeration<String> engineAliases(){return Collections.emptyEnumeration();}public int engineSize(){return exists?1:0;}public boolean engineIsKeyEntry(String a){return exists;}public boolean engineIsCertificateEntry(String a){return false;}public String engineGetCertificateAlias(Certificate c){return null;}public void engineStore(OutputStream o,char[] p){}public void engineLoad(InputStream i,char[] p){}
 }
 public static final class Generator extends KeyGeneratorSpi{
  private int bits=256;protected void engineInit(SecureRandom r){}protected void engineInit(int bits,SecureRandom r){this.bits=bits;}
  protected void engineInit(AlgorithmParameterSpec spec,SecureRandom r){bits=((android.security.keystore.KeyGenParameterSpec)spec).bits;if(bits!=256)throw new AssertionError("key size");}
  protected SecretKey engineGenerateKey(){try{KeyGenerator g=KeyGenerator.getInstance("AES","SunJCE");g.init(bits);stored=g.generateKey();exists=true;return stored;}catch(Exception e){throw new RuntimeException(e);}}
 }
 public static void main(String[] args)throws Exception{
  Provider provider=new Provider("InertAndroidKeyStore",1.0,"test-only provider"){};provider.put("KeyStore.AndroidKeyStore",Store.class.getName());provider.put("KeyGenerator.AES",Generator.class.getName());Security.addProvider(provider);
  // Production lookup uses the provider name AndroidKeyStore for KeyGenerator.
  Provider named=new Provider("AndroidKeyStore",1.0,"test-only provider"){};named.put("KeyGenerator.AES",Generator.class.getName());Security.addProvider(named);
  Context c=new Context();PasswordStore store=new PasswordStore(c);String mode=args[0];
  if(mode.equals("crypto")){store.save(host,user,secret);ok(exists&&stored.getEncoded().length==32);ok(!c.prefs.values.containsValue(secret)&&c.prefs.values.keySet().equals(Set.of("host","username","iv","ciphertext")));ok(store.load(host,user).equals(secret));String iv=c.prefs.values.get("iv");store.save(host,user,secret);ok(!iv.equals(c.prefs.values.get("iv")));ok(new PasswordStore(c).load(host,user).equals(secret));}
  else if(mode.equals("account")){store.save(host,user,secret);ok(store.load(host+"/other",user).isEmpty());ok(store.load(host,"wyw").isEmpty());c.prefs.values.put("host",host+"/tamper");reject(()->store.load(host+"/tamper",user));c.prefs.values.put("host",host);c.prefs.values.put("username","wyw");reject(()->store.load(host,"wyw"));}
  else if(mode.equals("tamper")){store.save(host,user,secret);byte[] bytes=java.util.Base64.getDecoder().decode(c.prefs.values.get("ciphertext"));bytes[0]^=1;c.prefs.values.put("ciphertext",java.util.Base64.getEncoder().encodeToString(bytes));reject(()->store.load(host,user));}
  else if(mode.equals("clear")){store.save(host,user,secret);c.prefs.fail=true;reject(store::clear);ok(store.load(host,user).equals(secret)&&exists);c.prefs.fail=false;store.clear();ok(c.prefs.values.isEmpty()&&!exists);ok(new PasswordStore(c).load(host,user).isEmpty());store.save(host,user,secret);ok(new PasswordStore(c).load(host,user).equals(secret));}
  else if(mode.equals("commit")){store.save(host,user,secret);c.prefs.fail=true;reject(()->store.save(host,user,"replacement"));ok(new PasswordStore(c).load(host,user).equals(secret));}
  else throw new AssertionError("mode");System.out.println("actual PasswordStore checks passed "+checks);
 }
}'''}
        paths=[]
        for name,source in sources.items():
            path=Path(cls.folder.name)/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(source);paths.append(str(path))
        paths.append(str(ROOT/'app/src/main/java/local/remoteandroid/direct/PasswordStore.java'))
        result=subprocess.run([javac,'-d',cls.folder.name,*paths],capture_output=True,text=True,timeout=30)
        if result.returncode:cls.folder.cleanup();raise AssertionError(result.stdout+result.stderr)
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def run_case(self,mode):
        result=subprocess.run([self.java,'-cp',self.folder.name,'local.remoteandroid.direct.PasswordCryptoCheck',mode],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('actual PasswordStore checks passed',result.stdout)
    def test_existing_store_uses_real_aes_gcm_random_iv_and_ciphertext_only(self):self.run_case('crypto')
    def test_full_endpoint_and_username_binding_rejects_tampered_metadata(self):self.run_case('account')
    def test_ciphertext_authentication_detects_tampering(self):self.run_case('tamper')
    def test_clear_is_persistent_and_failed_commit_does_not_delete_key(self):self.run_case('clear')
    def test_failed_save_commit_does_not_report_success_or_replace_saved_password(self):self.run_case('commit')
    def test_beta_ui_uses_existing_private_store_only_on_explicit_save_and_restore(self):
        source=(UDP/'AuthenticatedLanUdpUi.java').read_text()
        self.assertIn('passwordStore=new PasswordStore(activity)',source)
        self.assertIn('passwordStore.save(Endpoint.identity(destination),name,secret)',source)
        self.assertIn('passwordStore.load(Endpoint.identity(destination),user.getText().toString())',source)
        self.assertIn('passwordStore.clear();password.setText("")',source)
        self.assertIn('remember.setOnClickListener(v->savePassword())',source)
        settings=source[source.index('private void saveSettings()'):source.index('private void savePassword()')]
        self.assertNotIn('secret',settings);self.assertNotIn('putString("password"',source);self.assertNotIn('Log.',source)
        self.assertIn('restorePassword();ScrollView',source)
    def test_optin_helper_exercises_actual_save_reopen_clear_reopen_and_retained_save(self):
        source=(ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java').read_text()
        self.assertIn('credentialSave())a=credentialUiAcceptance',source)
        for label in ('保存密码','清除已保存密码'):self.assertIn('clickLabel(',source);self.assertIn(label,source)
        self.assertIn('runOnMainSync(old::finish)',source)
        self.assertIn('credential_save_reopen_restored',source);self.assertIn('credential_clear_reopen_empty',source)
        self.assertIn('credential_final_save_reopen_retained',source);self.assertIn('.put("credential_secret_exported",false)',source)
        self.assertNotIn('report.put("password"',source);self.assertNotIn('report.put("ciphertext"',source)

if __name__=='__main__':unittest.main()

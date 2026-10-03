package local.remoteandroid.direct;

import java.io.IOException;
import java.util.Collections;
import java.util.Set;

/** Executes the production policy only; no Android, network or package installs. */
public final class UpdateChannelPolicyCheck {
    private static int checks;
    private static final String S=UpdateChannelPolicy.STABLE_PACKAGE,T=UpdateChannelPolicy.TEST_PACKAGE;
    private static final Set<String> SIGNER=Collections.singleton(UpdateChannelPolicy.SIGNER_SHA256);
    private interface Checked{void run()throws Exception;}
    private static void ok(boolean condition){checks++;if(!condition)throw new AssertionError("check "+checks);}
    private static void rejected(Checked body)throws Exception{checks++;try{body.run();throw new AssertionError("accepted check "+checks);}catch(IOException expected){}}
    private static void manifest(String channel,String pkg,String signer,String name,long code,String url,String hash,long size)throws Exception{
        UpdateChannelPolicy.manifest(channel,pkg,signer,name,code,url,hash,size);
    }
    public static void main(String[] args)throws Exception{
        String mode=args[0],url="https://146.56.249.175:15556/updates/app.apk",hash=String.join("",Collections.nCopies(64,"a"));
        if(mode.equals("actions")){
            ok(UpdateChannelPolicy.action(T,S,31,0)==UpdateChannelPolicy.INSTALL);
            ok(UpdateChannelPolicy.action(T,S,31,31)==UpdateChannelPolicy.OPEN_OTHER);
            ok(UpdateChannelPolicy.action(T,S,31,32)==UpdateChannelPolicy.OPEN_OTHER);
            ok(UpdateChannelPolicy.action(T,S,32,31)==UpdateChannelPolicy.INSTALL);
            ok(UpdateChannelPolicy.action(S,T,37,0)==UpdateChannelPolicy.INSTALL);
            ok(UpdateChannelPolicy.action(S,T,37,38)==UpdateChannelPolicy.OPEN_OTHER);
            ok(UpdateChannelPolicy.action(T,T,37,37)==UpdateChannelPolicy.LATEST);
            ok(UpdateChannelPolicy.action(S,S,32,31)==UpdateChannelPolicy.INSTALL);
            rejected(()->UpdateChannelPolicy.action(S,S,31,32));
            rejected(()->UpdateChannelPolicy.action(T,T,36,37));
            rejected(()->UpdateChannelPolicy.action("unknown",S,31,0));
            rejected(()->UpdateChannelPolicy.action(T,"unknown",31,0));
            rejected(()->UpdateChannelPolicy.action(T,S,0,0));
            rejected(()->UpdateChannelPolicy.action(T,S,31,-1));
        }else if(mode.equals("channels")){
            ok(UpdateChannelPolicy.packageName("stable").equals(S));
            ok(UpdateChannelPolicy.packageName("experimental").equals(T));
            ok(UpdateChannelPolicy.channelForPackage(T).equals("experimental"));
            ok(UpdateChannelPolicy.manifestUrl("stable").equals("https://146.56.249.175:15556/updates/update.json"));
            ok(UpdateChannelPolicy.manifestUrl("experimental").equals("https://146.56.249.175:15556/experimental/experiment.json"));
            rejected(()->UpdateChannelPolicy.packageName("test"));
            rejected(()->UpdateChannelPolicy.channelForPackage("local.remoteandroid.other"));
        }else if(mode.equals("manifest")){
            manifest("stable",null,null,"1.30",31,url,hash,1);checks++;
            manifest("stable",S,UpdateChannelPolicy.SIGNER_SHA256,"1.31",32,url,hash,67108864);checks++;
            manifest("experimental",T,UpdateChannelPolicy.SIGNER_SHA256,"1.31-alpha.6",37,url,hash,1);checks++;
            rejected(()->manifest("experimental",null,null,"1.31-alpha.6",37,url,hash,1));
            rejected(()->manifest("experimental",S,null,"1.31-alpha.6",37,url,hash,1));
            rejected(()->manifest("stable",T,null,"1.30",31,url,hash,1));
            rejected(()->manifest("stable",S,"b"+hash.substring(1),"1.30",31,url,hash,1));
            for(String name:new String[]{"1.31-alpha.6","../1.30","", "1"})rejected(()->manifest("stable",S,null,name,31,url,hash,1));
            for(String name:new String[]{"1.30","1.31-alpha.0","../1.31-alpha.6","1.31-alpha6"})rejected(()->manifest("experimental",T,null,name,37,url,hash,1));
            for(long size:new long[]{-1,0,67108865})rejected(()->manifest("stable",S,null,"1.30",31,url,hash,size));
            for(long code:new long[]{0,-1,2100000001L})rejected(()->manifest("stable",S,null,"1.30",code,url,hash,1));
            rejected(()->manifest("stable",S,null,"1.30",31,url,"bad",1));
        }else if(mode.equals("https")){
            ok(UpdateChannelPolicy.https(url).getProtocol().equals("https"));
            ok(UpdateChannelPolicy.https("https://release-assets.githubusercontent.com/a?sig=public").getQuery()!=null);
            for(String bad:new String[]{"http://example.com/a","file:///tmp/app.apk","https://owner:secret@example.com/a","https://example.com/a#other","https:///a",""})rejected(()->UpdateChannelPolicy.https(bad));
            rejected(()->UpdateChannelPolicy.https(null));
        }else if(mode.equals("archives")){
            UpdateChannelPolicy.archive(T,S,31,0,S,31,SIGNER,SIGNER,1);checks++;
            UpdateChannelPolicy.archive(S,T,37,0,T,37,SIGNER,SIGNER,1);checks++;
            UpdateChannelPolicy.archive(T,T,38,37,T,38,SIGNER,SIGNER,1);checks++;
            rejected(()->UpdateChannelPolicy.archive(T,S,31,0,T,31,SIGNER,SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,0,S,30,SIGNER,SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,31,S,31,SIGNER,SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,32,S,31,SIGNER,SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,T,36,37,T,36,SIGNER,SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,0,S,31,Collections.emptySet(),SIGNER,1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,0,S,31,SIGNER,Collections.singleton("bad"),1));
            rejected(()->UpdateChannelPolicy.archive(T,S,31,0,S,31,SIGNER,SIGNER,2));
        }else throw new AssertionError("unknown fixture");
        System.out.println(mode+": "+checks+" actual production policy checks passed");
    }
}

package local.remoteandroid.direct;

import java.io.IOException;
import java.net.URL;
import java.util.Collections;
import java.util.Set;

/** Pure channel/package/version/signature boundaries; opens no network or installer. */
public final class UpdateChannelPolicy {
    private UpdateChannelPolicy(){}
    public static final String STABLE="stable",TEST="experimental";
    public static final String STABLE_PACKAGE="local.remoteandroid.direct",
        TEST_PACKAGE="local.remoteandroid.direct.experiment";
    public static final String STABLE_URL="https://146.56.249.175:15556/updates/update.json",
        TEST_URL="https://146.56.249.175:15556/experimental/experiment.json";
    public static final String SIGNER_SHA256="0d54d7cedd794e5beb96a27a69fbc57ae6453013a240dd3c5c7804baeff682da";
    public static final long MAX_APK_SIZE=67108864L;
    public static final int INSTALL=1,LATEST=2,OPEN_OTHER=3;
    public static String packageName(String channel)throws IOException{
        if(STABLE.equals(channel))return STABLE_PACKAGE;if(TEST.equals(channel))return TEST_PACKAGE;
        throw new IOException("未知更新通道");
    }
    public static String channelForPackage(String packageName)throws IOException{
        if(STABLE_PACKAGE.equals(packageName))return STABLE;if(TEST_PACKAGE.equals(packageName))return TEST;
        throw new IOException("未知应用身份");
    }
    public static String manifestUrl(String channel)throws IOException{
        packageName(channel);return STABLE.equals(channel)?STABLE_URL:TEST_URL;
    }
    public static String label(String channel)throws IOException{
        packageName(channel);return STABLE.equals(channel)?"稳定版":"测试版";
    }
    public static URL https(String value)throws IOException{
        if(value==null)throw new IOException("更新必须使用 HTTPS");
        URL url=new URL(value);
        if(!"https".equals(url.getProtocol())||url.getHost().isEmpty()||url.getUserInfo()!=null||url.getRef()!=null)
            throw new IOException("更新必须使用 HTTPS，不能包含账号或片段");
        return url;
    }
    public static void manifest(String channel,String declaredPackage,String declaredSigner,String versionName,
            long code,String apkUrl,String hash,long size)throws IOException{
        String expected=packageName(channel);
        // Already published stable metadata predates application_id. Only that
        // channel may omit it; APK package/signature validation stays mandatory.
        if((declaredPackage==null&&TEST.equals(channel))||(declaredPackage!=null&&!expected.equals(declaredPackage)))throw new IOException("更新通道与应用包名不匹配");
        if(declaredSigner!=null&&!SIGNER_SHA256.equals(declaredSigner))throw new IOException("更新签名说明不匹配");
        if(versionName==null||!(STABLE.equals(channel)?versionName.matches("[0-9]+(?:\\.[0-9]+){1,3}")
                :versionName.matches("[0-9]+\\.[0-9]+(?:\\.[0-9]+)?-(?:alpha|beta|rc)\\.[1-9][0-9]*")))throw new IOException("更新版本名称无效");
        if(code<1||code>2100000000L||size<1||size>MAX_APK_SIZE||hash==null||!hash.matches("[0-9a-fA-F]{64}"))throw new IOException("更新校验信息无效");
        https(apkUrl);
    }
    /** Compare against the chosen package, never a beta code against stable's code. */
    public static int action(String currentPackage,String targetPackage,long available,long targetInstalled)throws IOException{
        channelForPackage(currentPackage);channelForPackage(targetPackage);
        if(available<1||available>2100000000L||targetInstalled<0)throw new IOException("更新版本码无效");
        if(currentPackage.equals(targetPackage)&&available<targetInstalled)throw new IOException("服务器返回旧版本，已拒绝同一应用降级");
        if(targetInstalled==0||available>targetInstalled)return INSTALL;
        return currentPackage.equals(targetPackage)?LATEST:OPEN_OTHER;
    }
    public static void archive(String currentPackage,String targetPackage,long expected,long targetInstalled,
            String archivePackage,long archiveVersion,Set<String> sourceSigners,Set<String> archiveSigners,int signerCount)throws IOException{
        if(!packageName(channelForPackage(targetPackage)).equals(archivePackage)||archiveVersion!=expected
                ||action(currentPackage,targetPackage,expected,targetInstalled)!=INSTALL
                ||signerCount!=1||!Collections.singleton(SIGNER_SHA256).equals(sourceSigners)
                ||!Collections.singleton(SIGNER_SHA256).equals(archiveSigners))throw new IOException("安装包身份、版本或签名不匹配");
    }
}

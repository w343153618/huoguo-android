package local.remoteandroid.direct;

import java.util.Arrays;
import java.nio.ByteBuffer;

/** Actual JVM AES-GCM and anti-replay checks, no sockets or Android devices. */
public final class UdpVideoSecurityProbe {
    private static void check(boolean value,String reason){if(!value)throw new AssertionError(reason);}
    private static void rejected(UdpVideoSecurity security,byte[] packet,String expected)throws Exception{
        try{security.open(packet,packet.length);throw new AssertionError("Invalid packet accepted: "+expected);}
        catch(UdpVideoSecurity.Rejected refusal){check(refusal.reason.equals(expected),"Wrong rejection class");}
    }
    public static void main(String[] args)throws Exception{
        byte[] key=new byte[32];for(int index=0;index<key.length;index++)key[index]=(byte)(index*7+3);
        UdpVideoSecurity sender=new UdpVideoSecurity(key,0x123456789abcdef0L),phone=new UdpVideoSecurity(key,0x123456789abcdef0L);
        byte[] body=new byte[1080];for(int index=0;index<body.length;index++)body[index]=(byte)index;
        byte[] first=sender.seal(body,1,UdpVideoSecurity.SERVER_NONCE_PREFIX);
        check(first.length==1120,"AAD24 + payload1080 + tag16 wire size");
        check(Arrays.equals(phone.open(first,first.length),body),"Authenticated complete payload changed");
        rejected(phone,first,"replay");
        byte[] forged=sender.seal(body,100_000,UdpVideoSecurity.SERVER_NONCE_PREFIX);forged[forged.length-1]^=1;
        rejected(phone,forged,"authentication");
        byte[] second=sender.seal(body,2,UdpVideoSecurity.SERVER_NONCE_PREFIX);
        check(Arrays.equals(phone.open(second,second.length),body),"Invalid tag advanced replay window");
        byte[] clientDirection=sender.ready(3);rejected(phone,clientDirection,"authentication");
        byte[] aadTampered=sender.seal(body,4,UdpVideoSecurity.SERVER_NONCE_PREFIX);
        ByteBuffer.wrap(aadTampered).putLong(16,5);rejected(phone,aadTampered,"authentication");
        byte[] wrongSession=sender.seal(body,4,UdpVideoSecurity.SERVER_NONCE_PREFIX);wrongSession[8]^=1;
        rejected(phone,wrongSession,"header");
        rejected(phone,new byte[39],"header");rejected(phone,new byte[1401],"header");
        try{sender.seal(new byte[1361],5,UdpVideoSecurity.SERVER_NONCE_PREFIX);throw new AssertionError("Oversized seal accepted");}
        catch(java.security.GeneralSecurityException expected){}
        byte[] ready=sender.ready(0),request=sender.keyframe(1);
        check(ready.length==45&&request.length==48,"Control wire framing");
        UdpVideoSecurity.ReplayWindow window=new UdpVideoSecurity.ReplayWindow();
        check(window.accept(5000)&&window.accept(905)&&!window.accept(905),"Window boundary and duplicate");
        check(!window.accept(904),"Out-of-window replay");
        check(window.accept(9096)&&!window.accept(5000)&&window.accept(9095),"Modulo replacement and reordering");
        UdpVideoSecurity.ReplayWindow unsigned=new UdpVideoSecurity.ReplayWindow();
        check(unsigned.accept(Long.MAX_VALUE)&&unsigned.accept(Long.MIN_VALUE),"Unsigned sequence transition");
        check(unsigned.accept(Long.MAX_VALUE-1)&&!unsigned.accept(Long.MAX_VALUE),"Unsigned reordering/duplicate");
        check(unsigned.accept(-1L)&&!unsigned.accept(0),"Sequence counter wrap must not reuse nonce");
        System.out.println("UdpVideoSecurityProbe PASS");
    }
}

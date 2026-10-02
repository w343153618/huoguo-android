package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.BitSet;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** Pure authenticated datagram framing; no sockets, storage or Android dependencies. */
final class UdpVideoSecurity {
    static final int MAGIC=0x48475545,VERSION=1,HEADER_BYTES=24,MAX_DATAGRAM=1400;
    static final int SERVER_NONCE_PREFIX=0x48475545,CLIENT_NONCE_PREFIX=0x48475543;
    private final SecretKeySpec key;
    private final long sessionTag;
    private final ReplayWindow replay=new ReplayWindow();

    UdpVideoSecurity(byte[] key,long sessionTag) {
        if(key==null||key.length!=32)throw new IllegalArgumentException("AES256 key length");
        this.key=new SecretKeySpec(key,"AES");this.sessionTag=sessionTag;
    }

    byte[] ready(long sequence)throws GeneralSecurityException {
        return seal("READY".getBytes(StandardCharsets.US_ASCII),sequence,CLIENT_NONCE_PREFIX);
    }
    byte[] keyframe(long sequence)throws GeneralSecurityException {
        return seal("KEYFRAME".getBytes(StandardCharsets.US_ASCII),sequence,CLIENT_NONCE_PREFIX);
    }

    byte[] seal(byte[] plaintext,long sequence,int noncePrefix)throws GeneralSecurityException {
        byte[] header=ByteBuffer.allocate(HEADER_BYTES).order(ByteOrder.BIG_ENDIAN)
            .putInt(MAGIC).putInt(VERSION).putLong(sessionTag).putLong(sequence).array();
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE,key,new GCMParameterSpec(128,nonce(noncePrefix,sequence)));cipher.updateAAD(header);
        byte[] encrypted=cipher.doFinal(plaintext);
        if(header.length+encrypted.length>MAX_DATAGRAM)throw new GeneralSecurityException("Datagram bound");
        return ByteBuffer.allocate(header.length+encrypted.length).put(header).put(encrypted).array();
    }

    byte[] open(byte[] datagram,int length)throws Rejected {
        if(length<HEADER_BYTES+16||length>MAX_DATAGRAM||length>datagram.length)throw new Rejected("header");
        ByteBuffer header=ByteBuffer.wrap(datagram,0,HEADER_BYTES).order(ByteOrder.BIG_ENDIAN);
        if(header.getInt()!=MAGIC||header.getInt()!=VERSION||header.getLong()!=sessionTag)throw new Rejected("header");
        long sequence=header.getLong();
        if(sequence==0)throw new Rejected("header"); // Server data starts at one; READY uses client sequence zero.
        byte[] plaintext;
        try {
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,nonce(SERVER_NONCE_PREFIX,sequence)));
            cipher.updateAAD(datagram,0,HEADER_BYTES);plaintext=cipher.doFinal(datagram,HEADER_BYTES,length-HEADER_BYTES);
        } catch(GeneralSecurityException failure){throw new Rejected("authentication");}
        // An unauthenticated sequence must never advance or clear the window.
        if(!replay.accept(sequence))throw new Rejected("replay");
        return plaintext;
    }

    private static byte[] nonce(int prefix,long sequence) {
        return ByteBuffer.allocate(12).order(ByteOrder.BIG_ENDIAN).putInt(prefix).putLong(sequence).array();
    }

    static final class Rejected extends Exception {
        final String reason;
        Rejected(String reason){super(reason);this.reason=reason;}
    }

    static final class ReplayWindow {
        static final int SIZE=4096;
        private final BitSet seen=new BitSet(SIZE);
        private final long[] slotSequence=new long[SIZE];
        private boolean started;private long newest;
        boolean accept(long sequence) {
            if(!started){started=true;newest=sequence;}
            else if(Long.compareUnsigned(sequence,newest)>0)newest=sequence;
            else if(Long.compareUnsigned(newest-sequence,SIZE)>=0)return false;
            // A modulo slot is unique within a 4096-sequence window. Retaining
            // its exact sequence avoids O(window) shifts for every video packet.
            int slot=(int)(sequence&(SIZE-1));
            if(seen.get(slot)&&slotSequence[slot]==sequence)return false;
            seen.set(slot);slotSequence[slot]=sequence;return true;
        }
    }
}

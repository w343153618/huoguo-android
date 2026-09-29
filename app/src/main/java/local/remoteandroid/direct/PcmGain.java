package local.remoteandroid.direct;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** PCM16 gain with a bounded soft knee; consumes neither the codec buffer nor its position. */
final class PcmGain {
    static void process(ByteBuffer input,ByteBuffer output,float gain){
        if(gain<1||gain>3||Float.isNaN(gain))throw new IllegalArgumentException("Invalid gain");
        ByteBuffer source=input.duplicate().order(ByteOrder.LITTLE_ENDIAN);
        if((source.remaining()&1)!=0||source.remaining()>output.capacity())throw new IllegalArgumentException("Invalid PCM buffer");
        output.clear();output.order(ByteOrder.LITTLE_ENDIAN);
        if(gain==1){output.put(source);output.flip();return;}
        while(source.hasRemaining()){
            short sample=source.getShort();
            double level=Math.abs((double)sample)*gain/32768.0;
            if(level>0.9){double excess=level-0.9;level=0.9+excess/(1+excess/0.1);}
            int value=(int)Math.round(Math.copySign(level*32767,sample));
            output.putShort((short)Math.max(-32768,Math.min(32767,value)));
        }
        output.flip();
    }
}

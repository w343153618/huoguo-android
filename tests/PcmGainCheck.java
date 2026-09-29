package local.remoteandroid.direct;
import java.nio.*;
public class PcmGainCheck {
    static void check(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args){
        ByteBuffer input=ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
        for(short s:new short[]{0,1000,-1000,10000,-10000,32767,-32768,0})input.putShort(s);input.flip();
        ByteBuffer out=ByteBuffer.allocateDirect(16);
        PcmGain.process(input.asReadOnlyBuffer(),out,1);check(out.equals(input));
        PcmGain.process(input.asReadOnlyBuffer(),out,2);check(input.position()==0&&out.remaining()==16);
        check(out.getShort()==0);check(Math.abs(out.getShort()-2000)<=1);check(Math.abs(out.getShort()+2000)<=1);
        check(out.getShort()>19000&&out.getShort()<-19000);
        int positive=out.getShort(),negative=out.getShort();check(positive>30000&&positive<32767&&negative<-30000&&negative>-32768);check(out.getShort()==0);
        // Quiet sine energy doubles in amplitude; loud peaks retain sign, ordering and safe bounds.
        input=ByteBuffer.allocate(960).order(ByteOrder.LITTLE_ENDIAN);double original=0;
        for(int i=0;i<480;i++){short sample=(short)(2000*Math.sin(2*Math.PI*i/48));input.putShort(sample);original+=(double)sample*sample;}input.flip();out=ByteBuffer.allocateDirect(960);PcmGain.process(input,out,1.5f);double boosted=0;while(out.hasRemaining()){double sample=out.getShort();boosted+=sample*sample;}check(Math.abs(Math.sqrt(boosted/original)-1.5)<0.002);
        System.out.println("PCM gain: read-only input, unity, silence, quiet RMS and soft-limited peaks PASS");
    }
}

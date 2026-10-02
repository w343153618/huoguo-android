package local.remoteandroid.direct;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Arrays;

/** Bounds/framing checks only; no codec or Android device runs. */
public final class FramedH264FixtureProbe {
    private static byte[] fixture(int frames,long step)throws Exception {
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);
        out.writeInt(0x68323634);out.writeInt(0x80000000);out.writeInt(540);out.writeInt(1200);
        out.writeLong(FramedH264Fixture.CONFIG);out.writeInt(5);out.write(new byte[]{0,0,0,1,0x67});
        for(int index=0;index<frames;index++) {
            out.writeLong(1_790_000_000_000_000L+index*step|(index==0?FramedH264Fixture.KEYFRAME:0));
            out.writeInt(5);out.write(new byte[]{0,0,0,1,0x65});
        }
        return bytes.toByteArray();
    }
    private static void rejected(byte[] bytes,String label)throws Exception {
        try {FramedH264Fixture.parse(bytes,60);throw new AssertionError(label+" accepted");}
        catch(IOException expected) { }
    }
    public static void main(String[] args)throws Exception {
        byte[] valid=fixture(120,16667);
        FramedH264Fixture source=FramedH264Fixture.parse(valid,60);
        if(source.mediaFrames!=120||source.units.size()!=121||source.width!=540||source.durationUs<1_900_000)
            throw new AssertionError("valid source changed");
        if(!source.units.get(0).config||!source.units.get(1).keyframe)
            throw new AssertionError("flags changed");
        rejected(Arrays.copyOf(valid,valid.length-1),"truncation");
        byte[] bad=valid.clone();bad[0]=0;rejected(bad,"magic");
        rejected(fixture(120,0),"nonmonotonic PTS");
        rejected(fixture(2,16667),"subsecond duration");
        rejected(fixture(120,500000),"overlong duration");
        rejected(new byte[FramedH264Fixture.MAX_BYTES+1],"oversized source");
        rejected(fixture(3602,8333),"excessive frame count");
        System.out.println("FramedH264FixtureProbe PASS");
    }
}

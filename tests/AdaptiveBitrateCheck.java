package local.remoteandroid.direct;
public final class AdaptiveBitrateCheck {
    static void check(boolean value){if(!value)throw new AssertionError();}
    static long pts;
    static void window(AdaptiveBitrate a,long delay){for(int i=0;i<90;i++){pts+=33333;a.packet(pts,pts*1000+delay*1000000);}}
    public static void main(String[] args){
        AdaptiveBitrate a=new AdaptiveBitrate(2500000);check(a.target()==1750000);
        window(a,1000);check(a.update(30)==1750000);
        window(a,1250);check(a.update(200)==1312500);
        window(a,1400);check(a.update(250)==984375);
        for(int i=0;i<12;i++){window(a,1400+i*200);a.update(250);}
        check(a.target()==500000);
        int floor=a.target();for(int i=0;i<20;i++)a.update(30);check(a.target()==floor);
        // New timestamp timeline, like source rotation/recreation: clean baseline, no restart.
        a.packet(1,1000000000);pts=1;
        for(int i=0;i<100;i++){window(a,1000);a.update(30);}
        check(a.target()==2500000);
        AdaptiveBitrate low=new AdaptiveBitrate(500000);check(low.target()==500000);
        System.out.println("AdaptiveBitrate congestion, recovery, silence, floor and ceiling checks passed");
    }
}

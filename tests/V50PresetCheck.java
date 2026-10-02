package local.remoteandroid.direct;
public class V50PresetCheck {
    static void check(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args){
        V50Preset fast=new V50Preset(true,true);
        check(fast.maxSize==960&&fast.bitrate==4000000&&fast.fps==30&&fast.bufferMs==80&&fast.mode.equals("ADAPTIVE_VBR"));
        V50Preset limited=new V50Preset(true,false);
        check(limited.maxSize==960&&limited.bitrate==4000000&&limited.fps==30);
        V50Preset software=new V50Preset(false,true);
        check(software.maxSize==768&&software.bitrate==1500000&&software.fps==30&&software.mode.equals("VBR")&&software.bufferMs==80);
        System.out.println("V50 preset: hardware, rate-limited and software fallback PASS");
    }
}

package local.remoteandroid.direct;
public class V50PresetCheck {
    static void check(boolean value){if(!value)throw new AssertionError();}
    public static void main(String[] args){
        V50Preset fast=new V50Preset(true,true);
        check(fast.maxSize==1200&&fast.bitrate==2500000&&fast.fps==60&&fast.bufferMs==100&&fast.mode.equals("CBR"));
        V50Preset limited=new V50Preset(true,false);
        check(limited.maxSize==1200&&limited.bitrate==2500000&&limited.fps==30);
        V50Preset software=new V50Preset(false,true);
        check(software.maxSize==960&&software.bitrate==1500000&&software.fps==30);
        System.out.println("V50 preset: hardware, rate-limited and software fallback PASS");
    }
}

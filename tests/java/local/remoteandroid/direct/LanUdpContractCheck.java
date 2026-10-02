package local.remoteandroid.direct;
import java.util.*;
import java.io.IOException;
public final class LanUdpContractCheck {
    private static int checks;
    private static Map<String,Object> valid(){
        Map<String,Object> map=new HashMap<>();map.put("protocol","HGUE_UDP_V1");map.put("session","00000000000000000000000000000001");
        map.put("key_b64",Base64.getEncoder().encodeToString(new byte[32]));map.put("session_tag_hex","0000000000000001");map.put("peer_host","192.168.9.128");
        map.put("peer_port",15963);map.put("bind_port",0);map.put("seconds",120);map.put("fps",60);map.put("buffer_ms",80);map.put("display_hz",120);map.put("surface_submit_lead_ms",0);
        map.put("video_release","scheduled");for(String key:new String[]{"audio_enabled","touch_enabled","async_video","decoder_reanchor_enabled"})map.put(key,true);return map;
    }
    private static void reject(String key,Object value)throws Exception{Map<String,Object> map=valid();map.put(key,value);try{LanUdpContract.validate(map,"192.168.9.128");throw new AssertionError("accepted "+key);}catch(IOException good){checks++;}}
    public static void main(String[] args)throws Exception{
        LanUdpContract.validate(valid(),"192.168.9.128");checks++;
        for(String ip:new String[]{"10.0.0.1","172.16.0.1","172.31.255.254","192.168.9.128"}){if(!LanUdpContract.privateIpv4(ip))throw new AssertionError("private range");checks++;}
        for(String ip:new String[]{"127.0.0.1","0.0.0.0","224.0.0.1","146.56.249.175","100.65.0.2","172.15.0.1","172.32.0.1","192.168.009.128","192.168.9.256","localhost","192.168.9.128:15560","::1"}){if(LanUdpContract.privateIpv4(ip))throw new AssertionError("public/nonliteral");checks++;}
        reject("peer_host","192.168.9.125");reject("protocol","TCP");reject("peer_port",15556);reject("bind_port",15960);
        reject("session","../session");reject("session_tag_hex","x");reject("key_b64",Base64.getEncoder().encodeToString(new byte[31]));
        for(Object value:new Object[]{"80",true,80.5,Double.NaN,Double.POSITIVE_INFINITY,Long.MAX_VALUE,-1,120})reject("buffer_ms",value);
        reject("seconds",121);reject("seconds",0);reject("fps",30);reject("display_hz",60);reject("surface_submit_lead_ms",8);reject("video_release","immediate");
        reject("audio_enabled","true");reject("touch_enabled",false);reject("async_video",false);reject("decoder_reanchor_enabled",false);
        Map<String,Object> map=valid();map.put("audio_enabled",false);map.put("fps",120);map.put("buffer_ms",100);LanUdpContract.validate(map,"192.168.9.128");checks++;
        System.out.println("LAN UDP contract checks: "+checks+" passed");
    }
}

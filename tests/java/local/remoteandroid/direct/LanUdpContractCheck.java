package local.remoteandroid.direct;
import java.util.*;
import java.io.IOException;
public final class LanUdpContractCheck {
    private static int checks;
    private static Map<String,Object> valid(){
        Map<String,Object> map=new HashMap<>();map.put("protocol","HGUE_UDP_V1");map.put("network_scope","lan");map.put("session","00000000000000000000000000000001");
        map.put("key_b64",Base64.getEncoder().encodeToString(new byte[32]));map.put("session_tag_hex","0000000000000001");map.put("peer_host","192.168.9.128");
        map.put("peer_port",45963);map.put("bind_port",0);map.put("seconds",120);map.put("fps",60);map.put("buffer_ms",80);map.put("display_hz",120);map.put("surface_submit_lead_ms",0);
        map.put("video_release","scheduled");for(String key:new String[]{"audio_enabled","touch_enabled","async_video","decoder_reanchor_enabled"})map.put(key,true);return map;
    }
    private static void reject(String key,Object value)throws Exception{Map<String,Object> map=valid();map.put(key,value);try{LanUdpContract.validate(map,"192.168.9.128");throw new AssertionError("accepted "+key);}catch(IOException good){checks++;}}
    private static void rejectLogin(String host,int port,String scope)throws Exception{
        try{LanUdpContract.validateLogin(host,port,scope);throw new AssertionError("accepted login scope");}catch(IOException good){checks++;}
    }
    private static void rejectScopedDescriptor(Map<String,Object> data,String host,String scope)throws Exception{
        try{LanUdpContract.validate(data,host,scope);throw new AssertionError("accepted scoped descriptor");}catch(IOException good){checks++;}
    }
    private static void rejectLeadDescriptor(Map<String,Object> data,int expected)throws Exception{
        try{LanUdpContract.validate(data,"192.168.9.128","lan",expected);throw new AssertionError("accepted unexpected lead");}catch(IOException good){checks++;}
    }
    private static Map<String,Object> readback(int lead){
        Map<String,Object> report=new HashMap<>();report.put("surface_submit_lead_ms",lead);report.put("surface_submit_status_code",lead==0?0:1);
        report.put("surface_submit_wait_count",lead==0?0:200);report.put("surface_submit_applications",lead==0?0:20);return report;
    }
    private static void rejectReadback(Map<String,Object> report,int expected)throws Exception{
        try{LanUdpContract.validateSurfaceSubmissionReadback(report,expected);throw new AssertionError("accepted unobserved execution");}catch(IOException good){checks++;}
    }
    public static void main(String[] args)throws Exception{
        LanUdpContract.validate(valid(),"192.168.9.128");checks++;
        for(String ip:new String[]{"10.0.0.1","172.16.0.1","172.31.255.254","192.168.9.128"}){if(!LanUdpContract.privateIpv4(ip))throw new AssertionError("private range");checks++;}
        for(String ip:new String[]{"127.0.0.1","0.0.0.0","224.0.0.1","146.56.249.175","100.65.0.2","172.15.0.1","172.32.0.1","192.168.009.128","192.168.9.256","localhost","192.168.9.128:45560","::1"}){if(LanUdpContract.privateIpv4(ip))throw new AssertionError("public/nonliteral");checks++;}
        reject("peer_host","192.168.9.125");reject("protocol","TCP");reject("peer_port",15556);reject("bind_port",15960);
        reject("session","../session");reject("session_tag_hex","x");reject("key_b64",Base64.getEncoder().encodeToString(new byte[31]));
        for(Object value:new Object[]{"80",true,80.5,Double.NaN,Double.POSITIVE_INFINITY,Long.MAX_VALUE,-1,120})reject("buffer_ms",value);
        reject("seconds",121);reject("seconds",0);reject("fps",30);reject("display_hz",60);reject("surface_submit_lead_ms",8);reject("video_release","immediate");
        reject("surface_submit_lead_ms",16);
        Map<String,Object> opted=valid();opted.put("surface_submit_lead_ms",16);LanUdpContract.validate(opted,"192.168.9.128","lan",16);checks++;
        rejectLeadDescriptor(opted,0);rejectLeadDescriptor(valid(),16);
        for(int expected:new int[]{-1,8,17,80})rejectLeadDescriptor(opted,expected);
        for(Object lead:new Object[]{true,"16",16.5,Double.NaN,Double.POSITIVE_INFINITY,Long.MAX_VALUE,8,17}){
            Map<String,Object> bad=valid();bad.put("surface_submit_lead_ms",lead);rejectLeadDescriptor(bad,16);
        }
        for(int lead:new int[]{0,16}){LanUdpContract.validateSurfaceSubmissionReadback(readback(lead),lead);checks++;}
        rejectReadback(readback(16),0);rejectReadback(readback(0),16);
        for(int invalid:new int[]{-1,8,17})rejectReadback(readback(16),invalid);
        for(String key:new String[]{"surface_submit_lead_ms","surface_submit_status_code","surface_submit_wait_count","surface_submit_applications"}){
            Map<String,Object> missingReadback=readback(16);missingReadback.remove(key);rejectReadback(missingReadback,16);
            for(Object invalid:new Object[]{true,"1",1.5,Double.NaN,Double.POSITIVE_INFINITY}){
                Map<String,Object> bad=readback(16);bad.put(key,invalid);rejectReadback(bad,16);
            }
        }
        for(int status:new int[]{-1,0,2,3}){Map<String,Object> bad=readback(16);bad.put("surface_submit_status_code",status);rejectReadback(bad,16);}
        for(String key:new String[]{"surface_submit_wait_count","surface_submit_applications"}){
            Map<String,Object> bad=readback(16);bad.put(key,0);rejectReadback(bad,16);
            bad=readback(0);bad.put(key,1);rejectReadback(bad,0);
        }
        reject("audio_enabled","true");reject("touch_enabled",false);reject("async_video",false);reject("decoder_reanchor_enabled",false);
        reject("network_scope","tailnet");reject("network_scope",null);reject("network_scope",true);reject("network_scope","LAN");
        Map<String,Object> missing=valid();missing.remove("network_scope");rejectScopedDescriptor(missing,"192.168.9.128","lan");
        for(String host:new String[]{"10.0.0.1","172.16.0.1","192.168.9.128"}){LanUdpContract.validateLogin(host,45560,"lan");checks++;}
        for(String host:new String[]{"100.65.0.2","100.65.0.3","100.65.0.11","100.64.0.1","146.56.249.175"})rejectLogin(host,45560,"lan");
        LanUdpContract.validateLogin("100.65.0.2",45560,"tailnet");checks++;
        for(String host:new String[]{"100.65.0.3","100.65.0.11","100.64.0.1","192.168.9.128","hs.yilufa.site","100.065.0.2","::1"})rejectLogin(host,45560,"tailnet");
        for(String scope:new String[]{"", "TAILNET", "udp", "lan ", null})rejectLogin("100.65.0.2",45560,scope);
        for(int port:new int[]{15556,15558,45963,0,-1,65536}){rejectLogin("100.65.0.2",port,"tailnet");rejectLogin("192.168.9.128",port,"lan");}
        Map<String,Object> tailnet=valid();tailnet.put("network_scope","tailnet");tailnet.put("peer_host","100.65.0.2");
        LanUdpContract.validate(tailnet,"100.65.0.2","tailnet");checks++;
        rejectScopedDescriptor(tailnet,"100.65.0.2","lan");rejectScopedDescriptor(valid(),"192.168.9.128","tailnet");
        tailnet.put("network_scope","lan");rejectScopedDescriptor(tailnet,"100.65.0.2","tailnet");tailnet.put("network_scope","tailnet");
        tailnet.put("peer_host","100.65.0.11");rejectScopedDescriptor(tailnet,"100.65.0.2","tailnet");tailnet.put("peer_host","100.65.0.2");
        tailnet.put("peer_port",15556);rejectScopedDescriptor(tailnet,"100.65.0.2","tailnet");
        Map<String,Object> map=valid();map.put("audio_enabled",false);map.put("fps",120);map.put("buffer_ms",100);LanUdpContract.validate(map,"192.168.9.128");checks++;
        System.out.println("LAN UDP contract checks: "+checks+" passed");
    }
}

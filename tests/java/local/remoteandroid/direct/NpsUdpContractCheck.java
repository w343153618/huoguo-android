package local.remoteandroid.direct;

import java.io.IOException;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

/** Real contract, synthetic descriptors only: no account, socket or device. */
public final class NpsUdpContractCheck {
    private static int checks;
    private interface Action {void run()throws Exception;}
    private static void rejects(Action action)throws Exception{
        try{action.run();throw new AssertionError("unsafe NPS contract accepted");}catch(IOException expected){checks++;}
    }
    private static Map<String,Object> descriptor(String node)throws Exception{
        Map<String,Object> data=new HashMap<>();
        data.put("node",node);data.put("network_scope",LanUdpContract.NPS_SCOPE);data.put("protocol","HGUE_UDP_V1");
        data.put("session","00000000000000000000000000000001");data.put("session_tag_hex","0000000000000001");
        data.put("key_b64",Base64.getEncoder().encodeToString(new byte[32]));
        data.put("peer_host",LanUdpContract.NPS_HOST);data.put("peer_port",LanUdpContract.npsUdpPort(node));
        data.put("bind_port",0);data.put("seconds",120);data.put("fps",60);data.put("buffer_ms",80);
        data.put("display_hz",120);data.put("surface_submit_lead_ms",0);data.put("video_release","scheduled");
        for(String name:new String[]{"audio_enabled","touch_enabled","async_video","decoder_reanchor_enabled"})data.put(name,true);
        return data;
    }
    private static void validate(Map<String,Object> data,String node)throws Exception{
        LanUdpContract.validate(data,LanUdpContract.NPS_HOST,LanUdpContract.npsHttpsPort(node),LanUdpContract.NPS_SCOPE,node,0);
    }
    public static void main(String[] args)throws Exception{
        for(String node:new String[]{"m1","m5"}){
            LanUdpContract.validateLogin(LanUdpContract.NPS_HOST,LanUdpContract.npsHttpsPort(node),LanUdpContract.NPS_SCOPE,node);checks++;
            LanUdpContract.validateAppMediaPeer(LanUdpContract.NPS_HOST,LanUdpContract.npsUdpPort(node),LanUdpContract.NPS_SCOPE,node);checks++;
            validate(descriptor(node),node);checks++;
            String certificate=LanUdpContract.npsCertificateSha256(node);
            if(!certificate.matches("[0-9a-f]{64}"))throw new AssertionError("certificate pin format");checks++;
            String other=node.equals("m1")?"m5":"m1";
            if(certificate.equals(LanUdpContract.npsCertificateSha256(other)))throw new AssertionError("nodes share pin");checks++;
            rejects(()->LanUdpContract.validateLogin(LanUdpContract.NPS_HOST,LanUdpContract.npsHttpsPort(other),LanUdpContract.NPS_SCOPE,node));
            rejects(()->LanUdpContract.validateAppMediaPeer(LanUdpContract.NPS_HOST,LanUdpContract.npsUdpPort(other),LanUdpContract.NPS_SCOPE,node));
            rejects(()->LanUdpContract.validate(descriptor(node),LanUdpContract.NPS_HOST,LanUdpContract.NPS_SCOPE));
            for(String host:new String[]{"146.56.249.174","nps.yilufa.site","localhost","127.0.0.1","0.0.0.0","192.168.9.99","100.65.0.11","146.056.249.175","::1"}){
                rejects(()->LanUdpContract.validateLogin(host,LanUdpContract.npsHttpsPort(node),LanUdpContract.NPS_SCOPE,node));
                rejects(()->LanUdpContract.validateAppMediaPeer(host,LanUdpContract.npsUdpPort(node),LanUdpContract.NPS_SCOPE,node));
            }
            for(String scope:new String[]{"lan","tailnet","owner_nps","public","NPS_OWNER",null}){
                rejects(()->LanUdpContract.validateLogin(LanUdpContract.NPS_HOST,LanUdpContract.npsHttpsPort(node),scope,node));
                rejects(()->LanUdpContract.validateAppMediaPeer(LanUdpContract.NPS_HOST,LanUdpContract.npsUdpPort(node),scope,node));
            }
            for(String field:new String[]{"node","network_scope","peer_host","peer_port","bind_port","protocol"}){
                Map<String,Object> data=descriptor(node);data.remove(field);rejects(()->validate(data,node));
            }
            Map<String,Object> wrongNode=descriptor(node);wrongNode.put("node",other);rejects(()->validate(wrongNode,node));
            Map<String,Object> wrongPort=descriptor(node);wrongPort.put("peer_port",LanUdpContract.npsUdpPort(other));rejects(()->validate(wrongPort,node));
            Map<String,Object> localBind=descriptor(node);localBind.put("bind_port",45965);rejects(()->validate(localBind,node));
            for(Object bad:new Object[]{true,"15558",15558.5,Double.NaN,Double.POSITIVE_INFINITY}){
                Map<String,Object> badData=descriptor(node);badData.put("peer_port",bad);rejects(()->validate(badData,node));
            }
            for(Object bad:new Object[]{true,1,"M5","m5 ",null}){
                Map<String,Object> badData=descriptor(node);badData.put("node",bad);rejects(()->validate(badData,node));
            }
            for(int port:new int[]{0,15556,15558,45560,45963,45965,65536})rejects(()->LanUdpContract.validateLogin(LanUdpContract.NPS_HOST,port,LanUdpContract.NPS_SCOPE,node));
            Map<String,Object> lead=descriptor(node);lead.put("surface_submit_lead_ms",16);
            LanUdpContract.validate(lead,LanUdpContract.NPS_HOST,LanUdpContract.npsHttpsPort(node),LanUdpContract.NPS_SCOPE,node,16);checks++;
            rejects(()->validate(lead,node));
        }
        for(String node:new String[]{"","M1","m2","m5 ",null}){
            rejects(()->LanUdpContract.npsHttpsPort(node));rejects(()->LanUdpContract.npsUdpPort(node));rejects(()->LanUdpContract.npsCertificateSha256(node));
        }
        LanUdpContract.validateLogin("192.168.9.128",45560,"lan");checks++;
        LanUdpContract.validateLogin("100.65.0.2",45560,"tailnet");checks++;
        rejects(()->LanUdpContract.validateLogin("100.65.0.2",45560,"tailnet","m5"));
        System.out.println("NPS owner UDP profile contract: "+checks+" passed");
    }
}

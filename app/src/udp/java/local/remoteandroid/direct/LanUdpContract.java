package local.remoteandroid.direct;

import java.io.IOException;
import java.util.Arrays;
import java.util.Base64;
import java.util.Map;

/** Pure fail-closed descriptor contract, independent of Android/UI/network. */
public final class LanUdpContract {
    private LanUdpContract(){}
    public static final String LAN_SCOPE="lan",TAILNET_SCOPE="tailnet",NPS_SCOPE="nps_owner";
    public static final String TAILNET_HOST="100.65.0.2";
    public static final int HTTPS_PORT=45560,UDP_PORT=45963;
    public static final String NPS_HOST="146.56.249.175",M1_NODE="m1",M5_NODE="m5";
    public static final int NPS_M1_HTTPS_PORT=49556,NPS_M5_HTTPS_PORT=49558,
        NPS_M1_UDP_PORT=15556,NPS_M5_UDP_PORT=15558;
    public static final String M1_CERT_SHA256="17bdaea4cb05b89cf8d64af02b8ed445bdb731f10923da7505d2aec91109c3c0",
        M5_CERT_SHA256="15d5e6eb9e8ad36c6e06f94f4db872619d6745e253759a5b46701bb48957139e";
    /** Fixed owner-trial routing only; a node is not authentication or friend acceptance. */
    public static int npsHttpsPort(String node)throws IOException{
        if(M1_NODE.equals(node))return NPS_M1_HTTPS_PORT;
        if(M5_NODE.equals(node))return NPS_M5_HTTPS_PORT;
        throw new IOException("unknown_nps_node");
    }
    public static int npsUdpPort(String node)throws IOException{
        if(M1_NODE.equals(node))return NPS_M1_UDP_PORT;
        if(M5_NODE.equals(node))return NPS_M5_UDP_PORT;
        throw new IOException("unknown_nps_node");
    }
    public static String npsCertificateSha256(String node)throws IOException{
        npsHttpsPort(node);return M1_NODE.equals(node)?M1_CERT_SHA256:M5_CERT_SHA256;
    }
    static boolean privateIpv4(String host){
        if(host==null||!host.matches("(?:0|[1-9][0-9]{0,2})(?:\\.(?:0|[1-9][0-9]{0,2})){3}"))return false;
        String[] values=host.split("\\.");int[] bytes=new int[4];
        for(int i=0;i<4;i++){bytes[i]=Integer.parseInt(values[i]);if(bytes[i]>255)return false;}
        return bytes[0]==10||bytes[0]==172&&bytes[1]>=16&&bytes[1]<=31||bytes[0]==192&&bytes[1]==168;
    }
    private static String string(Map<String,Object> data,String name)throws IOException{
        Object value=data.get(name);if(!(value instanceof String))throw new IOException("descriptor_string");return (String)value;
    }
    private static int integer(Map<String,Object> data,String name)throws IOException{
        Object value=data.get(name);if(!(value instanceof Number))throw new IOException("descriptor_integer");
        double number=((Number)value).doubleValue();
        if(Double.isNaN(number)||Double.isInfinite(number)||number!=Math.rint(number)||number<Integer.MIN_VALUE||number>Integer.MAX_VALUE)throw new IOException("descriptor_integer");
        return (int)number;
    }
    /** Tailnet is an explicit test scope, never a broad CGNAT/private allowance. */
    public static void validateLogin(String loginHost,int loginPort,String scope)throws IOException{
        validateLogin(loginHost,loginPort,scope,"");
    }
    public static void validateLogin(String loginHost,int loginPort,String scope,String node)throws IOException{
        if(NPS_SCOPE.equals(scope)){
            if(!NPS_HOST.equals(loginHost)||loginPort!=npsHttpsPort(node))throw new IOException("login_nps_profile");
            return;
        }
        if(node!=null&&!node.isEmpty())throw new IOException("unexpected_node_for_existing_scope");
        if(loginPort!=HTTPS_PORT||!(LAN_SCOPE.equals(scope)&&privateIpv4(loginHost)
            ||TAILNET_SCOPE.equals(scope)&&TAILNET_HOST.equals(loginHost)))throw new IOException("login_network_scope");
    }
    /** Used again by the App-mode parser; legacy standalone probes retain their own ports. */
    public static void validateAppMediaPeer(String host,int port,String scope,String node)throws IOException{
        if(NPS_SCOPE.equals(scope)){
            if(!NPS_HOST.equals(host)||port!=npsUdpPort(node))throw new IOException("session_options_invalid");
            return;
        }
        if(node!=null&&!node.isEmpty())throw new IOException("session_options_invalid");
        if(port!=UDP_PORT||!(LAN_SCOPE.equals(scope)&&privateIpv4(host)
            ||TAILNET_SCOPE.equals(scope)&&TAILNET_HOST.equals(host)))throw new IOException("session_options_invalid");
    }
    public static void validate(Map<String,Object> data,String loginHost)throws IOException{
        validate(data,loginHost,LAN_SCOPE);
    }
    public static void validate(Map<String,Object> data,String loginHost,String expectedScope)throws IOException{
        validate(data,loginHost,expectedScope,0);
    }
    /** Nonzero lead is accepted only when this isolated caller explicitly requested it. */
    public static void validate(Map<String,Object> data,String loginHost,String expectedScope,int expectedLeadMs)throws IOException{
        validate(data,loginHost,HTTPS_PORT,expectedScope,"",expectedLeadMs);
    }
    public static void validate(Map<String,Object> data,String loginHost,int loginPort,String expectedScope,String expectedNode,int expectedLeadMs)throws IOException{
        validateOwnerSurfaceLead(expectedLeadMs);
        validateLogin(loginHost,loginPort,expectedScope,expectedNode);
        if(NPS_SCOPE.equals(expectedScope)&&!string(data,"node").equals(expectedNode))throw new IOException("descriptor_node_binding");
        if(!string(data,"network_scope").equals(expectedScope)||!string(data,"protocol").equals("HGUE_UDP_V1")
            ||!string(data,"peer_host").equals(loginHost))throw new IOException("descriptor_peer_binding");
        if(!string(data,"session").matches("[0-9a-f]{32}")||!string(data,"session_tag_hex").matches("[0-9a-fA-F]{16}")||!string(data,"key_b64").matches("[A-Za-z0-9+/]{43}="))throw new IOException("descriptor_secret_encoding");
        byte[] key;try{key=Base64.getDecoder().decode(string(data,"key_b64"));}catch(IllegalArgumentException failure){throw new IOException("descriptor_secret_encoding");}
        try{if(key.length!=32)throw new IOException("descriptor_key_length");}finally{Arrays.fill(key,(byte)0);}
        int seconds=integer(data,"seconds"),fps=integer(data,"fps"),buffer=integer(data,"buffer_ms");
        validateAppMediaPeer(string(data,"peer_host"),integer(data,"peer_port"),expectedScope,expectedNode);
        if(integer(data,"bind_port")!=0||seconds<1||seconds>(NPS_SCOPE.equals(expectedScope)?3600:120)||fps!=30&&fps!=60&&fps!=120||buffer<30||buffer>100
            ||integer(data,"display_hz")!=(fps==30?0:120)||integer(data,"surface_submit_lead_ms")!=expectedLeadMs||!string(data,"video_release").equals("scheduled"))throw new IOException("descriptor_options");
        for(String name:new String[]{"audio_enabled","touch_enabled","async_video","decoder_reanchor_enabled"})if(!(data.get(name) instanceof Boolean))throw new IOException("descriptor_boolean");
        if(!((Boolean)data.get("touch_enabled"))||!((Boolean)data.get("async_video"))||!((Boolean)data.get("decoder_reanchor_enabled")))throw new IOException("descriptor_required_components");
    }
    public static void validateOwnerSurfaceLead(int leadMs)throws IOException{
        if(leadMs!=0&&leadMs!=16)throw new IOException("owner_surface_submit_lead_bound");
    }
    /** Execution evidence is separate from descriptor acceptance and requested targets. */
    public static void validateSurfaceSubmissionReadback(Map<String,Object> report,int expectedLeadMs)throws IOException{
        validateOwnerSurfaceLead(expectedLeadMs);
        if(integer(report,"surface_submit_lead_ms")!=expectedLeadMs)throw new IOException("surface_submit_readback_mismatch");
        int waits=integer(report,"surface_submit_wait_count"),applications=integer(report,"surface_submit_applications");
        int status=integer(report,"surface_submit_status_code");
        if(expectedLeadMs==0?(status!=0||waits!=0||applications!=0)
            :(status!=1||waits<1||applications<1))throw new IOException("surface_submit_execution_unobserved");
    }
}

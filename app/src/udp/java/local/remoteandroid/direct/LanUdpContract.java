package local.remoteandroid.direct;

import java.io.IOException;
import java.util.Arrays;
import java.util.Base64;
import java.util.Map;

/** Pure fail-closed descriptor contract, independent of Android/UI/network. */
public final class LanUdpContract {
    private LanUdpContract(){}
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
    public static void validate(Map<String,Object> data,String loginHost)throws IOException{
        if(!string(data,"protocol").equals("HGUE_UDP_V1")||!privateIpv4(loginHost)||!string(data,"peer_host").equals(loginHost))throw new IOException("descriptor_peer_binding");
        if(!string(data,"session").matches("[0-9a-f]{32}")||!string(data,"session_tag_hex").matches("[0-9a-fA-F]{16}")||!string(data,"key_b64").matches("[A-Za-z0-9+/]{43}="))throw new IOException("descriptor_secret_encoding");
        byte[] key;try{key=Base64.getDecoder().decode(string(data,"key_b64"));}catch(IllegalArgumentException failure){throw new IOException("descriptor_secret_encoding");}
        try{if(key.length!=32)throw new IOException("descriptor_key_length");}finally{Arrays.fill(key,(byte)0);}
        int seconds=integer(data,"seconds"),fps=integer(data,"fps"),buffer=integer(data,"buffer_ms");
        if(integer(data,"peer_port")!=15963||integer(data,"bind_port")!=0||seconds<1||seconds>120||fps!=60&&fps!=120||buffer<30||buffer>100
            ||integer(data,"display_hz")!=120||integer(data,"surface_submit_lead_ms")!=0||!string(data,"video_release").equals("scheduled"))throw new IOException("descriptor_options");
        for(String name:new String[]{"audio_enabled","touch_enabled","async_video","decoder_reanchor_enabled"})if(!(data.get(name) instanceof Boolean))throw new IOException("descriptor_boolean");
        if(!((Boolean)data.get("touch_enabled"))||!((Boolean)data.get("async_video"))||!((Boolean)data.get("decoder_reanchor_enabled")))throw new IOException("descriptor_required_components");
    }
}

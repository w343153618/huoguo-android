package local.remoteandroid.direct;

/** Parse the editable host field; the service port remains fixed at 15556. */
final class Endpoint {
    static String host(String input) {
        String value=input.trim();
        if(value.isEmpty()||value.contains("/")||value.matches(".*\\s.*"))
            throw new IllegalArgumentException("请填写 IP 或主机名，也可填写 IP:15556");
        if(value.startsWith("[")) {
            int end=value.indexOf(']');
            if(end<2)throw new IllegalArgumentException("IPv6 地址格式不正确");
            String suffix=value.substring(end+1);
            if(!suffix.isEmpty()&&!suffix.equals(":15556"))throw new IllegalArgumentException("连接端口固定为 15556");
            value=value.substring(1,end);
        } else if(value.indexOf(':')>0&&value.indexOf(':')==value.lastIndexOf(':')) {
            if(!value.substring(value.indexOf(':')+1).equals("15556"))throw new IllegalArgumentException("连接端口固定为 15556");
            value=value.substring(0,value.indexOf(':'));
        }
        if(!value.matches("[A-Za-z0-9._:%-]+"))throw new IllegalArgumentException("地址格式不正确");
        if(value.contains(":")) {
            try{new java.net.URI("tls://["+value+"]");}
            catch(java.net.URISyntaxException e){throw new IllegalArgumentException("IPv6 地址格式不正确");}
        }
        return value;
    }
}

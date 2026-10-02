package local.remoteandroid.direct;

/** Parse an editable host and optional port; omitted ports retain the original 15556 default. */
final class Endpoint {
    static final int DEFAULT_PORT = 15556;

    static final class Address {
        final String host;
        final int port;
        Address(String host, int port) { this.host = host; this.port = port; }
        String identity() { return (host.contains(":") ? "[" + host + "]" : host) + ":" + port; }
    }

    static Address parse(String input) {
        if (input == null) throw new IllegalArgumentException("请填写服务器地址");
        String value=input.trim();
        if(value.isEmpty()||value.contains("/")||value.matches(".*\\s.*"))
            throw new IllegalArgumentException("请填写 IP 或主机名，也可填写 IP:端口");
        int port = DEFAULT_PORT;
        if(value.startsWith("[")) {
            int end=value.indexOf(']');
            if(end<2)throw new IllegalArgumentException("IPv6 地址格式不正确");
            String suffix=value.substring(end+1);
            if(!suffix.isEmpty()) {
                if (!suffix.startsWith(":")) throw new IllegalArgumentException("IPv6 地址格式不正确");
                port = parsePort(suffix.substring(1));
            }
            value=value.substring(1,end);
            if (!value.contains(":")) throw new IllegalArgumentException("方括号内请填写 IPv6 地址");
        } else if(value.indexOf(':')>0&&value.indexOf(':')==value.lastIndexOf(':')) {
            port = parsePort(value.substring(value.indexOf(':')+1));
            value=value.substring(0,value.indexOf(':'));
        }
        if(!value.matches("[A-Za-z0-9._:%-]+"))throw new IllegalArgumentException("地址格式不正确");
        if(value.contains(":")) {
            try{new java.net.URI("tls://["+value+"]");}
            catch(java.net.URISyntaxException e){throw new IllegalArgumentException("IPv6 地址格式不正确");}
        }
        return new Address(value, port);
    }

    private static int parsePort(String value) {
        if (!value.matches("[0-9]{1,5}")) throw new IllegalArgumentException("端口需为 1–65535 的数字");
        int port = Integer.parseInt(value);
        if (port < 1 || port > 65535) throw new IllegalArgumentException("端口需为 1–65535 的数字");
        return port;
    }

    static String host(String input) { return parse(input).host; }
    static int port(String input) { return parse(input).port; }
    static String destination(String input) { return parse(input).identity(); }
    static String identity(String input) { return parse(input).identity(); }
    static boolean sameDestination(String first, String second) {
        try { return identity(first).equals(identity(second)); }
        catch (IllegalArgumentException invalid) { return false; }
    }
}

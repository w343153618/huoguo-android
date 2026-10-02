package local.remoteandroid.direct;
public class EndpointCheck {
    public static void main(String[] args) {
        check("146.56.249.175:15556", "146.56.249.175", 15556, "146.56.249.175:15556");
        check("146.56.249.175:15558", "146.56.249.175", 15558, "146.56.249.175:15558");
        check(" 192.168.9.99 ", "192.168.9.99", 15556, "192.168.9.99:15556");
        check("macbook-m5-128.local:8089", "macbook-m5-128.local", 8089, "macbook-m5-128.local:8089");
        check("example.com:1", "example.com", 1, "example.com:1");
        check("example.com:65535", "example.com", 65535, "example.com:65535");
        check("example.com:00080", "example.com", 80, "example.com:80");
        check("[::1]:15558", "::1", 15558, "[::1]:15558");
        check("::1", "::1", 15556, "[::1]:15556");
        check("[2001:db8::1234]:443", "2001:db8::1234", 443, "[2001:db8::1234]:443");
        check("2001:db8::1234", "2001:db8::1234", 15556, "[2001:db8::1234]:15556");
        check("[fe80::1%en0]:15556", "fe80::1%en0", 15556, "[fe80::1%en0]:15556");
        for (String bad : new String[]{"", "http://146.56.249.175", "192.168.9.99 bad", "a:b:c",
                "host:", "host:0", "host:65536", "host:9999999999", "host:-1", "host:+15556",
                "host:abc", "[::1]:0", "[::1]:65536", "[::1]:", "[::1]suffix", "[::1]:1:2",
                "[example.com]:15556", "[2001:db8::1234", "host@other", "host?query", "host#fragment"}) {
            try { Endpoint.parse(bad); throw new AssertionError("Accepted " + bad); }
            catch (IllegalArgumentException expected) { }
        }
        if (!Endpoint.sameDestination("146.56.249.175", "146.56.249.175:15556"))
            throw new AssertionError("legacy report default endpoint no longer matches");
        if (Endpoint.sameDestination("146.56.249.175:15556", "146.56.249.175:15558")
                || Endpoint.sameDestination("", "146.56.249.175"))
            throw new AssertionError("different report ports or invalid endpoints match");
        System.out.println("EndpointCheck PASS: host/port/identity, default-port compatibility, IPv6 and invalid bounds");
    }
    static void check(String input, String host, int port, String identity) {
        Endpoint.Address parsed = Endpoint.parse(input);
        if (!parsed.host.equals(host) || parsed.port != port || !parsed.identity().equals(identity)
                || !Endpoint.host(input).equals(host) || Endpoint.port(input) != port
                || !Endpoint.identity(input).equals(identity) || !Endpoint.destination(input).equals(identity))
            throw new AssertionError("Unexpected parsed endpoint: " + input);
    }
}

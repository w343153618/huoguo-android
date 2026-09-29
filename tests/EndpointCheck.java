package local.remoteandroid.direct;
public class EndpointCheck {
    public static void main(String[] args) {
        check("146.56.249.175:15556","146.56.249.175");
        check(" 192.168.9.99 ","192.168.9.99");
        check("macbook-m5-128.local:15556","macbook-m5-128.local");
        check("[::1]:15556","::1");check("::1","::1");
        for(String bad:new String[]{"","http://146.56.249.175","192.168.9.99 bad","146.56.249.175:5555","[::1]:5555","a:b:c"}) {
            try{Endpoint.host(bad);throw new AssertionError("Accepted "+bad);}catch(IllegalArgumentException expected){}
        }
        System.out.println("PASS: fixed-port input normalized; invalid addresses/ports rejected");
    }
    static void check(String input,String expected) {
        String actual=Endpoint.host(input);if(!actual.equals(expected))throw new AssertionError(input+" -> "+actual);
    }
}

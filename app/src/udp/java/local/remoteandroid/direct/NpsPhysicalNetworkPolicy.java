package local.remoteandroid.direct;

import java.io.IOException;
import java.util.HashSet;
import java.util.Set;

/** Pure selection of a connected underlying Wi-Fi/cellular network, never a VPN. */
public final class NpsPhysicalNetworkPolicy {
    private NpsPhysicalNetworkPolicy(){}
    public static final class Candidate {
        public final long handle;
        public final boolean active,connected,internet,notVpn,vpn,wifi,cellular,validated;
        public Candidate(long handle,boolean active,boolean connected,boolean internet,
                boolean notVpn,boolean vpn,boolean wifi,boolean cellular,boolean validated){
            if(handle<=0)throw new IllegalArgumentException("network_handle_required");
            this.handle=handle;this.active=active;this.connected=connected;this.internet=internet;
            this.notVpn=notVpn;this.vpn=vpn;this.wifi=wifi;this.cellular=cellular;this.validated=validated;
        }
        public boolean usable(){return connected&&internet&&notVpn&&!vpn&&(wifi||cellular);}
        private int rank(){return active?0:wifi?(validated?1:2):(validated?3:4);}
    }
    public static Candidate select(Candidate[] candidates)throws IOException{
        if(candidates==null)throw new IOException("physical_network_list_unavailable");
        Candidate selected=null;Set<Long> seen=new HashSet<>();
        for(Candidate candidate:candidates){
            if(candidate==null||!seen.add(candidate.handle))throw new IOException("physical_network_identity_ambiguous");
            if(!candidate.usable())continue;
            if(selected==null||candidate.rank()<selected.rank()
                    ||candidate.rank()==selected.rank()&&candidate.handle<selected.handle)selected=candidate;
        }
        if(selected==null)throw new IOException("public_nps_requires_connected_non_vpn_wifi_or_cellular");
        return selected;
    }
}

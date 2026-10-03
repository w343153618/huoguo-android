package local.remoteandroid.direct;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import java.io.IOException;
import java.net.DatagramSocket;
import java.net.Socket;
import java.util.ArrayList;
import java.util.concurrent.atomic.AtomicInteger;

/** One attempt, one non-VPN Network. Never binds the process or changes VPN state. */
public final class NpsPhysicalNetwork {
    private final ConnectivityManager manager;
    private final Network network;
    private final long handle;
    private final int transport;
    private final AtomicInteger httpsBindings=new AtomicInteger(),udpBindings=new AtomicInteger();
    private NpsPhysicalNetwork(ConnectivityManager manager,Network network,int transport){
        this.manager=manager;this.network=network;this.handle=network.getNetworkHandle();this.transport=transport;
    }
    private static NpsPhysicalNetworkPolicy.Candidate describe(ConnectivityManager manager,Network network,Network active){
        NetworkCapabilities capabilities=manager.getNetworkCapabilities(network);
        if(capabilities==null)return null;
        return new NpsPhysicalNetworkPolicy.Candidate(network.getNetworkHandle(),network.equals(active),
            manager.getLinkProperties(network)!=null,
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET),
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN),
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN),
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI),
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR),
            capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED));
    }
    public static NpsPhysicalNetwork select(Context context)throws IOException{
        ConnectivityManager manager=context.getSystemService(ConnectivityManager.class);
        if(manager==null)throw new IOException("physical_connectivity_manager_unavailable");
        Network active=manager.getActiveNetwork();Network[] networks=manager.getAllNetworks();
        if(networks==null)throw new IOException("physical_network_list_unavailable");
        ArrayList<NpsPhysicalNetworkPolicy.Candidate> candidates=new ArrayList<>();
        for(Network network:networks){if(network==null)throw new IOException("physical_network_identity_ambiguous");
            NpsPhysicalNetworkPolicy.Candidate candidate=describe(manager,network,active);if(candidate!=null)candidates.add(candidate);}
        NpsPhysicalNetworkPolicy.Candidate selected=NpsPhysicalNetworkPolicy.select(candidates.toArray(new NpsPhysicalNetworkPolicy.Candidate[0]));
        for(Network network:networks)if(network.getNetworkHandle()==selected.handle){
            NpsPhysicalNetwork lease=new NpsPhysicalNetwork(manager,network,selected.wifi?1:2);lease.requireUsable();return lease;
        }
        throw new IOException("selected_physical_network_disappeared");
    }
    public long handle(){return handle;}
    /** 1=Wi-Fi, 2=cellular. This is transport metadata, not a country/IP assertion. */
    public int transport(){return transport;}
    public int httpsBindings(){return httpsBindings.get();}
    public int udpBindings(){return udpBindings.get();}
    public void requireUsable()throws IOException{
        NpsPhysicalNetworkPolicy.Candidate current=describe(manager,network,manager.getActiveNetwork());
        if(current==null||current.handle!=handle||!current.usable())throw new IOException("selected_physical_network_lost");
    }
    public void bind(Socket socket)throws IOException{
        if(socket==null||socket.isClosed()||socket.isConnected())throw new IOException("unconnected_physical_https_socket_required");
        requireUsable();network.bindSocket(socket);requireUsable();httpsBindings.incrementAndGet();
    }
    public void bind(DatagramSocket socket)throws IOException{
        if(socket==null||socket.isClosed()||socket.isConnected())throw new IOException("unconnected_physical_udp_socket_required");
        requireUsable();network.bindSocket(socket);requireUsable();udpBindings.incrementAndGet();
    }
    public static void validateScope(String scope,NpsPhysicalNetwork lease)throws IOException{
        if(LanUdpContract.NPS_SCOPE.equals(scope)){
            if(lease==null)throw new IOException("public_nps_physical_network_required");
        }else{
            if(!LanUdpContract.LAN_SCOPE.equals(scope)&&!LanUdpContract.TAILNET_SCOPE.equals(scope))throw new IOException("unknown_app_network_scope");
            if(lease!=null)throw new IOException("physical_network_only_for_public_nps");
        }
    }
}

"""Serve only on IPv4 addresses of explicitly selected physical LAN NICs."""
import ipaddress,os,socket,subprocess,threading,time
from http.server import ThreadingHTTPServer

def serve(handler,tls=None):
    interfaces=os.environ['DIRECT_INTERFACES'].split(',')
    if interfaces!=['en11','en0']:raise RuntimeError('Unexpected M5 interface set')
    subnet=ipaddress.ip_network('192.168.8.0/23')
    port=int(os.environ['DIRECT_PORT'])
    servers={}
    try:
        while True:
            desired={'127.0.0.1'} if os.environ.get('DIRECT_LOOPBACK')=='1' else set()
            for interface in interfaces:
                result=subprocess.run(['/usr/sbin/ipconfig','getifaddr',interface],capture_output=True,text=True,timeout=5)
                address=result.stdout.strip()
                if result.returncode==0:
                    try:
                        parsed=ipaddress.ip_address(address)
                        if parsed.version==4 and parsed in subnet:desired.add(address)
                    except ValueError:pass
            for address in list(servers):
                if address not in desired:
                    server=servers.pop(address);server.shutdown();server.server_close()
                    print('Stopped LAN listener '+address+':'+str(port),flush=True)
            for address in sorted(desired-set(servers)):
                try:
                    server=ThreadingHTTPServer((address,port),handler);server.daemon_threads=True
                    server.socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    if tls:server.socket=tls.wrap_socket(server.socket,server_side=True)
                    servers[address]=server
                    threading.Thread(target=server.serve_forever,daemon=True).start()
                    print('LAN listener '+address+':'+str(port),flush=True)
                except OSError as e:print('LAN bind retry '+address+': '+str(e),flush=True)
            time.sleep(2)
    finally:
        for server in servers.values():server.shutdown();server.server_close()

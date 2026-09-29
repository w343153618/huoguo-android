#!/usr/bin/env python3
"""Authenticated TLS bridge to a loopback-only scrcpy service. No credential storage."""
import re, base64, hashlib, hmac, http.client, json, os, pathlib, secrets, socket, ssl, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from media_transfer import MediaStore, MediaError
BASE = pathlib.Path(__file__).resolve().parent
SDK = pathlib.Path.home() / 'Library/Android/sdk'
ADB = str(SDK / 'platform-tools/adb')
CREDS = pathlib.Path.home() / '.config/sunshine/credentials'
CERT, KEY = os.environ.get('DIRECT_CERT',str(CREDS/'cacert.pem')), os.environ.get('DIRECT_KEY',str(CREDS/'cakey.pem'))
AUTH_FILE = os.environ.get('DIRECT_AUTH_FILE')
SERIAL = os.environ.get('DIRECT_SERIAL','emulator-5554')
AVD = os.environ.get('DIRECT_AVD','RemoteAndroid17')
VIDEO_MAX_SIZE = int(os.environ.get('DIRECT_MAX_SIZE','960'))
EMULATOR = str(SDK / 'emulator/emulator')
RAMDISK = os.environ.get('DIRECT_RAMDISK',str(pathlib.Path.home() / 'Documents/ChatGPT/others/android-remote/boot/ramdisk-ksu.img'))
media = MediaStore(ADB, SERIAL, BASE / 'media-cache')
vm_proc = None
lock = threading.RLock()
sessions = {}
auth_slots=threading.BoundedSemaphore(2)
auth_failures=[]
auth_guard=threading.Lock()
def adb(*args, **kw):
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True, timeout=20, check=True, **kw)
def ensure_android():
    global vm_proc
    device_present=False
    try:
        device_present=adb('get-state').stdout.strip() == 'device'
        if device_present and adb('shell','getprop','sys.boot_completed').stdout.strip() == '1': return
    except Exception: pass
    if not os.environ.get('DIRECT_EXTERNAL_VM') and not device_present and (vm_proc is None or vm_proc.poll() is not None):
        log=open(BASE/'emulator.log','ab',buffering=0)
        args=[EMULATOR,'@'+AVD,'-port','5554','-gpu','host','-no-window','-no-snapshot','-no-boot-anim','-no-metrics']
        if os.environ.get('DIRECT_RAMDISK') or AVD=='RemoteAndroid17': args+=['-ramdisk',RAMDISK]
        vm_proc=subprocess.Popen(args,stdout=log,stderr=log)
        log.close()
    for attempt in range(120):
        if vm_proc is not None and vm_proc.poll() is not None: raise RuntimeError('Android VM exited during startup')
        try:
            if adb('get-state').stdout.strip() == 'device' and adb('shell','getprop','sys.boot_completed').stdout.strip() == '1': return
        except Exception: pass
        time.sleep(1)
    raise TimeoutError('Android VM boot timed out')

def close_session(sid):
    with lock:
        s = sessions.pop(sid, None)
    if not s: return
    for sock in s['sockets']:
        try: sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: sock.close()
        except OSError: pass
    try: adb('forward', '--remove', 'tcp:'+str(s['port']))
    except Exception: pass
    if s['proc'].poll() is None: s['proc'].terminate()
def authorized(header):
    if not header or not header.startswith('Basic ') or len(header)>2048: return False
    try:
        decoded=base64.b64decode(header[6:], validate=True).decode('utf-8')
        if ':' not in decoded: return False
        if AUTH_FILE:
            username,password=decoded.split(':',1)
            config=json.loads(pathlib.Path(AUTH_FILE).read_text())
            if 'users' in config:
                record=config['users'].get(username)
                if not record:return False
            else:record=config
            digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(record['salt']),n=16384,r=8,p=1).hex()
            return hmac.compare_digest(username,record.get('username',username)) and hmac.compare_digest(digest,record['digest'])
        ctx=ssl.create_default_context(cafile=CERT); ctx.check_hostname=False
        conn=http.client.HTTPSConnection('127.0.0.1',47990,context=ctx,timeout=5)
        conn.request('GET','/api/config',headers={'Authorization':header})
        resp=conn.getresponse(); result=resp.status==200; resp.read(); conn.close()
        return result
    except Exception: return False
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def reply(self,status,data):
        payload=json.dumps(data).encode()
        self.send_response(status); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(payload))); self.send_header('Connection','close'); self.end_headers()
        self.wfile.write(payload); self.close_connection=True
    def auth(self):
        # Bound expensive password hashing and failed attempts on the public gateway.
        if AUTH_FILE:
            with auth_guard:
                now=time.monotonic()
                auth_failures[:]=[t for t in auth_failures if now-t<60]
                limited=len(auth_failures)>=20
            if limited or not auth_slots.acquire(blocking=False):
                self.reply(429,{'error':'Too many login attempts; retry later'}); return False
            try: valid=authorized(self.headers.get('Authorization'))
            finally: auth_slots.release()
            if valid: return True
            with auth_guard: auth_failures.append(time.monotonic())
        elif authorized(self.headers.get('Authorization')): return True
        self.reply(401,{'error':'Login rejected'}); return False
    def do_GET(self):
        if self.path == '/files' or self.path.startswith('/files/'):
            if not self.auth(): return
            try:
                if self.path == '/files': self.reply(200, media.listing())
                else: media.download(self, self.path[len('/files/'):])
            except MediaError as e: self.reply(e.status, {'error': e.message})
            except (subprocess.SubprocessError, ValueError): self.reply(503, {'error': 'Android media unavailable; retry later'})
            except OSError: self.close_connection = True
            return
        # Public signed client updates only; no runtime credentials or arbitrary files.
        prefix='/updates/'
        name=self.path[len(prefix):] if self.path.startswith(prefix) else ''
        if name!='update.json' and not re.fullmatch(r'HuoguoAndroid-v[0-9]+(?:\.[0-9]+){1,3}\.apk',name):
            self.reply(404,{'error':'Unknown route'});return
        directory=pathlib.Path(os.environ.get('DIRECT_UPDATE_DIR',str(BASE/'updates')))
        try:
            with (directory/name).open('rb') as source:
                size=os.fstat(source.fileno()).st_size
                if size>64*1024*1024:raise OSError('Oversized update')
                self.connection.settimeout(30)
                self.send_response(200)
                self.send_header('Content-Type','application/json' if name=='update.json' else 'application/vnd.android.package-archive')
                self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-cache')
                self.send_header('Connection','close');self.end_headers()
                while chunk:=source.read(65536):self.wfile.write(chunk)
                self.close_connection=True
        except FileNotFoundError:self.reply(404,{'error':'Update not available'})
        except OSError:self.close_connection=True
    def do_POST(self):
        if not self.auth(): return
        if self.path == '/files':
            try: self.reply(201, media.upload(self))
            except MediaError as e:
                # Drain small rejected bodies so TLS closing does not discard the error reply.
                try:
                    remaining = int(self.headers.get('Content-Length', '0')) - getattr(self, '_media_body_read', 0)
                    if 0 < remaining <= 4096:
                        self.connection.settimeout(2); self.rfile.read(remaining)
                except (ValueError, OSError): pass
                self.reply(e.status, {'error': e.message})
            except (subprocess.SubprocessError, ValueError): self.reply(503, {'error': 'Android media import failed; retry later'})
            except OSError: self.close_connection = True
            return
        if self.path!='/session': self.reply(404,{'error':'Unknown route'}); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=512:raise ValueError('Invalid settings length')
            self.connection.settimeout(10)
            body=self.rfile.read(length) if length else b''
            if len(body)!=length:raise ValueError('Incomplete settings')
            settings=json.loads(body) if body else {}
            if not isinstance(settings,dict):raise ValueError('Invalid settings object')
            max_size=settings.get('max_size',VIDEO_MAX_SIZE)
            if type(max_size) is not int or max_size not in (960,1200,1600,2400):raise ValueError('Invalid resolution')
        except (ValueError,OSError):
            self.reply(400,{'error':'Invalid resolution settings'}); return
        port=None
        proc=None
        try:
            with lock:
                for old in list(sessions): close_session(old)
                ensure_android()
                adb('push',str(BASE/'scrcpy-server-v4.1'),'/data/local/tmp/remoteandroid-scrcpy.jar')
                scid=secrets.randbelow(0x7fffffff)
                port=int(adb('forward','tcp:0','localabstract:scrcpy_'+format(scid,'08x')).stdout.strip())
                cmd='CLASSPATH=/data/local/tmp/remoteandroid-scrcpy.jar app_process / com.genymobile.scrcpy.Server 4.1 '+ ' '.join([
                    'scid='+format(scid,'x'),'tunnel_forward=true','send_device_meta=false','send_dummy_byte=false',
                    'video_codec=h264','audio_codec=aac','video_bit_rate=2500000','max_fps=60','max_size='+str(max_size),
                    'control=true','cleanup=true'])
                log=open(BASE/'server.log','ab',buffering=0)
                proc=subprocess.Popen([ADB,'-s',SERIAL,'shell',cmd],stdout=log,stderr=log)
                log.close()
                for attempt in range(30):
                    if ('scrcpy_'+format(scid,'08x')) in adb('shell','cat','/proc/net/unix').stdout: break
                    if proc.poll() is not None: raise RuntimeError('scrcpy exited during startup')
                    time.sleep(.1)
                else: raise TimeoutError('scrcpy socket startup')
                sid=secrets.token_hex(16)
                sessions[sid]={'port':port,'proc':proc,'sockets':[],'roles':[],'created':time.monotonic()}
                threading.Timer(30,lambda: self.expire(sid)).start()
            self.reply(200,{'session':sid,'codec':'h264','max_size':max_size,'max_fps':60})
        except Exception as e:
            if proc and proc.poll() is None: proc.terminate()
            if port:
                try: adb('forward','--remove','tcp:'+str(port))
                except Exception: pass
            self.reply(503,{'error':type(e).__name__})
    @staticmethod
    def expire(sid):
        with lock:
            s=sessions.get(sid)
            if s and len(s['roles'])<3: close_session(sid)
    def do_CONNECT(self):
        if not self.auth(): return
        parts=self.path.split('/')
        if len(parts)!=4 or parts[1]!='stream': self.reply(404,{'error':'Unknown route'}); return
        sid,role=parts[2:]
        with lock:
            s=sessions.get(sid)
            if not s: self.reply(404,{'error':'Session expired'}); return
            if len(s['roles'])>=3 or role!=['video','audio','control'][len(s['roles'])]:
                self.reply(409,{'error':'Invalid channel order'}); return
            upstream=None
            for retry in range(50):
                try:
                    upstream=socket.create_connection(('127.0.0.1',s['port']),timeout=1); break
                except OSError: time.sleep(.1)
            if not upstream: self.reply(503,{'error':'Android service unavailable'}); return
            upstream.settimeout(None); upstream.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            s['roles'].append(role); s['sockets'].extend([upstream,self.connection])
        self.send_response(200,'Connection Established'); self.end_headers(); self.wfile.flush()
        self.connection.settimeout(None); self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        def pump(src,dst):
            try:
                while True:
                    data=src.recv(65536)
                    if not data: break
                    dst.sendall(data)
            except OSError: pass
            finally: close_session(sid)
        threading.Thread(target=pump,args=(self.connection,upstream),daemon=True).start()
        pump(upstream,self.connection); self.close_connection=True
if __name__=='__main__':
    host=os.environ.get('DIRECT_HOST','192.168.9.125'); port=int(os.environ.get('DIRECT_PORT','15556'))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT,KEY)
    try:
        if os.environ.get('DIRECT_INTERFACES'):
            from lan_interfaces import serve
            os.environ['DIRECT_PORT']=str(port)
            serve(Handler,context)
        else:
            server=ThreadingHTTPServer((host,port),Handler); server.daemon_threads=True
            server.socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            server.socket=context.wrap_socket(server.socket,server_side=True)
            print('Android Direct TLS gateway listening on '+host+':'+str(port),flush=True)
            server.serve_forever()
    finally:
        for sid in list(sessions): close_session(sid)

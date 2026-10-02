#!/usr/bin/env python3
"""Authenticated TLS bridge to a loopback-only scrcpy service. No credential storage."""
import re, base64, hashlib, hmac, http.client, json, os, pathlib, secrets, socket, ssl, stat, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from media_transfer import MediaStore, MediaError
from idle_power import IdleScreen
from display_profile import apply_540_profile
from performance_profile import apply_performance_profile
from stream_settings import parse_settings, parse_bitrate_mode, parse_max_fps
from hardware_stream import HostHardwareSession
from diagnostics_reports import DiagnosticSource, DiagnosticsError, ReportStore, read_json
BASE = pathlib.Path(os.environ.get('DIRECT_STATE_DIR', str(pathlib.Path(__file__).resolve().parent)))
SDK = pathlib.Path.home() / 'Library/Android/sdk'
ADB = str(SDK / 'platform-tools/adb')
CREDS = pathlib.Path.home() / '.config/sunshine/credentials'
CERT, KEY = os.environ.get('DIRECT_CERT',str(CREDS/'cacert.pem')), os.environ.get('DIRECT_KEY',str(CREDS/'cakey.pem'))
AUTH_FILE = os.environ.get('DIRECT_AUTH_FILE')
SERIAL = os.environ.get('DIRECT_SERIAL','emulator-5556')
AVD = os.environ.get('DIRECT_AVD','RemoteAndroid17Compare')
VIDEO_MAX_SIZE = int(os.environ.get('DIRECT_MAX_SIZE','1200'))
VIDEO_BACKEND = os.environ.get('DIRECT_VIDEO_BACKEND', 'guest')
if VIDEO_BACKEND not in ('guest', 'videotoolbox'):
    raise ValueError('Invalid video backend')
EMULATOR = str(SDK / 'emulator/emulator')
RAMDISK = os.environ.get('DIRECT_RAMDISK',str(pathlib.Path.home() / 'Documents/ChatGPT/others/android-remote/boot/ramdisk-ksu.img'))
media = MediaStore(ADB, SERIAL, BASE / 'media-cache')
diagnostics = ReportStore(os.environ.get('DIRECT_DIAGNOSTICS_DIR', str(BASE / 'diagnostics-reports')))
vm_proc = None
lock = threading.RLock()
sessions = {}
auth_slots=threading.BoundedSemaphore(2)
auth_failures=[]
auth_guard=threading.Lock()
def adb(*args, **kw):
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True, timeout=20, check=True, **kw)
def sleep_guest():
    try: adb('shell','input','keyevent','127')
    except Exception: pass
    adb('shell','input','keyevent','223')
idle_screen = IdleScreen(lock, lambda: bool(sessions), sleep_guest, delay=int(os.environ.get('DIRECT_IDLE_DELAY','300')))
def ensure_android():
    global vm_proc
    device_present=False
    boot_ready=False
    try:
        device_present=adb('get-state').stdout.strip() == 'device'
        boot_ready=device_present and adb('shell','getprop','sys.boot_completed').stdout.strip() == '1'
    except Exception: pass
    if boot_ready:
        apply_performance_profile(adb)
        apply_540_profile(adb)
        return
    if not os.environ.get('DIRECT_EXTERNAL_VM') and not device_present and (vm_proc is None or vm_proc.poll() is not None):
        log=open(BASE/'emulator.log','ab',buffering=0)
        args=[EMULATOR,'@'+AVD,'-port','5554','-gpu','host','-no-window','-no-snapshot','-no-boot-anim','-no-metrics']
        if os.environ.get('DIRECT_RAMDISK') or AVD=='RemoteAndroid17': args+=['-ramdisk',RAMDISK]
        vm_proc=subprocess.Popen(args,stdout=log,stderr=log)
        log.close()
    for attempt in range(120):
        if vm_proc is not None and vm_proc.poll() is not None: raise RuntimeError('Android VM exited during startup')
        boot_ready=False
        try:
            boot_ready=adb('get-state').stdout.strip() == 'device' and adb('shell','getprop','sys.boot_completed').stdout.strip() == '1'
        except Exception: pass
        if boot_ready:
            apply_performance_profile(adb)
            apply_540_profile(adb)
            return
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
    if s.get('hardware'):
        s['hardware'].close()
    else:
        try: adb('forward', '--remove', 'tcp:'+str(s['port']))
        except Exception: pass
        if s['proc'].poll() is None: s['proc'].terminate()
    idle_screen.schedule()
def authenticated_account(header):
    if not header or not header.startswith('Basic ') or len(header)>2048: return None
    try:
        decoded=base64.b64decode(header[6:], validate=True).decode('utf-8')
        if ':' not in decoded: return None
        username,password=decoded.split(':',1)
        if AUTH_FILE:
            config=json.loads(pathlib.Path(AUTH_FILE).read_text())
            if 'users' in config:
                record=config['users'].get(username)
                if not record:return None
            else:record=config
            digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(record['salt']),n=16384,r=8,p=1).hex()
            return username if hmac.compare_digest(username,record.get('username',username)) and hmac.compare_digest(digest,record['digest']) else None
        ctx=ssl.create_default_context(cafile=CERT); ctx.check_hostname=False
        conn=http.client.HTTPSConnection('127.0.0.1',47990,context=ctx,timeout=5)
        conn.request('GET','/api/config',headers={'Authorization':header})
        resp=conn.getresponse(); result=resp.status==200; resp.read(); conn.close()
        return username if result else None
    except Exception: return None
def authorized(header):
    return authenticated_account(header) is not None

diagnostic_source = DiagnosticSource(adb, ensure_android)
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def reply(self,status,data):
        payload=json.dumps(data).encode()
        self.send_response(status); self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(payload))); self.send_header('Connection','close'); self.end_headers()
        self.wfile.write(payload); self.close_connection=True
    def auth(self):
        # Bound expensive password hashing and failed attempts on the public gateway.
        self.account = None
        if AUTH_FILE:
            with auth_guard:
                now=time.monotonic()
                auth_failures[:]=[t for t in auth_failures if now-t<60]
                limited=len(auth_failures)>=20
            if limited or not auth_slots.acquire(blocking=False):
                self.reply(429,{'error':'Too many login attempts; retry later'}); return False
            try: account=authenticated_account(self.headers.get('Authorization'))
            finally: auth_slots.release()
            if account is not None: self.account=account; return True
            with auth_guard: auth_failures.append(time.monotonic())
        else:
            account=authenticated_account(self.headers.get('Authorization'))
            if account is not None: self.account=account; return True
        self.reply(401,{'error':'Login rejected'}); return False
    def do_GET(self):
        if self.path == "/ping":
            self.reply(200, {"ok": True, "node": os.environ.get('DIRECT_NODE_NAME', 'unconfigured'),
                             "serial": SERIAL, "video_backend": VIDEO_BACKEND}); return
        if self.path.startswith('/experimental/'):
            self.experimental_asset(); return
        if self.path == '/diagnostics/reports' or self.path.startswith('/diagnostics/reports/'):
            if not self.auth(): return
            try:
                result = diagnostics.listing(self.account) if self.path == '/diagnostics/reports' else diagnostics.get(self.account, self.path[len('/diagnostics/reports/'):])
                self.reply(200, result)
            except DiagnosticsError as e: self.reply(e.status, {'error': e.message})
            except OSError: self.reply(503, {'error': 'Diagnostic storage unavailable; retry later'})
            return
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
        source=None;fd=None;headers_sent=False
        try:
            # A published name must never dereference a host-file symlink or
            # block the request on a FIFO/device masquerading as an update.
            fd=os.open(directory/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            source=os.fdopen(fd,'rb');fd=None
            info=os.fstat(source.fileno())
            limit=65536 if name=='update.json' else 64*1024*1024
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
                self.reply(503,{'error':'Update unavailable'});return
            self.connection.settimeout(30)
            self.send_response(200)
            self.send_header('Content-Type','application/json' if name=='update.json' else 'application/vnd.android.package-archive')
            self.send_header('Content-Length',str(info.st_size));self.send_header('Cache-Control','no-cache')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Connection','close');self.end_headers();headers_sent=True
            remaining=info.st_size
            while remaining:
                chunk=source.read(min(65536,remaining))
                if not chunk:break
                self.wfile.write(chunk);remaining-=len(chunk)
            self.close_connection=True
        except FileNotFoundError:self.reply(404,{'error':'Update not available'})
        except OSError:
            if not headers_sent:self.reply(503,{'error':'Update unavailable'})
            else:self.close_connection=True
        finally:
            if source is not None:source.close()
            if fd is not None:os.close(fd)
    def experimental_asset(self):
        # Isolated signed installation assets only; never credentials, arbitrary
        # files or a mutation of the established formal /updates/ channel.
        name=self.path[len('/experimental/'):]
        if name!='experiment.json' and not re.fullmatch(
                r'HuoguoAndroidExperiment-v[0-9]+(?:\.[0-9]+){1,2}-alpha\.[1-9][0-9]*\.apk',name):
            self.reply(404,{'error':'Unknown experimental asset'});return
        directory=pathlib.Path(os.environ.get('DIRECT_EXPERIMENTAL_DIR',str(BASE/'experimental')))
        source=None;fd=None;headers_sent=False
        try:
            fd=os.open(directory/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            source=os.fdopen(fd,'rb');fd=None
            info=os.fstat(source.fileno())
            limit=65536 if name=='experiment.json' else 64*1024*1024
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
                self.reply(503,{'error':'Experimental asset unavailable'});return
            self.connection.settimeout(30)
            self.send_response(200)
            self.send_header('Content-Type','application/json' if name=='experiment.json' else 'application/vnd.android.package-archive')
            self.send_header('Content-Length',str(info.st_size));self.send_header('Cache-Control','no-cache')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Connection','close');self.end_headers();headers_sent=True
            remaining=info.st_size
            while remaining:
                chunk=source.read(min(65536,remaining))
                if not chunk:break
                self.wfile.write(chunk);remaining-=len(chunk)
            self.close_connection=True
        except FileNotFoundError:self.reply(404,{'error':'Experimental asset not available'})
        except OSError:
            if not headers_sent:self.reply(503,{'error':'Experimental asset unavailable'})
            else:self.close_connection=True
        finally:
            if source is not None:source.close()
            if fd is not None:os.close(fd)
    def do_POST(self):
        if not self.auth(): return
        if self.path in ('/diagnostics/reports', '/diagnostics/source'):
            try:
                request = read_json(self, 512) if self.path == '/diagnostics/source' else read_json(self)
                if self.path == '/diagnostics/source': self.reply(200, diagnostic_source.control(self.account, request))
                else: self.reply(201, diagnostics.save(self.account, request))
            except DiagnosticsError as e: self.reply(e.status, {'error': e.message})
            except OSError: self.reply(503, {'error': 'Diagnostic storage unavailable; retry later'})
            return
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
            max_size, bit_rate = parse_settings(settings, VIDEO_MAX_SIZE)
            bitrate_mode, mode_value = parse_bitrate_mode(settings)
            max_fps = parse_max_fps(settings)
            if bit_rate > 12_000_000 and VIDEO_BACKEND != 'videotoolbox':
                self.reply(400, {'error': 'Bitrates above 12 Mbps require Mac hardware encoding'}); return
            if max_fps == 120 and VIDEO_BACKEND != 'videotoolbox':
                self.reply(400, {'error': '120 FPS requires the Mac hardware video backend'}); return
        except (ValueError,OSError):
            self.reply(400,{'error':'Invalid resolution or bitrate settings'}); return
        port=None
        proc=None
        hardware=None
        try:
            with lock:
                # Knowing another user's random SID must not grant its channels
                # or permit a login to displace an active, differently owned run.
                if any(s.get('account') != self.account for s in sessions.values()):
                    self.reply(409,{'error':'Android session is in use'});return
                for old in list(sessions): close_session(old)
                idle_screen.cancel()
                ensure_android()
                adb('shell','input','keyevent','224')
                if VIDEO_BACKEND == 'videotoolbox':
                    hardware=HostHardwareSession(BASE,SERIAL,AVD,max_size,bit_rate,max_fps,bitrate_mode)
                    proc=hardware.proc
                else:
                    port,proc=self.start_guest_video(max_size,bit_rate,max_fps,bitrate_mode,mode_value)
                sid=secrets.token_hex(16)
                sessions[sid]={'account':self.account,'port':port,'proc':proc,'hardware':hardware,'sockets':[],'roles':[],'created':time.monotonic()}
                threading.Timer(30,lambda: self.expire(sid)).start()
            self.reply(200,{'session':sid,'codec':'h264','max_size':max_size,'max_fps':max_fps,'video_bit_rate':bit_rate,'bitrate_mode':bitrate_mode,'adaptive_vbr':bitrate_mode=='ADAPTIVE_VBR','video_backend':VIDEO_BACKEND,'hardware_required':VIDEO_BACKEND=='videotoolbox'})
        except Exception as e:
            if hardware: hardware.close()
            elif proc and proc.poll() is None: proc.terminate()
            if port:
                try: adb('forward','--remove','tcp:'+str(port))
                except Exception: pass
            idle_screen.schedule()
            self.reply(503,{'error':type(e).__name__})
    @staticmethod
    def start_guest_video(max_size,bit_rate,max_fps,bitrate_mode,mode_value):
        port=proc=None
        try:
            server_file=BASE/('scrcpy-server-adaptive-v4.1' if bitrate_mode=='ADAPTIVE_VBR' else 'scrcpy-server-v4.1')
            if not server_file.is_file(): raise RuntimeError('Adaptive server unavailable')
            adb('push',str(server_file),'/data/local/tmp/remoteandroid-scrcpy.jar')
            scid=secrets.randbelow(0x7fffffff)
            port=int(adb('forward','tcp:0','localabstract:scrcpy_'+format(scid,'08x')).stdout.strip())
            cmd='CLASSPATH=/data/local/tmp/remoteandroid-scrcpy.jar app_process / com.genymobile.scrcpy.Server 4.1 '+ ' '.join([
                'scid='+format(scid,'x'),'tunnel_forward=true','send_device_meta=false','send_dummy_byte=false',
                'video_codec=h264','audio_codec=aac','video_bit_rate='+str(bit_rate),'max_fps='+str(max_fps),'max_size='+str(max_size),
                'video_codec_options=bitrate-mode='+str(mode_value),'control=true','cleanup=true'])
            with open(BASE/'server.log','ab',buffering=0) as log:
                proc=subprocess.Popen([ADB,'-s',SERIAL,'shell',cmd],stdout=log,stderr=log)
            for attempt in range(30):
                if ('scrcpy_'+format(scid,'08x')) in adb('shell','cat','/proc/net/unix').stdout: break
                if proc.poll() is not None: raise RuntimeError('scrcpy exited during startup')
                time.sleep(.1)
            else: raise TimeoutError('scrcpy socket startup')
            return port,proc
        except Exception:
            if proc and proc.poll() is None: proc.terminate()
            if port:
                try: adb('forward','--remove','tcp:'+str(port))
                except Exception: pass
            raise
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
            if not s or s.get('account') != self.account:
                self.reply(404,{'error':'Session expired'}); return
            if len(s['roles'])>=3 or role!=['video','audio','control'][len(s['roles'])]:
                self.reply(409,{'error':'Invalid channel order'}); return
            upstream=None
            if s.get('hardware'):
                try: upstream=s['hardware'].channel(role)
                except RuntimeError: pass
            else:
                for retry in range(50):
                    try:
                        upstream=socket.create_connection(('127.0.0.1',s['port']),timeout=1); break
                    except OSError: time.sleep(.1)
            if not upstream: self.reply(503,{'error':'Android service unavailable'}); return
            upstream.settimeout(None)
            if not s.get('hardware'): upstream.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            s['roles'].append(role); s['sockets'].extend([upstream,self.connection])
            if s.get('hardware') and len(s['roles']) == 3:
                try: s['hardware'].start()
                except OSError:
                    close_session(sid); self.reply(503,{'error':'Hardware worker stopped'}); return
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
    def do_DELETE(self):
        if not self.auth():return
        match=re.fullmatch(r'/session/([0-9a-f]{32})',self.path)
        if not match:self.reply(404,{'error':'Unknown route'});return
        sid=match[1]
        with lock:
            s=sessions.get(sid)
            if not s or s.get('account') != self.account:
                self.reply(404,{'error':'Session expired'});return
            close_session(sid)
        self.reply(200,{'closed':True})
if __name__=='__main__':
    if os.environ.get('DIRECT_PHYSICAL_DISPLAY') in ('540x1200', '720x1280', '1080x1920'):
        def prepare_display():
            try: ensure_android()
            except Exception as error: print('Physical display preparation: '+type(error).__name__,flush=True)
        threading.Thread(target=prepare_display,daemon=True).start()
    idle_screen.schedule()
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
        idle_screen.shutdown()
        for sid in list(sessions): close_session(sid)
        diagnostic_source.shutdown()

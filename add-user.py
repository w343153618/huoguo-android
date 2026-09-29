"""Add/update one scrypt account; passwords are entered privately, never CLI args."""
import argparse,getpass,hashlib,json,os,pathlib,re,secrets,shutil,time
parser=argparse.ArgumentParser();parser.add_argument('username');parser.add_argument('--auth-file',type=pathlib.Path,default=pathlib.Path.home()/'Library/Application Support/AndroidRemote/direct/auth.json');args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',args.username):raise SystemExit('Invalid username')
password=getpass.getpass('新账号密码（隐藏输入）：')
if not password:raise SystemExit('Empty password rejected')
path=args.auth_file;config=json.loads(path.read_text()) if path.exists() else {'users':{}}
if 'users' not in config:config={'users':{config['username']:config}}
salt=secrets.token_bytes(32);record={'username':args.username,'salt':salt.hex(),'digest':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()};password=None
config['users'][args.username]=record
if path.exists():
    backup=path.with_name(path.name+'.before-user-'+str(int(time.time())));shutil.copy2(path,backup);backup.chmod(0o600)
temp=path.with_name(path.name+'.new');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump(config,f)
temp.chmod(0o600);os.replace(temp,path)
print('Account saved: '+args.username+'; existing accounts preserved; password stored only as scrypt hash')

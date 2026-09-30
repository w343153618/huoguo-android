import gzip,sys,collections,json,pathlib

def varint(b,p=0):
 n=0;s=0
 while True:
  x=b[p];p+=1;n|=(x&127)<<s
  if x<128:return n,p
  s+=7

def fields(b):
 d=collections.defaultdict(list);p=0
 while p<len(b):
  key,p=varint(b,p);f,w=key>>3,key&7
  if w==0:v,p=varint(b,p)
  elif w==2:n,p=varint(b,p);v=b[p:p+n];p+=n
  elif w==1:v=b[p:p+8];p+=8
  elif w==5:v=b[p:p+4];p+=4
  else:raise ValueError(w)
  d[f].append(v)
 return d

def packed(b):
 if isinstance(b,int):return [b]
 p=0;v=[]
 while p<len(b):n,p=varint(b,p);v.append(n)
 return v
p=fields(gzip.decompress(pathlib.Path(sys.argv[1]).read_bytes()));strings=[s.decode(errors='replace') for s in p[6]]
functions={}
for raw in p[5]:
 f=fields(raw);functions[f[1][0]]=strings[f[2][0]]
locations={}
for raw in p[4]:
 f=fields(raw);locations[f[1][0]]=[functions.get(fields(l)[1][0],'?') for l in f[4]]
flat=collections.Counter();cum=collections.Counter();total=0
for raw in p[2]:
 s=fields(raw);ids=[n for v in s[1] for n in packed(v)];values=[n for v in s[2] for n in packed(v)];amount=values[-1];names=[name for i in ids for name in locations.get(i,[])];total+=amount
 if names:flat[names[0]]+=amount
 for name in set(names):cum[name]+=amount
print(json.dumps({'cpu_seconds':round(total/1e9,2),'flat_top':[(n,round(v/total*100,2)) for n,v in flat.most_common(12)],'cumulative_top':[(n,round(v/total*100,2)) for n,v in cum.most_common(12)]},ensure_ascii=False,indent=2))

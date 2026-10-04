/* Default-OFF helper scope finalizer candidate. No device/service/process signals.
 * FD-relative unlink is NOT compare-and-unlink. A captured inode alone cannot
 * safely delete a name in the old shell-writable /data/local/tmp parent.
 * This candidate requires the existing /data/local base to be root owned and
 * non-writable to other UIDs, plus a NEW protected parent. It seals the child
 * and excludes competing privileged writers. It never changes base permissions.
 * It is not a complete installer, ownership grant or reservation release.
 */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1
#define _GNU_SOURCE 1
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define MAX_APK 131072
static const char helper_sha[] = "28db54c6582fa6c855cb20eeb2163b2aa9bcccf8d4d675d111a8c98f54a9e425";
static const uint32_t k[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
struct sha { uint32_t h[8]; unsigned char block[64]; size_t used; uint64_t bytes; };
static uint32_t rotr(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }
static void block(struct sha *s, const unsigned char *p) {
    uint32_t w[64];
    for (int i=0;i<16;++i) w[i]=(uint32_t)p[i*4]<<24|(uint32_t)p[i*4+1]<<16|(uint32_t)p[i*4+2]<<8|p[i*4+3];
    for (int i=16;i<64;++i) {
        uint32_t a=w[i-15],b=w[i-2];
        w[i]=w[i-16]+(rotr(a,7)^rotr(a,18)^(a>>3))+w[i-7]+(rotr(b,17)^rotr(b,19)^(b>>10));
    }
    uint32_t a=s->h[0],b=s->h[1],c=s->h[2],d=s->h[3],e=s->h[4],f=s->h[5],g=s->h[6],h=s->h[7];
    for (int i=0;i<64;++i) {
        uint32_t t=h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^((~e)&g))+k[i]+w[i];
        uint32_t u=(rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;
    }
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
static void update(struct sha *s,const unsigned char *p,size_t n) {
    s->bytes+=n;
    while (n) {
        size_t take=64-s->used;if (take>n) take=n;
        memcpy(s->block+s->used,p,take);s->used+=take;p+=take;n-=take;
        if (s->used==64) {block(s,s->block);s->used=0;}
    }
}
static void digest(struct sha *s,char out[65]) {
    uint64_t bits=s->bytes*8;unsigned char pad[128]={0x80};
    size_t n=s->used<56?56-s->used:120-s->used;
    for (int i=0;i<8;++i) pad[n+i]=(unsigned char)(bits>>(56-i*8));
    update(s,pad,n+8);
    for (int i=0;i<8;++i) snprintf(out+i*8,9,"%08" PRIx32,s->h[i]);
}
static int number(const char *p,uint64_t *v) {
    if (!p || !*p || strlen(p)>19) return 0;
    for (const char *q=p;*q;++q) if (*q<'0'||*q>'9') return 0;
    char *end;errno=0;unsigned long long x=strtoull(p,&end,10);
    if (errno||*end||x>INT64_MAX) return 0;
    *v=x;return 1;
}
static int hex(const char *p,size_t n) {
    if (!p || strlen(p)!=n) return 0;
    for (size_t i=0;i<n;++i) if (!((p[i]>='0'&&p[i]<='9')||(p[i]>='a'&&p[i]<='f'))) return 0;
    return 1;
}
static uint64_t now_ms(void) {
    struct timespec t;if (clock_gettime(CLOCK_MONOTONIC,&t)) return 0;
    return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;
}
static int within(uint64_t end) { uint64_t n=now_ms();return n&&n<end; }
static int entries(int fd,const char *allowed) {
    /* dup shares directory offsets; explicitly rewind the independent stream. */
    int copy=dup(fd);if (copy<0) return 0;
    DIR *d=fdopendir(copy);if (!d) {close(copy);return 0;}
    rewinddir(d);unsigned count=0;struct dirent *e;int valid=1;
    errno=0;
    while ((e=readdir(d))) {
        if (!strcmp(e->d_name,".")||!strcmp(e->d_name,"..")) continue;
        if (++count>1||!allowed||strcmp(e->d_name,allowed)) {valid=0;break;}
    }
    if (errno) valid=0;
    if (closedir(d)) valid=0;
    return valid&&count==(unsigned)(allowed!=NULL);
}
static int inode(const struct stat *s,uint64_t dev,uint64_t ino) {
    return (uint64_t)s->st_dev==dev&&(uint64_t)s->st_ino==ino;
}
static int at_matches(int parent,const char *name,const struct stat *expected) {
    struct stat s;
    return !fstatat(parent,name,&s,AT_SYMLINK_NOFOLLOW)&&s.st_dev==expected->st_dev
        &&s.st_ino==expected->st_ino&&s.st_mode==expected->st_mode&&s.st_uid==expected->st_uid
        &&s.st_gid==expected->st_gid&&s.st_nlink==expected->st_nlink&&s.st_size==expected->st_size;
}
static int hash_fd(int fd,off_t size,const char *expected,uint64_t end) {
    if (size<=0||size>MAX_APK) return 0;
    struct sha s={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    unsigned char buf[4096];off_t offset=0;
    while (offset<size) {
        if (!within(end)) return 0;
        size_t n=(size_t)(size-offset);if (n>sizeof(buf)) n=sizeof(buf);
        ssize_t got=pread(fd,buf,n,offset);
        if (got<=0) return 0;
        update(&s,buf,(size_t)got);offset+=got;
    }
    char out[65];digest(&s,out);return within(end)&&!strcmp(out,expected);
}
#ifdef HG_HELPER_RETIRE_FIXTURE
/* Explicit host-only race injection. Never present in the Android build. */
static int hook(int parent,int child,const char *where,const char *requested) {
    if (strcmp(where,requested)) return 1;
    if (!strcmp(where,"stage_replace")) {
        if (renameat(parent,"stage",parent,"held-original")||mkdirat(parent,"stage",0700)) return 0;
        int other=openat(parent,"stage",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
        if (other<0) return 0;
        int f=openat(other,"foreign",O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0600);
        if (f<0) {close(other);return 0;}close(f);close(other);
    } else if (!strcmp(where,"file_replace")) {
        if (unlinkat(child,"owned.apk",0)) return 0;
        int f=openat(child,"owned.apk",O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0600);
        if (f<0) return 0;
        if (write(f,"foreign",7)!=7) {close(f);return 0;}close(f);
    } else if (!strcmp(where,"foreign_after_unlink")) {
        int f=openat(child,"foreign",O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW,0600);
        if (f<0) return 0;
        close(f);
    } else if (!strcmp(where,"file_growth_after_seal")) {
        int f=openat(child,"owned.apk",O_WRONLY|O_NOFOLLOW|O_CLOEXEC);
        if (f<0) return 0;
        int result=ftruncate(f,MAX_APK+1);close(f);if (result) return 0;
    }
    return 1;
}
#else
static int hook(int parent,int child,const char *where,const char *requested) {
    (void)parent;(void)child;(void)where;(void)requested;return 1;
}
#endif

int main(int argc,char **argv) {
    int fixture=0;uid_t owner=0,peer=2000;gid_t group=2000,sealed_group=0;
    const char *requested="none";
#ifdef HG_HELPER_RETIRE_FIXTURE
    fixture=1;owner=geteuid();peer=geteuid();group=getegid();sealed_group=getegid();
#endif
    if (argc==1) {
        puts("{\"event\":\"helper_scope_prepared\",\"operations_started\":false,\"release_authorized\":false}");return 0;
    }
    /* Production uses only the closed literal /data/local namespace.
     * Captured metadata comes from an independently owned caller, never grants
     * that caller ownership by itself. Fresh installer/driver/permission gates
     * must precede this command. /data/local/tmp is explicitly incompatible.
     */
    if (argc!=13||strcmp(argv[1],"--retire")||!hex(argv[12],64)) return 2;
    const char *path=argv[2];const char *leaf=strrchr(path,'/');
    static const char name_prefix[]="huoguo-helper-root-";
    if (!leaf||strncmp(leaf+1,name_prefix,sizeof(name_prefix)-1)
            ||!hex(leaf+sizeof(name_prefix),24)) return 2;
#ifndef HG_HELPER_RETIRE_FIXTURE
    static const char prefix[]="/data/local/";
    if (geteuid()!=0||strncmp(path,prefix,sizeof(prefix)-1)
            ||leaf!=path+sizeof(prefix)-2||strcmp(argv[12],helper_sha)) return 2;
#else
    (void)helper_sha;
    const char *env=getenv("HG_HELPER_FIXTURE_HOOK");if (env) requested=env;
    if (strcmp(requested,"none")&&strcmp(requested,"stage_replace")&&strcmp(requested,"file_replace")
            &&strcmp(requested,"foreign_after_unlink")&&strcmp(requested,"file_growth_after_seal")) return 2;
#endif
    uint64_t values[9];
    for (int i=0;i<9;++i) if (!number(argv[i+3],values+i)) return 2;
    if (!values[1]||!values[3]||!values[5]||!values[7]||!values[8]||values[8]>MAX_APK) return 2;
#ifndef HG_HELPER_RETIRE_FIXTURE
    if (values[8]!=86419) return 2;
#endif
    int base=-1,parent=-1,child=-1,file=-1,ok=0,seal_started=0,sealed=0,file_seal_started=0,removed=0,stage_removed=0,scope_removed=0;
    const char *failure="scope_unverified";
    struct stat b,p,d,f;uint64_t start=now_ms(),end=start+1800;
    if (!start) goto done;
    char base_path[4096];size_t base_length=(size_t)(leaf-path);
    if (!base_length||base_length>=sizeof(base_path)) goto done;
    memcpy(base_path,path,base_length);base_path[base_length]='\0';
    base=open(base_path,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (base<0||fstat(base,&b)||!inode(&b,values[0],values[1])||!S_ISDIR(b.st_mode)
            ||b.st_uid!=owner||(b.st_mode&07022)||b.st_nlink<1) {failure="protected_base_required";goto done;}
    parent=openat(base,leaf+1,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (parent<0||fstat(parent,&p)||!inode(&p,values[2],values[3])||!S_ISDIR(p.st_mode)
            ||(p.st_mode&07777)!=0710||p.st_uid!=owner||p.st_gid!=group
            ||p.st_nlink<1||p.st_nlink>4||p.st_dev!=b.st_dev||!entries(parent,"stage")) {failure="protected_parent_required";goto done;}
    child=openat(parent,"stage",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (child<0||fstat(child,&d)||!inode(&d,values[4],values[5])||!S_ISDIR(d.st_mode)
            ||(d.st_mode&07777)!=0700||d.st_uid!=peer||d.st_gid!=group||d.st_nlink<1||d.st_nlink>4
            ||d.st_dev!=p.st_dev) goto done;
    file=openat(child,"owned.apk",O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (file<0||fstat(file,&f)||!inode(&f,values[6],values[7])||!S_ISREG(f.st_mode)
            ||(f.st_mode&07777)!=0600||f.st_uid!=peer||f.st_gid!=group||f.st_nlink!=1
            ||f.st_size!=(off_t)values[8]||f.st_dev!=d.st_dev||!entries(child,"owned.apk")
            ||!hash_fd(file,f.st_size,argv[12],end)) goto done;
    if (!hook(parent,child,"stage_replace",requested)||!at_matches(parent,"stage",&d)
            ||!at_matches(child,"owned.apk",&f)||!at_matches(base,leaf+1,&p)||!within(end)) goto done;
    /* Parent names are root protected; sealing freezes non-root entry writers.
     * Existing write FDs are not revoked by chmod. Rehash and recheck the same
     * regular inode. Caller must independently exclude remaining writable FDs
     * and competing privileged writers; a hash is not an atomic content hold.
     */
    seal_started=1;
    if (fchown(child,owner,sealed_group)||fchmod(child,0700)) goto done;
    sealed=1;
    file_seal_started=1;
    if (fchown(file,owner,sealed_group)||fchmod(file,0600)
            ||!hook(parent,child,"file_growth_after_seal",requested)||fstat(file,&f)||fstat(child,&d)
            ||!inode(&f,values[6],values[7])||!inode(&d,values[4],values[5])
            ||!S_ISREG(f.st_mode)||(f.st_mode&07777)!=0600||f.st_uid!=owner||f.st_gid!=sealed_group
            ||f.st_nlink!=1||f.st_size!=(off_t)values[8]||f.st_dev!=d.st_dev
            ||!S_ISDIR(d.st_mode)||(d.st_mode&07777)!=0700||d.st_uid!=owner||d.st_gid!=sealed_group
            ||d.st_nlink<1||d.st_nlink>4||d.st_dev!=p.st_dev
            ||!hash_fd(file,f.st_size,argv[12],end)) goto done;
    struct stat f_final;
    if (fstat(file,&f_final)||!inode(&f_final,values[6],values[7])
            ||f_final.st_mode!=f.st_mode||f_final.st_uid!=f.st_uid||f_final.st_gid!=f.st_gid
            ||f_final.st_nlink!=1||f_final.st_size!=(off_t)values[8]) goto done;
    if (!hook(parent,child,"file_replace",requested)||!entries(child,"owned.apk")
            ||!at_matches(child,"owned.apk",&f)||!at_matches(parent,"stage",&d)||!within(end)) goto done;
    if (unlinkat(child,"owned.apk",0)) goto done;
    removed=1;
    if (!hook(parent,child,"foreign_after_unlink",requested)||!entries(child,NULL)
            ||fstat(child,&d)||!at_matches(parent,"stage",&d)||fsync(child)||!within(end)) goto done;
    struct stat p_after;
    if (fstat(parent,&p_after)||p_after.st_dev!=p.st_dev||p_after.st_ino!=p.st_ino
            ||p_after.st_uid!=owner||p_after.st_gid!=group||(p_after.st_mode&07777)!=0710
            ||!entries(parent,"stage")) goto done;
    if (unlinkat(parent,"stage",AT_REMOVEDIR)) goto done;
    stage_removed=1;
    struct stat absent;
    if (fstatat(parent,"stage",&absent,AT_SYMLINK_NOFOLLOW)!=-1||errno!=ENOENT
            ||!entries(parent,NULL)||fstat(parent,&p_after)||!at_matches(base,leaf+1,&p_after)
            ||fsync(parent)||!within(end)) goto done;
    struct stat b_after;
    if (fstat(base,&b_after)||!inode(&b_after,values[0],values[1])||b_after.st_uid!=owner
            ||b_after.st_mode!=b.st_mode||b_after.st_gid!=b.st_gid||(b_after.st_mode&07022)) goto done;
    if (unlinkat(base,leaf+1,AT_REMOVEDIR)) goto done;
    scope_removed=1;
    if (fstatat(base,leaf+1,&absent,AT_SYMLINK_NOFOLLOW)!=-1||errno!=ENOENT||fsync(base)||!within(end)) goto done;
    ok=1;failure="none";
done:
    if (file>=0&&close(file)) {ok=0;failure="close_unverified";}
    if (child>=0&&close(child)) {ok=0;failure="close_unverified";}
    if (parent>=0&&close(parent)) {ok=0;failure="close_unverified";}
    if (base>=0&&close(base)) {ok=0;failure="close_unverified";}
    int written=printf("{\"event\":\"helper_scope_retirement\",\"fixture_build\":%s,\"completed\":%s,\"directory_sealing_started\":%s,\"directory_sealed\":%s,\"file_sealing_started\":%s,\"APK_unlinked\":%s,\"stage_removed\":%s,\"protected_parent_removed\":%s,\"scope_may_remain\":%s,\"failure\":\"%s\",\"atomic_unlink_claimed\":false,\"remote_PM_quiescence\":false,\"release_authorized\":false}\n",
        fixture?"true":"false",ok?"true":"false",seal_started?"true":"false",sealed?"true":"false",file_seal_started?"true":"false",removed?"true":"false",stage_removed?"true":"false",scope_removed?"true":"false",ok?"false":"true",failure);
    return !ok||written<0||fflush(stdout)?2:0;
}

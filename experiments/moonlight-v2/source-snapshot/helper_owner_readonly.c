/* Default-OFF same-process readonly package/process snapshot component.
 * No install, uninstall, scope mutation, PID adoption or permission-by-wire.
 * Each query owns its sole fork result; timeout keeps that actual object.
 * This is NOT an operator/Attempt/server-lease/PM-quiescence qualifier.
 */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1
#define _GNU_SOURCE 1
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "helper_scope_crypto.h"

#define HG_READ_PREFIX 65536
#define HG_READ_PATH 1024
#define HG_READ_ROWS 4096
static const char read_app[]="local.remoteandroid.direct.experiment";
static const char read_helper[]="local.huoguo.lanuitest";
enum hg_read_kind { HG_READ_APP_PRESENT=1,HG_READ_HELPER_PRESENT,HG_READ_HELPER_ABSENT,HG_READ_PROCESS_IDLE };
struct hg_readonly {
    uint64_t constructed;
    int attempted,unknown,snapshot,natural_queries;
    pid_t child;
    int reaped,status,pipes[2],eof[2],overflow,pipe_failed,term_calls,kill_calls;
    size_t used[2];unsigned char output[2][HG_READ_PREFIX+1];
    int root,file;struct stat file_stat,dirs[12];unsigned dir_count;
#ifdef HG_HELPER_READ_FIXTURE
    const char *fixture_root,*fixture_mode;
    int fixture_queries,fixture_hold,fixture_inherited;
    void (*fixture_after_hash)(struct hg_readonly *);
#endif
};
static volatile sig_atomic_t read_cancelled=0;
void hg_readonly_request_cancel(int signum) { (void)signum;read_cancelled=1; }
struct hg_readonly *hg_readonly_new(void) {
    struct hg_readonly *q=calloc(1,sizeof(*q));if (!q) return NULL;
    q->constructed=now_ms();q->root=q->file=q->pipes[0]=q->pipes[1]=-1;
#ifdef HG_HELPER_READ_FIXTURE
    q->fixture_hold=q->fixture_inherited=-1;
#endif
    if (!q->constructed) {free(q);return NULL;}return q;
}
static int read_unknown(struct hg_readonly *q) {
    if (q) {q->unknown=1;q->snapshot=0;}return 0;
}
static int read_deadline(struct hg_readonly *q,uint64_t end) {
    uint64_t n=now_ms();return q&&n&&!read_cancelled&&n<end&&end<=q->constructed+15000;
}
static int read_pipe(int p[2]) {
    if (pipe(p)) return 0;int f=fcntl(p[0],F_GETFL);
    if (f<0||fcntl(p[0],F_SETFL,f|O_NONBLOCK)||fcntl(p[0],F_SETFD,FD_CLOEXEC)
            ||fcntl(p[1],F_SETFD,FD_CLOEXEC)) {close(p[0]);close(p[1]);return 0;}return 1;
}
static void read_drain(struct hg_readonly *q,int i) {
    if (q->pipes[i]<0||q->eof[i]) return;unsigned char b[4096];
    for (int j=0;j<16;++j) {
        ssize_t n=read(q->pipes[i],b,sizeof(b));
        if (n<0) {if (errno!=EAGAIN&&errno!=EINTR) q->pipe_failed=1;return;}
        if (!n) {q->eof[i]=1;return;}
        size_t keep=(size_t)n;if (keep>HG_READ_PREFIX-q->used[i]) keep=HG_READ_PREFIX-q->used[i];
        memcpy(q->output[i]+q->used[i],b,keep);q->used[i]+=keep;
        if (keep!=(size_t)n) q->overflow=1;
    }
}
static int read_reap(struct hg_readonly *q) {
    if (!q->child||q->reaped) return 1;int status;pid_t p=waitpid(q->child,&status,WNOHANG);
    if (p==q->child) {q->reaped=1;q->status=status;return 1;}
    return !p||(p<0&&errno==EINTR);
}
static int read_natural(struct hg_readonly *q) {
    return q->child&&q->reaped&&q->eof[0]&&q->eof[1]&&!q->overflow&&!q->pipe_failed
        &&!q->term_calls&&!q->kill_calls&&WIFEXITED(q->status);
}
static int read_close_inherited(void) {
    const char *path="/proc/self/fd";
#ifdef __APPLE__
    path="/dev/fd";
#endif
    DIR *d=opendir(path);if (!d) return 0;int own=dirfd(d),ok=own>=3;
    while (ok) {
        errno=0;struct dirent *e=readdir(d);if (!e) {if (errno) ok=0;break;}
        if (!strcmp(e->d_name,".")||!strcmp(e->d_name,"..")) continue;
        const char *p=e->d_name;unsigned long fd=0;
        if (!*p) {ok=0;break;}
        while (*p) {if (*p<'0'||*p>'9'||fd>(INT_MAX-(unsigned)(*p-'0'))/10U) {ok=0;break;}
            fd=fd*10U+(unsigned)(*p++-'0');}
        if (ok&&fd>=3&&(int)fd!=own&&close((int)fd)&&errno!=EBADF) ok=0;
    }
    if (closedir(d)) ok=0;return ok;
}
static int read_start_fixed(struct hg_readonly *q,int operation) {
    if (!q||q->unknown||q->child||read_cancelled||operation<1||operation>3) return 0;
    struct sigaction s;struct rlimit limit;
    /* Single-threaded exclusive waiter required. No autoreap/PID adoption.
     * The child closes all inherited FDs above stdio, including control/scope.
     */
    if (sigaction(SIGCHLD,NULL,&s)||s.sa_handler!=SIG_DFL||(s.sa_flags&SA_NOCLDWAIT)
            ||getrlimit(RLIMIT_NOFILE,&limit)||limit.rlim_cur==RLIM_INFINITY||limit.rlim_cur>65536) return 0;
    int a[2],b[2];if (!read_pipe(a)) return 0;
    if (!read_pipe(b)) {close(a[0]);close(a[1]);return 0;}
#ifdef HG_HELPER_READ_FIXTURE
    ++q->fixture_queries;
    if (!strcmp(q->fixture_mode,"held_EOF")) q->fixture_hold=dup(b[1]);
#endif
    pid_t p=fork();
    if (p<0) {close(a[0]);close(a[1]);close(b[0]);close(b[1]);return 0;}
    if (!p) {
        int null=open("/dev/null",O_RDONLY);
        if (null<0||dup2(null,0)<0||dup2(a[1],1)<0||dup2(b[1],2)<0) _exit(124);
        /* Existing FDs can be above a subsequently lowered soft limit.
         * Enumerate the isolated child's own FD namespace, not that limit.
         */
        if (!read_close_inherited()) _exit(124);
        signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);
#ifdef HG_HELPER_READ_FIXTURE
        /* Actual owned host child; only its Android query output is synthetic. */
        const char *m=q->fixture_mode;
        if (q->fixture_inherited>=0&&(fcntl(q->fixture_inherited,F_GETFD)!=-1||errno!=EBADF)) _exit(123);
        if (!strcmp(m,"ignore_TERM")) {signal(SIGTERM,SIG_IGN);for (;;) pause();}
        if (!strcmp(m,"nonzero")) _exit(7);
        if (!strcmp(m,"stderr")) {write(2,"unknown\n",8);_exit(0);}
        if (!strcmp(m,"overflow")) {unsigned char x[4096];memset(x,'x',sizeof(x));
            for (int j=0;j<40;++j) if (write(1,x,sizeof(x))!=(ssize_t)sizeof(x)) _exit(125);_exit(0);}
        if (!strcmp(m,"embedded_NUL")) {write(1,"PID UID NAME\n\0",14);_exit(0);}
        if (operation==3) {
            const char *text="PID UID NAME\n1 0 init\n";
            if (!strcmp(m,"active_App")) text="PID UID NAME\n1 0 init\n42 10200 local.remoteandroid.direct.experiment:worker\n";
            if (!strcmp(m,"active_helper")) text="PID UID NAME\n42 10200 local.huoguo.lanuitest\n";
            if (!strcmp(m,"later_active")&&q->fixture_queries>2) text="PID UID NAME\n42 10200 local.huoguo.lanuitest:later\n";
            if (!strcmp(m,"duplicate_PID")) text="PID UID NAME\n1 0 init\n1 0 zygote\n";
            if (!strcmp(m,"bad_UID")) text="PID UID NAME\n1 root init\n";
            if (!strcmp(m,"kernel_NAME")) text="  PID   UID NAME                       \n1 0 init\n1032 0 [irq/260-q6v5 wdog]\n1285 0 [Surge kthread]\n";
            if (!strcmp(m,"clone_UID")) text="PID UID NAME\n1 0 init\n9007 99910234 cloned.process\n";
            if (!strcmp(m,"clone_App")) text="PID UID NAME\n9007 99910234 local.remoteandroid.direct.experiment:worker\n";
            if (!strcmp(m,"clone_helper")) text="PID UID NAME\n9007 99910267 local.huoguo.lanuitest:worker\n";
            if (!strcmp(m,"bad_UID_range")) text="PID UID NAME\n1 4294967295 init\n";
            if (!strcmp(m,"control_NAME")) text="PID UID NAME\n1 0 [kernel\tthread]\n";
            if (!strcmp(m,"target_space")) text="PID UID NAME\n9007 10316 local.remoteandroid.direct.experiment ambiguous suffix\n";
            if (!strcmp(m,"bad_header_tail")) text="PID UID NAME EXTRA\n1 0 init\n";
            write(1,text,strlen(text));_exit(0);
        }
        const char *path="package:/data/app/~~fixture/name/base.apk\n";
        if (!strcmp(m,"absent")) _exit(1);
        if (!strcmp(m,"wrong_path")) path="package:/data/local/tmp/base.apk\n";
        if (!strcmp(m,"traversal")) path="package:/data/app/../base.apk\n";
        if (!strcmp(m,"multiple")) path="package:/data/app/~~fixture/name/base.apk\npackage:/data/app/other/base.apk\n";
        if (!strcmp(m,"later_path")&&q->fixture_queries>2) path="package:/data/app/~~fixture/later/base.apk\n";
        write(1,path,strlen(path));_exit(0);
#else
        char *const app[]={"pm","path","--user","0",(char *)read_app,NULL};
        char *const helper[]={"pm","path","--user","0",(char *)read_helper,NULL};
        char *const ps[]={"ps","-A","-o","PID,UID,NAME",NULL};
        char *const env[]={"PATH=/system/bin:/system/xbin","LANG=C",NULL};
        execve(operation==3?"/system/bin/ps":"/system/bin/pm",operation==1?app:operation==2?helper:ps,env);_exit(127);
#endif
    }
    q->child=p;q->pipes[0]=a[0];q->pipes[1]=b[0];close(a[1]);close(b[1]);return 1;
}
static int read_query(struct hg_readonly *q,int operation,uint64_t end,int absence) {
    if (!read_deadline(q,end)||!read_start_fixed(q,operation)) return read_unknown(q);
    uint64_t stop=now_ms()+3000;if (stop>end) stop=end;
#ifdef HG_HELPER_READ_FIXTURE
    /* Shorter host-only timeout fixtures never widen the production budget. */
    if (!strcmp(q->fixture_mode,"ignore_TERM")||!strcmp(q->fixture_mode,"held_EOF")) stop=now_ms()+100;
#endif
    while (within(stop)&&!read_cancelled) {
        if (!read_reap(q)) return read_unknown(q);read_drain(q,0);read_drain(q,1);
        if (q->reaped&&q->eof[0]&&q->eof[1]) break;
        struct pollfd f[2]={{q->eof[0]?-1:q->pipes[0],POLLIN,0},{q->eof[1]?-1:q->pipes[1],POLLIN,0}};
        if (poll(f,2,5)<0&&errno!=EINTR) return read_unknown(q);
    }
    if (!read_deadline(q,end)||!within(stop)||!read_natural(q)||q->used[1]
            ||memchr(q->output[0],0,q->used[0])) return read_unknown(q);
    int code=WEXITSTATUS(q->status);
    if (absence?(code!=0&&code!=1)||q->used[0]:code!=0) return read_unknown(q);
    q->output[0][q->used[0]]=0;++q->natural_queries;return 1;
}
static int read_reset_query(struct hg_readonly *q) {
    if (!q||q->unknown||!read_natural(q)) return 0;
    for (int i=0;i<2;++i) {int fd=q->pipes[i];q->pipes[i]=-1;if (close(fd)) return read_unknown(q);
        q->used[i]=0;q->eof[i]=0;}
    q->child=0;q->reaped=q->status=0;return 1;
}
static int read_uint(const char **cursor,uint64_t max,uint64_t *out) {
    const char *p=*cursor;uint64_t v=0;unsigned count=0;
    while (*p>='0'&&*p<='9') {if (++count>10||v>(max-(unsigned)(*p-'0'))/10) return 0;
        v=v*10+(unsigned)(*p++-'0');}
    if (!count) return 0;*cursor=p;*out=v;return 1;
}
static int read_spaces(const char **p) {
    if (**p!=' '&&**p!='\t') return 0;while (**p==' '||**p=='\t') ++*p;return 1;
}
static int read_idle_table(struct hg_readonly *q) {
    const char *p=(char *)q->output[0];while (*p==' '||*p=='\t') ++p;
    if (strncmp(p,"PID",3)) return 0;p+=3;if (!read_spaces(&p)||strncmp(p,"UID",3)) return 0;
    p+=3;if (!read_spaces(&p)||strncmp(p,"NAME",4)) return 0;p+=4;
    while (*p==' '||*p=='\t') ++p;if (*p++!='\n') return 0;
    uint64_t pids[HG_READ_ROWS];unsigned rows=0;
    while (*p) {
        while (*p==' '||*p=='\t') ++p;uint64_t pid,uid;
        if (rows>=HG_READ_ROWS||!read_uint(&p,4194304,&pid)||!pid||!read_spaces(&p)
                ||!read_uint(&p,UINT32_MAX-1,&uid)||!read_spaces(&p)) return 0;
        (void)uid;for (unsigned i=0;i<rows;++i) if (pids[i]==pid) return 0;pids[rows++]=pid;
        /* NAME is the entire final column. Keep Android kernel thread spaces
         * and clone-profile UIDs, not a user0-only shell-word approximation.
         */
        const char *name=p;while (*p&&*p!='\n') {
            if ((unsigned char)*p<32||(unsigned char)*p>126||p-name>=255) return 0;++p;}
        size_t n=(size_t)(p-name);while (n&&name[n-1]==' ') --n;if (!n) return 0;
        const char *targets[]={read_app,read_helper};
        for (unsigned i=0;i<2;++i) {size_t z=strlen(targets[i]);
            if (n>=z&&!memcmp(name,targets[i],z)&&(n==z||name[z]==':'||name[z]==' ')) return 0;}
        if (*p++!='\n') return 0;
    }
    return rows>0;
}
static int read_path(struct hg_readonly *q,char path[HG_READ_PATH]) {
    const char *p=(char *)q->output[0];size_t n=q->used[0];
    if (n<24||n>=HG_READ_PATH+8||strncmp(p,"package:/data/app/",18)||p[n-1]!='\n') return 0;
    /* No splits, dots, empty components, whitespace or extra package lines. */
    p+=8;n-=9;if (n>=HG_READ_PATH) return 0;memcpy(path,p,n);path[n]=0;
    const char *at=path+10;unsigned components=0;
    while (*at) {
        const char *start=at;while (*at&&*at!='/') {
            unsigned char c=(unsigned char)*at;
            if (!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||strchr("_+~=.-",c))) return 0;++at;}
        size_t z=(size_t)(at-start);if (!z||z>127||(z==1&&start[0]=='.')||(z==2&&!memcmp(start,"..",2))||++components>8) return 0;
        if (!*at) return z==8&&!memcmp(start,"base.apk",8)&&components>=2;++at;
        if (!*at) return 0;
    }
    return 0;
}
static int read_stat_same(const struct stat *a,const struct stat *b,int file) {
    if (a->st_dev!=b->st_dev||a->st_ino!=b->st_ino||a->st_uid!=b->st_uid||a->st_gid!=b->st_gid||a->st_mode!=b->st_mode) return 0;
    if (!file) return 1;
#ifdef __APPLE__
    return a->st_nlink==b->st_nlink&&a->st_size==b->st_size&&a->st_mtimespec.tv_sec==b->st_mtimespec.tv_sec
        &&a->st_mtimespec.tv_nsec==b->st_mtimespec.tv_nsec&&a->st_ctimespec.tv_sec==b->st_ctimespec.tv_sec&&a->st_ctimespec.tv_nsec==b->st_ctimespec.tv_nsec;
#else
    return a->st_nlink==b->st_nlink&&a->st_size==b->st_size&&a->st_mtim.tv_sec==b->st_mtim.tv_sec
        &&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec&&a->st_ctim.tv_sec==b->st_ctim.tv_sec&&a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
#endif
}
static int read_trusted_node(struct stat *s,int file) {
#ifdef HG_HELPER_READ_FIXTURE
    if (s->st_uid!=geteuid()||s->st_gid!=getegid()) return 0;
#else
    if ((s->st_uid!=0&&s->st_uid!=1000)||(s->st_gid!=0&&s->st_gid!=1000)) return 0;
#endif
    return !(s->st_mode&07002)&&(file?S_ISREG(s->st_mode)&&s->st_nlink==1&&!(s->st_mode&022):S_ISDIR(s->st_mode));
}
/* Resolve each component with nofollow directory FDs, and bracket each name.
 * This binds a READ observation, not an atomic package/content hold. */
static int read_open_package(struct hg_readonly *q,const char *path,int retain,uint64_t end) {
    if (!read_deadline(q,end)) return 0;
    const char *base="/";
#ifdef HG_HELPER_READ_FIXTURE
    base=q->fixture_root;
#endif
    int fd=open(base,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);struct stat bs;
    if (fd<0) return 0;
    if (!read_deadline(q,end)||fstat(fd,&bs)||!read_trusted_node(&bs,0)) {close(fd);return 0;}
    unsigned dirs=0;
    if (retain) q->dirs[dirs]=bs;
    else if (!q->dir_count||!read_stat_same(&q->dirs[dirs],&bs,0)) {close(fd);return 0;}
    ++dirs;
    char parts[HG_READ_PATH];
#ifdef HG_HELPER_READ_FIXTURE
    snprintf(parts,sizeof(parts),"%s",path+10);
#else
    snprintf(parts,sizeof(parts),"%s",path+1); /* Includes data/app; every component nofollow. */
#endif
    char *at=parts;int ok=0;
    for (;;) {
        if (!read_deadline(q,end)) break;
        char *slash=strchr(at,'/');int file=!slash;if (slash) *slash=0;
        struct stat before,actual,after;
        if (fstatat(fd,at,&before,AT_SYMLINK_NOFOLLOW)||!read_trusted_node(&before,file)) break;
        int next=openat(fd,at,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|(file?0:O_DIRECTORY));if (next<0) break;
        int good=!fstat(next,&actual)&&!fstatat(fd,at,&after,AT_SYMLINK_NOFOLLOW)
            &&read_stat_same(&before,&actual,file)&&read_stat_same(&before,&after,file);
        if (!good||!read_deadline(q,end)) {close(next);break;}
        close(fd);fd=next;
        if (file) {
            if (retain) {q->file=fd;q->file_stat=actual;q->dir_count=dirs;fd=-1;ok=1;}
            else ok=dirs==q->dir_count&&read_stat_same(&q->file_stat,&actual,1);
            break;
        }
        if (dirs>=12) break;
        if (retain) q->dirs[dirs]=actual;
        else if (dirs>=q->dir_count||!read_stat_same(&q->dirs[dirs],&actual,0)) break;
        ++dirs;
        at=slash+1;
    }
    if (fd>=0) close(fd);return ok;
}
static int read_hash_package(struct hg_readonly *q,enum hg_read_kind kind,uint64_t end) {
    uint64_t size=kind==HG_READ_APP_PRESENT?7245582:86419;
    const char *sha=kind==HG_READ_APP_PRESENT?"f97b3e3735eb729bf28c086c92cb9a4147093cd0ed05a3aeb456456c42419779"
        :"28db54c6582fa6c855cb20eeb2163b2aa9bcccf8d4d675d111a8c98f54a9e425";
#ifdef HG_HELPER_READ_FIXTURE
    (void)kind;size=sizeof("public host APK standin\n")-1;
    sha="fe3c3078632a6966a09cd0074f1092b48c94ac4a90a3efaca5fe73b525e44b75";
#endif
    if (q->file<0||q->file_stat.st_size!=(off_t)size||!hex(sha,64)) return 0;
    struct sha s={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    unsigned char b[4096];uint64_t at=0;
    while (at<size) {
        if (!read_deadline(q,end)) return 0;size_t need=(size_t)(size-at);if (need>sizeof(b)) need=sizeof(b);
        ssize_t n=pread(q->file,b,need,(off_t)at);if (n<=0) return 0;update(&s,b,(size_t)n);at+=(uint64_t)n;
    }
    char actual[65];digest(&s,actual);struct stat after;
    return read_deadline(q,end)&&!strcmp(actual,sha)&&!fstat(q->file,&after)&&read_stat_same(&q->file_stat,&after,1);
}
int hg_readonly_snapshot(struct hg_readonly *q,enum hg_read_kind kind,uint64_t end) {
    if (!q||q->attempted||q->unknown||kind<HG_READ_APP_PRESENT||kind>HG_READ_PROCESS_IDLE
            ||!read_deadline(q,end)) return read_unknown(q);
    q->attempted=1;
#ifndef HG_HELPER_READ_FIXTURE
    if (geteuid()!=0||getegid()!=0) return read_unknown(q);
#endif
    if (!read_query(q,3,end,0)||!read_idle_table(q)||!read_reset_query(q)) return read_unknown(q);
    if (kind!=HG_READ_PROCESS_IDLE) {
        int operation=kind==HG_READ_APP_PRESENT?1:2,absent=kind==HG_READ_HELPER_ABSENT;
        char first[HG_READ_PATH],last[HG_READ_PATH];
        if (!read_query(q,operation,end,absent)) return read_unknown(q);
        if (!absent&&(!read_path(q,first)||!read_open_package(q,first,1,end)||!read_hash_package(q,kind,end))) return read_unknown(q);
#ifdef HG_HELPER_READ_FIXTURE
        if (q->fixture_after_hash) q->fixture_after_hash(q);
#endif
        if (!read_reset_query(q)||!read_query(q,operation,end,absent)) return read_unknown(q);
        if (!absent&&(!read_path(q,last)||strcmp(first,last)||!read_open_package(q,last,0,end))) return read_unknown(q);
        if (!read_reset_query(q)) return read_unknown(q);
    }
    if (!read_query(q,3,end,0)||!read_idle_table(q)||!read_deadline(q,end)) return read_unknown(q);
    q->snapshot=1;return 1;
}
/* Explicit caller-requested cancellation of ONLY the unreaped fork result.
 * Never automatic on query timeout. Reaping a forced query cannot bless its
 * observation or any owner/scope/PM/server cleanup. Actual calls != delivery.
 */
int hg_readonly_stop_owned(struct hg_readonly *q,unsigned budget_ms) {
    if (!q||!q->child||!budget_ms||budget_ms>1000) return 0;read_unknown(q);
    struct sigaction s;if (sigaction(SIGCHLD,NULL,&s)||s.sa_handler!=SIG_DFL||(s.sa_flags&SA_NOCLDWAIT)) return 0;
    uint64_t end=now_ms()+budget_ms,term=0;
    while (within(end)) {
        if (!read_reap(q)) return 0;read_drain(q,0);read_drain(q,1);
        if (q->reaped&&q->eof[0]&&q->eof[1]) return 1;
        if (!q->reaped&&!q->term_calls) {q->term_calls=1;term=now_ms();kill(q->child,SIGTERM);}
        if (!q->reaped&&q->term_calls&&!q->kill_calls&&now_ms()>=term+100) {q->kill_calls=1;kill(q->child,SIGKILL);}
        struct timespec t={0,5000000};nanosleep(&t,NULL);
    }
    return 0;
}
int hg_readonly_close(struct hg_readonly *q) {
    if (!q||(q->child&&(!q->reaped||!q->eof[0]||!q->eof[1]))) return 0;
    int good=1;int *fds[]={&q->root,&q->file,&q->pipes[0],&q->pipes[1]};
    for (unsigned i=0;i<4;++i) if (*fds[i]>=0) {int fd=*fds[i];*fds[i]=-1;if (close(fd)) good=0;}
    return good; /* Read-object handles only, never native owner/release. */
}
#ifndef HG_HELPER_READ_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"helper_readonly_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"native_read_snapshot_grants_permission\":false,\"release_authorized\":false}");return 0;
}
#endif

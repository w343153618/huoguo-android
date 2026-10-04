/* Default-OFF native helper ownership kernel candidate. Android CLI cannot
 * activate this partial library. No installer/supervisor/operator/lease grant.
 * NEW root-only stage avoids granting shell new writable FDs after upload.
 * Old flat tmp and root:shell retirement contracts are NOT interchangeable.
 */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1
#define _GNU_SOURCE 1
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "helper_scope_crypto.h"

#define MAX_APK 131072
#define PIPE_PREFIX 4096
static const char expected_sha[]="28db54c6582fa6c855cb20eeb2163b2aa9bcccf8d4d675d111a8c98f54a9e425";
static volatile sig_atomic_t cancelled=0;
void hg_owner_request_cancel(int signum) { (void)signum;cancelled=1; }
struct hg_owner {
    int base,parent,stage,writer,reader;
    int possible_scope,created,writer_closed,upload_verified;
    struct stat base_stat,parent_stat,stage_stat,file_stat;
    char name[64],path[256];
    pid_t child;
    int child_reaped,child_status,term_calls,kill_calls,pipe_EOF[2],overflow,pipe_failed;
    int pipes[2],hold_fixture_pipe;
    size_t output_size[2];unsigned char output[2][PIPE_PREFIX];
};

/* Construction is inert. No public PID adoption parameter exists. */
struct hg_owner *hg_owner_new(void) {
    struct hg_owner *o=calloc(1,sizeof(*o));
    if (o) {o->base=o->parent=o->stage=o->writer=o->reader=-1;
        o->pipes[0]=o->pipes[1]=-1;o->hold_fixture_pipe=-1;}
    return o;
}
static int exact_node(int fd,const struct stat *a) {
    struct stat b;
    return !fstat(fd,&b)&&b.st_dev==a->st_dev&&b.st_ino==a->st_ino
        &&b.st_uid==a->st_uid&&b.st_gid==a->st_gid&&b.st_mode==a->st_mode
        &&b.st_nlink==a->st_nlink&&b.st_size==a->st_size;
}
static int named_node(int fd,const char *name,const struct stat *a) {
    struct stat b;
    return !fstatat(fd,name,&b,AT_SYMLINK_NOFOLLOW)&&b.st_dev==a->st_dev
        &&b.st_ino==a->st_ino&&b.st_uid==a->st_uid&&b.st_gid==a->st_gid
        &&b.st_mode==a->st_mode&&b.st_nlink==a->st_nlink&&b.st_size==a->st_size;
}
static int same_identity(const struct stat *a,const struct stat *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_uid==b->st_uid
        &&a->st_gid==b->st_gid&&a->st_mode==b->st_mode&&a->st_nlink==b->st_nlink;
}
static int directory_after_one_creation(const struct stat *before,const struct stat *after) {
    /* APFS counts a new regular-file entry here too; Linux directory nlink
     * usually changes only for a subdirectory. One exact owned new entry may
     * therefore keep or increment this count, never adopt other metadata.
     */
    return before->st_dev==after->st_dev&&before->st_ino==after->st_ino
        &&before->st_uid==after->st_uid&&before->st_gid==after->st_gid
        &&before->st_mode==after->st_mode
        &&(after->st_nlink==before->st_nlink||after->st_nlink==before->st_nlink+1);
}
static int only_entry(int fd,const char *name) {
    int copy=dup(fd);if (copy<0) return 0;
    DIR *d=fdopendir(copy);if (!d) {close(copy);return 0;}
    rewinddir(d);unsigned found=0;int ok=1;struct dirent *e;errno=0;
    while ((e=readdir(d))) {
        if (!strcmp(e->d_name,".")||!strcmp(e->d_name,"..")) continue;
        if (++found>1||strcmp(e->d_name,name)) {ok=0;break;}
    }
    int read_error=errno;
    if (closedir(d)) ok=0; /* Always close the duplicate, including read failure. */
    if (read_error) ok=0;
    return ok&&found==1;
}
static int current_scope(struct hg_owner *o) {
    struct stat b;
    return !fstat(o->base,&b)&&b.st_dev==o->base_stat.st_dev&&b.st_ino==o->base_stat.st_ino
        &&b.st_uid==o->base_stat.st_uid&&b.st_gid==o->base_stat.st_gid&&b.st_mode==o->base_stat.st_mode
        &&exact_node(o->parent,&o->parent_stat)&&exact_node(o->stage,&o->stage_stat)
        &&named_node(o->base,o->name,&o->parent_stat)&&named_node(o->parent,"stage",&o->stage_stat)
        &&only_entry(o->parent,"stage")&&only_entry(o->stage,"owned.apk");
}
int hg_owner_create(struct hg_owner *o,const char *base,const char *nonce,uint64_t dev,uint64_t ino) {
    if (!o||o->base>=0||!base||!hex(nonce,24)) return 0;
    uid_t uid=0;gid_t gid=0;
#ifdef HG_HELPER_OWNER_FIXTURE
    uid=geteuid();gid=getegid();
#else
    if (geteuid()!=0||getegid()!=0||strcmp(base,"/data/local")) return 0;
#endif
    o->base=open(base,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (o->base<0||fstat(o->base,&o->base_stat)||!S_ISDIR(o->base_stat.st_mode)
            ||o->base_stat.st_uid!=uid||(o->base_stat.st_mode&07022)
            ||(uint64_t)o->base_stat.st_dev!=dev||(uint64_t)o->base_stat.st_ino!=ino) return 0;
    int n=snprintf(o->name,sizeof(o->name),"huoguo-helper-owner-%s",nonce);
    int p=snprintf(o->path,sizeof(o->path),"%s/%s/stage/owned.apk",base,o->name);
    if (n<0||(size_t)n>=sizeof(o->name)||p<0||(size_t)p>=sizeof(o->path)) return 0;
    if (mkdirat(o->base,o->name,0700)) return 0; /* EEXIST never adopts a scope. */
    o->possible_scope=1;
    o->parent=openat(o->base,o->name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (o->parent<0||fstat(o->parent,&o->parent_stat)||!S_ISDIR(o->parent_stat.st_mode)
            ||o->parent_stat.st_uid!=uid||o->parent_stat.st_gid!=gid
            ||(o->parent_stat.st_mode&07777)!=0700||o->parent_stat.st_dev!=o->base_stat.st_dev) return 0;
    if (mkdirat(o->parent,"stage",0700)) return 0;
    o->stage=openat(o->parent,"stage",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (o->stage<0||fstat(o->stage,&o->stage_stat)||!S_ISDIR(o->stage_stat.st_mode)
            ||o->stage_stat.st_uid!=uid||o->stage_stat.st_gid!=gid
            ||(o->stage_stat.st_mode&07777)!=0700||o->stage_stat.st_dev!=o->base_stat.st_dev) return 0;
    o->writer=openat(o->stage,"owned.apk",O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if (o->writer<0||fstat(o->writer,&o->file_stat)||!S_ISREG(o->file_stat.st_mode)
            ||o->file_stat.st_uid!=uid||o->file_stat.st_gid!=gid||o->file_stat.st_nlink!=1
            ||(o->file_stat.st_mode&07777)!=0600||o->file_stat.st_dev!=o->base_stat.st_dev) return 0;
    struct stat parent_after,stage_after;
    if (fstat(o->parent,&parent_after)||fstat(o->stage,&stage_after)
            ||!directory_after_one_creation(&o->parent_stat,&parent_after)
            ||!directory_after_one_creation(&o->stage_stat,&stage_after)) return 0;
    /* Only our one new child/file may change directory size/link count.
     * Never adopt a replaced identity or permissions.
     */
    o->parent_stat=parent_after;o->stage_stat=stage_after;
    if (!current_scope(o)) return 0;
    o->created=1;return 1;
}
static int hash_file(int fd,off_t size,const char *expected,uint64_t end) {
    if (size<=0||size>MAX_APK) return 0;
    struct sha s={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    unsigned char data[4096];off_t at=0;
    while (at<size) {
        if (cancelled||!within(end)) return 0;
        size_t need=(size_t)(size-at);if (need>sizeof(data)) need=sizeof(data);
        ssize_t n=pread(fd,data,need,at);if (n<=0) return 0;
        update(&s,data,(size_t)n);at+=n;
    }
    char actual[65];digest(&s,actual);return within(end)&&!strcmp(actual,expected);
}
int hg_owner_upload(struct hg_owner *o,int input,uint64_t size,const char *sha,uint64_t end) {
    if (!o||!o->created||o->writer<0||o->upload_verified||!size||size>MAX_APK||!hex(sha,64)) return 0;
#ifndef HG_HELPER_OWNER_FIXTURE
    if (size!=86419||strcmp(sha,expected_sha)) return 0;
#else
    (void)expected_sha;
#endif
    int flags=fcntl(input,F_GETFL);if (flags<0||fcntl(input,F_SETFL,flags|O_NONBLOCK)) return 0;
    unsigned char data[4096];uint64_t got=0;int eof=0;
    while (!eof) {
        if (cancelled||!within(end)) return 0;
        struct pollfd p={input,POLLIN,0};int ready=poll(&p,1,20);
        if (ready<0) {if (errno==EINTR) continue;return 0;}if (!ready) continue;
        ssize_t n=read(input,data,sizeof(data));
        if (n<0) {if (errno==EAGAIN||errno==EINTR) continue;return 0;}
        if (!n) {eof=1;continue;}
        if ((uint64_t)n>size-got) return 0;
        size_t sent=0;
        while (sent<(size_t)n) {
            if (cancelled||!within(end)) return 0;
            ssize_t w=pwrite(o->writer,data+sent,(size_t)n-sent,(off_t)(got+sent));
            if (w<=0) return 0;sent+=(size_t)w;
        }
        got+=(uint64_t)n;
    }
    struct stat file_after;
    if (got!=size||fsync(o->writer)||fstat(o->writer,&file_after)
            ||!same_identity(&file_after,&o->file_stat)||file_after.st_size!=(off_t)size
            ||!current_scope(o)||!named_node(o->stage,"owned.apk",&file_after)) return 0;
    o->file_stat=file_after;
    /* This process created the only write FD and has not forked or duplicated
     * it. Close before any child. No shell ownership/publication is introduced.
     * Competing privileged actors remain a separately required trust boundary.
     */
    int writer=o->writer;o->writer=-1;
    if (close(writer)) return 0;o->writer_closed=1;
    o->reader=openat(o->stage,"owned.apk",O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    if (o->reader<0||!exact_node(o->reader,&o->file_stat)
            ||!hash_file(o->reader,(off_t)size,sha,end)||!exact_node(o->reader,&o->file_stat)) return 0;
    if (!current_scope(o)) return 0;
    o->upload_verified=1;return 1;
}
static int make_pipe(int p[2]) {
    if (pipe(p)) return 0;
    int flags=fcntl(p[0],F_GETFL);
    if (flags<0||fcntl(p[0],F_SETFL,flags|O_NONBLOCK)
            ||fcntl(p[0],F_SETFD,FD_CLOEXEC)||fcntl(p[1],F_SETFD,FD_CLOEXEC)) {
        close(p[0]);close(p[1]);return 0;}
    return 1;
}
/* No child argv/PID setters. The production library's only PM operation is
 * fixed helper user0 install. It still needs complete caller/package/lease
 * binding; the Android executable has no activation entry into this library.
 */
int hg_owner_pm_start(struct hg_owner *o,const char *fixture_action) {
    if (!o||cancelled||!o->upload_verified||!o->writer_closed||o->writer>=0||o->child
            ||!current_scope(o)||!exact_node(o->reader,&o->file_stat)
            ||!named_node(o->stage,"owned.apk",&o->file_stat)) return 0;
    /* Auto-reap or a competing SIGCHLD handler would invalidate the unreaped
     * PID guarantee between waitpid(WNOHANG) and kill. This isolated native
     * parent must be single-threaded and the exclusive waiter for its child.
     */
    struct sigaction child_policy;
    if (sigaction(SIGCHLD,NULL,&child_policy)||child_policy.sa_handler!=SIG_DFL
            ||(child_policy.sa_flags&SA_NOCLDWAIT)) return 0;
    int out[2],err[2];if (!make_pipe(out)) return 0;
    if (!make_pipe(err)) {close(out[0]);close(out[1]);return 0;}
#ifdef HG_HELPER_OWNER_FIXTURE
    if (!strcmp(fixture_action,"holdpipe")) o->hold_fixture_pipe=dup(err[1]);
#else
    (void)fixture_action;
#endif
    pid_t p=fork();
    if (p<0) {close(out[0]);close(out[1]);close(err[0]);close(err[1]);return 0;}
    if (!p) {
        close(out[0]);close(err[0]);close(o->base);close(o->parent);close(o->stage);close(o->reader);
        if (o->hold_fixture_pipe>=0) close(o->hold_fixture_pipe);
        int null=open("/dev/null",O_RDONLY);
        if (null<0||dup2(null,STDIN_FILENO)<0||dup2(out[1],STDOUT_FILENO)<0||dup2(err[1],STDERR_FILENO)<0) _exit(124);
        if (null>2) close(null);if (out[1]>2) close(out[1]);if (err[1]>2) close(err[1]);
        signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);
#ifdef HG_HELPER_OWNER_FIXTURE
        if (!strcmp(fixture_action,"ignoreTERM")||!strcmp(fixture_action,"cancel")) {
            signal(SIGTERM,SIG_IGN);write(1,"fixture-ready\n",14);
            for (;;) pause();
        }
        if (!strcmp(fixture_action,"overflow")) {
            unsigned char bytes[1024];memset(bytes,'x',sizeof(bytes));
            for (int i=0;i<80;++i) if (write(2,bytes,sizeof(bytes))!=(ssize_t)sizeof(bytes)) _exit(125);
        }
        if (!strcmp(fixture_action,"failure")) {write(1,"Failure\n",8);_exit(7);}
        write(1,"Success\n",8);_exit(0);
#else
        char *const words[]={"pm","install","--user","0","-r",o->path,NULL};
        char *const env[]={"PATH=/system/bin:/system/xbin","LANG=C",NULL};
        execve("/system/bin/pm",words,env);_exit(127);
#endif
    }
    o->child=p;close(out[1]);close(err[1]);o->pipes[0]=out[0];o->pipes[1]=err[0];
    return 1;
}
static void drain(struct hg_owner *o,int i) {
    if (o->pipe_EOF[i]||o->pipes[i]<0) return;
    unsigned char bytes[4096];
    for (int reads=0;reads<16;++reads) {
        ssize_t n=read(o->pipes[i],bytes,sizeof(bytes));
        if (n<0) {if (errno!=EAGAIN&&errno!=EINTR) o->pipe_failed=1;return;}
        if (!n) {o->pipe_EOF[i]=1;return;}
        size_t keep=(size_t)n;if (keep>PIPE_PREFIX-o->output_size[i]) keep=PIPE_PREFIX-o->output_size[i];
        memcpy(o->output[i]+o->output_size[i],bytes,keep);o->output_size[i]+=keep;
        if (keep!=(size_t)n) o->overflow=1;
    }
}
static int child_reap(struct hg_owner *o) {
    if (!o->child||o->child_reaped) return 1;
    int status;pid_t r=waitpid(o->child,&status,WNOHANG);
    if (r==o->child) {o->child_reaped=1;o->child_status=status;return 1;}
    return r==0||(r<0&&errno==EINTR);
}
int hg_owner_pm_wait(struct hg_owner *o,unsigned budget_ms) {
    if (!o||!o->child||!budget_ms||budget_ms>1000) return 0;
    uint64_t start=now_ms(),deadline=start+budget_ms,term_at=0,kill_at=0;
    if (!start) return 0;
#ifdef HG_HELPER_OWNER_FIXTURE
    int ready_emitted=0;
#endif
    while (1) {
        if (!child_reap(o)) return 0;
        drain(o,0);drain(o,1);
#ifdef HG_HELPER_OWNER_FIXTURE
        if (!ready_emitted&&o->output_size[0]>=14&&!memcmp(o->output[0],"fixture-ready\n",14)) {
            puts("{\"event\":\"helper_owner_child_ready\",\"fixture_build\":true,\"release_authorized\":false}");fflush(stdout);ready_emitted=1;
        }
#endif
        uint64_t current=now_ms();if (!current) return 0;
        if (o->child_reaped&&o->pipe_EOF[0]&&o->pipe_EOF[1]) break;
        if (!o->child_reaped&&(cancelled||current>=deadline)&&!o->term_calls) {
            /* This PID is the sole actual fork result and remains unreaped.
             * Even a zombie cannot be reused until this parent waitpids it.
             * Only this child, never a process group or discovered PID.
             */
            o->term_calls=1;term_at=current;kill(o->child,SIGTERM);
        }
        if (!o->child_reaped&&o->term_calls&&current>=term_at+150&&!o->kill_calls) {
            o->kill_calls=1;kill_at=current;kill(o->child,SIGKILL);
        }
        if (o->child_reaped&&current>=deadline+300) return 0; /* inherited pipe stays unknown */
        if (!o->child_reaped&&o->kill_calls&&current>=kill_at+1000) {
            /* Keep actual unreaped ownership rather than exiting on unknown. */
            fprintf(stdout,"{\"event\":\"helper_owner_child_unknown_live_hold\",\"release_authorized\":false}\n");fflush(stdout);
            for (;;) {if (!child_reap(o)) return 0;if (o->child_reaped) break;struct timespec t={0,10000000};nanosleep(&t,NULL);}
        }
        struct timespec t={0,5000000};nanosleep(&t,NULL);
    }
    return WIFEXITED(o->child_status)&&WEXITSTATUS(o->child_status)==0
        &&!o->term_calls&&!o->kill_calls&&!o->overflow&&!o->pipe_failed
        &&o->output_size[0]==8&&!memcmp(o->output[0],"Success\n",8)&&!o->output_size[1];
}
/* Only closed/reaped fixture/library teardown. Not scope removal or release.
 * A full live supervisor must retain this object on unknown, not call this
 * partial close path and exit. Production CLI cannot reach any of these ops.
 */
int hg_owner_close_handles(struct hg_owner *o) {
    if (!o||(o->child&&!o->child_reaped)) return 0;
#ifndef HG_HELPER_OWNER_FIXTURE
    /* Matched owner-FD retirement is not implemented yet. Keep these actual
     * handles rather than allowing a partial live caller to abandon its scope.
     */
    if (o->possible_scope) return 0;
#endif
    int *fds[]={&o->writer,&o->reader,&o->stage,&o->parent,&o->base,&o->pipes[0],&o->pipes[1],&o->hold_fixture_pipe};
    int good=1;
    for (size_t i=0;i<sizeof(fds)/sizeof(fds[0]);++i) if (*fds[i]>=0) {
        int fd=*fds[i];*fds[i]=-1;if (close(fd)) good=0;
    }
    return good;
}
int main(int argc,char **argv) {
    if (argc==1) {
        puts("{\"event\":\"helper_owner_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"release_authorized\":false}");return 0;
    }
#ifndef HG_HELPER_OWNER_FIXTURE
    (void)argv;return 2; /* no partial Android CLI execution */
#else
    if (argc!=9||strcmp(argv[1],"--fixture")||!hex(argv[3],24)||!hex(argv[7],64)) return 2;
    uint64_t dev,ino,size;
    if (!number(argv[4],&dev)||!number(argv[5],&ino)||!number(argv[6],&size)||!size||size>MAX_APK) return 2;
    const char *action=argv[8];
    if (strcmp(action,"natural")&&strcmp(action,"failure")&&strcmp(action,"ignoreTERM")
            &&strcmp(action,"cancel")&&strcmp(action,"overflow")&&strcmp(action,"holdpipe")
            &&strcmp(action,"auto_reap")&&strcmp(action,"handler_policy")
            &&strcmp(action,"cancel_before_fork")
            &&strcmp(action,"mutated_mode")&&strcmp(action,"foreign_entry")) return 2;
    struct sigaction sa;memset(&sa,0,sizeof(sa));sa.sa_handler=hg_owner_request_cancel;sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM,&sa,NULL);sigaction(SIGINT,&sa,NULL);
    struct hg_owner *o=hg_owner_new();if (!o) return 2;
    int created=hg_owner_create(o,argv[2],argv[3],dev,ino);
    int upload=created&&hg_owner_upload(o,STDIN_FILENO,size,argv[7],now_ms()+1500);
    /* Input EOF already observed; its FD cannot leak into the PM child. */
    close(STDIN_FILENO);
    printf("{\"event\":\"helper_owner_upload\",\"fixture_build\":true,\"scope_created\":%s,\"writer_closed\":%s,\"upload_verified\":%s,\"release_authorized\":false}\n",created?"true":"false",o->writer_closed?"true":"false",upload?"true":"false");fflush(stdout);
    if (!strcmp(action,"auto_reap")) signal(SIGCHLD,SIG_IGN);
    if (!strcmp(action,"handler_policy")) signal(SIGCHLD,hg_owner_request_cancel);
    if (!strcmp(action,"cancel_before_fork")) hg_owner_request_cancel(SIGTERM);
    if (upload&&!strcmp(action,"mutated_mode")) fchmod(o->reader,0400);
    if (upload&&!strcmp(action,"foreign_entry")) {
        int foreign=openat(o->stage,"foreign",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
        if (foreign>=0) {write(foreign,"foreign",7);close(foreign);}
    }
    int started=upload&&hg_owner_pm_start(o,action),accepted=0;
    if (started) {
        puts("{\"event\":\"helper_owner_owned_child_started\",\"fixture_build\":true,\"actual_PM_invoked\":false,\"release_authorized\":false}");fflush(stdout);
        accepted=hg_owner_pm_wait(o,!strcmp(action,"cancel")?1000:300);
    }
    int natural=o->child_reaped&&WIFEXITED(o->child_status)?WEXITSTATUS(o->child_status):-1;
    int signal_number=o->child_reaped&&WIFSIGNALED(o->child_status)?WTERMSIG(o->child_status):0;
    int closed=hg_owner_close_handles(o);
    printf("{\"event\":\"helper_owner_fixture_footer\",\"fixture_build\":true,\"host_child_result_accepted\":%s,\"child_reaped\":%s,\"child_exit\":%d,\"child_signal\":%d,\"TERM_calls\":%d,\"KILL_calls\":%d,\"both_pipe_EOF\":%s,\"pipe_overflow\":%s,\"handles_closed\":%s,\"possible_scope_created\":%s,\"scope_may_remain\":%s,\"actual_PM_invoked\":false,\"remote_PM_quiescence_verified\":false,\"retirement_authorized\":false,\"release_authorized\":false}\n",
        accepted?"true":"false",o->child_reaped?"true":"false",natural,signal_number,o->term_calls,o->kill_calls,
        o->pipe_EOF[0]&&o->pipe_EOF[1]?"true":"false",o->overflow?"true":"false",closed?"true":"false",o->possible_scope?"true":"false",o->possible_scope?"true":"false");
    free(o);return accepted&&closed?0:2;
#endif
}

/* Default-OFF partial same-native-parent App-report FD observation. No activation,
 * adoption, mutation, cleanup or release. Actual Android qualifiers absent. */
#define HG_HELPER_REPORT_LIBRARY 1
#include "helper_owner_report.c"
enum ar_gate { AR_BEFORE_FD=1,AR_AFTER_FD };
struct ar_report {
    struct helper_report *helper;
    int (*qualify)(void *,enum ar_gate,const struct ar_report *,uid_t *,gid_t *,uint64_t);
    void *context;int attempted,unknown,observed;
    uint64_t read_end;int fd[7];struct stat before[7];
    unsigned char bytes[65536];size_t used;char sha256[65];
    struct hg_readonly *queries[2];unsigned query_count;
#ifdef HG_APP_REPORT_FD_FIXTURE
    const char *fixture_root,*fixture_package;
    void (*fixture_after_read)(struct ar_report *);
#endif
};
struct ar_report *ar_new(const char *nonce,uint64_t size,const char *sha,unsigned process_seconds,unsigned sample_seconds,
        int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
        int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),
        int (*qualify)(void *,enum ar_gate,const struct ar_report *,uid_t *,gid_t *,uint64_t),void *context){
    if(!qualify)return NULL;struct ar_report *r=calloc(1,sizeof(*r));if(!r)return NULL;
    for(unsigned i=0;i<7;++i)r->fd[i]=-1;r->qualify=qualify;r->context=context;
    r->helper=helper_report_new(nonce,size,sha,process_seconds,sample_seconds,eligibility,driver_qualification,context);
    if(!r->helper){free(r);return NULL;}return r;
}
static int ar_unknown(struct ar_report *r){
    if(r){r->unknown=1;r->observed=0;hgs_failed(r->helper->output->control);}return 0;
}
static int ar_end(struct ar_report *r){
    return r&&!r->unknown&&finite_deadline(r->read_end)&&bd_phase_end(r->helper->output->control->bridge,r->read_end)
        &&r->read_end<=r->helper->output->control->bridge->driver_end;
}
static int ar_native_package(struct ar_report *r){
    if(!ar_end(r)||r->query_count>=2)return 0;struct hg_readonly *q=hg_readonly_new();if(!q)return 0;
    r->queries[r->query_count++]=q; /* Retain even an unknown actual query. */
#ifdef HG_APP_REPORT_FD_FIXTURE
    q->fixture_root=r->fixture_package;q->fixture_mode="valid";
#endif
    if(hg_readonly_snapshot(q,HG_READ_APP_PRESENT,r->read_end)!=1||!ar_end(r))return 0;
    if(r->query_count==2){struct hg_readonly *first=r->queries[0];
        if(!read_stat_same(&first->file_stat,&q->file_stat,1)||first->dir_count!=q->dir_count)return 0;
        for(unsigned i=0;i<q->dir_count;++i)if(!read_stat_same(&first->dirs[i],&q->dirs[i],1))return 0;
    }
    return 1;
}
static int ar_mode(struct stat *s,unsigned index,uid_t uid,gid_t gid){
    if(index==6)return S_ISREG(s->st_mode)&&s->st_nlink==1&&s->st_uid==uid&&s->st_gid==gid
        &&(s->st_mode&07777)==0600&&s->st_size>0&&s->st_size<=65536;
    if(!S_ISDIR(s->st_mode)||(s->st_mode&07002))return 0;
    if(index>=4)return s->st_uid==uid&&s->st_gid==gid&&!(s->st_mode&022);
#ifdef HG_APP_REPORT_FD_FIXTURE
    return s->st_uid==geteuid()&&s->st_gid==getegid();
#else
    return (s->st_uid==0||s->st_uid==1000)&&(s->st_gid==0||s->st_gid==1000);
#endif
}
int ar_read(struct ar_report *r,uint64_t end){
    if(!r||r->attempted||r->unknown)return 0;r->attempted=1;r->read_end=end;
    if(!r->helper->valid||!ar_end(r))return ar_unknown(r);
    uid_t uid=0;gid_t gid=0;
    if(r->qualify(r->context,AR_BEFORE_FD,r,&uid,&gid,end)!=1||!ar_end(r))return ar_unknown(r);
#ifndef HG_APP_REPORT_FD_FIXTURE
    if(geteuid()!=0||getegid()!=0||uid<10000||uid>19999||gid!=uid)return ar_unknown(r);
#endif
    if(!ar_native_package(r))return ar_unknown(r);
    const char *base="/",*const names[]={"data","user","0","local.remoteandroid.direct.experiment","files","udp-app-last-report.json"};
#ifdef HG_APP_REPORT_FD_FIXTURE
    base=r->fixture_root;
#endif
    r->fd[0]=open(base,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(r->fd[0]<0||fstat(r->fd[0],&r->before[0])||!ar_mode(&r->before[0],0,uid,gid))return ar_unknown(r);
    for(unsigned i=1;i<7;++i){struct stat seen,again;
        if(!ar_end(r)||fstatat(r->fd[i-1],names[i-1],&seen,AT_SYMLINK_NOFOLLOW)||!ar_mode(&seen,i,uid,gid))return ar_unknown(r);
        r->fd[i]=openat(r->fd[i-1],names[i-1],O_RDONLY|O_NOFOLLOW|O_CLOEXEC|(i==6?0:O_DIRECTORY));
        if(r->fd[i]<0||fstat(r->fd[i],&r->before[i])||fstatat(r->fd[i-1],names[i-1],&again,AT_SYMLINK_NOFOLLOW)
                ||!read_stat_same(&seen,&r->before[i],1)||!read_stat_same(&seen,&again,1)||!ar_end(r))return ar_unknown(r);
    }
    size_t size=(size_t)r->before[6].st_size;
    while(r->used<size){if(!ar_end(r))return ar_unknown(r);
        ssize_t n=pread(r->fd[6],r->bytes+r->used,size-r->used,(off_t)r->used);
        if(n<=0)return ar_unknown(r);r->used+=(size_t)n;
    }
    unsigned char extra;ssize_t n=pread(r->fd[6],&extra,1,(off_t)size);
    if(n!=0||!ar_end(r))return ar_unknown(r);
    struct sha sha={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    update(&sha,r->bytes,r->used);digest(&sha,r->sha256);
    if(strcmp(r->sha256,r->helper->observed.App_report_sha256))return ar_unknown(r);
#ifdef HG_APP_REPORT_FD_FIXTURE
    if(r->fixture_after_read)r->fixture_after_read(r);
#endif
    if(!ar_native_package(r))return ar_unknown(r);
    uid_t after_uid=0;gid_t after_gid=0;
    if(r->qualify(r->context,AR_AFTER_FD,r,&after_uid,&after_gid,end)!=1||uid!=after_uid||gid!=after_gid||!ar_end(r))return ar_unknown(r);
    for(unsigned i=0;i<7;++i){struct stat held,named;
        if(fstat(r->fd[i],&held)||!read_stat_same(&r->before[i],&held,1))return ar_unknown(r);
        if(i&&(fstatat(r->fd[i-1],names[i-1],&named,AT_SYMLINK_NOFOLLOW)||!read_stat_same(&r->before[i],&named,1)))return ar_unknown(r);
    }
    if(!ar_end(r))return ar_unknown(r);r->observed=1;return 1;
}
#ifndef HG_APP_REPORT_FD_LIBRARY
int main(int argc,char **argv){(void)argv;if(argc!=1)return 2;
    puts("{\"event\":\"App_report_FD_prepared\",\"activation_available\":false,\"permission\":false,\"release\":false}");return 0;}
#endif

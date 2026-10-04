/* Host fixture only. Android metadata/permission callbacks are synthetic. */
#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_READ_FIXTURE 1
#define HG_NATIVE_DRIVER_FIXTURE 1
#define HG_HELPER_REPORT_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_report.c"
static int eligible(void *p,enum hg_gate g,const struct hg_lifecycle *l,uint64_t end){
    (void)p;return l&&l->owner&&g<=HG_DRIVER_CLOSED&&within(end);
}
static int driver_gate(void *p,enum bd_gate g,const struct bd_bridge *b,uint64_t end){
    const char *mode=p;return b&&within(end)&&(g!=BD_AFTER_DRIVER||strcmp(mode,"later_Attempt"));
}
static const char *fixture_read(void *p,enum hg_gate g,enum hg_read_kind k){
    (void)p;(void)g;return k==HG_READ_HELPER_ABSENT?"absent":"valid";
}
static void reply(const char *phase,uint64_t sequence,const char *status){
    printf("{\"event\":\"schedule\",\"phase\":\"%s\",\"sequence\":%" PRIu64 ",\"status\":\"%s\"}\n",phase,sequence,status);fflush(stdout);
}
int main(int argc,char **argv){
    unsigned char raw[65537];size_t length=0;
    if(argc==2&&!strcmp(argv[1],"--missing-gates-fixture")){
        struct helper_report *a=helper_report_new("d2d2d2d2d2d2d2d2d2d2d2d2",1,"abababababababababababababababababababababababababababababababab",30,5,NULL,driver_gate,NULL);
        struct helper_report *b=helper_report_new("d2d2d2d2d2d2d2d2d2d2d2d2",1,"abababababababababababababababababababababababababababababababab",30,5,eligible,NULL,NULL);
        return a||b?2:0;
    }
    if(argc==2&&!strcmp(argv[1],"--parse-fixture")){
        length=fread(raw,1,sizeof(raw),stdin);struct hj_doc d;struct hr_observed observed;
        int good=hr_observed_json(&d,raw,length,5,&observed);
        printf("{\"parsed\":%s,\"permission\":false,\"release\":false}\n",good?"true":"false");return good?0:2;
    }
    if(argc!=8||strcmp(argv[1],"--owned-fixture")||!hex(argv[4],24)||!hex(argv[5],64))return 2;
    int input=open(argv[7],O_RDONLY|O_NOFOLLOW);if(input<0)return 2;
    ssize_t n;while(length<sizeof(raw)&&(n=read(input,raw+length,sizeof(raw)-length))>0)length+=(size_t)n;
    close(input);if(!length||length>=sizeof(raw))return 2;raw[length]=0;
    struct rlimit limit;if(getrlimit(RLIMIT_NOFILE,&limit))return 2;
    if(limit.rlim_cur>1024){limit.rlim_cur=1024;if(setrlimit(RLIMIT_NOFILE,&limit))return 2;}
    struct stat st;if(stat(argv[2],&st))return 2;
    struct helper_report *r=helper_report_new(argv[4],sizeof("public host APK standin\n")-1,argv[5],30,5,
        eligible,driver_gate,argv[6]);if(!r)return 2;
    struct hgs_control *s=r->output->control;
    s->bridge->reads->fixture_root=argv[3];s->bridge->reads->fixture_read_mode=fixture_read;
    s->bridge->fixture_mode=!strcmp(argv[6],"missing_footer")?"missing_footer":"natural";
    s->bridge->fixture_numeric_result=(const char *)raw;
    if(!hgs_upload(s,argv[2],st.st_dev,st.st_ino,now_ms()+3000))goto unknown;
    reply("upload",1,"uploaded");if(!hgs_install(s,now_ms()+3000))goto unknown;
    reply("install",2,"installed");
    if(!strcmp(argv[6],"before_driver"))goto readback;
    if(!hgs_start(s,now_ms()+3000))goto unknown;reply("start",s->sequence-1,"started");
    while(s->phase==HGS_RUNNING){int result=hgs_poll(s,now_ms()+1000);if(result<0)goto unknown;
        reply("poll",s->sequence-1,result?"local_closed":"pending");}
    if(!strcmp(argv[6],"cancel_after_wait"))bd_request_cancel(0);
    if(!strcmp(argv[6],"expired")){s->bridge->driver_end=now_ms()+1;struct timespec wait={0,20000000};nanosleep(&wait,NULL);}
readback:;
    uint64_t end=now_ms()+3000;if(s->bridge->driver_end&&end>s->bridge->driver_end)end=s->bridge->driver_end;
    int good=helper_report_read(r,end),repeat=helper_report_read(r,end);
    printf("{\"event\":\"report_read\",\"parsed\":%s,\"repeat_refused\":%s,\"unknown\":%s,\"payload_sha256\":\"%s\",\"App_sha256\":\"%s\",\"permission\":false,\"App_FD_verified\":false,\"release\":false}\n",
        good?"true":"false",!repeat?"true":"false",r->unknown?"true":"false",r->output->collected?r->output->sha256:"",good?r->observed.App_report_sha256:"");
    fflush(stdout);if(!good)return 2;
    if(!hgs_done(s,now_ms()+3000))goto unknown;reply("done",3,"done");return 0;
unknown:reply("unknown",s->sequence,"unknown");return 2;
}

#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_READ_FIXTURE 1
#define HG_NATIVE_DRIVER_FIXTURE 1
#define HG_HELPER_CONTROL_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_control.c"

struct control_fixture_context {const char *mode;};
static int fixture_eligibility(void *p,enum hg_gate g,const struct hg_lifecycle *l,uint64_t end) {
    (void)p;return l&&l->owner&&g<=HG_DRIVER_CLOSED&&within(end);
}
static int fixture_driver(void *p,enum bd_gate g,const struct bd_bridge *b,uint64_t end) {
    struct control_fixture_context *c=p;
    return b&&within(end)&&strcmp(c->mode,"native_gate_missing")
        &&(g!=BD_AFTER_DRIVER||strcmp(c->mode,"later_Attempt"));
}
static const char *fixture_read(void *p,enum hg_gate g,enum hg_read_kind k) {
    (void)p;(void)g;return k==HG_READ_HELPER_ABSENT?"absent":"valid";
}
static void reply(const char *phase,uint64_t sequence,const char *status) {
    printf("{\"event\":\"native_control_schedule\",\"phase\":\"%s\",\"sequence\":%" PRIu64 ",\"status\":\"%s\",\"permission_ACK\":false}\n",phase,sequence,status);fflush(stdout);
}
int main(int argc,char **argv) {
    if (argc!=7||strcmp(argv[1],"--control-fixture")||!hex(argv[4],24)||!hex(argv[5],64)) return 2;
    struct rlimit r;if (getrlimit(RLIMIT_NOFILE,&r)) return 2;
    if (r.rlim_cur>1024) {r.rlim_cur=1024;if (setrlimit(RLIMIT_NOFILE,&r)) return 2;}
    struct stat st;if (stat(argv[2],&st)) return 2;
    struct control_fixture_context c={argv[6]};
    struct hgs_control *s=hgs_new(argv[4],sizeof("public host APK standin\n")-1,argv[5],30,5,
        fixture_eligibility,fixture_driver,&c);
    if (!s) return 2;
    s->bridge->reads->fixture_root=argv[3];s->bridge->reads->fixture_read_mode=fixture_read;
    s->bridge->fixture_mode=!strcmp(c.mode,"five_seconds")?"five_seconds":"natural";
    if (!hgs_upload(s,argv[2],st.st_dev,st.st_ino,now_ms()+3000)) goto unknown;
    if (!strcmp(c.mode,"late_response")) {struct timespec t={3,100000000};nanosleep(&t,NULL);}
    reply("upload",1,"uploaded");
    if (!strcmp(c.mode,"duplicate_event")) reply("upload",1,"uploaded");
    if (!strcmp(c.mode,"extra_event")) {puts("{\"event\":\"native_control_schedule\",\"phase\":[],\"sequence\":1,\"status\":{},\"permission_ACK\":false}");fflush(stdout);}
    if (!strcmp(c.mode,"stderr")) {fputs("host fixture only\n",stderr);fflush(stderr);}
    if (!strcmp(c.mode,"overflow")) {unsigned char x[4096];memset(x,'x',sizeof(x));for(int i=0;i<40;++i)write(1,x,sizeof(x));}
    if (!hgs_install(s,now_ms()+3000)) goto unknown;
    reply("install",2,"installed");
    if (!strcmp(c.mode,"no_response")) {for (;;) pause();}
    if (!hgs_start(s,now_ms()+3000)) goto unknown;
    reply("start",s->sequence-1,"started");
    while (s->phase==HGS_RUNNING) {
        int result=hgs_poll(s,now_ms()+1000);
        if (result<0) goto unknown;
        reply("poll",s->sequence-1,result?"local_closed":"pending");
    }
    if (!hgs_done(s,now_ms()+3000)) goto unknown;
    reply("done",3,"done");
    puts("{\"event\":\"host_control_fixture_footer\",\"host_fixture\":true,\"native_callbacks_synthetic\":true,\"driver_done\":true,\"scope_retained\":true,\"release_authorized\":false}");fflush(stdout);
    /* This test fixture exits and leaves ONLY its disposable host namespace.
     * No remote unknown-owner destructor, scope retirement or release proof.
     */
    return 0;
unknown:
    reply("unknown",s->sequence,"unknown");return 2;
}

#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_READ_FIXTURE 1
#define HG_BOUND_READ_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_read_gate.c"

struct host_context {const char *mode;int independent_calls;};
static int independent_synthetic(void *data,enum hg_gate gate,const struct hg_lifecycle *l,uint64_t end) {
    struct host_context *f=data;++f->independent_calls;
    if (!within(end)||!l->owner) return 0;
    if (!strcmp(f->mode,"missing_admission")&&gate==HG_BEFORE_SCOPE) return 0;
    if (!strcmp(f->mode,"missing_operator")&&gate==HG_BEFORE_INSTALL) return 0;
    if (!strcmp(f->mode,"missing_after_install")&&gate==HG_AFTER_INSTALL) return 0;
    return gate<=HG_AFTER_INSTALL; /* Every driver/cleanup/retirement gate unknown. */
}
static const char *host_read_mode(void *data,enum hg_gate gate,enum hg_read_kind kind) {
    struct host_context *f=data;
    if (gate==HG_AFTER_INSTALL) {
        if (!strcmp(f->mode,"later_active")) return "active_App";
        if (!strcmp(f->mode,"later_package")) return "later_path";
        if (!strcmp(f->mode,"query_timeout")) return "ignore_TERM";
        if (!strcmp(f->mode,"package_unknown")&&kind==HG_READ_HELPER_PRESENT) return "absent";
    }
    return kind==HG_READ_HELPER_ABSENT?"absent":"valid";
}
int main(int argc,char **argv) {
    if (argc!=6||strcmp(argv[1],"--fixture")||!hex(argv[5],64)) return 2;
    const char *m=argv[4],*modes[]={"natural","missing_admission","missing_operator","missing_after_install",
        "later_active","later_package","query_timeout","package_unknown","null_callback"};int allowed=0;
    for (unsigned i=0;i<sizeof(modes)/sizeof(modes[0]);++i) if (!strcmp(m,modes[i])) allowed=1;
    if (!allowed) return 2;
    struct rlimit limit;if (getrlimit(RLIMIT_NOFILE,&limit)) return 2;
    if (limit.rlim_cur>1024) {limit.rlim_cur=1024;if (setrlimit(RLIMIT_NOFILE,&limit)) return 2;}
    struct host_context f={m,0};struct stat base;if (stat(argv[2],&base)) return 2;
    struct bound_read_gate *g=bound_read_new("c0c0c0c0c0c0c0c0c0c0c0c0",sizeof("public host APK standin\n")-1,argv[5],
        !strcmp(m,"null_callback")?NULL:independent_synthetic,&f);
    if (!g) {puts("{\"constructed\":false,\"actual_queries\":0,\"scope_created\":false,\"release_authorized\":false}");return 2;}
    g->fixture_root=argv[3];g->fixture_read_mode=host_read_mode;
    int uploaded=bound_read_upload(g,argv[2],base.st_dev,base.st_ino,now_ms()+3000);
    int installed=uploaded&&bound_read_install(g,now_ms()+3000);
    int pending=g->pending_read!=NULL,unreaped=pending&&g->pending_read->child&&!g->pending_read->reaped;
    int close_refused=!hg_lifecycle_close(g->lifecycle);
    printf("{\"constructed\":true,\"upload_verified\":%s,\"install_host_standin_qualified\":%s,\"completed_native_reads\":%d,\"independent_calls\":%d,\"unknown\":%s,\"actual_query_retained\":%s,\"query_unreaped\":%s,\"owner_close_refused\":%s,\"scope_created\":%s,\"actual_helper_PM_standin_reaped\":%s,\"Android_outputs_and_independent_gates_synthetic\":true,\"actual_Android_PM\":false,\"actual_coordinator_driver\":false,\"complete_live_bridge\":false,\"uninstall_or_retirement\":false,\"release_authorized\":false}\n",
        uploaded?"true":"false",installed?"true":"false",g->completed_native_reads,f.independent_calls,
        g->unknown?"true":"false",pending?"true":"false",unreaped?"true":"false",close_refused?"true":"false",
        g->lifecycle->owner->possible_scope?"true":"false",g->lifecycle->owner->child_reaped?"true":"false");
    /* Sole actual host fixture objects only. No Android unknown cleanup. */
    if (pending) {hg_readonly_stop_owned(g->pending_read,700);hg_readonly_close(g->pending_read);free(g->pending_read);}
    hg_owner_close_handles(g->lifecycle->owner);free(g->lifecycle->owner);free(g->lifecycle);free(g);
    return installed?0:2;
}

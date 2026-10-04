/* Actual host own-scope/owned-child fixture. Every Android qualifier is
 * explicitly synthetic. No device, PM-server, guest, App or lease operation.
 */
#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_LIFECYCLE_FIXTURE 1
#define HG_HELPER_LIFECYCLE_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_lifecycle.c"

struct fixture_context {const char *mode;struct hg_owner *driver;int driver_complete;};
static struct fixture_context *context;
void hg_fixture_after_file_unlink(struct hg_owner *o) {
    if (strcmp(context->mode,"partial_foreign")) return;
    int f=openat(o->stage,"foreign",O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
    if (f>=0) {write(f,"foreign",7);close(f);}
}
static int host_driver(struct fixture_context *f,const struct hg_owner *helper) {
    struct hg_owner *d=f->driver;
    struct sigaction policy;
    if (sigaction(SIGCHLD,NULL,&policy)||policy.sa_handler!=SIG_DFL||(policy.sa_flags&SA_NOCLDWAIT))
        return 0;
    int out[2],err[2];if (!make_pipe(out)) return 0;
    if (!make_pipe(err)) {close(out[0]);close(out[1]);return 0;}
    if (!strcmp(f->mode,"driver_pipe_unknown")) d->hold_fixture_pipe=dup(err[1]);
    pid_t p=fork();
    if (p<0) {close(out[0]);close(out[1]);close(err[0]);close(err[1]);return 0;}
    if (!p) {
        close(out[0]);close(err[0]);close(helper->base);close(helper->parent);close(helper->stage);close(helper->reader);
        for (int i=0;i<2;++i) if (helper->pipes[i]>=0) close(helper->pipes[i]);
        if (d->hold_fixture_pipe>=0) close(d->hold_fixture_pipe);
        int null=open("/dev/null",O_RDONLY);
        if (null<0||dup2(null,0)<0||dup2(out[1],1)<0||dup2(err[1],2)<0) _exit(124);
        if (null>2) close(null);if (out[1]>2) close(out[1]);if (err[1]>2) close(err[1]);
        write(1,"Success\n",8);_exit(0);
    }
    d->child=p;d->pipes[0]=out[0];d->pipes[1]=err[0];close(out[1]);close(err[1]);
    return hg_owner_pm_wait(d,100)&&natural_child(d);
}
static int synthetic_Android_callback(void *data,enum hg_gate gate,const struct hg_owner *o) {
    struct fixture_context *f=data;
    if (gate==HG_BEFORE_SCOPE&&!strcmp(f->mode,"missing_scope_qualification")) return 0;
    if (gate==HG_BEFORE_INSTALL&&!strcmp(f->mode,"missing_operator")) return 0;
    if (gate==HG_AFTER_INSTALL&&!strcmp(f->mode,"installed_package_unknown")) return 0;
    if (gate==HG_DRIVER_CLOSED) {
        if (!strcmp(f->mode,"wire_driver_only")) return 0;
        f->driver_complete=host_driver(f,o);return f->driver_complete;
    }
    if (gate==HG_BEFORE_UNINSTALL&&(!strcmp(f->mode,"later_Attempt")
            ||!strcmp(f->mode,"later_package")||!strcmp(f->mode,"input_unknown"))) return 0;
    if (gate==HG_AFTER_UNINSTALL&&!strcmp(f->mode,"absence_unknown")) return 0;
    if (gate!=HG_BEFORE_RETIRE) return 1;
    if (!strcmp(f->mode,"privileged_writer_unknown")) return 0;
    if (!strcmp(f->mode,"cancel")) hg_owner_request_cancel(SIGTERM);
    if (!strcmp(f->mode,"mode")) fchmod(o->reader,0400);
    if (!strcmp(f->mode,"contents")) {
        int w=openat(o->stage,"owned.apk",O_WRONLY|O_NOFOLLOW);
        if (w>=0) {pwrite(w,"changed",7,0);close(w);}
    }
    if (!strcmp(f->mode,"file_replaced")) {
        renameat(o->stage,"owned.apk",o->stage,"old.apk");
        int w=openat(o->stage,"owned.apk",O_WRONLY|O_CREAT|O_EXCL,0600);
        if (w>=0) {write(w,"foreign",7);close(w);}
    }
    if (!strcmp(f->mode,"parent_replaced")) {
        renameat(o->base,o->name,o->base,"old-parent");mkdirat(o->base,o->name,0700);
    }
    if (!strcmp(f->mode,"foreign")) {
        int w=openat(o->stage,"foreign",O_WRONLY|O_CREAT|O_EXCL,0600);
        if (w>=0) {write(w,"foreign",7);close(w);}
    }
    if (!strcmp(f->mode,"file_link")) linkat(o->stage,"owned.apk",o->stage,"other",0);
    return 1;
}
int main(int argc,char **argv) {
    if (argc!=9||strcmp(argv[1],"--fixture")||!hex(argv[3],24)||!hex(argv[7],64)) return 2;
    uint64_t dev,ino,size;if (!number(argv[4],&dev)||!number(argv[5],&ino)||!number(argv[6],&size)) return 2;
    const char *modes[]={"natural","missing_scope_qualification","missing_operator","installed_package_unknown",
        "wire_driver_only","driver_pipe_unknown","later_Attempt","later_package","input_unknown",
        "absence_unknown","privileged_writer_unknown","cancel","mode","contents","file_replaced",
        "parent_replaced","foreign","file_link","partial_foreign","second_call",
        "install_failure","uninstall_failure","uninstall_ignore_TERM",NULL};
    int allowed=0;for (int i=0;modes[i];++i) if (!strcmp(argv[8],modes[i])) allowed=1;
    if (!allowed) return 2;
    struct fixture_context f={argv[8],hg_owner_new(),0};context=&f;
    struct hg_lifecycle *l=hg_lifecycle_new(0,argv[3],size,argv[7],synthetic_Android_callback,&f);
    if (!l||!f.driver) return 2;
    struct sigaction sa;memset(&sa,0,sizeof(sa));sa.sa_handler=hg_owner_request_cancel;sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM,&sa,NULL);sigaction(SIGINT,&sa,NULL);
    int upload=hg_lifecycle_upload(l,argv[2],dev,ino,now_ms()+500);
    int installed=upload&&hg_lifecycle_install(l,now_ms()+300,!strcmp(argv[8],"install_failure")?"failure":"natural");
    int driver=installed&&hg_lifecycle_driver_done(l,now_ms()+500);
    const char *action=!strcmp(argv[8],"uninstall_failure")?"failure":
        !strcmp(argv[8],"uninstall_ignore_TERM")?"ignoreTERM":"natural";
    int uninstalled=driver&&hg_lifecycle_uninstall(l,now_ms()+300,action);
    int retired=uninstalled&&hg_lifecycle_retire(l,now_ms()+500),second_blocked=0;
    if (retired&&!strcmp(argv[8],"second_call")) {
        mkdirat(l->owner->base,l->owner->name,0700);
        second_blocked=!hg_lifecycle_retire(l,now_ms()+300);
    }
    int closed=hg_lifecycle_close(l);
    printf("{\"event\":\"helper_lifecycle_fixture_footer\",\"host_fixture\":true,\"upload_verified\":%s,\"install_standin_verified\":%s,\"driver_standin_verified\":%s,\"uninstall_standin_verified\":%s,\"last_PM_reaped\":%s,\"last_PM_signal\":%d,\"file_removed\":%s,\"stage_removed\":%s,\"parent_removed\":%s,\"host_same_owner_retirement_completed\":%s,\"second_call_blocked\":%s,\"bound_close_completed\":%s,\"unknown_keeps_handles\":%s,\"Android_qualifiers_synthetic\":true,\"actual_Android_PM_or_scope_operations\":0,\"complete_live_binding_implemented\":false,\"phone_operator_server_lease_verified\":false,\"idle_for_release\":false,\"release_authorized\":false}\n",
        upload?"true":"false",installed?"true":"false",driver?"true":"false",uninstalled?"true":"false",
        l->owner->child_reaped?"true":"false",l->owner->child_reaped&&WIFSIGNALED(l->owner->child_status)?WTERMSIG(l->owner->child_status):0,
        l->file_removed?"true":"false",l->stage_removed?"true":"false",l->parent_removed?"true":"false",
        retired?"true":"false",second_blocked?"true":"false",closed?"true":"false",
        !closed&&(l->owner->base>=0||l->owner->child)?"true":"false");
    /* Only fixture process teardown of actual reaped host clients. Production
     * hg_lifecycle_close refused UNKNOWN above and retains actual objects.
     * Host temporary directory cleanup is not remote scope/lease cleanup.
     */
    if (l->owner->child&&!l->owner->child_reaped) return 3;
    hg_owner_close_handles(f.driver);if (!closed) hg_owner_close_handles(l->owner);
    if (!closed) close(0);free(l->owner);free(l);free(f.driver);
    return retired&&(closed||second_blocked)?0:2;
}

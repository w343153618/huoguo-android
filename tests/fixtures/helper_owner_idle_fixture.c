/* Actual host retained native parent/control/scope and sole forked driver.
 * Android/operator/package/Attempt gates remain synthetic. No phone operation.
 */
#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_LIFECYCLE_FIXTURE 1
#define HG_HELPER_IDLE_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_idle.c"

struct fixture {const char *mode;struct hg_owner *driver;const struct hg_owner *helper;
    int emitted,services,started;};
void hg_fixture_after_file_unlink(struct hg_owner *o) {(void)o;}
static int start_driver(struct fixture *f,const struct hg_owner *helper) {
    if (f->started) return 0;
    struct sigaction policy;
    if (sigaction(SIGCHLD,NULL,&policy)||policy.sa_handler!=SIG_DFL||(policy.sa_flags&SA_NOCLDWAIT)) return 0;
    int out[2],err[2];if (!make_pipe(out)) return 0;
    if (!make_pipe(err)) {close(out[0]);close(out[1]);return 0;}
    if (!strcmp(f->mode,"driver_EOF_unknown")) f->driver->hold_fixture_pipe=dup(err[1]);
    pid_t p=fork();
    if (p<0) {close(out[0]);close(out[1]);close(err[0]);close(err[1]);return 0;}
    if (!p) {
        close(out[0]);close(err[0]);close(helper->base);close(helper->parent);close(helper->stage);close(helper->reader);
        for (int i=0;i<2;++i) if (helper->pipes[i]>=0) close(helper->pipes[i]);
        if (f->driver->hold_fixture_pipe>=0) close(f->driver->hold_fixture_pipe);
        int null=open("/dev/null",O_RDONLY);
        if (null<0||dup2(null,0)<0||dup2(out[1],1)<0||dup2(err[1],2)<0) _exit(124);
        if (null>2) close(null);if (out[1]>2) close(out[1]);if (err[1]>2) close(err[1]);
        signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);
        unsigned delay=!strcmp(f->mode,"five_seconds")?5000:80;
        struct timespec t={(time_t)(delay/1000),(long)(delay%1000)*1000000};
        while (nanosleep(&t,&t)&&errno==EINTR) {}
        if (!strcmp(f->mode,"driver_nonzero")) _exit(7);
        if (!strcmp(f->mode,"driver_overflow")) {
            unsigned char b[1024];memset(b,'x',sizeof(b));
            for (int i=0;i<80;++i) if (write(2,b,sizeof(b))!=(ssize_t)sizeof(b)) _exit(125);
        }
        write(1,"Success\n",8);_exit(0);
    }
    f->driver->child=p;f->driver->pipes[0]=out[0];f->driver->pipes[1]=err[0];
    close(out[1]);close(err[1]);f->helper=helper;f->started=1;return 1;
}
static int service(void *context,uint64_t end) {
    struct fixture *f=context;f->services++;
    if (!f->started||!f->helper||!within(end)||!strcmp(f->mode,"service_unknown")) return 0;
    if (!child_reap(f->driver)) return 0;
    drain(f->driver,0);drain(f->driver,1);
    if (!strcmp(f->mode,"scope_changed")) fchmod(f->helper->reader,0400);
    if (!f->emitted&&f->driver->child_reaped) {
        /* A host bridge uses this observation only to schedule its next frame.
         * Native DRIVER_CLOSED still checks actual objects, not this event.
         */
        puts("{\"event\":\"host_driver_reaped\",\"request_may_be_scheduled\":true,\"operation_or_permission_ACK\":false}");
        fflush(stdout);f->emitted=1;
    }
    return 1;
}
static int qualify(void *context,enum hg_gate gate,const struct hg_owner *o) {
    struct fixture *f=context;
    if (gate==HG_AFTER_INSTALL) return start_driver(f,o);
    if (gate==HG_DRIVER_CLOSED) return f->helper==o&&natural_child(f->driver)&&strcmp(f->mode,"later_Attempt");
    return 1; /* Explicitly synthetic Android/operator/package gates. */
}
int main(int argc,char **argv) {
    if (argc!=9||strcmp(argv[1],"--fixture")||!hex(argv[3],24)||!hex(argv[7],64)) return 2;
    const char *modes[]={"natural","five_seconds","early_frame","partial_frame","control_EOF",
        "ceiling","service_unknown","scope_changed","driver_EOF_unknown","driver_nonzero",
        "driver_overflow","later_Attempt","cancel","repeat_idle","contract","long_deadline",NULL};
    int allowed=0;for (int i=0;modes[i];++i) if (!strcmp(argv[8],modes[i])) allowed=1;
    uint64_t dev,ino,size;
    if (!allowed||!number(argv[4],&dev)||!number(argv[5],&ino)||!number(argv[6],&size)) return 2;
    struct fixture f={argv[8],hg_owner_new(),NULL,0,0,0};
    struct hg_lifecycle *l=hg_lifecycle_new(0,argv[3],size,argv[7],qualify,&f);
    if (!strcmp(f.mode,"contract")) {
        int invalid=l&&!hg_idle_new(l,29,service,&f)&&!hg_idle_new(l,3601,service,&f)
            &&!hg_idle_new(l,30,NULL,&f);
        struct hg_idle *first=hg_idle_new(l,30,service,&f);
        int bound=first&&first->ceiling==l->constructed_ms+30000&&!hg_idle_new(l,3600,service,&f);
        printf("{\"event\":\"idle_constructor_contract\",\"invalid_refused\":%s,\"original_ceiling_once\":%s,\"scope_created\":false}\n",
            invalid?"true":"false",bound?"true":"false");return invalid&&bound?0:2;
    }
    struct hg_idle *w=hg_idle_new(l,30,service,&f);if (!w||!f.driver) return 2;
    struct sigaction sa;memset(&sa,0,sizeof(sa));sa.sa_handler=hg_owner_request_cancel;sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM,&sa,NULL);sigaction(SIGINT,&sa,NULL);
    int uploaded=hg_lifecycle_upload(l,argv[2],dev,ino,now_ms()+1000);
    int installed=uploaded&&hg_lifecycle_install(l,now_ms()+1000,"natural");
    puts("{\"event\":\"host_helper_installed\",\"operation_or_permission_ACK\":false}");fflush(stdout);
    if (!strcmp(f.mode,"ceiling")) w->ceiling=now_ms()+150; /* Host fixture only, shorten not widen. */
    uint64_t start=now_ms();int readable=installed&&hg_idle_until_readable(w);
    int driver_done=readable&&hg_idle_driver_done(w,now_ms()+(!strcmp(f.mode,"long_deadline")?3001:250));
    int repeat=0;
    if (driver_done&&!strcmp(f.mode,"repeat_idle")) repeat=!hg_idle_until_readable(w);
    if (driver_done&&!repeat) {puts("{\"event\":\"host_driver_done_checked\",\"operation_or_permission_ACK\":false}");fflush(stdout);}
    int uninstalled=driver_done&&!repeat&&hg_lifecycle_uninstall(l,now_ms()+500,"natural");
    if (uninstalled) {puts("{\"event\":\"host_helper_uninstalled_checked\",\"operation_or_permission_ACK\":false}");fflush(stdout);}
    int retired=uninstalled&&hg_lifecycle_retire(l,now_ms()+500),closed=hg_lifecycle_close(l);
    printf("{\"event\":\"helper_idle_fixture_footer\",\"host_fixture\":true,\"idle_ms\":%llu,\"services\":%d,\"driver_reaped\":%s,\"driver_dual_EOF\":%s,\"driver_signals\":%d,\"driver_done\":%s,\"readable\":%s,\"retired\":%s,\"closed\":%s,\"repeat_refused\":%s,\"unknown_retains_scope_and_control\":%s,\"Android_qualifiers_synthetic\":true,\"complete_live_binding_implemented\":false,\"release_authorized\":false}\n",
        (unsigned long long)(now_ms()-start),f.services,f.driver->child_reaped?"true":"false",
        f.driver->pipe_EOF[0]&&f.driver->pipe_EOF[1]?"true":"false",f.driver->term_calls+f.driver->kill_calls,
        driver_done?"true":"false",readable?"true":"false",retired?"true":"false",closed?"true":"false",
        repeat?"true":"false",!closed&&l->owner->base>=0&&fcntl(0,F_GETFD)>=0?"true":"false");fflush(stdout);
    /* Fixture-only cleanup of the sole actual unreaped fork result. It is not
     * production normal cleanup, retirement or a server reservation release.
     */
    if (f.driver->child&&!f.driver->child_reaped) {
        kill(f.driver->child,SIGTERM);int status;
        if (waitpid(f.driver->child,&status,0)==f.driver->child) {f.driver->child_reaped=1;f.driver->child_status=status;}
    }
    if (f.driver->hold_fixture_pipe>=0) close(f.driver->hold_fixture_pipe);
    hg_owner_close_handles(f.driver);if (!closed) hg_owner_close_handles(l->owner);
    if (!closed) close(0);free(f.driver);free(l->owner);free(l);free(w);
    return retired&&closed?0:2;
}

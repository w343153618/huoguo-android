#define HG_HELPER_OWNER_FIXTURE 1
#define HG_HELPER_CHANNEL_FIXTURE 1
#define HG_HELPER_READ_FIXTURE 1
#define HG_NATIVE_DRIVER_FIXTURE 1
#define HG_NATIVE_DRIVER_LIBRARY 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_native_driver.c"

struct fixture_context { const char *mode;int independent_calls,driver_before,driver_after; };
static int eligibility_synthetic(void *data,enum hg_gate gate,const struct hg_lifecycle *l,uint64_t end) {
    struct fixture_context *c=data;++c->independent_calls;
    return l&&l->owner&&within(end)&&gate<=HG_DRIVER_CLOSED;
}
static int driver_synthetic(void *data,enum bd_gate gate,const struct bd_bridge *b,uint64_t end) {
    struct fixture_context *c=data;
    if (!b||!b->reads||!within(end)) return 0;
    if (gate==BD_BEFORE_DRIVER) {
        ++c->driver_before;return strcmp(c->mode,"missing_operator")!=0;
    }
    ++c->driver_after;
    return strcmp(c->mode,"later_Attempt")&&strcmp(c->mode,"input_unknown")
        &&strcmp(c->mode,"codec_unknown")&&strcmp(c->mode,"missing_cleanup");
}
static const char *read_synthetic(void *data,enum hg_gate gate,enum hg_read_kind kind) {
    struct bd_bridge *b=data;struct fixture_context *c=b->context;
    if (gate==HG_DRIVER_CLOSED&&!strcmp(c->mode,"later_package")) return "later_path";
    return kind==HG_READ_HELPER_ABSENT?"absent":"valid";
}
static void event(const char *name) {printf("{\"event\":\"%s\"}\n",name);fflush(stdout);}
int main(int argc,char **argv) {
    if (argc!=6||strcmp(argv[1],"--fixture")||!hex(argv[5],64)) return 2;
    const char *m=argv[4],*modes[]={"natural","five_seconds","missing_operator","missing_cleanup",
        "later_Attempt","input_unknown","codec_unknown","later_package","early_done",
        "nonzero","stderr","overflow","missing_footer","duplicate_footer","held_EOF",
        "ignore_TERM","cancel","control_EOF","trailing_request","null_gate","high_FD",
        "insufficient_ceiling","cancel_then_natural","late_first_poll"};int allowed=0;
    for (unsigned i=0;i<sizeof(modes)/sizeof(modes[0]);++i) if (!strcmp(m,modes[i])) allowed=1;
    if (!allowed) return 2;
    struct rlimit limit;if (getrlimit(RLIMIT_NOFILE,&limit)) return 2;
    int high=-1;
    if (!strcmp(m,"high_FD")) {
        int fd=open("/dev/null",O_RDONLY);high=fd>=0?fcntl(fd,F_DUPFD,1600):-1;
        if (fd>=0) close(fd);if (high<0) return 2;
    }
    if (limit.rlim_cur>1024) {limit.rlim_cur=1024;if (setrlimit(RLIMIT_NOFILE,&limit)) return 2;}
    struct fixture_context c={m,0,0,0};struct stat s;if (stat(argv[2],&s)) return 2;
    struct bd_bridge *b=bd_new("d0d0d0d0d0d0d0d0d0d0d0d0",sizeof("public host APK standin\n")-1,argv[5],30,5,
        eligibility_synthetic,!strcmp(m,"null_gate")?NULL:driver_synthetic,&c);
    if (!b) {puts("{\"constructed\":false,\"started\":false,\"release_authorized\":false}");return 2;}
    b->reads->fixture_root=argv[3];b->reads->fixture_read_mode=read_synthetic;
    b->fixture_mode=m;b->fixture_high_fd=high;
    if (!strcmp(m,"cancel")||!strcmp(m,"control_EOF")||!strcmp(m,"trailing_request")
            ||!strcmp(m,"early_done")||!strcmp(m,"cancel_then_natural")
            ||!strcmp(m,"late_first_poll")) b->fixture_mode="five_seconds";
    int upload=bd_upload(b,argv[2],s.st_dev,s.st_ino,now_ms()+3000);
    int install=upload&&bd_install(b,now_ms()+3000);
    if (install&&!strcmp(m,"insufficient_ceiling")) b->ceiling=now_ms()+1000;
    int started=install&&bd_start(b,now_ms()+(!strcmp(m,"insufficient_ceiling")?500:3000)),closed=0,done=0,polls=0;
    if (started) {
        event("driver_started");
        if (!strcmp(m,"late_first_poll")) {
            /* Own host fixture shortens its deadline; actual child runs five
             * seconds and is first collected after expiry. No fake clock.
             */
            b->driver_end=now_ms()+100;struct timespec t={5,100000000};nanosleep(&t,NULL);
        }
        if (!strcmp(m,"early_done")) bd_done(b,now_ms()+3000);
        uint64_t end=now_ms()+6500;
        do {
            ++polls;
            if ((!strcmp(m,"ignore_TERM")||!strcmp(m,"cancel")||!strcmp(m,"cancel_then_natural"))&&polls==3) bd_request_cancel(0);
            int r=bd_poll(b,now_ms()+100);
            if (r==1) {closed=1;event("driver_local_closed");done=bd_done(b,now_ms()+3000);break;}
            if (r<0) {
                if (strcmp(m,"cancel_then_natural")) break;
                if (b->driver->reaped&&b->driver->eof[0]&&b->driver->eof[1]) {
                    done=bd_done(b,now_ms()+3000);break;
                }
                struct timespec t={0,10000000};nanosleep(&t,NULL);
            }
            if (!strcmp(m,"held_EOF")&&polls==3) {bd_failed(b);break;}
        } while (within(end));
    }
    int unreaped=b->driver&&b->driver->child&&!b->driver->reaped;
    int eof=b->driver&&b->driver->eof[0]&&b->driver->eof[1];
    uint64_t elapsed=now_ms()-b->reads->lifecycle->constructed_ms;
    printf("{\"constructed\":true,\"started\":%s,\"actual_native_child_reaped\":%s,\"actual_dual_EOF\":%s,\"local_driver_closed\":%s,\"driver_done_qualified\":%s,\"unknown\":%s,\"unreaped_retained\":%s,\"scope_retained\":%s,\"driver_signal_calls\":0,\"poll_calls\":%d,\"elapsed_ms\":%" PRIu64 ",\"driver_before_calls\":%d,\"driver_after_calls\":%d,\"actual_Android_outputs_and_gates_synthetic\":true,\"actual_Mac_coordinator_bridge\":false,\"actual_Android_PM_driver\":false,\"production_activation_available\":false,\"uninstall_retirement_or_release\":false,\"release_authorized\":false}\n",
        started?"true":"false",b->driver&&b->driver->reaped?"true":"false",eof?"true":"false",
        closed?"true":"false",done?"true":"false",b->unknown||b->reads->unknown?"true":"false",unreaped?"true":"false",
        b->reads->lifecycle->owner->possible_scope?"true":"false",polls,elapsed,c.driver_before,c.driver_after);
    fflush(stdout);
    /* Only fixture's sole actual host child; production has NO such method.
     * Host temporary teardown below is not remote unknown cleanup/release.
     */
    if (b->driver) {
        if (b->driver->fixture_hold>=0) close(b->driver->fixture_hold);
        if (!b->driver->reaped&&b->driver->child) {
            kill(b->driver->child,SIGKILL);while (waitpid(b->driver->child,&b->driver->status,0)<0&&errno==EINTR) {}
            b->driver->reaped=1;
        }
        for (int i=0;i<2;++i) if (b->driver->pipes[i]>=0) close(b->driver->pipes[i]);
        free(b->driver);
    }
    if (b->reads->pending_read) {hg_readonly_stop_owned(b->reads->pending_read,700);hg_readonly_close(b->reads->pending_read);free(b->reads->pending_read);}
    hg_owner_close_handles(b->reads->lifecycle->owner);
    free(b->reads->lifecycle->owner);free(b->reads->lifecycle);free(b->reads);free(b);
    if (high>=0) close(high);return done?0:2;
}

/* Default-OFF native-owned instrumentation child composition. Inert production
 * CLI, no Android activation. Host/controller messages schedule phases only.
 * The native parent creates the fixed driver itself, never adopts a Mac PID,
 * ADB exit receipt, JSON object or a serialized qualification boolean.
 */
#define HG_BOUND_READ_LIBRARY 1
#include "helper_owner_read_gate.c"

enum bd_gate { BD_BEFORE_DRIVER=1, BD_AFTER_DRIVER };
struct bd_bridge {
    struct bound_read_gate *reads;
    struct hg_readonly *driver; /* sole fork/wait/dual-pipe owner; no query API */
    int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t);
    int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t);
    void *context;
    unsigned seconds;
    uint64_t ceiling,driver_end;
    int possible_start,started,done,unknown;
#ifdef HG_NATIVE_DRIVER_FIXTURE
    const char *fixture_mode;
    const char *fixture_numeric_result; /* host fixture only; never production */
    int fixture_high_fd;
#endif
};
static volatile sig_atomic_t bd_cancelled=0;
void bd_request_cancel(int signum) { (void)signum;bd_cancelled=1; }
static int bd_failed(struct bd_bridge *b) {
    if (b) {b->unknown=1;bound_read_failed(b->reads);}return 0;
}
static int bd_control_clear(struct bd_bridge *b) {
    if (!b||!b->reads||!b->reads->lifecycle) return 0;
    struct pollfd p={b->reads->lifecycle->channel.input,POLLIN,0};
    int n=poll(&p,1,0);return n==0;
}
static int bd_local_closed(struct bd_bridge *b) {
    struct hg_readonly *d=b?b->driver:NULL;
    return d&&read_natural(d)&&WEXITSTATUS(d->status)==0&&!d->used[1]
        &&!memchr(d->output[0],0,d->used[0]);
}
static int bd_footer(struct bd_bridge *b) {
    /* Only a local child-output check, not current Attempt/normal cleanup,
     * Android PM quiescence or authority. Independent gates remain required.
     */
    if (!bd_local_closed(b)) return 0;
    struct hg_readonly *d=b->driver;unsigned codes=0,results=0;
    size_t begin=0,last=0;
    for (size_t i=0;i<d->used[0];++i) if (d->output[0][i]=='\n') {
        const unsigned char *p=d->output[0]+begin;size_t n=i-begin;
        if (n>=22&&!memcmp(p,"INSTRUMENTATION_CODE: ",22)) {
            if (n!=24||memcmp(p+22,"-1",2)) return 0;
            ++codes;last=i+1;
        }
        if (n>=39&&!memcmp(p,"INSTRUMENTATION_RESULT: numeric_result=",39)) ++results;
        if ((n>=23&&!memcmp(p,"INSTRUMENTATION_FAILED:",23))
            ||(n>=24&&!memcmp(p,"INSTRUMENTATION_ABORTED:",24))) return 0;
        begin=i+1;
    }
    return begin==d->used[0]&&last==d->used[0]&&codes==1&&results==1;
}
static int bd_independent(void *data,enum hg_gate gate,const struct hg_lifecycle *l,uint64_t end) {
    struct bd_bridge *b=data;
    if (!b||b->unknown||bd_cancelled||!b->reads||l!=b->reads->lifecycle
        ||end>b->ceiling||!within(end)||!b->eligibility) return 0;
    if (gate==HG_DRIVER_CLOSED&&(!bd_footer(b)||!bd_control_clear(b)
            ||!b->driver_qualification
            ||b->driver_qualification(b->context,BD_AFTER_DRIVER,b,end)!=1)) return 0;
    return b->eligibility(b->context,gate,l,end)==1&&within(end)&&!bd_cancelled;
}
struct bd_bridge *bd_new(const char *nonce,uint64_t size,const char *sha,
        unsigned process_seconds,unsigned sample_seconds,
        int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
        int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),void *context) {
    if (!eligibility||!driver_qualification||process_seconds<30||process_seconds>3600
            ||sample_seconds<1||sample_seconds>10) return NULL;
    struct bd_bridge *b=calloc(1,sizeof(*b));if (!b) return NULL;
    b->eligibility=eligibility;b->driver_qualification=driver_qualification;b->context=context;
    b->seconds=sample_seconds;
#ifdef HG_NATIVE_DRIVER_FIXTURE
    b->fixture_high_fd=-1;
#endif
    b->reads=bound_read_new(nonce,size,sha,bd_independent,b);
    if (!b->reads) {free(b);return NULL;}
    uint64_t anchor=b->reads->lifecycle->constructed_ms;
    if (UINT64_MAX-anchor<(uint64_t)process_seconds*1000) return bd_failed(b),b;
    b->ceiling=anchor+(uint64_t)process_seconds*1000;return b;
}
static int bd_phase_end(struct bd_bridge *b,uint64_t end) {
    return b&&!b->unknown&&!bd_cancelled&&finite_deadline(end)&&end<=b->ceiling;
}
int bd_upload(struct bd_bridge *b,const char *base,uint64_t dev,uint64_t ino,uint64_t end) {
    return bd_phase_end(b,end)&&bound_read_upload(b->reads,base,dev,ino,end)?1:bd_failed(b);
}
int bd_install(struct bd_bridge *b,uint64_t end) {
    return bd_phase_end(b,end)&&bound_read_install(b->reads,end)?1:bd_failed(b);
}
static void bd_exec_fixed(struct bd_bridge *b,int a[2],int c[2]) {
    int null=open("/dev/null",O_RDONLY);
    if (null<0||dup2(null,0)<0||dup2(a[1],1)<0||dup2(c[1],2)<0||!read_close_inherited()) _exit(124);
    signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);
#ifdef HG_NATIVE_DRIVER_FIXTURE
    const char *m=b->fixture_mode;
    if (b->fixture_high_fd>=0&&(fcntl(b->fixture_high_fd,F_GETFD)!=-1||errno!=EBADF)) _exit(123);
    if (!strcmp(m,"ignore_TERM")) {signal(SIGTERM,SIG_IGN);for (;;) pause();}
    if (!strcmp(m,"nonzero")) _exit(7);
    if (!strcmp(m,"stderr")) {write(2,"fixture failure\n",16);_exit(0);}
    if (!strcmp(m,"overflow")) {unsigned char x[4096];memset(x,'x',sizeof(x));
        for (int j=0;j<40;++j) if (write(1,x,sizeof(x))!=(ssize_t)sizeof(x)) _exit(125);_exit(0);}
    if (!strcmp(m,"five_seconds")) {struct timespec t={5,0};while (nanosleep(&t,&t)&&errno==EINTR) {}}
    const char *result=b->fixture_numeric_result?b->fixture_numeric_result:"{\"fixture\":true}";
    dprintf(1,"INSTRUMENTATION_RESULT: numeric_result=%s\n",result);
    if (!strcmp(m,"missing_footer")) _exit(0);
    if (!strcmp(m,"duplicate_footer")) write(1,"INSTRUMENTATION_CODE: -1\n",25);
    write(1,"INSTRUMENTATION_CODE: -1\n",25);_exit(0);
#else
    char duration[3];snprintf(duration,sizeof(duration),"%u",b->seconds);
    char *const args[]={"am","instrument","--user","0","-w",
        "-e","touch_mode","direct","-e","rate_index","0","-e","network_scope","lan",
        "-e","v50_profile","on","-e","pcm_queue","off","-e","stage_diagnostics","off",
        "-e","codec_startup","off","-e","credential_save","off","-e","credential_source","saved-ui",
        "-e","source_input","off","-e","surface_submit_lead_ms","0","-e","steady_seconds","20",
        "-e","media_only","true","-e","owner_native_window","single",
        "-e","owner_native_window_seconds",duration,
        "local.huoguo.lanuitest/local.remoteandroid.direct.LanUiAcceptance",NULL};
    char *const env[]={"PATH=/system/bin:/system/xbin","LANG=C",NULL};
    execve("/system/bin/am",args,env);_exit(127);
#endif
}
int bd_start(struct bd_bridge *b,uint64_t end) {
    if (!bd_phase_end(b,end)||b->possible_start||b->driver||b->reads->pending_read
            ||b->reads->lifecycle->phase!=HG_INSTALLED
            ||b->driver_qualification(b->context,BD_BEFORE_DRIVER,b,end)!=1
            ||!within(end)||!current_scope(b->reads->lifecycle->owner)) return bd_failed(b);
    /* Fresh native package/process reads supplement the independent caller;
     * they never supply source/operator/admission/current Attempt permission.
     */
    if (!bound_read_begin(b->reads,end)) return bd_failed(b);
    int read_ok=bound_read_actual(b->reads,HG_AFTER_INSTALL,HG_READ_APP_PRESENT)
        &&bound_read_actual(b->reads,HG_AFTER_INSTALL,HG_READ_HELPER_PRESENT);
    b->reads->busy=0;
    if (!read_ok||!within(end)||bd_cancelled
            ||b->driver_qualification(b->context,BD_BEFORE_DRIVER,b,end)!=1
            ||!within(end)) return bd_failed(b);
    uint64_t n=now_ms(),needed=(uint64_t)(8+b->seconds+8)*1000;
    if (!n||n>b->ceiling||b->ceiling-n<needed) return bd_failed(b);
    struct sigaction s;struct rlimit limit;
    if (sigaction(SIGCHLD,NULL,&s)||s.sa_handler!=SIG_DFL||(s.sa_flags&SA_NOCLDWAIT)
            ||getrlimit(RLIMIT_NOFILE,&limit)||limit.rlim_cur==RLIM_INFINITY||limit.rlim_cur>65536) return bd_failed(b);
    b->driver=hg_readonly_new();if (!b->driver) return bd_failed(b);
    int a[2],c[2];if (!read_pipe(a)) return bd_failed(b);
    if (!read_pipe(c)) {close(a[0]);close(a[1]);return bd_failed(b);}
#ifdef HG_NATIVE_DRIVER_FIXTURE
    if (!strcmp(b->fixture_mode,"held_EOF")) b->driver->fixture_hold=dup(c[1]);
#endif
    /* Persisted host possible-start is a separate prerequisite in the complete
     * binding. This in-process possible flag precedes the actual sole fork.
     */
    b->possible_start=1;b->driver_end=n+needed;
    pid_t p=fork();
    if (p<0) {close(a[0]);close(a[1]);close(c[0]);close(c[1]);return bd_failed(b);}
    if (!p) bd_exec_fixed(b,a,c);
    b->driver->child=p;b->driver->pipes[0]=a[0];b->driver->pipes[1]=c[0];
    close(a[1]);close(c[1]);b->started=1;
    /* A slow fork is not a hard wall-clock guarantee. Failure after creation
     * still retains this actual child, pipes and possible-start flag.
     */
    if (!within(end)||!within(b->driver_end)||!within(b->ceiling)||bd_cancelled) return bd_failed(b);
    return 1;
}
/* Only pumps its actual child; no driver signals or PID setter. A poll/command
 * is at most3s. The five-second window spans multiple calls, never a wider
 * command read deadline. Unknown objects can still be drained/reaped locally.
 */
int bd_poll(struct bd_bridge *b,uint64_t end) {
    if (!b||!b->driver||!b->started||!finite_deadline(end)) return b?bd_failed(b),-1:-1;
    uint64_t stop=end;if (stop>b->driver_end) stop=b->driver_end;if (stop>b->ceiling) stop=b->ceiling;
    do {
        if (!read_reap(b->driver)) bd_failed(b);
        read_drain(b->driver,0);read_drain(b->driver,1);
        if (b->driver->overflow||b->driver->pipe_failed||bd_cancelled) bd_failed(b);
        /* Reaping/EOF can be observed for the first time AFTER expiry. Check
         * the original ceilings before accepting even a naturally closed
         * child. Unknown objects may still be drained/reaped, never promoted.
         */
        if (!within(b->driver_end)||!within(b->ceiling)) bd_failed(b);
        if (b->driver->reaped&&b->driver->eof[0]&&b->driver->eof[1]) {
            if (!bd_footer(b)) bd_failed(b);
            return b->unknown?-1:within(end)?1:0;
        }
        if (b->unknown) return -1;
        struct pollfd f[2]={{b->driver->eof[0]?-1:b->driver->pipes[0],POLLIN,0},
                           {b->driver->eof[1]?-1:b->driver->pipes[1],POLLIN,0}};
        if (poll(f,2,5)<0&&errno!=EINTR) return bd_failed(b),-1;
    } while (within(stop)&&!bd_cancelled);
    if (!within(b->driver_end)||!within(b->ceiling)||bd_cancelled) return bd_failed(b),-1;
    return 0;
}
int bd_done(struct bd_bridge *b,uint64_t end) {
    if (!bd_phase_end(b,end)||end>b->driver_end||b->done||!bd_footer(b)
            ||!bound_read_begin(b->reads,end)) return bd_failed(b);
    int ok=hg_lifecycle_driver_done(b->reads->lifecycle,end);b->reads->busy=0;
    if (!ok) return bd_failed(b);b->done=1;return 1;
}
/* No uninstall, scope retirement, unknown destructor, driver signal or release
 * API. These require the separately reviewed complete actual Android binding.
 */
#ifndef HG_NATIVE_DRIVER_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"native_driver_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"actual_Android_qualification_implemented\":false,\"release_authorized\":false}");return 0;
}
#endif

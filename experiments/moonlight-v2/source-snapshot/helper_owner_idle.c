/* Default-OFF persistent idle phase for the same native helper lifecycle.
 * Idle time is separate from the <=3s command read. This does not qualify a
 * driver, Android package, operator or lease. A complete caller is still absent.
 */
#define HG_HELPER_LIFECYCLE_LIBRARY 1
#include "helper_owner_lifecycle.c"

enum hg_idle_state { HG_IDLE_NEW=0,HG_IDLE_WAITING,HG_IDLE_READABLE,HG_IDLE_UNKNOWN };
struct hg_idle {
    struct hg_lifecycle *lifecycle;
    uint64_t ceiling;
    enum hg_idle_state state;
    int (*service)(void *,uint64_t);
    void *context;
};
/* Create before upload/install so their time consumes the same process budget.
 * Never accept a PID, serialized owner or a different input FD. Construction
 * does not perform any scope, PM, channel read or driver operation.
 */
struct hg_idle *hg_idle_new(struct hg_lifecycle *l,unsigned seconds,
        int (*service)(void *,uint64_t),void *context) {
    if (!l||l->phase!=HG_NEW||l->idle_attached||!service||seconds<30||seconds>3600) return NULL;
    uint64_t now=l->constructed_ms;
    if (!now||UINT64_MAX-now<(uint64_t)seconds*1000) return NULL;
    struct hg_idle *w=calloc(1,sizeof(*w));if (!w) return NULL;
    w->lifecycle=l;w->ceiling=now+(uint64_t)seconds*1000;l->idle_attached=1;
    w->service=service;w->context=context;return w;
}
static int idle_unknown(struct hg_idle *w) {
    if (w) {w->state=HG_IDLE_UNKNOWN;fail_lifecycle(w->lifecycle);}return 0;
}
int hg_idle_until_readable(struct hg_idle *w) {
    if (!w||w->state!=HG_IDLE_NEW||!w->lifecycle
            ||w->lifecycle->phase!=HG_INSTALLED) return idle_unknown(w);
    struct hg_lifecycle *l=w->lifecycle;struct hg_owner *o=l->owner;
    w->state=HG_IDLE_WAITING;
    for (;;) {
        uint64_t now=now_ms();
        if (!now||now>=w->ceiling||cancelled||l->phase!=HG_INSTALLED
                ||l->channel.input!=STDIN_FILENO||l->channel.stopped
                ||!l->install_natural||!natural_child(o)||!current_scope(o)
                ||!exact_node(o->reader,&o->file_stat)) return idle_unknown(w);
        uint64_t probe_end=now+3000;if (probe_end>w->ceiling) probe_end=w->ceiling;
        /* Same-process actual-object service callback, not a frame/JSON flag.
         * It may drain/poll an owned driver or independently bound bridge.
         * Before/after timing is cooperative, not a hard filesystem deadline.
         * Returning1 means service succeeded, never DRIVER_DONE or permission.
         */
        if (w->service(w->context,probe_end)!=1||cancelled||!within(probe_end)
                ||l->phase!=HG_INSTALLED||!current_scope(o)
                ||!exact_node(o->reader,&o->file_stat)) return idle_unknown(w);
        struct pollfd p={l->channel.input,POLLIN,0};int r=poll(&p,1,20);
        if (r<0) {if (errno==EINTR) continue;return idle_unknown(w);}
        /* Even queued bytes plus HUP refuse: a lost controller does not let a
         * native parent consume prequeued cleanup and release unknown scope.
         */
        if (p.revents&(POLLERR|POLLNVAL|POLLHUP)) return idle_unknown(w);
        if (r&&p.revents&POLLIN) {
            if (!within(w->ceiling)||cancelled) return idle_unknown(w);
            w->state=HG_IDLE_READABLE;return 1;
        }
    }
}
/* A readable FD is only a scheduling result. Begin a NEW finite <=3s phase
 * read, then the unchanged lifecycle DRIVER_CLOSED callback must independently
 * verify the actual held driver/EOF/current Attempt and normal cleanup.
 * An early/partial/foreign frame cannot replace that callback.
 */
int hg_idle_driver_done(struct hg_idle *w,uint64_t end) {
    if (!w||w->state!=HG_IDLE_READABLE||!finite_deadline(end)
            ||end>w->ceiling||!hg_lifecycle_driver_done(w->lifecycle,end)) return idle_unknown(w);
    /* One outstanding command only. On Darwin a pipe with queued data may
     * report POLLIN before HUP; reject any trailing readiness, including extra
     * cleanup frames or EOF, instead of mistaking that for a live controller.
     * This observation is not an atomic hold on the controller or permission.
     */
    struct pollfd p={w->lifecycle->channel.input,POLLIN,0};
    int r=poll(&p,1,0);if (r<0||r||p.revents) return idle_unknown(w);
    return 1;
}

#ifndef HG_HELPER_IDLE_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"helper_idle_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"complete_live_binding_implemented\":false,\"release_authorized\":false}");
    return 0;
}
#endif

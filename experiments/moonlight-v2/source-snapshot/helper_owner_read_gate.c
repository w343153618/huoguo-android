/* NEW partial callback composition. No production activation or whole bridge.
 * Independent same-process admission must succeed BEFORE any native read.
 * Artifact snapshots cannot supply operator/lease/Attempt/driver authority.
 */
#define HG_HELPER_LIFECYCLE_LIBRARY 1
#include "helper_owner_lifecycle.c"
#define HG_HELPER_READ_LIBRARY 1
#include "helper_owner_readonly.c"

struct bound_read_gate {
    struct hg_lifecycle *lifecycle;
    struct hg_readonly *pending_read;
    int (*independent)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t);
    void *context;
    uint64_t end;
    int busy,unknown,completed_native_reads;
#ifdef HG_HELPER_READ_FIXTURE
    const char *fixture_root;
    const char *(*fixture_read_mode)(void *,enum hg_gate,enum hg_read_kind);
#endif
};
static int bound_read_failed(struct bound_read_gate *g) {
    if (g) {g->unknown=1;fail_lifecycle(g->lifecycle);}return 0;
}
static int bound_read_actual(struct bound_read_gate *g,enum hg_gate gate,enum hg_read_kind kind) {
    if (g->pending_read||g->unknown||!within(g->end)) return bound_read_failed(g);
    struct hg_readonly *q=hg_readonly_new();if (!q) return bound_read_failed(g);
    g->pending_read=q;
#ifdef HG_HELPER_READ_FIXTURE
    q->fixture_root=g->fixture_root;q->fixture_mode=g->fixture_read_mode(g->context,gate,kind);
#else
    (void)gate;
#endif
    int ok=hg_readonly_snapshot(q,kind,g->end);
    /* Only this actual readonly object can close its own handles after sole
     * child/dual EOF. Unknown native owner/control/scope is never closed here.
     */
    if (!hg_readonly_close(q)) return bound_read_failed(g);
    g->pending_read=NULL;free(q);
    if (!ok) return bound_read_failed(g);++g->completed_native_reads;return 1;
}
static int bound_read_callback(void *data,enum hg_gate gate,const struct hg_owner *owner) {
    struct bound_read_gate *g=data;
    if (!g||!g->busy||g->unknown||g->pending_read||!g->lifecycle||owner!=g->lifecycle->owner
            ||!finite_deadline(g->end)||!g->independent
            ||g->independent(g->context,gate,g->lifecycle,g->end)!=1||!within(g->end)) return bound_read_failed(g);
    /* An idle package snapshot cannot inspect an active Attempt. Independent
     * actual driver/current Attempt cleanup MUST already be bound by caller.
     */
    enum hg_read_kind helper=(gate==HG_BEFORE_SCOPE||gate==HG_BEFORE_INSTALL
        ||gate==HG_AFTER_UNINSTALL||gate==HG_BEFORE_RETIRE)?HG_READ_HELPER_ABSENT:HG_READ_HELPER_PRESENT;
    return bound_read_actual(g,gate,HG_READ_APP_PRESENT)&&bound_read_actual(g,gate,helper);
}
/* Constructs its own lifecycle/owner. No existing owner/PID/receipt setter. */
struct bound_read_gate *bound_read_new(const char *nonce,uint64_t size,const char *sha,
        int (*independent)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),void *context) {
    if (!independent) return NULL;
    struct bound_read_gate *g=calloc(1,sizeof(*g));if (!g) return NULL;
    g->independent=independent;g->context=context;
    g->lifecycle=hg_lifecycle_new(STDIN_FILENO,nonce,size,sha,bound_read_callback,g);
    if (!g->lifecycle) {free(g);return NULL;}return g;
}
static int bound_read_begin(struct bound_read_gate *g,uint64_t end) {
    if (!g||g->busy||g->unknown||g->pending_read||!finite_deadline(end)) return bound_read_failed(g);
    g->busy=1;g->end=end;return 1;
}
int bound_read_upload(struct bound_read_gate *g,const char *base,uint64_t dev,uint64_t ino,uint64_t end) {
    if (!bound_read_begin(g,end)) return 0;
    int ok=hg_lifecycle_upload(g->lifecycle,base,dev,ino,end);g->busy=0;
    return ok?1:bound_read_failed(g);
}
int bound_read_install(struct bound_read_gate *g,uint64_t end) {
    if (!bound_read_begin(g,end)) return 0;
    int ok=hg_lifecycle_install(g->lifecycle,end,"natural");g->busy=0;
    return ok?1:bound_read_failed(g);
}
/* Deliberately no driver/uninstall/retire/close/release entry in this partial
 * composition. Actual coordinator-held driver and independent post-cleanup
 * qualifiers remain missing; idle package reads cannot substitute for them.
 */
#ifndef HG_BOUND_READ_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"bound_read_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"complete_live_bridge\":false,\"release_authorized\":false}");return 0;
}
#endif

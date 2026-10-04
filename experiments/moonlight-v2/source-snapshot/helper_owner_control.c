/* Default-OFF retained driver request scheduler. No Android activation entry.
 * A request schedules an operation; only the separately supplied same-process
 * callbacks and actual created native objects can qualify that operation.
 * The host controller cannot serialize a boolean into those callbacks.
 */
#define HG_NATIVE_DRIVER_LIBRARY 1
#include "helper_owner_native_driver.c"

#define HGS_BYTES 48
enum hgs_kind { HGS_START=1,HGS_POLL=2 };
enum hgs_phase { HGS_NEW=0,HGS_UPLOADED,HGS_INSTALLED,HGS_RUNNING,
    HGS_LOCAL_CLOSED,HGS_DONE,HGS_UNKNOWN };
struct hgs_control {
    struct bd_bridge *bridge;
    enum hgs_phase phase;
    uint64_t sequence;
    int busy;
};
static int hgs_failed(struct hgs_control *s) {
    if (s) {s->phase=HGS_UNKNOWN;bd_failed(s->bridge);}return 0;
}
struct hgs_control *hgs_new(const char *nonce,uint64_t size,const char *sha,
        unsigned process_seconds,unsigned sample_seconds,
        int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
        int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),void *context) {
    /* Construct, never adopt an existing bridge, owner, PID or receipt. */
    if (!eligibility||!driver_qualification) return NULL;
    struct hgs_control *s=calloc(1,sizeof(*s));if (!s) return NULL;
    s->bridge=bd_new(nonce,size,sha,process_seconds,sample_seconds,
        eligibility,driver_qualification,context);
    if (!s->bridge) {free(s);return NULL;}return s;
}
static int hgs_begin(struct hgs_control *s,enum hgs_phase phase,uint64_t end) {
    if (!s||s->busy||s->phase!=phase||!bd_phase_end(s->bridge,end)) return hgs_failed(s);
    s->busy=1;return 1;
}
static int hgs_finish(struct hgs_control *s,int good,enum hgs_phase phase) {
    s->busy=0;if (!good) return hgs_failed(s);s->phase=phase;return 1;
}
int hgs_upload(struct hgs_control *s,const char *base,uint64_t dev,uint64_t ino,uint64_t end) {
    if (!hgs_begin(s,HGS_NEW,end)) return 0;
    return hgs_finish(s,bd_upload(s->bridge,base,dev,ino,end),HGS_UPLOADED);
}
int hgs_install(struct hgs_control *s,uint64_t end) {
    if (!hgs_begin(s,HGS_UPLOADED,end)) return 0;
    return hgs_finish(s,bd_install(s->bridge,end),HGS_INSTALLED);
}
static int hgs_header(struct hgs_control *s,enum hgs_kind kind,uint64_t end) {
    unsigned char h[HGS_BYTES];struct bd_bridge *b=s->bridge;
    if (!channel_read(b->reads->lifecycle->channel.input,h,sizeof(h),end)
            ||memcmp(h,"HGDS0001",8)||h[8]!=kind
            ||memcmp(h+9,"\0\0\0\0\0\0\0",7)
            ||memcmp(h+16,b->reads->lifecycle->channel.nonce,24)
            ||wire_u64(h+40)!=s->sequence||s->sequence==UINT64_MAX
            ||!bd_control_clear(b)||!within(end)) return 0;
    ++s->sequence;return 1;
}
int hgs_start(struct hgs_control *s,uint64_t end) {
    if (!hgs_begin(s,HGS_INSTALLED,end)) return 0;
    /* START follows a completed INSTALL and a fresh independently qualified
     * host gateway. Neither the request nor an earlier installed event grants
     * native eligibility. bd_start repeats its own native queries/callbacks.
     */
    return hgs_finish(s,hgs_header(s,HGS_START,end)&&bd_start(s->bridge,end),HGS_RUNNING);
}
/* Return 0 pending,1 local client closure,-1 sticky unknown. One outstanding
 * request, including POLL. The native original driver/process ceilings remain
 * anchored; a new request cannot extend them. Driver waiting spans requests.
 */
int hgs_poll(struct hgs_control *s,uint64_t end) {
    if (!hgs_begin(s,HGS_RUNNING,end)) return -1;
    if (!hgs_header(s,HGS_POLL,end)) {hgs_finish(s,0,HGS_UNKNOWN);return -1;}
    int r=bd_poll(s->bridge,end);s->busy=0;
    if (r<0) return hgs_failed(s),-1;
    if (r==1) s->phase=HGS_LOCAL_CLOSED;
    return r;
}
int hgs_done(struct hgs_control *s,uint64_t end) {
    if (!hgs_begin(s,HGS_LOCAL_CLOSED,end)) return 0;
    /* HGHC DRIVER_DONE still has its original closed sequence. This scheduler
     * adds no cleanup/uninstall/retirement/release API or serialized authority.
     */
    return hgs_finish(s,bd_done(s->bridge,end),HGS_DONE);
}
/* Unknown pumping retains the actual graph. It cannot restore a phase or
 * authorize cleanup. The complete live supervisor must keep these objects.
 */
int hgs_drain_unknown(struct hgs_control *s,uint64_t end) {
    if (!s||s->phase!=HGS_UNKNOWN||!s->bridge||!s->bridge->started
            ||!finite_deadline(end)) return 0;
    bd_poll(s->bridge,end);return 0;
}
#ifndef HG_HELPER_CONTROL_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"native_control_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"complete_Android_qualifiers\":false,\"release_authorized\":false}");return 0;
}
#endif

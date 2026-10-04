/* Default-OFF same-process actual child-output snapshot. No activation,
 * JSON qualifier, cleanup/retirement/release API or existing graph adoption.
 */
#define HG_HELPER_CONTROL_LIBRARY 1
#include "helper_owner_control.c"
struct owned_output {
    struct hgs_control *control;
    unsigned char payload[65536];size_t bytes;
    char sha256[65];int collected;
};
struct owned_output *owned_output_new(const char *nonce,uint64_t size,const char *sha,
        unsigned process_seconds,unsigned sample_seconds,
        int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
        int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),void *context) {
    struct owned_output *r=calloc(1,sizeof(*r));if (!r) return NULL;
    r->control=hgs_new(nonce,size,sha,process_seconds,sample_seconds,eligibility,driver_qualification,context);
    if (!r->control) {free(r);return NULL;}return r;
}
int owned_output_snapshot(struct owned_output *r,uint64_t end) {
    if (!r||r->collected||!r->control||r->control->busy
            ||r->control->phase!=HGS_LOCAL_CLOSED||!bd_phase_end(r->control->bridge,end)
            ||end>r->control->bridge->driver_end||!bd_footer(r->control->bridge)) return 0;
    struct hg_readonly *d=r->control->bridge->driver;
    const char marker[]="INSTRUMENTATION_RESULT: numeric_result=";
    size_t begin=0,found=0;const unsigned char *payload=NULL;size_t length=0;
    for (size_t i=0;i<d->used[0];++i) if (d->output[0][i]=='\n') {
        size_t n=i-begin;
        if (n>=sizeof(marker)-1&&!memcmp(d->output[0]+begin,marker,sizeof(marker)-1)) {
            ++found;payload=d->output[0]+begin+sizeof(marker)-1;length=n-(sizeof(marker)-1);
        }
        begin=i+1;
    }
    if (found!=1||!length||length>sizeof(r->payload)||!within(end)) return 0;
    /* Same native child natural wait and dual EOF froze its retained output.
     * Copy once; a caller-supplied byte buffer/JSON/PID cannot replace it.
     * Bytes are deliberately opaque: this is NOT validation of App fields,
     * Attempt/normal cleanup/operator/PM/server lease or an atomic App hold.
     */
    memcpy(r->payload,payload,length);r->bytes=length;
    struct sha s={{0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19},{0},0,0};
    update(&s,r->payload,r->bytes);digest(&s,r->sha256);
    if (!within(end)||!bd_footer(r->control->bridge)) {r->bytes=0;return 0;}
    r->collected=1;return 1;
}
#ifndef HG_OWNED_OUTPUT_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if (argc!=1) return 2;
    puts("{\"event\":\"owned_output_prepared\",\"activation_available\":false,\"App_JSON_validated\":false,\"release_authorized\":false}");return 0;
}
#endif

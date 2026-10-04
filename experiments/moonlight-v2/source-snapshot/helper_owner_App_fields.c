/* NEW same-created graph. Production inert: independent Android association
 * callbacks are absent, and parsing never supplies those callbacks. */
#define HG_APP_REPORT_FD_LIBRARY 1
#include "helper_owner_App_report.c"
#include "helper_App_fields_json.h"
enum af_gate {AF_BEFORE_ASSOCIATION=1,AF_AFTER_ASSOCIATION};
struct af_capture {const void *attempt,*receiver,*surface,*phone_clock;
 int64_t captured_start_ns,phone_now_ns;};
struct af_owned {struct ar_report *report;struct aj_doc *document;struct af_claims claims;
 int (*associate)(void *,enum af_gate,const struct af_owned *,struct af_capture *,uint64_t);
 void *context;int attempted,unknown,core_prefix_observed,association_observed;
 struct af_capture before,after;};
static int af_unknown(struct af_owned *r){if(r){r->unknown=1;r->core_prefix_observed=r->association_observed=0;
 memset(&r->claims,0,sizeof(r->claims));ar_unknown(r->report);}return 0;}
struct af_owned *af_new(const char *nonce,uint64_t size,const char *sha,unsigned process_seconds,unsigned sample_seconds,
 int (*eligible)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
 int (*driver)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),
 int (*uid)(void *,enum ar_gate,const struct ar_report *,uid_t *,gid_t *,uint64_t),
 int (*associate)(void *,enum af_gate,const struct af_owned *,struct af_capture *,uint64_t),void *context){
 if(!associate||!uid||!driver||!eligible)return NULL;
 struct af_owned *r=calloc(1,sizeof(*r));if(!r)return NULL;r->document=calloc(1,sizeof(*r->document));
 if(!r->document){free(r);return NULL;}r->associate=associate;r->context=context;
 r->report=ar_new(nonce,size,sha,process_seconds,sample_seconds,eligible,driver,uid,context);
 if(!r->report){free(r->document);free(r);return NULL;}return r;
}
static int af_capture_valid(struct af_capture *c){return c->attempt&&c->receiver&&c->surface&&c->phone_clock
 &&c->captured_start_ns>0&&c->phone_now_ns>=c->captured_start_ns;}
int af_observe(struct af_owned *r,uint64_t end){
 if(!r||r->attempted||r->unknown)return 0;r->attempted=1;
 /* Entry consumes only its graph's actual held helper output/App file. */
 if(!r->report->helper->valid||!ar_read(r->report,end)||!ar_end(r->report))return af_unknown(r);
 if(r->associate(r->context,AF_BEFORE_ASSOCIATION,r,&r->before,end)!=1||!af_capture_valid(&r->before)||!ar_end(r->report))return af_unknown(r);
 if(!af_core_json(r->document,r->report->bytes,r->report->used,&r->claims)||!ar_end(r->report))return af_unknown(r);
 struct hr_observed *h=&r->report->helper->observed;
 if(r->claims.start<r->before.captured_start_ns||r->claims.observation>r->before.phone_now_ns
  ||h->click<r->claims.start||h->finished>r->claims.end)return af_unknown(r);
 if(r->associate(r->context,AF_AFTER_ASSOCIATION,r,&r->after,end)!=1||!af_capture_valid(&r->after)||!ar_end(r->report)
  ||r->before.attempt!=r->after.attempt||r->before.receiver!=r->after.receiver||r->before.surface!=r->after.surface
  ||r->before.phone_clock!=r->after.phone_clock||r->before.captured_start_ns!=r->after.captured_start_ns
  ||r->after.phone_now_ns<r->before.phone_now_ns)return af_unknown(r);
 /* Callback may consume time/change a named file. Retest all held components
  * and names after association; this still is not an atomic content hold. */
 const char *const names[]={"data","user","0","local.remoteandroid.direct.experiment","files","udp-app-last-report.json"};
 for(unsigned i=0;i<7;++i){struct stat held,named;
  if(fstat(r->report->fd[i],&held)||!read_stat_same(&r->report->before[i],&held,1)
   ||(i&&(fstatat(r->report->fd[i-1],names[i-1],&named,AT_SYMLINK_NOFOLLOW)||!read_stat_same(&r->report->before[i],&named,1))))return af_unknown(r);
 }
 if(!ar_end(r->report))return af_unknown(r);
 r->core_prefix_observed=r->association_observed=1;return 1;
}
#ifndef HG_APP_FIELDS_LIBRARY
int main(int argc,char **argv){(void)argv;if(argc!=1)return 2;
 puts("{\"event\":\"App_fields_prepared\",\"activation_available\":false,\"permission\":false,\"release\":false}");return 0;}
#endif

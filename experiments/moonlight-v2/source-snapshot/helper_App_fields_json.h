/* Private numeric observation. Syntax and closed core/prefix claims never
 * establish operator permission, an Attempt hold, presentation or cleanup. */
#ifndef HG_APP_FIELDS_JSON_H
#define HG_APP_FIELDS_JSON_H
#include "helper_App_numeric_json.h"
struct af_rule { const char *key; int64_t low,high; int decimal; };
#define AF_INT(k,a,b) {k,a,b,0}
#define AF_DEC(k) {k,0,INT64_MAX,1}
static const struct af_rule af_core[]={
 AF_INT("requested_seconds",30,30),AF_INT("session_limit_reached",0,0),AF_INT("session_end_reason_code",0,0),
 AF_INT("start_ns",1,INT64_MAX),AF_INT("first_server_packet_ns",1,INT64_MAX),
 AF_INT("receive_end_ns",1,INT64_MAX),AF_INT("observation_end_ns",1,INT64_MAX),
 AF_INT("fps_limit",30,30),AF_INT("buffer_ms",80,80),AF_INT("udp_packets",0,INT64_MAX),
 AF_INT("udp_payload_bytes",0,INT64_MAX),AF_INT("foreign_peer_packets",0,INT64_MAX),
 AF_INT("authentication_errors",0,INT64_MAX),AF_INT("replay_errors",0,INT64_MAX),
 AF_INT("received_media_frames",15,INT64_MAX),AF_INT("queued_media_frames",0,INT64_MAX),
 AF_INT("source_width",1,960),AF_INT("source_height",1,960),AF_INT("decoder_input_timeouts",0,INT64_MAX),
 AF_INT("late_discarded_count",0,INT64_MAX),AF_INT("codec_callback_count",10,INT64_MAX),
 AF_DEC("receive_loop_max_ms"),AF_DEC("receive_processing_max_ms"),AF_DEC("receive_socket_wait_max_ms"),
 AF_INT("video_worker_expired_frames",0,INT64_MAX),AF_INT("video_worker_stale_epoch_drops",0,INT64_MAX),
 AF_INT("audio_cleanup_confirmed",0,1),AF_INT("surface_submit_lead_ms",0,0),
 AF_INT("surface_submit_applications",0,0),AF_INT("surface_submit_wait_count",0,0),AF_DEC("surface_submit_wait_total_ms"),
 AF_DEC("surface_submit_max_output_hold_ms"),AF_DEC("surface_submit_max_park_ms"),
 AF_INT("surface_submit_budget_fallbacks",0,0),AF_INT("stage_diagnostics_enabled",0,0),
 AF_INT("surface_submit_status_code",0,0),AF_INT("hardware_video",1,1),AF_INT("codec_startup_ready_enabled",0,0)
};
static const char *const af_optional[]={"nps_physical_network_binding","native_fec","native_mapping_details","udp_audio","udp_touch",
 "video_input_queue","codec_startup_gate","codec_timestamp_validity","display_mode_start","display_mode_end","decoder_stage_metrics"};
static const char *const af_prefix[]={"inbox_epoch_events_numeric","transport_samples_numeric","native_frame_events_numeric"};
struct af_claims { int64_t start,first,end,observation,frames,callbacks,audio_claim;
 int inbox_available,inbox_enabled,native_available,native_enabled,transport_available;
 unsigned inbox_rows,native_rows,transport_rows,unqualified_optional_objects; };
static inline int af_closed(const struct aj_doc *d,unsigned o,const char *const *keys,unsigned count){
 if(o>=d->used||d->node[o].kind!=AJ_OBJECT||d->node[o].count!=count)return 0;
 for(unsigned k=0;k<count;++k)if(aj_get(d,o,keys[k])>=d->used)return 0;return 1;
}
static inline int64_t af_n(const struct aj_doc *d,unsigned o,const char *key){return d->node[aj_get(d,o,key)].integer;}
static inline int af_i(const struct aj_doc *d,unsigned o,const char *key,int64_t low,int64_t high){return aj_num(d,o,key,low,high,NULL);}
static inline int af_array(const struct aj_doc *d,unsigned o,const char *key,unsigned rows,int64_t low,int64_t high){
 unsigned a=aj_get(d,o,key);if(a>=d->used||d->node[a].kind!=AJ_ARRAY||d->node[a].count!=rows)return 0;
 for(unsigned i=a+1;i<d->node[a].next;i=d->node[i].next){
  if(d->node[i].kind!=AJ_INTEGER||d->node[i].integer<low||d->node[i].integer>high)return 0;}return 1;
}
static inline int64_t af_cell(const struct aj_doc *d,unsigned o,const char *key,unsigned row){
 /* Only after af_array proves a flat integral column, so one node per row. */
 return d->node[aj_get(d,o,key)+1+row].integer;
}
static inline int af_transport(const struct aj_doc *d,unsigned o,struct af_claims *out){
 static const char *const keys[]={"schema_version","available","capacity","recorded_count","exported_count",
 "omitted_prefix_following_count","all_recorded_samples_exported","all_session_observations_covered",
 "timestamp_is_exact_packet_arrival","physical_route_or_latency_verified","t_ns","udp_packets","authenticated_packets",
 "authenticated_video_packets","received_media_frames","codec_callback_count","FEC_packets","FEC_frames_expired",
 "FEC_reference_lost","FEC_dependency_dropped","FEC_keyframe_requests"};
 static const char *const columns[]={"t_ns","udp_packets","authenticated_packets","authenticated_video_packets","received_media_frames",
 "codec_callback_count","FEC_packets","FEC_frames_expired","FEC_reference_lost","FEC_dependency_dropped","FEC_keyframe_requests"};
 if(!af_closed(d,o,keys,sizeof(keys)/sizeof(*keys))||!af_i(d,o,"schema_version",1,1)||!af_i(d,o,"available",0,1)
  ||!af_i(d,o,"capacity",64,64)||!af_i(d,o,"recorded_count",0,3600)||!af_i(d,o,"exported_count",0,64)
  ||!af_i(d,o,"omitted_prefix_following_count",0,3600)||!af_i(d,o,"all_recorded_samples_exported",0,1)
  ||!af_i(d,o,"all_session_observations_covered",0,0)||!af_i(d,o,"timestamp_is_exact_packet_arrival",0,0)
  ||!af_i(d,o,"physical_route_or_latency_verified",0,0))return 0;
 unsigned rows=(unsigned)af_n(d,o,"exported_count");int available=(int)af_n(d,o,"available");
 for(unsigned c=0;c<sizeof(columns)/sizeof(*columns);++c)if(!af_array(d,o,columns[c],rows,0,INT64_MAX))return 0;
 if(!available){if(rows||af_n(d,o,"omitted_prefix_following_count")||af_n(d,o,"all_recorded_samples_exported"))return 0;}
 else {int64_t recorded=af_n(d,o,"recorded_count");
  if(rows!=(recorded>64?64:recorded)||af_n(d,o,"omitted_prefix_following_count")!=recorded-rows
   ||af_n(d,o,"all_recorded_samples_exported")!=(recorded<=64))return 0;
  for(unsigned i=0;i<rows;++i){
   int64_t time=af_cell(d,o,"t_ns",i);
   if(time<out->start||time>out->end||(i&&time<=af_cell(d,o,"t_ns",i-1)))return 0;
   for(unsigned c=1;c<sizeof(columns)/sizeof(*columns);++c)
    if(i&&af_cell(d,o,columns[c],i)<af_cell(d,o,columns[c],i-1))return 0;
   if(af_cell(d,o,"authenticated_video_packets",i)>af_cell(d,o,"authenticated_packets",i)
    ||af_cell(d,o,"authenticated_packets",i)>af_cell(d,o,"udp_packets",i))return 0;
  }
 }
 out->transport_available=available;out->transport_rows=rows;return 1;
}
static inline int af_inbox(const struct aj_doc *d,unsigned o,struct af_claims *out){
 static const char *const keys[]={"schema_version","available","enabled","capacity","retained_count","exported_count",
 "evicted_count","observed_count","unknown_event_count","unknown_reason_count","worker_alive","worker_join_timed_out",
 "worker_cleanup_confirmed","snapshot_complete","all_recorded_events_retained","known_codes_complete","availability_code",
 "all_pipeline_events_covered","event_code","reason_code","epoch","previous_epoch","time_ns","pts_us","received_ns","queue_frames","queue_bytes"};
 static const char *const columns[]={"event_code","reason_code","epoch","previous_epoch","time_ns","pts_us","received_ns","queue_frames","queue_bytes"};
 if(!af_closed(d,o,keys,sizeof(keys)/sizeof(*keys))||!af_i(d,o,"schema_version",1,1)||!af_i(d,o,"capacity",256,256))return 0;
 for(unsigned k=1;k<18;++k){int64_t low=(k==2||k==10||k==11)?-1:0;
  int64_t high=k==1||k==12||k==13||k==14||k==15?1:k==2||k==10||k==11?1:k==4||k==5?256:k==16?5:k==17?0:INT64_MAX;
  if(k!=3&&!af_i(d,o,keys[k],low,high))return 0;}
 unsigned rows=(unsigned)af_n(d,o,"exported_count");int available=(int)af_n(d,o,"available"),enabled=(int)af_n(d,o,"enabled");
 for(unsigned c=0;c<9;++c){int64_t low=c==5?-1:(c==4||c==6)?INT64_MIN:0;
  int64_t high=c==0?5:c==1?13:c==7?4:c==8?2097152:INT64_MAX;
  if(!af_array(d,o,columns[c],rows,low,high))return 0;}
 int cleanup=af_n(d,o,"worker_alive")==0&&af_n(d,o,"worker_join_timed_out")==0;
 if(af_n(d,o,"worker_cleanup_confirmed")!=cleanup)return 0;
 int64_t retained=af_n(d,o,"retained_count"),evicted=af_n(d,o,"evicted_count");
 if(evicted>INT64_MAX-retained||af_n(d,o,"observed_count")!=evicted+retained)return 0;
 int64_t code=af_n(d,o,"availability_code");
 if(!available){if(enabled!=-1||code||retained||evicted||rows)return 0;}
 else if(enabled==0){if(code!=1||retained||evicted||rows)return 0;}
 else if(enabled!=1||code<2||code>5)return 0;
 if(code!=5){
  if(rows||af_n(d,o,"snapshot_complete")||af_n(d,o,"all_recorded_events_retained")||af_n(d,o,"known_codes_complete")
    ||af_n(d,o,"unknown_event_count")||af_n(d,o,"unknown_reason_count"))return 0;
  if(code==2&&af_n(d,o,"worker_alive")!=-1&&af_n(d,o,"worker_join_timed_out")!=-1)return 0;
  if(code==3&&(cleanup||af_n(d,o,"worker_alive")==-1||af_n(d,o,"worker_join_timed_out")==-1))return 0;
  if(code==4&&!cleanup)return 0;
 } else {
  if(!available||enabled!=1||!cleanup||rows!=retained||af_n(d,o,"snapshot_complete")!=1
   ||af_n(d,o,"all_recorded_events_retained")!=(evicted==0))return 0;
  int64_t events=0,reasons=0;
  for(unsigned i=0;i<rows;++i){events+=af_cell(d,o,"event_code",i)==0;reasons+=af_cell(d,o,"reason_code",i)==0;}
  if(af_n(d,o,"unknown_event_count")!=events||af_n(d,o,"unknown_reason_count")!=reasons
   ||af_n(d,o,"known_codes_complete")!=(!events&&!reasons))return 0;
 }
 out->inbox_available=available;out->inbox_enabled=enabled;out->inbox_rows=rows;return 1;
}
static inline int af_native(const struct aj_doc *d,unsigned o,struct af_claims *out){
 static const char *const initial[]={"schema_version","available","enabled","capacity","source_ring_capacity","native_ring_capacity",
 "all_generated_events_exported","all_pipeline_events_covered","timestamp_is_physical_packet_arrival","quorum_is_codec_ready",
 "host_phone_clock_subtraction_valid","frame_identity_from_JSON_verified","omitted_rows_validated"};
 static const char *const extra[]={"source_retained_count","exported_count","omitted_prefix_following_count","generated_count",
 "native_pending_count","native_evicted_count","java_evicted_count","exported_sequence_contiguous_from_one"};
 static const char *const columns[]={"event_code","frame_id","reference_id","flags","first_arrival_us","last_arrival_us",
 "fec_quorum_ready_us","event_phone_us","deadline_phone_us","logical_bytes","reason_code","event_sequence","pts_us"};
 if(o>=d->used||d->node[o].kind!=AJ_OBJECT||!af_i(d,o,"schema_version",1,1)||!af_i(d,o,"available",0,1)
  ||!af_i(d,o,"enabled",-1,1)||!af_i(d,o,"capacity",64,64)||!af_i(d,o,"source_ring_capacity",8192,8192)
  ||!af_i(d,o,"native_ring_capacity",256,256)||!af_i(d,o,"all_generated_events_exported",0,1)
  ||!af_i(d,o,"omitted_rows_validated",0,1))return 0;
 for(unsigned k=7;k<12;++k)if(!af_i(d,o,initial[k],0,0))return 0;
 int available=(int)af_n(d,o,"available"),enabled=(int)af_n(d,o,"enabled");
 if(!available){
  if(!af_closed(d,o,initial,13)||enabled==1||af_n(d,o,"all_generated_events_exported")||af_n(d,o,"omitted_rows_validated"))return 0;
  out->native_available=0;out->native_enabled=enabled;return 1;
 }
 if(enabled!=1||d->node[o].count!=34)return 0;
 for(unsigned k=0;k<8;++k){int64_t high=k==0?8192:k==1?64:k==2?8192:k==4?256:k==7?1:INT64_MAX;
  if(!af_i(d,o,extra[k],0,high))return 0;}
 unsigned rows=(unsigned)af_n(d,o,"exported_count");
 for(unsigned c=0;c<13;++c)if(!af_array(d,o,columns[c],rows,c==1||c==4||c==9||c==11?1:0,c==0?5:c==3?3:c==9?1048576:c==10?6:INT64_MAX))return 0;
 int64_t source=af_n(d,o,"source_retained_count"),pending=af_n(d,o,"native_pending_count"),nativeEvicted=af_n(d,o,"native_evicted_count"),javaEvicted=af_n(d,o,"java_evicted_count"),generated=af_n(d,o,"generated_count");
 if(rows!=(source>64?64:source)||af_n(d,o,"omitted_prefix_following_count")!=source-rows
  ||nativeEvicted>INT64_MAX-source||javaEvicted>INT64_MAX-source-nativeEvicted
  ||pending>INT64_MAX-source-nativeEvicted-javaEvicted||source+nativeEvicted+javaEvicted+pending!=generated)return 0;
 int contiguous=1;int64_t seq=0,time=0;
 for(unsigned i=0;i<rows;++i){
  int64_t type=af_cell(d,o,"event_code",i),reason=af_cell(d,o,"reason_code",i),first=af_cell(d,o,"first_arrival_us",i),last=af_cell(d,o,"last_arrival_us",i),quorum=af_cell(d,o,"fec_quorum_ready_us",i),now=af_cell(d,o,"event_phone_us",i),deadline=af_cell(d,o,"deadline_phone_us",i),sequence=af_cell(d,o,"event_sequence",i);
  if(!((type==1&&reason==0)||(type==2&&(reason==1||reason==2))||(type==3&&reason==3)||(type==4&&reason==4)||(type==5&&(reason==5||reason==6)))
   ||last<first||now<last||now<time||deadline<=first||deadline-first<1000||deadline-first>80000
   ||(quorum&&(quorum<first||quorum>last))||(type==1&&!quorum)||(type==3&&now<deadline)
   ||sequence<=seq||sequence>generated)return 0;
  /* us quantization is retained. No ns comparison/expiry or arrival inference. */
  if(sequence!=seq+1)contiguous=0;seq=sequence;time=now;
 }
 int all=rows==source&&!pending&&!nativeEvicted&&!javaEvicted&&contiguous&&(rows?seq==generated:generated==0);
 if(af_n(d,o,"exported_sequence_contiguous_from_one")!=contiguous||af_n(d,o,"all_generated_events_exported")!=all
  ||af_n(d,o,"omitted_rows_validated")!=(rows==source))return 0;
 out->native_available=available;out->native_enabled=enabled;out->native_rows=rows;return 1;
}
static inline int af_core_json(struct aj_doc *d,const unsigned char *raw,size_t size,struct af_claims *out){
 (void)aj_flag;if(!aj_parse(d,raw,size)||!out)return 0;memset(out,0,sizeof(*out));
 for(unsigned k=0;k<sizeof(af_core)/sizeof(*af_core);++k){const struct af_rule *r=af_core+k;unsigned i=aj_get(d,0,r->key);
  if(i>=d->used)return 0;
  if(!r->decimal){if(!af_i(d,0,r->key,r->low,r->high))return 0;}
  else if(d->node[i].kind!=AJ_INTEGER&&d->node[i].kind!=AJ_DECIMAL)return 0;
  else {
   /* Finite decimal syntax stays decimal. strtod underflow is not exact zero
    * and must never turn a positive literal into an OFF/zero-wait claim. */
   const unsigned char *literal=d->raw+d->node[i].at;size_t n=d->node[i].len;
   if(literal[0]=='-')return 0;
   if(k>=30&&k<=32)for(size_t j=0;j<n&&literal[j]!='e'&&literal[j]!='E';++j)
    if(literal[j]>='1'&&literal[j]<='9')return 0;
  }
 }
 for(unsigned i=1;i<d->node[0].next;i=d->node[d->node[i].next].next){int known=0;
  for(unsigned k=0;k<sizeof(af_core)/sizeof(*af_core);++k)known|=aj_text(d,i,af_core[k].key);
  for(unsigned k=0;k<3;++k)known|=aj_text(d,i,af_prefix[k]);
  for(unsigned k=0;k<sizeof(af_optional)/sizeof(*af_optional);++k)if(aj_text(d,i,af_optional[k])){
   known=1;if(d->node[d->node[i].next].kind!=AJ_OBJECT||k==10)return 0;
   if(k!=6)++out->unqualified_optional_objects;}
  if(!known)return 0;
 }
 out->start=af_n(d,0,"start_ns");out->first=af_n(d,0,"first_server_packet_ns");out->end=af_n(d,0,"receive_end_ns");out->observation=af_n(d,0,"observation_end_ns");
 out->frames=af_n(d,0,"received_media_frames");out->callbacks=af_n(d,0,"codec_callback_count");out->audio_claim=af_n(d,0,"audio_cleanup_confirmed");
 if(out->start>out->first||out->first>out->end||out->end>out->observation||af_n(d,0,"queued_media_frames")>out->frames)return 0;
 unsigned gate=aj_get(d,0,"codec_startup_gate");
 static const char *const fields[]={"enabled","phase","phase_before_close","failure_code","started_ns","bootstrap_received_ns","prepare_started_ns","ready_ns","fresh_received_ns","committed_ns","bootstrap_pts_us","fresh_pts_us","width","height","bootstrap_admissions","preparing_drops","waiting_drops","fresh_expired_drops","before_ready_drops","requests_reserved","chain_losses"};
 if(!af_closed(d,gate,fields,21))return 0;
 for(unsigned k=0;k<21;++k){int64_t low=k==4?1:k==1?7:k==10||k==11?-1:0;
  if(!af_i(d,gate,fields[k],low,k==4?INT64_MAX:low))return 0;}
 return af_inbox(d,aj_get(d,0,af_prefix[0]),out)&&af_transport(d,aj_get(d,0,af_prefix[1]),out)
  &&af_native(d,aj_get(d,0,af_prefix[2]),out);
}
#endif

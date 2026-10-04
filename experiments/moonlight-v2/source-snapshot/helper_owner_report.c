/* Default-OFF same-native-parent observed helper JSON reader. No activation,
 * external payload adoption, App FD read, mutation, retirement or release.
 */
#define HG_OWNED_OUTPUT_LIBRARY 1
#include "helper_owner_output.c"
#include "helper_report_json.h"
struct hr_rule {const char *key;enum hj_kind kind;int64_t low,high;const char *text;};
#define HR_TRUE(k) {k,HJ_TRUE,0,0,NULL}
#define HR_FALSE(k) {k,HJ_FALSE,0,0,NULL}
#define HR_INT(k,a,b) {k,HJ_INTEGER,a,b,NULL}
#define HR_TEXT(k,s) {k,HJ_STRING,0,0,s}
static const struct hr_rule hr_rules[]={
    HR_TRUE("requested_owner_native_window"),HR_INT("requested_owner_native_window_seconds",1,10),
    HR_TEXT("requested_credential_source","saved-ui"),HR_TRUE("helper_owned_attempt_started"),
    HR_FALSE("saved_UI_private_input_touched"),HR_FALSE("saved_UI_secret_exported"),
    HR_TRUE("requested_v50_profile"),HR_INT("requested_surface_submit_lead_ms",0,0),
    HR_FALSE("requested_stage_diagnostics_enabled"),HR_FALSE("requested_codec_startup_ready_enabled"),
    {"requested_steady_seconds",HJ_NULL,0,0,NULL},HR_FALSE("requested_authenticated_source_input"),
    HR_FALSE("source_pause_only_recovery"),HR_TEXT("requested_network_scope","lan"),
    HR_TEXT("requested_node",""),HR_INT("requested_scope_index",0,0),HR_FALSE("physical_FPS_acceptance"),
    HR_FALSE("requested_credential_save_acceptance"),
    HR_TRUE("first_saved_UI_route_verified"),HR_TRUE("first_saved_UI_allowed_account_verified"),
    HR_TRUE("first_saved_UI_nonempty_credential_verified"),HR_TRUE("first_saved_UI_normal_restore_used"),
    HR_TRUE("normal_UI_login_received_media"),HR_INT("first_actual_fps_limit",30,30),
    HR_INT("first_actual_buffer_ms",80,80),HR_INT("first_actual_video_width",1,960),
    HR_INT("first_actual_video_height",1,960),HR_TRUE("first_v50_profile_button_clicked"),
    HR_INT("first_v50_profile_button_click_count",1,INT64_MAX),HR_TRUE("first_video_profile_readback_verified"),
    HR_FALSE("first_video_profile_is_presented_FPS"),HR_TEXT("first_actual_network_scope","lan"),
    HR_TEXT("first_actual_node",""),HR_TEXT("first_actual_control_host","192.168.9.128"),
    HR_INT("first_actual_control_port",45560,45560),HR_TEXT("first_actual_media_peer_host","192.168.9.128"),
    HR_INT("first_actual_media_peer_port",45963,45963),HR_INT("first_actual_received_frames",15,INT64_MAX),
    HR_INT("first_actual_codec_callback_count",10,INT64_MAX),HR_TRUE("first_network_readback_verified"),
    HR_INT("first_media_transport_code",1,1),HR_TRUE("first_media_transport_is_App_UDP_not_NPC_outer_verification"),
    HR_INT("native_window_started_ns",1,INT64_MAX),HR_INT("native_window_finished_ns",1,INT64_MAX),
    HR_INT("native_window_click_ns",1,INT64_MAX),HR_INT("native_window_budget_end_ns",1,INT64_MAX),
    HR_INT("native_window_descriptor_seconds",30,30),HR_INT("native_window_close_margin_ns",8000000000LL,8000000000LL),
    HR_TRUE("native_window_current_attempt_observed"),HR_FALSE("native_window_server_atomic_hold_verified"),
    HR_TRUE("native_window_diagnostic_events_observed"),{"native_window_samples",HJ_ARRAY,0,0,NULL},
    HR_FALSE("native_window_is_presented_FPS"),HR_TRUE("native_window_no_reconnect"),
    HR_TRUE("native_window_no_source_input"),HR_TRUE("native_window_no_UiAutomation"),
    HR_TRUE("first_exit_dialog_shown"),HR_TRUE("first_exit_repeated_back_same_dialog"),
    HR_TRUE("first_exit_continue_preserved_attempt"),HR_TRUE("first_exit_continue_media_progress"),
    HR_TRUE("first_exit_positive_button_clicked"),HR_TRUE("first_exit_captured_attempt_cancelled"),
    HR_TRUE("first_exit_used_actual_UI_buttons"),HR_TRUE("first_exit_UI_callbacks_observed"),
    {"first_App_report_sha256",HJ_STRING,0,0,NULL},HR_INT("first_surface_submit_lead_ms",0,0),
    HR_INT("first_surface_submit_status_code",0,0),HR_INT("first_surface_submit_wait_count",0,0),
    HR_INT("first_surface_submit_applications",0,0),HR_TRUE("first_surface_submit_execution_verified"),
    HR_INT("first_stage_diagnostics_enabled",0,0),HR_TRUE("first_stage_diagnostics_verified"),
    HR_INT("first_codec_startup_ready_enabled",0,0),HR_TRUE("first_codec_startup_readback_verified"),
    {"first_codec_startup_gate",HJ_OBJECT,0,0,NULL},HR_INT("first_leave_audio_threads_alive",0,0),
    HR_INT("first_leave_audio_thread_observations",1,INT64_MAX),HR_INT("first_leave_audio_thread_observation_ms",0,INT64_MAX),
    HR_TRUE("left_through_App_back"),HR_FALSE("activity_running_after_leave")
};
static const char *const hr_gate[]={"enabled","phase","phase_before_close","failure_code",
    "started_ns","bootstrap_received_ns","prepare_started_ns","ready_ns","fresh_received_ns","committed_ns",
    "bootstrap_pts_us","fresh_pts_us","width","height","bootstrap_admissions","preparing_drops",
    "waiting_drops","fresh_expired_drops","before_ready_drops","requests_reserved","chain_losses"};
struct hr_observed {int64_t click,started,finished,lease;unsigned samples;char App_report_sha256[65];};
static int hr_observed_json(struct hj_doc *d,const unsigned char *bytes,size_t length,unsigned seconds,struct hr_observed *out) {
    if(!out||seconds<1||seconds>10||!hj_parse(d,bytes,length)
            ||d->node[0].count!=sizeof(hr_rules)/sizeof(*hr_rules))return 0;
    memset(out,0,sizeof(*out));
    for(unsigned k=0;k<sizeof(hr_rules)/sizeof(*hr_rules);++k){
        const struct hr_rule *r=hr_rules+k;unsigned i=hj_get(d,0,r->key);
        if(i>=d->used||d->node[i].kind!=r->kind)return 0;
        if(r->kind==HJ_INTEGER&&(d->node[i].integer<r->low||d->node[i].integer>r->high))return 0;
        if(r->text&&!hj_text(d,i,r->text))return 0;
    }
    int64_t duration;
    if(!hj_num(d,0,"requested_owner_native_window_seconds",seconds,seconds,&duration)
            ||!hj_num(d,0,"native_window_click_ns",1,INT64_MAX,&out->click)
            ||!hj_num(d,0,"native_window_started_ns",out->click,INT64_MAX,&out->started)
            ||!hj_num(d,0,"native_window_finished_ns",out->started,INT64_MAX,&out->finished)
            ||!hj_num(d,0,"native_window_budget_end_ns",out->finished,INT64_MAX,&out->lease)
            ||out->lease-out->click!=30000000000LL||out->lease-out->finished<8000000000LL
            ||out->finished-out->started<(int64_t)seconds*1000000000LL)return 0;
    unsigned rows=hj_get(d,0,"native_window_samples");out->samples=d->node[rows].count;
    if(out->samples<2||out->samples>44)return 0;
    int64_t previous=0,frames=0,callbacks=0;unsigned count=0;
    for(unsigned i=rows+1;i<d->node[rows].next;i=d->node[i].next){int64_t n,f,c;
        if(d->node[i].kind!=HJ_OBJECT||d->node[i].count!=3
                ||!hj_num(d,i,"phone_ns",out->started,out->finished,&n)
                ||!hj_num(d,i,"worker_received_frames",frames,INT64_MAX,&f)
                ||!hj_num(d,i,"codec_callback_count",callbacks,INT64_MAX,&c)
                ||n<=previous||(!count&&n!=out->started))return 0;
        previous=n;frames=f;callbacks=c;++count;
    }
    if(count!=out->samples||previous!=out->finished)return 0;
    unsigned gate=hj_get(d,0,"first_codec_startup_gate");
    if(d->node[gate].count!=sizeof(hr_gate)/sizeof(*hr_gate))return 0;
    for(unsigned k=0;k<sizeof(hr_gate)/sizeof(*hr_gate);++k)
        if(!hj_num(d,gate,hr_gate[k],k==10||k==11?-1:0,INT64_MAX,NULL))return 0;
    for(unsigned k=0;k<sizeof(hr_gate)/sizeof(*hr_gate);++k){
        if(k==4){if(!hj_num(d,gate,hr_gate[k],1,INT64_MAX,NULL))return 0;continue;}
        int64_t expected=k==1?7:k==10||k==11?-1:0;
        if(!hj_num(d,gate,hr_gate[k],expected,expected,NULL))return 0;
    }
    unsigned sha=hj_get(d,0,"first_App_report_sha256");if(d->node[sha].len!=64)return 0;
    memcpy(out->App_report_sha256,d->raw+d->node[sha].at,64);
    if(!hex(out->App_report_sha256,64))return 0;
    return hj_flag(d,"native_window_server_atomic_hold_verified",HJ_FALSE);
}
struct helper_report {struct owned_output *output;struct hr_observed observed;int read_attempted,valid,unknown;};
struct helper_report *helper_report_new(const char *nonce,uint64_t size,const char *sha,
        unsigned process_seconds,unsigned sample_seconds,
        int (*eligibility)(void *,enum hg_gate,const struct hg_lifecycle *,uint64_t),
        int (*driver_qualification)(void *,enum bd_gate,const struct bd_bridge *,uint64_t),void *context) {
    struct helper_report *r=calloc(1,sizeof(*r));if(!r)return NULL;
    r->output=owned_output_new(nonce,size,sha,process_seconds,sample_seconds,eligibility,driver_qualification,context);
    if(!r->output){free(r);return NULL;}return r;
}
int helper_report_read(struct helper_report *r,uint64_t end) {
    if(!r||r->read_attempted||r->unknown)return 0;r->read_attempted=1;
    if(!owned_output_snapshot(r->output,end))goto unknown;
    /* The parser gets ONLY this graph's collected actual native child bytes.
     * No supplied JSON/digest/nonce can replace the actual output object. */
    struct hj_doc d;
    if(!within(end)||!hr_observed_json(&d,r->output->payload,r->output->bytes,
            r->output->control->bridge->seconds,&r->observed)||!within(end)
            ||!bd_footer(r->output->control->bridge)||!bd_phase_end(r->output->control->bridge,end))goto unknown;
    r->valid=1;return 1;
unknown:
    r->unknown=1;memset(&r->observed,0,sizeof(r->observed));hgs_failed(r->output->control);return 0;
}
#ifndef HG_HELPER_REPORT_LIBRARY
int main(int argc,char **argv) {
    (void)argv;if(argc!=1)return 2;
    puts("{\"event\":\"helper_report_prepared\",\"activation_available\":false,\"App_FD_read_verified\":false,\"Attempt_hold_verified\":false,\"release_authorized\":false}");return 0;
}
#endif

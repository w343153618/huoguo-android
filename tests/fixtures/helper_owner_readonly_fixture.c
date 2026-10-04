/* Actual owned host query children, synthetic Android outputs and host FDs. */
#define HG_HELPER_READ_LIBRARY 1
#define HG_HELPER_READ_FIXTURE 1
#include "../../experiments/moonlight-v2/source-snapshot/helper_owner_readonly.c"

static void changed_package(struct hg_readonly *q) {
    char path[2048];snprintf(path,sizeof(path),"%s/~~fixture/name/base.apk",q->fixture_root);
    const char *m=q->fixture_mode;
    if (!strcmp(m,"contents_changed")) {
        int fd=open(path,O_WRONLY);if (fd>=0) {pwrite(fd,"X",1,0);close(fd);}
    } else if (!strcmp(m,"mode_changed")) chmod(path,0600);
    else if (!strcmp(m,"growth")) {int fd=open(path,O_WRONLY|O_APPEND);if (fd>=0) {write(fd,"X",1);close(fd);}}
    else if (!strcmp(m,"file_replaced")) {
        char old[2200];snprintf(old,sizeof(old),"%s.old",path);rename(path,old);
        int fd=open(path,O_WRONLY|O_CREAT|O_EXCL,0644);if (fd>=0) {write(fd,"public host APK standin\n",23);close(fd);}
    } else if (!strcmp(m,"parent_replaced")) {
        char parent[2048],old[2200],from[2300];snprintf(parent,sizeof(parent),"%s/~~fixture/name",q->fixture_root);
        snprintf(old,sizeof(old),"%s.old",parent);rename(parent,old);mkdir(parent,0755);
        snprintf(from,sizeof(from),"%s/base.apk",old);rename(from,path);
    }
}
int main(int argc,char **argv) {
    if (argc!=4||strcmp(argv[1],"--fixture")) return 2;
    const char *m=argv[3];const char *modes[]={"valid","absent","wrong_path","traversal","multiple","later_path",
        "active_App","active_helper","later_active","duplicate_PID","bad_UID","nonzero","stderr","overflow",
        "embedded_NUL","ignore_TERM","held_EOF","contents_changed","mode_changed","growth","file_replaced",
        "parent_replaced","repeat","deadline","cancel","autoreap","invalid_kind","idle","App","above_soft_limit",
        "kernel_NAME","clone_UID","clone_App","clone_helper","bad_UID_range","control_NAME","target_space","bad_header_tail"};
    int allowed=0;for (unsigned i=0;i<sizeof(modes)/sizeof(modes[0]);++i) if (!strcmp(m,modes[i])) allowed=1;
    if (!allowed) return 2;
    /* Limit only this fresh host fixture process; actual Android limits are
     * independently queried and unsupported inherited limits still refuse. */
    int inherited=-1;
    if (!strcmp(m,"above_soft_limit")) {
        int fd=open("/dev/null",O_RDONLY);if (fd<0) return 2;
        inherited=fcntl(fd,F_DUPFD,1600);close(fd);if (inherited<0) return 2;
    }
    struct rlimit limit;if (getrlimit(RLIMIT_NOFILE,&limit)) return 2;
    if (limit.rlim_cur>1024) {limit.rlim_cur=1024;if (setrlimit(RLIMIT_NOFILE,&limit)) return 2;}
    struct hg_readonly *q=hg_readonly_new();if (!q) return 2;q->fixture_root=argv[2];q->fixture_mode=m;
    q->fixture_inherited=inherited;
    q->fixture_after_hash=changed_package;
    if (!strcmp(m,"cancel")) hg_readonly_request_cancel(SIGTERM);
    if (!strcmp(m,"autoreap")) signal(SIGCHLD,SIG_IGN);
    enum hg_read_kind kind=!strcmp(m,"absent")?HG_READ_HELPER_ABSENT:!strcmp(m,"idle")?HG_READ_PROCESS_IDLE:
        !strcmp(m,"App")?HG_READ_APP_PRESENT:HG_READ_HELPER_PRESENT;
    if (!strcmp(m,"invalid_kind")) kind=(enum hg_read_kind)19;
    uint64_t end=q->constructed+15000;if (!strcmp(m,"deadline")) end+=1;
    int accepted=hg_readonly_snapshot(q,kind,end),repeat=0;
    if (!strcmp(m,"repeat")) repeat=!hg_readonly_snapshot(q,kind,end)&&q->unknown;
    int unreaped=q->child&&!q->reaped,initial_term=q->term_calls,initial_kill=q->kill_calls;
    int initial_EOF=q->eof[0]&&q->eof[1],close_refused=!hg_readonly_close(q);
    if (q->fixture_hold>=0) {close(q->fixture_hold);q->fixture_hold=-1;}
    if (close_refused) hg_readonly_stop_owned(q,700);
    int closed=hg_readonly_close(q);
    printf("{\"event\":\"helper_readonly_fixture_footer\",\"host_query_snapshot_accepted\":%s,\"unknown\":%s,\"queries_natural\":%d,\"actual_child_reaped\":%s,\"initial_dual_EOF\":%s,\"unreaped_at_failure\":%s,\"close_initially_refused\":%s,\"initial_TERM_calls\":%d,\"initial_KILL_calls\":%d,\"final_TERM_calls\":%d,\"final_KILL_calls\":%d,\"overflow\":%s,\"repeat_sticky_refused\":%s,\"readonly_handles_closed\":%s,\"Android_query_outputs_synthetic\":true,\"actual_Android_queries\":false,\"operator_Attempt_server_lease_verified\":false,\"remote_PM_quiescence_verified\":false,\"scope_retirement_or_release\":false}\n",
        accepted?"true":"false",q->unknown?"true":"false",q->natural_queries,q->reaped?"true":"false",
        initial_EOF?"true":"false",unreaped?"true":"false",close_refused?"true":"false",initial_term,initial_kill,
        q->term_calls,q->kill_calls,q->overflow?"true":"false",repeat?"true":"false",closed?"true":"false");
    if (inherited>=0) close(inherited);
    free(q);return accepted&&closed?0:2;
}

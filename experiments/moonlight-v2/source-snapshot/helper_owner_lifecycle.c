/* Default-OFF same-parent helper lifecycle and post-PM FD retirement kernel.
 * No Android activation entry or remote qualification implementation.
 * The trusted caller must supply independently reviewed finite callbacks.
 * Requests, callbacks' serialized output and local PM0 are not permissions.
 */
#define HG_HELPER_CHANNEL_LIBRARY 1
#include "helper_owner_channel.c"

enum hg_gate {
    HG_BEFORE_SCOPE=1,HG_BEFORE_INSTALL,HG_AFTER_INSTALL,HG_DRIVER_CLOSED,
    HG_BEFORE_UNINSTALL,HG_AFTER_UNINSTALL,HG_BEFORE_RETIRE
};
enum hg_lifecycle_phase {
    HG_NEW=0,HG_UPLOADED,HG_INSTALLED,HG_DRIVER_FINISHED,HG_UNINSTALLED,
    HG_RETIRED,HG_UNKNOWN
};
struct hg_lifecycle {
    struct hg_owner *owner;
    struct hg_channel channel;
    enum hg_lifecycle_phase phase;
    int (*qualify)(void *,enum hg_gate,const struct hg_owner *);
    void *context;
    int install_natural,uninstall_natural;
    int file_removed,stage_removed,parent_removed,retirement_started;
};
/* Never accept an existing hg_owner, receipt, child PID or old scope. This
 * instance creates and retains its own actual owner across all four requests.
 * Construction itself reads no device, file or channel payload.
 */
struct hg_lifecycle *hg_lifecycle_new(int control,const char *nonce,uint64_t size,
        const char *sha,int (*qualify)(void *,enum hg_gate,const struct hg_owner *),void *context) {
    /* Fixed native parent's stdin only; do not leak a later arbitrary control
     * FD into the PM child or accept an adopted channel from serialized data.
     */
    if (!qualify||control!=STDIN_FILENO) return NULL;
    struct hg_lifecycle *l=calloc(1,sizeof(*l));if (!l) return NULL;
    l->owner=hg_owner_new();
    if (!l->owner||!hg_channel_init(&l->channel,control,nonce,size,sha)) {
        free(l->owner);free(l);return NULL;
    }
    l->qualify=qualify;l->context=context;return l;
}
static int fail_lifecycle(struct hg_lifecycle *l) {
    if (l) {l->phase=HG_UNKNOWN;stop_channel(&l->channel);}return 0;
}
static int qualified(struct hg_lifecycle *l,enum hg_gate gate,uint64_t end) {
    /* Finite callback/probe enforcement belongs to the complete caller.
     * Checking time before/after is cooperative, not filesystem hard timing.
     */
    return l&&l->phase!=HG_UNKNOWN&&!cancelled&&finite_deadline(end)
        &&l->qualify(l->context,gate,l->owner)==1&&within(end)&&!cancelled;
}
int hg_lifecycle_upload(struct hg_lifecycle *l,const char *base,uint64_t dev,uint64_t ino,uint64_t end) {
    if (!l||l->phase!=HG_NEW||!qualified(l,HG_BEFORE_SCOPE,end)
            ||!hg_channel_upload(&l->channel,l->owner,base,dev,ino,end)) return fail_lifecycle(l);
    l->phase=HG_UPLOADED;return 1;
}
static int natural_child(struct hg_owner *o) {
    return o&&o->child&&o->child_reaped&&WIFEXITED(o->child_status)
        &&WEXITSTATUS(o->child_status)==0&&o->pipe_EOF[0]&&o->pipe_EOF[1]
        &&!o->term_calls&&!o->kill_calls&&!o->overflow&&!o->pipe_failed
        &&o->output_size[0]==8&&!memcmp(o->output[0],"Success\n",8)&&!o->output_size[1];
}
static int request(struct hg_lifecycle *l,enum hg_lifecycle_phase phase,
        enum hg_request kind,uint64_t end) {
    return l&&l->phase==phase&&finite_deadline(end)
        &&hg_channel_next_request(&l->channel,end)==kind;
}
static unsigned remaining_PM_budget(uint64_t end) {
    uint64_t n=now_ms();if (!n||end<=n) return 0;
    return end-n>1000?1000:(unsigned)(end-n);
}
int hg_lifecycle_install(struct hg_lifecycle *l,uint64_t end,const char *fixture_action) {
    if (!request(l,HG_UPLOADED,HG_INSTALL,end)||!qualified(l,HG_BEFORE_INSTALL,end)
            ||!hg_owner_pm_start_fixed(l->owner,1,fixture_action)
            ||!hg_owner_pm_wait(l->owner,remaining_PM_budget(end))||!natural_child(l->owner)
            ||!qualified(l,HG_AFTER_INSTALL,end)) return fail_lifecycle(l);
    l->install_natural=1;l->phase=HG_INSTALLED;return 1;
}
int hg_lifecycle_driver_done(struct hg_lifecycle *l,uint64_t end) {
    /* Wire DRIVER_DONE cannot stand in for actual held-driver wait+dual EOF,
     * current captured Attempt/receiver/Surface and codec/audio/input cleanup.
     * Only the independently bound caller callback may check these objects.
     */
    if (!request(l,HG_INSTALLED,HG_DRIVER_DONE,end)||!l->install_natural
            ||!natural_child(l->owner)||!qualified(l,HG_DRIVER_CLOSED,end)) return fail_lifecycle(l);
    l->phase=HG_DRIVER_FINISHED;return 1;
}
static int finish_child_slot(struct hg_owner *o) {
    /* Internal only, no PID setter/adoption. Record install completion first.
     * A later child never reuses output/status from a prior client.
     */
    if (!natural_child(o)) return 0;
    for (int i=0;i<2;++i) {
        int fd=o->pipes[i];o->pipes[i]=-1;if (fd>=0&&close(fd)) return 0;
    }
    o->child=0;o->child_reaped=0;o->child_status=0;
    for (int i=0;i<2;++i) {o->pipe_EOF[i]=0;o->output_size[i]=0;}
    return 1;
}
int hg_lifecycle_uninstall(struct hg_lifecycle *l,uint64_t end,const char *fixture_action) {
    if (!request(l,HG_DRIVER_FINISHED,HG_UNINSTALL,end)||!l->install_natural
            ||!qualified(l,HG_BEFORE_UNINSTALL,end)||!finish_child_slot(l->owner)
            ||!hg_owner_pm_start_fixed(l->owner,2,fixture_action)
            ||!hg_owner_pm_wait(l->owner,remaining_PM_budget(end))||!natural_child(l->owner)
            ||!qualified(l,HG_AFTER_UNINSTALL,end)) return fail_lifecycle(l);
    l->uninstall_natural=1;l->phase=HG_UNINSTALLED;return 1;
}
static int empty_entries(int fd) {
    int copy=dup(fd);if (copy<0) return 0;
    DIR *d=fdopendir(copy);if (!d) {close(copy);return 0;}
    rewinddir(d);int good=1;struct dirent *e;errno=0;
    while ((e=readdir(d))) if (strcmp(e->d_name,".")&&strcmp(e->d_name,"..")) {good=0;break;}
    int error=errno;if (closedir(d)) good=0;return good&&!error;
}
static int after_one_removal(const struct stat *a,const struct stat *b) {
    return a->st_dev==b->st_dev&&a->st_ino==b->st_ino&&a->st_uid==b->st_uid
        &&a->st_gid==b->st_gid&&a->st_mode==b->st_mode
        &&(b->st_nlink==a->st_nlink||(a->st_nlink&&b->st_nlink==a->st_nlink-1));
}
static int base_stable(struct hg_owner *o) {
    struct stat b;return !fstat(o->base,&b)&&b.st_dev==o->base_stat.st_dev
        &&b.st_ino==o->base_stat.st_ino&&b.st_uid==o->base_stat.st_uid
        &&b.st_gid==o->base_stat.st_gid&&b.st_mode==o->base_stat.st_mode;
}
static int parent_pair(struct hg_owner *o) {
    return base_stable(o)&&exact_node(o->parent,&o->parent_stat)
        &&named_node(o->base,o->name,&o->parent_stat);
}
int hg_lifecycle_retire(struct hg_lifecycle *l,uint64_t end) {
    if (!request(l,HG_UNINSTALLED,HG_RETIRE,end)||l->retirement_started
            ||!l->install_natural||!l->uninstall_natural||!natural_child(l->owner)
            ||!qualified(l,HG_BEFORE_RETIRE,end)) return fail_lifecycle(l);
    struct hg_owner *o=l->owner;
    if (!o->created||!o->upload_verified||!o->writer_closed||o->writer>=0
            ||!current_scope(o)||!exact_node(o->reader,&o->file_stat)
            ||!hash_file(o->reader,o->file_stat.st_size,l->channel.sha,end)
            ||!exact_node(o->reader,&o->file_stat)||!current_scope(o)||cancelled||!within(end))
        return fail_lifecycle(l);
    /* unlinkat(FD,name) is NOT atomic compare-and-unlink. The fresh root-only
     * ancestors exclude ordinary writers; the BEFORE_RETIRE caller must also
     * independently exclude privileged writers and remaining external write
     * FDs. A hash or completed PM client does not prove those prerequisites.
     * Any partial operation sticks UNKNOWN. Never resume/delete a later scope.
     */
    l->retirement_started=1;
    if (unlinkat(o->stage,"owned.apk",0)) return fail_lifecycle(l);l->file_removed=1;
#ifdef HG_HELPER_LIFECYCLE_FIXTURE
    extern void hg_fixture_after_file_unlink(struct hg_owner *);
    hg_fixture_after_file_unlink(o);
#endif
    struct stat s,oldstage=o->stage_stat;
    if (!within(end)||cancelled||fstat(o->stage,&s)||!after_one_removal(&oldstage,&s))
        return fail_lifecycle(l);
    o->stage_stat=s;
    if (!parent_pair(o)||!named_node(o->parent,"stage",&s)||!empty_entries(o->stage)
            ||!only_entry(o->parent,"stage")||unlinkat(o->parent,"stage",AT_REMOVEDIR))
        return fail_lifecycle(l);
    l->stage_removed=1;
    struct stat p,oldparent=o->parent_stat;
    if (!within(end)||cancelled||fstat(o->parent,&p)||!after_one_removal(&oldparent,&p))
        return fail_lifecycle(l);
    o->parent_stat=p;
    if (!parent_pair(o)||!empty_entries(o->parent)||unlinkat(o->base,o->name,AT_REMOVEDIR))
        return fail_lifecycle(l);
    l->parent_removed=1;struct stat gone;
    if (!within(end)||cancelled||!base_stable(o)
            ||!fstatat(o->base,o->name,&gone,AT_SYMLINK_NOFOLLOW)||errno!=ENOENT)
        return fail_lifecycle(l);
    o->possible_scope=0;l->phase=HG_RETIRED;return 1;
}
int hg_lifecycle_close(struct hg_lifecycle *l) {
    /* No destructor or unknown close. Hold parent/PM/control/scope objects.
     * Only actual retirement authorizes closing these owned handles. This
     * still does not authorize gateway/server lease or reservation release.
     */
    if (!l||l->phase!=HG_RETIRED||!hg_owner_close_handles(l->owner)) return 0;
    int fd=l->channel.input;l->channel.input=-1;
    return fd>=0&&close(fd)==0;
}

#ifndef HG_HELPER_LIFECYCLE_LIBRARY
int main(int argc,char **argv) {
    (void)argv;
    if (argc!=1) return 2;
    puts("{\"event\":\"helper_lifecycle_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"complete_live_qualification_implemented\":false,\"release_authorized\":false}");
    return 0;
}
#endif

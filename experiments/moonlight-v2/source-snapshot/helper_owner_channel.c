/* Default-OFF framed transport for a native-owned helper upload/control FD.
 * This partial library only returns closed requests; no frame grants PM,
 * driver/package ownership, operator permission or reservation release.
 * Production CLI rejects activation. No old scope/PID/JSON adoption.
 */
#define main hg_owner_inert_main_unused
#include "helper_scope_owner.c"
#undef main

#define HG_HEADER_BYTES 112
enum hg_request { HG_UPLOAD=1,HG_SEAL=2,HG_INSTALL=3,HG_DRIVER_DONE=4,
    HG_UNINSTALL=5,HG_RETIRE=6,HG_CANCEL=7 };
struct hg_channel {
    int input;char nonce[25],sha[65];uint64_t size,next_sequence;
    enum hg_request expected;int stopped,upload_verified,requests;
};
static uint64_t wire_u64(const unsigned char *b) {
    uint64_t n=0;for (int i=0;i<8;++i) n=(n<<8)|b[i];return n;
}
static uint32_t wire_u32(const unsigned char *b) {
    uint32_t n=0;for (int i=0;i<4;++i) n=(n<<8)|b[i];return n;
}
static int channel_read(int input,unsigned char *data,size_t size,uint64_t end) {
    size_t got=0;
    while (got<size) {
        if (cancelled||!within(end)) return 0;
        struct pollfd p={input,POLLIN,0};int r=poll(&p,1,20);
        if (r<0) {if (errno==EINTR) continue;return 0;}if (!r) continue;
        ssize_t n=read(input,data+got,size-got);
        if (n<0) {if (errno==EINTR||errno==EAGAIN) continue;return 0;}
        if (!n) return 0;got+=(size_t)n;
    }
    return !cancelled&&within(end);
}
int hg_channel_init(struct hg_channel *c,int input,const char *nonce,uint64_t size,const char *sha) {
    if (!c||input<0||!hex(nonce,24)||!hex(sha,64)||!size||size>MAX_APK) return 0;
#ifndef HG_HELPER_CHANNEL_FIXTURE
    if (size!=86419||strcmp(sha,expected_sha)) return 0;
#endif
    int flags=fcntl(input,F_GETFL);
    if (flags<0||fcntl(input,F_SETFL,flags|O_NONBLOCK)) return 0;
    memset(c,0,sizeof(*c));c->input=input;c->size=size;c->expected=HG_UPLOAD;
    memcpy(c->nonce,nonce,25);memcpy(c->sha,sha,65);return 1;
}
/* caller deadline is monotonic, finite and <=3s away; no header/env timeout. */
static int finite_deadline(uint64_t end) {
    uint64_t n=now_ms();return n&&end>n&&end-n<=3000;
}
static int channel_header(struct hg_channel *c,unsigned char h[HG_HEADER_BYTES],uint64_t end) {
    if (!c||c->stopped||!finite_deadline(end)
            ||!channel_read(c->input,h,HG_HEADER_BYTES,end)) return 0;
    if (memcmp(h,"HGHC0001",8)||h[9]||h[10]||h[11]||memcmp(h+16,c->nonce,24)
            ||wire_u64(h+40)!=c->next_sequence) return 0;
    enum hg_request kind=(enum hg_request)h[8];uint32_t size=wire_u32(h+12);
    if (kind==HG_CANCEL) {
        if (size||memcmp(h+48,"0000000000000000000000000000000000000000000000000000000000000000",64)) return 0;
        c->next_sequence++;c->stopped=1;hg_owner_request_cancel(SIGTERM);return 2;
    }
    if (kind!=c->expected) return 0;
    if (kind==HG_UPLOAD) {
        if (size!=c->size||memcmp(h+48,c->sha,64)) return 0;
    } else if (size||memcmp(h+48,"0000000000000000000000000000000000000000000000000000000000000000",64)) return 0;
    c->next_sequence++;return 1;
}
static void stop_channel(struct hg_channel *c) {if (c) c->stopped=1;}
/* A valid upload header is read before any scope creation. The exact-length
 * payload is followed by an exact SEAL record, replacing old stdin EOF.
 * Extraneous payload bytes corrupt SEAL and refuse. The actual sole APK write
 * FD is closed before rehash; stdin/control remains open and is never forked.
 */
int hg_channel_upload(struct hg_channel *c,struct hg_owner *o,const char *base,
                      uint64_t dev,uint64_t ino,uint64_t end) {
    unsigned char h[HG_HEADER_BYTES];
    if (!c||!o||channel_header(c,h,end)!=1||!hg_owner_create(o,base,c->nonce,dev,ino)) goto fail;
    unsigned char bytes[4096];uint64_t at=0;
    while (at<c->size) {
        size_t need=(size_t)(c->size-at);if (need>sizeof(bytes)) need=sizeof(bytes);
        if (!channel_read(c->input,bytes,need,end)) goto fail;
        size_t sent=0;
        while (sent<need) {
            if (cancelled||!within(end)) goto fail;
            ssize_t n=pwrite(o->writer,bytes+sent,need-sent,(off_t)(at+sent));
            if (n<=0) goto fail;sent+=(size_t)n;
        }
        at+=need;
    }
    c->expected=HG_SEAL;
    if (channel_header(c,h,end)!=1) goto fail;
    struct stat after;
    if (fsync(o->writer)||fstat(o->writer,&after)||!same_identity(&after,&o->file_stat)
            ||after.st_size!=(off_t)c->size||!current_scope(o)
            ||!named_node(o->stage,"owned.apk",&after)) goto fail;
    o->file_stat=after;int writer=o->writer;o->writer=-1;
    if (close(writer)) goto fail;o->writer_closed=1;
    o->reader=openat(o->stage,"owned.apk",O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    if (o->reader<0||!exact_node(o->reader,&o->file_stat)
            ||!hash_file(o->reader,(off_t)c->size,c->sha,end)
            ||!exact_node(o->reader,&o->file_stat)||!current_scope(o)) goto fail;
    o->upload_verified=1;c->upload_verified=1;c->expected=HG_INSTALL;return 1;
fail:
    stop_channel(c);return 0;
}
/* Request parsing only. Advancing this wire phase never claims INSTALL,
 * driver completion, UNINSTALL or RETIRE actually happened. Trusted whole
 * caller must separately bind held objects, normal UI/operator/server lease,
 * exact package and independent cleanup. No such live caller exists here.
 */
enum hg_request hg_channel_next_request(struct hg_channel *c,uint64_t end) {
    unsigned char h[HG_HEADER_BYTES];
    if (!c||!c->upload_verified||c->expected<HG_INSTALL) return 0;
    int r=channel_header(c,h,end);
    if (r==2) return HG_CANCEL;
    if (r!=1) {stop_channel(c);return 0;}
    enum hg_request kind=(enum hg_request)h[8];c->requests++;
    if (kind==HG_RETIRE) c->stopped=1;else c->expected=(enum hg_request)(kind+1);
    return kind;
}
#ifndef HG_HELPER_CHANNEL_LIBRARY
int main(int argc,char **argv) {
    if (argc==1) {puts("{\"event\":\"helper_channel_prepared\",\"operations_started\":false,\"production_activation_available\":false,\"release_authorized\":false}");return 0;}
#ifndef HG_HELPER_CHANNEL_FIXTURE
    (void)argv;return 2;
#else
    if (argc!=8||strcmp(argv[1],"--fixture")||!hex(argv[3],24)||!hex(argv[7],64)) return 2;
    uint64_t dev,ino,size;
    if (!number(argv[4],&dev)||!number(argv[5],&ino)||!number(argv[6],&size)) return 2;
    struct sigaction sa;memset(&sa,0,sizeof(sa));sa.sa_handler=hg_owner_request_cancel;sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM,&sa,NULL);sigaction(SIGINT,&sa,NULL);
    struct hg_owner *o=hg_owner_new();if (!o) return 2;struct hg_channel c;
    int init=hg_channel_init(&c,STDIN_FILENO,argv[3],size,argv[7]);
    int upload=init&&hg_channel_upload(&c,o,argv[2],dev,ino,now_ms()+1000);
    printf("{\"event\":\"helper_channel_upload\",\"fixture_build\":true,\"upload_verified\":%s,\"writer_closed\":%s,\"control_FD_still_open\":%s,\"release_authorized\":false}\n",upload?"true":"false",o->writer_closed?"true":"false",fcntl(STDIN_FILENO,F_GETFD)>=0?"true":"false");fflush(stdout);
    int complete=0;
    while (upload&&!c.stopped) {
        enum hg_request kind=hg_channel_next_request(&c,now_ms()+1000);
        if (!kind) break;
        printf("{\"event\":\"helper_channel_request\",\"fixture_build\":true,\"request\":%d,\"request_only\":true,\"PM_driver_retirement_performed\":false,\"operator_or_lease_verified\":false,\"release_authorized\":false}\n",kind);fflush(stdout);
        if (kind==HG_RETIRE) complete=1;
    }
    close(STDIN_FILENO);int closed=hg_owner_close_handles(o);
    printf("{\"event\":\"helper_channel_fixture_footer\",\"fixture_build\":true,\"wire_sequence_complete\":%s,\"requests\":%d,\"handles_closed\":%s,\"scope_may_remain\":%s,\"PM_invocations\":0,\"driver_cleanup_verified\":false,\"retirement_performed\":false,\"release_authorized\":false}\n",complete?"true":"false",init?c.requests:0,closed?"true":"false",o->possible_scope?"true":"false");
    free(o);return complete&&closed?0:2;
#endif
}
#endif

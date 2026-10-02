#include "ice_udp_backend.h"
#include <juice/juice.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <ifaddrs.h>
#include <net/if.h>
#include <arpa/inet.h>
#endif

#define ROUNDS 300
typedef struct {
    unsigned expected_sender;
    atomic_uint_fast64_t last_sequence, accepted, errors;
} observations;

static uint64_t monotonic_us(void) {
    struct timespec value; clock_gettime(CLOCK_MONOTONIC, &value);
    return (uint64_t)value.tv_sec * 1000000 + (uint64_t)value.tv_nsec / 1000;
}
static void fill(uint8_t *data, size_t size, uint32_t sequence, unsigned sender) {
    memcpy(data, "HGIP", 4); data[4] = (uint8_t)sender;
    for (unsigned i = 0; i < 4; ++i) data[5+i] = (uint8_t)(sequence >> (24-8*i));
    for (size_t i = 9; i < size; ++i) data[i] = (uint8_t)(sequence + 17*i + sender);
}
static void got(const uint8_t *data, size_t size, void *user) {
    observations *received = user;
    if (size < 9 || size > 1200 || memcmp(data, "HGIP", 4) || data[4] != received->expected_sender) {
        atomic_fetch_add(&received->errors, 1); return;
    }
    uint32_t sequence = 0;
    for (unsigned i = 0; i < 4; ++i) sequence = (sequence << 8) | data[5+i];
    for (size_t i = 9; i < size; ++i) {
        if (data[i] != (uint8_t)(sequence + 17*i + data[4])) {
            atomic_fetch_add(&received->errors, 1); return;
        }
    }
    atomic_fetch_add(&received->accepted, 1);
    atomic_store(&received->last_sequence, sequence);
}
static int wait_sequence(observations *observed, unsigned sequence) {
    uint64_t deadline = monotonic_us()+1000000;
    while (atomic_load(&observed->last_sequence) != sequence) {
        if (monotonic_us() >= deadline || atomic_load(&observed->errors)) return -1;
        usleep(100);
    }
    return 0;
}
static int compare(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left, b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

#ifdef __APPLE__
static int raw_fail_closed_checks(const char *interface_name) {
    juice_set_log_level(JUICE_LOG_LEVEL_NONE);
    /* No SDP is retrieved or emitted. Unknown interface must fail at udp.c,
       and shared-mode binding and TCP enabling must be rejected. */
    struct ifaddrs *interfaces;
    if (getifaddrs(&interfaces)) return 0;
    char address[INET_ADDRSTRLEN] = {0};
    for (struct ifaddrs *item = interfaces; item; item = item->ifa_next) {
        if (item->ifa_addr && item->ifa_addr->sa_family == AF_INET && !strcmp(item->ifa_name, interface_name)) {
            inet_ntop(AF_INET, &((const struct sockaddr_in *)item->ifa_addr)->sin_addr, address, sizeof(address)); break;
        }
    }
    freeifaddrs(interfaces);
    if (!address[0]) return 0;
    juice_config_t config = {0};
    config.bind_address = address;
    config.bind_interface = "hg-no-such-iface";
    config.concurrency_mode = JUICE_CONCURRENCY_MODE_THREAD;
    juice_agent_t *agent = juice_create(&config);
    if (!agent) return 0;
    int closed = juice_gather_candidates(agent) != 0;
    int tcp_disabled = juice_set_ice_tcp_mode(agent, JUICE_ICE_TCP_MODE_ACTIVE) != 0;
    juice_destroy(agent);
    config.bind_interface = interface_name;
    config.concurrency_mode = JUICE_CONCURRENCY_MODE_MUX;
    agent = juice_create(&config);
    int mux_rejected = agent == NULL;
    if (agent) juice_destroy(agent);
    config.concurrency_mode = JUICE_CONCURRENCY_MODE_POLL;
    agent = juice_create(&config);
    int poll_rejected = agent == NULL;
    if (agent) juice_destroy(agent);
    return closed && tcp_disabled && mux_rejected && poll_rejected;
}
#endif

int main(int argc, char **argv) {
    const char *interface_name = argc == 2 ? argv[1] : "en7";
    if (argc > 2 || (strcmp(interface_name, "en7") && strcmp(interface_name, "en0"))) {
        printf("{\"schema\":1,\"passed\":false,\"error_code\":1}\n"); return 1;
    }
    observations received[2] = {{.expected_sender = 2}, {.expected_sender = 1}};
    hg_ice_config config = {0};
    config.bind_interface = interface_name; config.receive = got;
    hg_ice *peers[2] = {NULL, NULL};
    hg_ice_stats stats[2] = {{0}, {0}};
    char descriptions[2][JUICE_MAX_SDP_STRING_LEN] = {{0}, {0}};
    uint64_t roundtrip_us[ROUNDS] = {0}, rounds = 0, payload_bytes = 0;
    int passed = 0, error = 0, closed = 0, tcp_candidate_rejected = 0, oversize_rejected = 0;
    uint64_t begin = monotonic_us();
    for (int i = 0; i < 2; ++i) {
        config.user = &received[i]; peers[i] = hg_ice_create(&config);
        if (!peers[i]) { error = 2; goto finish; }
    }
    config.bind_interface = "utun0";
    hg_ice *forbidden = hg_ice_create(&config);
    if (forbidden) { hg_ice_destroy(forbidden); error = 3; goto finish; }
#ifdef __APPLE__
    closed = raw_fail_closed_checks(interface_name);
#endif
    if (!closed) { error = 4; goto finish; }
    for (int i = 0; i < 2; ++i) if (hg_ice_gather(peers[i])) { error = 5; goto finish; }
    uint64_t deadline = monotonic_us()+3000000;
    for (;;) {
        for (int i = 0; i < 2; ++i) hg_ice_get_stats(peers[i], &stats[i]);
        if (stats[0].gathering_done && stats[1].gathering_done) break;
        if (monotonic_us() >= deadline) { error = 6; goto finish; }
        usleep(1000);
    }
    for (int i = 0; i < 2; ++i) {
        if (hg_ice_local_description_private(peers[i], descriptions[i], sizeof(descriptions[i]))) { error = 7; goto finish; }
    }
    tcp_candidate_rejected = hg_ice_remote_description_private(peers[0],
        "a=candidate:0 1 TCP 1 127.0.0.1 9 typ host tcptype passive\r\n") != 0;
    if (!tcp_candidate_rejected) { error = 8; goto finish; }
    for (int i = 0; i < 2; ++i) {
        if (hg_ice_remote_description_private(peers[i], descriptions[1-i])) { error = 9; goto finish; }
    }
    /* ICE credentials never leave the two in-memory buffers. */
    memset(descriptions, 0, sizeof(descriptions));
    deadline = monotonic_us()+5000000;
    for (;;) {
        for (int i = 0; i < 2; ++i) hg_ice_get_stats(peers[i], &stats[i]);
        if (stats[0].state >= JUICE_STATE_CONNECTED && stats[0].state <= JUICE_STATE_COMPLETED &&
            stats[1].state >= JUICE_STATE_CONNECTED && stats[1].state <= JUICE_STATE_COMPLETED) break;
        if (monotonic_us() >= deadline) { error = 10; goto finish; }
        usleep(1000);
    }
    uint8_t payload[1201];
    const size_t sizes[] = {16, 256, 1080, 1200};
    oversize_rejected = hg_ice_send(peers[0], payload, sizeof(payload)) != 0;
    if (!oversize_rejected) { error = 11; goto finish; }
    for (uint32_t sequence = 1; sequence <= ROUNDS; ++sequence) {
        size_t size = sizes[(sequence-1)%4];
        fill(payload, size, sequence, 1);
        uint64_t sent = monotonic_us();
        if (hg_ice_send(peers[0], payload, size) || wait_sequence(&received[1], sequence)) { error = 12; goto finish; }
        fill(payload, size, sequence, 2);
        if (hg_ice_send(peers[1], payload, size) || wait_sequence(&received[0], sequence)) { error = 13; goto finish; }
        roundtrip_us[rounds++] = monotonic_us()-sent; payload_bytes += 2*size;
    }
    for (int i = 0; i < 2; ++i) {
        hg_ice_get_stats(peers[i], &stats[i]);
        if (stats[i].socket_binding_audits != 1 || !stats[i].requested_ifindex ||
            stats[i].actual_ifindex != stats[i].requested_ifindex ||
            stats[i].selected_local_class != 1 || stats[i].selected_remote_class != 1 ||
            stats[i].sent_datagrams != ROUNDS || stats[i].received_datagrams != ROUNDS ||
            atomic_load(&received[i].errors)) { error = 14; goto finish; }
    }
    passed = 1;
finish:
    memset(descriptions, 0, sizeof(descriptions));
    uint64_t elapsed_us = monotonic_us()-begin;
    for (int i = 0; i < 2; ++i) {
        if (peers[i]) { hg_ice_get_stats(peers[i], &stats[i]); hg_ice_destroy(peers[i]); }
    }
    if (rounds) qsort(roundtrip_us, rounds, sizeof(roundtrip_us[0]), compare);
    printf("{\"schema\":1,\"scope\":\"same_host_real_udp_host_candidate_only_not_WAN\","
           "\"passed\":%s,\"error_code\":%d,\"elapsed_us\":%" PRIu64 ",\"roundtrips\":%" PRIu64 ","
           "\"application_datagrams\":%" PRIu64 ",\"application_bytes\":%" PRIu64 ","
           "\"roundtrip_p50_us\":%" PRIu64 ",\"roundtrip_p95_us\":%" PRIu64 ",\"roundtrip_max_us\":%" PRIu64 ","
           "\"fail_closed_checks_passed\":%s,\"tcp_candidate_rejected\":%s,\"oversize_rejected\":%s,"
           "\"stun_used\":false,\"turn_used\":false,\"signaling_memory_only\":true,"
           "\"tcp_media_used\":false,\"peers\":[",
           passed ? "true":"false", error, elapsed_us, rounds, 2*rounds, payload_bytes,
           rounds ? roundtrip_us[(rounds-1)/2]:0, rounds ? roundtrip_us[(rounds-1)*95/100]:0,
           rounds ? roundtrip_us[rounds-1]:0, closed ? "true":"false",
           tcp_candidate_rejected ? "true":"false", oversize_rejected ? "true":"false");
    for (int i = 0; i < 2; ++i) {
        printf("%s{\"state\":%" PRIu64 ",\"host_candidates\":%" PRIu64 ",\"selected_local_class\":%" PRIu64 ","
               "\"selected_remote_class\":%" PRIu64 ",\"socket_binding_audits\":%" PRIu64 ","
               "\"requested_ifindex\":%" PRIu64 ",\"actual_ifindex\":%" PRIu64 ","
               "\"sent_datagrams\":%" PRIu64 ",\"received_datagrams\":%" PRIu64 ",\"payload_errors\":%" PRIuFAST64 "}",
               i ? ",":"", stats[i].state, stats[i].gathered_host,
               stats[i].selected_local_class, stats[i].selected_remote_class, stats[i].socket_binding_audits,
               stats[i].requested_ifindex, stats[i].actual_ifindex, stats[i].sent_datagrams,
               stats[i].received_datagrams, atomic_load(&received[i].errors));
    }
    printf("]}\n");
    return passed ? 0:1;
}

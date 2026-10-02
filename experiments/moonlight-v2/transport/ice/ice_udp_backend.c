#include "ice_udp_backend.h"
#include <juice/juice.h>
#include <arpa/inet.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <ifaddrs.h>
#include <net/if.h>
#endif

struct hg_ice {
    juice_agent_t *agent;
    hg_ice_receive receive;
    void *user;
    atomic_uint_fast64_t state, gathering_done, classes[5];
    atomic_uint_fast64_t sent_datagrams, sent_bytes, received_datagrams, received_bytes, send_errors;
    atomic_uint_fast64_t audits, requested_ifindex, actual_ifindex;
};

static unsigned candidate_class(const char *sdp) {
    if (strstr(sdp, " typ host")) return 1;
    if (strstr(sdp, " typ srflx")) return 2;
    if (strstr(sdp, " typ prflx")) return 3;
    if (strstr(sdp, " typ relay")) return 4;
    return 0;
}
static void changed(juice_agent_t *agent, juice_state_t state, void *user) {
    (void)agent;
    atomic_store(&((hg_ice *)user)->state, state);
}
static void candidate(juice_agent_t *agent, const char *sdp, void *user) {
    (void)agent;
    atomic_fetch_add(&((hg_ice *)user)->classes[candidate_class(sdp)], 1);
}
static void gathered(juice_agent_t *agent, void *user) {
    (void)agent;
    atomic_store(&((hg_ice *)user)->gathering_done, 1);
}
static void receive(juice_agent_t *agent, const char *data, size_t size, void *user) {
    (void)agent;
    hg_ice *endpoint = user;
    if (size > 1200) return;
    atomic_fetch_add(&endpoint->received_datagrams, 1);
    atomic_fetch_add(&endpoint->received_bytes, size);
    if (endpoint->receive) endpoint->receive((const uint8_t *)data, size, endpoint->user);
}
static void socket_bound(uint32_t requested, uint32_t actual, int family, void *user) {
    hg_ice *endpoint = user;
    if (family != AF_INET || requested == 0 || requested != actual) return;
    atomic_store(&endpoint->requested_ifindex, requested);
    atomic_store(&endpoint->actual_ifindex, actual);
    atomic_fetch_add(&endpoint->audits, 1);
}
static int numeric_ipv4(const char *address) {
    struct in_addr value;
    return !address || (strlen(address) <= 15 && inet_pton(AF_INET, address, &value) == 1);
}

#ifdef __APPLE__
static int physical_ipv4(const char *name, char result[INET_ADDRSTRLEN]) {
    if (!name || (strcmp(name, "en7") && strcmp(name, "en0")) || !if_nametoindex(name)) return -1;
    struct ifaddrs *addresses;
    if (getifaddrs(&addresses)) return -1;
    int found = 0;
    for (struct ifaddrs *address = addresses; address; address = address->ifa_next) {
        if (!address->ifa_addr || address->ifa_addr->sa_family != AF_INET ||
            strcmp(address->ifa_name, name) || !(address->ifa_flags & IFF_UP) ||
            !(address->ifa_flags & IFF_RUNNING) || (address->ifa_flags & IFF_LOOPBACK)) continue;
        const struct sockaddr_in *value = (const struct sockaddr_in *)address->ifa_addr;
        if (inet_ntop(AF_INET, &value->sin_addr, result, INET_ADDRSTRLEN)) { found = 1; break; }
    }
    freeifaddrs(addresses);
    return found ? 0 : -1;
}
#endif

hg_ice *hg_ice_create(const hg_ice_config *config) {
    /* NONE is mandatory: upstream WARN logs can contain ufrag, not just DEBUG. */
    juice_set_log_level(JUICE_LOG_LEVEL_NONE);
    if (!config || !numeric_ipv4(config->stun_udp_ipv4) || !numeric_ipv4(config->turn_udp_ipv4) ||
        (config->stun_udp_ipv4 && !config->stun_udp_port) ||
        (config->turn_udp_ipv4 && (!config->turn_udp_port || !config->turn_username || !config->turn_password))) return NULL;
    char ipv4[INET_ADDRSTRLEN] = "0.0.0.0";
#ifdef __APPLE__
    if (physical_ipv4(config->bind_interface, ipv4)) return NULL;
#else
    if (config->bind_interface) return NULL;
#endif
    hg_ice *endpoint = calloc(1, sizeof(*endpoint));
    if (!endpoint) return NULL;
    atomic_init(&endpoint->state, 0); atomic_init(&endpoint->gathering_done, 0);
    for (unsigned i = 0; i < 5; ++i) atomic_init(&endpoint->classes[i], 0);
    atomic_init(&endpoint->sent_datagrams, 0); atomic_init(&endpoint->sent_bytes, 0);
    atomic_init(&endpoint->received_datagrams, 0); atomic_init(&endpoint->received_bytes, 0);
    atomic_init(&endpoint->send_errors, 0); atomic_init(&endpoint->audits, 0);
    atomic_init(&endpoint->requested_ifindex, 0); atomic_init(&endpoint->actual_ifindex, 0);
    endpoint->receive = config->receive; endpoint->user = config->user;
    juice_config_t juice = {0};
    juice.concurrency_mode = JUICE_CONCURRENCY_MODE_THREAD; /* independent socket, no MUX reuse */
    juice.bind_address = ipv4;
    juice.bind_interface = config->bind_interface;
    juice.cb_socket_bound = socket_bound;
    juice.stun_server_host = config->stun_udp_ipv4;
    juice.stun_server_port = config->stun_udp_port;
    juice_turn_server_t turn = {0};
    if (config->turn_udp_ipv4) {
        turn.host = config->turn_udp_ipv4; turn.port = config->turn_udp_port;
        turn.username = config->turn_username; turn.password = config->turn_password;
        juice.turn_servers = &turn; juice.turn_servers_count = 1;
    }
    juice.cb_state_changed = changed; juice.cb_candidate = candidate;
    juice.cb_gathering_done = gathered; juice.cb_recv = receive; juice.user_ptr = endpoint;
    endpoint->agent = juice_create(&juice);
    if (!endpoint->agent || juice_set_ice_tcp_mode(endpoint->agent, JUICE_ICE_TCP_MODE_NONE)) {
        if (endpoint->agent) juice_destroy(endpoint->agent);
        free(endpoint); return NULL;
    }
    return endpoint;
}
int hg_ice_gather(hg_ice *endpoint) { return endpoint ? juice_gather_candidates(endpoint->agent) : -1; }
int hg_ice_local_description_private(hg_ice *endpoint, char *memory, size_t capacity) {
    if (!endpoint || !memory || capacity > JUICE_MAX_SDP_STRING_LEN) return -1;
    return juice_get_local_description(endpoint->agent, memory, capacity);
}
int hg_ice_remote_description_private(hg_ice *endpoint, const char *memory) {
    if (!endpoint || !memory || strnlen(memory, JUICE_MAX_SDP_STRING_LEN) == JUICE_MAX_SDP_STRING_LEN) return -1;
    /* No candidate parsing of passwords: inspect only SDP candidate lines. */
    for (const char *line = memory; line && *line; ) {
        const char *candidate_line = line;
        if (!strncmp(line, "a=candidate:", 12)) candidate_line += 2;
        if (!strncmp(candidate_line, "candidate:", 10)) {
            char foundation[65], transport[9]; unsigned component;
            if (sscanf(candidate_line, "candidate:%64s %u %8s", foundation, &component, transport) != 3 ||
                component != 1 || (strcmp(transport, "UDP") && strcmp(transport, "udp"))) return -1;
        }
        const char *newline = strchr(line, '\n');
        line = newline ? newline + 1 : NULL;
    }
    return juice_set_remote_description(endpoint->agent, memory);
}
int hg_ice_send(hg_ice *endpoint, const uint8_t *datagram, size_t size) {
    if (!endpoint || !datagram || size == 0 || size > 1200) return -1;
    int status = juice_send(endpoint->agent, (const char *)datagram, size);
    if (!status) {
        atomic_fetch_add(&endpoint->sent_datagrams, 1); atomic_fetch_add(&endpoint->sent_bytes, size);
    } else atomic_fetch_add(&endpoint->send_errors, 1);
    return status;
}
int hg_ice_get_stats(hg_ice *endpoint, hg_ice_stats *stats) {
    if (!endpoint || !stats) return -1;
    memset(stats, 0, sizeof(*stats));
    stats->state = atomic_load(&endpoint->state); stats->gathering_done = atomic_load(&endpoint->gathering_done);
    stats->gathered_host = atomic_load(&endpoint->classes[1]); stats->gathered_srflx = atomic_load(&endpoint->classes[2]);
    stats->gathered_prflx = atomic_load(&endpoint->classes[3]); stats->gathered_relay = atomic_load(&endpoint->classes[4]);
    stats->sent_datagrams = atomic_load(&endpoint->sent_datagrams); stats->sent_bytes = atomic_load(&endpoint->sent_bytes);
    stats->received_datagrams = atomic_load(&endpoint->received_datagrams); stats->received_bytes = atomic_load(&endpoint->received_bytes);
    stats->send_errors = atomic_load(&endpoint->send_errors); stats->socket_binding_audits = atomic_load(&endpoint->audits);
    stats->requested_ifindex = atomic_load(&endpoint->requested_ifindex); stats->actual_ifindex = atomic_load(&endpoint->actual_ifindex);
    stats->tcp_disabled = 1;
    char local[JUICE_MAX_CANDIDATE_SDP_STRING_LEN] = {0}, remote[JUICE_MAX_CANDIDATE_SDP_STRING_LEN] = {0};
    if (!juice_get_selected_candidates(endpoint->agent, local, sizeof(local), remote, sizeof(remote))) {
        stats->selected_local_class = candidate_class(local); stats->selected_remote_class = candidate_class(remote);
    }
    memset(local, 0, sizeof(local)); memset(remote, 0, sizeof(remote));
    return 0;
}
void hg_ice_destroy(hg_ice *endpoint) {
    if (!endpoint) return;
    juice_destroy(endpoint->agent); memset(endpoint, 0, sizeof(*endpoint)); free(endpoint);
}

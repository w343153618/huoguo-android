#ifndef HUOGUO_ICE_UDP_BACKEND_H
#define HUOGUO_ICE_UDP_BACKEND_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct hg_ice hg_ice;
#define HG_ICE_EXPORT __attribute__((visibility("default")))
typedef void (*hg_ice_receive)(const uint8_t *datagram, size_t size, void *user);
typedef struct {
    /* Mac: mandatory en7/en0; current IPv4 resolved internally, never default route.
       Android: must be NULL; caller must implement Network/VpnService socket routing
       before claiming Android VPN bypass. This build-only port makes no such claim. */
    const char *bind_interface;
    /* Numeric IPv4 only. No resolver/default-proxy dependency for STUN/TURN.
       NULL means host candidates only. TURN always uses a UDP host+port; no URL,
       TLS or TCP fallback is accepted. Credentials remain in caller memory. */
    const char *stun_udp_ipv4;
    uint16_t stun_udp_port;
    const char *turn_udp_ipv4;
    uint16_t turn_udp_port;
    const char *turn_username;
    const char *turn_password;
    hg_ice_receive receive;
    void *user;
} hg_ice_config;

typedef struct {
    uint64_t state;
    uint64_t gathering_done;
    uint64_t gathered_host, gathered_srflx, gathered_prflx, gathered_relay;
    uint64_t sent_datagrams, sent_bytes, received_datagrams, received_bytes;
    uint64_t send_errors;
    uint64_t socket_binding_audits, requested_ifindex, actual_ifindex;
    uint64_t selected_local_class, selected_remote_class; /* 0 unknown,1 host,2 srflx,3 prflx,4 relay */
    uint64_t tcp_disabled;
} hg_ice_stats;

/* No logging. Only callbacks consume application datagrams. State/metrics contain
   no candidates, local/remote addresses, SDP, ICE passwords or application bytes. */
HG_ICE_EXPORT hg_ice *hg_ice_create(const hg_ice_config *config);
HG_ICE_EXPORT int hg_ice_gather(hg_ice *endpoint);
HG_ICE_EXPORT int hg_ice_local_description_private(hg_ice *endpoint, char *memory, size_t capacity);
HG_ICE_EXPORT int hg_ice_remote_description_private(hg_ice *endpoint, const char *memory);
HG_ICE_EXPORT int hg_ice_send(hg_ice *endpoint, const uint8_t *encrypted_datagram, size_t size);
HG_ICE_EXPORT int hg_ice_get_stats(hg_ice *endpoint, hg_ice_stats *stats);
HG_ICE_EXPORT void hg_ice_destroy(hg_ice *endpoint);

#ifdef __cplusplus
}
#endif
#endif

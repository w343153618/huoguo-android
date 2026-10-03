"""Pure fixed endpoint contract for the next owner-only NPS UDP candidate.

This module opens no sockets and grants no authenticated session. Existing
account/TLS verification, AES-GCM, directional nonces, replay checks, 120-second
session lifecycle, cancellation and guest reservation remain separate layers.
Nothing here verifies a live NPC, a domestic route or host/LAN isolation.

The public endpoint is an advertised address, never a local socket bind. A
loopback peer identifies proxy placement only, not the original phone/user.
M5 defaults to planning data. Only a trusted server-side owner-trial opt-in may
admit the user's bounded M5 experiment; it never admits friend deployment or
claims that host/LAN isolation was accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import ipaddress
from types import MappingProxyType
from typing import Literal


NPS_OWNER_SCOPE = 'nps_owner'
PUBLIC_HOST = '146.56.249.175'
LOOPBACK_HOST = '127.0.0.1'
LOOPBACK_INTERFACE = 'lo0'
SESSION_MAX_SECONDS = 120


class ProfileError(ValueError):
    """Payload-free boundary error, safe to classify without printing inputs."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int
    transport: Literal['tcp', 'udp']

    def __post_init__(self):
        if type(self.host) is not str:
            raise ProfileError('literal_ipv4_required')
        try:
            address = ipaddress.IPv4Address(self.host)
        except ipaddress.AddressValueError:
            raise ProfileError('literal_ipv4_required') from None
        if str(address) != self.host:
            raise ProfileError('canonical_ipv4_required')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ProfileError('integer_port_required')
        if type(self.transport) is not str or self.transport not in ('tcp', 'udp'):
            raise ProfileError('explicit_transport_required')


@dataclass(frozen=True, slots=True)
class NpsProfile:
    node: Literal['m1', 'm5']
    scope: str
    public_control: Endpoint
    public_media: Endpoint
    local_https: Endpoint
    local_udp: Endpoint
    interface: str
    proxy_protocol: int
    deployment: Literal['owner_nonisolated', 'friend_deployment_gated']
    guest_serial: str
    guest_avd: str
    max_session_seconds: int = SESSION_MAX_SECONDS

    def __post_init__(self):
        if type(self.node) is not str or self.node not in ('m1', 'm5'):
            raise ProfileError('unknown_nps_node')
        if type(self.scope) is not str or self.scope != NPS_OWNER_SCOPE:
            raise ProfileError('explicit_nps_owner_scope_required')
        if any(type(endpoint) is not Endpoint for endpoint in (
                self.public_control, self.public_media, self.local_https, self.local_udp)):
            raise ProfileError('typed_profile_endpoints_required')
        if type(self.interface) is not str or self.interface != LOOPBACK_INTERFACE:
            raise ProfileError('exact_loopback_interface_required')
        if type(self.proxy_protocol) is not int or self.proxy_protocol != 0:
            raise ProfileError('proxy_protocol_zero_required')
        if (type(self.deployment) is not str or self.deployment not in
                ('owner_nonisolated', 'friend_deployment_gated')):
            raise ProfileError('explicit_deployment_state_required')
        if type(self.max_session_seconds) is not int or self.max_session_seconds != SESSION_MAX_SECONDS:
            raise ProfileError('fixed_session_bound_required')
        expected_guest = {'m1': ('emulator-5556', 'RemoteAndroid17Compare'),
                          'm5': ('emulator-5554', 'phone17-root')}[self.node]
        if (type(self.guest_serial) is not str or type(self.guest_avd) is not str
                or (self.guest_serial, self.guest_avd) != expected_guest):
            raise ProfileError('fixed_node_guest_required')


M1_OWNER = NpsProfile(
    node='m1', scope=NPS_OWNER_SCOPE,
    public_control=Endpoint(PUBLIC_HOST, 49556, 'tcp'),
    public_media=Endpoint(PUBLIC_HOST, 15556, 'udp'),
    local_https=Endpoint(LOOPBACK_HOST, 45561, 'tcp'),
    local_udp=Endpoint(LOOPBACK_HOST, 45965, 'udp'),
    interface=LOOPBACK_INTERFACE, proxy_protocol=0,
    deployment='owner_nonisolated',
    guest_serial='emulator-5556', guest_avd='RemoteAndroid17Compare',
)
M5_PLANNED = NpsProfile(
    node='m5', scope=NPS_OWNER_SCOPE,
    public_control=Endpoint(PUBLIC_HOST, 49558, 'tcp'),
    public_media=Endpoint(PUBLIC_HOST, 15558, 'udp'),
    local_https=Endpoint(LOOPBACK_HOST, 45561, 'tcp'),
    local_udp=Endpoint(LOOPBACK_HOST, 45965, 'udp'),
    interface=LOOPBACK_INTERFACE, proxy_protocol=0,
    deployment='friend_deployment_gated',
    guest_serial='emulator-5554', guest_avd='phone17-root',
)
_M5_OWNER_TRIAL = replace(M5_PLANNED, deployment='owner_nonisolated')
_PROFILES = MappingProxyType({'m1': M1_OWNER, 'm5': M5_PLANNED})


def planned_profile(node: str) -> NpsProfile:
    """Return immutable planning data; this does not admit or enable a service."""
    if type(node) is not str or node not in _PROFILES:
        raise ProfileError('unknown_nps_node')
    return _PROFILES[node]


def _validate_owner_trial_opt_in(allow_m5_owner_trial: bool) -> None:
    if type(allow_m5_owner_trial) is not bool:
        raise ProfileError('trusted_owner_trial_boolean_required')


def owner_profile(node: str, *, allow_m5_owner_trial: bool = False) -> NpsProfile:
    """M5 needs a trusted server flag, never a value read from an App body."""
    _validate_owner_trial_opt_in(allow_m5_owner_trial)
    profile = planned_profile(node)
    if profile.deployment != 'owner_nonisolated':
        if allow_m5_owner_trial:
            return _M5_OWNER_TRIAL
        raise ProfileError('friend_deployment_isolation_gate_required')
    return profile


def _require_owner_profile(profile: NpsProfile, *,
                           allow_m5_owner_trial: bool = False) -> NpsProfile:
    # Do not let a caller-constructed altered dataclass, changed bind/target or forged
    # M5 deployment flag become a routing configuration.
    if type(profile) is not NpsProfile:
        raise ProfileError('fixed_nps_profile_required')
    expected = owner_profile(profile.node, allow_m5_owner_trial=allow_m5_owner_trial)
    if profile != expected:
        raise ProfileError('fixed_nps_profile_mismatch')
    return expected


def validate_scope(scope: str) -> None:
    if type(scope) is not str or scope != NPS_OWNER_SCOPE:
        raise ProfileError('explicit_nps_owner_scope_required')


def validate_proxy_protocol(proxy_protocol: int) -> None:
    # bool/float/string coercions are forbidden; a proxy header would corrupt
    # the first authenticated HGUE datagram.
    if type(proxy_protocol) is not int or proxy_protocol != 0:
        raise ProfileError('proxy_protocol_zero_required')


def validate_owner_request(identity: dict, *,
                           allow_m5_owner_trial: bool = False) -> NpsProfile:
    """Validate a closed identity object, not a full stream settings object.

    A future HTTP adapter must separately validate its complete settings body
    using a closed field contract. It must not silently strip bind/target fields
    before calling this function. Client routing parameters are never accepted.
    """
    if type(identity) is not dict or set(identity) != {'node', 'network_scope'}:
        raise ProfileError('closed_nps_identity_required')
    validate_scope(identity['network_scope'])
    return owner_profile(identity['node'], allow_m5_owner_trial=allow_m5_owner_trial)


def validate_public_endpoints(profile: NpsProfile, control: Endpoint,
                              media: Endpoint, *, network_scope: str,
                              proxy_protocol: int,
                              allow_m5_owner_trial: bool = False) -> None:
    """Validate selected public tuples; neither endpoint is a Mac bind target."""
    expected = _require_owner_profile(profile, allow_m5_owner_trial=allow_m5_owner_trial)
    validate_scope(network_scope)
    validate_proxy_protocol(proxy_protocol)
    if (type(control) is not Endpoint or type(media) is not Endpoint
            or control != expected.public_control or media != expected.public_media):
        raise ProfileError('public_endpoint_profile_mismatch')


def validate_loopback_backend(profile: NpsProfile, https: Endpoint,
                              udp: Endpoint, interface: str, *,
                              proxy_protocol: int,
                              allow_m5_owner_trial: bool = False) -> None:
    """Validate server-selected local bindings, never values from an App body."""
    expected = _require_owner_profile(profile, allow_m5_owner_trial=allow_m5_owner_trial)
    validate_proxy_protocol(proxy_protocol)
    if (type(https) is not Endpoint or type(udp) is not Endpoint
            or https != expected.local_https or udp != expected.local_udp
            or type(interface) is not str or interface != expected.interface):
        raise ProfileError('loopback_backend_profile_mismatch')


def validate_proxy_peer(profile: NpsProfile, peer: tuple[str, int], *,
                        allow_m5_owner_trial: bool = False) -> None:
    """Require exact loopback plus a typed source port, not a phone identity.

    This alone does not authorize READY or pin a UDP peer. The session worker
    must authenticate/replay-check the first READY, then pin that full tuple;
    HTTPS and UDP proxy source ports are not required to be equal.
    """
    _require_owner_profile(profile, allow_m5_owner_trial=allow_m5_owner_trial)
    if (type(peer) is not tuple or len(peer) != 2
            or type(peer[0]) is not str or peer[0] != LOOPBACK_HOST
            or type(peer[1]) is not int or not 1 <= peer[1] <= 65535):
        raise ProfileError('exact_loopback_proxy_peer_required')


def validate_session_seconds(seconds: int) -> None:
    """Only a range check; READY/ALIVE/deadlines remain session-layer duties."""
    if type(seconds) is not int or not 1 <= seconds <= SESSION_MAX_SECONDS:
        raise ProfileError('bounded_session_seconds_required')

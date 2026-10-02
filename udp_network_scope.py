"""Explicit, fail-closed network admission for the isolated UDP candidate.

LAN remains RFC1918/same-/24. Tailnet is a separate exact M1/registered-phone
allowlist, not a CGNAT subnet exception. Read-only LocalAPI calls stay outside
the UDP/touch hot path. A failed Tailnet verification is sticky until this
candidate process is restarted; it never changes a Tailscale profile or route.

LocalAPI field contracts were checked against Tailscale v1.96.4:
client/local/local.go, client/tailscale/apitype/apitype.go, ipn/ipnstate and
tailcfg.Node. Neither LocalAPI response bodies nor preference data are logged.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import subprocess
import threading


RFC1918 = tuple(ipaddress.IPv4Network(prefix) for prefix in
                ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
TAILNET_HOST = '100.65.0.2'
TAILNET_PHONE = '100.65.0.3'
TAILNET_SELF_ID = '46'
TAILNET_PHONE_ID = '48'
TAILNET_USER_ID = 3
TAILNET_LOGIN = 'wyw'
TAILNET_CONTROL = 'https://hs.yilufa.site'
LOCALAPI_SOCKET = '/var/run/tailscaled.socket'


def rfc1918_address(host):
    try:
        address = ipaddress.IPv4Address(host)
    except (ValueError, TypeError):
        return False
    return any(address in subnet for subnet in RFC1918)


def same_private_lan(peer, host):
    try:
        address = ipaddress.IPv4Address(peer)
        subnet = ipaddress.IPv4Network(host + '/24', strict=False)
        return (rfc1918_address(host) and address in subnet
                and address not in (subnet.network_address, subnet.broadcast_address))
    except (ValueError, TypeError):
        return False


class ScopeUnavailable(Exception):
    """Only a fixed diagnostic code, never LocalAPI bodies or commands."""


class _UnixHttpConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('local-tailscaled.sock', timeout=2)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(self.path)


class LocalTailscaleApi:
    """Bounded GET-only client for the selected Homebrew daemon's Unix socket.

    No TCP fallback, HTTP proxy, GUI-client discovery, token read or mutating
    endpoint. Unsupported API schemas/permissions fail closed.
    """
    MAX_RESPONSE_BYTES = 262144
    PATHS = frozenset(('/localapi/v0/prefs', '/localapi/v0/status',
                       '/localapi/v0/whois?addr=' + TAILNET_PHONE))

    def __init__(self, path=LOCALAPI_SOCKET):
        self.path = path

    def get(self, path):
        if path not in self.PATHS:
            raise ScopeUnavailable('tailnet_localapi_route_not_allowed')
        connection = _UnixHttpConnection(self.path)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            if response.status != 200:
                raise ScopeUnavailable('tailnet_localapi_read_failed')
            body = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise ScopeUnavailable('tailnet_localapi_response_bound')
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ScopeUnavailable('tailnet_localapi_schema_invalid')
            return value
        except (OSError, http.client.HTTPException, ValueError):
            raise ScopeUnavailable('tailnet_localapi_unavailable') from None
        finally:
            connection.close()


class LanScope:
    name = 'lan'
    ping_scope = 'isolated_same_subnet_LAN_not_WAN'

    def __init__(self, host):
        if not rfc1918_address(host):
            raise ValueError('physical_private_LAN_IPv4_required')
        self.host = host

    def permits_peer(self, peer):
        return same_private_lan(peer, self.host)

    def verify(self):
        # Physical address matching is done at gateway startup, as before.
        return True

    def healthy(self):
        return True


class TailnetScope:
    name = 'tailnet'
    ping_scope = 'isolated_registered_Tailnet_UDP_not_public_UDP'
    CHECK_SECONDS = 1.0

    def __init__(self, host, interface, localapi=None, runner=None):
        if host != TAILNET_HOST:
            raise ValueError('exact_M1_Tailnet_address_required')
        if not re.fullmatch(r'utun[0-9]{1,3}', interface or ''):
            raise ValueError('kernel_Tailnet_utun_required')
        self.host, self.interface = host, interface
        self.localapi = localapi or LocalTailscaleApi()
        self.run = runner or subprocess.run
        self._healthy = False
        self._failed = False
        self.last_failure = ''
        self._verify_lock = threading.Lock()
        self.verify()

    def permits_peer(self, peer):
        return peer == TAILNET_PHONE

    def healthy(self):
        return self._healthy

    @staticmethod
    def _require(condition, code):
        if not condition:
            raise ScopeUnavailable(code)

    def verify(self):
        # HTTP admissions and the service reaper can verify concurrently. A
        # late successful read must never undo another verifier's revocation.
        with self._verify_lock:
            return self._verify_locked()

    def _verify_locked(self):
        if self._failed:
            raise ScopeUnavailable(self.last_failure)
        try:
            prefs = self.localapi.get('/localapi/v0/prefs')
            self._require(prefs.get('ControlURL') == TAILNET_CONTROL
                          and prefs.get('WantRunning') is True,
                          'tailnet_control_profile_mismatch')
            self._require(not prefs.get('ExitNodeID') and not prefs.get('ExitNodeIP'),
                          'tailnet_unexpected_exit_node')
            status = self.localapi.get('/localapi/v0/status')
            self._require(status.get('BackendState') == 'Running'
                          and status.get('TUN') is True and not status.get('Health'),
                          'tailnet_daemon_not_healthy_kernel_TUN')
            current = status.get('Self') or {}
            self._require(isinstance(current, dict)
                          and current.get('ID') == TAILNET_SELF_ID
                          and current.get('UserID') == TAILNET_USER_ID
                          and current.get('Online') is True
                          and TAILNET_HOST in current.get('TailscaleIPs', [])
                          and TAILNET_HOST in status.get('TailscaleIPs', []),
                          'tailnet_self_identity_mismatch')
            peers = status.get('Peer')
            self._require(isinstance(peers, dict), 'tailnet_peer_schema_invalid')
            matches = [peer for peer in peers.values() if isinstance(peer, dict)
                       and TAILNET_PHONE in peer.get('TailscaleIPs', [])]
            self._require(len(matches) == 1, 'tailnet_registered_peer_missing')
            peer = matches[0]
            self._require(peer.get('ID') == TAILNET_PHONE_ID
                          and peer.get('UserID') == TAILNET_USER_ID
                          and peer.get('Online') is True
                          and peer.get('InNetworkMap') is True
                          and not peer.get('Expired'), 'tailnet_peer_identity_mismatch')
            whois = self.localapi.get('/localapi/v0/whois?addr=' + TAILNET_PHONE)
            node, owner = whois.get('Node') or {}, whois.get('UserProfile') or {}
            self._require(isinstance(node, dict) and isinstance(owner, dict)
                          and node.get('ID') == 48
                          and node.get('StableID') == TAILNET_PHONE_ID
                          and node.get('User') == TAILNET_USER_ID
                          and TAILNET_PHONE + '/32' in node.get('Addresses', [])
                          and owner.get('ID') == TAILNET_USER_ID
                          and owner.get('LoginName') == TAILNET_LOGIN,
                          'tailnet_whois_identity_mismatch')
            # Inner plaintext UDP must enter the selected kernel TUN. Binding
            # 100.65.* to en7/en0 is wrong; tailscaled owns the outer route.
            interface = self.run(['/sbin/ifconfig', self.interface], check=True,
                                 capture_output=True, text=True, timeout=2).stdout
            first = interface.splitlines()[0] if interface else ''
            mtu = re.search(r'\bmtu ([0-9]+)\b', first)
            self._require(first.startswith(self.interface + ':')
                          and re.search(r'\bUP\b', first) is not None
                          and mtu is not None and int(mtu.group(1)) >= 1280
                          and re.search(r'^\s*inet ' + re.escape(TAILNET_HOST)
                                        + r'(?:\s|$)', interface, re.MULTILINE) is not None,
                          'tailnet_kernel_interface_mismatch')
            route = self.run(['/sbin/route', '-n', 'get', TAILNET_PHONE], check=True,
                             capture_output=True, text=True, timeout=2).stdout
            self._require(re.search(r'^\s*interface:\s*' + re.escape(self.interface)
                                    + r'\s*$', route, re.MULTILINE) is not None,
                          'tailnet_kernel_route_mismatch')
            self._healthy = True
            return True
        except ScopeUnavailable as error:
            self._healthy, self._failed, self.last_failure = False, True, str(error)
            raise
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            self._healthy, self._failed = False, True
            self.last_failure = 'tailnet_scope_verification_unavailable'
            raise ScopeUnavailable(self.last_failure) from None

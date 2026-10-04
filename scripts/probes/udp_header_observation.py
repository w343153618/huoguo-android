"""Pure, bounded classic-PCAP observation of one selected IPv4 UDP endpoint.

No capture, file access, decryption or sockets. Accepts Ethernet or raw IPv4
classic pcap only; pcapng and unsupported link types fail explicitly. The
HGUE prefix is unauthenticated metadata. Sequence holes are not packet loss.
"""
from __future__ import annotations

import ipaddress
import struct

MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_GROUPS = 8
BIN_NS = 100_000_000
MAX_BINS = 1024
MAGICS = {
    b'\xd4\xc3\xb2\xa1': ('<', 1_000_000, 1000),
    b'\xa1\xb2\xc3\xd4': ('>', 1_000_000, 1000),
    b'\x4d\x3c\xb2\xa1': ('<', 1_000_000_000, 1),
    b'\xa1\xb2\x3c\x4d': ('>', 1_000_000_000, 1),
}


class ObservationError(ValueError):
    """Closed error codes only; never include packet bytes or addresses."""


def observe(data: bytes, *, server_ip: str, server_port: int,
            phone_ip: str) -> dict:
    """Return only numeric arrival summaries for the exact selected peer.

    Snapshot truncation after the24-byte envelope is expected. Frame bodies,
    addresses, session tags and raw sequence numbers are never returned.
    Capture drop counters/lifecycle/route checks must be supplied separately.
    """
    if type(data) is not bytes or not 24 <= len(data) <= MAX_BYTES:
        raise ObservationError('pcap_byte_bound')
    if type(server_ip) is not str or type(phone_ip) is not str:
        raise ObservationError('explicit_IPv4_peers_required')
    try:
        server = ipaddress.IPv4Address(server_ip).packed
        phone = ipaddress.IPv4Address(phone_ip).packed
    except (ipaddress.AddressValueError, TypeError):
        raise ObservationError('explicit_IPv4_peers_required') from None
    if server == phone or type(server_port) is not int or not 1024 <= server_port <= 65535:
        raise ObservationError('explicit_distinct_peers_and_high_port_required')
    if data[:4] not in MAGICS:
        raise ObservationError('classic_pcap_required')
    endian, precision, multiplier = MAGICS[data[:4]]
    major, minor, zone, accuracy, snaplen, linktype = struct.unpack_from(endian+'HHiiII', data, 4)
    if (major, minor) != (2, 4) or zone or accuracy:
        raise ObservationError('pcap_header_contract')
    if not 64 <= snaplen <= 160 or linktype not in (1, 101):
        raise ObservationError('header_snapshot_or_linktype_unsupported')
    counts = dict(records=0, foreign_or_non_UDP=0, header_incomplete=0,
                  fragmented=0, invalid_IPv4_UDP=0, invalid_envelope=0,
                  matching_datagrams=0, capture_timestamp_regressions=0,
                  bins_outside_retention=0)
    groups = {}
    offset, previous_ts = 24, None
    while offset < len(data):
        if len(data)-offset < 16:
            raise ObservationError('partial_pcap_record_header')
        sec, fraction, captured, original = struct.unpack_from(endian+'IIII', data, offset)
        offset += 16
        if fraction >= precision or not 0 < captured <= snaplen or captured > original:
            raise ObservationError('pcap_record_contract')
        if captured > len(data)-offset:
            raise ObservationError('partial_pcap_packet')
        counts['records'] += 1
        if counts['records'] > MAX_RECORDS:
            raise ObservationError('pcap_record_bound')
        packet = memoryview(data)[offset:offset+captured]
        offset += captured
        ts = sec*1_000_000_000 + fraction*multiplier
        if previous_ts is not None and ts < previous_ts:
            counts['capture_timestamp_regressions'] += 1
        previous_ts = ts
        ipoff = 0
        if linktype == 1:
            if captured < 14:
                counts['header_incomplete'] += 1
                continue
            ether = struct.unpack_from('>H', packet, 12)[0]
            ipoff = 14
            # Single tagged Ethernet is supported explicitly, no guessing.
            if ether in (0x8100, 0x88a8):
                if captured < 18:
                    counts['header_incomplete'] += 1
                    continue
                ether, ipoff = struct.unpack_from('>H', packet, 16)[0], 18
            if ether != 0x0800:
                counts['foreign_or_non_UDP'] += 1
                continue
        if captured-ipoff < 20:
            counts['header_incomplete'] += 1
            continue
        if packet[ipoff] >> 4 != 4:
            counts['foreign_or_non_UDP'] += 1
            continue
        ihl = (packet[ipoff] & 15)*4
        total, fragment = struct.unpack_from('>HH', packet, ipoff+2)[0], struct.unpack_from('>H', packet, ipoff+6)[0]
        if ihl < 20 or total < ihl+8 or original < ipoff+total or fragment & 0x8000:
            counts['invalid_IPv4_UDP'] += 1
            continue
        src, dst = bytes(packet[ipoff+12:ipoff+16]), bytes(packet[ipoff+16:ipoff+20])
        if packet[ipoff+9] != 17 or (src, dst) not in ((server, phone), (phone, server)):
            counts['foreign_or_non_UDP'] += 1
            continue
        if fragment & 0x3fff:
            counts['fragmented'] += 1
            continue
        udpoff = ipoff+ihl
        if captured < udpoff+8:
            counts['header_incomplete'] += 1
            continue
        sport, dport, ulen = struct.unpack_from('>HHH', packet, udpoff)
        direction = 'ingress' if src == server else 'egress'
        if (sport if direction == 'ingress' else dport) != server_port:
            counts['foreign_or_non_UDP'] += 1
            continue
        if ulen != total-ihl or not 48 <= ulen <= 1408:
            counts['invalid_IPv4_UDP'] += 1
            continue
        payload = udpoff+8
        if captured < payload+24:
            counts['header_incomplete'] += 1
            continue
        magic, version, tag, seq = struct.unpack_from('>4sIQQ', packet, payload)
        if magic != b'HGUE' or version != 1:
            counts['invalid_envelope'] += 1
            continue
        key = direction, tag
        if key not in groups:
            if len(groups) >= MAX_GROUPS:
                raise ObservationError('envelope_group_bound')
            groups[key] = dict(direction=direction, first_ns=ts, last_ns=ts,
                               timestamps_regressed=0, datagrams=0, UDP_bytes=0,
                               duplicates=0, out_of_order=0, max_arrival_gap_ns=0,
                               sequences=set(), maximum_sequence=-1, bins={})
        g = groups[key]
        g['datagrams'] += 1
        g['UDP_bytes'] += ulen
        if seq in g['sequences']:
            g['duplicates'] += 1
        elif seq < g['maximum_sequence']:
            g['out_of_order'] += 1
        g['sequences'].add(seq)
        g['maximum_sequence'] = max(seq, g['maximum_sequence'])
        gap = ts-g['last_ns']
        if gap < 0:
            g['timestamps_regressed'] += 1
        else:
            g['max_arrival_gap_ns'] = max(g['max_arrival_gap_ns'], gap)
        g['last_ns'] = ts
        index = (ts-g['first_ns'])//BIN_NS
        if 0 <= index < MAX_BINS:
            g['bins'][index] = g['bins'].get(index, 0)+1
        else:
            counts['bins_outside_retention'] += 1
        counts['matching_datagrams'] += 1
    projected = []
    for ordinal, g in enumerate(groups.values(), 1):
        seqs = g['sequences']
        span = max(seqs)-min(seqs)+1
        projected.append(dict(group_ordinal=ordinal, direction=g['direction'],
            datagrams=g['datagrams'], UDP_bytes=g['UDP_bytes'],
            duration_ns=g['last_ns']-g['first_ns'],
            timestamp_regressions=g['timestamps_regressed'],
            max_arrival_gap_ns=g['max_arrival_gap_ns'], duplicates=g['duplicates'],
            out_of_order=g['out_of_order'], unique_sequence_count=len(seqs),
            observed_sequence_span=span, unobserved_sequences_inside_span=span-len(seqs),
            bins=[[i, n] for i, n in sorted(g['bins'].items())]))
    return dict(schema='selected-peer-UDP-header-observation-v1',
        clock='capture_timestamp_relative_per_envelope_group_not_phone_System_nanoTime',
        linktype=linktype, snapshot_bytes=snaplen, input_bytes=len(data),
        counts=counts, bin_ns=BIN_NS, groups=projected,
        packet_authentication_verified=False, sequence_holes_are_proven_loss=False,
        encrypted_media_lane_identified=False, capture_drop_counters_known=False,
        packet_route_or_domestic_exit_verified=False,
        capture_lifecycle_or_complete_window_verified=False)

import importlib.util
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('udp_header_observation',
    Path(__file__).resolve().parents[1]/'scripts/probes/udp_header_observation.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SERVER, PHONE, PORT = '146.56.249.175', '192.168.9.77', 15556


def packet(seq=1, tag=7, *, ingress=True, vlan=False, options=0, fragment=0,
           server=SERVER, port=PORT, envelope=b'HGUE', raw=False):
    src, dst = (server, PHONE) if ingress else (PHONE, server)
    payload = struct.pack('>4sIQQ', envelope, 1, tag, seq)+bytes(80)
    udp = struct.pack('>HHHH', port if ingress else 40000,
                      40000 if ingress else port, len(payload)+8, 0)+payload
    ip = struct.pack('>BBHHHBBH4s4s', 0x45+options//4, 0, 20+options+len(udp),
                     0, fragment, 64, 17, 0, socket.inet_aton(src), socket.inet_aton(dst))+bytes(options)+udp
    eth = bytes(12)+struct.pack('>H', 0x8100 if vlan else 0x0800)
    if vlan:
        eth += struct.pack('>HH', 1, 0x0800)
    return ip if raw else eth+ip


def capture(records=(), *, endian='<', nanos=False, linktype=1, snap=128):
    magic = (b'\x4d\x3c\xb2\xa1' if nanos else b'\xd4\xc3\xb2\xa1') if endian == '<' else (
        b'\xa1\xb2\x3c\x4d' if nanos else b'\xa1\xb2\xc3\xd4')
    data = magic+struct.pack(endian+'HHiiII', 2, 4, 0, 0, snap, linktype)
    for ts, full in records:
        part = full[:snap]
        data += struct.pack(endian+'IIII', 100+ts//(10**9 if nanos else 10**6),
                            ts%(10**9 if nanos else 10**6), len(part), len(full))+part
    return data


def observed(data):
    return m.observe(data, server_ip=SERVER, server_port=PORT, phone_ip=PHONE)


class HeaderObservationTests(unittest.TestCase):
    def test_snapshot_body_truncation_and_exact_relative_gap(self):
        d=observed(capture([(0, packet(1)), (5_000_000, packet(2))],snap=96))
        self.assertEqual(d['groups'][0]['max_arrival_gap_ns'],5_000_000_000)
        self.assertEqual(d['counts']['matching_datagrams'],2)
        self.assertFalse(d['packet_authentication_verified'])
        self.assertFalse(d['capture_lifecycle_or_complete_window_verified'])

    def test_all_endian_and_timestamp_formats(self):
        for endian in ('<','>'):
            for nano in (False,True):
                with self.subTest(endian=endian,nano=nano):
                    ts=123456789 if nano else 123456
                    d=observed(capture([(0,packet()),(ts,packet(2))],endian=endian,nanos=nano))
                    self.assertEqual(d['groups'][0]['duration_ns'],ts if nano else ts*1000)

    def test_holes_duplicates_reordering_do_not_claim_loss(self):
        d=observed(capture([(i*1000,packet(s)) for i,s in enumerate((2,5,3,5))]))
        g=d['groups'][0]
        self.assertEqual((g['duplicates'],g['out_of_order'],g['unobserved_sequences_inside_span']),(1,1,1))
        self.assertFalse(d['sequence_holes_are_proven_loss'])

    def test_direction_and_session_spaces_not_merged(self):
        d=observed(capture([(0,packet()),(1000,packet(ingress=False)),(2000,packet(tag=8))]))
        self.assertEqual(len(d['groups']),3)
        self.assertEqual([x['direction'] for x in d['groups']],['ingress','egress','ingress'])
        self.assertNotIn('session_tag',str(d))
        self.assertNotIn(SERVER,str(d))
        self.assertFalse(d['encrypted_media_lane_identified'])

    def test_foreign_endpoint_and_wrong_port_excluded(self):
        d=observed(capture([(0,packet(server='146.56.249.174')),(1000,packet(port=15558)),(2000,packet())]))
        self.assertEqual(d['counts']['matching_datagrams'],1)
        self.assertEqual(d['counts']['foreign_or_non_UDP'],2)

    def test_vlan_IPv4_options_and_raw_link(self):
        self.assertEqual(observed(capture([(0,packet(vlan=True,options=40))]))['counts']['matching_datagrams'],1)
        self.assertEqual(observed(capture([(0,packet(raw=True))],linktype=101))['counts']['matching_datagrams'],1)

    def test_partial_envelope_is_unknown_not_fake_arrival(self):
        d=observed(capture([(0,packet(options=40))],snap=64))
        self.assertEqual(d['counts']['header_incomplete'],1)
        self.assertEqual(d['groups'],[])

    def test_fragmented_packet_not_interpreted_as_envelope(self):
        d=observed(capture([(0,packet(fragment=0x2000)),(1000,packet(fragment=1))]))
        self.assertEqual(d['counts']['fragmented'],2)
        self.assertEqual(d['groups'],[])

    def test_invalid_envelope_and_inconsistent_UDP_length(self):
        bad=bytearray(packet());struct.pack_into('>H',bad,14+20+4,9)
        d=observed(capture([(0,packet(envelope=b'NOPE')),(1000,bytes(bad))]))
        self.assertEqual(d['counts']['invalid_envelope'],1)
        self.assertEqual(d['counts']['invalid_IPv4_UDP'],1)

    def test_regressing_capture_timestamp_explicit(self):
        d=observed(capture([(2000,packet(1)),(1000,packet(2))]))
        self.assertEqual(d['counts']['capture_timestamp_regressions'],1)
        self.assertEqual(d['groups'][0]['timestamp_regressions'],1)

    def test_partial_headers_and_packets_fail_closed(self):
        for data in (capture()+b'\x01',capture([(0,packet())])[:-1]):
            with self.assertRaises(m.ObservationError):observed(data)

    def test_record_and_group_bounds(self):
        with patch.object(m,'MAX_RECORDS',1):
            with self.assertRaisesRegex(m.ObservationError,'pcap_record_bound'):
                observed(capture([(0,packet()),(1000,packet(2))]))
        with self.assertRaisesRegex(m.ObservationError,'envelope_group_bound'):
            observed(capture([(i*1000,packet(tag=i)) for i in range(9)]))

    def test_invalid_fraction_lengths_and_file_contracts(self):
        data=bytearray(capture([(0,packet())]));struct.pack_into('<I',data,28,1_000_000)
        with self.assertRaises(m.ObservationError):observed(bytes(data))
        for kwargs in ({'linktype':113},{'snap':4096}):
            with self.assertRaises(m.ObservationError):observed(capture(**kwargs))
        with self.assertRaisesRegex(m.ObservationError,'classic_pcap_required'):
            observed(b'\x0a\x0d\x0d\x0a'+bytes(20))

    def test_observation_bin_retention_explicit(self):
        d=observed(capture([(0,packet()),(103_000_000,packet(2))]))
        self.assertEqual(d['counts']['bins_outside_retention'],1)
        self.assertEqual(d['groups'][0]['datagrams'],2)

    def test_bad_endpoint_and_port_inputs(self):
        for kw in ({'server_ip':'::1'},{'server_ip':7},{'server_port':80},{'server_port':True},{'phone_ip':SERVER}):
            args=dict(server_ip=SERVER,server_port=PORT,phone_ip=PHONE);args.update(kw)
            with self.assertRaises(m.ObservationError):m.observe(capture(),**args)

    def test_reserved_IP_flag_and_invalid_original_extent(self):
        d=observed(capture([(0,packet(fragment=0x8000))]))
        self.assertEqual(d['counts']['invalid_IPv4_UDP'],1)
        data=bytearray(capture([(0,packet())]));struct.pack_into('<I',data,36,30)
        with self.assertRaisesRegex(m.ObservationError,'pcap_record_contract'):observed(bytes(data))


if __name__ == '__main__':
    unittest.main()

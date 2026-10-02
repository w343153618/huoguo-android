#!/usr/bin/env python3
"""Offline, deliberately narrow SPS restriction A/B for a framed H.264 fixture.

Accept Baseline, progressive, POC type 0/2, no FMO/redundant pictures or B slices.
Preserve reference count, media bytes, every PTS/key flag and all non-SPS NALs.
Only a missing VUI bitstream restriction is added. Binary output stays in
/private/tmp. No VT, emulator, screen, phone, ADB, network or production service.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CONFIG_FLAG, KEY_FLAG, PTS_MASK = 1 << 62, 1 << 61, (1 << 61) - 1
sys.path.insert(0, str(ROOT))
from h264_low_latency import (Bits, Invalid, annex_nals, escape, pack_rbsp,
                              parse_pps, parse_sps, patch_sps, ue_bits, unescape)


def slice_prefix(nal, pps, sps):
    reader = Bits(unescape(nal[1:]))
    first_mb, kind, pps_id = reader.ue(262143), reader.ue(9), reader.ue(255)
    if first_mb != 0 or kind % 5 not in (0, 2) or pps_id not in pps:
        raise Invalid('slice is not an ordinary first P/I slice with known PPS')
    source = sps[pps[pps_id]]
    frame_num = reader.read(source['frame_num_bits'])
    idr = nal[0] & 31 == 5
    if idr:
        reader.ue(65535)
    poc_lsb = reader.read(source['poc_lsb_bits']) if source['pic_order_cnt_type'] == 0 else None
    return {'slice_type_mod5': kind % 5, 'frame_num': frame_num, 'idr': idr,
            'poc_lsb': poc_lsb, 'nal_ref_idc': (nal[0] >> 5) & 3, 'sps_id': source['sps_id']}


def framed_packets(data):
    if not 16 <= len(data) <= 128 * 1024 * 1024:
        raise Invalid('fixture length outside 16 bytes...128 MiB')
    codec, marker, width, height = struct.unpack('>4sIII', data[:16])
    if codec != b'h264' or marker != 0x80000000 or not 2 <= width <= 8192 or not 2 <= height <= 8192:
        raise Invalid('invalid fixture codec or geometry')
    packets, at = [], 16
    while at < len(data):
        if at + 12 > len(data):
            raise Invalid('truncated packet header')
        flagged, size = struct.unpack('>QI', data[at:at + 12])
        if flagged & (1 << 63) or not 1 <= size <= 8 * 1024 * 1024 or at + 12 + size > len(data):
            raise Invalid('resize or invalid packet length')
        packets.append((flagged, data[at + 12:at + 12 + size]))
        at += 12 + size
        if len(packets) > 650:
            raise Invalid('fixture packet bound exceeded')
    return (width, height), packets


def patch_framed(data):
    geometry, packets = framed_packets(data)
    output, sps, pps, changes = bytearray(data[:16]), {}, {}, []
    old_media, new_media = hashlib.sha256(), hashlib.sha256()
    hist, frame_count, previous_pts = Counter(), 0, None
    previous_ref_lsb, previous_ref_msb, previous_output_poc = 0, 0, None
    previous_frame_num, frame_offset = 0, 0
    for index, (flagged, payload) in enumerate(packets):
        rebuilt, vcl = bytearray(), []
        for marker, nal in annex_nals(payload):
            kind = nal[0] & 31
            replacement = nal
            if kind == 7:
                if not flagged & CONFIG_FLAG:
                    raise Invalid('in-band SPS in media is outside byte-identical media experiment')
                replacement, before, after = patch_sps(nal)
                if (before['width'], before['height']) != geometry:
                    raise Invalid('SPS and fixture geometry differ')
                source = parse_sps(nal)
                sps[source['sps_id']] = source
                changes.append({'packet': index, 'before': before, 'after': after,
                                'sps_before_sha256': hashlib.sha256(nal).hexdigest(),
                                'sps_after_sha256': hashlib.sha256(replacement).hexdigest()})
            elif kind == 8:
                pps_id, sps_id = parse_pps(nal)
                if sps_id not in sps:
                    raise Invalid('PPS references an unknown SPS')
                pps[pps_id] = sps_id
            elif kind in (1, 5):
                if flagged & CONFIG_FLAG:
                    raise Invalid('VCL slice in codec-config packet')
                vcl.append(slice_prefix(nal, pps, sps))
            elif kind in (2, 3, 4, 13, 14, 15, 19, 20, 21):
                raise Invalid('partitioned/extended H264 syntax is unsupported')
            rebuilt.extend(marker)
            rebuilt.extend(replacement)
        new_payload = bytes(rebuilt)
        header = struct.pack('>QI', flagged, len(new_payload))
        output.extend(header)
        output.extend(new_payload)
        if not flagged & CONFIG_FLAG:
            if payload != new_payload or len(vcl) != 1:
                raise Invalid('media must stay byte-identical and contain exactly one P/I slice')
            row = vcl[0]
            if bool(flagged & KEY_FLAG) != row['idr'] or frame_count == 0 and not row['idr']:
                raise Invalid('fixture IDR/key flag is inconsistent or first picture is not IDR')
            pts = flagged & PTS_MASK
            if previous_pts is not None and pts <= previous_pts:
                raise Invalid('media PTS must strictly increase')
            previous_pts = pts
            spec = sps[row['sps_id']]
            if row['idr']:
                previous_ref_lsb = previous_ref_msb = previous_frame_num = frame_offset = 0
                previous_output_poc = None
            if spec['pic_order_cnt_type'] == 0:
                half = 1 << (spec['poc_lsb_bits'] - 1)
                msb = previous_ref_msb
                if row['poc_lsb'] < previous_ref_lsb and previous_ref_lsb - row['poc_lsb'] >= half:
                    msb += half * 2
                elif row['poc_lsb'] > previous_ref_lsb and row['poc_lsb'] - previous_ref_lsb > half:
                    msb -= half * 2
                poc = msb + row['poc_lsb']
                if row['nal_ref_idc']:
                    previous_ref_lsb, previous_ref_msb = row['poc_lsb'], msb
            else:
                if row['frame_num'] < previous_frame_num:
                    frame_offset += 1 << spec['frame_num_bits']
                poc = 2 * (frame_offset + row['frame_num']) - (0 if row['nal_ref_idc'] else 1)
                previous_frame_num = row['frame_num']
            if previous_output_poc is not None and poc <= previous_output_poc:
                raise Invalid('picture output order differs from decode order; zero reorder claim is unsafe')
            previous_output_poc = poc
            hist[row['slice_type_mod5']] += 1
            frame_count += 1
            original_packet = struct.pack('>QI', flagged, len(payload)) + payload
            old_media.update(original_packet)
            new_media.update(header + new_payload)
    if not changes or not 1 <= frame_count <= 360 or old_media.digest() != new_media.digest():
        raise Invalid('no patch, invalid frame count or media equality verification failed')
    return bytes(output), {'geometry': list(geometry), 'media_frames': frame_count,
                           'media_packets_byte_identical': True, 'media_pts_and_key_flags_identical': True,
                           'media_sha256': old_media.hexdigest(), 'strictly_increasing_picture_order_verified': True,
                           'slice_type_mod5_counts': dict(hist), 'sps_changes': changes}


def private_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.resolve().is_relative_to(Path('/private/tmp')):
        raise Invalid('binary input/output must resolve under /private/tmp')
    return path.resolve()


def ffmpeg_verify(original, patched, expected):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise Invalid('ffmpeg unavailable for optional software verification')
    results = []
    with tempfile.TemporaryDirectory(prefix='huoguo-sps-verify-', dir='/private/tmp') as temp:
        for number, data in enumerate((original, patched)):
            path = Path(temp) / f'{number}.h264'
            _, packets = framed_packets(data)
            path.write_bytes(b''.join(payload for _, payload in packets))
            run = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-threads', '1',
                                  '-i', str(path), '-map', '0:v:0', '-an', '-fps_mode', 'passthrough',
                                  '-f', 'framemd5', 'pipe:1'], capture_output=True, timeout=20)
            rows = [line.strip() for line in run.stdout.splitlines() if line and not line.startswith(b'#')]
            # Ignore demuxer's inferred timestamps; compare exact decoded frame
            # dimensions/size and the per-frame pixel hash in display order.
            hashes = [line.split(b',')[-2:] for line in rows]
            results.append((run.returncode, hashes, bool(run.stderr)))
    valid = all(code == 0 and len(hashes) == expected and not stderr for code, hashes, stderr in results)
    equal = results[0][1] == results[1][1]
    if not valid or not equal:
        raise Invalid('full software decode/frame-pixel equality verification failed')
    return {'software_decode_exit_codes': [row[0] for row in results],
            'software_decoded_frames': expected, 'decoded_frame_pixels_identical': True,
            'decode_method': 'ffmpeg software threads=1 framemd5; no VT'}


def self_test():
    # Minimal progressive Baseline SPS, one reference, no crop/VUI.
    prefix = '01000010' + '00000000' + '00100000' + ue_bits(0) + ue_bits(1)
    prefix += ue_bits(0) + ue_bits(2) + ue_bits(1) + '0' + ue_bits(33) + ue_bits(74) + '11' + '0' + '0'
    original = b'\x67' + escape(pack_rbsp(prefix))
    patched, before, after = patch_sps(original)
    assert before['bitstream_restriction_flag'] == 0 and after['restriction_values'] == [2, 1, 16, 16, 0, 1]
    assert before['level_max_dpb_frames'] == 8 and after['max_num_ref_frames'] == 1
    raw = bytes.fromhex('0000000100000200000300000301')
    assert unescape(escape(raw)) == raw
    try:
        patch_sps(patched)
        raise AssertionError('already restricted SPS accepted')
    except Invalid:
        pass
    try:
        patch_sps(b'\x67\x64' + original[2:])
        raise AssertionError('High SPS accepted')
    except Invalid:
        pass
    try:
        parse_sps(original[:-1])
        raise AssertionError('truncated SPS accepted')
    except Invalid:
        pass
    assert b''.join(marker + nal for marker, nal in annex_nals(b'\x00\x00\x00\x01' + original + b'\x00\x00\x01\x68\x80')) == b'\x00\x00\x00\x01' + original + b'\x00\x00\x01\x68\x80'
    print(json.dumps({'self_test': 'PASS', 'checks': 6, 'scope': 'pure Python bits/SPS/escape tests; no codec/process'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--input')
    parser.add_argument('--output')
    parser.add_argument('--report')
    parser.add_argument('--verify-ffmpeg', action='store_true', help='optional software pixel-equality verification; never VT')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.input or not args.output:
        parser.error('--input and --output are required')
    try:
        source, destination = private_path(args.input), private_path(args.output)
        if source == destination or destination.exists():
            raise Invalid('output must be a distinct, new fixture path')
        if source.stat().st_size > 128 * 1024 * 1024:
            raise Invalid('fixture exceeds bound')
        original = source.read_bytes()
        patched, evidence = patch_framed(original)
        report = {'probe': 'baseline-sps-vui-restriction-ab-v1', 'status': 'PASS',
                  'scope': 'offline SPS-only patch; no VT/phone/network; performance not measured',
                  'input': str(source), 'output': str(destination),
                  'original_sha256': hashlib.sha256(original).hexdigest(),
                  'patched_sha256': hashlib.sha256(patched).hexdigest(), **evidence}
        if args.verify_ffmpeg:
            report.update(ffmpeg_verify(original, patched, evidence['media_frames']))
        with destination.open('xb') as output:
            output.write(patched)
        if args.report:
            report_path = Path(args.report).expanduser().resolve()
            if report_path.suffix != '.json' or report_path in (source, destination):
                raise Invalid('report must be a distinct .json metadata path')
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print(json.dumps(report, sort_keys=True))
        return 0
    except (Invalid, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'probe': 'baseline-sps-vui-restriction-ab-v1', 'status': 'FAIL',
                          'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

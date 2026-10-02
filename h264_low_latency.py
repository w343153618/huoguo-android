"""Conservative SPS-only low-delay hints for verified Apple Baseline H.264.

The caller must restrict this to native hardware-verified codec CONFIG packets.
No socket, file, process, image capture or Android access is performed here.
Unsupported/invalid configs are returned unchanged with JSON-safe metadata.
This parser is deliberately narrow rather than a general H.264 rewriter.
"""
import re
from typing import Any

MAX_DPB_MBS = {10: 396, 11: 900, 12: 2376, 13: 2376, 20: 2376, 21: 4752,
               22: 8100, 30: 8100, 31: 18000, 32: 20480, 40: 32768,
               41: 32768, 42: 34816, 50: 110400, 51: 184320, 52: 184320,
               60: 696320, 61: 696320, 62: 696320}


class Invalid(ValueError):
    pass


class Bits:
    def __init__(self, data):
        self.bits = ''.join(f'{byte:08b}' for byte in data)
        self.pos = 0

    def read(self, size):
        if not 0 <= size <= 64 or self.pos + size > len(self.bits):
            raise Invalid('truncated or oversized bit read')
        value = int(self.bits[self.pos:self.pos + size] or '0', 2)
        self.pos += size
        return value

    def ue(self, maximum=65535):
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
            if zeros > 31:
                raise Invalid('Exp-Golomb exceeds bound')
        value = (1 << zeros) - 1 + self.read(zeros)
        if value > maximum:
            raise Invalid('Exp-Golomb value outside supported range')
        return value

    def se(self, minimum=-65535, maximum=65535):
        unsigned = self.ue(maximum=131070)
        value = (unsigned + 1) // 2 if unsigned & 1 else -unsigned // 2
        if not minimum <= value <= maximum:
            raise Invalid('signed Exp-Golomb outside supported range')
        return value

    def trailing(self):
        if self.read(1) != 1 or len(self.bits) - self.pos > 7 or '1' in self.bits[self.pos:]:
            raise Invalid('invalid RBSP trailing bits or unsupported extension')
        self.pos = len(self.bits)

    def more(self):
        remaining = self.bits[self.pos:]
        return not (remaining.startswith('1') and len(remaining) <= 8 and '1' not in remaining[1:])


def ue_bits(value):
    binary = f'{value + 1:b}'
    return '0' * (len(binary) - 1) + binary


def pack_rbsp(bits):
    bits += '1'
    bits += '0' * (-len(bits) % 8)
    return bytes(int(bits[index:index + 8], 2) for index in range(0, len(bits), 8))


def unescape(data):
    result, zeros, index = bytearray(), 0, 0
    while index < len(data):
        byte = data[index]
        if zeros == 2 and byte == 3:
            if index + 1 >= len(data) or data[index + 1] > 3:
                raise Invalid('invalid emulation-prevention byte')
            zeros = 0
            index += 1
            continue
        if zeros == 2 and byte <= 2:
            raise Invalid('unescaped start-code sequence inside NAL')
        result.append(byte)
        zeros = min(2, zeros + 1) if byte == 0 else 0
        index += 1
    return bytes(result)


def escape(data):
    result, zeros = bytearray(), 0
    for byte in data:
        if zeros == 2 and byte <= 3:
            result.append(3)
            zeros = 0
        result.append(byte)
        zeros = min(2, zeros + 1) if byte == 0 else 0
    return bytes(result)


def annex_nals(payload):
    markers = list(re.finditer(rb'\x00\x00(?:\x00)?\x01', payload))
    if not markers or markers[0].start() != 0:
        raise Invalid('Annex B payload must start with a three/four byte start code')
    result = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(payload)
        nal = payload[marker.end():end]
        if not nal or nal[0] & 0x80:
            raise Invalid('missing or invalid NAL header')
        result.append((marker.group(), nal))
    return result


def hrd(reader):
    count = reader.ue(31) + 1
    reader.read(8)
    for _ in range(count):
        reader.ue(0x7fffffff)
        reader.ue(0x7fffffff)
        reader.read(1)
    reader.read(20)


def parse_sps(nal):
    if nal[0] & 31 != 7 or not 2 <= len(nal) <= 4096:
        raise Invalid('unsupported SPS NAL length/type')
    reader = Bits(unescape(nal[1:].rstrip(b'\x00')))
    profile, constraints, level = reader.read(8), reader.read(8), reader.read(8)
    if profile != 66 or constraints & 3:
        raise Invalid('only Baseline SPS with valid reserved bits is supported')
    identifier = reader.ue(31)
    frame_num_bits = reader.ue(12) + 4
    poc_type = reader.ue(2)
    if poc_type == 1:
        raise Invalid('POC type 1 is outside the conservative patch scope')
    poc_bits = reader.ue(12) + 4 if poc_type == 0 else None
    references = reader.ue(16)
    if references != 1:
        raise Invalid('this one-reference fixture experiment refuses another reference count')
    gaps = reader.read(1)
    width_mbs, height_map = reader.ue(511) + 1, reader.ue(511) + 1
    if reader.read(1) != 1:
        raise Invalid('interlaced SPS is outside the patch scope')
    reader.read(1)  # direct_8x8_inference_flag, preserved
    crop = [reader.ue(8192) for _ in range(4)] if reader.read(1) else [0, 0, 0, 0]
    width, height = width_mbs * 16 - 2 * (crop[0] + crop[1]), height_map * 16 - 2 * (crop[2] + crop[3])
    level_for_dpb = 10 if level == 11 and constraints & 0x10 else level
    if level_for_dpb not in MAX_DPB_MBS or width <= 0 or height <= 0:
        raise Invalid('unknown level or invalid cropping')
    max_dpb = min(MAX_DPB_MBS[level_for_dpb] // (width_mbs * height_map), 16)
    if references > max_dpb:
        raise Invalid('reference count exceeds level DPB limit')
    vui_position = reader.pos
    vui = reader.read(1)
    restriction_position = None
    hrd_present = False
    if vui:
        if reader.read(1):
            aspect = reader.read(8)
            if aspect == 255:
                if reader.read(16) == 0 or reader.read(16) == 0:
                    raise Invalid('invalid extended aspect ratio')
        if reader.read(1):
            reader.read(1)
        if reader.read(1):
            reader.read(4)
            if reader.read(1):
                reader.read(24)
        if reader.read(1):
            reader.ue(5)
            reader.ue(5)
        if reader.read(1):
            if reader.read(32) == 0 or reader.read(32) == 0:
                raise Invalid('invalid timing information')
            reader.read(1)
        nal_hrd = reader.read(1)
        if nal_hrd:
            hrd(reader)
        vcl_hrd = reader.read(1)
        if vcl_hrd:
            hrd(reader)
        hrd_present = bool(nal_hrd or vcl_hrd)
        if hrd_present:
            reader.read(1)
        reader.read(1)  # pic_struct_present_flag, preserved
        restriction_position = reader.pos
        restriction = reader.read(1)
        motion_boundary = None
        if restriction:
            motion_boundary = reader.read(1)
            restriction_values = [reader.ue(65535) for _ in range(6)]
        else:
            restriction_values = None
    else:
        restriction, restriction_values, motion_boundary = 0, None, None
    reader.trailing()
    return {'profile_idc': profile, 'constraints_byte': constraints, 'level_idc': level,
            'sps_id': identifier, 'frame_num_bits': frame_num_bits, 'pic_order_cnt_type': poc_type,
            'poc_lsb_bits': poc_bits, 'max_num_ref_frames': references, 'gaps_in_frame_num_flag': gaps,
            'width': width, 'height': height, 'width_mbs': width_mbs, 'height_mbs': height_map,
            'level_max_dpb_frames': max_dpb, 'vui_parameters_present_flag': vui,
            'bitstream_restriction_flag': restriction, 'restriction_values': restriction_values,
            'motion_vectors_over_pic_boundaries_flag': motion_boundary,
            'hrd_present': hrd_present, '_vui_position': vui_position,
            '_restriction_position': restriction_position, '_bits': reader.bits}


def sps_metadata(value):
    return {key: item for key, item in value.items() if not key.startswith('_')}


def patch_sps(nal, *, allow_existing=False):
    before = parse_sps(nal)
    if before['hrd_present']:
        raise Invalid('HRD is outside the conservative low-delay scope')
    if before['bitstream_restriction_flag']:
        restriction = before['restriction_values']
        if (allow_existing and restriction[-2:] == [0, before['max_num_ref_frames']]
                and all(0 <= value <= 16 for value in restriction[:4])):
            metadata = sps_metadata(before)
            return nal, metadata, dict(metadata)
        raise Invalid('existing restriction is not a supported zero-reorder one-reference limit')
    # Standard default bounds used by Moonlight; reference count stays unchanged.
    values = [2, 1, 16, 16, 0, before['max_num_ref_frames']]
    restriction_bits = '1' + ''.join(ue_bits(value) for value in values)
    if before['vui_parameters_present_flag']:
        prefix = before['_bits'][:before['_restriction_position']] + '1'
    else:
        # Add VUI with all preceding optional fields absent, matching their
        # previous absence; only bitstream restriction is introduced.
        prefix = before['_bits'][:before['_vui_position']] + '1' + '00000000' + '1'
    patched = nal[:1] + escape(pack_rbsp(prefix + restriction_bits))
    trailing_zero_count = len(nal) - len(nal.rstrip(b'\x00'))
    patched += b'\x00' * trailing_zero_count
    after = parse_sps(patched)
    for key in ('profile_idc', 'constraints_byte', 'level_idc', 'sps_id', 'frame_num_bits',
                'pic_order_cnt_type', 'poc_lsb_bits', 'max_num_ref_frames', 'width', 'height'):
        if before[key] != after[key]:
            raise Invalid('SPS core changed unexpectedly')
    if after['restriction_values'] != values:
        raise Invalid('patched restriction did not round-trip')
    return patched, sps_metadata(before), sps_metadata(after)


def parse_pps(nal):
    reader = Bits(unescape(nal[1:].rstrip(b'\x00')))
    identifier, sps_id = reader.ue(255), reader.ue(31)
    if reader.read(1) or reader.read(1) or reader.ue(7):
        raise Invalid('CABAC, bottom-field POC or slice groups are outside this Baseline fixture scope')
    reader.ue(31)
    reader.ue(31)
    reader.read(1)
    if reader.read(2):
        raise Invalid('weighted B prediction is unsupported')
    reader.se(-26, 25)
    reader.se(-26, 25)
    reader.se(-12, 12)
    reader.read(2)
    if reader.read(1):
        raise Invalid('redundant pictures are outside the fixture scope')
    if reader.more():
        if reader.read(1) or reader.read(1):
            raise Invalid('8x8 transform/scaling extensions are unsupported')
        reader.se(-12, 12)
    reader.trailing()
    return identifier, sps_id


def patch_apple_baseline_config(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Patch one native SPS+PPS config, or leave it unchanged with a reason.

    Baseline/progressive/one-ref/no-HRD syntax is required. Correct existing
    zero-reorder/one-ref restrictions are idempotently accepted. This API cannot
    verify the producer identity or actual reference use; the caller must first
    verify the native Apple hardware encoder and disabled frame reordering.
    """
    if not isinstance(payload, bytes):
        raise TypeError('codec configuration must be bytes')
    try:
        if not 1 <= len(payload) <= 65536:
            raise Invalid('configuration length must be 1...65536 bytes')
        nals = annex_nals(payload)
        if len(nals) != 2 or [nal[0] & 31 for _, nal in nals] != [7, 8]:
            raise Invalid('configuration must contain exactly one SPS followed by one PPS')
        (sps_marker, sps_nal), (pps_marker, pps_nal) = nals
        before = parse_sps(sps_nal)
        if before['pic_order_cnt_type'] != 0:
            raise Invalid('production hint accepts only verified Apple POC type 0')
        pps_id, referenced_sps = parse_pps(pps_nal)
        if referenced_sps != before['sps_id']:
            raise Invalid('PPS references another SPS')
        rewritten, original, after = patch_sps(sps_nal, allow_existing=True)
        result = sps_marker + rewritten + pps_marker + pps_nal
        changed = result != payload
        restriction = after['restriction_values']
        metadata: dict[str, Any] = {
            'status': 'patched' if changed else 'unchanged',
            'applied': True,
            'reason': 'missing_restriction_added' if changed else 'already_zero_reorder_one_reference',
            'changed': changed, 'sps_count': 1, 'pps_count': 1, 'pps_id': pps_id,
            'profile_idc': after['profile_idc'], 'level_idc': after['level_idc'],
            'width': after['width'], 'height': after['height'],
            'max_num_ref_frames': after['max_num_ref_frames'],
            'pic_order_cnt_type': after['pic_order_cnt_type'],
            'bitstream_restriction_before': original['bitstream_restriction_flag'],
            'bitstream_restriction_after': after['bitstream_restriction_flag'],
            'max_num_reorder_frames': restriction[-2],
            'max_dec_frame_buffering': restriction[-1],
            'level_max_dpb_frames': after['level_max_dpb_frames'],
            'sps_before': original, 'sps_after': after,
            'non_sps_nals_byte_identical': True,
        }
        return result, metadata
    except Invalid as exc:
        return payload, {'status': 'unsupported', 'applied': False, 'changed': False,
                         'reason': str(exc)}

"""Bounded AAC application datagrams; AES-GCM authentication is an outer layer.

Every packet is independently framed. No retransmission or reliable stream is
created here. Repeat codec configuration with the SAME frame id if needed.
"""
import struct

MAGIC = b'HGUA'
VERSION = 1
AAC = 0x00616163
CONFIG_FLAG = 1 << 62
PTS_MASK = (1 << 61) - 1
HEADER = struct.Struct('>4sBBHQIIIHHII')
MAX_DATAGRAM = 1080
MAX_FRAGMENT = MAX_DATAGRAM - HEADER.size
MAX_RECORD = 65536


def packetize_audio(flagged_pts, payload, frame_id, codec=AAC):
    """Return plaintext fragments for one scrcpy AAC record.

    Audio source timestamps have already been normalized by HostHardwareSession
    to the same microsecond epoch as the hardware video source. Bit 62 identifies
    codec configuration; bit 61 is not a valid audio flag.
    """
    if not isinstance(flagged_pts, int) or not 0 <= flagged_pts < 1 << 63:
        raise ValueError('audio timestamp')
    if flagged_pts & (1 << 61):
        raise ValueError('audio reserved flag')
    if codec != AAC or not isinstance(frame_id, int) or not 0 < frame_id < 1 << 32:
        raise ValueError('audio codec or frame id')
    if not isinstance(payload, (bytes, bytearray, memoryview)) or not 0 < len(payload) <= MAX_RECORD:
        raise ValueError('audio record length')
    if flagged_pts & CONFIG_FLAG and len(payload) > 64:
        raise ValueError('audio configuration length')
    count = (len(payload) + MAX_FRAGMENT - 1) // MAX_FRAGMENT
    flags = 1 if flagged_pts & CONFIG_FLAG else 0
    pts = flagged_pts & PTS_MASK
    if pts > ((1 << 63) - 1) // 1000:
        raise ValueError('audio timestamp conversion overflow')
    packets = []
    for index in range(count):
        offset = index * MAX_FRAGMENT
        fragment = bytes(payload[offset:offset + MAX_FRAGMENT])
        packets.append(HEADER.pack(MAGIC, VERSION, flags, HEADER.size, pts, frame_id,
                                   codec, len(payload), index, count, offset,
                                   len(fragment)) + fragment)
    return packets

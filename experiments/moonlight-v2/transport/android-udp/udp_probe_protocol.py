"""Authenticated datagrams for isolated USB-provisioned UDP video experiments.

This is not a production pairing or Internet NAT traversal protocol. Keys are
random for each bounded test, provisioned through the already authorized USB
connection, and never included in the saved performance evidence.
"""
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'HGUE'
SERVER_NONCE = 0x48475545
CLIENT_NONCE = 0x48475543
HEADER = struct.Struct('>4sIQQ')
MAX_DATAGRAM = 1400


class ReplayWindow:
    def __init__(self, width=4096):
        self.width, self.maximum, self.bits = width, -1, 0

    def accept(self, sequence):
        if sequence < 0 or sequence > 0xffffffffffffffff:
            return False
        if sequence > self.maximum:
            shift = sequence - self.maximum
            self.bits = 0 if shift >= self.width else (self.bits << shift) & ((1 << self.width) - 1)
            self.maximum = sequence
            self.bits |= 1
            return True
        distance = self.maximum - sequence
        if distance >= self.width or self.bits & (1 << distance):
            return False
        self.bits |= 1 << distance
        return True


def seal(key, session, sequence, payload, direction=SERVER_NONCE):
    if len(key) != 32 or not 0 <= session <= 0xffffffffffffffff or not 0 <= sequence <= 0xffffffffffffffff:
        raise ValueError('Invalid experiment key/session/sequence')
    header = HEADER.pack(MAGIC, 1, session, sequence)
    nonce = struct.pack('>IQ', direction, sequence)
    packet = header + AESGCM(key).encrypt(nonce, payload, header)
    if len(packet) > MAX_DATAGRAM:
        raise ValueError('Authenticated datagram exceeds the experiment MTU bound')
    return packet


def open_packet(key, session, packet, replay, direction=CLIENT_NONCE):
    if not HEADER.size + 16 <= len(packet) <= MAX_DATAGRAM:
        raise ValueError('Datagram length')
    magic, version, tag, sequence = HEADER.unpack(packet[:HEADER.size])
    if magic != MAGIC or version != 1 or tag != session:
        raise ValueError('Wrong experiment session')
    # Authenticate before advancing the replay window: an unauthenticated high
    # sequence must never suppress the next real packet.
    payload = AESGCM(key).decrypt(struct.pack('>IQ', direction, sequence),
                                   packet[HEADER.size:], packet[:HEADER.size])
    if not replay.accept(sequence):
        raise ValueError('Duplicate or old authenticated sequence')
    return payload

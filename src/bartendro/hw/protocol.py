"""Wire protocol between the Pi and the Bartendro dispensers (via the router board).

Ported from ui/bartendro/router/driver.py + pack7.py; must match firmware/common/packet.h
and firmware/common/pack7.c exactly.

A packet is 8 bytes: dest id, type, 4 payload bytes, CRC16 (little endian). It is
"7-bit packed" into 10 bytes (so no byte has the high bit set) and sent after a
0xFF 0xFF header. The receiver answers a non-broadcast packet with one ACK byte.
"""

from __future__ import annotations

import struct

BAUD_RATE = 9600

RAW_PACKET_SIZE = 10
PACKET_SIZE = 8
HEADER = b"\xff\xff"

# ACK codes (sent back as a single byte)
ACK_OK = 0
ACK_CRC_FAIL = 1
ACK_TIMEOUT = 2  # host side only: nothing came back
ACK_INVALID = 3
ACK_INVALID_HEADER = 4
ACK_HEADER_IN_PACKET = 5
ACK_RX_CRC_FAIL = 6  # host side only: CRC of a packet we received was wrong

ACK_NAMES = {
    ACK_OK: "ok",
    ACK_CRC_FAIL: "crc fail",
    ACK_TIMEOUT: "timeout",
    ACK_INVALID: "invalid packet",
    ACK_INVALID_HEADER: "invalid header",
    ACK_HEADER_IN_PACKET: "header in packet",
    ACK_RX_CRC_FAIL: "received crc fail",
}

# Packet types
PING = 3
SET_MOTOR_SPEED = 4
TICK_DISPENSE = 5
TIME_DISPENSE = 6
LED_OFF = 7
LED_IDLE = 8
LED_DISPENSE = 9
LED_DRINK_DONE = 10
IS_DISPENSING = 11  # requires response
LIQUID_LEVEL = 12  # requires response
UPDATE_LIQUID_LEVEL = 13
ID_CONFLICT = 14
LED_CLEAN = 15
SET_CS_THRESHOLD = 16
SAVED_TICK_COUNT = 17  # requires response
RESET_SAVED_TICK_COUNT = 18
GET_LIQUID_THRESHOLDS = 19  # requires response
SET_LIQUID_THRESHOLDS = 20
FLUSH_SAVED_TICK_COUNT = 21
TICK_SPEED_DISPENSE = 22
PATTERN_DEFINE = 23
PATTERN_ADD_SEGMENT = 24
PATTERN_FINISH = 25
SET_MOTOR_DIRECTION = 26
GET_VERSION = 27  # requires response (v3+ firmware)
COMM_TEST = 0xFE

DEST_BROADCAST = 0xFF

MOTOR_DIRECTION_FORWARD = 1
MOTOR_DIRECTION_BACKWARD = 0


def crc16_update(crc: int, byte: int) -> int:
    """CRC-16/ARC step (poly 0xA001 reflected), as avr-libc's _crc16_update."""
    crc ^= byte
    for _ in range(8):
        crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = crc16_update(crc, b)
    return crc


def pack_7bit(data: bytes) -> bytes:
    """Spread bytes over 7-bit bytes (port of pack7.c pack_7bit)."""
    buffer = 0
    bitcount = 0
    out = bytearray()
    data = bytes(data)
    i = 0
    while True:
        if bitcount < 7:
            buffer = (buffer << 8) | data[i]
            i += 1
            bitcount += 8
        out.append(buffer >> (bitcount - 7))
        buffer &= (1 << (bitcount - 7)) - 1
        bitcount -= 7
        if i == len(data):
            break
    out.append((buffer << (7 - bitcount)) & 0xFF)
    return bytes(out)


def unpack_7bit(data: bytes) -> bytes:
    """Inverse of pack_7bit (port of pack7.c unpack_7bit)."""
    buffer = 0
    bitcount = 0
    out = bytearray()
    i = 0
    while True:
        if bitcount < 8:
            buffer = (buffer << 7) | data[i]
            i += 1
            bitcount += 7
        if bitcount >= 8:
            out.append(buffer >> (bitcount - 8))
            buffer &= (1 << (bitcount - 8)) - 1
            bitcount -= 8
        if i == len(data):
            break
    return bytes(out)


def body8(dest: int, ptype: int, v0: int = 0, v1: int = 0, v2: int = 0, v3: int = 0) -> bytes:
    return struct.pack("BBBBBB", dest, ptype, v0, v1, v2, v3)


def body16(dest: int, ptype: int, v0: int, v1: int) -> bytes:
    return struct.pack("<BBHH", dest, ptype, v0, v1)


def body32(dest: int, ptype: int, v: int) -> bytes:
    return struct.pack("<BBI", dest, ptype, v)


def encode(body: bytes) -> bytes:
    """6-byte packet body -> header + 10 packed bytes, ready for the wire."""
    if len(body) != PACKET_SIZE - 2:
        raise ValueError(f"packet body must be 6 bytes, got {len(body)}")
    raw = pack_7bit(body + struct.pack("<H", crc16(body)))
    if len(raw) != RAW_PACKET_SIZE:
        raise ValueError(f"encoded packet is {len(raw)} bytes, expected {RAW_PACKET_SIZE}")
    return HEADER + raw


def decode(raw: bytes) -> tuple[int, bytes]:
    """10 packed bytes (header already stripped) -> (ack code, 6-byte body or b"")."""
    if len(raw) != RAW_PACKET_SIZE:
        return ACK_TIMEOUT, b""
    packet = unpack_7bit(raw)
    if len(packet) != PACKET_SIZE:
        return ACK_INVALID, b""
    body = packet[:6]
    (received_crc,) = struct.unpack("<H", packet[6:8])
    if received_crc != crc16(body):
        return ACK_RX_CRC_FAIL, b""
    return ACK_OK, body

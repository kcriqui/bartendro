import random

import pytest

from bartendro.hw import protocol as p


def test_crc16_arc_check_value():
    # Standard check value for CRC-16/ARC (same as avr-libc _crc16_update).
    assert p.crc16(b"123456789") == 0xBB3D


def test_pack_7bit_known_vector():
    # 8 bytes 0xFF -> 64 bits spread over 10 bytes of 7 bits (64 = 9*7 + 1: last byte holds 1 bit).
    assert p.pack_7bit(b"\xff" * 8) == bytes([0x7F] * 9 + [0x40])
    assert p.pack_7bit(bytes(8)) == bytes(10)


def test_pack_unpack_roundtrip_and_no_high_bit():
    rnd = random.Random(1)
    for _ in range(2000):
        data = bytes(rnd.randrange(256) for _ in range(8))
        packed = p.pack_7bit(data)
        assert len(packed) == p.RAW_PACKET_SIZE
        assert all(b < 0x80 for b in packed)  # so 0xFF can only be a header byte
        assert p.unpack_7bit(packed) == data


def test_encode_decode():
    body = p.body16(7, p.TICK_SPEED_DISPENSE, 1234, 166)
    frame = p.encode(body)
    assert frame[:2] == p.HEADER and len(frame) == 12
    assert p.decode(frame[2:]) == (p.ACK_OK, body)


def test_decode_detects_corruption():
    frame = bytearray(p.encode(p.body8(1, p.PING)))
    frame[5] ^= 0x01
    ack, body = p.decode(bytes(frame[2:]))
    assert ack == p.ACK_RX_CRC_FAIL and body == b""
    assert p.decode(b"\x00" * 3)[0] == p.ACK_TIMEOUT


def test_body_layouts_match_firmware_struct():
    # packet_t: dest, type, then a 4-byte union (little endian on AVR)
    assert p.body32(5, p.TIME_DISPENSE, 950) == bytes([5, 6, 0xB6, 0x03, 0, 0])
    assert p.body16(5, p.SET_LIQUID_THRESHOLDS, 120, 75) == bytes([5, 20, 120, 0, 75, 0])
    with pytest.raises(ValueError):
        p.encode(b"\x00" * 5)

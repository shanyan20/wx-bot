import hashlib
import hmac
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wechat_bot.adapters.sqlcipher_snapshot import (
    committed_frames,
    decode_snapshot,
    wal_checksum,
)

KEY, SALT = bytes(range(32)), bytes(range(16))


def encrypted_page(pgno=1, marker=0):
    plain = bytearray(4096)
    plain[:16] = b"SQLite format 3\0"
    struct.pack_into(">H", plain, 16, 4096)
    plain[18:21] = bytes([2, 2, 80])
    plain[100] = marker
    start = 16 if pgno == 1 else 0
    iv = bytes([pgno % 256]) * 16
    enc = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    body = enc.update(bytes(plain[start:4016])) + enc.finalize()
    mac_key = hashlib.pbkdf2_hmac("sha512", KEY, bytes(x ^ 0x3A for x in SALT), 2, 32)
    mac = hmac.digest(mac_key, body + iv + struct.pack("<I", pgno), "sha512")
    return (SALT if start else b"") + body + iv + mac


def make_wal(frames, endian="<"):
    magic = 0x377F0682 if endian == "<" else 0x377F0683
    header = struct.pack(">6I", magic, 3007000, 4096, 1, 123, 456)
    state = wal_checksum(header, endian=endian)
    result = header + struct.pack(">2I", *state)
    for pgno, commit, page in frames:
        prefix = struct.pack(">2I", pgno, commit)
        state = wal_checksum(prefix + page, state, endian)
        result += prefix + struct.pack(">4I", 123, 456, *state) + page
    return result


def test_authenticated_page_decode_does_not_mutate_source():
    source = encrypted_page(marker=17)
    decoded, stats = decode_snapshot(source, b"", KEY, SALT)
    assert decoded[:16] == b"SQLite format 3\0"
    assert decoded[18:20] == b"\x01\x01" and decoded[100] == 17
    assert source == encrypted_page(marker=17)
    assert stats == {"pages": 1, "committed_wal_frames": 0}


@pytest.mark.parametrize("offset", [20, 100, 4017, 4095])
def test_page_corruption_rejected(offset):
    page = bytearray(encrypted_page())
    page[offset] ^= 1
    with pytest.raises(ValueError, match="authentication"):
        decode_snapshot(page, b"", KEY, SALT)


@pytest.mark.parametrize("endian", ["<", ">"])
def test_only_last_committed_prefix_is_applied(endian):
    wal = make_wal([(1, 1, encrypted_page(marker=22)),
                    (1, 0, encrypted_page(marker=33))], endian)
    decoded, stats = decode_snapshot(encrypted_page(marker=11), wal, KEY, SALT)
    assert decoded[100] == 22 and stats["committed_wal_frames"] == 1


def test_uncommitted_only_wal_does_not_override_database():
    wal = make_wal([(1, 0, encrypted_page(marker=33))])
    decoded, stats = decode_snapshot(encrypted_page(marker=11), wal, KEY, SALT)
    assert decoded[100] == 11 and stats["committed_wal_frames"] == 0


def test_stale_salt_tail_is_ignored():
    wal = bytearray(make_wal([(1, 1, encrypted_page())]))
    wal[40] ^= 1
    assert committed_frames(wal) == ([], 0)


def test_bad_wal_header_rejected():
    wal = bytearray(make_wal([]))
    wal[24] ^= 1
    with pytest.raises(ValueError, match="header checksum"):
        committed_frames(wal)


def test_torn_frame_after_commit_preserves_prior_commit():
    wal = make_wal([(1, 1, encrypted_page(marker=22)), (1, 1, encrypted_page(marker=33))])
    decoded, stats = decode_snapshot(encrypted_page(), wal[:-50], KEY, SALT)
    assert decoded[100] == 22 and stats["committed_wal_frames"] == 1


def test_wal_growth_with_missing_page_fails():
    wal = make_wal([(1, 2, encrypted_page())])
    with pytest.raises(ValueError, match="missing page"):
        decode_snapshot(encrypted_page(), wal, KEY, SALT)


def test_wrong_key_is_rejected():
    with pytest.raises(ValueError, match="authentication"):
        decode_snapshot(encrypted_page(), b"", bytes(32), SALT)


def test_bad_frame_checksum_after_commit_is_not_applied():
    wal = bytearray(make_wal([(1, 1, encrypted_page(marker=22)),
                              (1, 1, encrypted_page(marker=33))]))
    wal[-1] ^= 1
    decoded, stats = decode_snapshot(encrypted_page(), wal, KEY, SALT)
    assert decoded[100] == 22 and stats["committed_wal_frames"] == 1

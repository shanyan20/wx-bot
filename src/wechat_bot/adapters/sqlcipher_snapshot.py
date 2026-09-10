"""Authenticated SQLCipher4 snapshot decoding; no source writes or key persistence.

Only 4096-byte pages with an 80-byte reserve are accepted. WAL parsing follows
sqlite.org/fileformat2.html and applies only the valid prefix through its last commit.
"""

import hashlib
import hmac
import struct

PAGE = 4096
RESERVE = 80


def wal_checksum(data, state=(0, 0), endian="<"):
    if len(data) % 8:
        raise ValueError("WAL checksum input must contain pairs of words")
    first, second = state
    for a, b in struct.iter_unpack(endian + "II", data):
        first = (first + a + second) & 0xFFFFFFFF
        second = (second + b + first) & 0xFFFFFFFF
    return first, second


def committed_frames(wal):
    if not wal:
        return [], 0
    if len(wal) < 32:
        raise ValueError("Truncated WAL header")
    magic, version, size, _, salt1, salt2, c1, c2 = struct.unpack(">8I", wal[:32])
    if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or size != PAGE:
        raise ValueError("Unsupported WAL format")
    endian = "<" if magic == 0x377F0682 else ">"
    state = wal_checksum(wal[:24], endian=endian)
    if state != (c1, c2):
        raise ValueError("WAL header checksum mismatch")
    frames, committed, dbsize = [], 0, 0
    for offset in range(32, len(wal) - (24 + PAGE) + 1, 24 + PAGE):
        header = wal[offset:offset + 24]
        pgno, commit_size, s1, s2, c1, c2 = struct.unpack(">6I", header)
        if (s1, s2) != (salt1, salt2) or pgno == 0:
            break  # stale preallocated WAL tail
        page = wal[offset + 24:offset + 24 + PAGE]
        state = wal_checksum(header[:8] + page, state, endian)
        if state != (c1, c2):
            break  # torn/uncommitted tail, never apply it
        frames.append((pgno, page))
        if commit_size:
            committed, dbsize = len(frames), commit_size
    return frames[:committed], dbsize


def decode_snapshot(database, wal, key, salt):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(key) != 32 or len(salt) != 16 or not database or len(database) % PAGE:
        raise ValueError("Invalid SQLCipher snapshot parameters")
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(x ^ 0x3A for x in salt), 2, 32)

    def decode(page, pgno):
        start = 16 if pgno == 1 else 0
        expected = hmac.digest(mac_key, page[start:PAGE - 64] + struct.pack("<I", pgno),
                               "sha512")
        if not hmac.compare_digest(expected, page[PAGE - 64:]):
            raise ValueError(f"Page authentication failed at {pgno}")
        decryptor = Cipher(algorithms.AES(key), modes.CBC(page[4016:4032])).decryptor()
        plaintext = decryptor.update(page[start:4016]) + decryptor.finalize()
        return (b"SQLite format 3\0" if start else b"") + plaintext + bytes(RESERVE)

    frames, committed_size = committed_frames(wal)
    base_size = len(database) // PAGE
    final_size = committed_size or base_size
    if final_size > 262144:  # one GiB decoded snapshot limit
        raise ValueError("Snapshot exceeds memory limit")
    replacements = {pgno: page for pgno, page in frames if pgno <= final_size}
    decoded = bytearray()
    for pgno in range(1, final_size + 1):
        page = replacements.get(pgno, database[(pgno - 1) * PAGE:pgno * PAGE])
        if len(page) != PAGE:
            raise ValueError("Committed snapshot has a missing page")
        decoded.extend(decode(page, pgno))
    if decoded[20] != RESERVE or struct.unpack_from(">H", decoded, 16)[0] != PAGE:
        raise ValueError("Decoded SQLite layout is unsupported")
    # The assembled in-memory image has no accompanying WAL; source stays untouched.
    decoded[18:20] = b"\x01\x01"
    return bytes(decoded), {"pages": final_size, "committed_wal_frames": len(frames)}

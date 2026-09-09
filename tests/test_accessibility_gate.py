"""Synthetic PE checks; these tests never open or write a process."""

import importlib.util
import struct
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "gate", Path(__file__).resolve().parents[1] / "scripts/accessibility_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture_pe():
    data = bytearray(0x800)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, 0x8664, 2)
    struct.pack_into("<H", data, 0x94, 0xF0)
    struct.pack_into("<H", data, 0x98, 0x20B)
    for off, rva, raw, flags in ((0x188, 0x1000, 0x400, 0x20000000),
                                 (0x1B0, 0x2000, 0x600, 0x80000000)):
        struct.pack_into("<IIII", data, off + 8, 0x200, rva, 0x200, raw)
        struct.pack_into("<I", data, off + 36, flags)
    text = b"qt.accessibility.core\0"
    data[0x600:0x600 + len(text)] = text
    data[0x400:0x407] = b"\x48\x8d\x0d" + struct.pack("<i", 0x2000 - 0x1007)
    add_pattern(data, 0x420, 0x2100)
    return data


def add_pattern(data, offset, target):
    pattern = (b"\x48\x85\xc9\x0f\x84\0\0\0\0\x80\x3d"
               + struct.pack("<i", target - (0x1000 + offset - 0x400 + 16))
               + b"\0\x0f\x84")
    data[offset:offset + len(pattern)] = pattern


def test_unique_writable_gate():
    assert gate.scan(fixture_pe())[0]["rva"] == 0x2100


def test_reject_ambiguous_gate():
    data = fixture_pe()
    add_pattern(data, 0x450, 0x2101)
    with pytest.raises(ValueError, match="Expected one gate"):
        gate.scan(data)


def test_reject_missing_string_reference():
    data = fixture_pe()
    data[0x400:0x407] = bytes(7)
    with pytest.raises(ValueError, match="No RIP"):
        gate.scan(data)


def test_reject_executable_target():
    data = fixture_pe()
    add_pattern(data, 0x420, 0x1100)
    with pytest.raises(ValueError, match="Expected one gate"):
        gate.scan(data)


def test_reject_non_amd64_image():
    # Corrupt PE machine must fail before any platform-specific process access.
    data = fixture_pe()
    struct.pack_into("<H", data, 0x84, 0x14C)
    with pytest.raises(ValueError, match="AMD64"):
        gate.scan(data)

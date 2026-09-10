"""Authorized, PID-scoped read-only WCDB key probe. Never prints keys.

Config.Cipher layout reference: wechatauto-replica 798989c, db.py.
Output is Windows DPAPI protected for the current OS user, in ignored data/.
No master-key fallback, DB writes, client writes, or network calls.
"""

import argparse
import ctypes
import hashlib
import hmac
import json
import re
import struct
import time
from ctypes import wintypes
from pathlib import Path

from native_probe import one_window

NAME = b"com.Tencent.WCDB.Config.Cipher"
MASK = bytes.fromhex("d2c7442458020000004889442450488b450048844c2448488944254048584c24")


class Region(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
                ("allocation_protect", wintypes.DWORD), ("alignment", wintypes.DWORD),
                ("size", ctypes.c_size_t), ("state", wintypes.DWORD),
                ("protect", wintypes.DWORD), ("kind", wintypes.DWORD),
                ("alignment2", wintypes.DWORD)]


def verifies(key, page, salt):
    if len(key) != 32 or len(page) != 4096 or len(salt) != 16:
        return False
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(x ^ 0x3A for x in salt), 2, 32)
    expected = hmac.digest(mac_key, page[16:4032] + struct.pack("<I", 1), "sha512")
    return hmac.compare_digest(expected, page[4032:])


def extract(pid, pages):
    import win32api

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = wintypes.BOOL
    deadline = time.monotonic() + 150
    handle = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
    try:
        def read(address, size):
            if time.monotonic() > deadline:
                raise TimeoutError("Read-only scan deadline reached")
            buffer = ctypes.create_string_buffer(size)
            got = ctypes.c_size_t()
            ok = kernel.ReadProcessMemory(int(handle), address, buffer, size, ctypes.byref(got))
            return buffer.raw if ok and got.value == size else b""

        def find(needles):
            found = []
            address = 0
            total = 0
            while address < 0x800000000000:
                region = Region()
                if not kernel.VirtualQueryEx(int(handle), address, ctypes.byref(region),
                                             ctypes.sizeof(region)):
                    break
                base = region.base or 0
                if not region.size or base + region.size <= address:
                    raise ValueError("Invalid memory region")
                if (region.state == 0x1000 and region.protect & 0xFF in (2, 4, 8, 32, 64, 128)
                        and not region.protect & 0x100):
                    for offset in range(0, region.size, 4 * 1024 * 1024):
                        size = min(region.size - offset, 4 * 1024 * 1024 + 128)
                        total += size
                        if total > 8 * 1024**3:
                            raise ValueError("Memory scan budget exceeded")
                        block = read(base + offset, size)
                        for needle in needles:
                            pos = block.find(needle)
                            while pos >= 0:
                                found.append(base + offset + pos)
                                pos = block.find(needle, pos + 1)
                address = base + region.size
            return sorted(set(found))

        names = find([NAME])
        if not names or len(names) > 100:
            raise ValueError("Cipher anchors absent or excessive")
        references = find([struct.pack("<QQ", address, len(NAME)) for address in names])
        keys, tested = {}, set()
        for address in references:
            node = read(address - 16, 80)
            if len(node) != 80:
                continue
            pointer = struct.unpack_from("<Q", node, 40)[0]
            if not 0x10000 <= pointer < 0x800000000000:
                continue
            obj = read(pointer + 0x88, 40)
            if not obj:
                continue
            data_pointer, length = struct.unpack_from("<QQ", obj, 8)
            if not 0 < length <= 1024 or not 0x10000 <= data_pointer < 0x800000000000:
                continue
            blob = read(data_pointer, length)
            decoded = bytes(x ^ MASK[i % len(MASK)] for i, x in enumerate(blob))
            for match in re.finditer(rb"[xX]'([a-fA-F0-9]{64,192})'", decoded):
                raw = bytes.fromhex(match[1].decode("ascii"))
                for start in range(0, len(raw) - 31, 16):
                    key = raw[start:start + 32]
                    for path, page in pages.items():
                        salts = [page[:16]]
                        if len(raw) >= start + 48:
                            salts.append(raw[start + 32:start + 48])
                        for salt in salts:
                            trial = (path, key, salt)
                            if trial in tested:
                                continue
                            tested.add(trial)
                            if verifies(key, page, salt):
                                value = {"key": key.hex(), "salt": salt.hex()}
                                if path in keys and keys[path] != value:
                                    raise ValueError("Conflicting validated keys")
                                keys[path] = value
        return keys
    finally:
        handle.Close()


def main():
    import psutil
    import win32crypt
    from pywinauto import Desktop

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    hwnd = one_window(args.title, args.pid)
    proc = psutil.Process(args.pid)
    started = proc.create_time()
    if Path(proc.exe()).name.lower() != "weixin.exe":
        raise ValueError("Expected Weixin.exe")
    window = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    root_id = window.element_info.automation_id
    if not root_id.startswith("ChatSingleWindowwxid_"):
        raise ValueError("Unrecognized independent chat identity")
    conversation = root_id.removeprefix("ChatSingleWindow")
    files = [Path(f.path) for f in proc.open_files()
             if re.fullmatch(r"message_\d+\.db", Path(f.path).name)
             and Path(f.path).parent.name == "message"]
    roots = {p.parent.parent for p in files}
    if len(roots) != 1 or not files:
        raise ValueError("Message database account is absent or ambiguous")
    root = roots.pop()
    with_pages = {}
    for path in files:
        with path.open("rb") as stream:
            with_pages[str(path)] = stream.read(4096)
    print(json.dumps({"stage": "scanning", "message_shards": len(files)}), flush=True)
    keys = extract(args.pid, with_pages)
    if not keys or len(keys) != len(files):
        raise ValueError(f"Validated only {len(keys)} of {len(files)} message keys")
    if proc.create_time() != started or one_window(args.title, args.pid) != hwnd:
        raise ValueError("Target changed")
    payload = {"pid": args.pid, "started": started, "hwnd": hwnd,
               "title": args.title, "conversation": conversation, "root": str(root), "keys": keys}
    output = Path("data/db-probe/keys.dpapi")
    output.parent.mkdir(parents=True, exist_ok=True)
    protected = win32crypt.CryptProtectData(json.dumps(payload).encode(),
                                            "wx-bot authorized metadata probe", None, None, None, 1)
    output.write_bytes(protected)
    print(json.dumps({"stage": "validated", "key_count": len(keys), "cache": str(output),
                      "protection": "Windows DPAPI current user"}))


if __name__ == "__main__":
    main()

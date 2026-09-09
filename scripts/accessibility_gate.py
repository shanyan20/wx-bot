"""Explicit, experimental one-byte Qt gate probe. Default is read-only.

Pattern reference: fanyuantaier/wechatauto-replica commit 798989c.
No version fallback, chat actions, database access or system setting changes.
--apply-report requires a matching prior preflight and writes only a 0 byte to 1.
This is process-wide; it is not part of normal bot startup.
"""

import argparse
import ctypes
import hashlib
import json
import re
import struct
from pathlib import Path


def scan(data):
    if data[:2] != b"MZ":
        raise ValueError("Not a PE image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    machine, count = struct.unpack_from("<HH", data, pe + 4)
    optsize = struct.unpack_from("<H", data, pe + 20)[0]
    if machine != 0x8664 or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
        raise ValueError("Expected AMD64 PE32+")
    sections = []
    for i in range(count):
        off = pe + 24 + optsize + i * 40
        vsize, rva, size, raw = struct.unpack_from("<IIII", data, off + 8)
        flags = struct.unpack_from("<I", data, off + 36)[0]
        if raw + size > len(data):
            raise ValueError("Truncated section")
        sections.append((rva, vsize, raw, size, flags))
    cores = []
    for match in re.finditer(rb"qt\.accessibility\.core\x00", data):
        for rva, _, raw, size, _ in sections:
            if raw <= match.start() < raw + size:
                cores.append(rva + match.start() - raw)
    if len(cores) != 1:
        raise ValueError("Accessibility string is absent or ambiguous")
    xrefs = []
    executable = [s for s in sections if s[4] & 0x20000000]
    for rva, _, raw, size, _ in executable:
        for match in re.finditer(rb"[\x40-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....",
                                 data[raw:raw + size], re.DOTALL):
            pos = rva + match.start()
            if pos + 7 + struct.unpack_from("<i", match.group(), 3)[0] == cores[0]:
                xrefs.append(pos)
    if not xrefs:
        raise ValueError("No RIP references to accessibility string")
    candidates = []
    pattern = rb"\x48\x85\xc9\x0f\x84....\x80\x3d(?P<disp>.{4})\x00\x0f\x84"
    for rva, _, raw, size, _ in executable:
        for match in re.finditer(pattern, data[raw:raw + size], re.DOTALL):
            pos = rva + match.start()
            target = rva + match.start("disp") + 5 + struct.unpack("<i", match["disp"])[0]
            writable = any(s[0] <= target < s[0] + s[1]
                           and s[4] & 0x80000000 and not s[4] & 0x20000000 for s in sections)
            distance = min(abs(pos - ref) for ref in xrefs)
            if writable and distance <= 0x20000:
                candidates.append({"rva": target, "pattern_rva": pos,
                                   "pattern_hex": match.group().hex(), "distance": distance})
    if len({c["rva"] for c in candidates}) != 1:
        raise ValueError(f"Expected one gate target, found {len(candidates)} candidates")
    return candidates


def main():
    import psutil
    import win32api
    import win32process
    from native_probe import one_window

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-report", type=Path)
    args = parser.parse_args()
    hwnd = one_window(args.title, args.pid)
    process = psutil.Process(args.pid)
    started = process.create_time()
    if Path(process.exe()).name.lower() != "weixin.exe":
        raise ValueError("Expected Weixin.exe")
    access = 0x0400 | 0x0010
    if args.apply_report:
        access |= 0x0020 | 0x0008
    handle = win32api.OpenProcess(access, False, args.pid)
    try:
        modules = [(base, win32process.GetModuleFileNameEx(handle, base))
                   for base in win32process.EnumProcessModules(handle)]
        modules = [(base, path) for base, path in modules
                   if Path(path).name.lower() == "weixin.dll"]
        if len(modules) != 1:
            raise ValueError("Expected one Weixin.dll")
        base, path = modules[0]
        data = Path(path).read_bytes()
        candidates = scan(data)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signature = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        kernel.ReadProcessMemory.argtypes = signature
        kernel.ReadProcessMemory.restype = ctypes.c_int
        kernel.WriteProcessMemory.argtypes = signature
        kernel.WriteProcessMemory.restype = ctypes.c_int

        def read(address, length):
            buffer = ctypes.create_string_buffer(length)
            transferred = ctypes.c_size_t()
            ok = kernel.ReadProcessMemory(int(handle), address, buffer, length,
                                          ctypes.byref(transferred))
            if not ok or transferred.value != length:
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer.raw

        for candidate in candidates:
            expected = bytes.fromhex(candidate["pattern_hex"])
            if read(base + candidate["pattern_rva"], len(expected)) != expected:
                raise ValueError("Live code differs from disk signature")
        address = base + candidates[0]["rva"]
        original = read(address, 1)[0]
        if original not in (0, 1):
            raise ValueError("Gate byte is neither 0 nor 1")
        identity = {"pid": args.pid, "started": started, "hwnd": hwnd, "dll": path,
                    "sha256": hashlib.sha256(data).hexdigest(), "base": base,
                    "candidates": candidates}
        report = {"identity": identity, "original": original, "write_attempted": False,
                  "status": "preflight_passed"}
        args.output.parent.mkdir(parents=True, exist_ok=True)

        def save():
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

        if args.apply_report:
            approved = json.loads(args.apply_report.read_text(encoding="utf-8"))
            if approved["identity"] != identity or approved["original"] != original:
                raise ValueError("Preflight identity or gate byte changed")
            if (one_window(args.title, args.pid) != hwnd
                    or psutil.Process(args.pid).create_time() != started):
                raise ValueError("Process/window changed")
            if read(address, 1)[0] != original:
                raise ValueError("Gate changed before write")
            if original == 0:
                report.update(write_attempted=True, status="write_pending")
                save()
                value, transferred = ctypes.c_ubyte(1), ctypes.c_size_t()
                ok = kernel.WriteProcessMemory(int(handle), address, ctypes.byref(value), 1,
                                               ctypes.byref(transferred))
                report.update(write_api_success=bool(ok), bytes_written=transferred.value)
                save()
                report["after"] = read(address, 1)[0]
                report["status"] = ("activated" if ok and transferred.value == 1
                                    and report["after"] == 1 else "write_unconfirmed")
            else:
                report["status"] = "already_active"
        save()
        print(json.dumps(report, indent=2))
    finally:
        handle.Close()


if __name__ == "__main__":
    main()

"""Local image resolution scoped to one conversation and native metadata digests."""

import hashlib
import io
import re
import struct
import subprocess
import time
from pathlib import Path

from PIL import Image

MAX_BYTES = 8 * 1024 * 1024
_IMAGE_KEYS = {}  # process-local only; every reuse must validate against the target image


def image_key(pid, data, check=lambda: None, xor_key=None):
    """Read only the pinned process; validate candidates against this exact image."""
    check()
    if pid in _IMAGE_KEYS:
        try:
            decode_dat(data, _IMAGE_KEYS[pid], xor_key)
            return _IMAGE_KEYS[pid]
        except (ValueError, OSError):
            del _IMAGE_KEYS[pid]
    import ctypes
    from ctypes import wintypes

    import win32api
    import win32process
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    class Region(ctypes.Structure):
        _fields_ = [("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
                    ("protect0", wintypes.DWORD), ("align", wintypes.DWORD),
                    ("size", ctypes.c_size_t), ("state", wintypes.DWORD),
                    ("protect", wintypes.DWORD), ("kind", wintypes.DWORD),
                    ("align2", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = wintypes.BOOL
    handle = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
    seen, address, deadline, total = set(), 0, time.monotonic() + 45, 0
    try:
        def read(address, size):
            check()
            if size <= 0 or size > 256 * 1024 * 1024:
                return b""
            buf, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
            ok = kernel.ReadProcessMemory(int(handle), address, buf, size, ctypes.byref(got))
            return buf.raw if ok and got.value == size else b""

        # Version-sensitive candidate only: validate against this exact image before use.
        # Config layout was statically reviewed in replica db.py; never read/export master keys.
        for base in win32process.EnumProcessModules(handle):
            path = Path(win32process.GetModuleFileNameEx(handle, base))
            if path.name.lower() != "weixin.dll":
                continue
            with path.open("rb") as stream:
                header = stream.read(4096)
            pe = struct.unpack_from("<I", header, 0x3C)[0]
            size = struct.unpack_from("<I", header, pe + 24 + 56)[0]
            module = read(base, size)
            for match in re.finditer(b"global_config", module):
                pos = match.start() + 16
                if pos < 0x138 or pos + 16 > len(module):
                    continue
                length, capacity = struct.unpack_from("<QQ", module, pos)
                if length != 13 or capacity != 15:
                    continue
                pointer = read(base + pos - 0x138, 8)
                if not pointer:
                    continue
                pointer = read(struct.unpack("<Q", pointer)[0] + 0x68, 8)
                if not pointer:
                    continue
                config = struct.unpack("<Q", pointer)[0]
                value, user = read(config + 0x40, 4), read(config + 0x48, 32)
                if not value or not user:
                    continue
                length = struct.unpack_from("<Q", user, 16)[0]
                if not 1 <= length <= 64:
                    continue
                username = user[:length] if length <= 15 else read(
                    struct.unpack_from("<Q", user)[0], length)
                if not username or not re.fullmatch(rb"[a-zA-Z0-9_@.-]+", username):
                    continue
                material = str(struct.unpack("<I", value)[0]).encode() + username
                candidate = hashlib.md5(material).hexdigest()[:16].encode()
                try:
                    decode_dat(data, candidate, xor_key)
                    _IMAGE_KEYS[pid] = candidate
                    return candidate
                except (ValueError, OSError):
                    pass
            del module
        while address < 0x800000000000 and time.monotonic() < deadline:
            check()
            region = Region()
            if not kernel.VirtualQueryEx(int(handle), address, ctypes.byref(region),
                                         ctypes.sizeof(region)):
                break
            base = region.base or 0
            if not region.size or base + region.size <= address:
                break
            if (region.state == 0x1000 and region.protect & 0xFF in (2, 4, 8, 32, 64, 128)
                    and not region.protect & 0x100):
                for offset in range(0, region.size, 4 * 1024 * 1024):
                    check()
                    size = min(region.size - offset, 4 * 1024 * 1024 + 64)
                    total += size
                    if total > 4 * 1024**3 or time.monotonic() > deadline:
                        raise ValueError("图片密钥读取超时；请在微信中打开该图片后重试")
                    buf, read = ctypes.create_string_buffer(size), ctypes.c_size_t()
                    if not kernel.ReadProcessMemory(int(handle), base + offset, buf, size,
                                                    ctypes.byref(read)):
                        continue
                    block = buf.raw[:read.value]
                    candidates = [m[0][:16] for m in re.finditer(
                        rb"(?<![A-Za-z0-9])[A-Za-z0-9]{16,32}(?![A-Za-z0-9])", block)]
                    candidates += [m[0][::2][:16] for m in re.finditer(
                        rb"(?:[A-Za-z0-9]\x00){16,32}", block)]
                    for key in candidates:
                        check()
                        if time.monotonic() > deadline:
                            raise ValueError("图片密钥读取超时；请打开原图后重试")
                        if key in seen:
                            continue
                        seen.add(key)
                        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
                        prefix = dec.update(data[15:31]) + dec.finalize()
                        magic = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"wxgf")
                        if prefix.startswith(magic):
                            try:
                                decode_dat(data, key, xor_key)  # require full decode
                                _IMAGE_KEYS[pid] = key
                                return key
                            except (ValueError, OSError):
                                pass
            address = base + region.size
    finally:
        handle.Close()
    raise ValueError("未取得可验证的图片密钥；请在微信中打开这张图片后重试")


def normalize_image(data):
    if len(data) > MAX_BYTES:
        raise ValueError("图片超过 8 MiB")
    if data.startswith(b"wxgf"):
        import imageio_ffmpeg
        start = data.find(b"\x00\x00\x00\x01")
        if start < 0:
            raise ValueError("WXGF 图片没有有效 HEVC 数据")
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-xerror", "-err_detect", "explode", "-max_alloc", "67108864", "-threads", "1",
             "-f", "hevc", "-i", "pipe:0", "-frames:v", "1", "-f", "image2pipe",
             "-vcodec", "png", "pipe:1"], input=data[start:], capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not result.stdout:
            raise ValueError("WXGF/HEVC 图片解码失败，不能送入模型")
        data = result.stdout
        if len(data) > MAX_BYTES:
            raise ValueError("WXGF 解码结果超过测试大小上限")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("图片像素超过测试上限")
        image.load()
        out = io.BytesIO()
        image.convert("RGB").save(out, format="PNG")
        result = out.getvalue()
        if len(result) > MAX_BYTES:
            raise ValueError("解码后的图片超过测试上限")
        return result


def decode_dat(data, aes_key=None, xor_key=None):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(data) > MAX_BYTES:
        raise ValueError("媒体文件过大")
    if data.startswith(b"\x07\x08\x56\x32\x08\x07"):
        if len(data) < 31:
            raise ValueError("V2 图片头不完整")
        if not aes_key:
            raise ValueError("V2 图片需要本机图片密钥；不得用占位图代替")
        aes_size, xor_size = struct.unpack_from("<II", data, 6)
        aes_length = (aes_size // 16 + 1) * 16
        if 15 + aes_length + xor_size > len(data):
            raise ValueError("损坏的 V2 图片")
        dec = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
        prefix = dec.update(data[15:15 + aes_length]) + dec.finalize()
        pad = prefix[-1]
        if not 1 <= pad <= 16 or prefix[-pad:] != bytes([pad]) * pad:
            raise ValueError("V2 图片密钥校验失败")
        if len(prefix) - pad != aes_size:
            raise ValueError("V2 图片 AES 长度不一致")
        end = len(data) - xor_size
        prefix = prefix[:-pad] + data[15 + aes_length:end]
        if not xor_size:
            return normalize_image(prefix)
        # Only infer the XOR suffix from a JPEG terminator, never use a default key.
        key = xor_key
        if key is None:
            trailer = b"IEND\xaeB`\x82" if prefix.startswith(b"\x89PNG") else b"\xff\xd9"
            key = data[-1] ^ trailer[-1]
            if bytes(x ^ key for x in data[-len(trailer):]) != trailer:
                raise ValueError("图片尾部 XOR 密钥无法验证")
        return normalize_image(prefix + bytes(x ^ key for x in data[end:]))
    if data.startswith(b"\x07\x08\x05\x56\x02\x05"):
        data = data[22:]
    try:
        return normalize_image(data)
    except (ValueError, OSError):
        pass
    for magic in (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF"):
        if not data:
            break
        key = data[0] ^ magic[0]
        if bytes(x ^ key for x in data[:len(magic)]) == magic:
            return normalize_image(bytes(x ^ key for x in data))
    raise ValueError("图片尚未下载或编码不支持；本条不能通过图片验收")


def resolve_image(account_dir, conversation, blobs, output_dir, aes_key=None, pid=None,
                  check=lambda: None):
    check()
    chat_hash = hashlib.md5(conversation.encode()).hexdigest()
    root = (Path(account_dir) / "msg" / "attach" / chat_hash).resolve()
    digests = set()
    for blob in blobs:
        if isinstance(blob, str):
            blob = blob.encode()
        if isinstance(blob, bytes):
            digests.update(m.decode().lower() for m in re.findall(rb"[0-9a-fA-F]{32}", blob))
    matches = []
    for digest in digests:
        for suffix in ("_h.dat", ".dat", "_t.dat"):
            found = [p.resolve() for p in root.glob(f"*/Img/{digest}{suffix}")
                     if p.resolve().is_relative_to(root)]
            if found:
                matches.extend(found)
                break
    if len(matches) != 1:
        raise ValueError("无法在目标会话目录唯一定位图片；请先在微信下载原图")
    path = matches[0]
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    xor_key = None
    base_name = re.sub(r"_[ht]$", "", path.stem)
    thumbnail = path.with_name(base_name + "_t.dat")
    if thumbnail.exists() and thumbnail.resolve().is_relative_to(root):
        with thumbnail.open("rb") as stream:
            if stream.seek(0, 2) >= 2:
                stream.seek(-2, 2)
                tail = stream.read(2)
                candidate = tail[0] ^ 0xFF
                if tail[1] ^ candidate == 0xD9:
                    xor_key = candidate
    if data.startswith(b"\x07\x08\x56\x32\x08\x07") and not aes_key and pid:
        aes_key = image_key(pid, data, check, xor_key)
    result = decode_dat(data, aes_key, xor_key)
    output = Path(output_dir) / (hashlib.sha256(result).hexdigest() + ".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    check()
    output.write_bytes(result)
    return str(output), "缩略图" if path.stem.endswith("_t") else "本地图片"

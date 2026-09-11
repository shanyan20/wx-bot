import io
import struct

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image

from wechat_bot.adapters.native_media import decode_dat, resolve_image


def png():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG")
    return output.getvalue()


def test_plain_and_xor_image_are_real_decodable_pixels():
    raw = png()
    for data in (raw, bytes(x ^ 77 for x in raw)):
        result = decode_dat(data)
        with Image.open(io.BytesIO(result)) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)


def test_v2_aes_roundtrip_and_wrong_key_rejected():
    raw, key = png(), b"1234567890abcdef"
    pad = 16 - len(raw) % 16
    encoder = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    body = encoder.update(raw + bytes([pad]) * pad) + encoder.finalize()
    data = b"\x07\x08\x56\x32\x08\x07" + struct.pack("<II", len(raw), 0) + b"\0" + body
    assert decode_dat(data, key) == decode_dat(raw)
    with pytest.raises(ValueError):
        decode_dat(data, b"0000000000000000")
    with pytest.raises(ValueError):
        decode_dat(data)


def test_missing_or_ambiguous_media_never_uses_other_conversation(tmp_path):
    import hashlib
    digest = "a" * 32
    root = tmp_path / "msg/attach" / hashlib.md5(b"other").hexdigest() / "2026/Img"
    root.mkdir(parents=True)
    (root / (digest + ".dat")).write_bytes(png())
    with pytest.raises(ValueError, match="唯一定位"):
        resolve_image(tmp_path, "allowed", [digest], tmp_path / "out")
    allowed = tmp_path / "msg/attach" / hashlib.md5(b"allowed").hexdigest()
    for date in ("2026", "2025"):
        path = allowed / date / "Img"
        path.mkdir(parents=True)
        (path / (digest + ".dat")).write_bytes(png())
    with pytest.raises(ValueError, match="唯一定位"):
        resolve_image(tmp_path, "allowed", [digest], tmp_path / "out")


def test_media_stop_checked_before_access(tmp_path):
    def stopped():
        raise RuntimeError("stopped")
    with pytest.raises(RuntimeError, match="stopped"):
        resolve_image(tmp_path, "allowed", [], tmp_path / "out", check=stopped)


def test_wxgf_container_with_real_hevc_frame_decodes():
    import subprocess

    import imageio_ffmpeg

    from wechat_bot.adapters.native_media import normalize_image
    encoded = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1",
         "-c:v", "libx265", "-x265-params", "pools=1:frame-threads=1:log-level=error",
         "-f", "hevc", "pipe:1"], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    result = normalize_image(b"wxgf" + bytes(12) + encoded.stdout)
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (64, 64)
        red, green, blue = image.getpixel((32, 32))
        assert red > 200 and green < 30 and blue < 30

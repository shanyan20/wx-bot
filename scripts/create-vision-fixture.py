"""生成一张非隐私的视觉接口测试图片：左红右蓝。使用标准 PNG 编码，无额外依赖。"""

import struct
import zlib
from pathlib import Path


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def main():
    size = 256
    row = b"\x00" + b"\xff\x00\x00" * (size // 2) + b"\x00\x00\xff" * (size // 2)
    image = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    ) + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b"")
    target = Path(__file__).resolve().parents[1] / "data/vision-fixture.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image)
    print(target)


if __name__ == "__main__":
    main()


"""将明确传入的本地图片编码为多模态 content block。

不从聊天正文解析本地路径，不自动截图，不访问任意图片 URL。
人工验收入口通过 native_media 校验会话媒体并解码后调用本模块；旧内核入口仍需单独接入。
"""

import base64
from pathlib import Path

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 项目上限，刻意小于提供者上限。
MAX_IMAGES = 4


def image_data_url(path: Path) -> str:
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("单张测试图片不可超过 8 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("图片必须为 PNG、JPEG、GIF 或 WebP，文件头不匹配")
    # 这里只做魔数和大小检查；真正的图像解码有效性由模型服务验证。
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def user_content(prompt: str, images: list[Path] | None = None) -> str | list[dict]:
    if not images:
        return prompt
    if len(images) > MAX_IMAGES:
        raise ValueError("单次请求最多 4 张图片")
    return [{"type": "text", "text": prompt}, *[
        {"type": "image_url", "image_url": {"url": image_data_url(path)}} for path in images
    ]]

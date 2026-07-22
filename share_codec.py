import base64
import json
import zlib
from dataclasses import dataclass

PREFIX = "xfb1."
VERSION = 1


@dataclass
class SharePayload:
    url: str
    name: str
    size: int


def encode_share_text(payload):
    data = {
        "v": VERSION,
        "u": payload.url,
        "n": payload.name,
        "s": payload.size,
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return PREFIX + encoded


def decode_share_text(text):
    if not isinstance(text, str) or not text.startswith(PREFIX):
        raise ValueError("分享文本前缀错误")

    payload = text[len(PREFIX):].strip()
    if not payload:
        raise ValueError("分享文本解析失败")

    padding = "=" * (-len(payload) % 4)
    try:
        compressed = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        raw = zlib.decompress(compressed)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("分享文本解析失败") from exc

    if data.get("v") != VERSION:
        raise ValueError("分享文本版本不支持")

    url = data.get("u")
    name = data.get("n")
    size = data.get("s")
    if not isinstance(url, str) or not url:
        raise ValueError("分享文本缺少直链")
    if not isinstance(name, str) or not name:
        raise ValueError("分享文本缺少文件名")
    if not isinstance(size, int) or size < 0:
        raise ValueError("分享文本文件大小无效")

    return SharePayload(url=url, name=name, size=size)

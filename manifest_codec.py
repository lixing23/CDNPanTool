import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
MODE = "chunked"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class ChunkRecord:
    index: int
    size: int
    sha256: str
    url: str
    stego: bool

    def to_dict(self):
        return {
            "index": self.index,
            "size": self.size,
            "sha256": self.sha256,
            "url": self.url,
            "stego": self.stego,
        }


@dataclass
class ChunkManifest:
    name: str
    size: int
    sha256: str
    chunk_size: int
    chunks: list
    created_at: str = ""

    def to_dict(self):
        created_at = self.created_at or datetime.now(timezone.utc).isoformat()
        return {
            "v": VERSION,
            "mode": MODE,
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "chunk_size": self.chunk_size,
            "chunk_count": len(self.chunks),
            "stego_chunks": any(chunk.stego for chunk in self.chunks),
            "created_at": created_at,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }


def _is_sha256(value):
    return isinstance(value, str) and bool(SHA256_PATTERN.match(value))


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("manifest 格式错误")
    if data.get("v") != VERSION:
        raise ValueError("manifest 版本不支持")
    if data.get("mode") != MODE:
        raise ValueError("manifest 模式不支持")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise ValueError("manifest 文件名无效")
    if not isinstance(data.get("size"), int) or data["size"] < 0:
        raise ValueError("manifest 文件大小无效")
    if not _is_sha256(data.get("sha256")):
        raise ValueError("manifest 文件校验值无效")
    if not isinstance(data.get("chunk_size"), int) or data["chunk_size"] <= 0:
        raise ValueError("manifest 分片大小无效")

    chunks = data.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("manifest 分片列表无效")
    if data.get("chunk_count") != len(chunks):
        raise ValueError("分片数量不一致")

    for expected_index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError("manifest 分片格式错误")
        if chunk.get("index") != expected_index:
            raise ValueError("分片序号不连续")
        if not isinstance(chunk.get("size"), int) or chunk["size"] < 0:
            raise ValueError("manifest 分片大小无效")
        if not _is_sha256(chunk.get("sha256")):
            raise ValueError("manifest 分片校验值无效")
        if not isinstance(chunk.get("url"), str) or not chunk["url"]:
            raise ValueError("manifest 分片链接无效")
        if not isinstance(chunk.get("stego"), bool):
            raise ValueError("manifest 分片伪装标记无效")

    return data


def save_manifest_file(manifest, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.to_dict()
    validate_manifest(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest_file(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("manifest 读取失败") from exc
    validate_manifest(data)
    chunks = [
        ChunkRecord(
            index=chunk["index"],
            size=chunk["size"],
            sha256=chunk["sha256"],
            url=chunk["url"],
            stego=chunk["stego"],
        )
        for chunk in data["chunks"]
    ]
    return ChunkManifest(
        name=data["name"],
        size=data["size"],
        sha256=data["sha256"],
        chunk_size=data["chunk_size"],
        chunks=chunks,
        created_at=data.get("created_at", ""),
    )

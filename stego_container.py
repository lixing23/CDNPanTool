import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAGIC = "XFBSTEG1"
VERSION = 1
TRAILER_WINDOW = 4096


@dataclass
class ContainerMetadata:
    name: str
    size: int
    mime: str
    sha256: str
    created_at: str


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(src, dst):
    while True:
        chunk = src.read(1024 * 1024)
        if not chunk:
            break
        dst.write(chunk)


def create_container(cover_path, source_path, output_path):
    cover_path = Path(cover_path)
    source_path = Path(source_path)
    output_path = Path(output_path)

    if not cover_path.exists():
        raise FileNotFoundError("封面图片不存在")
    if not source_path.exists():
        raise FileNotFoundError("待上传文件不存在")

    source_size = source_path.stat().st_size
    metadata = ContainerMetadata(
        name=source_path.name,
        size=source_size,
        mime=mimetypes.guess_type(str(source_path))[0] or "application/octet-stream",
        sha256=_sha256_file(source_path),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    metadata_dict = {
        "v": VERSION,
        "name": metadata.name,
        "size": metadata.size,
        "mime": metadata.mime,
        "sha256": metadata.sha256,
        "created_at": metadata.created_at,
    }
    metadata_bytes = json.dumps(metadata_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as output:
        with open(cover_path, "rb") as cover:
            _copy_file(cover, output)
        metadata_offset = output.tell()
        output.write(metadata_bytes)
        file_offset = output.tell()
        with open(source_path, "rb") as source:
            _copy_file(source, output)
        trailer = f"{MAGIC}|{metadata_offset}|{len(metadata_bytes)}|{file_offset}|{source_size}|".encode("utf-8")
        trailer += str(len(trailer) + len(str(len(trailer)))).encode("utf-8")
        output.write(trailer)

    return metadata


def _find_trailer(container_path):
    container_path = Path(container_path)
    size = container_path.stat().st_size
    read_size = min(size, TRAILER_WINDOW)
    with open(container_path, "rb") as file:
        file.seek(size - read_size)
        tail = file.read(read_size)

    marker = MAGIC.encode("utf-8") + b"|"
    index = tail.rfind(marker)
    if index < 0:
        raise ValueError("不是有效伪装图片")

    trailer_bytes = tail[index:]
    try:
        text = trailer_bytes.decode("utf-8")
        parts = text.split("|")
        if len(parts) != 6 or parts[0] != MAGIC:
            raise ValueError("不是有效伪装图片")
        metadata_offset = int(parts[1])
        metadata_size = int(parts[2])
        file_offset = int(parts[3])
        file_size = int(parts[4])
        trailer_size = int(parts[5])
    except Exception as exc:
        raise ValueError("不是有效伪装图片") from exc

    if trailer_size != len(trailer_bytes):
        raise ValueError("不是有效伪装图片")
    if metadata_offset < 0 or metadata_size <= 0 or file_offset < 0 or file_size < 0:
        raise ValueError("不是有效伪装图片")
    if file_offset + file_size + trailer_size != size:
        raise ValueError("不是有效伪装图片")

    return metadata_offset, metadata_size, file_offset, file_size


def inspect_container(container_path):
    container_path = Path(container_path)
    metadata_offset, metadata_size, _, _ = _find_trailer(container_path)
    with open(container_path, "rb") as file:
        file.seek(metadata_offset)
        raw = file.read(metadata_size)

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("不是有效伪装图片") from exc

    if data.get("v") != VERSION:
        raise ValueError("伪装图片版本不支持")

    return ContainerMetadata(
        name=data["name"],
        size=data["size"],
        mime=data["mime"],
        sha256=data["sha256"],
        created_at=data["created_at"],
    )


def extract_container(container_path, output_path):
    container_path = Path(container_path)
    output_path = Path(output_path)
    _, _, file_offset, file_size = _find_trailer(container_path)
    metadata = inspect_container(container_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    remaining = file_size
    with open(container_path, "rb") as source, open(output_path, "wb") as output:
        source.seek(file_offset)
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("不是有效伪装图片")
            output.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)

    if digest.hexdigest() != metadata.sha256:
        output_path.unlink(missing_ok=True)
        raise ValueError("SHA-256 校验失败")

    return metadata

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LocalChunk:
    index: int
    path: Path
    size: int
    sha256: str


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def split_file(source_path, output_dir, chunk_size):
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    index = 0
    with open(source_path, "rb") as source:
        while True:
            data = source.read(chunk_size)
            if not data:
                break
            path = output_dir / f"part-{index:06d}.bin"
            path.write_bytes(data)
            chunks.append(LocalChunk(index=index, path=path, size=len(data), sha256=sha256_file(path)))
            index += 1
    return chunks


def merge_chunks(chunk_paths, output_path, expected_sha256):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as output:
        for chunk_path in chunk_paths:
            with open(chunk_path, "rb") as chunk:
                while True:
                    data = chunk.read(1024 * 1024)
                    if not data:
                        break
                    output.write(data)
    actual = sha256_file(output_path)
    if actual != expected_sha256:
        output_path.unlink(missing_ok=True)
        raise ValueError("原文件 SHA-256 校验失败")
    return output_path


def cleanup_dir(path):
    path = Path(path)
    if path.exists():
        shutil.rmtree(path)

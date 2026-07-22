# 分片上传与索引图片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前伪装图片云盘桌面工具增加可自定义分片大小、并发上传、分片 manifest 索引图片、分片下载合并和失败重试降级能力。

**Architecture:** 在现有单文件伪装上传基础上新增分片层。小文件继续走当前单文件流程，大文件走 chunk_manager 切分、并发上传分片、manifest_codec 生成索引清单、stego_container 生成索引图片，解析时自动识别 manifest 并下载合并。

**Tech Stack:** Python 3 标准库、Tkinter、urllib、json、hashlib、concurrent.futures、unittest。

---

## File Structure

- Create: `chunk_manager.py`
  - 切分文件、计算 SHA-256、合并分片、清理临时目录。
- Create: `manifest_codec.py`
  - 定义 `ChunkRecord`、`ChunkManifest`，生成、保存、加载、校验 manifest。
- Modify: `settings_store.py`
  - 增加 `chunk_size_mb`、`upload_workers`、`stego_chunks`，并进行范围归一化。
- Modify: `oss_client.py`
  - 增加可重试错误识别和 `upload_file_with_retry`。
- Modify: `image_uploader_gui.py`
  - 增加分片设置 UI。
  - 根据文件大小选择单文件或分片上传。
  - 增加分片上传 worker 和分片下载合并 worker。
- Create: `tests/test_chunk_manager.py`
- Create: `tests/test_manifest_codec.py`
- Modify: `tests/test_settings_store.py`
- Create: `tests/test_oss_client.py`

---

### Task 1: 扩展设置模型

**Files:**
- Modify: `settings_store.py`
- Modify: `tests/test_settings_store.py`

- [ ] **Step 1: 添加设置测试**

在 `tests/test_settings_store.py` 中新增测试：

```python
    def test_default_chunk_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(Path(temp_dir))
            settings = store.load_settings()

            self.assertEqual(settings.chunk_size_mb, 50)
            self.assertEqual(settings.upload_workers, 3)
            self.assertTrue(settings.stego_chunks)

    def test_invalid_chunk_settings_are_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "settings.json").write_text(
                '{"chunk_size_mb":999,"upload_workers":99,"stego_chunks":"yes"}',
                encoding="utf-8",
            )
            store = SettingsStore(root)
            settings = store.load_settings()

            self.assertEqual(settings.chunk_size_mb, 200)
            self.assertEqual(settings.upload_workers, 8)
            self.assertTrue(settings.stego_chunks)

    def test_low_chunk_settings_are_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "settings.json").write_text(
                '{"chunk_size_mb":1,"upload_workers":0,"stego_chunks":false}',
                encoding="utf-8",
            )
            store = SettingsStore(root)
            settings = store.load_settings()

            self.assertEqual(settings.chunk_size_mb, 5)
            self.assertEqual(settings.upload_workers, 1)
            self.assertFalse(settings.stego_chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_settings_store -v
```

Expected: FAIL，错误包含 `AppSettings` 没有 `chunk_size_mb`。

- [ ] **Step 3: 修改 `settings_store.py`**

将 `AppSettings` 和 `load_settings` 改为支持分片配置：

```python
@dataclass
class AppSettings:
    default_cover_path: str = ""
    chunk_size_mb: int = 50
    upload_workers: int = 3
    stego_chunks: bool = True


def _normalize_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
```

`load_settings` 返回：

```python
        return AppSettings(
            default_cover_path=str(data.get("default_cover_path") or ""),
            chunk_size_mb=_normalize_int(data.get("chunk_size_mb"), 50, 5, 200),
            upload_workers=_normalize_int(data.get("upload_workers"), 3, 1, 8),
            stego_chunks=bool(data.get("stego_chunks", True)),
        )
```

- [ ] **Step 4: 运行设置测试**

Run:

```powershell
python -m unittest tests.test_settings_store -v
```

Expected: 设置测试全部 PASS。

---

### Task 2: 实现分片管理模块

**Files:**
- Create: `chunk_manager.py`
- Create: `tests/test_chunk_manager.py`

- [ ] **Step 1: 写测试**

创建 `tests/test_chunk_manager.py`：

```python
import tempfile
import unittest
from pathlib import Path

from chunk_manager import cleanup_dir, merge_chunks, sha256_file, split_file


class ChunkManagerTest(unittest.TestCase):
    def test_split_and_merge_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            chunks_dir = root / "chunks"
            merged = root / "merged.bin"
            source.write_bytes(bytes(range(256)) * 10)

            chunks = split_file(source, chunks_dir, 300)
            merge_chunks([chunk.path for chunk in chunks], merged, sha256_file(source))

            self.assertEqual(len(chunks), 9)
            self.assertEqual(merged.read_bytes(), source.read_bytes())
            self.assertEqual(chunks[0].index, 0)
            self.assertEqual(chunks[-1].size, 160)

    def test_merge_rejects_bad_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            chunks_dir = root / "chunks"
            merged = root / "merged.bin"
            source.write_bytes(b"abcdef")
            chunks = split_file(source, chunks_dir, 2)

            with self.assertRaises(ValueError) as context:
                merge_chunks([chunk.path for chunk in chunks], merged, "0" * 64)

            self.assertIn("原文件 SHA-256 校验失败", str(context.exception))

    def test_cleanup_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.mkdir()
            (target / "a.txt").write_text("a", encoding="utf-8")

            cleanup_dir(target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_chunk_manager -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'chunk_manager'`。

- [ ] **Step 3: 创建 `chunk_manager.py`**

```python
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
```

- [ ] **Step 4: 运行分片测试**

Run:

```powershell
python -m unittest tests.test_chunk_manager -v
```

Expected: 3 个测试 PASS。

---

### Task 3: 实现 manifest 模块

**Files:**
- Create: `manifest_codec.py`
- Create: `tests/test_manifest_codec.py`

- [ ] **Step 1: 写测试**

创建 `tests/test_manifest_codec.py`：

```python
import tempfile
import unittest
from pathlib import Path

from manifest_codec import ChunkManifest, ChunkRecord, load_manifest_file, save_manifest_file, validate_manifest


class ManifestCodecTest(unittest.TestCase):
    def test_save_load_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "xfb_manifest.json"
            manifest = ChunkManifest(
                name="big.zip",
                size=10,
                sha256="a" * 64,
                chunk_size=5,
                chunks=[
                    ChunkRecord(index=0, size=5, sha256="b" * 64, url="https://example.com/0", stego=True),
                    ChunkRecord(index=1, size=5, sha256="c" * 64, url="https://example.com/1", stego=False),
                ],
            )

            save_manifest_file(manifest, path)
            loaded = load_manifest_file(path)

            self.assertEqual(loaded.name, "big.zip")
            self.assertEqual(len(loaded.chunks), 2)
            self.assertTrue(loaded.chunks[0].stego)
            self.assertFalse(loaded.chunks[1].stego)

    def test_rejects_bad_chunk_count(self):
        manifest = ChunkManifest(name="x", size=1, sha256="a" * 64, chunk_size=1, chunks=[])
        data = manifest.to_dict()
        data["chunk_count"] = 2

        with self.assertRaises(ValueError) as context:
            validate_manifest(data)

        self.assertIn("分片数量不一致", str(context.exception))

    def test_rejects_non_continuous_index(self):
        manifest = ChunkManifest(
            name="x",
            size=2,
            sha256="a" * 64,
            chunk_size=1,
            chunks=[ChunkRecord(index=1, size=1, sha256="b" * 64, url="https://example.com/1", stego=True)],
        )

        with self.assertRaises(ValueError) as context:
            validate_manifest(manifest.to_dict())

        self.assertIn("分片序号不连续", str(context.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_manifest_codec -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'manifest_codec'`。

- [ ] **Step 3: 创建 `manifest_codec.py`**

实现 `ChunkRecord`、`ChunkManifest`、`save_manifest_file`、`load_manifest_file`、`validate_manifest`。

- [ ] **Step 4: 运行 manifest 测试**

Run:

```powershell
python -m unittest tests.test_manifest_codec -v
```

Expected: 3 个测试 PASS。

---

### Task 4: 增强 OSS 重试能力

**Files:**
- Modify: `oss_client.py`
- Create: `tests/test_oss_client.py`

- [ ] **Step 1: 写重试错误识别测试**

创建 `tests/test_oss_client.py`：

```python
import socket
import unittest
from urllib import error

from oss_client import is_retryable_upload_error


class OssClientTest(unittest.TestCase):
    def test_10054_is_retryable(self):
        self.assertTrue(is_retryable_upload_error(ConnectionResetError(10054, "reset")))

    def test_timeout_is_retryable(self):
        self.assertTrue(is_retryable_upload_error(TimeoutError("timeout")))
        self.assertTrue(is_retryable_upload_error(socket.timeout("timeout")))

    def test_http_500_is_retryable(self):
        exc = error.HTTPError("https://example.com", 500, "server", {}, None)
        self.assertTrue(is_retryable_upload_error(exc))

    def test_http_400_is_not_retryable(self):
        exc = error.HTTPError("https://example.com", 400, "bad", {}, None)
        self.assertFalse(is_retryable_upload_error(exc))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_oss_client -v
```

Expected: FAIL，错误包含 `cannot import name 'is_retryable_upload_error'`。

- [ ] **Step 3: 修改 `oss_client.py`**

新增：

```python
import socket
import time
```

新增函数：

```python
def is_retryable_upload_error(exc):
    if isinstance(exc, error.HTTPError):
        return 500 <= exc.code <= 599
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError)):
        return True
    text = str(exc)
    retryable_tokens = ["10054", "timed out", "timeout", "Connection reset", "远程主机强迫关闭"]
    return any(token in text for token in retryable_tokens)


def upload_file_with_retry(file_path, retries=3):
    last_error = None
    for attempt in range(retries):
        try:
            return upload_file(file_path)
        except Exception as exc:
            last_error = exc
            if not is_retryable_upload_error(exc) or attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise last_error
```

- [ ] **Step 4: 运行 OSS 测试**

Run:

```powershell
python -m unittest tests.test_oss_client -v
```

Expected: 4 个测试 PASS。

---

### Task 5: 接入分片上传 worker

**Files:**
- Modify: `image_uploader_gui.py`

- [ ] **Step 1: 增加导入**

在 `image_uploader_gui.py` 增加：

```python
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from chunk_manager import cleanup_dir, merge_chunks, sha256_file, split_file
from manifest_codec import ChunkManifest, ChunkRecord, load_manifest_file, save_manifest_file
from oss_client import download_file, upload_file_with_retry
```

保留现有 `upload_file` 导入用于小文件流程，或将小文件也改为 `upload_file_with_retry`。

- [ ] **Step 2: 增加设置变量**

在 `__init__` 增加：

```python
        self.chunk_size_var = tk.StringVar(value=str(self.settings.chunk_size_mb))
        self.upload_workers_var = tk.StringVar(value=str(self.settings.upload_workers))
        self.stego_chunks_var = tk.BooleanVar(value=self.settings.stego_chunks)
```

- [ ] **Step 3: 上传 Tab 增加设置区域**

在封面区域之后增加分片设置区域，包含分片大小、并发数、分片伪装、保存设置、探测最大分片大小按钮。

- [ ] **Step 4: 增加保存设置方法**

新增 `save_chunk_settings`，读取 UI 中分片大小和并发数，归一化后保存到 `settings_store`。

- [ ] **Step 5: 修改上传选择逻辑**

在 `upload_worker` 中根据 `item.size > self.settings.chunk_size_mb * 1024 * 1024` 选择：

- 小文件：当前单文件流程。
- 大文件：调用 `upload_chunked_item`。

- [ ] **Step 6: 新增 `upload_chunked_item`**

实现：切分文件、可选伪装分片、并发上传分片、生成 manifest、伪装并上传索引图片、返回 URL 和分享文本。

- [ ] **Step 7: 运行语法检查和测试**

Run:

```powershell
python -m py_compile image_uploader_gui.py chunk_manager.py manifest_codec.py oss_client.py settings_store.py
python -m unittest discover -s tests -v
```

Expected: 语法检查通过，所有测试 PASS。

---

### Task 6: 接入分片解析下载

**Files:**
- Modify: `image_uploader_gui.py`

- [ ] **Step 1: 修改 `extract_worker`**

下载分享文本指向的图片后先提取到临时文件。如果提取文件名为 `xfb_manifest.json`，进入分片下载合并；否则按普通文件保存。

- [ ] **Step 2: 新增 `download_chunked_manifest`**

实现：加载 manifest、并发下载分片、按 `stego` 字段决定是否提取分片、校验每个分片、合并文件、校验原文件。

- [ ] **Step 3: 状态提示**

下载分片时通过 `result_queue` 更新状态：`下载分片 3/11`、`合并文件`、`校验完成`。

- [ ] **Step 4: 运行语法检查和测试**

Run:

```powershell
python -m py_compile image_uploader_gui.py chunk_manager.py manifest_codec.py oss_client.py settings_store.py
python -m unittest discover -s tests -v
```

Expected: 语法检查通过，所有测试 PASS。

---

### Task 7: 探测最大分片大小

**Files:**
- Modify: `image_uploader_gui.py`

- [ ] **Step 1: 实现探测按钮方法**

新增 `start_probe_chunk_size` 和 `probe_chunk_size_worker`。

- [ ] **Step 2: 探测规则**

从当前配置大小开始，生成临时随机文件，调用 `upload_file_with_retry` 测试上传。成功则乘以 1.5，最高 200MB；失败则减半，最低 5MB。

- [ ] **Step 3: 探测结果提示**

探测完成后提示建议大小，并询问是否保存到设置。

- [ ] **Step 4: 运行语法检查和测试**

Run:

```powershell
python -m py_compile image_uploader_gui.py chunk_manager.py manifest_codec.py oss_client.py settings_store.py
python -m unittest discover -s tests -v
```

Expected: 语法检查通过，所有测试 PASS。

---

### Task 8: 最终验证

**Files:**
- All changed files

- [ ] **Step 1: 运行语法检查**

Run:

```powershell
python -m py_compile image_uploader_gui.py oss_client.py share_codec.py stego_container.py settings_store.py chunk_manager.py manifest_codec.py
```

Expected: 退出码为 0。

- [ ] **Step 2: 运行全量测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 所有测试 PASS。

- [ ] **Step 3: 检查 Git 状态**

Run:

```powershell
git status --short
```

Expected: 显示新增和修改的源码、测试、设计和计划文档。

- [ ] **Step 4: 汇报结果**

向用户说明：分片大小可自定义，默认 50MB；并发数可配置；大文件自动分片；分片链接写入 manifest 并藏入索引图片；解析时可下载合并。

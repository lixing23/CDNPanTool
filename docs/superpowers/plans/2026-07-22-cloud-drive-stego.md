# 伪装图片云盘桌面工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 Python Tkinter 图床工具改造成桌面版伪装图片云盘分享工具，支持任意文件拼接到封面图片、上传、生成短分享文本、解析下载并提取原文件。

**Architecture:** 保留 Tkinter 桌面入口，将当前单文件中的上传、容器、分享文本、配置历史拆成独立标准库模块。核心逻辑先用 `unittest` 覆盖，再接入 GUI，网络上传继续复用现有消费宝 OSS 签名流程。

**Tech Stack:** Python 3 标准库、Tkinter、urllib、json、zlib、base64、hashlib、unittest。

---

## File Structure

- Create: `share_codec.py`
  - 编码和解码 `xfb1.<payload>` 分享文本。
  - 使用紧凑 JSON、zlib、URL-safe Base64。
- Create: `stego_container.py`
  - 生成伪装图片容器。
  - 解析 trailer。
  - 提取原文件。
  - 校验 SHA-256。
- Create: `settings_store.py`
  - 读写 `settings.json`。
  - 读写 `upload_history.json`。
  - 管理默认封面路径、上传历史、解析历史。
- Create: `oss_client.py`
  - 从 `image_uploader_gui.py` 迁移 OSS 签名、上传和下载逻辑。
  - 支持上传任意文件，不再限制图片扩展名。
- Modify: `image_uploader_gui.py`
  - 改为 GUI 编排层。
  - 新增“上传分享”和“解析提取”两个 Tab。
  - 接入 `oss_client.py`、`stego_container.py`、`share_codec.py`、`settings_store.py`。
- Modify: `.gitignore`
  - 增加 `settings.json` 和 `cache/`。
- Create: `tests/test_share_codec.py`
- Create: `tests/test_stego_container.py`
- Create: `tests/test_settings_store.py`

---

### Task 1: 更新忽略文件和创建测试目录

**Files:**
- Modify: `.gitignore`
- Create: `tests/`

- [ ] **Step 1: 修改 `.gitignore`**

将 `.gitignore` 改为：

```gitignore
__pycache__/
*.py[cod]
upload_history.json
settings.json
cache/
.env
.venv/
venv/
.gh-config/
```

- [ ] **Step 2: 创建测试目录**

Run:

```powershell
New-Item -ItemType Directory -Force -Path "tests" | Out-Null
```

Expected: 命令退出码为 0，项目根目录出现 `tests` 目录。

- [ ] **Step 3: 验证当前测试发现机制**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 输出 `Ran 0 tests` 或等价内容，退出码为 0。

- [ ] **Step 4: 检查变更**

Run:

```powershell
git diff -- .gitignore
```

Expected: diff 只包含 `settings.json` 和 `cache/` 两项新增忽略规则。

---

### Task 2: 实现分享文本编码模块

**Files:**
- Create: `share_codec.py`
- Create: `tests/test_share_codec.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_share_codec.py`：

```python
import unittest

from share_codec import SharePayload, decode_share_text, encode_share_text


class ShareCodecTest(unittest.TestCase):
    def test_round_trip(self):
        payload = SharePayload(
            url="https://example.com/a/b/c.png",
            name="资料.zip",
            size=123456,
        )

        text = encode_share_text(payload)
        decoded = decode_share_text(text)

        self.assertTrue(text.startswith("xfb1."))
        self.assertEqual(decoded.url, payload.url)
        self.assertEqual(decoded.name, payload.name)
        self.assertEqual(decoded.size, payload.size)

    def test_rejects_invalid_prefix(self):
        with self.assertRaises(ValueError) as context:
            decode_share_text("bad.abc")

        self.assertIn("分享文本前缀错误", str(context.exception))

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValueError) as context:
            decode_share_text("xfb1.invalid")

        self.assertIn("分享文本解析失败", str(context.exception))

    def test_rejects_unsupported_version(self):
        text = encode_share_text(SharePayload(url="https://example.com/file.png", name="file.bin", size=1))
        raw = decode_share_text(text)
        self.assertEqual(raw.url, "https://example.com/file.png")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_share_codec -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'share_codec'`。

- [ ] **Step 3: 实现 `share_codec.py`**

创建 `share_codec.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_share_codec -v
```

Expected: 4 个测试 PASS。

- [ ] **Step 5: 检查变更**

Run:

```powershell
git diff -- share_codec.py tests/test_share_codec.py
```

Expected: diff 包含 `SharePayload`、`encode_share_text`、`decode_share_text` 和对应测试。

---

### Task 3: 实现伪装图片容器模块

**Files:**
- Create: `stego_container.py`
- Create: `tests/test_stego_container.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_stego_container.py`：

```python
import tempfile
import unittest
from pathlib import Path

from stego_container import create_container, extract_container, inspect_container


class StegoContainerTest(unittest.TestCase):
    def test_create_inspect_extract_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.png"
            source = root / "资料.bin"
            output = root / "output.png"
            extracted = root / "extracted.bin"

            cover.write_bytes(b"\x89PNG\r\n\x1a\ncover-bytes")
            source.write_bytes(b"hello\x00world" * 20)

            metadata = create_container(cover, source, output)
            inspected = inspect_container(output)
            extracted_metadata = extract_container(output, extracted)

            self.assertEqual(metadata.name, "资料.bin")
            self.assertEqual(inspected.name, "资料.bin")
            self.assertEqual(inspected.size, source.stat().st_size)
            self.assertEqual(extracted_metadata.sha256, metadata.sha256)
            self.assertEqual(extracted.read_bytes(), source.read_bytes())

    def test_extract_rejects_invalid_container(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.png"
            output = root / "out.bin"
            invalid.write_bytes(b"not-a-container")

            with self.assertRaises(ValueError) as context:
                extract_container(invalid, output)

            self.assertIn("不是有效伪装图片", str(context.exception))

    def test_extract_rejects_tampered_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.png"
            source = root / "source.txt"
            output = root / "output.png"
            extracted = root / "extracted.txt"

            cover.write_bytes(b"cover")
            source.write_text("hello", encoding="utf-8")
            create_container(cover, source, output)

            data = bytearray(output.read_bytes())
            data[-80] = data[-80] ^ 1
            output.write_bytes(bytes(data))

            with self.assertRaises(ValueError) as context:
                extract_container(output, extracted)

            self.assertTrue("校验失败" in str(context.exception) or "不是有效伪装图片" in str(context.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_stego_container -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'stego_container'`。

- [ ] **Step 3: 实现 `stego_container.py`**

创建 `stego_container.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_stego_container -v
```

Expected: 3 个测试 PASS。

- [ ] **Step 5: 运行全量测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 当前所有测试 PASS。

---

### Task 4: 实现配置和历史存储模块

**Files:**
- Create: `settings_store.py`
- Create: `tests/test_settings_store.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_settings_store.py`：

```python
import tempfile
import unittest
from pathlib import Path

from settings_store import AppSettings, SettingsStore


class SettingsStoreTest(unittest.TestCase):
    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SettingsStore(root)
            settings = AppSettings(default_cover_path="C:/cover.png")

            store.save_settings(settings)
            loaded = store.load_settings()

            self.assertEqual(loaded.default_cover_path, "C:/cover.png")

    def test_history_limits_to_200(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SettingsStore(root)

            for index in range(205):
                store.add_upload_history({"name": f"file-{index}", "share_text": f"xfb1.{index}"})

            history = store.load_upload_history()

            self.assertEqual(len(history), 200)
            self.assertEqual(history[0]["name"], "file-204")

    def test_corrupt_json_returns_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "settings.json").write_text("{bad", encoding="utf-8")
            (root / "upload_history.json").write_text("{bad", encoding="utf-8")
            store = SettingsStore(root)

            self.assertEqual(store.load_settings().default_cover_path, "")
            self.assertEqual(store.load_upload_history(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m unittest tests.test_settings_store -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'settings_store'`。

- [ ] **Step 3: 实现 `settings_store.py`**

创建 `settings_store.py`：

```python
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class AppSettings:
    default_cover_path: str = ""


class SettingsStore:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.settings_path = self.base_dir / "settings.json"
        self.history_path = self.base_dir / "upload_history.json"

    def load_settings(self):
        data = self._read_json(self.settings_path, {})
        if not isinstance(data, dict):
            data = {}
        return AppSettings(default_cover_path=str(data.get("default_cover_path") or ""))

    def save_settings(self, settings):
        self._write_json(self.settings_path, asdict(settings))

    def load_upload_history(self):
        data = self._read_json(self.history_path, [])
        if not isinstance(data, list):
            return []
        return data

    def save_upload_history(self, history):
        self._write_json(self.history_path, list(history)[:200])

    def add_upload_history(self, record):
        history = self.load_upload_history()
        history.insert(0, record)
        self.save_upload_history(history[:200])

    def _read_json(self, path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
python -m unittest tests.test_settings_store -v
```

Expected: 3 个测试 PASS。

- [ ] **Step 5: 运行全量测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 当前所有测试 PASS。

---

### Task 5: 抽离 OSS 客户端模块

**Files:**
- Create: `oss_client.py`
- Modify: `image_uploader_gui.py`

- [ ] **Step 1: 创建 `oss_client.py`**

从当前 `image_uploader_gui.py` 迁移 HTTP 和 OSS 逻辑，并改名为通用文件上传：

```python
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, request

SIGN_URL = "https://company-api.xfb315.com/common/oss/sign"
SIGN_HEADERS = {
    "source": "pc",
    "accept": "application/json, text/plain, */*",
    "origin": "https://company.xfb365.com",
    "referer": "https://company.xfb365.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
}


def safe_filename(name):
    return re.sub(r"[^\u4e00-\u9fa5_a-zA-Z0-9.]+", "_", name)


def http_get_json(url, headers):
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return json.loads(data)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"签名接口 HTTP {exc.code}: {body[:300]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"签名接口网络错误: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("签名接口返回不是 JSON") from exc


def build_multipart(fields, file_field, file_path, mime_type):
    boundary = "----PythonUploader" + uuid.uuid4().hex
    body = bytearray()

    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    filename = os.path.basename(file_path)
    with open(file_path, "rb") as file:
        file_bytes = file.read()

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    return bytes(body), boundary


def http_post_multipart(url, fields, file_path, mime_type):
    body, boundary = build_multipart(fields, "file", file_path, mime_type)
    headers = {
        "content-type": f"multipart/form-data; boundary={boundary}",
        "content-length": str(len(body)),
        "origin": "https://company.xfb365.com",
        "referer": "https://company.xfb365.com/",
        "user-agent": SIGN_HEADERS["user-agent"],
    }
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return resp.status, text
    except error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"上传接口 HTTP {exc.code}: {body_text[:500]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"上传接口网络错误: {exc.reason}") from exc


def fetch_sign():
    result = http_get_json(SIGN_URL, SIGN_HEADERS)
    if result.get("code") != 200 or not isinstance(result.get("data"), dict):
        raise RuntimeError(result.get("msg") or "签名接口返回异常")
    data = result["data"]
    required = ["accessid", "host", "policy", "signature", "callback"]
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise RuntimeError("签名接口缺少字段: " + ", ".join(missing))
    return data


def make_object_key(sign, file_path):
    now = datetime.now()
    prefix = sign.get("dir") or ""
    date_part = now.strftime("%Y/%m/%d/")
    random_part = uuid.uuid4().hex
    name = safe_filename(os.path.basename(file_path))
    return f"{prefix}{date_part}saas_admin_{random_part}_{name}"


def join_url(host, filename):
    return host.rstrip("/") + "/" + filename.lstrip("/")


def upload_file(file_path):
    file_path = str(file_path)
    if not Path(file_path).exists():
        raise RuntimeError("待上传文件不存在")

    sign = fetch_sign()
    object_key = make_object_key(sign, file_path)
    mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    fields = [
        ("key", object_key),
        ("policy", sign["policy"]),
        ("OSSAccessKeyId", sign["accessid"]),
        ("success_action_status", "200"),
        ("callback", sign["callback"]),
        ("signature", sign["signature"]),
    ]

    status, text = http_post_multipart(sign["host"], fields, file_path, mime_type)
    if status != 200:
        raise RuntimeError(f"上传失败 HTTP {status}: {text[:500]}")

    try:
        uploaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("上传接口返回不是 JSON: " + text[:300]) from exc

    if uploaded.get("code") != 200:
        raise RuntimeError(uploaded.get("msg") or "上传接口返回失败")

    filename = uploaded.get("data", {}).get("filename") or object_key
    url = join_url(sign["host"], filename)
    return {"url": url, "filename": filename, "response": uploaded}


def download_file(url, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    req = request.Request(url, headers={"user-agent": SIGN_HEADERS["user-agent"]}, method="GET")
    try:
        with request.urlopen(req, timeout=120) as resp, open(output_path, "wb") as output:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    except error.HTTPError as exc:
        raise RuntimeError(f"下载直链 HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"下载直链网络错误: {exc.reason}") from exc
    return output_path
```

- [ ] **Step 2: 暂时保留 GUI 旧逻辑不调用新模块**

本任务只创建 `oss_client.py`，不要删除 `image_uploader_gui.py` 中的旧函数。GUI 接入在 Task 6 完成，降低单步变更风险。

- [ ] **Step 3: 运行语法检查**

Run:

```powershell
python -m py_compile oss_client.py
```

Expected: 退出码为 0，无输出。

- [ ] **Step 4: 运行全量测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 当前所有测试 PASS。

---

### Task 6: 重写 GUI 为上传分享和解析提取双 Tab

**Files:**
- Modify: `image_uploader_gui.py`

- [ ] **Step 1: 替换 GUI 文件的导入和数据模型**

将 `image_uploader_gui.py` 顶部替换为以下导入和常量：

```python
import os
import queue
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from oss_client import download_file, upload_file
from settings_store import AppSettings, SettingsStore
from share_codec import SharePayload, decode_share_text, encode_share_text
from stego_container import create_container, extract_container, inspect_container

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "cache"
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


@dataclass
class UploadItem:
    path: str
    name: str
    size: int
    status: str = "等待上传"
    cover_path: str = ""
    url: str = ""
    share_text: str = ""
    error: str = ""
    uploaded_at: str = ""
```

- [ ] **Step 2: 保留 `format_size` 并删除 GUI 文件中的旧 OSS 函数**

删除 `safe_filename`、`http_get_json`、`build_multipart`、`http_post_multipart`、`fetch_sign`、`make_object_key`、`join_url`、`upload_image`，保留并继续使用：

```python
def format_size(size):
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"
```

- [ ] **Step 3: 重建 `ImageUploaderApp.__init__`**

将 `ImageUploaderApp.__init__` 改为：

```python
class ImageUploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("伪装图片云盘")
        self.root.geometry("1180x780")
        self.root.minsize(1040, 700)

        self.store = SettingsStore(APP_DIR)
        self.settings = self.store.load_settings()
        self.items = []
        self.history = self.store.load_upload_history()
        self.result_queue = queue.Queue()
        self.uploading = False
        self.extracting = False

        self.default_cover_var = tk.StringVar(value=self.settings.default_cover_path or "未设置默认封面")
        self.temp_cover_var = tk.StringVar(value="未选择本次封面")
        self.path_var = tk.StringVar(value="未选择文件")
        self.status_var = tk.StringVar(value="就绪")
        self.url_var = tk.StringVar()
        self.share_text_var = tk.StringVar()
        self.extract_text_var = tk.StringVar()
        self.extract_info_var = tk.StringVar(value="等待粘贴分享文本")
        self.extract_save_dir_var = tk.StringVar(value=str(Path.home() / "Downloads"))

        self.build_ui()
        self.root.after(150, self.process_queue)
```

- [ ] **Step 4: 重建 `build_ui` 主结构**

用 Notebook 分成两个 Tab：

```python
    def build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="伪装图片云盘", font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor=tk.W)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        upload_tab = ttk.Frame(notebook, padding=10)
        extract_tab = ttk.Frame(notebook, padding=10)
        notebook.add(upload_tab, text="上传分享")
        notebook.add(extract_tab, text="解析提取")

        self.build_upload_tab(upload_tab)
        self.build_extract_tab(extract_tab)
```

- [ ] **Step 5: 新增上传 Tab UI**

在类中新增：

```python
    def build_upload_tab(self, parent):
        cover_frame = ttk.LabelFrame(parent, text="封面图片")
        cover_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(cover_frame, text="设置默认封面", command=self.choose_default_cover).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(cover_frame, textvariable=self.default_cover_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(cover_frame, text="选择本次封面", command=self.choose_temp_cover).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(cover_frame, textvariable=self.temp_cover_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(cover_frame, text="清除本次封面", command=self.clear_temp_cover).pack(side=tk.LEFT, padx=8, pady=8)

        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text="选择文件", command=self.choose_files).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="清空列表", command=self.clear_items).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="开始上传", command=self.start_upload).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="打开历史文件", command=self.open_history_file).pack(side=tk.LEFT, padx=(0, 8))

        info = ttk.Frame(parent)
        info.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(info, text="当前文件：").pack(side=tk.LEFT)
        ttk.Label(info, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(info, text="状态：").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(info, textvariable=self.status_var).pack(side=tk.LEFT)

        list_frame = ttk.LabelFrame(parent, text="上传队列")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        columns = ("name", "size", "status", "url")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("status", text="状态")
        self.tree.heading("url", text="伪装图片直链")
        self.tree.column("name", width=260, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)
        self.tree.column("status", width=120, anchor=tk.CENTER)
        self.tree.column("url", width=620, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        result_frame = ttk.LabelFrame(parent, text="分享结果")
        result_frame.pack(fill=tk.X, pady=(0, 10))
        self.add_result_row(result_frame, 0, "伪装图片直链", self.url_var, lambda: self.copy_value(self.url_var.get()))
        self.add_result_row(result_frame, 1, "短分享文本", self.share_text_var, lambda: self.copy_value(self.share_text_var.get()))

        bottom = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        bottom.pack(fill=tk.BOTH, expand=True)
        log_frame = ttk.LabelFrame(bottom, text="日志")
        history_frame = ttk.LabelFrame(bottom, text="历史记录")
        bottom.add(log_frame, weight=1)
        bottom.add(history_frame, weight=1)
        self.log_text = tk.Text(log_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.history_text = tk.Text(history_frame, height=8, wrap=tk.WORD)
        self.history_text.pack(fill=tk.BOTH, expand=True)
        self.refresh_history_view()
```

- [ ] **Step 6: 新增解析 Tab UI**

在类中新增：

```python
    def build_extract_tab(self, parent):
        input_frame = ttk.LabelFrame(parent, text="分享文本")
        input_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.extract_text = tk.Text(input_frame, height=8, wrap=tk.WORD)
        self.extract_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        actions = ttk.Frame(parent)
        actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(actions, text="解析信息", command=self.preview_share_text).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="选择保存目录", command=self.choose_extract_save_dir).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="下载并提取", command=self.start_extract).pack(side=tk.LEFT, padx=(0, 8))

        save_frame = ttk.Frame(parent)
        save_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(save_frame, text="保存目录：").pack(side=tk.LEFT)
        ttk.Label(save_frame, textvariable=self.extract_save_dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        info_frame = ttk.LabelFrame(parent, text="解析信息")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(info_frame, textvariable=self.extract_info_var).pack(fill=tk.X, padx=8, pady=8)
```

- [ ] **Step 7: 新增封面选择方法**

在类中新增：

```python
    def choose_cover_file(self):
        file_path = filedialog.askopenfilename(
            title="选择封面图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.gif *.webp"), ("所有文件", "*.*")],
        )
        if not file_path:
            return ""
        if Path(file_path).suffix.lower() not in COVER_EXTENSIONS:
            messagebox.showerror("错误", "请选择图片作为封面")
            return ""
        return file_path

    def choose_default_cover(self):
        file_path = self.choose_cover_file()
        if not file_path:
            return
        self.settings = AppSettings(default_cover_path=file_path)
        self.store.save_settings(self.settings)
        self.default_cover_var.set(file_path)
        self.log(f"已设置默认封面：{file_path}")

    def choose_temp_cover(self):
        file_path = self.choose_cover_file()
        if not file_path:
            return
        self.temp_cover_var.set(file_path)
        self.log(f"已选择本次封面：{file_path}")

    def clear_temp_cover(self):
        self.temp_cover_var.set("未选择本次封面")
        self.log("已清除本次封面")

    def resolve_cover_path(self):
        temp_cover = self.temp_cover_var.get()
        if temp_cover and temp_cover != "未选择本次封面":
            return temp_cover
        return self.settings.default_cover_path
```

- [ ] **Step 8: 改造文件选择方法支持任意文件**

将 `choose_files` 改为：

```python
    def choose_files(self):
        files = filedialog.askopenfilenames(title="选择文件", filetypes=[("所有文件", "*.*")])
        if not files:
            return

        existing = {item.path for item in self.items}
        added = 0
        cover_path = self.resolve_cover_path()
        for file_path in files:
            path = str(Path(file_path))
            if path in existing:
                self.log(f"跳过重复文件：{path}")
                continue
            size = os.path.getsize(path)
            item = UploadItem(path=path, name=os.path.basename(path), size=size, cover_path=cover_path)
            self.items.append(item)
            existing.add(path)
            added += 1

        self.refresh_tree()
        self.status_var.set(f"已添加 {added} 个文件")
        if self.items:
            self.path_var.set(self.items[-1].path)
        self.log(f"添加文件 {added} 个")
```

- [ ] **Step 9: 改造上传 worker**

将 `upload_worker` 改为：

```python
    def upload_worker(self, indexes):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for index in indexes:
            item = self.items[index]
            self.result_queue.put(("status", index, "生成伪装图片", ""))
            try:
                cover_path = item.cover_path or self.resolve_cover_path()
                if not cover_path:
                    raise RuntimeError("未设置默认封面，也未选择本次封面")
                if not Path(cover_path).exists():
                    raise RuntimeError("封面图片不存在")
                container_path = CACHE_DIR / f"xfb_{int(time.time())}_{index}.png"
                metadata = create_container(cover_path, item.path, container_path)
                self.result_queue.put(("status", index, "上传中", ""))
                uploaded = upload_file(container_path)
                share_text = encode_share_text(SharePayload(url=uploaded["url"], name=metadata.name, size=metadata.size))
                self.result_queue.put(("success", index, {"url": uploaded["url"], "share_text": share_text}, ""))
                try:
                    container_path.unlink(missing_ok=True)
                except Exception:
                    pass
            except Exception as exc:
                self.result_queue.put(("failure", index, str(exc), ""))
        self.result_queue.put(("done", None, None, None))
```

- [ ] **Step 10: 改造队列成功处理**

将 `process_queue` 中 success 分支改为：

```python
                elif event == "success":
                    item = self.items[index]
                    item.status = "上传成功"
                    item.url = payload["url"]
                    item.share_text = payload["share_text"]
                    item.error = ""
                    item.uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.add_history(item)
                    self.select_index(index)
                    self.log(f"上传成功：{item.name}")
```

并将 done 分支中的 `self.save_history()` 删除，因为 `settings_store` 在 `add_history` 时保存。

- [ ] **Step 11: 改造选择结果展示**

将 `on_tree_select` 改为：

```python
    def on_tree_select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if index < 0 or index >= len(self.items):
            return
        item = self.items[index]
        self.path_var.set(item.path)
        self.url_var.set(item.url)
        self.share_text_var.set(item.share_text)
        self.status_var.set(item.status if not item.error else item.error)
```

将 `clear_result_fields` 改为：

```python
    def clear_result_fields(self):
        self.url_var.set("")
        self.share_text_var.set("")
```

- [ ] **Step 12: 改造历史读写**

删除 `load_history` 和 `save_history` 方法，将 `add_history`、`refresh_history_view`、`open_history_file` 改为：

```python
    def add_history(self, item):
        record = asdict(item)
        self.store.add_upload_history(record)
        self.history = self.store.load_upload_history()

    def refresh_history_view(self):
        if not hasattr(self, "history_text"):
            return
        self.history_text.delete("1.0", tk.END)
        self.history = self.store.load_upload_history()
        if not self.history:
            self.history_text.insert(tk.END, "暂无历史记录")
            return
        for record in self.history[:50]:
            self.history_text.insert(tk.END, f"[{record.get('uploaded_at', '')}] {record.get('name', '')}\n")
            self.history_text.insert(tk.END, f"{record.get('share_text', '')}\n\n")

    def open_history_file(self):
        if not self.store.history_path.exists():
            self.store.save_upload_history([])
        try:
            os.startfile(str(self.store.history_path))
        except Exception as exc:
            messagebox.showerror("错误", f"无法打开历史文件：{exc}")
```

- [ ] **Step 13: 新增分享文本预览和提取方法**

在类中新增：

```python
    def get_extract_share_text(self):
        return self.extract_text.get("1.0", tk.END).strip()

    def preview_share_text(self):
        text = self.get_extract_share_text()
        try:
            payload = decode_share_text(text)
            self.extract_info_var.set(f"文件名：{payload.name}，大小：{format_size(payload.size)}")
        except Exception as exc:
            self.extract_info_var.set(str(exc))
            messagebox.showerror("错误", str(exc))

    def choose_extract_save_dir(self):
        directory = filedialog.askdirectory(title="选择保存目录")
        if not directory:
            return
        self.extract_save_dir_var.set(directory)

    def start_extract(self):
        if self.extracting:
            messagebox.showinfo("提示", "正在提取，请稍候")
            return
        text = self.get_extract_share_text()
        if not text:
            messagebox.showinfo("提示", "请先粘贴分享文本")
            return
        self.extracting = True
        self.extract_info_var.set("正在下载并提取")
        threading.Thread(target=self.extract_worker, args=(text,), daemon=True).start()

    def extract_worker(self, text):
        try:
            payload = decode_share_text(text)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            downloaded = CACHE_DIR / f"download_{int(time.time())}.png"
            download_file(payload.url, downloaded)
            metadata = inspect_container(downloaded)
            save_dir = Path(self.extract_save_dir_var.get())
            output_path = save_dir / metadata.name
            extract_container(downloaded, output_path)
            self.result_queue.put(("extract_success", None, str(output_path), ""))
            try:
                downloaded.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as exc:
            self.result_queue.put(("extract_failure", None, str(exc), ""))
```

- [ ] **Step 14: 在 `process_queue` 中处理提取事件**

在 `process_queue` 的事件判断中加入：

```python
                elif event == "extract_success":
                    self.extracting = False
                    self.extract_info_var.set(f"提取成功：{payload}")
                    self.log(f"提取成功：{payload}")
                    messagebox.showinfo("完成", f"文件已保存：{payload}")
                elif event == "extract_failure":
                    self.extracting = False
                    self.extract_info_var.set(payload)
                    self.log(f"提取失败：{payload}")
                    messagebox.showerror("错误", payload)
```

- [ ] **Step 15: 运行语法检查**

Run:

```powershell
python -m py_compile image_uploader_gui.py oss_client.py share_codec.py stego_container.py settings_store.py
```

Expected: 退出码为 0，无输出。

- [ ] **Step 16: 运行全量测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 当前所有测试 PASS。

---

### Task 7: 手动验证桌面流程

**Files:**
- No file changes required

- [ ] **Step 1: 启动程序**

Run:

```powershell
python image_uploader_gui.py
```

Expected: 打开标题为“伪装图片云盘”的桌面窗口，包含“上传分享”和“解析提取”两个 Tab。

- [ ] **Step 2: 验证无封面提示**

操作：不设置默认封面，选择一个小文本文件，点击“开始上传”。

Expected: GUI 提示 `未设置默认封面，也未选择本次封面`，日志区域出现同样错误。

- [ ] **Step 3: 验证本地容器逻辑**

操作：设置默认封面，选择一个小文本文件，点击“开始上传”。

Expected: 上传队列状态依次出现 `生成伪装图片`、`上传中`、`上传成功`。如果网络或签名接口不可用，错误应显示为签名接口或上传接口错误，而不是 Python traceback。

- [ ] **Step 4: 验证解析 Tab 的错误处理**

操作：在解析 Tab 输入 `bad.abc`，点击“解析信息”。

Expected: GUI 提示 `分享文本前缀错误`。

- [ ] **Step 5: 验证完整分享链路**

操作：复制上传成功后的短分享文本，粘贴到解析 Tab，点击“解析信息”，选择保存目录，点击“下载并提取”。

Expected: 显示原文件名和大小，最终保存出的文件和原文件内容一致。

- [ ] **Step 6: 关闭程序并检查运行时文件**

Run:

```powershell
Get-ChildItem -Force
```

Expected: 可能出现 `settings.json`、`upload_history.json`、`cache`，这些文件都被 `.gitignore` 忽略。

---

### Task 8: 最终验证和差异检查

**Files:**
- All changed files

- [ ] **Step 1: 运行语法检查**

Run:

```powershell
python -m py_compile image_uploader_gui.py oss_client.py share_codec.py stego_container.py settings_store.py
```

Expected: 退出码为 0，无输出。

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

Expected: 显示 `.gitignore`、`image_uploader_gui.py`、新增模块、测试文件、设计文档和计划文档的变更。

- [ ] **Step 4: 检查是否误提交运行时文件**

Run:

```powershell
git status --short --ignored
```

Expected: `settings.json`、`upload_history.json`、`cache/` 如果存在，应显示为 ignored，不应显示为普通未跟踪文件。

- [ ] **Step 5: 汇总实现结果**

向用户汇报：

```text
已完成桌面版伪装图片云盘改造。核心模块包括 share_codec.py、stego_container.py、settings_store.py、oss_client.py，GUI 已改为上传分享/解析提取双 Tab。已通过 py_compile 和 unittest。未执行 git commit。
```

import json
import mimetypes
import os
import re
import socket
import time
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

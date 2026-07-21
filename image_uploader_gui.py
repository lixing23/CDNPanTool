import json
import mimetypes
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from urllib import error, request

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SIGN_URL = "https://company-api.xfb315.com/common/oss/sign"
HISTORY_FILE = Path(__file__).with_name("upload_history.json")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
SIGN_HEADERS = {
    "source": "pc",
    "accept": "application/json, text/plain, */*",
    "origin": "https://company.xfb365.com",
    "referer": "https://company.xfb365.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
}


@dataclass
class UploadItem:
    path: str
    name: str
    size: int
    status: str = "等待上传"
    url: str = ""
    watermark_url: str = ""
    markdown: str = ""
    html: str = ""
    bbcode: str = ""
    error: str = ""
    uploaded_at: str = ""


def safe_filename(name):
    return re.sub(r"[^\u4e00-\u9fa5_a-zA-Z0-9]+", "_", name)


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
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8")
    )
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


def upload_image(file_path):
    suffix = Path(file_path).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise RuntimeError(f"不支持的图片格式: {suffix}")

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
    watermark_url = url + "?x-oss-process=style/watermark"
    basename = os.path.basename(file_path)

    return {
        "url": url,
        "watermark_url": watermark_url,
        "markdown": f"![{basename}]({url})",
        "html": f'<img src="{url}" alt="{basename}">',
        "bbcode": f"[img]{url}[/img]",
        "filename": filename,
        "response": uploaded,
    }


class ImageUploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("消费宝图床api")
        self.root.geometry("1100x760")
        self.root.minsize(980, 680)

        self.items = []
        self.history = []
        self.result_queue = queue.Queue()
        self.uploading = False

        self.path_var = tk.StringVar(value="未选择文件")
        self.status_var = tk.StringVar(value="就绪")
        self.url_var = tk.StringVar()
        self.watermark_var = tk.StringVar()
        self.markdown_var = tk.StringVar()
        self.html_var = tk.StringVar()
        self.bbcode_var = tk.StringVar()

        self.load_history()
        self.build_ui()
        self.root.after(150, self.process_queue)

    def build_ui(self):
        root = self.root
        outer = ttk.Frame(root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="消费宝图床api", font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor=tk.W)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(12, 8))

        ttk.Button(toolbar, text="选择图片", command=self.choose_files).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="移除选中", command=self.remove_selected).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="清空列表", command=self.clear_items).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="开始上传", command=self.start_upload).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(toolbar, text="打开历史文件", command=self.open_history_file).pack(side=tk.LEFT, padx=(0, 8))

        info = ttk.Frame(outer)
        info.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(info, text="当前文件：").pack(side=tk.LEFT)
        ttk.Label(info, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(info, text="状态：").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(info, textvariable=self.status_var).pack(side=tk.LEFT)

        list_frame = ttk.LabelFrame(outer, text="上传队列")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        columns = ("name", "size", "status", "url")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("status", text="状态")
        self.tree.heading("url", text="直链")
        self.tree.column("name", width=250, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)
        self.tree.column("status", width=120, anchor=tk.CENTER)
        self.tree.column("url", width=560, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        result_frame = ttk.LabelFrame(outer, text="上传结果")
        result_frame.pack(fill=tk.X, pady=(0, 10))
        self.add_result_row(result_frame, 0, "原始直链", self.url_var, lambda: self.copy_value(self.url_var.get()))
        self.add_result_row(result_frame, 1, "水印链接", self.watermark_var, lambda: self.copy_value(self.watermark_var.get()))
        self.add_result_row(result_frame, 2, "Markdown", self.markdown_var, lambda: self.copy_value(self.markdown_var.get()))
        self.add_result_row(result_frame, 3, "HTML", self.html_var, lambda: self.copy_value(self.html_var.get()))
        self.add_result_row(result_frame, 4, "BBCode", self.bbcode_var, lambda: self.copy_value(self.bbcode_var.get()))

        bottom = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
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

    def add_result_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label + "：").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(parent, text="复制", command=command).grid(row=row, column=2, sticky=tk.E, padx=8, pady=4)
        parent.columnconfigure(1, weight=1)

    def choose_files(self):
        files = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.bmp *.gif *.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return

        existing = {item.path for item in self.items}
        added = 0
        for file_path in files:
            path = str(Path(file_path))
            suffix = Path(path).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                self.log(f"跳过不支持格式：{path}")
                continue
            if path in existing:
                self.log(f"跳过重复文件：{path}")
                continue
            size = os.path.getsize(path)
            item = UploadItem(path=path, name=os.path.basename(path), size=size)
            self.items.append(item)
            existing.add(path)
            added += 1

        self.refresh_tree()
        self.status_var.set(f"已添加 {added} 个文件")
        if self.items:
            self.path_var.set(self.items[-1].path)
        self.log(f"添加文件 {added} 个")

    def refresh_tree(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for index, item in enumerate(self.items):
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(item.name, format_size(item.size), item.status, item.url),
            )

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
        self.watermark_var.set(item.watermark_url)
        self.markdown_var.set(item.markdown)
        self.html_var.set(item.html)
        self.bbcode_var.set(item.bbcode)
        self.status_var.set(item.status if not item.error else item.error)

    def remove_selected(self):
        if self.uploading:
            messagebox.showinfo("提示", "上传中不能移除文件")
            return
        selected = self.tree.selection()
        if not selected:
            return
        indexes = sorted([int(value) for value in selected], reverse=True)
        for index in indexes:
            if 0 <= index < len(self.items):
                self.items.pop(index)
        self.refresh_tree()
        self.clear_result_fields()
        self.status_var.set("已移除选中文件")

    def clear_items(self):
        if self.uploading:
            messagebox.showinfo("提示", "上传中不能清空列表")
            return
        self.items.clear()
        self.refresh_tree()
        self.clear_result_fields()
        self.path_var.set("未选择文件")
        self.status_var.set("已清空")

    def clear_result_fields(self):
        self.url_var.set("")
        self.watermark_var.set("")
        self.markdown_var.set("")
        self.html_var.set("")
        self.bbcode_var.set("")

    def start_upload(self):
        if self.uploading:
            messagebox.showinfo("提示", "正在上传，请稍候")
            return
        pending = [index for index, item in enumerate(self.items) if item.status in {"等待上传", "上传失败"}]
        if not pending:
            messagebox.showinfo("提示", "没有待上传的图片")
            return
        self.uploading = True
        self.status_var.set("正在上传")
        threading.Thread(target=self.upload_worker, args=(pending,), daemon=True).start()

    def upload_worker(self, indexes):
        for index in indexes:
            item = self.items[index]
            self.result_queue.put(("status", index, "上传中", ""))
            try:
                result = upload_image(item.path)
                self.result_queue.put(("success", index, result, ""))
            except Exception as exc:
                self.result_queue.put(("failure", index, str(exc), ""))
        self.result_queue.put(("done", None, None, None))

    def process_queue(self):
        try:
            while True:
                event, index, payload, _ = self.result_queue.get_nowait()
                if event == "status":
                    self.items[index].status = payload
                    self.items[index].error = ""
                    self.log(f"开始上传：{self.items[index].name}")
                elif event == "success":
                    item = self.items[index]
                    item.status = "上传成功"
                    item.url = payload["url"]
                    item.watermark_url = payload["watermark_url"]
                    item.markdown = payload["markdown"]
                    item.html = payload["html"]
                    item.bbcode = payload["bbcode"]
                    item.error = ""
                    item.uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.add_history(item)
                    self.select_index(index)
                    self.log(f"上传成功：{item.name} -> {item.url}")
                elif event == "failure":
                    item = self.items[index]
                    item.status = "上传失败"
                    item.error = payload
                    self.log(f"上传失败：{item.name}，{payload}")
                elif event == "done":
                    self.uploading = False
                    self.status_var.set("上传完成")
                    self.save_history()
                    self.refresh_history_view()
                    self.log("队列上传完成")
                self.refresh_tree()
        except queue.Empty:
            pass
        self.root.after(150, self.process_queue)

    def select_index(self, index):
        self.tree.selection_set(str(index))
        self.tree.focus(str(index))
        self.tree.see(str(index))
        self.on_tree_select()

    def add_history(self, item):
        record = asdict(item)
        self.history.insert(0, record)
        self.history = self.history[:200]

    def load_history(self):
        if not HISTORY_FILE.exists():
            self.history = []
            return
        try:
            self.history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if not isinstance(self.history, list):
                self.history = []
        except Exception:
            self.history = []

    def save_history(self):
        HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")

    def refresh_history_view(self):
        if not hasattr(self, "history_text"):
            return
        self.history_text.delete("1.0", tk.END)
        if not self.history:
            self.history_text.insert(tk.END, "暂无历史记录")
            return
        for record in self.history[:50]:
            self.history_text.insert(tk.END, f"[{record.get('uploaded_at', '')}] {record.get('name', '')}\n")
            self.history_text.insert(tk.END, f"{record.get('url', '')}\n\n")

    def open_history_file(self):
        if not HISTORY_FILE.exists():
            self.save_history()
        try:
            os.startfile(str(HISTORY_FILE))
        except Exception as exc:
            messagebox.showerror("错误", f"无法打开历史文件：{exc}")

    def copy_value(self, value):
        if not value:
            messagebox.showinfo("提示", "没有可复制的内容")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update()
        self.status_var.set("已复制到剪贴板")

    def log(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{stamp}] {text}\n")
        self.log_text.see(tk.END)


def main():
    root = tk.Tk()
    app = ImageUploaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

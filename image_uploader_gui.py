import os
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from chunk_manager import cleanup_dir, merge_chunks, sha256_file, split_file
from manifest_codec import ChunkManifest, ChunkRecord, load_manifest_file, save_manifest_file
from oss_client import download_file, is_retryable_upload_error, upload_file_with_retry
from settings_store import AppSettings, SettingsStore
from share_codec import SharePayload, decode_share_text, encode_share_text
from stego_container import create_container, extract_container, inspect_container

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "cache"
CHUNKS_DIR = CACHE_DIR / "chunks"
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
MANIFEST_NAME = "xfb_manifest.json"
MIN_CHUNK_MB = 5
MAX_CHUNK_MB = 200


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


def normalize_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


class ImageUploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("伪装图片云盘")
        self.root.geometry("1180x820")
        self.root.minsize(1040, 740)

        self.store = SettingsStore(APP_DIR)
        self.settings = self.store.load_settings()
        self.items = []
        self.history = self.store.load_upload_history()
        self.result_queue = queue.Queue()
        self.uploading = False
        self.extracting = False
        self.probing = False

        self.default_cover_var = tk.StringVar(value=self.settings.default_cover_path or "未设置默认封面")
        self.temp_cover_var = tk.StringVar(value="未选择本次封面")
        self.path_var = tk.StringVar(value="未选择文件")
        self.status_var = tk.StringVar(value="就绪")
        self.url_var = tk.StringVar()
        self.share_text_var = tk.StringVar()
        self.extract_info_var = tk.StringVar(value="等待粘贴分享文本")
        self.extract_save_dir_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.chunk_size_var = tk.StringVar(value=str(self.settings.chunk_size_mb))
        self.upload_workers_var = tk.StringVar(value=str(self.settings.upload_workers))
        self.stego_chunks_var = tk.BooleanVar(value=self.settings.stego_chunks)

        self.build_ui()
        self.root.after(150, self.process_queue)

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

    def build_upload_tab(self, parent):
        cover_frame = ttk.LabelFrame(parent, text="封面图片")
        cover_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(cover_frame, text="设置默认封面", command=self.choose_default_cover).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(cover_frame, textvariable=self.default_cover_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(cover_frame, text="选择本次封面", command=self.choose_temp_cover).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(cover_frame, textvariable=self.temp_cover_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(cover_frame, text="清除本次封面", command=self.clear_temp_cover).pack(side=tk.LEFT, padx=8, pady=8)

        chunk_frame = ttk.LabelFrame(parent, text="分片上传设置")
        chunk_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(chunk_frame, text="分片大小 MB").pack(side=tk.LEFT, padx=(8, 4), pady=8)
        ttk.Entry(chunk_frame, width=8, textvariable=self.chunk_size_var).pack(side=tk.LEFT, padx=(0, 12), pady=8)
        ttk.Label(chunk_frame, text="上传并发数").pack(side=tk.LEFT, padx=(0, 4), pady=8)
        ttk.Entry(chunk_frame, width=8, textvariable=self.upload_workers_var).pack(side=tk.LEFT, padx=(0, 12), pady=8)
        ttk.Checkbutton(chunk_frame, text="分片也伪装成图片", variable=self.stego_chunks_var).pack(side=tk.LEFT, padx=(0, 12), pady=8)
        ttk.Button(chunk_frame, text="保存设置", command=self.save_chunk_settings).pack(side=tk.LEFT, padx=(0, 8), pady=8)
        ttk.Button(chunk_frame, text="探测最大分片大小", command=self.start_probe_chunk_size).pack(side=tk.LEFT, padx=(0, 8), pady=8)

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
        self.tree.column("status", width=150, anchor=tk.CENTER)
        self.tree.column("url", width=590, anchor=tk.W)
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

    def add_result_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label + "：").grid(row=row, column=0, sticky=tk.W, padx=8, pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=4, pady=4)
        ttk.Button(parent, text="复制", command=command).grid(row=row, column=2, sticky=tk.E, padx=8, pady=4)
        parent.columnconfigure(1, weight=1)

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
        self.settings.default_cover_path = file_path
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

    def save_chunk_settings(self):
        self.settings.chunk_size_mb = normalize_int(self.chunk_size_var.get(), 50, MIN_CHUNK_MB, MAX_CHUNK_MB)
        self.settings.upload_workers = normalize_int(self.upload_workers_var.get(), 3, 1, 8)
        self.settings.stego_chunks = bool(self.stego_chunks_var.get())
        self.chunk_size_var.set(str(self.settings.chunk_size_mb))
        self.upload_workers_var.set(str(self.settings.upload_workers))
        self.store.save_settings(self.settings)
        self.log(f"已保存分片设置：{self.settings.chunk_size_mb}MB，并发 {self.settings.upload_workers}，分片伪装 {self.settings.stego_chunks}")

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
        self.share_text_var.set(item.share_text)
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
        self.share_text_var.set("")

    def start_upload(self):
        if self.uploading:
            messagebox.showinfo("提示", "正在上传，请稍候")
            return
        self.save_chunk_settings()
        pending = [index for index, item in enumerate(self.items) if item.status in {"等待上传", "上传失败"}]
        if not pending:
            messagebox.showinfo("提示", "没有待上传的文件")
            return
        cover_path = self.resolve_cover_path()
        for index in pending:
            if not self.items[index].cover_path:
                self.items[index].cover_path = cover_path
        self.uploading = True
        self.status_var.set("正在上传")
        threading.Thread(target=self.upload_worker, args=(pending,), daemon=True).start()

    def upload_worker(self, indexes):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for index in indexes:
            item = self.items[index]
            try:
                cover_path = item.cover_path
                if not cover_path:
                    raise RuntimeError("未设置默认封面，也未选择本次封面")
                if not Path(cover_path).exists():
                    raise RuntimeError("封面图片不存在")
                chunk_size = self.settings.chunk_size_mb * 1024 * 1024
                if item.size > chunk_size:
                    result = self.upload_chunked_item(index, item, cover_path)
                else:
                    result = self.upload_single_item(index, item, cover_path)
                self.result_queue.put(("success", index, result, ""))
            except Exception as exc:
                self.result_queue.put(("failure", index, str(exc), ""))
        self.result_queue.put(("done", None, None, None))

    def upload_single_item(self, index, item, cover_path):
        self.result_queue.put(("status", index, "生成伪装图片", ""))
        container_path = CACHE_DIR / f"xfb_{int(time.time())}_{index}.png"
        try:
            metadata = create_container(cover_path, item.path, container_path)
            self.result_queue.put(("status", index, "上传中", ""))
            uploaded = upload_file_with_retry(container_path)
            share_text = encode_share_text(SharePayload(url=uploaded["url"], name=metadata.name, size=metadata.size))
            return {"url": uploaded["url"], "share_text": share_text}
        finally:
            container_path.unlink(missing_ok=True)

    def upload_chunked_item(self, index, item, cover_path):
        current_mb = self.settings.chunk_size_mb
        last_error = None
        while current_mb >= MIN_CHUNK_MB:
            upload_id = uuid.uuid4().hex
            work_dir = CHUNKS_DIR / upload_id
            try:
                return self.upload_chunked_round(index, item, cover_path, work_dir, current_mb)
            except Exception as exc:
                last_error = exc
                cleanup_dir(work_dir)
                if not is_retryable_upload_error(exc) or current_mb <= MIN_CHUNK_MB:
                    raise
                next_mb = max(MIN_CHUNK_MB, current_mb // 2)
                self.result_queue.put(("status", index, f"降低分片大小后重试：{next_mb}MB", ""))
                current_mb = next_mb
        raise last_error

    def upload_chunked_round(self, index, item, cover_path, work_dir, chunk_size_mb):
        self.result_queue.put(("status", index, f"准备分片：{chunk_size_mb}MB", ""))
        chunk_size = chunk_size_mb * 1024 * 1024
        chunks_dir = work_dir / "parts"
        stego_dir = work_dir / "stego"
        manifest_path = work_dir / MANIFEST_NAME
        manifest_image_path = work_dir / "xfb_manifest.png"
        local_chunks = split_file(item.path, chunks_dir, chunk_size)
        source_sha256 = sha256_file(item.path)
        workers = self.settings.upload_workers
        records = [None] * len(local_chunks)

        def upload_one(local_chunk):
            upload_path = local_chunk.path
            if self.settings.stego_chunks:
                stego_dir.mkdir(parents=True, exist_ok=True)
                upload_path = stego_dir / f"part-{local_chunk.index:06d}.png"
                create_container(cover_path, local_chunk.path, upload_path)
            uploaded = upload_file_with_retry(upload_path)
            return ChunkRecord(
                index=local_chunk.index,
                size=local_chunk.size,
                sha256=local_chunk.sha256,
                url=uploaded["url"],
                stego=self.settings.stego_chunks,
            )

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(upload_one, local_chunk): local_chunk for local_chunk in local_chunks}
            completed = 0
            for future in as_completed(futures):
                record = future.result()
                records[record.index] = record
                completed += 1
                self.result_queue.put(("status", index, f"上传分片 {completed}/{len(local_chunks)}", ""))

        manifest = ChunkManifest(
            name=item.name,
            size=item.size,
            sha256=source_sha256,
            chunk_size=chunk_size,
            chunks=records,
        )
        save_manifest_file(manifest, manifest_path)
        self.result_queue.put(("status", index, "上传索引图片", ""))
        create_container(cover_path, manifest_path, manifest_image_path)
        uploaded_manifest = upload_file_with_retry(manifest_image_path)
        share_text = encode_share_text(SharePayload(url=uploaded_manifest["url"], name=item.name, size=item.size))
        cleanup_dir(work_dir)
        return {"url": uploaded_manifest["url"], "share_text": share_text}

    def process_queue(self):
        try:
            while True:
                event, index, payload, _ = self.result_queue.get_nowait()
                if event == "status":
                    if index is not None:
                        self.items[index].status = payload
                        self.items[index].error = ""
                        self.log(f"{payload}：{self.items[index].name}")
                    else:
                        self.status_var.set(payload)
                        self.log(payload)
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
                elif event == "failure":
                    item = self.items[index]
                    item.status = "上传失败"
                    item.error = payload
                    self.log(f"上传失败：{item.name}，{payload}")
                elif event == "done":
                    self.uploading = False
                    self.status_var.set("上传完成")
                    self.refresh_history_view()
                    self.log("队列上传完成")
                elif event == "extract_status":
                    self.extract_info_var.set(payload)
                    self.log(payload)
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
                elif event == "probe_success":
                    self.probing = False
                    suggested = int(payload)
                    if messagebox.askyesno("探测完成", f"建议分片大小为 {suggested}MB，是否保存到设置？"):
                        self.chunk_size_var.set(str(suggested))
                        self.save_chunk_settings()
                elif event == "probe_failure":
                    self.probing = False
                    messagebox.showerror("探测失败", payload)
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
        self.save_chunk_settings()
        self.extracting = True
        self.extract_info_var.set("正在下载并提取")
        threading.Thread(target=self.extract_worker, args=(text,), daemon=True).start()

    def extract_worker(self, text):
        work_dir = CHUNKS_DIR / f"download_{uuid.uuid4().hex}"
        downloaded = work_dir / "index.png"
        extracted_payload = None
        try:
            payload = decode_share_text(text)
            work_dir.mkdir(parents=True, exist_ok=True)
            self.result_queue.put(("extract_status", None, "下载索引图片", ""))
            download_file(payload.url, downloaded)
            metadata = inspect_container(downloaded)
            extracted_payload = work_dir / metadata.name
            extract_container(downloaded, extracted_payload)
            save_dir = Path(self.extract_save_dir_var.get())
            if metadata.name == MANIFEST_NAME:
                output_path = self.download_chunked_manifest(extracted_payload, save_dir, work_dir)
            else:
                output_path = save_dir / metadata.name
                output_path.parent.mkdir(parents=True, exist_ok=True)
                if extracted_payload != output_path:
                    output_path.write_bytes(extracted_payload.read_bytes())
            self.result_queue.put(("extract_success", None, str(output_path), ""))
        except Exception as exc:
            self.result_queue.put(("extract_failure", None, str(exc), ""))
        finally:
            cleanup_dir(work_dir)

    def download_chunked_manifest(self, manifest_path, save_dir, work_dir):
        self.result_queue.put(("extract_status", None, "读取分片清单", ""))
        manifest = load_manifest_file(manifest_path)
        save_dir = Path(save_dir)
        downloads_dir = work_dir / "downloads"
        extracted_dir = work_dir / "extracted"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = [None] * len(manifest.chunks)
        workers = self.settings.upload_workers

        def download_one(record):
            raw_path = downloads_dir / f"part-{record.index:06d}.png"
            output_path = extracted_dir / f"part-{record.index:06d}.bin"
            download_file(record.url, raw_path)
            if record.stego:
                extract_container(raw_path, output_path)
            else:
                output_path.write_bytes(raw_path.read_bytes())
            actual_sha256 = sha256_file(output_path)
            if actual_sha256 != record.sha256:
                raise ValueError(f"分片 {record.index} SHA-256 校验失败")
            return record.index, output_path

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(download_one, record): record for record in manifest.chunks}
            completed = 0
            for future in as_completed(futures):
                chunk_index, chunk_path = future.result()
                chunk_paths[chunk_index] = chunk_path
                completed += 1
                self.result_queue.put(("extract_status", None, f"下载分片 {completed}/{len(manifest.chunks)}", ""))

        self.result_queue.put(("extract_status", None, "合并文件", ""))
        output_path = save_dir / manifest.name
        merge_chunks(chunk_paths, output_path, manifest.sha256)
        self.result_queue.put(("extract_status", None, "校验完成", ""))
        return output_path

    def start_probe_chunk_size(self):
        if self.probing:
            messagebox.showinfo("提示", "正在探测，请稍候")
            return
        if not messagebox.askyesno("提示", "探测会上传临时测试文件，当前无法自动删除远程测试文件。是否继续？"):
            return
        self.save_chunk_settings()
        self.probing = True
        threading.Thread(target=self.probe_chunk_size_worker, daemon=True).start()

    def probe_chunk_size_worker(self):
        work_dir = CACHE_DIR / f"probe_{uuid.uuid4().hex}"
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            current = self.settings.chunk_size_mb
            best = None
            while MIN_CHUNK_MB <= current <= MAX_CHUNK_MB:
                self.result_queue.put(("status", None, f"探测分片大小：{current}MB", ""))
                test_path = work_dir / f"probe_{current}mb.bin"
                self.write_random_file(test_path, current * 1024 * 1024)
                try:
                    upload_file_with_retry(test_path, retries=1)
                    best = current
                    next_size = int(current * 1.5)
                    if next_size == current:
                        next_size += 1
                    if next_size > MAX_CHUNK_MB:
                        break
                    current = next_size
                except Exception:
                    if current <= MIN_CHUNK_MB:
                        break
                    current = max(MIN_CHUNK_MB, current // 2)
                    if best is not None and current <= best:
                        break
            if best is None:
                raise RuntimeError("未探测到可用分片大小")
            self.result_queue.put(("probe_success", None, best, ""))
        except Exception as exc:
            self.result_queue.put(("probe_failure", None, str(exc), ""))
        finally:
            cleanup_dir(work_dir)

    def write_random_file(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        remaining = size
        with open(path, "wb") as file:
            while remaining > 0:
                chunk = os.urandom(min(1024 * 1024, remaining))
                file.write(chunk)
                remaining -= len(chunk)

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
    ImageUploaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

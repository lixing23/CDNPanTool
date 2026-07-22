import os
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import flet as ft

from chunk_manager import cleanup_dir, merge_chunks, sha256_file, split_file
from manifest_codec import ChunkManifest, ChunkRecord, load_manifest_file, save_manifest_file
from oss_client import download_file, is_retryable_upload_error, upload_file_with_retry
from settings_store import SettingsStore
from share_codec import SharePayload, decode_share_text, encode_share_text
from stego_container import create_container, extract_container, inspect_container

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BASE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
ASSETS_DIR = BASE_DIR / "assets"
DEFAULT_COVER = ASSETS_DIR / "cover.jpg"
MANIFEST_NAME = "xfb_manifest.json"
MIN_CHUNK_MB = 5
MAX_CHUNK_MB = 200
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


@dataclass
class CloudFileItem:
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


class FletCloudDriveApp:
    def __init__(self, page):
        self.page = page
        self.store = SettingsStore(APP_DIR)
        self.settings = self.store.load_settings()
        self.items = []
        self.selected_index = None
        self.history = self.store.load_upload_history()
        self.selected_history_record = None
        self.current_view = "upload"
        self.uploading = False
        self.extracting = False
        self.probing = False
        self.save_dir = str(APP_DIR)
        self.log_visible = True
        self.log_lines = []

        self.main_area = ft.Container(expand=True)
        self.detail_area = ft.Container(width=360)
        self.status_text = ft.Text("就绪", size=12, color=ft.Colors.GREY_600)
        self.progress_bar = ft.ProgressBar(value=0, height=4, color=ft.Colors.BLUE_500, bgcolor=ft.Colors.GREY_200)
        self.log_panel = ft.Container()
        self.log_text = ft.Text("", size=11, selectable=True, color=ft.Colors.GREY_800)

        self.file_picker = ft.FilePicker()
        self.cover_picker = ft.FilePicker()
        self.save_dir_picker = ft.FilePicker()
        self.page.services.extend([self.file_picker, self.cover_picker, self.save_dir_picker])

        self.share_input = ft.TextField(multiline=True, min_lines=5, max_lines=8, hint_text="粘贴 xfb1. 开头的分享文本", border_radius=12)
        self.extract_result = ft.Text("等待解析", color=ft.Colors.GREY_600)
        self.default_cover_field = ft.TextField(label="默认封面路径", value=self.settings.default_cover_path, expand=True, read_only=True)
        self.chunk_size_field = ft.TextField(label="分片大小 MB", value=str(self.settings.chunk_size_mb), width=160)
        self.workers_field = ft.TextField(label="上传并发数", value=str(self.settings.upload_workers), width=160)
        self.stego_switch = ft.Switch(label="分片也伪装成图片", value=self.settings.stego_chunks)

        self.configure_page()
        self.initialize_default_cover()
        self.default_cover_field.value = self.settings.default_cover_path
        self.render_shell()
        self.append_log("应用启动")

    def initialize_default_cover(self):
        if not self.settings.default_cover_path or not Path(self.settings.default_cover_path).exists():
            self.settings.default_cover_path = str(DEFAULT_COVER)
            self.store.save_settings(self.settings)
        self.default_cover_field.value = self.settings.default_cover_path

    def configure_page(self):
        self.page.title = "CDNPanTool"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.window.width = 1280
        self.page.window.height = 820
        self.page.window.icon = str(ASSETS_DIR / "CDNPanTool.ico")
        self.page.padding = 0
        self.page.bgcolor = "#F5F7FB"

    def render_shell(self):
        self.page.controls.clear()
        self.page.add(
            ft.Row(
                controls=[
                    self.build_sidebar(),
                    ft.VerticalDivider(width=1, color="#E5E7EB"),
                    ft.Column(
                        controls=[
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        self.main_area,
                                        ft.VerticalDivider(width=1, color="#E5E7EB"),
                                        self.detail_area,
                                    ],
                                    expand=True,
                                ),
                                expand=True,
                                padding=20,
                            ),
                            self.build_status_bar(),
                        ],
                        expand=True,
                        spacing=0,
                    ),
                ],
                expand=True,
                spacing=0,
            )
        )
        self.render_current_view()

    def build_sidebar(self):
        nav_items = [
            ("upload", ft.Icons.CLOUD_UPLOAD_OUTLINED, "上传分享"),
            ("extract", ft.Icons.DOWNLOAD_OUTLINED, "解析文件"),
            ("history", ft.Icons.HISTORY, "历史记录"),
            ("settings", ft.Icons.SETTINGS_OUTLINED, "设置"),
        ]
        buttons = []
        for key, icon, label in nav_items:
            selected = self.current_view == key
            buttons.append(
                ft.Container(
                    content=ft.Row(
                        controls=[ft.Icon(icon, size=20), ft.Text(label, size=14, weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400)],
                        spacing=12,
                    ),
                    bgcolor="#E8F1FF" if selected else None,
                    border_radius=12,
                    padding=ft.Padding(14, 12, 14, 12),
                    ink=True,
                    on_click=lambda e, value=key: self.switch_view(value),
                )
            )
        return ft.Container(
            width=220,
            bgcolor="#FFFFFF",
            padding=20,
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Container(
                                content=ft.Image(src=str(ASSETS_DIR / "logo.png"), fit=ft.BoxFit.COVER),
                                width=42,
                                height=42,
                                border_radius=14,
                                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                            ),
                            ft.Column(
                                controls=[ft.Text("CDNPanTool", size=17, weight=ft.FontWeight.BOLD), ft.Text("Cloud Drive Pan", size=11, color=ft.Colors.GREY_500)],
                                spacing=0,
                            ),
                        ],
                        spacing=12,
                    ),
                    ft.Divider(height=28, color="#EEF2F7"),
                    *buttons,
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.Text("直链隐藏 · 分片上传 · 图片伪装", size=11, color=ft.Colors.GREY_500),
                        padding=12,
                        border_radius=12,
                        bgcolor="#F8FAFC",
                    ),
                ],
                spacing=8,
                expand=True,
            ),
        )

    def build_status_bar(self):
        return ft.Container(
            bgcolor="#FFFFFF",
            border=ft.Border(top=ft.BorderSide(1, "#E5E7EB")),
            padding=ft.Padding(20, 10, 20, 10),
            content=ft.Column(
                controls=[
                    self.progress_bar,
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=ft.Colors.GREY_500),
                            self.status_text,
                            ft.Container(expand=True),
                            ft.Button("隐藏日志" if self.log_visible else "显示日志", icon=ft.Icons.ARTICLE_OUTLINED, on_click=self.toggle_log_panel),
                            ft.Button("复制日志", icon=ft.Icons.CONTENT_COPY, on_click=self.copy_log_content),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            ),
        )

    def initialize_logging(self):
        self.log_lines = []

    def safe_preview(self, text):
        value = text or ""
        if len(value) <= 16:
            return value
        return f"{value[:8]}...{value[-8:]}"

    def append_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-200:]
        if hasattr(self, "log_text"):
            self.log_text.value = "\n".join(self.log_lines[-80:])
        if hasattr(self, "log_panel") and self.log_visible:
            self.log_panel.content = self.build_log_content()

    def build_log_content(self):
        self.log_text.value = "\n".join(self.log_lines[-80:])
        return ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("运行日志", size=13, weight=ft.FontWeight.BOLD),
                        ft.Text(f"共 {len(self.log_lines)} 条", size=11, color=ft.Colors.GREY_500),
                    ],
                    spacing=12,
                ),
                ft.Container(
                    content=ft.Column(controls=[self.log_text], scroll=ft.ScrollMode.AUTO),
                    height=130,
                    bgcolor="#F8FAFC",
                    border_radius=10,
                    padding=10,
                ),
            ],
            spacing=8,
        )

    def render_log_panel(self):
        self.log_panel.visible = self.log_visible
        self.log_panel.bgcolor = "#FFFFFF"
        self.log_panel.border = ft.Border(top=ft.BorderSide(1, "#E5E7EB"))
        self.log_panel.padding = ft.Padding(20, 10, 20, 10)
        self.log_panel.content = self.build_log_content()
        return self.log_panel

    def toggle_log_panel(self, event=None):
        self.log_visible = not self.log_visible
        self.append_log(f"日志面板切换：{'显示' if self.log_visible else '隐藏'}")
        self.render_shell()

    async def copy_log_content(self, event=None):
        await self.copy_text("\n".join(self.log_lines), "运行日志内容")

    def switch_view(self, value):
        self.current_view = value
        self.render_shell()

    def render_current_view(self):
        if self.current_view == "upload":
            self.render_upload_view()
        elif self.current_view == "extract":
            self.render_extract_view()
        elif self.current_view == "history":
            self.render_history_view()
        else:
            self.render_settings_view()
        self.page.update()

    def card(self, content, padding=18):
        return ft.Container(
            content=content,
            padding=padding,
            bgcolor="#FFFFFF",
            border_radius=18,
            border=ft.Border(
                left=ft.BorderSide(1, "#EEF2F7"),
                top=ft.BorderSide(1, "#EEF2F7"),
                right=ft.BorderSide(1, "#EEF2F7"),
                bottom=ft.BorderSide(1, "#EEF2F7"),
            ),
            shadow=ft.BoxShadow(blur_radius=18, color="#14000000", offset=ft.Offset(0, 4)),
        )

    def render_upload_view(self):
        self.main_area.content = ft.Column(
            controls=[
                self.build_page_header("上传分享", "选择文件后自动判断单文件或分片上传，生成短分享文本。"),
                self.card(
                    ft.Row(
                        controls=[
                            ft.Button("选择文件", icon=ft.Icons.ADD, on_click=self.pick_files),
                            ft.Button("开始上传", icon=ft.Icons.CLOUD_UPLOAD, on_click=self.start_upload),
                            ft.Button("清空列表", icon=ft.Icons.DELETE_OUTLINE, on_click=self.clear_items),
                        ],
                        spacing=12,
                    )
                ),
                self.card(self.build_file_table(), padding=10),
            ],
            spacing=16,
            expand=True,
        )
        self.render_upload_detail()

    def build_page_header(self, title, subtitle):
        return ft.Row(
            controls=[
                ft.Column(
                    controls=[ft.Text(title, size=26, weight=ft.FontWeight.BOLD), ft.Text(subtitle, size=13, color=ft.Colors.GREY_600)],
                    spacing=4,
                    expand=True,
                )
            ]
        )

    def build_file_table(self):
        rows = []
        for index, item in enumerate(self.items):
            selected = index == self.selected_index
            rows.append(
                ft.DataRow(
                    selected=selected,
                    on_select_change=lambda e, idx=index: self.select_item(idx),
                    cells=[
                        ft.DataCell(ft.Text(item.name, overflow=ft.TextOverflow.ELLIPSIS)),
                        ft.DataCell(ft.Text(format_size(item.size))),
                        ft.DataCell(ft.Text("分片" if item.size > self.settings.chunk_size_mb * 1024 * 1024 else "单文件")),
                        ft.DataCell(self.status_badge(item.status)),
                    ],
                )
            )
        return ft.Column(
            controls=[
                ft.Text("文件列表", size=16, weight=ft.FontWeight.BOLD),
                ft.DataTable(
                    columns=[
                        ft.DataColumn(ft.Text("文件名")),
                        ft.DataColumn(ft.Text("大小")),
                        ft.DataColumn(ft.Text("类型")),
                        ft.DataColumn(ft.Text("状态")),
                    ],
                    rows=rows,
                    expand=True,
                    heading_row_color="#F8FAFC",
                ),
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    def status_badge(self, status):
        color = ft.Colors.GREEN_600 if "成功" in status else ft.Colors.RED_600 if "失败" in status else ft.Colors.BLUE_600 if "上传" in status else ft.Colors.GREY_600
        return ft.Container(content=ft.Text(status, size=12, color=color), bgcolor="#F8FAFC", border_radius=20, padding=ft.Padding(10, 5, 10, 5))

    def render_upload_detail(self):
        item = self.items[self.selected_index] if self.selected_index is not None and self.selected_index < len(self.items) else None
        if item is None:
            content = ft.Column(
                controls=[
                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=42, color=ft.Colors.GREY_400),
                    ft.Text("未选择文件", size=18, weight=ft.FontWeight.BOLD),
                    ft.Text("从左侧列表选择文件后，这里会显示分享文本和直链。", size=12, color=ft.Colors.GREY_500),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
            )
        else:
            content = ft.Column(
                controls=[
                    ft.Text("文件详情", size=18, weight=ft.FontWeight.BOLD),
                    self.detail_line("文件名", item.name),
                    self.detail_line("大小", format_size(item.size)),
                    self.detail_line("状态", item.status if not item.error else item.error),
                    ft.Divider(),
                    ft.Text("短分享文本", size=13, weight=ft.FontWeight.BOLD),
                    ft.TextField(value=item.share_text, multiline=True, min_lines=3, max_lines=5, read_only=True, border_radius=10),
                    ft.Row(
                        controls=[
                            ft.Button("复制分享", icon=ft.Icons.CONTENT_COPY, on_click=self.copy_selected_item_share),
                            ft.Button("复制直链", icon=ft.Icons.LINK, on_click=self.copy_selected_item_url),
                        ],
                        spacing=8,
                    ),
                    ft.Text("伪装图片直链", size=13, weight=ft.FontWeight.BOLD),
                    ft.TextField(value=item.url, multiline=True, min_lines=2, max_lines=4, read_only=True, border_radius=10),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
            )
        self.detail_area.content = self.card(content)

    def detail_line(self, label, value):
        return ft.Column(controls=[ft.Text(label, size=12, color=ft.Colors.GREY_500), ft.Text(value or "-", size=14)], spacing=2)

    def render_extract_view(self):
        self.main_area.content = ft.Column(
            controls=[
                self.build_page_header("解析文件", "粘贴短分享文本，下载伪装图片并还原原文件。"),
                self.card(
                    ft.Column(
                        controls=[
                            self.share_input,
                            ft.Row(
                                controls=[
                                    ft.Button("解析信息", icon=ft.Icons.SEARCH, on_click=self.preview_share_text),
                                    ft.Button("选择保存目录", icon=ft.Icons.FOLDER_OPEN, on_click=self.pick_save_dir),
                                    ft.Button("下载并提取", icon=ft.Icons.DOWNLOAD, on_click=self.start_extract),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=14,
                    )
                ),
                self.card(ft.Column(controls=[ft.Text("解析结果", size=16, weight=ft.FontWeight.BOLD), self.extract_result], spacing=12)),
            ],
            spacing=16,
            expand=True,
        )
        self.detail_area.content = self.card(
            ft.Column(
                controls=[
                    ft.Text("保存位置", size=18, weight=ft.FontWeight.BOLD),
                    ft.Text(self.save_dir, size=12, color=ft.Colors.GREY_600),
                    ft.Divider(),
                    ft.Text("提示", size=14, weight=ft.FontWeight.BOLD),
                    ft.Text("分片分享会先下载索引图片，再自动下载所有分片并合并。", size=12, color=ft.Colors.GREY_600),
                ],
                spacing=10,
            )
        )

    def render_history_view(self):
        self.history = self.store.load_upload_history()
        rows = []
        for record in self.history[:200]:
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(record.get("name", ""), overflow=ft.TextOverflow.ELLIPSIS)),
                        ft.DataCell(ft.Text(format_size(record.get("size", 0)))),
                        ft.DataCell(ft.Text(record.get("uploaded_at", ""))),
                        ft.DataCell(ft.Text(record.get("status", ""))),
                    ],
                    on_select_change=lambda e, item=record: self.show_history_detail(item),
                )
            )
        self.main_area.content = ft.Column(
            controls=[
                self.build_page_header("历史记录", "查看上传过的文件，快速复制分享文本。"),
                self.card(
                    ft.Column(
                        controls=[
                            ft.Row(controls=[ft.Button("刷新", icon=ft.Icons.REFRESH, on_click=lambda e: self.render_history_view())]),
                            ft.DataTable(
                                columns=[ft.DataColumn(ft.Text("文件名")), ft.DataColumn(ft.Text("大小")), ft.DataColumn(ft.Text("上传时间")), ft.DataColumn(ft.Text("状态"))],
                                rows=rows,
                                heading_row_color="#F8FAFC",
                            ),
                        ],
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    padding=10,
                ),
            ],
            spacing=16,
            expand=True,
        )
        self.detail_area.content = self.card(ft.Text("选择一条历史记录查看详情", color=ft.Colors.GREY_500))

    def show_history_detail(self, record):
        self.selected_history_record = record
        self.detail_area.content = self.card(
            ft.Column(
                controls=[
                    ft.Text("历史详情", size=18, weight=ft.FontWeight.BOLD),
                    self.detail_line("文件名", record.get("name", "")),
                    self.detail_line("大小", format_size(record.get("size", 0))),
                    self.detail_line("上传时间", record.get("uploaded_at", "")),
                    ft.Divider(),
                    ft.TextField(value=record.get("share_text", ""), multiline=True, min_lines=3, max_lines=5, read_only=True, border_radius=10),
                    ft.Row(
                        controls=[
                            ft.Button("复制分享", icon=ft.Icons.CONTENT_COPY, on_click=self.copy_history_share),
                            ft.Button("复制直链", icon=ft.Icons.LINK, on_click=self.copy_history_url),
                        ],
                    ),
                ],
                spacing=12,
            )
        )
        self.page.update()

    def render_settings_view(self):
        self.main_area.content = ft.Column(
            controls=[
                self.build_page_header("设置", "把低频配置收纳在这里，保持上传界面清爽。"),
                self.card(
                    ft.Column(
                        controls=[
                            ft.Text("封面设置", size=16, weight=ft.FontWeight.BOLD),
                            ft.Row(controls=[self.default_cover_field, ft.Button("选择封面", icon=ft.Icons.IMAGE, on_click=self.pick_cover)]),
                        ],
                        spacing=12,
                    )
                ),
                self.card(
                    ft.Column(
                        controls=[
                            ft.Text("分片设置", size=16, weight=ft.FontWeight.BOLD),
                            ft.Row(controls=[self.chunk_size_field, self.workers_field, self.stego_switch], spacing=16),
                            ft.Row(
                                controls=[
                                    ft.Button("保存设置", icon=ft.Icons.SAVE, on_click=self.save_settings),
                                    ft.Button("探测最大分片大小", icon=ft.Icons.SPEED, on_click=self.start_probe_chunk_size),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=14,
                    )
                ),
            ],
            spacing=16,
            expand=True,
        )
        self.detail_area.content = self.card(
            ft.Column(
                controls=[
                    ft.Text("建议", size=18, weight=ft.FontWeight.BOLD),
                    ft.Text("分片大小默认 100MB。如果上传遇到 10054，可以降低到 50MB 或使用探测功能。", size=13, color=ft.Colors.GREY_600),
                    ft.Text("并发数建议 3，网络稳定时可提高。", size=13, color=ft.Colors.GREY_600),
                ],
                spacing=12,
            )
        )

    async def pick_files(self, event=None):
        files = await self.file_picker.pick_files(allow_multiple=True)
        if not files:
            return
        existing = {item.path for item in self.items}
        cover_path = self.settings.default_cover_path
        for file in files:
            path = file.path
            if path in existing:
                continue
            size = os.path.getsize(path)
            self.items.append(CloudFileItem(path=path, name=os.path.basename(path), size=size, cover_path=cover_path))
        if self.items and self.selected_index is None:
            self.selected_index = 0
        self.render_current_view()
        self.show_message("文件已加入列表")

    async def pick_cover(self, event=None):
        files = await self.cover_picker.pick_files(allow_multiple=False)
        if not files:
            return
        path = files[0].path
        if Path(path).suffix.lower() not in COVER_EXTENSIONS:
            self.show_message("请选择图片作为封面")
            return
        self.default_cover_field.value = path
        self.page.update()

    async def pick_save_dir(self, event=None):
        path = await self.save_dir_picker.get_directory_path()
        if not path:
            return
        self.save_dir = path
        self.render_current_view()

    def select_item(self, index):
        self.selected_index = index
        self.render_current_view()

    def clear_items(self, event=None):
        if self.uploading:
            self.show_message("上传中不能清空列表")
            return
        self.items.clear()
        self.selected_index = None
        self.render_current_view()

    def save_settings(self, event=None):
        self.settings.default_cover_path = self.default_cover_field.value or ""
        self.settings.chunk_size_mb = normalize_int(self.chunk_size_field.value, 50, MIN_CHUNK_MB, MAX_CHUNK_MB)
        self.settings.upload_workers = normalize_int(self.workers_field.value, 3, 1, 8)
        self.settings.stego_chunks = bool(self.stego_switch.value)
        self.chunk_size_field.value = str(self.settings.chunk_size_mb)
        self.workers_field.value = str(self.settings.upload_workers)
        self.store.save_settings(self.settings)
        self.show_message("设置已保存")
        self.render_current_view()

    def start_upload(self, event=None):
        if self.uploading:
            self.show_message("正在上传，请稍候")
            return
        self.save_settings()
        pending = [index for index, item in enumerate(self.items) if item.status in {"等待上传", "上传失败"}]
        if not pending:
            self.show_message("没有待上传的文件")
            return
        if not self.settings.default_cover_path:
            self.show_message("请先在设置中选择默认封面")
            return
        self.uploading = True
        self.set_status("开始上传", 0)
        threading.Thread(target=self.upload_worker, args=(pending,), daemon=True).start()

    def upload_worker(self, indexes):
        for index in indexes:
            item = self.items[index]
            try:
                cover_path = self.settings.default_cover_path
                if not Path(cover_path).exists():
                    raise RuntimeError("封面图片不存在")
                chunk_size = self.settings.chunk_size_mb * 1024 * 1024
                self.append_log(f"上传开始：{item.name}，大小={item.size}，分片阈值={chunk_size}")
                result = self.upload_chunked_item(index, item, cover_path) if item.size > chunk_size else self.upload_single_item(index, item, cover_path)
                item.status = "上传成功"
                item.url = result["url"]
                item.share_text = result["share_text"]
                item.error = ""
                item.uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.store.add_upload_history(asdict(item))
                self.append_log(f"上传成功：{item.name}，预览={self.safe_preview(item.url)}")
                self.page.run_thread(lambda: self.render_current_view())
            except Exception as exc:
                item.status = "上传失败"
                item.error = str(exc)
                self.append_log(f"上传失败：{item.name}，错误={exc}")
                self.append_log(traceback.format_exc())
                self.page.run_thread(lambda message=str(exc): self.show_message(message))
                self.page.run_thread(lambda: self.render_current_view())
        self.uploading = False
        self.page.run_thread(lambda: self.set_status("上传完成", 1))

    def upload_single_item(self, index, item, cover_path):
        self.update_item_status(index, "生成伪装图片")
        work_dir = Path(tempfile.mkdtemp(prefix="xfb_single_"))
        container_path = work_dir / f"xfb_{int(time.time())}_{index}.png"
        try:
            metadata = create_container(cover_path, item.path, container_path)
            self.update_item_status(index, "上传中")
            uploaded = upload_file_with_retry(container_path)
            share_text = encode_share_text(SharePayload(url=uploaded["url"], name=metadata.name, size=metadata.size))
            return {"url": uploaded["url"], "share_text": share_text}
        finally:
            cleanup_dir(work_dir)

    def upload_chunked_item(self, index, item, cover_path):
        current_mb = self.settings.chunk_size_mb
        last_error = None
        while current_mb >= MIN_CHUNK_MB:
            work_dir = Path(tempfile.mkdtemp(prefix="xfb_upload_"))
            try:
                return self.upload_chunked_round(index, item, cover_path, work_dir, current_mb)
            except Exception as exc:
                last_error = exc
                cleanup_dir(work_dir)
                if not is_retryable_upload_error(exc) or current_mb <= MIN_CHUNK_MB:
                    raise
                current_mb = max(MIN_CHUNK_MB, current_mb // 2)
                self.update_item_status(index, f"降低分片到 {current_mb}MB 重试")
        raise last_error

    def upload_chunked_round(self, index, item, cover_path, work_dir, chunk_size_mb):
        self.update_item_status(index, f"准备分片 {chunk_size_mb}MB")
        chunk_size = chunk_size_mb * 1024 * 1024
        chunks_dir = work_dir / "parts"
        stego_dir = work_dir / "stego"
        manifest_path = work_dir / MANIFEST_NAME
        manifest_image_path = work_dir / "xfb_manifest.png"
        local_chunks = split_file(item.path, chunks_dir, chunk_size)
        source_sha256 = sha256_file(item.path)
        records = [None] * len(local_chunks)

        def upload_one(local_chunk):
            upload_path = local_chunk.path
            if self.settings.stego_chunks:
                stego_dir.mkdir(parents=True, exist_ok=True)
                upload_path = stego_dir / f"part-{local_chunk.index:06d}.png"
                create_container(cover_path, local_chunk.path, upload_path)
            uploaded = upload_file_with_retry(upload_path)
            return ChunkRecord(local_chunk.index, local_chunk.size, local_chunk.sha256, uploaded["url"], self.settings.stego_chunks)

        with ThreadPoolExecutor(max_workers=self.settings.upload_workers) as executor:
            futures = {executor.submit(upload_one, chunk): chunk for chunk in local_chunks}
            completed = 0
            for future in as_completed(futures):
                record = future.result()
                records[record.index] = record
                completed += 1
                self.update_item_status(index, f"上传分片 {completed}/{len(local_chunks)}")
                self.set_status(f"上传分片 {completed}/{len(local_chunks)}", completed / len(local_chunks))

        manifest = ChunkManifest(item.name, item.size, source_sha256, chunk_size, records)
        save_manifest_file(manifest, manifest_path)
        self.update_item_status(index, "上传索引图片")
        create_container(cover_path, manifest_path, manifest_image_path)
        uploaded_manifest = upload_file_with_retry(manifest_image_path)
        share_text = encode_share_text(SharePayload(uploaded_manifest["url"], item.name, item.size))
        cleanup_dir(work_dir)
        return {"url": uploaded_manifest["url"], "share_text": share_text}

    def update_item_status(self, index, status):
        self.items[index].status = status
        self.page.run_thread(lambda: self.render_current_view())

    def preview_share_text(self, event=None):
        try:
            payload = decode_share_text((self.share_input.value or "").strip())
            self.extract_result.value = f"文件名：{payload.name}\n大小：{format_size(payload.size)}"
            self.page.update()
        except Exception as exc:
            self.show_message(str(exc))

    def start_extract(self, event=None):
        if self.extracting:
            self.show_message("正在提取，请稍候")
            return
        text = (self.share_input.value or "").strip()
        if not text:
            self.show_message("请先粘贴分享文本")
            return
        self.extracting = True
        self.set_status("正在下载并提取", 0)
        threading.Thread(target=self.extract_worker, args=(text,), daemon=True).start()

    def extract_worker(self, text):
        work_dir = Path(tempfile.mkdtemp(prefix="xfb_extract_"))
        downloaded = work_dir / "index.png"
        try:
            payload = decode_share_text(text)
            self.append_log(f"提取开始：文件名={payload.name}，大小={payload.size}，预览={self.safe_preview(payload.url)}")
            work_dir.mkdir(parents=True, exist_ok=True)
            self.set_status("下载索引图片", 0.1)
            download_file(payload.url, downloaded)
            self.append_log(f"索引图片下载完成：{downloaded.name}，字节数={downloaded.stat().st_size}")
            metadata = inspect_container(downloaded)
            self.append_log(f"容器解析完成：name={metadata.name}，size={metadata.size}")
            extracted_payload = work_dir / metadata.name
            extract_container(downloaded, extracted_payload)
            self.append_log(f"容器提取完成：{extracted_payload.name}，字节数={extracted_payload.stat().st_size}")
            if metadata.name == MANIFEST_NAME:
                output_path = self.download_chunked_manifest(extracted_payload, Path(self.save_dir), work_dir)
            else:
                output_path = Path(self.save_dir) / metadata.name
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(extracted_payload.read_bytes())
                self.append_log(f"单文件写入完成：{output_path}")
            self.extract_result.value = f"提取成功：{output_path}"
            self.append_log(f"提取成功：{output_path}")
            self.page.run_thread(lambda: self.render_current_view())
            self.page.run_thread(lambda: self.show_message("提取完成"))
        except Exception as exc:
            self.append_log(f"提取失败：{exc}")
            self.append_log(traceback.format_exc())
            self.page.run_thread(lambda message=str(exc): self.show_message(message))
        finally:
            self.extracting = False
            cleanup_dir(work_dir)
            self.page.run_thread(lambda: self.set_status("就绪", 0))

    def download_chunked_manifest(self, manifest_path, save_dir, work_dir):
        manifest = load_manifest_file(manifest_path)
        self.append_log(f"分片清单加载：{manifest.name}，分片数={len(manifest.chunks)}，源大小={manifest.size}")
        downloads_dir = work_dir / "downloads"
        extracted_dir = work_dir / "extracted"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        extracted_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = [None] * len(manifest.chunks)

        def download_one(record):
            raw_path = downloads_dir / f"part-{record.index:06d}.png"
            output_path = extracted_dir / f"part-{record.index:06d}.bin"
            download_file(record.url, raw_path)
            if record.stego:
                extract_container(raw_path, output_path)
            else:
                output_path.write_bytes(raw_path.read_bytes())
            actual_sha = sha256_file(output_path)
            if actual_sha != record.sha256:
                self.append_log(f"分片 {record.index} SHA-256 校验失败：期望={record.sha256[:12]}，实际={actual_sha[:12]}")
                raise ValueError(f"分片 {record.index} SHA-256 校验失败")
            return record.index, output_path

        try:
            with ThreadPoolExecutor(max_workers=self.settings.upload_workers) as executor:
                futures = {executor.submit(download_one, record): record for record in manifest.chunks}
                completed = 0
                for future in as_completed(futures):
                    chunk_index, chunk_path = future.result()
                    chunk_paths[chunk_index] = chunk_path
                    completed += 1
                    self.set_status(f"下载分片 {completed}/{len(manifest.chunks)}", completed / len(manifest.chunks))
            output_path = save_dir / manifest.name
            self.append_log(f"开始合并分片：目标={output_path}，分片数={len(chunk_paths)}")
            merge_chunks(chunk_paths, output_path, manifest.sha256)
            self.append_log(f"分片合并完成：{output_path}，字节数={output_path.stat().st_size}")
            return output_path
        except Exception as exc:
            self.append_log(f"分片下载/合并失败：{exc}")
            self.append_log(traceback.format_exc())
            raise

    def start_probe_chunk_size(self, event=None):
        if self.probing:
            self.show_message("正在探测，请稍候")
            return
        self.save_settings()
        self.probing = True
        self.show_message("开始探测，会产生远程测试文件")
        threading.Thread(target=self.probe_chunk_size_worker, daemon=True).start()

    def probe_chunk_size_worker(self):
        work_dir = Path(tempfile.mkdtemp(prefix="xfb_probe_"))
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            current = self.settings.chunk_size_mb
            best = None
            while MIN_CHUNK_MB <= current <= MAX_CHUNK_MB:
                self.set_status(f"探测分片大小 {current}MB", 0)
                test_path = work_dir / f"probe_{current}mb.bin"
                self.write_random_file(test_path, current * 1024 * 1024)
                try:
                    upload_file_with_retry(test_path, retries=1)
                    best = current
                    next_size = int(current * 1.5)
                    if next_size > MAX_CHUNK_MB:
                        break
                    current = max(next_size, current + 1)
                except Exception:
                    current = max(MIN_CHUNK_MB, current // 2)
                    if best is not None and current <= best:
                        break
            if best is None:
                raise RuntimeError("未探测到可用分片大小")
            self.chunk_size_field.value = str(best)
            self.page.run_thread(lambda: self.save_settings())
            self.page.run_thread(lambda: self.show_message(f"建议分片大小：{best}MB，已保存"))
        except Exception as exc:
            self.page.run_thread(lambda message=str(exc): self.show_message(message))
        finally:
            cleanup_dir(work_dir)
            self.probing = False
            self.page.run_thread(lambda: self.set_status("就绪", 0))

    def write_random_file(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        remaining = size
        with open(path, "wb") as file:
            while remaining > 0:
                chunk = os.urandom(min(1024 * 1024, remaining))
                file.write(chunk)
                remaining -= len(chunk)

    def get_selected_item(self):
        if self.selected_index is None or self.selected_index >= len(self.items):
            return None
        return self.items[self.selected_index]

    async def copy_selected_item_share(self, event=None):
        item = self.get_selected_item()
        await self.copy_text(item.share_text if item else "", "当前文件分享文本")

    async def copy_selected_item_url(self, event=None):
        item = self.get_selected_item()
        await self.copy_text(item.url if item else "", "当前文件直链")

    async def copy_history_share(self, event=None):
        await self.copy_text((self.selected_history_record or {}).get("share_text", ""), "历史分享文本")

    async def copy_history_url(self, event=None):
        await self.copy_text((self.selected_history_record or {}).get("url", ""), "历史直链")

    async def copy_text(self, text, label="内容"):
        value = text or ""
        self.append_log(f"复制请求：{label}，长度={len(value)}，预览={self.safe_preview(value)}")
        if not value:
            self.append_log(f"复制取消：{label} 为空")
            self.show_message("没有可复制的内容")
            return
        try:
            await self.page.clipboard.set(value)
            self.append_log(f"复制成功：{label}")
            self.show_message("已复制")
        except Exception as exc:
            self.append_log(f"复制失败：{label}，错误={exc}")
            self.append_log(traceback.format_exc())
            self.show_message(f"复制失败：{exc}")

    def set_status(self, text, progress=None):
        self.status_text.value = text
        if text and hasattr(self, "log_lines"):
            self.append_log(f"状态：{text}")
        if progress is not None:
            self.progress_bar.value = max(0, min(1, progress))
        self.page.update()

    def show_message(self, text):
        self.page.snack_bar = ft.SnackBar(ft.Text(text), open=True)
        self.page.update()


def main(page):
    FletCloudDriveApp(page)


if __name__ == "__main__":
    ft.run(main)

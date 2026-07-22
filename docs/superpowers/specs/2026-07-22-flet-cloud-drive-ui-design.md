# Flet 云盘管理界面设计

## 背景

当前桌面工具已经具备任意文件伪装图片上传、分片上传、短分享文本、分片解析下载和历史记录能力。但现有 Tkinter 界面把封面设置、分片设置、上传队列、分享结果、日志和历史记录全部堆在主界面，导致视觉复杂、操作负担高，也不符合“类似云盘”的产品定位。

本次改造目标是新增一个基于 Flet 的现代化桌面入口，把界面重构为云盘管理风格。现有核心业务模块继续复用，旧 Tkinter 入口保留为 legacy 入口。

## 目标

- 新增 `flet_app.py` 作为现代 UI 入口。
- 新增 `requirements.txt`，声明 `flet` 依赖。
- 保留现有核心模块：
  - `oss_client.py`
  - `stego_container.py`
  - `share_codec.py`
  - `chunk_manager.py`
  - `manifest_codec.py`
  - `settings_store.py`
- 主界面采用云盘客户端布局：左侧导航、中间主内容、右侧详情、底部任务状态。
- 高频操作展示在主界面。
- 低频设置收纳到设置页。
- 支持上传分享、解析文件、历史记录、设置四个页面。
- 上传流程复用现有单文件与分片上传逻辑。
- 解析流程复用现有单文件与分片解析逻辑。
- 不再在主界面直接展示长日志区。
- 状态和错误用 snackbar、进度条、状态文本展示。

## 非目标

- 不删除 `image_uploader_gui.py`。
- 不改变分享文本格式。
- 不改变分片 manifest 格式。
- 不实现账号系统。
- 不实现云端删除。
- 不实现断点续传。
- 不做完整文件管理服务端。

## 依赖

新增 `requirements.txt`：

```text
flet>=0.25.0
```

Flet 作为现代 UI 入口依赖。核心上传、分片和解析模块仍只依赖 Python 标准库。

## 信息架构

应用包含四个页面：

```text
上传分享
解析文件
历史记录
设置
```

左侧导航固定显示：

```text
小飞包云盘
- 上传分享
- 解析文件
- 历史记录
- 设置
```

中间区域根据页面切换。

右侧详情面板在上传页和历史页显示选中文件详情；在解析页显示解析结果；在设置页隐藏或显示帮助说明。

底部状态栏全局显示当前任务状态和进度。

## 上传分享页

上传页布局：

```text
顶部操作区：
[选择文件] [开始上传] [清空列表]

文件列表：
文件名 | 大小 | 类型 | 状态 | 分享

右侧详情：
文件名
文件大小
上传状态
伪装图片直链
短分享文本
[复制分享文本]
[复制直链]
```

交互规则：

- 点击“选择文件”选择一个或多个文件。
- 文件加入上传列表后状态为“等待上传”。
- 点击“开始上传”后逐个处理。
- 小文件使用单文件伪装上传。
- 大文件自动使用分片上传。
- 上传成功后右侧详情显示分享文本。
- 选中文件时右侧详情切换到该文件。
- 主界面不显示封面和分片设置；这些放入设置页。

## 解析文件页

解析页布局：

```text
分享文本输入框
[解析信息] [选择保存目录] [下载并提取]

解析结果卡片：
文件名
文件大小
保存目录
当前状态
进度
```

交互规则：

- 用户粘贴 `xfb1.` 分享文本。
- 点击“解析信息”只解码分享文本并展示基础信息。
- 点击“下载并提取”执行完整下载与提取。
- 如果索引图片中提取出 `xfb_manifest.json`，进入分片下载合并流程。
- 如果提取出普通文件，按单文件分享处理。

## 历史记录页

历史页布局：

```text
顶部：
[刷新] [复制选中分享文本]

历史列表：
文件名 | 大小 | 上传时间 | 状态

右侧详情：
短分享文本
直链
[复制分享文本]
[复制直链]
```

历史数据来源继续使用 `upload_history.json`。

## 设置页

设置页使用卡片分组：

### 封面设置

- 默认封面路径
- 选择封面按钮

### 分片设置

- 分片大小 MB，默认 50，范围 5 到 200
- 上传并发数，默认 3，范围 1 到 8
- 分片也伪装成图片，默认开启
- 探测最大分片大小按钮

### 操作

- 保存设置

设置保存到 `settings.json`。

## 状态反馈

Flet UI 使用以下方式反馈状态：

- `SnackBar`：成功、错误、复制完成。
- `ProgressBar`：上传、下载、探测进度。
- 底部状态栏：当前正在执行的任务文本。
- 文件列表状态列：等待上传、生成伪装图片、上传分片、上传成功、上传失败。

## 模块设计

新增 `flet_app.py`。

主要类：

```text
FletCloudDriveApp
```

职责：

- 构建 Flet 页面。
- 管理当前导航页面。
- 管理上传列表状态。
- 调用现有业务模块执行上传、分片上传、解析和下载。
- 将后台线程结果回传到 UI。

后台任务继续使用 Python `threading.Thread` 和 `queue.Queue`，避免阻塞 UI。

## 复用逻辑

`flet_app.py` 需要复用 Tkinter 入口中已实现的业务流程，但不依赖 Tkinter 控件：

- 单文件上传：`create_container` + `upload_file_with_retry` + `encode_share_text`
- 分片上传：`split_file` + `create_container` + `upload_file_with_retry` + `ChunkManifest` + `save_manifest_file`
- 单文件解析：`decode_share_text` + `download_file` + `extract_container`
- 分片解析：`load_manifest_file` + `download_file` + `extract_container` + `merge_chunks`

如果实现时发现业务流程在 Tkinter 类中重复太多，可以新增 `cloud_drive_service.py` 抽离纯业务逻辑。但第一版可以先在 `flet_app.py` 内封装方法，避免一次性大重构。

## 视觉风格

- 使用深色或浅色现代主题。
- 背景使用浅灰或深灰。
- 主内容使用卡片布局。
- 操作按钮使用主色按钮和次级按钮区分。
- 长文本结果使用只读文本框。
- 高级设置不出现在上传主界面。

## 运行方式

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动新版界面：

```powershell
python flet_app.py
```

旧版界面仍可启动：

```powershell
python image_uploader_gui.py
```

## 测试策略

- 现有核心单元测试必须继续通过。
- 新增 `flet_app.py` 至少通过 `py_compile`。
- 不对 Flet 控件做复杂 UI 单元测试。
- 验证命令：

```powershell
python -m py_compile flet_app.py image_uploader_gui.py oss_client.py share_codec.py stego_container.py settings_store.py chunk_manager.py manifest_codec.py
python -m unittest discover -s tests -v
```

## 风险

- Flet 是新增依赖，用户需要安装依赖后才能运行新版界面。
- Flet 桌面窗口的打包方式不同于 Tkinter，后续如果打包 exe，需要单独处理。
- 当前运行环境可能无法安装或启动 Flet；如果如此，至少保证代码语法检查通过，并保留旧 Tkinter 入口。

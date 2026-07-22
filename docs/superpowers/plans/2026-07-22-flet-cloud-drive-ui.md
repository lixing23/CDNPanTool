# Flet 云盘管理界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个基于 Flet 的现代云盘管理界面入口，替代复杂丑陋的 Tkinter 主界面，同时保留旧入口和现有核心业务模块。

**Architecture:** 新增 `flet_app.py` 作为 UI 编排层，复用现有上传、分片、解析、配置和历史模块。新增 `requirements.txt` 声明 Flet 依赖。旧 `image_uploader_gui.py` 不删除，作为 legacy 入口保留。

**Tech Stack:** Python 3、Flet、threading、queue、现有标准库业务模块、unittest。

---

## File Structure

- Create: `requirements.txt`
  - 声明 `flet>=0.25.0`。
- Create: `flet_app.py`
  - Flet 现代云盘管理 UI。
  - 上传分享、解析文件、历史记录、设置四个页面。
  - 后台线程执行上传和解析任务。
- Existing modules reused:
  - `oss_client.py`
  - `stego_container.py`
  - `share_codec.py`
  - `chunk_manager.py`
  - `manifest_codec.py`
  - `settings_store.py`

---

### Task 1: 新增依赖文件

**Files:**
- Create: `requirements.txt`

- [ ] **Step 1: 写 requirements.txt**

```text
flet>=0.25.0
```

- [ ] **Step 2: 检查文件内容**

Run:

```powershell
Get-Content requirements.txt
```

Expected: 输出 `flet>=0.25.0`。

---

### Task 2: 实现 Flet 入口骨架

**Files:**
- Create: `flet_app.py`

- [ ] **Step 1: 创建 imports、数据模型和常量**

实现 Flet 入口所需 imports、`CloudFileItem` 数据类、路径常量、状态常量。

- [ ] **Step 2: 创建 `FletCloudDriveApp` 类**

类负责初始化 page、settings、queue、items、history、构建布局。

- [ ] **Step 3: 实现左侧导航和页面切换**

导航项：上传分享、解析文件、历史记录、设置。

- [ ] **Step 4: 运行语法检查**

Run:

```powershell
python -m py_compile flet_app.py
```

Expected: 如果环境已安装 flet，语法检查通过；如果未安装，提示缺少 flet，需要先安装依赖。

---

### Task 3: 实现上传分享页

**Files:**
- Modify: `flet_app.py`

- [ ] **Step 1: 实现选择文件按钮**

使用 Flet `FilePicker` 选择多个文件并加入列表。

- [ ] **Step 2: 实现文件列表**

用 `DataTable` 展示文件名、大小、状态。

- [ ] **Step 3: 实现右侧详情**

展示选中文件、直链、分享文本和复制按钮。

- [ ] **Step 4: 实现上传后台线程**

复用单文件和分片上传逻辑。

- [ ] **Step 5: 上传完成后写入历史**

调用 `SettingsStore.add_upload_history`。

---

### Task 4: 实现解析文件页

**Files:**
- Modify: `flet_app.py`

- [ ] **Step 1: 实现分享文本输入框**

使用多行 TextField。

- [ ] **Step 2: 实现解析信息按钮**

调用 `decode_share_text` 展示文件名和大小。

- [ ] **Step 3: 实现选择保存目录**

使用 FilePicker 获取目录。

- [ ] **Step 4: 实现下载并提取线程**

复用单文件和分片解析逻辑。

---

### Task 5: 实现历史记录页和设置页

**Files:**
- Modify: `flet_app.py`

- [ ] **Step 1: 历史记录页**

读取 `upload_history.json`，用 DataTable 展示文件名、大小、上传时间、状态。

- [ ] **Step 2: 设置页**

设置默认封面、分片大小、上传并发、分片伪装开关。

- [ ] **Step 3: 保存设置**

调用 `SettingsStore.save_settings`。

---

### Task 6: 最终验证

**Files:**
- All changed files

- [ ] **Step 1: 安装依赖**

Run:

```powershell
python -m pip install -r requirements.txt
```

Expected: flet 安装成功。

- [ ] **Step 2: 语法检查**

Run:

```powershell
python -m py_compile flet_app.py image_uploader_gui.py oss_client.py share_codec.py stego_container.py settings_store.py chunk_manager.py manifest_codec.py
```

Expected: 通过。

- [ ] **Step 3: 单元测试**

Run:

```powershell
python -m unittest discover -s tests -v
```

Expected: 所有测试通过。

- [ ] **Step 4: 启动检查**

Run:

```powershell
python flet_app.py
```

Expected: 打开现代云盘管理界面。

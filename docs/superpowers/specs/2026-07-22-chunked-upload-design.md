# 分片上传与索引图片设计

## 背景

当前桌面工具已支持将任意文件拼接到封面图片后上传，并生成短分享文本。大文件上传时，当前实现会先生成一个完整伪装图片，再通过单次 multipart 请求上传。文件较大时可能出现 `10054`、连接重置、超时或服务端断开连接。

本次改造目标是引入分片上传：大文件被拆成多个小分片，每个分片独立上传。所有分片链接写入 manifest 清单，再把 manifest 藏入索引图片。最终分享文本仍只指向索引图片，接收方通过索引图片获取分片列表，并发下载后合并还原原文件。

## 目标

- 支持用户自定义分片大小。
- 默认分片大小为 50MB。
- 分片大小可配置范围为 5MB 到 200MB。
- 上传并发数可配置，默认 3，范围 1 到 8。
- 分片上传失败时自动重试。
- 对连接重置、超时、`10054` 等错误执行失败降级。
- 失败降级时将当前分片大小减半，最低降到 5MB。
- 支持“探测最大分片大小”按钮。
- 支持配置分片是否也伪装成图片。
- 默认开启分片伪装图片。
- manifest 索引始终伪装成图片。
- 接收方可解析索引图片、下载所有分片、合并并校验原文件 SHA-256。
- 保留当前小文件单文件上传模式。

## 非目标

- 不实现真正 OSS Multipart Upload API。
- 不实现云端删除分片。
- 不实现断点续传到下次启动。
- 不实现分片级加密。
- 不实现服务端短码。
- 不改变短分享文本的 `xfb1.` 格式。

## 推荐策略

上传模式按文件大小自动选择：

```text
文件大小 <= 当前分片大小
    使用当前单文件伪装图片上传流程

文件大小 > 当前分片大小
    使用分片上传流程
```

这样小文件路径保持简单，大文件自动进入更稳定的分片路径。

分片大小由用户配置，默认 50MB。探测功能只用于给出建议值，不作为每次上传的前置步骤。实际上传仍要有重试和失败降级。

## 配置项

`settings.json` 增加字段：

```json
{
  "default_cover_path": "",
  "chunk_size_mb": 50,
  "upload_workers": 3,
  "stego_chunks": true
}
```

字段规则：

- `chunk_size_mb`：整数，范围 5 到 200。
- `upload_workers`：整数，范围 1 到 8。
- `stego_chunks`：布尔值，表示每个分片是否也伪装成图片。

读取设置时，如果文件不存在、字段缺失或字段类型错误，使用默认值。

## 上传流程

### 小文件流程

1. 用户选择文件。
2. 文件大小小于或等于配置的分片大小。
3. 继续使用现有流程：原文件拼接到封面图片，上传伪装图片，生成短分享文本。

### 大文件流程

1. 用户选择文件。
2. 文件大小大于配置的分片大小。
3. 程序计算原文件 SHA-256。
4. 程序按配置分片大小切分文件。
5. 每个分片生成 metadata，包括分片序号、分片大小、分片 SHA-256。
6. 如果 `stego_chunks=true`，每个分片先拼接到封面图片后再上传。
7. 如果 `stego_chunks=false`，每个分片作为普通二进制文件上传。
8. 使用线程池并发上传分片。
9. 每个分片失败最多重试 3 次。
10. 如果连接重置、超时或 `10054` 持续发生，整个文件使用减半后的分片大小重新切分并上传。
11. 所有分片上传成功后生成 manifest。
12. manifest 写入一个临时 JSON 文件。
13. manifest JSON 拼接到封面图片后生成索引图片。
14. 上传索引图片。
15. 生成短分享文本，短分享文本中的 URL 指向索引图片。

## 解析与下载流程

1. 接收方粘贴短分享文本。
2. 程序解析出 URL。
3. 程序下载该 URL 对应文件。
4. 程序尝试将其作为伪装图片容器解析。
5. 如果 metadata 表示 `mode=chunked_manifest`，程序将提取出的文件视为 manifest JSON。
6. 程序读取 manifest，得到原文件信息和分片列表。
7. 程序按用户配置的并发数下载分片。
8. 如果分片记录中 `stego=true`，先从分片图片中提取分片数据。
9. 如果分片记录中 `stego=false`，直接使用下载到的分片数据。
10. 校验每个分片 SHA-256。
11. 按分片序号合并为原文件。
12. 校验原文件 SHA-256。
13. 保存原文件。

## manifest 格式

manifest JSON 使用 UTF-8 编码，格式如下：

```json
{
  "v": 1,
  "mode": "chunked",
  "name": "big-file.zip",
  "size": 536870912,
  "sha256": "原文件sha256",
  "chunk_size": 52428800,
  "chunk_count": 11,
  "stego_chunks": true,
  "created_at": "2026-07-22T12:00:00+08:00",
  "chunks": [
    {
      "index": 0,
      "size": 52428800,
      "sha256": "分片sha256",
      "url": "https://example.com/part-000.png",
      "stego": true
    }
  ]
}
```

校验规则：

- `v` 必须为 1。
- `mode` 必须为 `chunked`。
- `name` 必须是非空字符串。
- `size` 必须是非负整数。
- `sha256` 必须是 64 位十六进制字符串。
- `chunk_size` 必须是正整数。
- `chunk_count` 必须等于 `chunks` 数量。
- `chunks` 中 index 必须从 0 连续递增。
- 每个 chunk 的 `size`、`sha256`、`url`、`stego` 必须有效。

## 索引图片容器

索引图片使用现有 `stego_container.py` 的容器格式。为了区分普通单文件伪装图片和 manifest 索引图片，manifest JSON 文件名固定为：

```text
xfb_manifest.json
```

接收方解析短分享文本后，下载并提取索引图片。如果提取出的文件名是 `xfb_manifest.json`，就进入分片下载流程。否则按普通单文件提取流程处理。

## 分片文件命名

本地临时分片文件命名：

```text
cache/chunks/<upload_id>/part-000000.bin
cache/chunks/<upload_id>/part-000001.bin
```

分片伪装图片命名：

```text
cache/chunks/<upload_id>/part-000000.png
cache/chunks/<upload_id>/part-000001.png
```

manifest 临时文件：

```text
cache/chunks/<upload_id>/xfb_manifest.json
cache/chunks/<upload_id>/xfb_manifest.png
```

上传成功后可删除对应 `upload_id` 临时目录。

## 失败重试与降级

单个分片上传失败时：

- 最多重试 3 次。
- 每次重试之间等待 1 秒、2 秒、4 秒。
- 可重试错误包括：连接重置、超时、`10054`、HTTP 5xx、网络错误。
- HTTP 4xx 默认不可重试。

如果同一轮分片上传中出现持续连接重置或超过阈值的分片失败：

1. 停止当前上传轮次。
2. 删除本轮临时分片。
3. 将分片大小减半。
4. 如果减半后小于 5MB，则停止并提示失败。
5. 用新的分片大小重新切分和上传。

## 探测最大分片大小

GUI 增加按钮：

```text
探测最大分片大小
```

探测规则：

1. 从当前配置分片大小开始。
2. 使用临时随机数据生成测试文件。
3. 测试上传该大小文件。
4. 成功则尝试更大大小：当前大小乘以 1.5，最高不超过 200MB。
5. 失败则尝试更小大小：当前大小减半，最低 5MB。
6. 得到最大成功值后，建议用户保存为分片大小。

探测上传成功后产生的远程测试文件不会自动删除，因为当前项目没有云端删除接口。GUI 需要在提示中说明探测会产生测试上传文件。

## GUI 改造

上传 Tab 增加设置区域：

```text
分片大小 MB: [50]
上传并发数: [3]
[√] 分片也伪装成图片
[保存设置]
[探测最大分片大小]
```

上传队列状态新增：

- 准备分片
- 上传分片 3/11
- 上传索引图片
- 分片上传成功
- 分片上传失败
- 降低分片大小后重试

解析 Tab 状态新增：

- 下载索引图片
- 读取分片清单
- 下载分片 3/11
- 合并文件
- 校验完成

## 模块划分

新增 `chunk_manager.py`：

- `split_file(source_path, output_dir, chunk_size)`
- `sha256_file(path)`
- `sha256_bytes(data)`
- `merge_chunks(chunk_paths, output_path, expected_sha256)`
- `cleanup_dir(path)`

新增 `manifest_codec.py`：

- `ChunkRecord`
- `ChunkManifest`
- `create_manifest(...)`
- `manifest_to_json_file(...)`
- `load_manifest_file(...)`
- `validate_manifest(...)`

增强 `oss_client.py`：

- 流式构造 multipart body，降低内存占用。
- `upload_file_with_retry(file_path, retries=3)`。
- `is_retryable_upload_error(exc)`。

增强 `settings_store.py`：

- `AppSettings` 增加 `chunk_size_mb`、`upload_workers`、`stego_chunks`。
- 加入范围归一化。

增强 `image_uploader_gui.py`：

- 增加分片设置 UI。
- 根据文件大小选择单文件上传或分片上传。
- 增加分片上传 worker。
- 增加分片下载与合并 worker。

## 测试策略

使用 Python 标准库 `unittest`。

需要新增测试：

- settings 读取缺失字段时使用默认分片设置。
- settings 对非法分片大小和非法并发数做归一化。
- chunk_manager 能正确切分和合并文件。
- chunk_manager 能校验合并后 SHA-256。
- manifest_codec 能生成和加载 manifest。
- manifest_codec 拒绝 chunk_count 与 chunks 数量不一致的 manifest。
- manifest_codec 拒绝 index 不连续的 manifest。
- oss_client 能识别 10054、timeout、HTTP 5xx 为可重试错误。

## 兼容性

已有普通单文件分享文本继续可用。解析时先下载分享文本指向的图片并提取内容：

- 提取出普通文件：按当前单文件流程保存。
- 提取出 `xfb_manifest.json`：按分片流程下载、合并、校验。

## 风险与限制

- 分片越小，上传链接越多，manifest 越大。
- 分片都伪装成图片会增加总上传体积，因为每个分片都会重复带一份封面图片。
- 探测最大分片大小会产生远程测试文件，目前无法自动删除。
- 如果某些分片链接失效，整个原文件无法恢复。
- 如果分享文本泄露，持有工具的人可以下载所有分片并还原文件。

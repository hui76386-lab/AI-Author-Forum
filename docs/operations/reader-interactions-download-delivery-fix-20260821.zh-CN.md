# PDF 下载交付权限故障修复记录

## 1. 故障与根因

- 时间：2026-08-21（Asia/Shanghai）
- 环境：`ai-author-forum-test`，`https://test-author.huixixi.top`
- 表现：第一次打开下载授权 URL 返回浏览器错误，再次访问显示 `download_grant_expired`。
- 根因：filesystem protected storage 通过临时文件硬链接生成 PDF，但最终文件继承了临时文件的 `0600` 权限。Django 已消费一次性 grant 并返回 `X-Accel-Redirect`，随后 Nginx worker 因 `Permission denied` 无法读取 PDF；浏览器重试时 grant 已消费，所以 API 返回 410。

## 2. 修复

- `ai_author_forum/reader_access/protected_storage.py`：新对象在原子链接前设置为 `0644`；已存在且内容一致的对象会自动修复读取权限。
- `ai_author_forum/reader_access/tests/test_pdfs.py`：覆盖新对象权限和既有 `0600` 对象的自动修复。
- 对活动 protected manifest 中的 1213 个对象逐一校验大小和 SHA-256 后修正权限，不重写 PDF 内容。
- 文件仍位于非公开 Docker volume；Nginx `/_protected_pdf/` 保持 `internal`，直接访问返回 404，实际下载仍要求已验证 reader session 和一次性 grant。

## 3. 发布与审计

- 活动静态 release：`20260820T110301037817Z-job111`（未改变）
- protected manifest SHA-256：`0f2142892ace3ea70d84c97d3199f6678092b14f9ad322917ba33121e520967f`（未改变）
- 权限修复：1213/1213，修复后非 `0644` 文件数为 0。
- 审计记录：`AuditLog #18278`，状态 `success`。
- 本次仅修复受保护文件的交付权限和后续生成逻辑，没有修改公共静态内容，因此不生成新的静态 release。

## 4. 验证结果

| 检查 | 结果 |
| --- | --- |
| `python manage.py check` | 通过 |
| `python manage.py makemigrations --check --dry-run` | 通过，无模型变更 |
| PDF 与下载测试 | 12 项通过，1 项按环境跳过 |
| 真实 grant API | HTTP 201 |
| 真实 Nginx 下载 | HTTP 200，`application/pdf`，49311 bytes，文件头 `%PDF` |
| 私有对象直接访问 | HTTP 404 |
| `/healthz/`、`/readyz/`、`/__static_health__/` | 全部 HTTP 200 |
| 临时 smoke session | 全部撤销 |
| interactions 测试权限 | `rolcreatedb=false` |


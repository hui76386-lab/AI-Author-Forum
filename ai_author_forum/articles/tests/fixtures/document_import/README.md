# 文档导入测试夹具

本目录对应《DOCX / Markdown 文章一键导入开发任务书》第 23.6 节。所有“危险”夹具仅包含用于触发结构检测的纯文本占位内容，不含宏、ActiveX、OLE 可执行载荷或真实恶意代码。

## 生成方式

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe ai_author_forum/articles/tests/fixtures/document_import/generate_fixtures.py
```

生成器使用 Python 标准库 `zipfile` 构造最小 Open XML 包，并使用 Pillow 生成 32×24 的无害 PNG。`package-mixed.zip` 由生成器打包 DOCX、Markdown、HTML 和 UTF-8-SIG CSV 清单。每次生成后，脚本会输出所有交付夹具的 SHA-256。

## 文件说明与 SHA-256

| 文件 | 用途 | SHA-256 |
|---|---|---|
| `minimal.docx` | 最小合法 DOCX | `2b7d46d656cd9363d2ed40a03464210dea874fd1e0922a9e4df9b38c9f7ab0f1` |
| `formatted.docx` | 标题、强调、列表、链接、表格 | `0cf771ef2b7a66916e6ab3f8d17ae688ea79733cdb585e7f056644971b580075` |
| `embedded-images.docx` | 内嵌 PNG | `3e46b78c47ac662d4f5e6f659af11abb3162534ab7885a4d05c62f5ce187dae7` |
| `revisions.docx` | 修订和隐藏文本警告 | `803f46d09dade7e89eb5ff67f42eac8bea243c4d841c27e181a97a1b670a98dc` |
| `unsafe-external-image.docx` | 外部图片关系拒绝 | `d3d353db44b47e8f2f371613ba3ea66e2842265ff11d7e16019159a7ae467e0f` |
| `unsafe-embedded-object.docx` | 无害 OLE 路径占位成员拒绝 | `903e74a00974ffce97c5e792a54c97da3cebd9dc3364c1131cab8ddfe852d0a4` |
| `valid.md` | 合法 UTF-8 Markdown | `a2f179a282b914fc8a4f3fc66c0c1f82ba6ebcbe67f33657236d855726c6fc8f` |
| `front-matter.md` | 合法 Front Matter | `ada6a7c482e9606f6e079894b62a6619c629d3d4312a3db831ab15b346e4665b` |
| `unsafe-html.md` | 原始脚本 HTML 进入公共 HTML 安全校验的夹具 | `699ef21e98e0f855cbbebfc0e7a952a27826fbc7f957277dc86b3772ccfad66b` |
| `package-mixed.zip` | DOCX、Markdown、HTML 混合批量包 | `2787246651091fb2e85b9e4352eefd099dfd1075530328c94f7a0ac486212e72` |

ZIP 元数据时间戳会影响二进制 SHA-256；交付文件的哈希以上表为准。若主动重新生成夹具，应同步更新本表并复核差异。

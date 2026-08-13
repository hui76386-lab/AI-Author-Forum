# AI Author Forum 项目协作规约

本文档适用于本项目根目录及其所有子目录。所有自动化代理和开发人员都必须遵守本文件；更具体的子目录 `AGENTS.md` 可以补充规则。

## 1. 开发与验证要求

修改代码后，至少根据变更范围完成相关检查；涉及发布、权限、模型或跨模块接口时，不能只依赖单个简单检查。

最低要求：

1. 运行 `python manage.py check`。
2. 涉及模型时运行 `python manage.py makemigrations --check --dry-run`，并提交对应迁移文件。
3. 运行受影响的单元/集成测试；发布前运行完整测试集和 E2E 测试。
4. 涉及前端或静态发布时，验证桌面端和移动端页面、关键资源、链接、健康检查及发布目录内容。
5. 记录验证命令、结果、版本标识和已知限制，确保发布时可以追溯。

本项目的正式数据链路必须保持为：

```text
导入暂存 -> canonical ArticlePage 草稿/revision -> 审核 -> 正式投放
  -> 冻结构建快照 -> 不可变 manifest -> current 原子激活
```

不得绕过审核、正式投放、manifest 校验或审计日志，把兼容模型、草稿或测试数据直接送到正式前台。

## 2. 静态发布与回滚规则

站点前台由 Nginx/CDN/静态服务器直接服务已激活目录，普通前台请求不能依赖 Django 实时查询文章数据库。每次发布必须生成不可变 release 和 manifest：

- 构建输入必须冻结；构建失败不能激活新版本。
- 所有必需页面和资源校验通过后才允许切换 `current`。
- `current` 切换必须是原子动作；不得先删除旧版本再写新版本。
- 激活、失败、重试和回滚都必须写入 `AuditLog`。
- 已激活 manifest 不得原地修改；修复必须产生新 release。
- 回滚只能切换到已验证且完整的旧 manifest，不得把数据库内容静默改回任意历史状态。

## 3. 变更纪律

- 优先做范围清晰、可验证、可回滚的最小修改；不要顺手重构无关代码或覆盖用户已有改动。
- 发现当前工作区存在不明变更时，先读取并理解相关文件，不得使用 `git reset --hard`、`git checkout --` 或其他破坏性操作清理现场。
- 修改模型、权限、状态机、跨模块接口、发布流程或环境配置时，同步更新对应的 `docs/` 文档和测试。
- 最终报告必须说明修改文件、验证命令及结果；未能完成的验证要明确写出。

## 4. 相关文档

- `README.md`：本地启动、模块边界、环境变量和基础检查。
- `docs/cms-development/01-system-architecture.zh-CN.md`：架构与正式模型边界。
- `docs/cms-development/02-core-business-workflows.zh-CN.md`：导入、审核、投放、静态发布和回滚流程。
- `docs/cms-development/05-static-publishing-development-and-operations.zh-CN.md`：静态发布完整性与审计。

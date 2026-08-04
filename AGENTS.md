# AI Author Forum 项目协作与环境规约

本文档适用于本项目根目录及其所有子目录。所有自动化代理和开发人员都必须遵守本文件；更具体的子目录 `AGENTS.md` 可以补充规则，但不能放宽正式环境安全要求。

## 1. 两台服务器的明确关系

| 环境 | 身份 | 用途 | 允许的操作 |
| --- | --- | --- | --- |
| 当前服务器/当前项目目录 | 测试与开发服务器 | 编码、联调、测试、构建和发布前验收 | 可进行项目开发所需的高权限操作，包括修改代码和配置、安装依赖、运行迁移、构建静态站点、启动/停止本地服务和使用测试数据 |
| `64.90.31.87` | 正式服务器 | 运行已验证的正式项目和正式数据 | 仅接收已经完成验证的发布产物，并按正式部署流程发布、检查或回滚 |

这两台服务器不是同一个环境：

- 当前服务器是变更发生和验证的地方，不代表正式环境状态。
- `64.90.31.87` 是正式环境，正式数据和正式服务不能当作测试资源使用。
- 当前服务器权限较高，只表示开发环境便于调试，不表示可以直接修改正式服务器。
- 能够通过网络访问 `64.90.31.87` 不等于可以执行生产操作。任何生产操作都必须先完成本文件规定的发布前检查。
- 不得把测试数据库、测试媒体、测试账号、日志、`.env` 文件或本机临时产物直接上传到正式服务器。

## 2. 环境识别与命令边界

执行命令前先确认当前目录和环境；不要仅凭目录名、端口或 SSH 连接结果猜测环境。需要操作远程主机时，先使用只读命令核对主机名、IP 和部署目录，再执行后续命令。

开发/测试服务器上可以按项目文档进行以下操作：

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest
python manage.py build_static_site
npm run build:prod
npm run test:e2e
```

实际命令应以当前 Python、Node、Docker 和项目配置为准。涉及数据库迁移、批量导入、清理测试数据或重启服务时，确认目标是测试环境，并保留可诊断的输出。

对 `64.90.31.87`：

- 开发、调试、试验性修改和未经验证的代码禁止直接执行。
- 禁止直接在正式机上编辑代码、临时改配置、安装未验证依赖、运行测试数据导入或使用 `DEBUG` 模式。
- 禁止删除正式数据库、媒体、发布目录、Docker volume、日志或备份来“解决”部署问题。
- 禁止把本地 `published/`、`output/`、SQLite 数据库或完整工作目录当作正式发布源。
- 未明确得到发布指令时，不执行 `ssh`、`scp`、`rsync`、`docker compose up`、数据库迁移或正式服务重启等生产变更操作。

## 3. 开发与验证要求

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

## 4. 正式上传与发布门槛

上传到 `64.90.31.87` 只允许使用已经在当前测试/开发服务器完成验证的明确版本。一次正式上传必须能够回答以下问题：

- 发布版本号、来源提交或源码归档的 SHA-256 是什么？
- 该版本通过了哪些检查和测试？是否包含迁移？
- 目标正式服务、数据库、Redis/Celery、媒体和静态发布目录是什么？
- 失败时回滚到哪个已验证版本？回滚依据的 manifest 是否仍然完整？
- 本次变更是否会影响正式数据、后台权限、域名、HTTPS 或缓存？

正式发布流程：

1. 在测试/开发服务器冻结待发布版本，清理临时文件，并确认没有把密钥、`.env`、数据库、媒体、日志、测试报告或未跟踪构建产物混入发布包。
2. 运行与变更相关的完整检查，构建正式镜像或发布归档，生成版本号、文件清单和 SHA-256。
3. 在测试环境按接近正式环境的方式验证 Docker、PostgreSQL、Redis/Celery、Gunicorn、Nginx、静态发布、健康检查和关键用户流程。
4. 只有验证通过后，才将该版本上传到 `64.90.31.87` 的受控发布入口；优先上传不可变归档或镜像，不直接覆盖正式运行目录。
5. 正式机先保存当前活动版本和回滚信息，再进行部署。生产配置和密钥只从正式机的 secret manager 或受保护环境变量读取，不能由代码仓库覆盖。
6. 正式发布使用独立 release 目录，完成文件清单、路径、大小、SHA-256 和 manifest 校验后，才原子切换 `current`。
7. 发布后检查健康接口、后台登录、首页、文章详情、栏目页、子期刊页、静态资源和关键 API，并确认审计记录完整。
8. 任一关键检查失败，保留现场和日志，停止继续变更；按已验证 manifest 回滚，不删除旧版本后再重建。

推荐的上传形式（变量必须由操作者在受控环境中提供，不能写入仓库）：

```bash
PROD_HOST=64.90.31.87
PROD_USER='<正式服务器发布账号>'
RELEASE_ARCHIVE='<已验证发布归档>'

# 仅在版本已通过本文件规定的验证，并且明确执行正式发布时使用。
sha256sum "$RELEASE_ARCHIVE"
scp "$RELEASE_ARCHIVE" "$PROD_USER@$PROD_HOST:<正式发布入口>/"
```

上面的示例不是完整部署脚本；正式服务器的账号、目录、服务名和重启方式必须以正式运维配置为准，不得凭猜测执行。

## 5. 生产数据与配置安全

- 任何真实凭据、私钥、token、生产数据库 URL、生产媒体和备份都不得提交到 Git 或写入 `AGENTS.md`。
- 生产与测试必须使用不同的数据库、缓存、Celery、对象存储、域名和密钥；禁止混用连接串。
- 生产环境必须关闭调试模式，启用 HTTPS、安全 Cookie、HSTS 和正确的 `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`。
- 生产数据库迁移必须先在测试环境验证，并在正式发布记录中说明迁移顺序、耗时和回滚限制。
- 不要为了验证功能读取、复制或下载不必要的正式数据。排查问题时优先使用脱敏数据和健康检查。
- 删除、覆盖或迁移正式数据前，必须明确目标、确认备份和恢复方式；异常情况下优先停止服务或回滚版本，而不是破坏现场。

## 6. 静态发布与回滚规则

生产前台由 Nginx/CDN/静态服务器直接服务已激活目录，普通前台请求不能依赖 Django 实时查询文章数据库。每次发布必须生成不可变 release 和 manifest：

- 构建输入必须冻结；构建失败不能激活新版本。
- 所有必需页面和资源校验通过后才允许切换 `current`。
- `current` 切换必须是原子动作；不得先删除旧版本再写新版本。
- 激活、失败、重试和回滚都必须写入 `AuditLog`。
- 已激活 manifest 不得原地修改；修复必须产生新 release。
- 回滚只能切换到已验证且完整的旧 manifest，不得把数据库内容静默改回任意历史状态。

## 7. 变更纪律

- 优先做范围清晰、可验证、可回滚的最小修改；不要顺手重构无关代码或覆盖用户已有改动。
- 发现当前工作区存在不明变更时，先读取并理解相关文件，不得使用 `git reset --hard`、`git checkout --` 或其他破坏性操作清理现场。
- 修改模型、权限、状态机、跨模块接口、发布流程或环境配置时，同步更新对应的 `docs/` 文档和测试。
- 最终报告必须说明修改文件、验证命令及结果；如果未执行正式上传或未能完成某项验证，要明确写出，不得把测试环境结果描述为生产已发布。

## 8. 相关文档

- `README.md`：本地启动、模块边界、环境变量和基础检查。
- `docs/cms-development/01-system-architecture.zh-CN.md`：架构与正式模型边界。
- `docs/cms-development/02-core-business-workflows.zh-CN.md`：导入、审核、投放、静态发布和回滚流程。
- `docs/cms-development/05-static-publishing-development-and-operations.zh-CN.md`：静态发布完整性、审计和生产检查。
- `docker-compose.production.yml`：生产服务拓扑及外部依赖要求。

## 9. 当前测试服务器开发完成后立即应用

本节只适用于当前测试服务器和 Compose 项目 `ai-author-forum-test`，不适用于正式服务器 `64.90.31.87`。功能代码在当前工作区完成并通过对应检查后，应立即将当前工作区构建为测试环境正在使用的最新版本，供浏览器验收；不得把未验证代码直接作为正式版本发布。

统一使用当前测试环境的受控命令，不要省略 Compose 项目名、测试环境文件或生产配置基文件：

```bash
cd /opt/ai-author-forum-test-current

compose_test() {
  docker compose \
    --project-name ai-author-forum-test \
    --env-file /opt/ai-author-forum-test-shared/.env.test \
    -f docker-compose.production.yml \
    -f docker-compose.test.yml \
    "$@"
}

compose_test config >/dev/null
compose_test build web worker static-frontend
compose_test up -d web worker static-frontend nginx
compose_test exec web python manage.py check
compose_test exec web python manage.py makemigrations --check --dry-run
```

- 涉及数据库模型时，必须先完成测试数据库迁移，并确认迁移命令只作用于 `ai-author-forum-test`；容器启动时的自动迁移不能替代迁移检查。
- 涉及 Django 静态资源时，运行 `compose_test exec web python manage.py collectstatic --noinput`，然后确认 Web 和静态前台健康。
- 涉及静态前台模板、文章页面、栏目页、期刊页或投放内容时，完成审核、正式投放和 manifest 校验后，再运行 `compose_test exec web python manage.py build_static_site`；不得绕过正式数据链路直接写入前台目录。
- 应至少检查 `compose_test ps`、`/healthz/`、后台登录入口和本次变更对应页面。测试站点的浏览器验收入口为 `https://test-author.huixixi.top/`，截图涉及的页面为 `https://test-author.huixixi.top/en/journals/representation-learning/` 和 `https://test-author.huixixi.top/admin/placements/`。
- 交付报告必须说明已应用的测试版本、执行过的检查、浏览器验收地址和未执行的项目。除非用户明确给出正式发布指令，否则不得执行面向 `64.90.31.87` 的上传、远程登录或生产重启。

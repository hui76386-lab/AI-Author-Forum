# AI Author Forum CMS 基础工程

这是基于 Wagtail News Template 实例化的 AI Author Forum CMS。当前工程由负责人 A 维护技术底座，保证子期刊、文章管理、文章审核、投放、版位和静态发布模块在同一套 Wagtail 架构下协作。

## 本地启动

安装完成后，推荐使用一个命令同时启动 Django 和静态前台：

```powershell
cd "E:\AI Author Forum\news-template"
.\.venv\Scripts\python.exe scripts\start_dev.py

???????? Django???????? Celery worker???????????????????????? Redis???? Redis ?????????? `.env.example` ? `.env`????? Celery Redis URL ??? URL ????????
```

启动后：

- Wagtail 后台和动态预览：`http://127.0.0.1:8000/`
- 静态前台：`http://127.0.0.1:4173/`
- 文章静态页面示例：`http://127.0.0.1:4173/articles/live-demo-journal-001-article-001/`

按 `Ctrl+C` 会同时停止两个服务。不要直接双击 `published/current` 下的 `index.html`；静态 HTML 需要通过 4173 的 HTTP 静态服务器访问，CSS、JavaScript 和图片才能按 `/static/...` 路径加载。

首次安装和初始化：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_navigation
python manage.py seed_roles
```

创建管理员：

```powershell
python manage.py createsuperuser
```

首次初始化可以运行 `python manage.py dev_setup`，但正式验收前应确认已执行 `seed_navigation` 和 `seed_roles`。

修改后台内容后，需要重新生成静态发布版本：

```powershell
.\.venv\Scripts\python.exe manage.py build_static_site
```

然后刷新 4173 端口的前台页面。

如只需启动单个服务，仍可使用：

```powershell
python manage.py runserver                  # 仅 Django，8000
python -m ai_author_forum.static_publish.static_server --root .\published --port 4173  # 仅静态前台
```

## 工程边界

```text
ai_author_forum/
  settings/          Django 环境、数据库、静态文件和存储配置
  home/              News Template 主页兼容层
  journals/          子期刊模块接入边界，由 B 实现业务模型
  articles/          文章和审核模块接入边界，由 C 实现业务模型
  placements/        投放和版位模块接入边界，由 D 实现业务模型
  static_publish/    静态发布模块接入边界，由 E 实现业务模型
  site_settings/     主站配置、导航、角色预设和审计日志
```

A 只提供模块目录、后台入口、权限基线、基础配置、审计接口和合并约束，不代做 B–E 的业务模型和静态生成逻辑。

## B 模块验收命令

子期刊批量导入、AI Article 静态 HTML 导入、主站/子期站点投放和统一静态发布可用以下命令做重复验收：

```powershell
python manage.py seed_journal_demo_data --journals 120 --articles-per-journal 100
python manage.py seed_journal_demo_data --journals 120 --articles-per-journal 125
python manage.py seed_journal_demo_data --publish-static-site --operator-id 1
```

第一条覆盖 120 个子期站点、每站 100 篇文章；第二条覆盖 15,000 篇文章通道；第三条在导入成功后进入统一静态发布中心。AI Article 文章以 HTML 静态页面形式固定生成，线上不走后台文章详情入库查询，也不建设真实搜索系统。详见 `docs/journal-import-demo-operations.zh-CN.md`。

## 后台菜单

- `子期刊`：`journals` 接入入口，需要 `site_settings.access_journals`。
- `文章管理`：`articles` 内容入口，需要 `site_settings.access_articles`。
- `文章审核`：`articles` 审核入口，需要 `site_settings.access_article_review`。
- `投放管理`：`placements` 投放入口，需要 `site_settings.access_placements`。
- `版位编排`：受控版位入口，需要 `site_settings.access_slots`。
- `静态发布`：`static_publish` 发布入口，需要 `site_settings.access_static_publish`。
- `设置 > 主站配置`：Wagtail Site Settings 中维护站点名称、Logo、SEO、默认图片和静态输出根目录。
- `设置 > 导航基线`：维护四大导航及子菜单；核心导航写操作要求 `site_settings.manage_core_navigation`。
- `设置 > 角色权限预设`：查看标准角色与 Wagtail Group 的映射。
- `设置 > 审计日志`：只读查看发布、导入、回滚和重试记录。

## 标准角色

`seed_roles` 会创建以下 Wagtail 用户组：

| 角色 | 菜单与能力边界 |
| --- | --- |
| 超级管理员 | 全局配置、用户权限、发布和回滚 |
| 内容管理员 | 文章编辑、栏目编辑、文章投放和预览 |
| 审核人员 | 文章审核、驳回和审核意见 |
| 站点运营 | 子期刊资料、素材和 SEO 配置 |
| 发布管理员 | 静态生成、发布、重试和回滚 |
| 只读人员 | 查看配置、发布记录和审计日志 |

角色组只定义权限，不自动把用户加入组；用户需要由超级管理员在 Wagtail 用户管理中分配用户组，并设置为可登录后台的用户。

## 跨模块接口

- B 提供 `get_active_journals()`、`get_journal_context(slug)`。
- C 提供 `get_approved_articles()`、`get_article_context(slug)`。
- D 提供 `get_slot_items(slot_code, journal=None)`。
- E 通过 `site_settings.services.record_audit_event()` 回写发布状态和审计日志。

高风险动作示例：

```python
from ai_author_forum.site_settings.models import AuditAction, AuditStatus
from ai_author_forum.site_settings.services import record_audit_event

record_audit_event(
    request=request,
    action=AuditAction.PUBLISH,
    status=AuditStatus.SUCCESS,
    target=publish_job,
    message="静态发布完成",
    metadata={"manifest": manifest_path},
)
```

`AuditLog` 创建后不可修改或删除；失败、重试和回滚也必须分别写入记录。

## 环境变量

- `DJANGO_SETTINGS_MODULE`：默认开发配置为 `ai_author_forum.settings.dev`。
- `SECRET_KEY`：生产环境必填。
- `DATABASE_URL`：可配置 PostgreSQL，未配置时开发环境使用 SQLite。
- `CACHE_BACKEND`, `CACHE_LOCATION`: development may use local memory cache; production must use shared Redis cache.
- `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`：逗号分隔的主机和来源列表。
- `MEDIA_ROOT`、`STATIC_ROOT`、`STATIC_PUBLISH_ROOT`：媒体、静态资源和静态站点输出目录。
- `AWS_STORAGE_BUCKET_NAME` 或 `DEFAULT_STORAGE_BUCKET`：启用 S3 兼容对象存储。
- `WAGTAILADMIN_BASE_URL`：后台通知使用的站点根地址。
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`: task queue and result storage; production must use remote Redis.
- `STATIC_PUBLISH_HEALTHCHECK_BROKER`：在 `/readyz/` 中启用任务队列检查。

## 生产静态发布

Production uses remote PostgreSQL, remote Redis/Celery, Gunicorn, and Nginx; the production Compose file does not start local database or Redis services.
```powershell
Copy-Item .env.production.example .env.production
# Fill the remote DATABASE_URL, CACHE_LOCATION, and CELERY_* URLs in the private server env file
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
docker compose --env-file .env.production -f docker-compose.production.yml exec web python manage.py build_static_site
```

The local middleware overlay is only for isolated acceptance:

```powershell
Copy-Item .env.production.local.example .env.production.local
docker compose --env-file .env.production.local `
  -f docker-compose.production.yml `
  -f docker-compose.local-middleware.yml up -d --build
```

See the remote middleware deployment guide for validation and network isolation:
`docs/remote-middleware.zh-CN.md`; never commit real credentials to Git-tracked files.

Nginx 仅将后台和健康检查请求转发给 Django，普通前台请求直接读取
`STATIC_PUBLISH_ROOT/current`。详细发布、回滚和灾备步骤见
`docs/static-publishing-operations.zh-CN.md`。

## 统一开发文档

针对架构边界、导入/审核/投放/静态发布闭环、后台菜单与权限、状态机、当前冲突和待确认需求，统一阅读：

- [`docs/cms-development/README.zh-CN.md`](docs/cms-development/README.zh-CN.md)

该目录是持续开发的文档入口；模型、菜单、权限、状态或发布流程发生变化时，应同步更新对应章节。
## 开发规范

```powershell
python manage.py makemigrations --check --dry-run
python manage.py check
python -m pytest ai_author_forum/site_settings/tests -q
ruff check ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish
black --check ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish
isort --check-only ai_author_forum/site_settings ai_author_forum/articles ai_author_forum/journals ai_author_forum/placements ai_author_forum/static_publish
pre-commit run --all-files
```

数据库模型变更必须提交迁移；业务应用不得绕过 Wagtail Group、模型权限和 `AuditLog`。

# AI Author Forum CMS 开发文档总览

> 文档版本：v1.3
> 基线日期：2026-08-07
> 适用工程：`E:\AI Author Forum\news-template`
> 文档性质：开发、联调、评审和发布的统一约定

## 1. 文档目的

本目录把项目现有的需求文档、代码实现和近期审计结论整理成一套可以持续维护的开发文档。后续新增模型、后台菜单、权限、导入流程、投放逻辑或静态发布逻辑时，应先更新对应文档，再修改代码。

本项目的核心业务闭环是：

```text
子期刊资料/文章导入
  -> 文章正式模型草稿
  -> Wagtail 审核
  -> ArticlePlacement 投放
  -> LayoutSlot 版位编排
  -> 静态 HTML 构建
  -> manifest 激活
  -> 前台只读静态页面
```

## 2. 文档导航

| 文档 | 用途 |
| --- | --- |
| [01-系统架构与模块边界](./01-system-architecture.zh-CN.md) | 说明应用职责、模型归属、数据流和依赖方向 |
| [02-核心业务流程](./02-core-business-workflows.zh-CN.md) | 说明导入、审核、投放、编排、发布、回滚的标准流程 |
| [03-后台菜单与权限](./03-admin-menu-and-permissions.zh-CN.md) | 统一后台导航、角色、用户组、菜单和高风险操作权限 |
| [04-数据模型与状态机](./04-data-models-and-state-machines.zh-CN.md) | 说明正式模型、兼容模型、状态字段和合法状态组合 |
| [05-静态发布开发与运维](./05-static-publishing-development-and-operations.zh-CN.md) | 说明构建、manifest、切换、失败、重试、回滚和生产边界 |
| [06-当前冲突与整改路线](./06-current-conflicts-and-remediation-roadmap.zh-CN.md) | 记录本次扫描发现的问题、优先级、影响和整改顺序 |
| [07-待确认需求与验收标准](./07-open-decisions-and-acceptance-criteria.zh-CN.md) | 记录尚未明确的业务决策和模块验收标准 |
| [08-账号、子期刊编辑角色与审核开发任务书](./08-account-and-editorial-access-development-spec.zh-CN.md) | 定义超级管理员、主编辑、常务副编辑、副编辑、账号创建、两级审核、编辑团队和作者声明 |
| [09-作者投稿角色开发任务书](./09-author-submission-role-development-spec.zh-CN.md) | 定义作者账号、对象级投稿关系、作者工作台、投稿状态、审核意见和投稿权限 |
| [10-作者投稿实现与运维说明](./10-author-submission-implementation-and-operations.zh-CN.md) | 说明作者投稿功能的代码入口、迁移边界、启用条件和发布验收 |
| [11-统一登录入口与作者工作台权限隔离开发任务书](./11-unified-login-author-workbench-development-spec.zh-CN.md) | 定义统一品牌登录入口、中立首次改密、作者工作台边界、双角色路由和重定向循环验收 |
| [简化账号与子期刊角色迁移和回滚手册](../operations/simple-role-migration-and-rollback.zh-CN.md) | 说明显式映射、dry-run、幂等应用、旧授权快照和不删除新业务记录的回滚步骤 |
| [远程中间件接入与生产部署](../remote-middleware.zh-CN.md) | 说明远程 PostgreSQL、Redis、Celery 的配置、隔离、本地 overlay 和验收 |

## 3. 权威来源与优先级

发生冲突时，按以下顺序处理：

1. `AGENTS.md` 中的不可破坏业务口径和权限基线；
2. 本目录中的开发约定；
3. `cms-wagtail-core-business-design.zh-CN.md` 和 `cms-wagtail-5-person-workplan.zh-CN.md`；
4. 现有代码和迁移；
5. 历史审计文档、演示数据和截图。

如果代码与文档不一致，不要默默选择一方。必须在 issue、变更记录或本目录的整改文档中记录差异，并明确是“修正文档”还是“修正实现”。

## 4. 当前实现基线

当前工程已完成本轮 P0/P1 收口：导入会事务性进入唯一正式 `ArticlePage` 草稿；账号和权限统一为平台超级管理员及按期刊任命的主编辑、常务副编辑、副编辑；Workflow 固定为初审和主编辑终审；正式投放统一使用 `placements.ArticlePlacement`；投放同步具备 revision/计划级幂等；静态构建使用冻结输入快照，并在激活、重试和回滚时强制执行不可变 manifest、文件完整性和审计一致性。旧角色、旧模型仍仅作为迁移或只读兼容边界保留，不再提供业务授权，尚未物理删除。

当前数据库审计快照：

| 对象 | 数量/状态 |
| --- | ---: |
| 正式 `articles.ArticlePage` | 3 |
| 导入/兼容 `journals.StaticArticle` | 33 |
| 旧 `news.ArticlePage` | 0 |
| 正式 `placements.ArticlePlacement` | 6 |
| 旧 `journals.ArticlePlacement` | 0 |
| `Journal` | 152 |
| `JournalCategory` | 2 |
| `LayoutSlot` | 17 |
| `StaticManifest` | 9 |
| `StaticPublishJob` | 10 |
| `AuditLog` | 89 |

迁移 `articles/0007_repair_article_approval_and_sync_state.py` 已修复本地审计发现的 3 篇异常正式文章：补齐审核版本、同步 revision、同步状态和幂等 request id。其他环境必须在部署时执行迁移并复核结果；本地修复不等同于生产数据已经迁移。

## 5. 开发规则

- 正式文章唯一使用 `ai_author_forum.articles.models.ArticlePage`。
- 正式投放唯一使用 `ai_author_forum.placements.models.ArticlePlacement`。
- `journals.StaticArticle` 只作为导入暂存或兼容源；不能绕过审核直接被当作正式前台文章。
- `news.ArticlePage`、`journals.ArticlePlacement` 等旧模型不得新增业务引用，迁移前只允许只读兼容。
- 文章“审核通过”不等于“前台发布”；必须存在有效投放，并经过静态构建和 manifest 激活。
- 初审和终审必须通过统一 service、有效 `JournalEditorAssignment` 和固定 revision 判定；旧 Group、直接审核 permission 和 `is_superuser` 不得旁路主编辑终审。
- 所有导入、审核、投放同步、静态发布、重试、回滚等高风险操作必须写入 `AuditLog`。
- 前台生产请求不能依赖文章数据库实时查询，只读取已激活的静态目录。
- 新增后台入口必须先确定归属、菜单位置、权限 codename、角色可见范围和审计要求。
- 新增或修改模型必须同时提交迁移、测试、权限说明和本目录相关文档更新。

## 6. 开发检查清单

每个跨模块改动至少检查：

- [ ] 是否使用了正式模型，而不是旧模型或暂存模型；
- [ ] 是否有明确的状态迁移和非法组合处理；
- [ ] 是否经过统一权限和角色组；
- [ ] 是否留下审计日志；
- [ ] 是否影响静态 manifest、current 激活或回滚；
- [ ] 是否有重复 hook、重复菜单或重复后台操作入口；
- [ ] 是否增加数据库迁移；
- [ ] 是否增加模型、服务和端到端测试；
- [ ] 是否更新本目录文档和待确认需求记录。

## 7. 2026-07-23 本轮验收快照

- Django system check、迁移漂移检查、Ruff、Black、isort：通过；
- pytest：`269 passed, 2 skipped, 136 subtests passed`；两项 skip 为必须使用 PostgreSQL 才能验证的行锁/并发约束；
- 前端生产构建：通过，保留图片体积和 Browserslist 数据过期两项非阻塞警告；
- Playwright 固定 HTML 验收：`23 passed`，覆盖 17 个静态页面、manifest、301、回滚、导航和本地静态资源；
- production settings 下 `manage.py check --deploy`：通过；
- 当前机器未安装 Docker，因此尚未完成 PostgreSQL/Redis/Celery/Nginx 联合验收，SQLite 结果不得表述为生产验收通过。

D-01～D-10 仍保持“待产品/项目负责人确认”；代码采用 07 文档规定的默认保守行为，不以工程实现替代产品决策。

# 负责人 A：Wagtail 技术底座交付说明

## 交付范围

本次底座实现完成以下内容：

1. 将 News Template 实例化为 `ai_author_forum` Django 项目包，修正模板归档中的重复 `__init__.py` 文件，保证 Django 可以正常加载应用。
2. 新增 `journals`、`articles`、`placements`、`static_publish`、`site_settings` 五个协作边界，B–E 可以在各自应用内添加业务模型、迁移和服务。
3. 新增 `SiteSettings`、`NavigationItem`、`AdminRolePreset`、`AuditLog` 四个底座模型。
4. 通过 Wagtail ViewSet 注册子期刊、文章管理、文章审核、投放管理、版位编排和静态发布后台入口。
5. 通过 Wagtail 用户组、Django 模型权限和页面权限建立六类标准角色。
6. 提供 `seed_navigation`、`seed_roles` 初始化命令和跨模块审计记录服务。

## 模型边界

### `SiteSettings`

使用 Wagtail `BaseSiteSetting`，按站点维护主站名称、Logo、SEO 默认值、默认图片、静态输出根目录和核心导航锁定开关。

### `NavigationItem`

使用普通 Django 模型保存站点、四大导航区域、父子菜单、排序、启用状态和核心标识。四大区域是首页、子期刊、文章、关于。核心导航变更需要 `site_settings.manage_core_navigation`，普通编辑不会获得该权限。

### `AdminRolePreset`

保存角色编码、展示名称、角色说明和对应 Wagtail Group。实际操作权限由 Group 权限表执行，`seed_roles` 可重复运行并同步系统角色。

### `AuditLog`

保存动作、状态、操作者、目标、消息、元数据、请求 ID、IP 和发生时间。模型只允许创建，不允许更新或实例删除；发布、导入、回滚和重试服务应调用 `record_audit_event`。

## 菜单与权限

模块菜单使用 `PermissionedModuleViewSet`，菜单可见性和路由访问均检查同一项权限。所有标准角色都显式获得 Wagtail 后台登录权限，未授权模块访问会被重定向回后台首页。设置菜单中的导航基线使用专用权限策略：查看权限可以让站点运营或只读人员查看，新增、修改和删除必须具备核心导航管理权限。

B–E 新增模型后，执行：

```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py seed_roles
```

`seed_roles` 会按应用标签补充业务模型权限；如果需要精确到单个模型，应在 `ROLE_DEFINITIONS` 中使用 `app_label.ModelName` 形式配置。

## 合并约束

- 子期刊通过 `Journal` 配置模型扩展，不复制页面树。
- 文章审核通过不等于前台投放；投放仍由 `ArticlePlacement` 控制。
- 前台文章页面、首页和子期刊页面由静态发布链路输出，后台菜单不直接提供数据库查询接口。
- 搜索仅保留静态推荐配置，不在底座中加入实时搜索服务。
- 图片和素材删除前由业务模块检查静态页面引用。
- 业务模块不得自行注册绕过现有 Group 权限和审计服务的后台入口。

## 负责人 A 验收结果

- 六类标准角色均可真实登录 Wagtail 后台。
- 文章管理与文章审核使用独立菜单和独立权限。
- 角色菜单在后台侧边栏按权限渲染，直接访问未授权模块同样会被拦截。
- 站点运营可维护主站配置，但内容管理员不能进入全局配置。
- 站点运营和只读人员可查看导航基线，只有核心导航管理员可新增、修改或删除。
- 审计日志后台仅允许列表和查看详情，不提供新增、修改或删除操作。
- 开发环境无需预先执行静态文件收集或创建缓存表即可打开后台。

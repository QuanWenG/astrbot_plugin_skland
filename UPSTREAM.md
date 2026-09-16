# 上游追踪

本地插件版本：**v2.4.0**。本轮本地起点：`281e337f1026b6383bbb8e2ec435e44f15433119`。

| 项目 | 基线 |
| --- | --- |
| 上游项目 | FrostN0v0/nonebot-plugin-skland |
| 原移植提交 | `0ac997a99d41e5d7fa36ae37e772ec919a210419` |
| 当前移植提交 | `a33bf84199f2511ed0716fa4eb80cf89dbce9f45` |
| 上游声明版本 | 0.7.1（以提交号区分此次更新） |

完整更新说明见 [UPDATE_REPORT.md](UPDATE_REPORT.md)，当前特化及历史缺口处理见
[UPSTREAM_DIFF.md](UPSTREAM_DIFF.md)。之前未提交的差异分析原文保存在
[UPSTREAM_DIFF_v2.2.0.md](UPSTREAM_DIFF_v2.2.0.md)。

## 校验

```powershell
# 无需本机上游仓库：检查本地已审阅实现、模板及全部静态资源
python tools/sync_upstream.py --check

# 完整验收：可传仓库根目录，也可传 nonebot_plugin_skland 包目录
python tools/sync_upstream.py --check --upstream '<上游仓库路径>'

# 也可以用环境变量配置路径，测试不再硬编码开发者目录
$env:SKLAND_UPSTREAM = '<上游仓库路径>'
python tools/sync_upstream.py --check
```

`tools/upstream_manifest.json` 登记全部 123 项上游变更的结论与去向，并校验 100 个本地实现文件、
277 张静态图片和 3 个字体文件。显式上游检查还验证上游提交和源码工作区。

## 后续移植

```powershell
# 写入脚本清单中的 18 个文本文件及 35 个图片文件
python tools/sync_upstream.py --upstream '<上游仓库路径>'

# 完成人工适配、检查差异并验证后，登记审阅结果；此参数不改写代码
python tools/sync_upstream.py --record-review --upstream '<上游仓库路径>'
```

自动同步仅覆盖明确列举的文件，不覆盖 AstrBot 入口、权限、SQLite、业务服务或消息适配器。
模型中的模组身份、动态技能及官方异格关系继续通过严格锚点叠加；绑定与战争回响等新模型、
JavaScript 分页和图片资源列入同步检查。手工适配文件由审阅清单的本地哈希追踪。

更新提交基线时必须重新核对完整上游差异，不能仅重录哈希来跳过未知变更。
离线清单校验通过不代表真实账号 API 或生产平台验证通过。

## 助战联动空集合扩展（2026-09-12）

已复核 `main.py` 和 `skland/service.py` 的定向修改：批量快照增加默认关闭的
`allow_empty` 参数，显式开启后允许无角色空集合；导出前后校验绑定版本，
失败继续向调用方传播，DTO v2 不变。仅更新这两个文件的本地审阅哈希，
上游提交、上游审阅结论和静态资源记录保持原值。
对应回归：`tests/test_snapshot_export_contract.py`、多角色快照及干员 DTO 测试。


## 登记入口与可选登录联动（2026-09-12）

本轮在现有多账号工作区基础上定向修改 `main.py`、`skland/access.py`、`skland/store.py`、
`_conf_schema.json` 和版本元数据，新增 `skland/support_integration.py`。
登记入口配置与持久化目标白名单分离；数据库 v4 带备份迁移旧白名单且不重复恢复撤销记录。
登录确认提交、二维码清理后通过无凭证公开接口通知可选助战插件；缺失、禁用或接口过旧时跳过。
没有修改上游 API、签名、角色获取规则或资源镜像。审阅清单只更新这些已复核文件的摘要。

回归包含登记入口与目标群隔离、旧库备份/回滚、撤销、登录提交边界、二维码清理、
失败隔离及无助战插件的真实入口加载。详见 `tests/test_registration.py`、
`tests/test_standalone_loading.py` 和已有绑定、多角色快照测试。

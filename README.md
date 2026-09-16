# astrbot-plugin-skland

将 [`nonebot-plugin-skland`](https://github.com/FrostN0v0/nonebot-plugin-skland) 的森空岛能力移植到 AstrBot。当前版本直接使用 AstrBot 的插件生命周期、事件、权限和数据目录，不依赖 NoneBot 运行时。

当前版本 **v2.4.0**，对应上游提交 `a33bf84199f2511ed0716fa4eb80cf89dbce9f45`。
本轮变更、数据库升级及验证结果见 [中文更新报告](UPDATE_REPORT.md)。

## 功能

- token / cred 与森空岛 App 二维码绑定，先展示角色卡并等待确认，支持凭证自动刷新
- 多个森空岛账号、双游戏角色总览及默认角色切换
- 使用原版 Jinja 模板渲染明日方舟、终末地角色卡和线索板图片
- 双游戏签到、签到状态、抽卡记录图片、缓存与多图分页
- 明日方舟集成战略（肉鸽）总览和单局详情图片
- 终末地战争回响，支持赛季、轮换和历史赛季回溯
- 终末地抽卡查询自动同步、缓存回退、免费抽与联合寻访、三列按内容高度分页
- 明日方舟干员 Box，支持持有状态、星级、职业、分支、性别、势力、种族、潜能、名称与练度筛选
- 小黑盒抽卡记录导入，卡池/角色游戏数据与图片资源同步
- 支持查询 @用户或平台数字 ID 所绑定的角色
- 管理员全体账号签到、全体角色同步
- 可配置每日自动签到
- 默认每日 09:00 更新数据与卡池；自动签到仍默认关闭
- 管理员动态快捷指令、可配置背景、回复图片查询背景/线索/战绩
- AstrBot 插件数据目录内的独立 SQLite 持久化

## 安装与配置

将本仓库作为 AstrBot 插件安装。AstrBot 会根据 `requirements.txt` 安装依赖。数据写入：

```text
data/plugin_data/astrbot_plugin_skland/skland.sqlite3
```

WebUI 可配置自动签到时间、白名单登记入口群、Chromium/Edge 可执行文件、抽卡/Box 单图数量、渲染超时、立绘与角色数据缓存、QQ 官方图片压缩和 GitHub 下载代理。`credential_registration_groups` 配置允许接收登记指令的入口群，可填写群 ID 或 `/sid` 返回的会话 ID。入口群与获准登录的目标群独立，详见下方登记流程。时间使用 AstrBot 服务器本地时区。图片渲染依赖 Playwright；Windows 会自动查找 Edge/Chrome，Linux 若没有浏览器请执行：

```bash
playwright install chromium
```

## 使用

`skland` 与 `sk` 等价。首次使用请在私聊绑定，避免凭证泄露：

```text
森空岛绑定 <24位token或32位cred>
扫码绑定
扫码登录
角色更新
/sk char
/sk char set ark 1
/sk char set ef 2
森空岛解绑
```

手动和扫码绑定均需在同一用户、同一会话内于 60 秒内回复“确认”；取消、超时或图片渲染失败不保存。重复远端账号会更新原绑定，无需 `-u`。解绑先选择单个账号或“全部”，再确认。

QQ 官方与 OneBot 在扫码成功、取消、超时或失败后均尝试撤回对应二维码；使用实际发送回执，无回执时跳过撤回。

查询与签到：

```text
/sk card [@用户|平台用户ID]
/sk efcard [@用户|平台用户ID] [-a] [-s]
ef / zmd
树海肉鸽 / 界园肉鸽 / 萨卡兹肉鸽 / 萨米肉鸽 / 水月肉鸽 / 傀影肉鸽
战绩详情 <序号> [-f]
方舟抽卡记录 [@用户|平台用户ID] [-b 起始] [-l 结束]
方舟干员 [@用户] [筛选词...] [页码]
/sk box [筛选词...] [页码] [options]
/sk box 范围 <页码>
/sk box 用户 <平台用户ID> [筛选词...] [页码]
导入抽卡记录 <小黑盒导出URL>
终末地抽卡记录 [@用户|平台用户ID] [-b 起始] [-l 结束]
终末地抽卡更新
/sk efwar [@用户|平台用户ID] [-r 角色序号] [-s 赛季] [-w 轮换]
战争回响 -s -1
/sk clue
/sk background
/sk arksign sign [--all] [UID]
/sk arksign status
/sk efsign sign [--all] [角色ID]
/sk efsign status
```

管理员命令：

```text
/sk arksign all
/sk arksign status --all
/sk efsign all
/sk efsign status --all
/sk char update --all
/sk sync [--img|--data] [-f] [-u]
```

查询、签到、导入命令可加 `-r/--role <序号>`，序号按游戏在 `/sk char` 中显示。查询他人时使用其默认角色，不能替他人指定角色。中文签到入口无参数时保留本用户全部角色签到行为；指定 `-r` 时仅签到该角色。

终末地抽卡查询会自动同步，失败时明确标注本地缓存；`终末地抽卡更新` 与 `-u` 入口继续兼容。抽卡范围使用 Python 切片语义，例如 `-b -3` 表示最后三个卡池，终末地按分类取范围，累计统计保持完整。

回复查询图片发送 `background`、`clue` 或 `战绩详情 <序号>` 可使用该图片上下文；也保留直接命令。上下文默认 300 秒，按平台实例、会话和请求用户隔离。有回执时按消息匹配，无回执时使用同会话最近上下文。

### 动态快捷指令

```text
/sk shortcut add 我的六星 box --rarity 6
/sk shortcut list
我的六星 2 -r 1
/sk shortcut remove 我的六星
```

`--shortcut` 与 `shortcut` 等价。管理员增删，全局生效并写入插件 SQLite，重启恢复。名称完整匹配并透传尾随参数；不覆盖内置入口、不递归展开，目标命令权限仍会检查。

### 新配置默认值

| 配置 | 默认值 | 行为 |
| --- | --- | --- |
| `auto_update_resources` | `true` | 服务器本地时区每日 09:00 更新数据/卡池与筛选资料，结果仅写日志 |
| `auto_sign` | `false` | 保持原来的默认关闭行为 |
| `background_source` | `default` | 内置角色卡背景；Box 无背景 |
| `rogue_background_source` | `rogue` | 使用肉鸽主题背景 |
| `context_ttl` | `300` | 图片查询上下文有效秒数 |

背景可填 `default`、`random`、`Lolicon`、本地文件/目录、URL、data URI，或 CustomSource JSON，例如 `{"uri":"backgrounds"}`。相对路径基于 `data/plugin_data/astrbot_plugin_skland/`；失败回退内置背景。已有代理设置保持原值，GitHub Token 仅用于官方 API。

### 方舟干员筛选

自然筛选词与上游 0.7.1 一致，例如：

```text
方舟干员 未拥有 6星
方舟干员 近卫 满潜 练度
方舟干员 @某人 远程 女 最近
```

高级参数支持 `--ownership`、`--rarity`、`--profession`、`--branch`、`--position`、`--gender`、`--faction`、`--race`、`--potential`、`--sort` 和 `--name`。同维度为“或”，不同维度为“且”。

### QQ 官方图片上传

QQ 官方 API 使用 Base64 JSON 上传图片，肉鸽和 Box 长图会在插件内自动转为 JPEG，并受 `qq_image_max_bytes`、`qq_image_max_side` 与 `qq_image_jpeg_quality` 控制。二维码保持 PNG。日志只记录格式、尺寸、字节数和耗时，不记录图片内容或凭证。

### 同步上游源码

当前移植基线是 `a33bf84`（上游仍声明 0.7.1）：

```powershell
python tools/sync_upstream.py --check
python tools/sync_upstream.py --check --upstream '<上游仓库路径>'
```

未指定上游路径时检查本地审阅快照；显式指定路径或配置 `SKLAND_UPSTREAM` 时同时检查源码。同步器只改写清单中的 schema、filters、模板及静态资源，AstrBot 入口、权限、SQLite、服务和图片发送适配器由手工适配清单追踪。模组身份、异格关系及 DTO v2 扩展使用严格叠加锚点。详见 [上游追踪说明](UPSTREAM.md)。

## 与 NoneBot 原版的差异

后续移植上游更新前，请先查看 [完整差异与合并保留清单](UPSTREAM_DIFF.md)，其中记录了本地扩展、模块对应关系、未移植能力及回归检查。

业务 API、Schema、模板与静态资源基于原插件移植；入口、权限、用户标识和 SQLite 存储按 AstrBot 重写。回复上下文和直接详情命令均保留；OneBot/Satori 多图采用节点，其他平台逐图发送。Box 每次仅发送指定页，默认第一页。

## 群登记与登录白名单（v2.4.0）

设置中的 **白名单登记入口群**（`credential_registration_groups`）只控制在哪里接受登记。
登录白名单由本插件 SQLite 独立保存，不依赖助战插件。

1. 管理员把入口群 A 的群 ID 或完整 SID 填入 `credential_registration_groups`。
2. 群友在目标群 B 通过 `/sid` 获取完整 SID，然后在入口群 A 发送：

   ```text
   /森空岛登记 平台实例:GroupMessage:群B会话ID
   ```

3. 目标群 B 加入登录白名单，可使用扫码登录、凭证绑定和解绑。入口群 A 不会自动获得这些权限。

入口群内群友均可自助登记。必须提供完整群 SID，不能只填群号、私聊 SID 或省略参数；
重复登记不会增加记录。非入口群和私聊不能登记。也支持 `/sk register <目标群SID>`。
登记按完整 SID 隔离平台实例，并保存登记入口、登记人和登记时间。

管理员管理指令（可在私聊使用）：

```text
/森空岛白名单
/森空岛撤销登记 <完整SID或迁移群ID>
```

也支持 `/sk whitelist` 和 `/sk unregister <标识>`。撤销后立即停止目标群的绑定、扫码和解绑，
已保存账号、角色历史和助战数据保留，私聊绑定功能仍可用。撤销完整 SID 时也移除匹配它的旧群号规则；
旧群号规则原本对各平台同名群号生效。移除登记入口只停止该入口接受新登记，不影响已登记目标群。

升级时，数据库 v3 原地升级到 v4，保留账号、角色和历史；更早的旧库继续通过已有迁移升级。
迁移前生成 `.pre-v4-时间.bak` 备份，在同一事务中添加登记表、导入旧白名单并写入版本标记。
旧 `credential_group_whitelist` 中的群 ID 和 SID 首次升级自动成为已登记目标，保留原匹配语义；
它们不会成为登记入口。之后该旧配置只供兼容展示，修改不再生效，重启也不会恢复已经撤销的记录。
新登记入口默认空列表，需要管理员单独配置；未配置入口不影响已迁移目标群登录。

## 登录后自动导入助战

本插件可以独立使用，不要求安装 `astrbot_plugin_arksupport`。两插件同时启用且助战插件为 v0.9.0
及以上时，群内登录并确认保存账号后，默认立即导入本人全部明日方舟角色到 **登录所在群**。
不会导入登记入口群或其他群，私聊登录不导入。二维码清理完成后才调用助战接口。

导入失败不回滚账号绑定，旧助战快照保留，可在登录群发送 `/助战 森空岛导入` 重试。
用户执行过 `/助战 森空岛移除` 后，再次在同群登录会重新导入。
助战插件设置 `skland_login_auto_import_enabled` 可关闭登录导入，默认开启，与周期同步开关独立。

联动每次通过 AstrBot 注册表查找已启用助战实例，检测公开异步方法
`import_skland_after_login(*, owner_id, group_umo, platform_id, group_id, group_name, member_nickname)`，
只传用户标识与群信息，不传 token、cred 或原始事件。接口返回摘要字符串或 `None`。
助战插件缺失、禁用或缺少接口时直接跳过，登录不受影响；导入异常与发送回执异常分别处理。
两插件不互相导入包、读取数据库或添加安装依赖。仅安装助战插件时，Excel 导入、查询和图片功能照常可用。

## 插件联动接口

插件实例公开异步只读门面
`export_arknights_operator_snapshots(owner_id: str) -> list[dict]`，返回该平台用户全部森空岛
账号下已绑定明日方舟角色（包括官服和 B 服）的 `schema_version=2`
已拥有干员快照。任一角色获取失败时接口整体失败，不返回部分结果。
`export_arknights_operator_snapshot(owner_id: str) -> dict` 继续返回默认角色，供
旧版调用方兼容。复数接口按区服 + UID 去重，同角色优先采用默认角色所属账号，否则采用最早绑定账号。单个 DTO 结构如下：

```text
schema_version, snapshot_at, variant_metadata_complete
role: uid, nickname, server_id, server_name
operators[]:
  char_id, variant_group_id?, name, rarity, profession, evolve_phase, level,
  potential_rank
  modules[]: module_id, module_name, type_code, type_icon, level
  skills[]: skill_id, skill_index, mastery_level
```

`modules` 只导出已解锁模组，按游戏数据返回实际数量，不限制为三项；X、Y 和
Delta 模组通过 `type_code` 区分（Delta 的原始代码为 `D`）。`skills` 按干员
实际技能顺序导出，`skill_index` 从 1 开始，因此低星干员不会补齐三个技能，
未来超过三个技能也不会被截断。`potential_rank` 保持森空岛 API 的 0–5。
`variant_group_id` 来自官方 `char_meta_table.spCharGroups`，用于识别本体和异格；
旧缓存没有该数据时字段省略，不根据干员名推断关系。
`variant_metadata_complete` 只在该官方表结构有效、目录干员全部有分组，且
本次导出没有实时新干员回退记录时才为 `true`，便于调用方区分正常单体
分组与“离线/旧缓存尚未取得完整分组表”。

批量接口额外支持关键字参数 `allow_empty: bool = False`。调用方显式传入
`allow_empty=True` 时，当前没有可用明日方舟角色会返回 `[]`，可用于同步清空
助战列表；默认仍抛出缺少角色异常，保持旧调用方行为。请求或凭证失败不会
转换为空列表。接口在导出前后检查用户绑定版本，期间发生绑定或角色变化则
整体失败，避免调用方误用过期集合。单角色快照 DTO 仍为 v2。

DTO 只包含角色显示信息、区服和练度字段，不包含 token、cred 或完整账号对
象。调用方应通过 AstrBot `get_registered_star()` 动态取得当前激活实例并校验
快照版本；不要读取本插件 SQLite 或依赖内部 Service/Schema。

## 安全提示

- 绑定、扫码和解绑允许在私聊或已登记的登录白名单群中使用；登记入口群本身不会自动放行。
- 即使是白名单群，绑定 token 仍会出现在聊天记录中；优先使用扫码绑定或私聊绑定。
- 插件不会在日志或回复中回显凭证。
- 不要分享扫码二维码、token、cred 或 SQLite 数据库。

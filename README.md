# astrbot-plugin-skland

将 [`nonebot-plugin-skland`](https://github.com/FrostN0v0/nonebot-plugin-skland) 的森空岛能力移植到 AstrBot。当前版本直接使用 AstrBot 的插件生命周期、事件、权限和数据目录，不依赖 NoneBot 运行时。

当前版本 **v2.7.6**，对应上游提交 `a33bf84199f2511ed0716fa4eb80cf89dbce9f45`。
此前 v2.4.0 的变更、数据库升级及验证结果见 [中文更新报告](UPDATE_REPORT.md)；v2.5.0 的订阅与升级行为见下文。

## 功能

- token / cred 与森空岛 App 二维码绑定，认证成功后直接保存，支持凭证自动刷新
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
- 双游戏理智私信订阅，默认提前 10 分钟，按角色提醒、取消和投递测试
- 可配置每日自动签到
- 默认每日 09:00 更新数据与卡池；自动签到仍默认关闭
- 管理员动态快捷指令、可配置背景、回复图片查询背景/线索/战绩
- AstrBot 插件数据目录内的独立 SQLite 持久化

## 安装与配置

将本仓库作为 AstrBot 插件安装。AstrBot 会根据 `requirements.txt` 安装依赖。数据写入：

```text
data/plugin_data/astrbot_plugin_skland/skland.sqlite3
```

手动覆盖更新请把包中的 **`main.py`、`metadata.yaml` 和整个 `skland/`** 放进 AstrBot 实际加载的插件目录
（通常是 `data/plugins/astrbot_plugin_skland/`），与原来的同名文件、目录合并覆盖。
覆盖完成后，`main.py` 和 `metadata.yaml` 必须直接位于这个插件目录内，不能多套一层 `astrbot_plugin_skland/`。
`dist/astrbot_plugin_skland-v2.7.6-overlay.zip` 是无外壳覆盖包，包根目录直接包含这些文件。
插件数据独立保存在上述 `data/plugin_data/` 路径，覆盖包不包含数据库。

完整重启 AstrBot 后发送 `/sk version` 检查实际运行版本、角色接口 401 恢复组件和凭证自动恢复链。
两项认证组件均显示“已加载”才表示修复已生效。管理员还能看到实际加载的接口和服务文件路径。
如果显示旧版本、未知指令或组件未加载，请根据启动日志核对覆盖目录；只更新 metadata.yaml 不能更新运行代码。

WebUI 可配置自动签到时间、Chromium/Edge 可执行文件、抽卡/Box 单图数量、渲染超时、立绘与角色数据缓存、QQ 官方图片压缩和 GitHub 下载代理。所有群聊和私聊均可绑定、扫码登录及解绑，不再需要登记或白名单。时间使用 AstrBot 服务器本地时区。图片渲染依赖 Playwright；Windows 会自动查找 Edge/Chrome，Linux 若没有浏览器请执行：

```bash
playwright install chromium
```

v2.7.6 降低启动和资源更新时的内存峰值：游戏表按文件读取并只保留目录、技能、模组、筛选资料和卡池所需字段；旧文件备份写入磁盘，更新完成后释放暂存内容。已有完整缓存可直接读取，无需删除或重新下载。缓存解析在后台线程执行。

启动时默认仅读取本地缓存，`startup_update_resources` 默认关闭；缺少数据时由首次相关查询或手动“资源更新 --data”获取。需要开机联网更新可在 WebUI 开启该项。每日 09:00 更新仍由 `auto_update_resources` 独立控制。PRTS 元数据分页遇到重复数据或超过数量上限会停止，并保留旧缓存。

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

v2.7.5 起，手动绑定认证成功、扫码登录成功后直接保存账号并发送角色同步结果，无需回复“确认”，也不依赖角色卡图片渲染。扫码仍可回复“取消”中止，取消、超时或认证失败不保存。重复远端账号会更新原绑定，无需 `-u`。解绑先选择单个账号或“全部”，再确认。

v2.7.4 修复角色接口 HTTP 401 绕过自动刷新流程的问题。升级后执行 `/sk card` 或 `/理智订阅状态`
即可尝试自动恢复旧绑定：先刷新签名 token，cred 失效时使用已保存的登录 token 获取新认证并核验账号身份。
自动恢复保留账号、角色、抽卡历史和订阅；网络或服务器错误不会触发重新登录。
仅绑定 cred 且已失效、保存的登录 token 也失效，或刷新后仍无法认证时，按提示重新 `/扫码绑定` 即可。
同一远端账号会更新原绑定并保留记录，无需先解绑或删除数据库。

QQ 官方与 OneBot 在扫码成功、取消、超时或失败后均尝试撤回对应二维码；使用实际发送回执，无回执时跳过撤回。

QQ 官 Bot 群内扫码先发送独立 Markdown @ 提示，再发送二维码图片，避免富媒体说明把提及标签显示成原始文字。
@ 显示需要官 Bot 的原生 Markdown 权限；提示失败时继续发送无标签普通说明和二维码，并在日志记录原因。
私聊和其他平台继续使用原有消息形式，撤回始终针对实际二维码图片消息。

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

## 理智私信订阅（v2.5.0）

```text
/理智订阅
/理智订阅 方舟 10 -r 1
/理智订阅 终末地 10 -r 2
/理智订阅 方舟 0
/理智取消 方舟 -r 1
/理智取消 终末地 --all
/理智订阅状态
/理智提醒测试
/sk sanity subscribe ark 10 -r 1
/sk sanity subscribe ef 10
/sk sanity unsubscribe ark --all
/sk sanity status
/sk sanity test
```

默认游戏为明日方舟，默认提前 10 分钟；提前分钟接受非负整数，`0` 表示满时提醒。
未指定 `-r` 时订阅该游戏当前默认角色，订阅后固定跟随该角色，切换默认角色不改变订阅。
同一角色重复订阅更新提前时间，不重置本轮已提醒状态。全部指令仅管理本人订阅。

群内订阅立即保存。**请将 Bot 添加到私聊，并开启“允许主动发送”，再执行 `/理智提醒测试` 验证。**
QQ 官 Bot 无好友关系或用户拒收时会暂停投递，订阅仍保留；测试成功后恢复投递。
收到同一用户的私聊指令后保存实际私聊目标，之后群内订阅也使用该目标。
通知仅发私聊，不在群里补发。QQ 官方 WebSocket 和 Webhook 均使用主动单聊接口，并检查实际发送回执。

每 60 秒读取已订阅角色的新数据。接近满值或已满时每轮提醒一次；持续满着不重复。
消耗理智使剩余恢复时间再次超过所设提前时间后，才开始下一轮提醒。
首次订阅已接近满值时，在下一次检查提醒。森空岛数据更新和网络延迟可能影响提醒时间。
临时失败每 5 分钟重试并重新验证理智状态。状态指令实时查询本人已订阅角色，显示当前理智/上限、
预计多少分钟后满及具体满值时间；查询失败时明确标注上次记录。查询状态不触发私信，也不重置本轮已提醒状态。
获取数据恢复成功后自动清除旧的读取失败提示及其重试等待；仍未解决的私信投递错误继续显示。
订阅状态与私信提醒的显示时间统一使用 UTC+8，不在回复中标注时区。
所有订阅全天有效并在重启后恢复；解绑清理对应角色订阅，暂不可用角色暂停检查。

数据库 v5 在迁移前生成 `.pre-v5-时间.bak` 备份，并在事务内移除旧登记表、增加订阅及私聊目标记录。
v4、v3 及更早的旧库均可升级，账号和角色历史保留。旧白名单、登记配置不再使用，可删除旧配置值。

## 登录后自动导入助战

本插件可以独立使用，不要求安装 `astrbot_plugin_arksupport`。两插件同时启用且助战插件为 v0.9.0
及以上时，群内登录成功并保存账号后，默认立即导入本人全部明日方舟角色到 **登录所在群**。
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

- 所有群聊和私聊均可绑定、扫码和解绑。
- 群内绑定 token 会出现在聊天记录中；优先使用扫码绑定或私聊绑定。
- 插件不会在日志或回复中回显凭证。
- 不要分享扫码二维码、token、cred 或 SQLite 数据库。

## 不依赖资源的原始干员接口（v2.6.0）

公开异步门面 `export_arknights_raw_rosters(owner_id, *, allow_empty=False) -> dict`
直接请求新鲜角色数据，复用签名和凭证刷新，不经过角色卡模型、展示缓存、游戏目录、资源更新或渲染。
凭证保留在森空岛；两个插件无需互相导入。助战 v0.10.0 优先使用此门面，自行解析和后台补齐资料。

返回 JSON 契约 v1：

```text
schema_version: 1
fetched_at: UTC ISO 时间
binding_revision: 非负绑定版本
complete_role_set: true
roles[]:
  role: uid, nickname, server_id, server_name
  chars[]: charId, evolvePhase, level, potentialRank
    skills[]: id, specializeLevel
    equip[]: id, level, locked
  charInfoMap: id, name，以及 API 明确提供的 profession/rarity/skill_ids/variant_group_id
  equipmentInfoMap: id, name, typeIcon，以及 API 明确提供的 type_code
```

只导出已经保存的可用方舟角色，按区服和 UID 去重；导出前后检查绑定版本。
`allow_empty=True` 且明确无可用角色时返回 `roles=[]`；任一请求失败、绑定变化或无效响应整体失败。
异常携带稳定 `code`：not_bound、auth_failed、network_failed、binding_changed、invalid_response，
消息不含凭证或原始网络错误。旧 v2 快照接口和登录助战回调保持兼容。

升级建议先更新森空岛至 v2.6.0，再更新助战至 v0.10.0；单独使用森空岛不受影响。

## 助战图片回执与绑定筛选（v2.7.0）

新增公开异步接口 `filter_skland_bound_owners(owner_ids: list[str]) -> list[str]`，只筛选调用方
传入的用户 ID，返回已绑定用户 ID，不包含账号对象或凭证；候选去重、保持顺序，大名单分批查询。
助战用该接口筛选当前群成员，仍不读取森空岛内部服务或数据库。

登录助战联动优先调用助战 v0.11.0 的 `import_skland_after_login_receipt(...)`，验证
schema_version=1、text、short_text、role_count、images（PNG 字节列表）。4 个起发送图片，
图片复用森空岛现有平台尺寸与压缩适配；生成或发送失败只发送短摘要。最多 3 个账号仍发送文字。
新回调不存在时兼容旧文字回调；新回调失败不重复调用旧回调，账号绑定不回滚。
部署顺序：森空岛 v2.7.0，然后助战 v0.11.0。森空岛单独安装时登录行为不受影响。

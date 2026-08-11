# astrbot-plugin-skland

将 [`nonebot-plugin-skland`](https://github.com/FrostN0v0/nonebot-plugin-skland) 的森空岛能力移植到 AstrBot。当前版本直接使用 AstrBot 的插件生命周期、事件、权限和数据目录，不依赖 NoneBot 运行时。

## 功能

- token / cred 与森空岛 App 二维码绑定，支持凭证自动刷新
- 同步明日方舟与终末地角色
- 使用原版 Jinja 模板渲染明日方舟、终末地角色卡和线索板图片
- 双游戏签到、签到状态、抽卡记录图片、缓存与多图分页
- 明日方舟集成战略（肉鸽）总览和单局详情图片
- 明日方舟干员 Box，支持持有状态、星级、职业、分支、性别、势力、种族、潜能、名称与练度筛选
- 小黑盒抽卡记录导入，卡池/角色游戏数据与图片资源同步
- 支持查询 @用户或平台数字 ID 所绑定的角色
- 管理员全体账号签到、全体角色同步
- 可配置每日自动签到
- AstrBot 插件数据目录内的独立 SQLite 持久化

## 安装与配置

将本仓库作为 AstrBot 插件安装。AstrBot 会根据 `requirements.txt` 安装依赖。数据写入：

```text
data/plugin_data/astrbot_plugin_skland/skland.sqlite3
```

WebUI 可配置自动签到时间、绑定指令群聊白名单、Chromium/Edge 可执行文件、抽卡/Box 单图数量、渲染超时、立绘与角色数据缓存、QQ 官方图片压缩和 GitHub 下载代理。`credential_group_whitelist` 可填写群 ID 或 `/sid` 返回的会话 ID；为空时绑定相关指令仍仅限私聊。时间使用 AstrBot 服务器本地时区。图片渲染依赖 Playwright；Windows 会自动查找 Edge/Chrome，Linux 若没有浏览器请执行：

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
森空岛解绑 确认
```

QQ 官方 Bot 的群聊与 C2C 私聊在扫码绑定成功后会自动撤回对应的二维码消息；撤回失败不会影响绑定结果。

查询与签到：

```text
/sk card [@用户|平台用户ID]
/sk efcard [@用户|平台用户ID] [-a] [-s]
ef / zmd
树海肉鸽 / 界园肉鸽 / 萨卡兹肉鸽 / 萨米肉鸽 / 水月肉鸽 / 傀影肉鸽
战绩详情 <序号> [-f]
方舟抽卡记录 [@用户|平台用户ID] [-b 起始] [-l 结束]
方舟干员 [@用户|平台用户ID] [筛选词...]
/sk box [筛选词...] [页码] [options]
/sk box 范围 <页码>
/sk box 用户 <平台用户ID> [筛选词...] [页码]
导入抽卡记录 <小黑盒导出URL>
终末地抽卡记录 [@用户|平台用户ID] [-b 起始] [-l 结束]
终末地抽卡更新
/sk clue
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

解绑需显式确认：`/sk unbind confirm`。

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

当前可移植核心对应上游提交 `0ac997a`（0.7.1）：

```powershell
python tools/sync_upstream.py --check
python tools/sync_upstream.py
```

同步器只更新 schema、filters、模板和静态资源，并拒绝新的 NoneBot/ORM/Alconna 依赖；AstrBot 入口、权限、SQLite、服务和图片发送适配器不会被覆盖。干员 schema 会确定性叠加 `schema_version=2` 快照所需的模组身份字段与目录回退逻辑，上游锚点变化时检查会直接失败，避免静默覆盖联动契约。

## 与 NoneBot 原版的差异

业务 API、Schema、模板与静态资源基于原插件移植；入口、权限、用户标识和 SQLite 存储按 AstrBot 重写。原版依赖回复消息暗语打开肉鸽详情和线索板，AstrBot 版改为“最近一次肉鸽查询缓存 + `战绩详情`”以及显式 `/sk clue`。QQ 合并转发改为按平台顺序发送多张图片。

## 插件联动接口

插件实例公开异步只读门面
`export_arknights_operator_snapshots(owner_id: str) -> list[dict]`，返回同一森空岛
账号下全部已绑定明日方舟角色（包括官服和 B 服）的 `schema_version=2`
已拥有干员快照。任一角色获取失败时接口整体失败，不返回部分结果。
`export_arknights_operator_snapshot(owner_id: str) -> dict` 继续返回默认角色，供
旧版调用方兼容。单个 DTO 结构如下：

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

DTO 只包含角色显示信息、区服和练度字段，不包含 token、cred 或完整账号对
象。调用方应通过 AstrBot `get_registered_star()` 动态取得当前激活实例并校验
快照版本；不要读取本插件 SQLite 或依赖内部 Service/Schema。

## 安全提示

- 绑定、扫码和解绑允许在私聊或 `credential_group_whitelist` 指定的群中使用。
- 即使是白名单群，绑定 token 仍会出现在聊天记录中；优先使用扫码绑定或私聊绑定。
- 插件不会在日志或回复中回显凭证。
- 不要分享扫码二维码、token、cred 或 SQLite 数据库。

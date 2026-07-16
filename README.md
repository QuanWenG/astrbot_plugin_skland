# astrbot-plugin-skland

将 [`nonebot-plugin-skland`](https://github.com/FrostN0v0/nonebot-plugin-skland) 的森空岛能力移植到 AstrBot。当前版本直接使用 AstrBot 的插件生命周期、事件、权限和数据目录，不依赖 NoneBot 运行时。

## 功能

- token / cred 与森空岛 App 二维码绑定，支持凭证自动刷新
- 同步明日方舟与终末地角色
- 使用原版 Jinja 模板渲染明日方舟、终末地角色卡和线索板图片
- 双游戏签到、签到状态、抽卡记录图片、缓存与多图分页
- 明日方舟集成战略（肉鸽）总览和单局详情图片
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

WebUI 可配置自动签到时间、绑定指令群聊白名单、Chromium/Edge 可执行文件、抽卡单图卡池数和 GitHub 下载代理。`credential_group_whitelist` 可填写群 ID 或 `/sid` 返回的会话 ID；为空时绑定相关指令仍仅限私聊。时间使用 AstrBot 服务器本地时区。图片渲染依赖 Playwright；Windows 会自动查找 Edge/Chrome，Linux 若没有浏览器请执行：

```bash
playwright install chromium
```

## 使用

`skland` 与 `sk` 等价。首次使用请在私聊绑定，避免凭证泄露：

```text
森空岛绑定 <24位token或32位cred>
扫码绑定
角色更新
森空岛解绑 确认
```

查询与签到：

```text
/sk card [@用户|平台用户ID]
/sk efcard [@用户|平台用户ID] [-a] [-s]
ef / zmd
界园肉鸽 / 萨卡兹肉鸽 / 萨米肉鸽 / 水月肉鸽 / 傀影肉鸽
战绩详情 <序号> [-f]
方舟抽卡记录 [@用户|平台用户ID] [-b 起始] [-l 结束]
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

## 与 NoneBot 原版的差异

业务 API、Schema、模板与静态资源基于原插件移植；入口、权限、用户标识和 SQLite 存储按 AstrBot 重写。原版依赖回复消息暗语打开肉鸽详情和线索板，AstrBot 版改为“最近一次肉鸽查询缓存 + `战绩详情`”以及显式 `/sk clue`。QQ 合并转发改为按平台顺序发送多张图片。

## 安全提示

- 绑定、扫码和解绑允许在私聊或 `credential_group_whitelist` 指定的群中使用。
- 即使是白名单群，绑定 token 仍会出现在聊天记录中；优先使用扫码绑定或私聊绑定。
- 插件不会在日志或回复中回显凭证。
- 不要分享扫码二维码、token、cred 或 SQLite 数据库。

"""Review ledger for manual AstrBot adaptations and offline drift verification."""
import hashlib
import json
import subprocess
from pathlib import Path

PREVIOUS_COMMIT = "0ac997a99d41e5d7fa36ae37e772ec919a210419"
UPSTREAM_COMMIT = "a33bf84199f2511ed0716fa4eb80cf89dbce9f45"
MANIFEST = "tools/upstream_manifest.json"

# Each framework-owned upstream module has an explicit destination and decision.
ADAPTATIONS = {
    "__init__.py": ("生命周期、命令注册与每日 09:00 数据更新改为 AstrBot", ["main.py"]),
    "account.py": ("稳定账号/角色 ID 与默认选择使用 SQLite 实现", ["skland/store.py", "skland/binding.py"]),
    "api/request.py": ("战争回响 API；保留本地签名与凭证适配", ["skland/api/request.py"]),
    "config.py": ("保留已有配置值并新增背景、上下文、每日更新默认值", ["skland/config.py", "_conf_schema.json"]),
    "data_source.py": ("数据校验、固定提交与缓存回退使用本地更新服务", ["skland/resourcesync.py"]),
    "db_handler.py": ("ORM 操作改写为 SQLite 事务及稳定角色关联", ["skland/store.py"]),
    "download.py": ("连接复用、官方 API Token、代理直连回退", ["skland/download.py", "skland/resourcesync.py"]),
    "exception.py": ("保留本地异常边界和用户提示", ["skland/exception.py", "main.py"]),
    "extras.py": ("快捷指令、回复上下文和平台能力适配", ["main.py", "skland/interactions.py"]),
    "hook.py": ("QQ/OneBot 回执撤回和按平台能力启用消息反应", ["skland/interactions.py", "skland/qq_message.py"]),
    "image_cache.py": ("保留本地缓存安全校验及动态路径", ["skland/image_cache.py", "skland/filters.py"]),
    "matcher.py": ("命令权限及完整名称快捷展开由 AstrBot 执行", ["main.py", "skland/commands.py"]),
    "model.py": ("多账号、角色与历史关联改写为 dataclass / SQLite", ["skland/models.py", "skland/store.py"]),
    "player_data.py": ("保留 TTL/LRU/并发合并，缓存身份加入账号和完整角色", ["skland/service.py", "skland/card_cache.py"]),
    "render.py": ("Playwright 渲染、三列按高度分页及 QQ 图片策略", ["skland/cards.py", "skland/renderer.py", "skland/image_output.py"]),
    "tasks.py": ("服务器本地时区每日数据更新；自动签到默认关闭", ["main.py"]),
    "utils.py": ("上游拆分旧工具模块；对应职责迁入本地服务和适配器", ["skland/service.py", "skland/background.py", "skland/interactions.py"]),
    "utils/__init__.py": ("上游包布局调整；本地无需框架工具包", ["skland/__init__.py"]),
    "utils/background.py": ("默认/随机/CustomSource/Lolicon 与失败回退", ["skland/background.py"]),
    "utils/message.py": ("回执、多图转发和反应按 AstrBot 平台能力适配", ["skland/interactions.py", "skland/qq_message.py"]),
    "utils/qrcode.py": ("保留本地二维码卡、PNG 策略及确认/退出清理", ["skland/qrcode_card.py", "main.py"]),
    "services/__init__.py": ("领域服务保留现有本地分层", ["skland/service.py"]),
    "services/auth.py": ("凭证刷新检查账号身份及条件写回", ["skland/service.py", "skland/store.py"]),
    "services/binding.py": ("脱离事务的确认预览、互斥及提交前版本核验", ["skland/binding.py", "skland/store.py", "main.py"]),
    "services/gacha.py": ("按角色存储、自动同步/缓存回退、免费抽与联合寻访", ["skland/service.py", "skland/gacha.py"]),
    "services/resources.py": ("启动、手动和每日更新共用互斥服务", ["skland/resourcesync.py"]),
    "services/sign.py": ("双游戏按角色签到，保留中文命令与管理员全体入口", ["skland/service.py", "skland/store.py", "main.py"]),
    "schemas/sign.py": ("签到结果采用本地角色主键记录及结构化模型", ["skland/store.py", "skland/schemas/sign.py"]),
    "schemas/endfield/gacha/base.py": ("保留武器可选字段兼容；过滤武器礼盒与角色附加条目，保留原始分页游标，空抽卡页继续同步并拒绝异常游标", ["skland/schemas/endfield/gacha/base.py", "skland/service.py", "tests/test_ef_gacha_response.py"]),
}


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def digest(path):
    if path.suffix in {".py", ".jinja2", ".js", ".css", ".json", ".yaml"}:
        content = (path.read_text("utf-8").rstrip() + "\n").encode("utf-8")
    else:
        content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def asset_tree(root):
    files = sorted(p for p in root.rglob("*") if p.is_file())
    content = "\n".join(p.relative_to(root).as_posix() + ":" + digest(p) for p in files)
    return {"count": len(files), "sha256": hashlib.sha256(content.encode()).hexdigest()}


def review_destination(path, status, mirrored):
    prefix = "nonebot_plugin_skland/"
    if not path.startswith(prefix):
        if path.startswith("tests/"):
            return "test", "上游测试语义由本地领域测试、模拟 HTTP/事件和浏览器验收覆盖", ["tests"]
        return "documentation", "上游说明、构建或开发配置；由本地说明和运行配置承接", ["README.md", "UPSTREAM_DIFF.md"]
    relative = path[len(prefix):]
    if relative in mirrored:
        return "mirror", "确定性同步及本地兼容叠加", ["skland/" + relative]
    if relative in ADAPTATIONS:
        explanation, destinations = ADAPTATIONS[relative]
        return "adapted", explanation, destinations
    if relative.startswith("commands/"):
        return "adapted", "NoneBot 命令/角色选择改写为 AstrBot 统一解析、权限与服务调用", ["main.py", "skland/service.py", "skland/commands.py"]
    if relative.startswith("migrations/"):
        return "adapted", "Alembic 迁移改为带备份的版本化 SQLite 事务迁移，歧义历史保留待归属", ["skland/store.py", "tests/test_account_migration.py"]
    if relative.startswith("schemas/"):
        return "adapted", "保留本地模型兼容导出；新模型由根 schemas 导出", ["skland/" + relative]
    raise ValueError(f"上游变化未登记审阅结论：{path}")


def record_review(root, source_root, mirrored):
    repo = source_root.parent
    if git(repo, "rev-parse", "HEAD").decode().strip() != UPSTREAM_COMMIT:
        raise ValueError("上游 HEAD 与指定移植基线不一致")
    changes = git(repo, "diff", "--name-status", PREVIOUS_COMMIT, UPSTREAM_COMMIT).decode("utf-8").splitlines()
    reviews = {}
    for line in changes:
        status, path = line.split("\t", 1)
        disposition, reason, targets = review_destination(path, status, mirrored)
        for target in targets:
            if not (root / target).exists():
                raise ValueError(f"审阅结论缺少本地交付：{path} → {target}")
        reviews[path] = {"status": status, "disposition": disposition, "reason": reason, "targets": targets,
            "upstream_sha256": None if status == "D" else hashlib.sha256(git(repo, "show", UPSTREAM_COMMIT + ":" + path)).hexdigest()}
    local_files = [root / "main.py", root / "_conf_schema.json", root / "metadata.yaml"]
    local_files += [p for p in (root / "skland").rglob("*") if p.is_file() and p.suffix in {".py", ".jinja2", ".js", ".css"} and not any(part.startswith(".") for part in p.relative_to(root).parts)]
    value = {"previous_commit": PREVIOUS_COMMIT, "upstream_commit": UPSTREAM_COMMIT,
        "files": {p.relative_to(root).as_posix(): digest(p) for p in sorted(local_files)},
        "assets": {name: asset_tree(root / name) for name in ("skland/resources/fonts", "skland/resources/images")},
        "reviews": reviews}
    (root / MANIFEST).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return value


def verify_review(root, source_root=None):
    value = json.loads((root / MANIFEST).read_text("utf-8"))
    errors = []
    for name, expected in value["files"].items():
        if not (root / name).is_file() or digest(root / name) != expected:
            errors.append("本地适配待复核：" + name)
    for name, expected in value["assets"].items():
        if asset_tree(root / name) != expected:
            errors.append("静态资源待复核：" + name)
    if source_root is not None:
        repo = source_root.parent
        if git(repo, "rev-parse", "HEAD").decode().strip() != value["upstream_commit"]:
            errors.append("上游 HEAD 与审阅基线不一致")
        for name, row in value["reviews"].items():
            # Compare committed bytes: Git's checkout line-ending conversion is not source drift.
            actual = None if row["status"] == "D" else hashlib.sha256(git(repo, "show", "HEAD:" + name)).hexdigest()
            if actual != row["upstream_sha256"]:
                errors.append("上游适配待复核：" + name)
        if git(repo, "status", "--porcelain", "--", "nonebot_plugin_skland").strip():
            errors.append("上游源码工作区含未审阅改动")
    return errors

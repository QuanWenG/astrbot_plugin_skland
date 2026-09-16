"""Argument handling shared by command aliases and the AstrBot entry point."""
ROLE_FLAGS = {"-r", "--role"}
BOX_OPTIONS = {"-o", "--ownership", "ownership", "--rarity", "rarity", "-p", "--profession", "profession",
    "-b", "--branch", "branch", "--position", "position", "--gender", "gender", "-f", "--faction", "faction",
    "--race", "race", "--potential", "potential", "-s", "--sort", "sort", "-n", "--name", "name"}


def take_option(args: list[str], names: set[str], *, integer=False):
    result, remaining = None, []
    i = 0
    while i < len(args):
        name, sep, inline = args[i].partition("=")
        if name not in names:
            remaining.append(args[i])
            i += 1
            continue
        if result is not None:
            raise ValueError(f"参数 {name} 重复")
        if sep:
            value = inline
            i += 1
        else:
            if i + 1 >= len(args):
                raise ValueError(f"参数 {name} 缺少值")
            value = args[i + 1]
            i += 2
        try:
            result = int(value) if integer else value
        except ValueError as exc:
            raise ValueError(f"参数 {name} 需要整数") from exc
    return result, remaining


def take_role(args):
    value, remaining = take_option(args, ROLE_FLAGS, integer=True)
    if value is not None and value < 1:
        raise ValueError("角色序号必须从 1 开始")
    return value, remaining


def validate_shortcut(name: str, argv: list[str], reserved: set[str], existing: dict):
    if not name or any(c.isspace() for c in name) or name.startswith(("/", "-")):
        raise ValueError("快捷指令名称须为一个不带前缀的词")
    if name in reserved:
        raise ValueError("不能覆盖内置快捷指令")
    if not argv or argv[0] in {"shortcut", "--shortcut", "sk", "skland", name} or argv[0] in existing:
        raise ValueError("快捷指令必须直接指向森空岛子命令，不能递归展开")
    if argv[0] not in {"bind", "qrcode", "unbind", "char", "card", "efcard", "box", "clue", "rogue", "rginfo", "gacha", "efgacha", "efwar", "import", "arksign", "efsign", "sync", "background", "help"}:
        raise ValueError("未知森空岛子命令")

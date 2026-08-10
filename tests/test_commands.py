import ast
from pathlib import Path


def test_all_source_shortcuts_are_registered():
    tree = ast.parse(Path("main.py").read_text("utf-8"))
    registered = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and decorator.args:
                value = decorator.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    registered.add(value.value)
    expected = {
        "skland",
        "森空岛绑定",
        "扫码绑定",
        "扫码登录",
        "森空岛解绑",
        "明日方舟签到",
        "签到详情",
        "全体签到",
        "全体签到详情",
        "终末地签到",
        "终末地签到详情",
        "终末地全体签到",
        "终末地全体签到详情",
        "角色更新",
        "全体角色更新",
        "资源更新",
        "方舟抽卡记录",
        "导入抽卡记录",
        "终末地抽卡记录",
        "终末地抽卡更新",
        "战绩详情",
        "收藏战绩详情",
        "树海肉鸽",
        "界园肉鸽",
        "萨卡兹肉鸽",
        "萨米肉鸽",
        "水月肉鸽",
        "傀影肉鸽",
        "ef",
        "zmd",
        "方舟干员",
    }
    assert expected <= registered


def test_rendered_cards_are_sent_as_native_image_bytes():
    source = Path("main.py").read_text("utf-8")
    assert "Image.fromBytes(prepared)" in source
    assert "image_output.prepare" in source
    assert "event.plain_result(content)" not in source


def test_qrcode_reply_mentions_sender_name():
    source = Path("main.py").read_text("utf-8")
    assert "event.get_sender_name()" in source
    assert "{sender_name} 请使用森空岛 App 扫码" in source


def test_box_renders_only_the_requested_page():
    source = Path("main.py").read_text("utf-8")
    assert "parse_box_page_arguments(args)" in source
    assert "cards = roster.cards[start : start + page_size]" in source
    assert "for index, cards in enumerate(pages, 1)" not in source


def test_greedy_command_arguments_are_not_shadowed_by_string_defaults():
    source = Path("main.py").read_text("utf-8")
    assert 'GreedyStr = ""' not in source
    assert "shortcut_box(self, event: AstrMessageEvent, args: GreedyStr)" in source

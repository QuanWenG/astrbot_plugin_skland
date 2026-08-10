import pytest

from skland.box_pagination import parse_box_page_arguments


@pytest.mark.parametrize(
    ("args", "page", "query_args"),
    [
        ([], 1, ()),
        (["2"], 2, ()),
        (["近卫", "2"], 2, ("近卫",)),
        (["范围", "3", "未拥有"], 3, ("未拥有",)),
        (["page", "4", "6星"], 4, ("6星",)),
    ],
)
def test_box_page_syntax(args, page, query_args):
    parsed = parse_box_page_arguments(args)
    assert parsed.page == page
    assert parsed.query_args == query_args


@pytest.mark.parametrize("args", [["0"], ["范围"], ["范围", "近卫"], ["2", "3"]])
def test_invalid_box_page_syntax(args):
    with pytest.raises(ValueError):
        parse_box_page_arguments(args)

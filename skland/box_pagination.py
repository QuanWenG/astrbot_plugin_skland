from __future__ import annotations

from dataclasses import dataclass


PAGE_MARKERS = frozenset({"页", "页码", "page", "范围"})


@dataclass(frozen=True, slots=True)
class BoxPageArguments:
    page: int
    query_args: tuple[str, ...]


def parse_box_page_arguments(args: list[str]) -> BoxPageArguments:
    """Separate the one-based Box page number from filter arguments."""
    page: int | None = None
    query_args: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token.casefold() in PAGE_MARKERS:
            if index + 1 >= len(args) or not args[index + 1].isdigit():
                raise ValueError(f"{token} 后需要填写正整数页码")
            candidate = int(args[index + 1])
            index += 2
        elif token.isdigit():
            candidate = int(token)
            index += 1
        else:
            query_args.append(token)
            index += 1
            continue

        if candidate < 1:
            raise ValueError("Box 页码必须从 1 开始")
        if page is not None and page != candidate:
            raise ValueError("一次只能指定一个 Box 页码")
        page = candidate

    return BoxPageArguments(page=page or 1, query_args=tuple(query_args))

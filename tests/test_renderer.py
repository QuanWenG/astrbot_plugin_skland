from pathlib import Path

import pytest

from skland.cards import render_clue_board
from skland.renderer import renderer
from skland.schemas import Clue


@pytest.mark.asyncio
async def test_local_renderer_creates_png(tmp_path):
    renderer.configure(tmp_path)
    try:
        png = await renderer.template_to_pic(
            template_path=str(Path(__file__).parent / "fixtures"),
            template_name="smoke.html.jinja2",
            templates={"title": "Skland", "value": "image"},
            filters={"upper": str.upper},
            pages={"viewport": {"width": 640, "height": 200}},
        )
    finally:
        await renderer.close()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1_000


@pytest.mark.asyncio
async def test_bundled_clue_card_template_creates_png(tmp_path):
    renderer.configure(
        tmp_path,
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    clue = Clue(
        own=3,
        received=1,
        dailyReward=True,
        needReceive=0,
        board=["RHINE", "PENGUIN", "RHODES"],
        sharing=False,
        shareCompleteTime=0,
    )
    image = await render_clue_board(clue)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 10_000
    await renderer.close()

"""Real Chromium rendering acceptance for the three newly ported cards."""
from io import BytesIO
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from skland.binding import AccountBindings
from skland.models import Character
from skland.cards import render_bound_roles_card, render_ef_gacha_history, render_ef_war_echoes
from skland.image_output import image_output
from skland.renderer import renderer
from skland.schemas import EfGachaView, WarEchoesView
from skland.schemas.endfield.gacha.statistics import EfGroupedGachaRecord
from skland.store import SklandStore
from test_account_migration import add_account
from test_ef_gacha_view import _pool, _pull
from test_war_echoes import _war_echoes_data


@pytest.mark.asyncio
async def test_render_role_gacha_and_war_cards_with_page_boundaries(tmp_path, monkeypatch):
    output = Path(os.environ.get("SKLAND_QA_DIR", str(tmp_path / "images")))
    output.mkdir(parents=True, exist_ok=True)
    renderer.configure(tmp_path)
    await renderer.start()
    new_context = renderer._browser.new_context
    diagnostics = []

    async def context_with_checks(**kwargs):
        context = await new_context(**kwargs)
        await context.route("https://**/*", lambda route: route.abort())
        close = context.close
        async def checked_close():
            for page in context.pages:
                diagnostics.append(await page.evaluate("""() => ({
                    title: document.title,
                    fonts: document.fonts.status,
                    missingLocalImages: [...document.images].filter(i => i.src.startsWith('file:') && !i.naturalWidth).map(i => i.src),
                    pages: [...document.querySelectorAll('#ef-pages > .ef-page')].map(p => ({
                        width: p.getBoundingClientRect().width, height: p.getBoundingClientRect().height,
                        columns: [...p.querySelectorAll('.ef-column')].map(c => c.getBoundingClientRect().x),
                        keys: [...p.querySelectorAll('[data-event-key]')].map(e => e.dataset.eventKey)
                    }))
                })"""))
            await close()
        monkeypatch.setattr(context, "close", checked_close)
        return context
    monkeypatch.setattr(renderer._browser, "new_context", context_with_checks)

    def save(name, content):
        (output / name).write_bytes(content)
        assert len(content) > 1000
        with Image.open(BytesIO(content)) as image:
            assert image.width > 400
        compressed = image_output.prepare("qq_official", content, name)
        with Image.open(BytesIO(compressed)) as image:
            assert max(image.size) <= 4096
        assert len(compressed) <= 4194304
        (output / ("qq-" + name)).write_bytes(compressed)

    try:
        store = SklandStore(tmp_path / "db")
        await store.initialize()
        await add_account(store)
        second, ark = await add_account(store, "第二个森空岛账号", server="2")
        await store.replace_characters("bot:u", [ark,
            Character("bot:u", "binding", "20001", "endfield", "1", "管理员", server_name="China", level=60, is_skland_default=True)], second.id)
        save("roles.png", await render_bound_roles_card(await AccountBindings(store).overview("bot:u")))
        record = EfGroupedGachaRecord(
            special_pools=[_pool("special_long", [(1700000000 + i * 1000, [
                _pull(i * 3, 5, "五星角色"), _pull(i * 3 + 1, 6, "莱万汀", is_free=i % 4 == 0),
                _pull(i * 3 + 2, 6, "洁尔佩塔")]) for i in range(50)])],
            weapon_pools=[_pool("weapon_1", [(1700000100, [_pull(1, 6, "熔铸火焰")])])],
            joint_pools=[_pool("joint_1", [(1700000200, [_pull(1, 6, "联合寻访")])])],
        )
        view = EfGachaView.from_record(record, nickname="测试管理员", role_id="123456", server_name="China",
            notice="同步失败，本次展示本地缓存", is_cached=True)
        images = await render_ef_gacha_history(view)
        assert len(images) > 1
        for i, image in enumerate(images, 1):
            save(f"gacha-{i}.png", image)
            with Image.open(BytesIO(image)) as decoded:
                assert decoded.width == 1200 and decoded.height <= 2400
        data = _war_echoes_data()
        for season in data.seasons:
            for week in season.weeks:
                for group in week.dungeonGroups:
                    if group.selected_dungeon and group.selected_dungeon.bestRecord:
                        for char in group.selected_dungeon.bestRecord.chars:
                            char.avatarUrl = (Path(__file__).parents[1] / "skland/resources/images/endfield/war_echoes/operator_empty.png").as_uri()
        save("war.png", await render_ef_war_echoes(WarEchoesView.from_data(data, nickname="管理员", season_id=3, week_id=1)))
        save("war-empty.png", await render_ef_war_echoes(WarEchoesView.from_data(data, nickname="管理员", season_id=3, week_id=2)))
    finally:
        await renderer.close()
        (output / "diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), "utf-8")
    assert all(item["fonts"] == "loaded" for item in diagnostics)
    assert not [url for item in diagnostics for url in item["missingLocalImages"]]
    pages = [page for item in diagnostics for page in item["pages"]]
    assert len(pages) == len(images)
    assert all(page["width"] == 800 and page["height"] <= 1600 for page in pages)
    assert all(len(page["columns"]) == 3 and page["columns"] == sorted(set(page["columns"])) for page in pages)
    rendered_keys = [key for page in pages for key in page["keys"]]
    assert sorted(rendered_keys) == sorted(event.key for pool in view.pools for event in pool.events)
    assert len(rendered_keys) == len(set(rendered_keys))

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

import skland.service as service_module
from skland.models import Character
from skland.service import ARKNIGHTS, SklandService


@pytest.mark.asyncio
async def test_operator_rosters_fetches_every_bound_arknights_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = [
        Character(
            owner_id="bot:user",
            uid="shared-uid",
            role_id=None,
            app_code=ARKNIGHTS,
            channel_master_id="1",
            nickname="官服博士",
            isdefault=True,
        ),
        Character(
            owner_id="bot:user",
            uid="shared-uid",
            role_id=None,
            app_code=ARKNIGHTS,
            channel_master_id="2",
            nickname="B服博士",
        ),
    ]
    store = SimpleNamespace(get_characters=AsyncMock(return_value=roles))
    service = SklandService(store)
    account = object()
    service.require_account = AsyncMock(  # type: ignore[method-assign]
        return_value=account
    )

    catalog = SimpleNamespace(entries=[object()])
    monkeypatch.setattr(service_module.game_data, "operator_catalog", catalog)
    monkeypatch.setattr(service_module.game_data, "variant_groups_checked", False)
    load_game_data = AsyncMock()
    monkeypatch.setattr(service_module.game_data, "load", load_game_data)
    query = object()
    monkeypatch.setattr(
        service_module,
        "OperatorRosterQuery",
        SimpleNamespace(from_input=lambda selected: query),
    )
    monkeypatch.setattr(
        service_module,
        "OperatorRoster",
        SimpleNamespace(
            build=lambda *, status, **_kwargs: f"roster:{status}",
        ),
    )
    cards = [
        SimpleNamespace(status="official", chars=[], equipmentInfoMap={}),
        SimpleNamespace(status="bilibili", chars=[], equipmentInfoMap={}),
    ]
    get_card = AsyncMock(side_effect=cards)
    monkeypatch.setattr(service_module.ark_card_cache, "get", get_card)

    results = await service.operator_rosters("bot:user")

    assert results == [
        (roles[0], "roster:official"),
        (roles[1], "roster:bilibili"),
    ]
    store.get_characters.assert_awaited_once_with("bot:user", ARKNIGHTS)
    service.require_account.assert_awaited_once_with("bot:user")
    load_game_data.assert_awaited_once_with()
    assert get_card.await_args_list == [
        call("bot:user", "1:shared-uid", get_card.await_args_list[0].args[2]),
        call("bot:user", "2:shared-uid", get_card.await_args_list[1].args[2]),
    ]

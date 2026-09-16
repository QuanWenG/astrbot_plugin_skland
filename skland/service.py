import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter

from .api import SklandAPI, SklandLoginAPI
from .card_cache import ark_card_cache
from .exception import LoginException, RequestException, UnauthorizedException
from .models import Account, Character, GachaRecord
from .resourcesync import game_data
from .schemas import (
    CRED,
    EfGroupedGachaRecord,
    EndfieldPoolType,
    OperatorRoster,
    OperatorRosterQuery,
    RogueData,
)
from .schemas.endfield.gacha.base import EfGachaContentPool
from .store import SklandStore
from .binding import AccountBindings, roles_from_apps
from .schemas import WarEchoesView, EfGachaView
from .gacha import group_endfield_records

T = TypeVar("T")
ARKNIGHTS = "arknights"
ENDFIELD = "endfield"
logger = logging.getLogger("astrbot")


def _ark_card_cache_subject(character: Character) -> str:
    return f"{character.account_id}:{character.app_code}:{character.channel_master_id}:{character.role_id or character.uid}:{character.uid}"


def _catalog_gaps(characters: list[Character]) -> list[str]:
    catalog = getattr(game_data.operator_catalog, "by_id", {})
    return sorted(
        {
            char_id
            for character in characters
            if (char_id := str(character.charId).strip())
            and (
                (entry := catalog.get(char_id)) is None
                or not str(getattr(entry, "profession", "")).strip()
            )
        }
    )


class SklandService:
    """Framework-independent application service for every command use case."""

    def __init__(self, store: SklandStore) -> None:
        self.store = store
        self.bindings = AccountBindings(store)

    async def _ensure_operator_catalog(self, characters: list[Character]) -> None:
        if (
            not game_data.operator_catalog.entries
            or not getattr(game_data, "variant_groups_checked", True)
        ):
            await game_data.load()

        gaps = _catalog_gaps(characters)
        if not gaps:
            return

        logger.warning(
            "森空岛返回了本地干员目录缺少的干员，强制刷新游戏数据：%s",
            ", ".join(gaps),
        )
        await game_data.load(force=True)
        gaps = _catalog_gaps(characters)
        if gaps:
            raise RequestException(
                "游戏数据目录仍缺少账号中的干员职业信息："
                f"{', '.join(gaps)}。请执行“资源更新 --data --force”后重试。"
            )

    async def bind(self, owner_id: str, secret: str) -> list[Character]:
        """Programmatic binding. Interactive commands use prepare/confirm/commit."""
        async with self.bindings.exclusive(owner_id):
            prepared = await self.bindings.prepare(owner_id, secret)
            await self.bindings.commit(prepared)
            await ark_card_cache.invalidate_owner(owner_id)
            return await self.store.get_characters(owner_id)

    async def bind_scan_token(self, owner_id: str, token: str) -> list[Character]:
        return await self.bind(owner_id, token)

    async def unbind(self, owner_id: str, account_ids=None, *, expected_version=None) -> bool:
        deleted = await self.store.delete_account(owner_id, account_ids, expected_version=expected_version)
        await ark_card_cache.invalidate_owner(owner_id)
        return deleted

    async def require_account(self, owner_id: str, account_id: int | None = None) -> Account:
        account = await self.store.get_account(owner_id, account_id)
        if not account:
            raise ValueError("尚未绑定森空岛账号，请使用 /扫码绑定")
        return account

    async def sync_characters(self, owner_id: str) -> list[Character]:
        failures = []
        async with self.bindings.exclusive(owner_id):
            accounts = await self.store.list_accounts(owner_id)
            if not accounts:
                raise ValueError("尚未绑定森空岛账号")
            for account in accounts:
                try:
                    if not account.user_id:
                        account.user_id = await self._with_refresh(account, SklandAPI.get_user_ID)
                        await self.store.save_account(account)
                    version = await self.store.owner_version(owner_id)
                    apps = await self._with_refresh(account, SklandAPI.get_binding)
                    await self.store.replace_characters(owner_id, roles_from_apps(account, apps), account.id, expected_version=version)
                    await ark_card_cache.invalidate_owner(owner_id)
                except Exception as exc:
                    logger.warning("账号 %s 同步失败：%s", account.id, type(exc).__name__)
                    failures.append(f"账号 {account.id}: {exc}")
        if failures:
            raise RequestException("其他账号已独立同步；以下账号保留原角色：" + "；".join(failures))
        return await self.store.get_characters(owner_id)

    async def require_character(
        self, owner_id: str, game: str, identity: str | None = None
    ) -> Character:
        chars = await self.store.get_characters(owner_id, game)
        if identity:
            if isinstance(identity, int):
                if identity < 1 or identity > len(chars):
                    raise ValueError("角色序号无效，请查看 /sk char")
                return chars[identity - 1]
            chars = [c for c in chars if identity in {c.uid, c.role_id, c.nickname}]
            if len(chars) == 1:
                return chars[0]
            raise ValueError("角色标识不唯一或不存在，请使用 -r 序号")
        if not chars:
            raise ValueError(f"未找到 {game} 角色，请先使用 角色更新")
        selected = next((c for c in chars if c.isdefault), None)
        if selected is None:
            raise ValueError("尚未设置默认角色，请查看 /sk char 并使用 char set 切换")
        return selected

    async def card(
        self, owner_id: str, game: str, identity: str | None = None
    ) -> tuple[Character, Any]:
        char = await self.require_character(owner_id, game, identity)
        return char, await self._card_for_character(char)

    async def _card_for_character(self, char: Character):
        owner_id, game = char.owner_id, char.app_code
        account = await self.require_account(owner_id, char.account_id)
        if game == ARKNIGHTS:
            card = await ark_card_cache.get(
                owner_id,
                _ark_card_cache_subject(char),
                lambda: self._with_refresh(
                    account, lambda cred: SklandAPI.ark_card(cred, char.uid)
                ),
            )
        else:
            card = await self._with_refresh(
                account,
                lambda cred: SklandAPI.endfield_card(cred, account.user_id or "", char),
            )
        return card

    async def operator_roster(
        self,
        owner_id: str,
        *,
        identity: int | None = None,
        character: Character | None = None,
        filters: tuple[str, ...] = (),
        options: dict[str, str] | None = None,
    ) -> OperatorRoster:
        if character is not None:
            if character.owner_id != owner_id or character.app_code != ARKNIGHTS:
                raise ValueError("角色归属不匹配")
            card = await self._card_for_character(character)
        else:
            _, card = await self.card(owner_id, ARKNIGHTS, identity)
        await self._ensure_operator_catalog(card.chars)
        query = OperatorRosterQuery.from_input(
            game_data.operator_catalog, filters=filters, **(options or {})
        )
        return OperatorRoster.build(
            status=card.status,
            catalog=game_data.operator_catalog,
            characters=card.chars,
            query=query,
            equipment_map=card.equipmentInfoMap,
        )

    async def operator_rosters(
        self,
        owner_id: str,
        *,
        allow_empty: bool = False,
    ) -> list[tuple[Character, OperatorRoster]]:
        """Build a complete roster for every bound Arknights role."""
        roles = await self.store.get_characters(owner_id, ARKNIGHTS)
        if not roles:
            if allow_empty:
                return []
            raise ValueError(f"未找到 {ARKNIGHTS} 角色，请先使用 角色更新")
        role_cards: list[tuple[Character, Any]] = []
        all_characters: list[Any] = []
        unique = {}
        for role in sorted(roles, key=lambda r: (not r.isdefault, r.account_id or 0)):
            unique.setdefault((role.channel_master_id, role.uid), role)
        for role in unique.values():
            account = await self.require_account(owner_id, role.account_id)
            card = await ark_card_cache.get(
                owner_id,
                _ark_card_cache_subject(role),
                lambda current=role: self._with_refresh(
                    account,
                    lambda cred: SklandAPI.ark_card(cred, current.uid),
                ),
            )
            role_cards.append((role, card))
            all_characters.extend(card.chars)

        await self._ensure_operator_catalog(all_characters)
        query = OperatorRosterQuery.from_input(game_data.operator_catalog)
        return [
            (
                role,
                OperatorRoster.build(
                    status=card.status,
                    catalog=game_data.operator_catalog,
                    characters=card.chars,
                    query=query,
                    equipment_map=card.equipmentInfoMap,
                ),
            )
            for role, card in role_cards
        ]

    async def sign(
        self,
        owner_id: str,
        game: str,
        *,
        all_roles: bool = False,
        identity: str | None = None,
    ) -> list[tuple[Character, str]]:
        chars = await self.store.get_characters(owner_id, game)
        if identity or not all_roles:
            chars = [await self.require_character(owner_id, game, identity)]
        if not chars:
            raise ValueError(f"未找到 {game} 角色，请先使用 角色更新")
        results = []
        for char in chars:
            try:
                account = await self.require_account(owner_id, char.account_id)
                if game == ARKNIGHTS:
                    value = await self._with_refresh(
                        account,
                        lambda cred, c=char: SklandAPI.ark_sign(
                            cred, c.uid, c.channel_master_id
                        ),
                    )
                    awards = "、".join(
                        f"{award.resource.name} x{award.count}"
                        for award in value.awards
                    )
                else:
                    if not char.role_id:
                        raise ValueError("终末地角色缺少 roleId")
                    value = await self._with_refresh(
                        account,
                        lambda cred, c=char: SklandAPI.endfield_sign(
                            cred, c.role_id or "", c.channel_master_id
                        ),
                    )
                    awards = value.award_summary
                text = f"✅ {awards or '签到成功'}"
            except (RequestException, LoginException, UnauthorizedException, ValueError) as exc:
                text = f"❌ {exc}"
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self.store.save_sign_result(game, owner_id, char.nickname, text, now, character_id=char.id)
            results.append((char, text))
        return results

    async def sign_all_accounts(self, game: str) -> list[tuple[str, Character, str]]:
        output = []
        seen = set()
        for account in await self.store.list_accounts():
            if account.owner_id in seen:
                continue
            seen.add(account.owner_id)
            try:
                output.extend(
                    (account.owner_id, char, text)
                    for char, text in await self.sign(
                        account.owner_id, game, all_roles=True
                    )
                )
            except Exception as exc:
                output.append(
                    (
                        account.owner_id,
                        Character(account.owner_id, "", None, game, "", "未知角色"),
                        f"❌ {exc}",
                    )
                )
        return output

    async def rogue(
        self, owner_id: str, topic: str | None = None, identity: int | None = None
    ) -> tuple[Character, RogueData]:
        char = await self.require_character(owner_id, ARKNIGHTS, identity)
        account = await self.require_account(owner_id, char.account_id)
        topic_map = {
            "傀影": "rogue_1",
            "水月": "rogue_2",
            "萨米": "rogue_3",
            "萨卡兹": "rogue_4",
            "界园": "rogue_5",
            "黑流树海": "rogue_6",
        }
        topic_id = topic_map.get(topic or "", topic or "")
        data = await self._with_refresh(
            account,
            lambda cred: SklandAPI.get_rogue(
                CRED(cred=cred.cred, token=cred.token, userId=account.user_id),
                char.uid,
                topic_id,
            ),
        )
        await self.store.save_rogue_cache(owner_id, data.model_dump(mode="json"))
        return char, data

    async def rogue_detail(self, owner_id: str) -> RogueData:
        payload = await self.store.get_rogue_cache(owner_id)
        if not payload:
            raise ValueError("暂无肉鸽战绩缓存，请先查询一次肉鸽战绩")
        return TypeAdapter(RogueData).validate_python(payload)

    async def arknights_gacha(
        self, owner_id: str, identity: int | None = None
    ) -> tuple[Character, list[GachaRecord], int]:
        if not game_data.gacha_table:
            await game_data.load()
        char = await self.require_character(owner_id, ARKNIGHTS, identity)
        account = await self.require_account(owner_id, char.account_id)
        token = self._require_access_token(account)
        grant = await SklandLoginAPI.get_grant_code(token, 1)
        role_token = await SklandLoginAPI.get_role_token_by_uid(char.uid, grant)
        cookie = await SklandLoginAPI.get_ak_cookie(role_token)
        categories = await SklandAPI.get_gacha_categories(
            char.uid, role_token, token, cookie
        )
        fetched = []
        async with httpx.AsyncClient() as client:
            for category in categories:
                page = await SklandAPI.get_gacha_history(
                    char.uid, role_token, token, cookie, category.id, client=client
                )
                seen = set()
                while page and page.gacha_list:
                    fetched.extend(page.gacha_list)
                    cursor = (page.next_ts, page.next_pos)
                    if not page.hasMore or cursor in seen:
                        break
                    seen.add(cursor)
                    page = await SklandAPI.get_gacha_history(
                        char.uid,
                        role_token,
                        token,
                        cookie,
                        category.id,
                        gachaTs=page.next_ts,
                        pos=page.next_pos,
                        client=client,
                    )
        records = [
            GachaRecord(
                owner_id,
                char.uid,
                ARKNIGHTS,
                "char",
                item.poolId,
                item.poolName,
                item.charId,
                item.charName,
                item.rarity,
                item.isNew,
                False,
                item.gacha_ts_sec,
                item.pos,
                character_id=char.id,
            )
            for item in fetched
        ]
        added = await self.store.save_gacha_records(records)
        return (
            char,
            await self.store.get_gacha_records(owner_id, char.uid, ARKNIGHTS, character_id=char.id),
            added,
        )

    async def endfield_gacha(
        self, owner_id: str, *, update: bool = True, identity: int | None = None
    ) -> tuple[Character, list[GachaRecord], int]:
        char = await self.require_character(owner_id, ENDFIELD, identity)
        account = await self.require_account(owner_id, char.account_id)
        added = 0
        if update:
            token = self._require_access_token(account)
            grant = await SklandLoginAPI.get_grant_code(token, 1)
            role_token = await SklandLoginAPI.get_role_token_by_uid(char.uid, grant)
            pool_types = [
                EndfieldPoolType.STANDARD,
                EndfieldPoolType.SPECIAL,
                EndfieldPoolType.BEGINNER,
                EndfieldPoolType.JOINT,
                EndfieldPoolType.WEAPON,
            ]
            batches = await asyncio.gather(
                *(self._all_ef_records(pool, char, role_token) for pool in pool_types)
            )
            records = [
                GachaRecord(
                    owner_id,
                    char.uid,
                    ENDFIELD,
                    item.item_type,
                    item.poolId,
                    item.poolName,
                    item.item_id,
                    item.item_name,
                    item.rarity,
                    item.isNew,
                    item.is_free_pull,
                    item.gacha_ts_sec,
                    item.seq_id_int,
                    character_id=char.id,
                )
                for batch in batches
                for item in batch
            ]
            added = await self.store.save_gacha_records(records)
        cached = await self.store.get_gacha_records(owner_id, char.uid, ENDFIELD, character_id=char.id)
        return char, cached, added

    async def endfield_history_view(self, owner_id: str, *, identity=None, begin=None, limit=None) -> EfGachaView:
        char = await self.require_character(owner_id, ENDFIELD, identity)
        notice, is_cached, added = "", False, 0
        try:
            char, records, added = await self.endfield_gacha(owner_id, identity=identity, update=True)
        except (RequestException, LoginException, UnauthorizedException, ValueError, httpx.HTTPError) as exc:
            records = await self.store.get_gacha_records(owner_id, char.uid, ENDFIELD, character_id=char.id)
            if not records:
                raise RequestException(f"抽卡同步失败且暂无缓存：{exc}") from exc
            notice = "同步失败，本次展示本地缓存；请稍后重试或更新账号凭证"
            is_cached = True
        grouped = group_endfield_records(records)
        await self.enrich_endfield_pools(grouped, char)
        avatar = ""
        try:
            _, card = await self.card(owner_id, ENDFIELD, identity)
            avatar = card.base.avatarUrl
        except Exception as exc:
            logger.warning("终末地头像读取失败：%s", type(exc).__name__)
        return EfGachaView.from_record(grouped, nickname=char.nickname, role_id=char.role_id or char.uid,
            server_name=char.display_server, avatar_url=avatar, new_count=added, is_cached=is_cached,
            notice=notice, begin=begin, limit=limit)

    async def war_echoes(self, owner_id: str, *, identity=None, season_id=None, week_id=None) -> WarEchoesView:
        char = await self.require_character(owner_id, ENDFIELD, identity)
        account = await self.require_account(owner_id, char.account_id)
        if not account.user_id:
            raise ValueError("账号身份尚未同步，请执行 /sk char update")

        async def fetch(season):
            return await self._with_refresh(account, lambda cred: SklandAPI.endfield_war_echoes(
                cred, user_id=account.user_id, role_id=char.role_id or char.uid,
                server_id=char.channel_master_id, season_id=season))

        data = await fetch(None if season_id is not None and season_id < 0 else season_id)
        if season_id is not None and season_id < 0:
            season_id = data.select_season(season_id).id
            data = await fetch(season_id)
        avatar = ""
        try:
            _, card = await self.card(owner_id, ENDFIELD, identity)
            avatar = card.base.avatarUrl
        except Exception as exc:
            logger.warning("战争回响头像读取失败：%s", type(exc).__name__)
        return WarEchoesView.from_data(data, season_id=season_id, week_id=week_id, nickname=char.nickname,
            role_id=char.role_id or char.uid, server_name=char.display_server, avatar_url=avatar)

    async def import_heybox(self, owner_id: str, url: str, identity: int | None = None) -> tuple[int, int]:
        if not game_data.gacha_table:
            await game_data.load()
        char = await self.require_character(owner_id, ARKNIGHTS, identity)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RequestException(f"小黑盒记录下载失败：{exc}") from exc
        if str(payload.get("info", {}).get("uid")) != char.uid:
            raise ValueError("导入的抽卡记录与当前默认角色不匹配")
        records = []
        for timestamp, group in payload.get("data", {}).items():
            gacha_ts = int(timestamp)
            pool_name = group.get("p", "未知寻访")
            pool_id = game_data.pool_id(pool_name, gacha_ts)
            if pool_id == "NORM_1_0_1":
                pool_name = "未知寻访"
            for pos, item in enumerate(group.get("c", [])):
                char_name = "麒麟R夜刀" if str(item[0]) == "麒麟X夜刀" else str(item[0])
                records.append(
                    GachaRecord(
                        owner_id,
                        char.uid,
                        ARKNIGHTS,
                        "char",
                        pool_id,
                        pool_name,
                        game_data.char_id(char_name),
                        char_name,
                        int(item[1]),
                        bool(item[2]),
                        False,
                        gacha_ts,
                        pos,
                        character_id=char.id,
                    )
                )
        return len(records), await self.store.save_gacha_records(records)

    async def enrich_endfield_pools(
        self, grouped: EfGroupedGachaRecord, char: Character
    ) -> None:
        pools = grouped.special_pools + grouped.joint_pools + grouped.weapon_pools

        async def hydrate(pool) -> None:
            try:
                raw = game_data.ef_pool_table.get(pool.pool_id)
                if raw:
                    content_pool = EfGachaContentPool(**raw)
                else:
                    response = await SklandAPI.get_ef_gacha_content(
                        pool.pool_id, char.channel_master_id
                    )
                    content_pool = response.pool
                pool.up_six_chars = content_pool.up_six_char_ids
                pool.up6_img = content_pool.up6_image or content_pool.rotate_image
                pool.up6_name = content_pool.up_six_display_name
            except Exception as exc:
                logger.warning(
                    "获取终末地卡池 %s 的 UP 信息失败：%s", pool.pool_id, exc
                )

        await asyncio.gather(*(hydrate(pool) for pool in pools))

    async def _all_ef_records(
        self, pool_type, char: Character, token: str
    ) -> list[Any]:
        records = []
        page = await SklandAPI.get_ef_gacha_history(
            pool_type, char.channel_master_id, token
        )
        seen = set()
        while page and page.gacha_list:
            records.extend(page.gacha_list)
            cursor = page.next_seq
            if not page.hasMore or cursor in seen:
                break
            seen.add(cursor)
            page = await SklandAPI.get_ef_gacha_history(
                pool_type, char.channel_master_id, token, seq_id=cursor
            )
        return records

    @staticmethod
    def _require_access_token(account: Account) -> str:
        if not account.access_token:
            raise ValueError(
                "抽卡查询需要 24 位 token，请用 森空岛绑定 <token> 更新绑定"
            )
        return account.access_token

    async def _with_refresh(
        self, account: Account, request: Callable[[CRED], Awaitable[T]]
    ) -> T:
        def cred() -> CRED:
            return CRED(account.cred, account.cred_token, account.user_id)

        original = (account.cred, account.cred_token)
        try:
            return await request(cred())
        except UnauthorizedException:
            account.cred_token = await SklandLoginAPI.refresh_token(account.cred)
            await self.store.save_refreshed_credentials(account, original)
            return await request(cred())
        except LoginException:
            if not account.access_token:
                raise RequestException("cred 已失效且未保存 token，无法自动刷新")
            grant = await SklandLoginAPI.get_grant_code(account.access_token, 0)
            new_cred = await SklandLoginAPI.get_cred(grant)
            if account.user_id and new_cred.userId and account.user_id != new_cred.userId:
                raise RequestException("刷新后的账号身份不一致，请重新绑定")
            account.cred, account.cred_token = new_cred.cred, new_cred.token
            account.user_id = new_cred.userId or account.user_id
            await self.store.save_refreshed_credentials(account, original)
            return await request(cred())

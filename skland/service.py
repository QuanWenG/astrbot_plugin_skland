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

T = TypeVar("T")
ARKNIGHTS = "arknights"
ENDFIELD = "endfield"
logger = logging.getLogger("astrbot")


def _ark_card_cache_subject(character: Character) -> str:
    return f"{character.channel_master_id}:{character.uid}"


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
        secret = secret.strip()
        if len(secret) == 24:
            grant_code = await SklandLoginAPI.get_grant_code(secret, 0)
            cred = await SklandLoginAPI.get_cred(grant_code)
            account = Account(owner_id, secret, cred.cred, cred.token, cred.userId)
        elif len(secret) == 32:
            cred_token = await SklandLoginAPI.refresh_token(secret)
            user_id = await SklandAPI.get_user_ID(CRED(cred=secret, token=cred_token))
            old = await self.store.get_account(owner_id)
            account = Account(
                owner_id, old.access_token if old else None, secret, cred_token, user_id
            )
        else:
            raise ValueError("token 应为 24 位，cred 应为 32 位")
        await self.store.save_account(account)
        await ark_card_cache.invalidate_owner(owner_id)
        return await self.sync_characters(owner_id)

    async def bind_scan_token(self, owner_id: str, token: str) -> list[Character]:
        return await self.bind(owner_id, token)

    async def unbind(self, owner_id: str) -> bool:
        deleted = await self.store.delete_account(owner_id)
        await ark_card_cache.invalidate_owner(owner_id)
        return deleted

    async def require_account(self, owner_id: str) -> Account:
        account = await self.store.get_account(owner_id)
        if not account:
            raise ValueError("尚未绑定森空岛账号，请使用 /扫码绑定")
        return account

    async def sync_characters(self, owner_id: str) -> list[Character]:
        account = await self.require_account(owner_id)
        apps = await self._with_refresh(account, SklandAPI.get_binding)
        chars: list[Character] = []
        for app in apps:
            for binding in app.bindingList:
                if binding.roles:
                    chars.extend(
                        Character(
                            owner_id,
                            binding.uid,
                            role.roleId,
                            app.appCode,
                            role.serverId,
                            role.nickname,
                            len(binding.roles) == 1 or role.isDefault,
                        )
                        for role in binding.roles
                    )
                else:
                    chars.append(
                        Character(
                            owner_id,
                            binding.uid,
                            None,
                            app.appCode,
                            binding.channelMasterId,
                            binding.nickName,
                            len(app.bindingList) == 1 or binding.isDefault,
                        )
                    )
        await self.store.replace_characters(owner_id, chars)
        await ark_card_cache.invalidate_owner(owner_id)
        return chars

    async def require_character(
        self, owner_id: str, game: str, identity: str | None = None
    ) -> Character:
        chars = await self.store.get_characters(owner_id, game)
        if identity:
            chars = [c for c in chars if identity in {c.uid, c.role_id, c.nickname}]
        if not chars:
            raise ValueError(f"未找到 {game} 角色，请先使用 角色更新")
        return next((c for c in chars if c.isdefault), chars[0])

    async def card(
        self, owner_id: str, game: str, identity: str | None = None
    ) -> tuple[Character, Any]:
        account = await self.require_account(owner_id)
        char = await self.require_character(owner_id, game, identity)
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
        return char, card

    async def operator_roster(
        self,
        owner_id: str,
        *,
        filters: tuple[str, ...] = (),
        options: dict[str, str] | None = None,
    ) -> OperatorRoster:
        _, card = await self.card(owner_id, ARKNIGHTS)
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
    ) -> list[tuple[Character, OperatorRoster]]:
        """Build a complete roster for every bound Arknights role."""
        account = await self.require_account(owner_id)
        roles = await self.store.get_characters(owner_id, ARKNIGHTS)
        if not roles:
            raise ValueError(f"未找到 {ARKNIGHTS} 角色，请先使用 角色更新")
        role_cards: list[tuple[Character, Any]] = []
        all_characters: list[Any] = []
        for role in roles:
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
        account = await self.require_account(owner_id)
        chars = await self.store.get_characters(owner_id, game)
        if identity:
            chars = [c for c in chars if identity in {c.uid, c.role_id, c.nickname}]
        elif not all_roles:
            chars = [next((c for c in chars if c.isdefault), chars[0])] if chars else []
        if not chars:
            raise ValueError(f"未找到 {game} 角色，请先使用 角色更新")
        results = []
        for char in chars:
            try:
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
            except (RequestException, LoginException, UnauthorizedException) as exc:
                text = f"❌ {exc}"
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self.store.save_sign_result(game, owner_id, char.nickname, text, now)
            results.append((char, text))
        return results

    async def sign_all_accounts(self, game: str) -> list[tuple[str, Character, str]]:
        output = []
        for account in await self.store.list_accounts():
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
        self, owner_id: str, topic: str | None = None
    ) -> tuple[Character, RogueData]:
        account = await self.require_account(owner_id)
        char = await self.require_character(owner_id, ARKNIGHTS)
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
        self, owner_id: str
    ) -> tuple[Character, list[GachaRecord], int]:
        if not game_data.gacha_table:
            await game_data.load()
        account = await self.require_account(owner_id)
        char = await self.require_character(owner_id, ARKNIGHTS)
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
            )
            for item in fetched
        ]
        added = await self.store.save_gacha_records(records)
        return (
            char,
            await self.store.get_gacha_records(owner_id, char.uid, ARKNIGHTS),
            added,
        )

    async def endfield_gacha(
        self, owner_id: str, *, update: bool = False
    ) -> tuple[Character, list[GachaRecord], int]:
        account = await self.require_account(owner_id)
        char = await self.require_character(owner_id, ENDFIELD)
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
                )
                for batch in batches
                for item in batch
            ]
            added = await self.store.save_gacha_records(records)
        cached = await self.store.get_gacha_records(owner_id, char.uid, ENDFIELD)
        if not cached:
            raise ValueError("暂无终末地抽卡记录，请使用 终末地抽卡更新")
        return char, cached, added

    async def import_heybox(self, owner_id: str, url: str) -> tuple[int, int]:
        if not game_data.gacha_table:
            await game_data.load()
        char = await self.require_character(owner_id, ARKNIGHTS)
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

        try:
            return await request(cred())
        except UnauthorizedException:
            account.cred_token = await SklandLoginAPI.refresh_token(account.cred)
            await self.store.save_account(account)
            return await request(cred())
        except LoginException:
            if not account.access_token:
                raise RequestException("cred 已失效且未保存 token，无法自动刷新")
            grant = await SklandLoginAPI.get_grant_code(account.access_token, 0)
            new_cred = await SklandLoginAPI.get_cred(grant)
            account.cred, account.cred_token = new_cred.cred, new_cred.token
            account.user_id = new_cred.userId or account.user_id
            await self.store.save_account(account)
            return await request(cred())

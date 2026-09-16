"""Detached account previews; commands commit only the confirmed projection."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from .api import SklandAPI, SklandLoginAPI
from .models import Account, Character
from .schemas import CRED
from .schemas.binding import BindingAccountSnapshot, BoundRoleCardAccount, BoundRoleCardItem, BoundRolesCard

GAME_NAMES = {"arknights": "明日方舟", "endfield": "终末地"}


def roles_from_apps(account: Account, apps) -> list[Character]:
    snapshot = BindingAccountSnapshot.from_apps(account.user_id or "", apps)
    return [Character(account.owner_id, r.binding_uid, r.game_role_id, r.app_code, r.server_id,
        r.nickname, account_id=account.id, server_name=r.server_name, level=r.level,
        is_available=r.is_available, is_skland_default=r.is_skland_default) for r in snapshot.roles]


def role_key(role: Character):
    return role.app_code, role.channel_master_id, role.role_id or role.uid


@dataclass
class PreparedBinding:
    account: Account
    characters: list[Character]
    version: int
    card: BoundRolesCard


class AccountBindings:
    def __init__(self, store):
        self.store = store
        self._busy: set[str] = set()

    @asynccontextmanager
    async def exclusive(self, owner: str):
        if owner in self._busy:
            raise ValueError("已有账号管理操作进行中，请先完成或取消")
        self._busy.add(owner)
        try:
            yield
        finally:
            self._busy.discard(owner)

    async def prepare(self, owner: str, secret: str) -> PreparedBinding:
        version = await self.store.owner_version(owner)
        secret = secret.strip()
        if len(secret) == 24:
            grant = await SklandLoginAPI.get_grant_code(secret, 0)
            cred = await SklandLoginAPI.get_cred(grant)
            remote = cred.userId or await SklandAPI.get_user_ID(cred)
            candidate = Account(owner, secret, cred.cred, cred.token, remote)
        elif len(secret) == 32:
            token = await SklandLoginAPI.refresh_token(secret)
            remote = await SklandAPI.get_user_ID(CRED(secret, token))
            candidate = Account(owner, None, secret, token, remote)
        else:
            raise ValueError("token 应为 24 位，cred 应为 32 位")
        if not remote:
            raise ValueError("无法确认森空岛账号身份，未保存账号")
        accounts = await self.store.list_accounts(owner)
        matching = []
        for account in accounts:
            identity = account.user_id
            if not identity:
                try:
                    identity = await SklandAPI.get_user_ID(CRED(account.cred, account.cred_token))
                except Exception as exc:
                    raise ValueError("旧账号身份校验失败，请先角色更新或解绑异常账号") from exc
            if identity == remote:
                matching.append(account)
        if len(matching) > 1:
            raise ValueError("检测到重复账号身份，请先处理异常绑定")
        if matching:
            candidate.id = matching[0].id
            candidate.access_token = candidate.access_token or matching[0].access_token
        apps = await SklandAPI.get_binding(CRED(candidate.cred, candidate.cred_token, remote))
        roles = roles_from_apps(candidate, apps)
        if candidate.id is None and not any(r.is_available for r in roles):
            raise ValueError("未找到可绑定的明日方舟或终末地角色，未保存账号")
        card = await self.overview(owner, candidate=candidate, candidate_roles=roles)
        return PreparedBinding(candidate, roles, version, card)

    async def overview(self, owner: str, *, candidate=None, candidate_roles=(), unbind_ids=(), mode=None):
        accounts = await self.store.list_accounts(owner)
        roles = await self.store.get_characters(owner, include_unavailable=True)
        before = {r.app_code for r in roles if r.is_available}
        defaults = {(r.account_id, role_key(r)) for r in roles if r.isdefault}
        if candidate is not None:
            roles = [r for r in roles if candidate.id is None or r.account_id != candidate.id]
            roles.extend(replace(r, isdefault=(candidate.id, role_key(r)) in defaults) for r in candidate_roles)
            accounts = [a for a in accounts if candidate.id is None or a.id != candidate.id] + [candidate]
            accounts.sort(key=lambda a: a.id if a.id is not None else float("inf"))
            for game in GAME_NAMES:
                if game in before:
                    continue
                candidates = [r for r in roles if r.app_code == game and r.is_available]
                remote = [r for r in candidates if r.is_skland_default]
                chosen = remote[0] if len(remote) == 1 else candidates[0] if len(candidates) == 1 else None
                if chosen:
                    chosen.isdefault = True
        counts = {game: 0 for game in GAME_NAMES}
        cards = []
        for index, account in enumerate(accounts, 1):
            items = []
            for role in sorted((r for r in roles if r.account_id == account.id), key=role_key):
                game = role.app_code
                # Legacy databases can contain other Skland games. Keep their
                # rows/history, but only number and render supported games.
                if game not in GAME_NAMES:
                    continue
                number = None
                if role.is_available:
                    counts[game] += 1
                    number = counts[game]
                items.append(BoundRoleCardItem(app_code=game, app_name=GAME_NAMES[game], nickname=role.nickname,
                    binding_uid=role.uid, game_role_id=role.role_id or role.uid, server_id=role.channel_master_id,
                    server_name=role.display_server, level=role.level, is_skland_default=bool(role.is_skland_default),
                    is_available=bool(role.is_available), unavailable_reason=None if role.is_available else "角色不可用",
                    index=number, is_local_default=bool(role.isdefault and account.id not in unbind_ids)))
            state = "bound"
            if candidate is account:
                state = "pending_update" if account.id is not None else "pending_bind"
            if account.id in unbind_ids:
                state = "pending_unbind"
            cards.append(BoundRoleCardAccount(account_id=account.id, index=index, account_user_id=account.user_id,
                account_hint=f"••••{account.user_id[-4:]}" if account.user_id else "待同步", state=state, roles=items))
        return BoundRolesCard(mode=mode or ("bind_confirmation" if candidate is not None else "overview"), accounts=cards)

    async def commit(self, prepared: PreparedBinding):
        await self.store.commit_binding(prepared.account, prepared.characters, prepared.version)

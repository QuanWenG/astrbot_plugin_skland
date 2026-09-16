from dataclasses import dataclass


@dataclass(slots=True)
class Account:
    owner_id: str
    access_token: str | None
    cred: str
    cred_token: str
    user_id: str | None
    id: int | None = None


@dataclass(slots=True)
class Character:
    owner_id: str
    uid: str
    role_id: str | None
    app_code: str
    channel_master_id: str
    nickname: str
    isdefault: bool = False
    id: int | None = None
    account_id: int | None = None
    server_name: str = ""
    level: int | None = None
    is_available: bool = True
    is_skland_default: bool = False

    @property
    def display_server(self) -> str:
        return self.server_name or {"1": "官服", "2": "B服"}.get(
            self.channel_master_id, f"未知区服（{self.channel_master_id}）"
        )


@dataclass(slots=True)
class GachaRecord:
    owner_id: str
    char_uid: str
    app_code: str
    item_type: str
    pool_id: str
    pool_name: str
    char_id: str
    char_name: str
    rarity: int
    is_new: bool
    is_free: bool
    gacha_ts: int
    pos: int
    character_id: int | None = None

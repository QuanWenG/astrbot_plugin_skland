from dataclasses import dataclass


@dataclass(slots=True)
class Account:
    owner_id: str
    access_token: str | None
    cred: str
    cred_token: str
    user_id: str | None


@dataclass(slots=True)
class Character:
    owner_id: str
    uid: str
    role_id: str | None
    app_code: str
    channel_master_id: str
    nickname: str
    isdefault: bool = False


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

"""Models for the Skland Stronghold Protocol history endpoint."""

from pydantic import BaseModel, Field

GARRISON_SEASON = "act3autochess"

class GarrisonChessInfo(BaseModel):
    charId: str = ""
    charName: str = ""
    chessLevel: int = 0
    charLevel: int | None = None
    evolvePhase: int | None = None


class GarrisonEquipment(BaseModel):
    equipId: str = ""


class GarrisonChess(BaseModel):
    charId: str = ""
    chessId: str = ""
    mergeLevel: int | None = None
    equips: list[GarrisonEquipment] | None = None
    chessInfo: GarrisonChessInfo = Field(default_factory=GarrisonChessInfo)

    @property
    def character_id(self) -> str:
        return self.charId or self.chessInfo.charId


class GarrisonBond(BaseModel):
    bondId: str = ""
    layer: int = 0
    activate: int = 0


class GarrisonBoss(BaseModel):
    bossId: str = ""
    isHidden: bool = False
    isKilled: bool = False


class GarrisonRecord(BaseModel):
    id: str = ""
    completeState: int
    passRound: int = 0
    gameEndTs: int = 0
    gameStartTs: int = 0
    trophyAfter: int | None = None
    acBossInfo: list[GarrisonBoss] = Field(default_factory=list)
    modeType: str = ""
    modeDifficulty: str = ""
    bandId: str = ""
    chessSquad: list[GarrisonChess] = Field(default_factory=list)
    bondInfo: list[GarrisonBond] = Field(default_factory=list)


class GarrisonHistory(BaseModel):
    records: list[GarrisonRecord] = Field(default_factory=list)
    is_archived: bool = False
    new_count: int = 0

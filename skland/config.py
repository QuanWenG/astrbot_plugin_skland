from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RES_DIR = PACKAGE_DIR / "resources"
TEMPLATES_DIR = RES_DIR / "templates"
CACHE_DIR = PACKAGE_DIR / ".cache"
DATA_DIR = PACKAGE_DIR / ".data"
GACHA_DATA_PATH = DATA_DIR / "gamedata" / "excel"


@dataclass(slots=True)
class RuntimeConfig:
    github_proxy_url: str = ""
    github_token: str = ""
    endfield_background_simple: bool = False
    gacha_render_max: int = 3
    ef_gacha_render_max: int = 3


config = RuntimeConfig()


def configure_paths(data_dir: Path) -> None:
    """Point runtime caches at AstrBot's per-plugin data directory."""
    global CACHE_DIR, DATA_DIR, GACHA_DATA_PATH
    CACHE_DIR = data_dir / "cache"
    DATA_DIR = data_dir / "data"
    GACHA_DATA_PATH = DATA_DIR / "gamedata" / "excel"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RES_DIR = PACKAGE_DIR / "resources"
TEMPLATES_DIR = RES_DIR / "templates"
CACHE_DIR = PACKAGE_DIR / ".cache"
DATA_DIR = PACKAGE_DIR / ".data"
GACHA_DATA_PATH = DATA_DIR / "gamedata" / "excel"
OPERATOR_METADATA_PATH = DATA_DIR / "operator_metadata.json"


@dataclass(slots=True)
class RuntimeConfig:
    github_proxy_url: str = ""
    github_token: str = ""
    endfield_background_simple: bool = False
    gacha_render_max: int = 3
    ef_gacha_render_max: int = 3
    render_timeout: int = 180_000
    ark_portrait_cache_enabled: bool = False
    ark_card_cache_ttl: int = 120
    ark_card_cache_max_entries: int = 64
    roster_render_max: int = 16
    roster_render_format: str = "jpeg"
    roster_jpeg_quality: int = 90
    qq_image_max_bytes: int = 4 * 1024 * 1024
    qq_image_max_side: int = 4096
    qq_image_jpeg_quality: int = 88


config = RuntimeConfig()


def configure_paths(data_dir: Path) -> None:
    """Point runtime caches at AstrBot's per-plugin data directory."""
    global CACHE_DIR, DATA_DIR, GACHA_DATA_PATH, OPERATOR_METADATA_PATH
    CACHE_DIR = data_dir / "cache"
    DATA_DIR = data_dir / "data"
    GACHA_DATA_PATH = DATA_DIR / "gamedata" / "excel"
    OPERATOR_METADATA_PATH = DATA_DIR / "operator_metadata.json"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

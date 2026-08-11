"""Synchronize the portable part of nonebot-plugin-skland into this plugin.

Only domain models, filters and presentation assets are mirrored. AstrBot-owned
entrypoints, persistence, permissions and services are intentionally excluded.
"""

from __future__ import annotations

import argparse
import subprocess
from textwrap import dedent, indent
from pathlib import Path

UPSTREAM_COMMIT = "0ac997a"
TEXT_FILES = (
    "filters.py",
    "schemas/arknights/game_data.py",
    "schemas/arknights/operators.py",
    "schemas/arknights/models/chars.py",
    "schemas/arknights/models/assist_chars.py",
    "resources/templates/gacha.html.jinja2",
    "resources/templates/index.css",
    "resources/templates/operator_roster.html.jinja2",
    "resources/templates/operator_roster_macros.html.jinja2",
)
BINARY_FILES = (
    "resources/images/ark_card/confidential_mini.png",
    "resources/images/ark_card/raft_dec_text_01.png",
)
FORBIDDEN_IMPORTS = (
    "from nonebot ",
    "from nonebot.",
    "import nonebot",
    "nonebot_plugin_orm",
    "nonebot_plugin_alconna",
    "nonebot_plugin_htmlrender",
)


def _replace_once(relative: str, source: str, old: str, new: str) -> str:
    """Apply a reviewed AstrBot extension without hiding future upstream drift."""
    occurrences = source.count(old)
    if occurrences != 1:
        raise RuntimeError(
            f"local extension anchor changed in {relative}: expected 1, found {occurrences}"
        )
    return source.replace(old, new, 1)


def _at(indentation: int, value: str) -> str:
    return indent(dedent(value), " " * indentation)


def _apply_operator_snapshot_extensions(relative: str, source: str) -> str:
    """Overlay fields required by the versioned cross-plugin snapshot contract."""
    if relative == "schemas/arknights/game_data.py":
        source = _replace_once(
            relative,
            source,
            dedent(
                """\
                class OperatorCatalogModule(BaseModel):
                    id: str
                    type_icon: str
                """
            ),
            dedent(
                """\
                class OperatorCatalogModule(BaseModel):
                    id: str
                    type_icon: str
                    module_name: str = ""
                    type_code: str = ""
                """
            ),
        )
        source = _replace_once(
            relative,
            source,
            _at(
                16,
                """\
                            OperatorCatalogModule(
                                id=module_id,
                                type_icon=module.get("typeIcon") or "original",
                            )
                """
            ),
            _at(
                16,
                """\
                            OperatorCatalogModule(
                                id=module_id,
                                type_icon=module.get("typeIcon") or "original",
                                module_name=module.get("uniEquipName") or "",
                                type_code=module.get("typeName2") or "",
                            )
                """
            ),
        )
        source = _replace_once(
            relative,
            source,
            dedent(
                """\
                class OperatorCatalogEntry(BaseModel):
                    char_id: str
                    name: str
                """
            ),
            dedent(
                """\
                class OperatorCatalogEntry(BaseModel):
                    char_id: str
                    variant_group_id: str = ""
                    name: str
                """
            ),
        )
        source = _replace_once(
            relative,
            source,
            _at(
                4,
                """\
                    def from_game_tables(
                        cls,
                        character_table: dict[str, dict[str, Any]],
                        char_patch_table: dict[str, Any],
                        uniequip_table: dict[str, Any],
                        handbook_info_table: dict[str, Any],
                        handbook_team_table: dict[str, dict[str, Any]],
                        metadata_snapshot: OperatorMetadataSnapshot | None = None,
                    ) -> "OperatorCatalog":
                """,
            ),
            _at(
                4,
                """\
                    def from_game_tables(
                        cls,
                        character_table: dict[str, dict[str, Any]],
                        char_patch_table: dict[str, Any],
                        uniequip_table: dict[str, Any],
                        handbook_info_table: dict[str, Any],
                        handbook_team_table: dict[str, dict[str, Any]],
                        metadata_snapshot: OperatorMetadataSnapshot | None = None,
                        char_meta_table: dict[str, Any] | None = None,
                    ) -> "OperatorCatalog":
                """,
            ),
        )
        source = _replace_once(
            relative,
            source,
            _at(
                8,
                """\
                        patch_characters = char_patch_table.get("patchChars") or {}
                        characters.update(patch_characters)

                        handbook_dict = handbook_info_table.get("handbookDict") or {}
                """,
            ),
            _at(
                8,
                """\
                        patch_characters = char_patch_table.get("patchChars") or {}
                        characters.update(patch_characters)

                        variant_group_by_id: dict[str, str] = {}
                        for group_id, member_ids in (
                            (char_meta_table or {}).get("spCharGroups") or {}
                        ).items():
                            if not isinstance(member_ids, list):
                                continue
                            for member_id in member_ids:
                                if isinstance(member_id, str) and member_id:
                                    variant_group_by_id[member_id] = str(group_id)

                        handbook_dict = handbook_info_table.get("handbookDict") or {}
                """,
            ),
        )
        source = _replace_once(
            relative,
            source,
            _at(
                16,
                """\
                                OperatorCatalogEntry(
                                    char_id=char_id,
                                    name=data.get("name") or char_id,
                """,
            ),
            _at(
                16,
                """\
                                OperatorCatalogEntry(
                                    char_id=char_id,
                                    variant_group_id=variant_group_by_id.get(
                                        char_id,
                                        variant_group_by_id.get(base_id, ""),
                                    ),
                                    name=data.get("name") or char_id,
                """,
            ),
        )
        return source

    if relative != "schemas/arknights/operators.py":
        return source

    source = _replace_once(
        relative,
        source,
        dedent(
            """\
            class OperatorModule(BaseModel):
                type_icon: str
                equipment: Equip | None = None
            """
        ),
        dedent(
            """\
            class OperatorModule(BaseModel):
                type_icon: str
                module_id: str = ""
                name: str = ""
                type_code: str = ""
                equipment: Equip | None = None
            """
        ),
    )
    source = _replace_once(
        relative,
        source,
        _at(
            8,
            """\
                    def resolve_type_icon(module_id: str, fallback: str) -> str:
                        metadata = equipment_map.get(module_id)
                        return metadata.typeIcon if metadata and metadata.typeIcon else fallback or "original"

                    modules: list[OperatorModule] = []
                    if entry.modules:
                        for catalog_module in entry.modules:
                            type_icon = resolve_type_icon(catalog_module.id, catalog_module.type_icon)
            """
        ),
        _at(
            8,
            """\
                    def resolve_metadata(
                        module_id: str,
                        fallback_icon: str,
                        fallback_name: str = "",
                        fallback_code: str = "",
                    ) -> tuple[str, str, str]:
                        metadata = equipment_map.get(module_id)
                        type_icon = (
                            metadata.typeIcon
                            if metadata and metadata.typeIcon
                            else fallback_icon or "original"
                        )
                        name = metadata.name if metadata and metadata.name else fallback_name
                        type_code = fallback_code.strip()
                        if not type_code:
                            type_match = re.search(
                                r"(?:^|[_-])(?P<code>x|y|d|delta)$",
                                type_icon,
                                flags=re.IGNORECASE,
                            )
                            if type_match:
                                inferred = type_match.group("code").upper()
                                type_code = "D" if inferred == "DELTA" else inferred
                        return type_icon, name, type_code

                    modules: list[OperatorModule] = []
                    known_module_ids: set[str] = set()
                    if entry.modules:
                        for catalog_module in entry.modules:
                            if catalog_module.id in known_module_ids:
                                continue
                            known_module_ids.add(catalog_module.id)
                            type_icon, name, type_code = resolve_metadata(
                                catalog_module.id,
                                catalog_module.type_icon,
                                catalog_module.module_name,
                                catalog_module.type_code,
                            )
            """
        ),
    )
    source = _replace_once(
        relative,
        source,
        _at(
            20,
            """\
                                OperatorModule(
                                    type_icon=type_icon,
                                    equipment=equipment,
                                    selected=bool(
            """
        ),
        _at(
            20,
            """\
                                OperatorModule(
                                    type_icon=type_icon,
                                    module_id=catalog_module.id,
                                    name=name,
                                    type_code=type_code,
                                    equipment=equipment,
                                    selected=bool(
            """
        ),
    )
    source = _replace_once(
        relative,
        source,
        _at(
            8,
            """\
                            )
                        return modules

                    if character is None:
                        return modules
                    for equipment in character.equip:
                        type_icon = resolve_type_icon(equipment.id, "original")
            """
        ),
        _at(
            8,
            """\
                            )

                    if character is None:
                        return modules
                    for equipment in character.equip:
                        # A locally cached catalog can lag behind the live account API when a
                        # new module is released.  Preserve those modules from the card's
                        # equipment list instead of silently truncating the snapshot to the
                        # catalog-known set.
                        if equipment.id in known_module_ids:
                            continue
                        known_module_ids.add(equipment.id)
                        type_icon, name, type_code = resolve_metadata(
                            equipment.id,
                            "original",
                        )
            """
        ),
    )
    source = _replace_once(
        relative,
        source,
        _at(
            16,
            """\
                            OperatorModule(
                                type_icon=type_icon,
                                equipment=equipment,
                                selected=not equipment.locked and character.defaultEquipId == equipment.id,
            """
        ),
        _at(
            16,
            """\
                            OperatorModule(
                                type_icon=type_icon,
                                module_id=equipment.id,
                                name=name,
                                type_code=type_code,
                                equipment=equipment,
                                selected=not equipment.locked and character.defaultEquipId == equipment.id,
            """
        ),
    )
    source = _replace_once(
        relative,
        source,
        _at(
            4,
            """\
                @property
                def char_id(self) -> str:
                    return self.entry.char_id

                @property
                def name(self) -> str:
            """,
        ),
        _at(
            4,
            """\
                @property
                def char_id(self) -> str:
                    return self.entry.char_id

                @property
                def variant_group_id(self) -> str:
                    return self.entry.variant_group_id

                @property
                def name(self) -> str:
            """,
        ),
    )
    return source


def normalize(relative: str, source: str) -> str:
    if relative in {
        "schemas/arknights/game_data.py",
        "schemas/arknights/operators.py",
    }:
        source = source.replace(
            "from nonebot.compat import model_validator",
            "from pydantic import model_validator",
        )
    if relative == "filters.py":
        source = source.replace("import json\n", "import json\nimport logging\n", 1)
        source = source.replace(
            "from nonebot import logger\n\nfrom .config import RES_DIR, CACHE_DIR\n",
            'from . import config as paths\nfrom .image_cache import register_missing_image\n\n'
            'logger = logging.getLogger("astrbot")\nRES_DIR = paths.RES_DIR\n'
            'CACHE_DIR = paths.CACHE_DIR\n\n',
        )
        source = source.replace(
            "from .image_cache import register_missing_image\n\n\n",
            "\n",
            1,
        )
    return _apply_operator_snapshot_extensions(relative, source)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--upstream",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "nonebot-plugin-skland"
        / "nonebot_plugin_skland",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1] / "skland"
    source_root = args.upstream.resolve()
    if not source_root.is_dir():
        parser.error(f"upstream source not found: {source_root}")

    repo = source_root.parent
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if commit != UPSTREAM_COMMIT:
        print(f"warning: expected upstream {UPSTREAM_COMMIT}, found {commit}")
        changed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                f"{UPSTREAM_COMMIT}..HEAD",
                "--",
                "nonebot_plugin_skland",
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        mirrored = {f"nonebot_plugin_skland/{name}" for name in (*TEXT_FILES, *BINARY_FILES)}
        portable_prefixes = (
            "nonebot_plugin_skland/api/",
            "nonebot_plugin_skland/schemas/",
            "nonebot_plugin_skland/resources/templates/",
            "nonebot_plugin_skland/resources/fonts/",
            "nonebot_plugin_skland/resources/images/",
        )
        unreviewed = [
            name
            for name in changed
            if (name == "nonebot_plugin_skland/filters.py" or name.startswith(portable_prefixes))
            and name not in mirrored
        ]
        if unreviewed:
            print("new or changed upstream portable files require manifest review:")
            print("\n".join(f"- {name}" for name in unreviewed))
            if args.check:
                return 1

    drift: list[str] = []
    for relative in TEXT_FILES:
        source = normalize(relative, (source_root / relative).read_text("utf-8"))
        forbidden = [token for token in FORBIDDEN_IMPORTS if token in source]
        if forbidden:
            raise SystemExit(f"forbidden framework import in {relative}: {forbidden}")
        target = destination / relative
        if args.check:
            if not target.exists() or target.read_text("utf-8") != source:
                drift.append(relative)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, "utf-8")

    for relative in BINARY_FILES:
        payload = (source_root / relative).read_bytes()
        target = destination / relative
        if args.check:
            if not target.exists() or target.read_bytes() != payload:
                drift.append(relative)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    if drift:
        print("upstream drift detected:")
        print("\n".join(f"- {relative}" for relative in drift))
        return 1
    print("upstream portable core is synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

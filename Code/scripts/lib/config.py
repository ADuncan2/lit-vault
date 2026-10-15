"""Load Code/config.toml into a small typed object.

Relative paths in the file resolve against Code/, so the config works no
matter which directory a script is launched from.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .store import CODE_DIR

CONFIG_PATH = CODE_DIR / "config.toml"

# Zotero 7's highlight palette -> heading in paper notes. Neutral colour
# names by default; [highlights] renames them to what each colour means.
DEFAULT_HIGHLIGHTS = {
    "#ffd400": "Yellow highlights",
    "#ff6666": "Red highlights",
    "#5fb236": "Green highlights",
    "#2ea8e5": "Blue highlights",
    "#a28ae5": "Purple highlights",
    "#e56eee": "Magenta highlights",
    "#f19837": "Orange highlights",
    "#aaaaaa": "Grey highlights",
}
DEFAULT_EXCLUDE = ["Templates", ".obsidian", ".trash"]


@dataclass
class Config:
    zotero_data: Path
    export: Path
    key_tag: str = ""
    key_collection: str = ""
    main_vault: Path | None = None
    vault_name: str = ""
    lit_notes: list[str] = field(default_factory=lambda: [""])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    related_tags: int = 8
    ignore_tags: list[str] = field(default_factory=list)
    highlights: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HIGHLIGHTS))


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (CODE_DIR / p)


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    zot = raw.get("zotero", {})
    mv = raw.get("main_vault", {})
    tags = raw.get("tags", {})
    highlights = {k.lower(): v for k, v in
                  raw.get("highlights", DEFAULT_HIGHLIGHTS).items()}

    lit_notes = mv.get("lit_notes", [""])
    if isinstance(lit_notes, str):
        lit_notes = [lit_notes]

    vault_path = mv.get("path", "")
    return Config(
        zotero_data=_resolve(zot.get("data_dir", "")),
        export=_resolve(zot.get("export", "Export_data")),  # file or folder: lib.bbt.find_export
        key_tag=zot.get("key_tag", ""),
        key_collection=zot.get("key_collection", ""),
        main_vault=Path(vault_path) if vault_path else None,
        vault_name=mv.get("vault_name", ""),
        lit_notes=lit_notes,
        exclude=mv.get("exclude", DEFAULT_EXCLUDE),
        related_tags=int(tags.get("related_tags", 8)),
        ignore_tags=list(tags.get("ignore", [])),
        highlights=highlights,
    )

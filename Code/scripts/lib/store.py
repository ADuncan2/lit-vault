"""Paths and small storage helpers: JSONL/JSON files and change-aware writes."""

import json
from pathlib import Path

# lib/ -> scripts/ -> Code/ -> repo root (which is also the Obsidian vault)
ROOT = Path(__file__).resolve().parents[3]
CODE_DIR = ROOT / "Code"
DATA_DIR = CODE_DIR / "data"
CORPUS_PATH = DATA_DIR / "corpus.jsonl"
ANNOTATIONS_PATH = DATA_DIR / "annotations.jsonl"
MY_NOTES_PATH = DATA_DIR / "my_notes.json"
MANIFEST_PATH = DATA_DIR / "fulltext_manifest.json"
FULLTEXT_DIR = ROOT / "Fulltext"
LOG_DIR = ROOT / "logs"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_if_changed(path, json.dumps(data, indent=2, ensure_ascii=False,
                                      sort_keys=True) + "\n")


def write_if_changed(path: Path, content: str) -> bool:
    """Write `content` (LF line endings) only if it differs from what is on
    disk. Returns True if the file was written."""
    data = content.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True

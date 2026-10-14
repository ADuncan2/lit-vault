"""Better BibTeX JSON export parsing.

The BBT export wraps items in {"config": ..., "collections": {...}, "items": [...]}.
Only the "Better BibTeX JSON" format is supported.
"""

import json
import re
from pathlib import Path


def find_export(path: Path) -> Path:
    """The export file. `path` is the file itself, or a folder holding
    exactly one .json export (any name; the whole library or a single
    collection). Raises ValueError, with the fix, if that can't be decided."""
    if not path.is_dir():
        if path.exists():
            return path
        raise ValueError(f"export not found: {path}")
    found = sorted(p for p in path.glob("*.json") if p.is_file())
    if len(found) == 1:
        return found[0]
    if not found:
        raise ValueError(f"no .json export in {path} — save the Better BibTeX "
                         "JSON export there (docs/setup-bbt.md)")
    raise ValueError(f"{len(found)} .json files in {path} "
                     f"({', '.join(p.name for p in found)}) — keep only the one "
                     "export to build from, or name it in [zotero] export")


def load_bibtex_export(path: Path) -> dict:
    """Load the full export: returns {"items": [...], "collections": {...}}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "items" in data:
        return {"items": data["items"], "collections": data.get("collections", {}) or {}}
    raise ValueError(f"Not a Better BibTeX JSON export: {path}")


def collection_keys_by_name(collections: dict, name: str) -> set[str]:
    """Resolve a collection name to its key plus all descendant keys."""
    by_key = {}
    for key, coll in collections.items():
        if isinstance(coll, dict):
            by_key[coll.get("key", key)] = coll

    matched = {k for k, c in by_key.items()
               if c.get("name", "").lower() == name.lower()}

    # expand to descendants
    result = set(matched)
    frontier = list(matched)
    while frontier:
        parent = frontier.pop()
        children = by_key.get(parent, {}).get("collections", []) or []
        for child in children:
            if child not in result:
                result.add(child)
                frontier.append(child)
    return result


def get_citekey(item: dict) -> str | None:
    return item.get("citationKey")


def get_title(item: dict) -> str:
    return item.get("title", "Untitled")


def get_authors(item: dict) -> list[str]:
    """Return authors as ['Family, G.', ...]."""
    authors = []
    for c in item.get("creators", []):
        if c.get("creatorType", "author") == "author":
            last = c.get("lastName", "")
            first = c.get("firstName", "")
            initial = f"{first[0]}." if first else ""
            authors.append(f"{last}, {initial}".strip(", "))
    return authors


def get_year(item: dict) -> str:
    m = re.match(r"(\d{4})", str(item.get("date", "")))
    return m.group(1) if m else ""


def get_doi(item: dict) -> str:
    return item.get("DOI", "")


def get_tags(item: dict) -> list[str]:
    """Manual Zotero tags (type absent or 0), sorted. Automatic tags (type 1,
    mostly publisher keywords) are ignored."""
    out = set()
    for t in item.get("tags", []):
        name = (t.get("tag") or "").strip()
        if name and not t.get("type"):
            out.add(name)
    return sorted(out, key=str.lower)


def get_zotero_key(item: dict) -> str:
    return item.get("key", "")


def get_abstract(item: dict) -> str:
    return item.get("abstractNote", "")


def get_zotero_uri(item: dict) -> str:
    return item.get("select", "")


def get_item_type(item: dict) -> str:
    return item.get("itemType", "")


def get_collections(item: dict) -> list[str]:
    return item.get("collections", []) or []


def get_pdf_attachment(item: dict) -> tuple[str, str] | None:
    """(attachment key, path) of the first PDF attachment, or None.

    BBT attachments carry no `key` field, so it is read from the end of
    `uri` (…/items/KEY) or `select` (zotero://select/library/items/KEY).
    """
    for att in item.get("attachments", []):
        path_str = att.get("path", "") or ""
        if not path_str.lower().endswith(".pdf"):
            continue
        ref = att.get("key") or att.get("uri") or att.get("select") or ""
        return ref.rstrip("/").rsplit("/", 1)[-1], path_str
    return None


def resolve_pdf_path(path_str: str, att_key: str, zotero_data: Path | None) -> Path | None:
    """The PDF on disk: the exported path if it exists, else the same file
    name under {zotero_data}/storage/{att_key}/ (export paths can carry a
    drive letter from another machine)."""
    if not path_str:
        return None
    p = Path(path_str)
    if p.exists():
        return p
    if zotero_data and att_key:
        alt = zotero_data / "storage" / att_key / Path(path_str.replace("\\", "/")).name
        if alt.exists():
            return alt
    return None


def collection_names(collections: dict) -> dict[str, str]:
    """Collection key -> name."""
    return {coll.get("key", key): coll.get("name", "")
            for key, coll in collections.items() if isinstance(coll, dict)}

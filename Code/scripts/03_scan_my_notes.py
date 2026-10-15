"""
03_scan_my_notes.py — map citekeys to my reading notes in the main vault.

Read-only walk of the main vault's literature-notes folder(s). Any markdown
note with a `citekey:` frontmatter field counts as my note on that paper.
Writes Code/data/my_notes.json: citekey -> {path, link}, where `link` is an
obsidian://open URL that opens the note in the main vault.

Never writes to the main vault. Optional: with no [main_vault] path in
config.toml it writes an empty map and exits cleanly, so the build runs
unchanged without one (no ✎ markers, no "My note" links).

Usage:
    uv run python scripts/03_scan_my_notes.py
"""

import argparse
import re
from pathlib import Path
from urllib.parse import quote

from lib.config import load_config
from lib.logutil import setup_logging
from lib.store import LOG_DIR, MY_NOTES_PATH, write_json

_CITEKEY_RE = re.compile(r"^citekey:\s*[\"']?([^\"'\s]+)[\"']?\s*$", re.MULTILINE)


def note_citekey(path: Path) -> str | None:
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:4000]
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("\n---", 3)
    m = _CITEKEY_RE.search(head[: end if end != -1 else len(head)])
    if not m or "{" in m.group(1):  # skip template placeholders
        return None
    return m.group(1)


def obsidian_link(vault_name: str, rel_path: str) -> str:
    file = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return (f"obsidian://open?vault={quote(vault_name, safe='')}"
            f"&file={quote(file, safe='')}")


def main():
    cfg = load_config()
    argparse.ArgumentParser(
        description="Map citekeys to reading notes in the main vault."
    ).parse_args()

    log = setup_logging(LOG_DIR, "scan_my_notes")
    if cfg.main_vault is None:
        write_json(MY_NOTES_PATH, {})
        print("No [main_vault] path in config.toml — skipped (no reading-note links).")
        return
    if not cfg.main_vault.exists():
        raise SystemExit(f"Main vault not found: {cfg.main_vault} — fix "
                         "[main_vault] path in config.toml")
    vault = cfg.main_vault.resolve()
    vault_name = cfg.vault_name or vault.name
    excluded = {e.strip("/\\").lower() for e in cfg.exclude}

    notes: dict[str, dict] = {}
    dupes: dict[str, list[str]] = {}
    seen_files: set[Path] = set()
    for folder in cfg.lit_notes:
        base = vault / folder if folder else vault
        if not base.exists():
            log.warning("Literature-notes folder not found: %s", base)
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(vault).as_posix()
            folders = {part.lower() for part in rel.split("/")[:-1]}
            if path in seen_files or folders & excluded:
                continue
            seen_files.add(path)
            ck = note_citekey(path)
            if not ck:
                continue
            if ck in notes:
                dupes.setdefault(ck, [notes[ck]["path"]]).append(rel)
                continue
            notes[ck] = {"path": rel, "link": obsidian_link(vault_name, rel)}

    for ck, paths in sorted(dupes.items()):
        log.warning("Several notes for %s (first kept): %s", ck, paths)

    write_json(MY_NOTES_PATH, notes)
    summary = (f"\n{'='*50}\n  Notes with a citekey: {len(notes)}\n"
               f"  Duplicates:           {len(dupes)}\n"
               f"  -> {MY_NOTES_PATH}\n{'='*50}")
    log.debug(summary)
    print(summary)


if __name__ == "__main__":
    main()

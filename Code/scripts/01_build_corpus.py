"""
01_build_corpus.py — build the canonical per-paper corpus from a BBT export.

Parses a Better BibTeX JSON export (whole library or one collection —
see lib.bbt.find_export for how the file is chosen) into one PaperRecord per
usable item and writes Code/data/corpus.jsonl, sorted by citekey. No PDF
work happens here — abstracts come straight from the export.

Only manual Zotero tags are kept; automatic publisher keywords are
ignored, as are tags matching [tags] ignore. A paper is a key paper if it
carries the configured key tag or sits in the key collection (including
sub-collections).

The corpus is cheap to build and is fully rewritten on every run — the
export file is the source of truth. --tags-report adds papers per tag to
the run summary.

Usage:
    uv run python scripts/01_build_corpus.py [--tags-report]
"""

import argparse
from collections import Counter
from dataclasses import asdict
from fnmatch import fnmatchcase

from lib.bbt import (
    SKIP_ITEM_TYPES,
    collection_keys_by_name,
    collection_names,
    find_export,
    get_abstract,
    get_authors,
    get_citekey,
    get_collections,
    get_doi,
    get_item_type,
    get_library,
    get_pdf_attachment,
    get_tags,
    get_title,
    get_year,
    get_zotero_key,
    get_zotero_uri,
    load_bibtex_export,
)
from lib.config import load_config
from lib.logutil import setup_logging
from lib.schema import PaperRecord
from lib.store import CORPUS_PATH, LOG_DIR, write_jsonl


def is_ignored(tag: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(tag.lower(), p.lower()) for p in patterns)


def build_record(
    item: dict,
    key_tag: str,
    key_collection_keys: set[str],
    coll_names: dict[str, str],
    ignore_tags: list[str],
    log,
) -> PaperRecord | None:
    item_type = get_item_type(item)
    if item_type in SKIP_ITEM_TYPES:
        log.debug("skip (itemType=%s): %s", item_type, get_title(item)[:60])
        return None

    citekey = get_citekey(item)
    if not citekey:
        log.warning("SKIP  item without citekey: %s", get_title(item)[:80])
        return None

    title = " ".join(item.get("title", "").split())
    if not title:
        log.warning("SKIP  %s — no title", citekey)
        return None

    tags = get_tags(item)
    coll_keys = get_collections(item)
    key_paper = bool(
        (key_tag and key_tag.lower() in {t.lower() for t in tags})
        or (key_collection_keys and set(coll_keys) & key_collection_keys)
    )
    tags = [t for t in tags if not is_ignored(t, ignore_tags)]
    pdf = get_pdf_attachment(item)

    return PaperRecord(
        citekey=citekey,
        zotero_key=get_zotero_key(item),
        title=title,
        authors=get_authors(item),
        year=get_year(item),
        doi=get_doi(item),
        item_type=item_type,
        tags=tags,
        collections=sorted({coll_names.get(k, k) for k in coll_keys}, key=str.lower),
        key_paper=key_paper,
        abstract=" ".join(get_abstract(item).split()),
        zotero_uri=get_zotero_uri(item),
        library=get_library(item),
        has_pdf=pdf is not None,
        pdf_key=pdf[0] if pdf else "",
        pdf_path=pdf[1] if pdf else "",
    )


def tag_summary(records: list[dict], full: bool) -> str:
    manual = Counter(t for r in records for t in r["tags"])
    untagged = sum(1 for r in records if not r["tags"])
    lines = [
        f"  Tags:          {len(manual)} distinct, "
        f"{sum(manual.values())} assignments",
        f"  Untagged:      {untagged} papers have no tag",
        f"  Single-paper:  {sum(1 for n in manual.values() if n == 1)} tags "
        "are on one paper only",
    ]
    if full:
        lines.append("  Papers per tag:")
        for tag, n in sorted(manual.items(), key=lambda kv: (-kv[1], kv[0].lower())):
            lines.append(f"    {n:5d}  {tag}")
    return "\n".join(lines)


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser(
        description="Build Code/data/corpus.jsonl from a Better BibTeX JSON export."
    )
    parser.add_argument(
        "--tags-report", action="store_true",
        help="Also list the number of papers per tag."
    )
    args = parser.parse_args()

    log = setup_logging(LOG_DIR, "build_corpus")

    try:
        export_path = find_export(cfg.export)
    except ValueError as e:
        log.error("%s", e)
        return 1
    log.info("Loading %s", export_path)
    export = load_bibtex_export(export_path)
    items = export["items"]
    log.info("Found %d items in export", len(items))

    key_collection_keys: set[str] = set()
    if cfg.key_collection:
        key_collection_keys = collection_keys_by_name(
            export["collections"], cfg.key_collection
        )
        if not key_collection_keys:
            log.warning("Collection '%s' not found in export — no key papers "
                        "by collection", cfg.key_collection)
    coll_names = collection_names(export["collections"])

    records = []
    skipped = 0
    for item in items:
        record = build_record(item, cfg.key_tag, key_collection_keys,
                              coll_names, cfg.ignore_tags, log)
        if record is None:
            skipped += 1
            continue
        records.append(asdict(record))

    # citekeys must be unique — they name every downstream file
    seen = Counter(r["citekey"] for r in records)
    dupes = {k: n for k, n in seen.items() if n > 1}
    if dupes:
        log.warning("Duplicate citekeys in export (later wins): %s", dupes)
    records = sorted({r["citekey"]: r for r in records}.values(),
                     key=lambda r: r["citekey"])

    write_jsonl(CORPUS_PATH, records)

    n_key = sum(1 for r in records if r["key_paper"])
    if cfg.key_tag and not any(cfg.key_tag.lower() == t.lower()
                               for it in items for t in get_tags(it)):
        log.warning("Key tag '%s' is on no item in the export — no key "
                    "papers by tag", cfg.key_tag)
    if not n_key:
        log.warning("No key papers, so step 02 will extract no full text — "
                    "set key_tag / key_collection in config.toml, or run "
                    "02 with --tier all")
    n_pdf = sum(1 for r in records if r["has_pdf"])
    n_no_abstract = sum(1 for r in records if not r["abstract"])
    summary = (
        f"\n{'='*50}\n"
        f"  Papers:        {len(records)}\n"
        f"  Key papers:    {n_key}\n"
        f"  With PDF:      {n_pdf}\n"
        f"  No abstract:   {n_no_abstract}\n"
        f"  Skipped items: {skipped}\n"
        f"{tag_summary(records, args.tags_report)}\n"
        f"  -> {CORPUS_PATH}\n"
        f"{'='*50}"
    )
    log.debug(summary)
    print(summary)


if __name__ == "__main__":
    raise SystemExit(main())

"""
02_extract_fulltext.py — Zotero annotations and page-marked PDF full text.

Two jobs, both driven by Code/data/corpus.jsonl (run 01 first):

1. Annotations — fetched from Zotero's SQLite database for every paper that
   has them, regardless of tier, into Code/data/annotations.jsonl. Step 04
   renders them in the paper notes. The DB is copied to a temp file first
   since Zotero locks it while running.

2. Full text — pypdf text written to Fulltext/{citekey}.md: plain text, a
   `<!-- page N -->` marker before each physical page, minimal cleaning
   (de-hyphenate, normalise whitespace) and the references cut at the first
   References/Bibliography heading in the last third of the document. Each
   file's hash, page count and status go in Code/data/fulltext_manifest.json.
   Existing files are skipped unless --force.

Tiers: --tier fulltext (key papers only, the default) or --tier all.
--annotations-only skips the PDFs entirely.
--measure reports PDF and page counts and a projected Fulltext/ size
without writing anything.

Usage:
    uv run python scripts/02_extract_fulltext.py [--tier fulltext|all]
        [--annotations-only] [--measure] [--force] [--citekey KEY]
"""

import argparse
import hashlib
import logging
import re
from datetime import date
from pathlib import Path

import pypdf
from pypdf import PdfReader

from lib.bbt import resolve_pdf_path
from lib.config import load_config
from lib.logutil import setup_logging
from lib.rawmd import render_fulltext
from lib.store import (
    ANNOTATIONS_PATH,
    CORPUS_PATH,
    FULLTEXT_DIR,
    LOG_DIR,
    MANIFEST_PATH,
    read_json,
    read_jsonl,
    write_if_changed,
    write_json,
    write_jsonl,
)
from lib.zotero_db import fetch_all_annotations, open_zotero_db

EXTRACTOR = f"pypdf {pypdf.__version__}"
MIN_CHARS_PER_PAGE = 100  # below this the PDF is probably a scan

_REFERENCES_RE = re.compile(
    r"^(?:[^A-Za-z]{0,8}|[IVX]+\.\s*)"  # "7.", "10 ", stray glyphs, "VI."
    r"(?:references|bibliography|reference list|literature cited|works cited)"
    r"\s*:?$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# PDF text extraction and cleaning
# ---------------------------------------------------------------------------

def clean_page(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def cut_references(pages: list[str]) -> tuple[list[str], bool]:
    """Drop everything from the first references heading that starts in the
    last third of the document. Returns (pages, was_cut)."""
    total = sum(len(p) for p in pages)
    threshold = total * 2 / 3
    offset = 0
    for i, page in enumerate(pages):
        line_start = 0
        for line in page.split("\n"):
            if offset + line_start >= threshold and _REFERENCES_RE.match(line.strip()):
                kept = pages[:i] + [page[:line_start].rstrip()]
                return kept, True
            line_start += len(line) + 1
        offset += len(page)
    return pages, False


def extract_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(pdf_path)
    return [clean_page(page.extract_text() or "") for page in reader.pages]


def page_status(pages: list[str]) -> str:
    if not pages:
        return "error"
    chars = sum(len(p) for p in pages)
    return "ok" if chars / len(pages) >= MIN_CHARS_PER_PAGE else "needs_ocr"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def update_annotations(corpus: list[dict], zotero_data: Path, log) -> None:
    conn = open_zotero_db(zotero_data)
    if conn is None:
        log.warning("Could not open zotero.sqlite in %s — annotations.jsonl "
                    "left unchanged", zotero_data)
        return
    try:
        by_item = fetch_all_annotations(conn)
    finally:
        conn.close()

    rows = []
    for r in corpus:  # corpus is sorted by citekey
        anns = by_item.get(r["zotero_key"])
        if anns:
            rows.append({"citekey": r["citekey"], "annotations": anns})
    write_jsonl(ANNOTATIONS_PATH, rows)
    log.info("Annotations: %d papers, %d annotations -> %s", len(rows),
             sum(len(row["annotations"]) for row in rows), ANNOTATIONS_PATH)


def extract_fulltext(papers: list[dict], zotero_data: Path, force: bool,
                     log) -> dict:
    manifest = read_json(MANIFEST_PATH, {}) or {}
    FULLTEXT_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"extracted": 0, "skipped": 0, "no_pdf": 0, "missing_file": 0,
             "needs_ocr": 0, "error": 0}

    try:
        for r in papers:
            _extract_one(r, manifest, zotero_data, force, stats, log)
    finally:  # keep the manifest in step with Fulltext/ even if interrupted
        write_json(MANIFEST_PATH, manifest)
    return stats


def _extract_one(r: dict, manifest: dict, zotero_data: Path, force: bool,
                 stats: dict, log) -> None:
    ck = r["citekey"]
    out_path = FULLTEXT_DIR / f"{ck}.md"
    prev = manifest.get(ck)

    if not r["has_pdf"]:
        stats["no_pdf"] += 1
        return
    if (not force and prev and prev.get("pdf_key") == r["pdf_key"]
            and (prev.get("status") != "ok" or out_path.exists())):
        stats["skipped"] += 1
        return

    pdf_path = resolve_pdf_path(r["pdf_path"], r["pdf_key"], zotero_data)
    if pdf_path is None:
        log.warning("MISSING  %s — PDF not found at %s", ck, r["pdf_path"])
        stats["missing_file"] += 1
        return

    entry = {"pdf_key": r["pdf_key"], "pdf_file": pdf_path.name,
             "extractor": EXTRACTOR, "extracted": date.today().isoformat()}
    try:
        pages = extract_pages(pdf_path)
        status = page_status(pages)
    except Exception as e:  # pypdf raises a zoo of exception types
        log.error("ERROR  %s — %s", ck, e)
        pages, status = [], "error"

    entry.update(status=status, pages=len(pages))
    if status == "ok":
        kept, was_cut = cut_references(pages)
        content = render_fulltext(ck, kept, len(pages), status, EXTRACTOR)
        write_if_changed(out_path, content)
        entry.update(
            kept_pages=len(kept), references_cut=was_cut,
            chars=sum(len(p) for p in kept),
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        log.info("OK  %s (%d pages%s)", ck, len(pages),
                 ", references cut" if was_cut else "")
        stats["extracted"] += 1
    else:
        if out_path.exists():
            out_path.unlink()  # never leave a stale file for a failed PDF
        log.warning("%s  %s (%d pages)", status.upper(), ck, len(pages))
        stats[status] += 1
    manifest[ck] = entry


def measure(corpus: list[dict], zotero_data: Path, log) -> str:
    """What would full extraction cost? Writes nothing."""
    with_pdf = [r for r in corpus if r["has_pdf"]]
    found, missing, unreadable = [], 0, 0
    for r in with_pdf:
        p = resolve_pdf_path(r["pdf_path"], r["pdf_key"], zotero_data)
        if p is None:
            missing += 1
            continue
        try:
            n_pages = len(PdfReader(p).pages)
        except Exception as e:
            log.debug("unreadable %s: %s", r["citekey"], e)
            unreadable += 1
            continue
        found.append((r, p, n_pages))

    total_pages = sum(n for _, _, n in found)
    total_bytes = sum(p.stat().st_size for _, p, _ in found)
    key_pages = sum(n for r, _, n in found if r["key_paper"])

    # Extract a deterministic sample to estimate text bytes per page
    step = max(1, len(found) // 25)
    sample = found[::step][:25]
    s_pages = s_bytes = 0
    for r, p, n in sample:
        try:
            text = "\n".join(extract_pages(p)).encode("utf-8")
        except Exception:
            continue
        s_pages += n
        s_bytes += len(text)
    per_page = s_bytes / s_pages if s_pages else 0
    raw_mb = per_page * total_pages / 1e6
    page_counts = sorted(n for _, _, n in found)
    median = page_counts[len(page_counts) // 2] if page_counts else 0

    return (
        f"  Papers:              {len(corpus)}\n"
        f"  No PDF attachment:   {len(corpus) - len(with_pdf)}\n"
        f"  PDF not on disk:     {missing}\n"
        f"  PDF unreadable:      {unreadable}\n"
        f"  PDFs measured:       {len(found)} "
        f"({sum(1 for r, _, _ in found if r['key_paper'])} key papers)\n"
        f"  Pages:               {total_pages} total, median {median}, "
        f"max {page_counts[-1] if page_counts else 0}; key papers {key_pages}\n"
        f"  PDF size on disk:    {total_bytes / 1e6:.0f} MB\n"
        f"  Text per page:       {per_page:.0f} bytes (sample of {len(sample)})\n"
        f"  Projected Fulltext/: {raw_mb:.0f} MB (every PDF), "
        f"{per_page * key_pages / 1e6:.0f} MB (key papers)"
    )


def main():
    cfg = load_config()
    parser = argparse.ArgumentParser(
        description="Fetch Zotero annotations and extract page-marked full text."
    )
    parser.add_argument(
        "--tier", default="fulltext", choices=["fulltext", "all"],
        help="Full-text scope: key papers only (default) or every paper."
    )
    parser.add_argument(
        "--annotations-only", action="store_true",
        help="Fetch annotations, skip all PDF extraction."
    )
    parser.add_argument(
        "--measure", action="store_true",
        help="Report PDF/page counts and projected size; write nothing."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract papers already in the manifest."
    )
    parser.add_argument(
        "--citekey", type=str, default=None,
        help="Extract only this paper (ignores --tier)."
    )
    args = parser.parse_args()

    log = setup_logging(LOG_DIR, "extract_fulltext")
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    corpus = read_jsonl(CORPUS_PATH)
    if not corpus:
        raise SystemExit(f"No corpus at {CORPUS_PATH} — run 01_build_corpus.py first")

    if args.measure:
        report = measure(corpus, cfg.zotero_data, log)
        print(f"\n{'='*50}\n{report}\n{'='*50}")
        return

    update_annotations(corpus, cfg.zotero_data, log)
    if args.annotations_only:
        return

    if args.citekey:
        papers = [r for r in corpus if r["citekey"] == args.citekey]
        if not papers:
            raise SystemExit(f"Citekey {args.citekey} not in corpus")
    elif args.tier == "fulltext":
        papers = [r for r in corpus if r["key_paper"]]
        log.info("Full-text tier: %d key papers", len(papers))
        if not papers:
            log.warning("No key papers — check key_tag / key_collection in "
                        "config.toml, or use --tier all")
    else:
        papers = corpus
    stats = extract_fulltext(papers, cfg.zotero_data, args.force, log)
    summary = "\n".join(f"  {k + ':':<14}{v}" for k, v in stats.items())
    summary = f"\n{'='*50}\n{summary}\n{'='*50}"
    log.debug(summary)
    print(summary)


if __name__ == "__main__":
    main()

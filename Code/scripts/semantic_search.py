"""
semantic_search.py — query the semantic index built by 06.

Two modes, both printing JSON Lines to stdout (one hit per line: citekey,
page, part, score, preview, zotero_pdf_page):

    --query "..."          embed the question, return the top-k pages
    --like CITEKEY:PAGE    pages in *other* papers nearest to that page
                           (no embedding call: works with Ollama down)

Hits are leads, never evidence: open Fulltext/{citekey}.md at the page and
cite the real text. Previews are for scanning a hit list only.

If the index or its infrastructure is unavailable, prints one JSON status
line ({"status": ..., "message": ...}) and exits 0, so callers can fall back
to the keyword funnel without parsing errors.

Usage:
    uv run python scripts/semantic_search.py --query "..."
        [--k 12] [--min-score 0.45] [--per-paper 3]
    uv run python scripts/semantic_search.py --like someCitekey:12
"""

import argparse
import json
import sys

from lib.semantic import (
    CHUNKER_VERSION,
    INDEX_PATH,
    check_capability,
    embed,
    load_matrix,
    load_semantic_config,
    open_index,
    read_meta,
)
from lib.store import MANIFEST_PATH, read_json


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def status(code: str, message: str) -> None:
    emit({"status": code, "message": message})


def parse_like(spec: str) -> tuple[str, int, int | None]:
    """CITEKEY:PAGE or CITEKEY:PAGE:PART."""
    bits = spec.rsplit(":", 2)
    if len(bits) == 3 and bits[1].isdigit() and bits[2].isdigit():
        return bits[0], int(bits[1]), int(bits[2])
    ck, _, page = spec.rpartition(":")
    if not ck or not page.isdigit():
        raise SystemExit("--like expects CITEKEY:PAGE (or CITEKEY:PAGE:PART)")
    return ck, int(page), None


def like_vector(rows, matrix, citekey: str, page: int, part: int | None):
    """Mean of the chunk(s) at that page; a page merged into an earlier
    chunk resolves to that chunk."""
    import numpy as np
    paper = [i for i, r in enumerate(rows) if r["citekey"] == citekey]
    if not paper:
        return None
    at = [i for i in paper if rows[i]["page"] == page
          and (part is None or rows[i]["part"] == part)]
    if not at:
        earlier = [i for i in paper if rows[i]["page"] <= page]
        if not earlier:
            return None
        start = rows[earlier[-1]]["page"]
        at = [i for i in earlier if rows[i]["page"] == start]
    vec = matrix[at].mean(axis=0)
    return vec / max(np.linalg.norm(vec), 1e-12)


def main():
    cfg = load_semantic_config()
    parser = argparse.ArgumentParser(description="Search the semantic index.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query", help="Natural-language question.")
    mode.add_argument("--like", help="CITEKEY:PAGE — related pages in other papers.")
    parser.add_argument("--k", type=int, default=cfg.k)
    parser.add_argument("--min-score", type=float, default=cfg.min_score)
    parser.add_argument("--per-paper", type=int, default=cfg.per_paper)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    cap = check_capability(cfg, need_ollama=bool(args.query))
    if cap["status"] != "ok":
        return status(cap["status"], cap["message"])
    if not INDEX_PATH.exists():
        return status("no_index", "no semantic index — run 06_build_semantic_index.py")

    conn = open_index()
    try:
        meta = read_meta(conn)
        rows, matrix = load_matrix(conn)
    finally:
        conn.close()
    if not rows:
        return status("no_index", "semantic index is empty — run 06_build_semantic_index.py")
    if meta.get("chunker_version") != str(CHUNKER_VERSION):
        return status("stale", "index built by another chunker — run 06_build_semantic_index.py --force")

    import numpy as np
    exclude = None
    if args.query:
        if (meta.get("embed_model") != cfg.embed_model
                or meta.get("model_digest") != cap["model_digest"]):
            return status("stale", "index built with another embedding model — "
                                   "run 06_build_semantic_index.py --force")
        qvec = embed([args.query], cfg, prefix=cfg.query_prefix)[0]
    else:
        ck, page, part = parse_like(args.like)
        qvec = like_vector(rows, matrix, ck, page, part)
        if qvec is None:
            return status("not_found", f"{args.like} is not in the index")
        exclude = ck

    scores = matrix @ qvec
    pdf_keys = {ck: e.get("pdf_key", "")
                for ck, e in (read_json(MANIFEST_PATH, {}) or {}).items()}
    per_paper: dict[str, int] = {}
    n = 0
    for i in np.argsort(-scores):
        score = float(scores[i])
        if score < args.min_score or n >= args.k:
            break
        r = rows[i]
        if r["citekey"] == exclude or per_paper.get(r["citekey"], 0) >= args.per_paper:
            continue
        per_paper[r["citekey"]] = per_paper.get(r["citekey"], 0) + 1
        n += 1
        key = pdf_keys.get(r["citekey"])
        emit({"citekey": r["citekey"], "page": r["page"], "part": r["part"],
              "score": round(score, 4), "preview": r["preview"],
              "zotero_pdf_page": (f"zotero://open-pdf/library/items/{key}?page={r['page']}"
                                  if key else None)})


if __name__ == "__main__":
    main()

"""
06_build_semantic_index.py — optional embedding index over Fulltext/, run last.

Chunks each Fulltext/{citekey}.md by page (long pages split at paragraph
boundaries, near-empty pages merged forward), embeds new or re-extracted
papers through Ollama, prunes papers gone from the full-text manifest, and
writes Code/data/semantic_index.sqlite plus Code/data/semantic_manifest.json
(the file the lit-vault-search skill reads to decide whether semantic search
is on). Both are gitignored and regenerable; the vault build never reads them.

Unless [semantic] embed_abstracts is false, every abstract in the corpus is
also embedded (one vector per paper, incremental on the abstract's hash), so
semantic_report.py and serve_search.py can match papers that have no
extracted full text.

Missing infrastructure (no numpy, no Ollama, model not pulled) is a degraded
mode, not an error: one log line, manifest status updated, exit 0.

Incremental on (citekey, sha256) from fulltext_manifest.json. A change of
embedding model, model digest or chunker version needs a full rebuild,
which only runs with --force.

Usage:
    uv run python scripts/06_build_semantic_index.py
        [--setup] [--check] [--measure] [--force] [--citekey KEY]
"""

import argparse
import hashlib
import sys
import time
from datetime import datetime

from lib.logutil import setup_logging
from lib.semantic import (
    CHUNKER_VERSION,
    INDEX_PATH,
    check_capability,
    delete_abstract,
    delete_paper,
    embed,
    indexed_abstracts,
    indexed_papers,
    load_chunks,
    load_semantic_config,
    open_index,
    pull_model,
    read_meta,
    replace_abstract,
    replace_paper,
    write_meta,
    write_semantic_manifest,
)
from lib.store import (
    CORPUS_PATH,
    FULLTEXT_DIR,
    LOG_DIR,
    MANIFEST_PATH,
    read_json,
    read_jsonl,
)


def wanted_papers() -> dict[str, str]:
    """citekey -> sha256 for every paper with usable full text."""
    manifest = read_json(MANIFEST_PATH, {}) or {}
    return {ck: e["sha256"] for ck, e in sorted(manifest.items())
            if e.get("status") == "ok" and e.get("sha256")
            and (FULLTEXT_DIR / f"{ck}.md").exists()}


def wanted_abstracts() -> dict[str, tuple[str, str]]:
    """citekey -> (sha256, abstract text) for every corpus record with one."""
    out = {}
    for r in read_jsonl(CORPUS_PATH):
        text = (r.get("abstract") or "").strip()
        if text and r.get("citekey"):
            out[r["citekey"]] = (
                hashlib.sha256(text.encode("utf-8")).hexdigest(), text)
    return dict(sorted(out.items()))


def main():
    parser = argparse.ArgumentParser(
        description="Build/update the semantic (embedding) index over Fulltext/.")
    parser.add_argument("--setup", action="store_true",
                        help="Pull the configured embedding model into Ollama, then build.")
    parser.add_argument("--check", action="store_true",
                        help="Capability check only; refresh the manifest status.")
    parser.add_argument("--measure", action="store_true",
                        help="Report what would be (re)embedded; write nothing.")
    parser.add_argument("--force", action="store_true",
                        help="Confirm a full rebuild after a model/chunker change, "
                             "or re-embed even unchanged papers.")
    parser.add_argument("--citekey", default=None,
                        help="Only (re)index this paper.")
    args = parser.parse_args()

    log = setup_logging(LOG_DIR, "semantic_index")
    cfg = load_semantic_config()

    cap = check_capability(cfg)
    if cap["status"] == "no_model" and args.setup:
        log.info("Pulling %s into Ollama (one-off, ~270 MB for nomic-embed-text)…",
                 cfg.embed_model)
        pull_model(cfg)
        cap = check_capability(cfg)

    if cap["status"] != "ok":
        log.info("Semantic index skipped: %s", cap["message"])
        if not args.measure:
            write_semantic_manifest(status=cap["status"], embed_model=cfg.embed_model,
                                    message=cap["message"])
        return

    papers = wanted_papers()
    abstracts = wanted_abstracts() if cfg.embed_abstracts else {}
    if args.citekey:
        if args.citekey not in papers and args.citekey not in abstracts:
            raise SystemExit(f"{args.citekey} has neither usable full text in "
                             f"{MANIFEST_PATH} nor an abstract in the corpus")

    conn = open_index()
    try:
        meta = read_meta(conn)
        have = indexed_papers(conn)
        have_abs = indexed_abstracts(conn)
        want_meta = {"embed_model": cfg.embed_model,
                     "model_digest": cap["model_digest"],
                     "chunker_version": str(CHUNKER_VERSION),
                     "max_words": str(cfg.max_words),
                     "min_words": str(cfg.min_words)}
        changed = [k for k, v in want_meta.items()
                   if (have or have_abs) and meta.get(k) != v]
        if args.check:
            status = "stale" if changed else ("ok" if have or have_abs else "no_index")
            write_semantic_manifest(status=status, embed_model=cfg.embed_model,
                                    message="" if status == "ok" else
                                    "run 06_build_semantic_index.py"
                                    + (" --force" if changed else ""))
            log.info("Semantic index: %s", status)
            return
        if changed:
            detail = ", ".join(f"{k}: {meta.get(k)} -> {want_meta[k]}" for k in changed)
            if not args.force:
                log.warning("Index was built with different settings (%s) — "
                            "a full rebuild is needed; rerun with --force", detail)
                if not args.measure:
                    write_semantic_manifest(
                        status="stale", message=f"rebuild needed ({detail}); "
                        "run 06_build_semantic_index.py --force")
                return
            log.warning("Full rebuild (%s)", detail)
            have, have_abs = {}, {}
            if not args.measure:
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM abstracts")

        if args.citekey:
            scope = {k: papers[k] for k in (args.citekey,) if k in papers}
            scope_abs = {k: abstracts[k] for k in (args.citekey,) if k in abstracts}
        else:
            scope, scope_abs = papers, abstracts
        todo = [ck for ck, h in scope.items() if args.force or have.get(ck) != h]
        gone = [] if args.citekey else sorted(set(have) - set(papers))
        todo_abs = [ck for ck, (h, _) in scope_abs.items()
                    if args.force or have_abs.get(ck) != h]
        gone_abs = [] if args.citekey else sorted(set(have_abs) - set(abstracts))

        if args.measure:
            n_chunks = sum(len(load_chunks(ck, cfg)) for ck in todo)
            print(f"\n{'='*50}\n"
                  f"  Papers with full text: {len(papers)}\n"
                  f"  Already indexed:       {len(set(have) & set(papers))}\n"
                  f"  To (re)embed:          {len(todo)} papers, {n_chunks} chunks\n"
                  f"  To prune:              {len(gone)}\n"
                  f"  Abstracts in corpus:   {len(abstracts)}"
                  + ("" if cfg.embed_abstracts else " (embed_abstracts off)") + "\n"
                  f"  Abstracts to (re)embed: {len(todo_abs)}\n"
                  f"  Abstracts to prune:    {len(gone_abs)}\n{'='*50}")
            return

        for ck in gone:
            delete_paper(conn, ck)
            log.info("PRUNED  %s", ck)
        for ck in gone_abs:
            delete_abstract(conn, ck)
            log.info("PRUNED ABSTRACT  %s", ck)
        conn.commit()

        t0 = time.monotonic()
        total_chunks = 0
        for i, ck in enumerate(todo, start=1):
            chunks = load_chunks(ck, cfg)
            if not chunks:
                delete_paper(conn, ck)
                log.warning("EMPTY  %s — no text to index", ck)
                continue
            vectors = embed([c.text for c in chunks], cfg, prefix=cfg.doc_prefix)
            replace_paper(conn, ck, scope[ck], chunks, vectors)
            write_meta(conn, {**want_meta, "dims": vectors.shape[1],
                              "built_at": datetime.now().isoformat(timespec="seconds")})
            conn.commit()  # per paper, so an interrupted run keeps its progress
            total_chunks += len(chunks)
            log.info("EMBEDDED  %s (%d chunks) [%d/%d]", ck, len(chunks), i, len(todo))

        if todo_abs:
            log.info("Embedding %d abstracts…", len(todo_abs))
            vectors = embed([scope_abs[ck][1] for ck in todo_abs], cfg,
                            prefix=cfg.doc_prefix)
            for ck, vec in zip(todo_abs, vectors):
                replace_abstract(conn, ck, scope_abs[ck][0], scope_abs[ck][1], vec)
            write_meta(conn, {**want_meta, "dims": vectors.shape[1],
                              "built_at": datetime.now().isoformat(timespec="seconds")})
            conn.commit()
            log.info("EMBEDDED ABSTRACTS  %d", len(todo_abs))

        n_chunks, n_papers = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT citekey) FROM chunks").fetchone()
        n_abs = conn.execute("SELECT COUNT(*) FROM abstracts").fetchone()[0]
        dims = read_meta(conn).get("dims")
    finally:
        conn.close()

    elapsed = time.monotonic() - t0
    write_semantic_manifest(
        status="ok", message="", embed_model=cfg.embed_model,
        model_digest=cap["model_digest"], chunker_version=CHUNKER_VERSION,
        dims=int(dims) if dims else None, chunks=n_chunks, papers=n_papers,
        abstracts=n_abs,
        index_mb=round(INDEX_PATH.stat().st_size / 1e6, 1),
    )
    summary = (f"\n{'='*50}\n"
               f"  Embedded:  {len(todo)} papers, {total_chunks} chunks, "
               f"{len(todo_abs)} abstracts in {elapsed:.0f}s\n"
               f"  Pruned:    {len(gone)} papers, {len(gone_abs)} abstracts\n"
               f"  Index:     {n_papers} papers, {n_chunks} chunks, "
               f"{n_abs} abstracts, "
               f"{INDEX_PATH.stat().st_size / 1e6:.1f} MB\n{'='*50}")
    log.debug(summary)
    print(summary)


if __name__ == "__main__":
    sys.exit(main())

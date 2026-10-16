"""
semantic_report.py — deterministic search report: a query becomes a rendered
report page, with no LLM anywhere.

The query is embedded through the local Ollama model and cosine-compared
against every full-text page chunk and every abstract in the semantic index
(built by 06). Hits aggregate to paper level — a paper's score is its best
hit — papers under --min-score are dropped and the top --max-papers kept.
The result is written as a review.json of type "search" (schema in
render_report.py) and rendered through the same template machinery as the
LLM-written surveys: archived page in Summaries/, stable lit-vault.html.
Papers are grouped by where their best hit landed: full text first,
abstract-only second.

This is retrieval, not synthesis — a score says "near the query in embedding
space", never "this paper shows X". Full-text cards carry the best-matching
page's preview and a PDF deep link to that page; open the paper, don't cite
the score. Identical index + query + settings give an identical report.

With --gaps, citation_gaps.py then augments the archived report with the
OpenAlex gap analysis, exactly as for a survey.

If Ollama, numpy or the index is unavailable, prints one status line and
exits 1. serve_search.py calls run_search() directly and shows the same
message in the browser.

Usage:
    uv run python scripts/semantic_report.py --query "..."
        [--min-score 0.45] [--max-papers 30]
        [--scope both|fulltext|abstracts] [--gaps]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from lib.semantic import (
    CHUNKER_VERSION,
    INDEX_PATH,
    check_capability,
    embed,
    load_abstract_matrix,
    load_matrix,
    load_semantic_config,
    open_index,
    read_meta,
)
from lib.store import DATA_DIR, ROOT

PAPERS_JSON = ROOT / "papers.json"
REVIEW_PATH = DATA_DIR / "search_review.json"
RENDER_SCRIPT = Path(__file__).resolve().parent / "render_report.py"
GAPS_SCRIPT = Path(__file__).resolve().parent / "citation_gaps.py"

ARCHIVED_RE = re.compile(r"^archived: (Summaries/\S+\.html)", re.MULTILINE)


def run_script(script: Path, arg: str) -> subprocess.CompletedProcess:
    """Run a sibling stdlib-only script; UTF-8 pipes on every platform."""
    return subprocess.run(
        [sys.executable, str(script), arg], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def fail(message: str) -> dict:
    return {"ok": False, "error": message}


def best_hits(conn, qvec, scope: str, meta: dict) -> tuple[dict, dict]:
    """citekey -> best hit across the chosen tables, plus searched counts."""
    best: dict[str, dict] = {}
    counts = {"chunks": 0, "chunk_papers": 0, "abstracts": 0}
    if scope in ("both", "fulltext"):
        rows, matrix = load_matrix(conn)
        if rows:
            if meta.get("chunker_version") != str(CHUNKER_VERSION):
                raise LookupError("index built by another chunker — run "
                                  "06_build_semantic_index.py --force")
            counts["chunks"] = len(rows)
            counts["chunk_papers"] = len({r["citekey"] for r in rows})
            for r, s in zip(rows, matrix @ qvec):
                s = float(s)
                b = best.get(r["citekey"])
                if b is None or s > b["score"]:
                    best[r["citekey"]] = {"score": s, "match": "fulltext",
                                          "page": r["page"], "part": r["part"],
                                          "excerpt": r["preview"]}
    if scope in ("both", "abstracts"):
        rows, matrix = load_abstract_matrix(conn)
        if rows:
            counts["abstracts"] = len(rows)
            for r, s in zip(rows, matrix @ qvec):
                s = float(s)
                b = best.get(r["citekey"])
                if b is None or s > b["score"]:
                    best[r["citekey"]] = {"score": s, "match": "abstract"}
    return best, counts


def build_review(query: str, hits: list, counts: dict, cfg, min_score: float,
                 max_papers: int, scope: str, capped: bool) -> dict:
    """hits: [(citekey, best-hit dict)], already filtered, sorted, capped."""
    groups = []
    for name, match in (("Matched in full text", "fulltext"),
                        ("Matched on the abstract", "abstract")):
        cards = []
        for ck, h in hits:
            if h["match"] != match:
                continue
            card = {"citekey": ck, "score": round(h["score"], 4),
                    "match": h["match"]}
            if match == "fulltext":
                card.update({"page": h["page"], "part": h["part"],
                             "excerpt": h["excerpt"], "pdf_page": h["page"]})
            cards.append(card)
        if cards:
            groups.append({"name": name, "cards": cards})

    lede = (f"Deterministic semantic search — no language model involved. "
            f"The query was embedded locally with {cfg.embed_model} and "
            f"compared against {counts['chunks']:,} full-text page chunks "
            f"({counts['chunk_papers']:,} papers) and {counts['abstracts']:,} "
            f"abstracts; {len(hits)} papers scored at or above "
            f"{min_score:.2f}" + (f", capped at the top {max_papers}."
                                  if capped else "."))
    return {
        "schema": 1,
        "type": "search",
        "topic": " ".join(query.split()),
        "lede": lede,
        "params": {"query": query, "embed_model": cfg.embed_model,
                   "min_score": min_score, "max_papers": max_papers,
                   "scope": scope, "searched": counts,
                   "generated": date.today().isoformat()},
        "groups": groups,
    }


def run_search(query: str, min_score: float | None = None,
               max_papers: int = 30, scope: str = "both",
               gaps: bool = False) -> dict:
    """The whole pipeline; returns {"ok": True, ...} or {"ok": False, "error"}."""
    query = query.strip()
    if not query:
        return fail("empty query")
    if scope not in ("both", "fulltext", "abstracts"):
        return fail(f"unknown scope {scope!r}")

    cfg = load_semantic_config()
    if min_score is None:
        min_score = cfg.min_score
    cap = check_capability(cfg)
    if cap["status"] != "ok":
        return fail(cap["message"])
    if not INDEX_PATH.exists():
        return fail("no semantic index — run 06_build_semantic_index.py")
    if not PAPERS_JSON.exists():
        return fail("papers.json missing — run the pipeline (step 05) first")

    conn = open_index()
    try:
        meta = read_meta(conn)
        if meta and (meta.get("embed_model") != cfg.embed_model
                     or meta.get("model_digest") != cap["model_digest"]):
            return fail("index built with another embedding model — run "
                        "06_build_semantic_index.py --force")
        qvec = embed([query], cfg, prefix=cfg.query_prefix)[0]
        try:
            best, counts = best_hits(conn, qvec, scope, meta)
        except LookupError as e:
            return fail(str(e))
    finally:
        conn.close()
    if not best:
        return fail("nothing to search — the index has no "
                    + {"fulltext": "full-text chunks", "abstracts": "abstracts"}
                      .get(scope, "vectors")
                    + "; run 06_build_semantic_index.py")

    papers = {p["citekey"]
              for p in json.loads(PAPERS_JSON.read_text(encoding="utf-8"))["papers"]}
    stale = sorted(set(best) - papers)
    for ck in stale:
        del best[ck]
    if stale:
        print(f"note: {len(stale)} indexed paper(s) not in papers.json, "
              "skipped (re-run the pipeline): " + ", ".join(stale[:5]),
              file=sys.stderr)

    ranked = sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    hits = [(ck, h) for ck, h in ranked if h["score"] >= min_score]
    if not hits:
        top = ranked[0][1]["score"] if ranked else 0.0
        return fail(f"no papers scored at or above {min_score:.2f} — the best "
                    f"was {top:.2f}; lower the threshold")
    capped = len(hits) > max_papers
    hits = hits[:max_papers]

    review = build_review(query, hits, counts, cfg, min_score, max_papers,
                          scope, capped)
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_PATH.write_text(json.dumps(review, indent=2, ensure_ascii=False)
                           + "\n", encoding="utf-8", newline="\n")

    render = run_script(RENDER_SCRIPT, str(REVIEW_PATH))
    if render.returncode != 0:
        return fail("render failed:\n" + (render.stderr or render.stdout).strip())
    m = ARCHIVED_RE.search(render.stdout)
    archived_html = m.group(1) if m else None

    result = {"ok": True, "n_papers": len(hits),
              "n_fulltext": sum(1 for _, h in hits if h["match"] == "fulltext"),
              "min_score": min_score, "capped": capped,
              "archived_html": archived_html,
              "stable_html": str(ROOT / "lit-vault.html"),
              "render_summary": render.stdout.strip()}

    if gaps and archived_html:
        gaps_json = ROOT / archived_html.replace(".html", ".json")
        run = run_script(GAPS_SCRIPT, str(gaps_json))
        result["gaps_output"] = (run.stdout + run.stderr).strip()
        result["gaps_ok"] = run.returncode == 0
    return result


def main() -> int:
    try:
        cfg = load_semantic_config()
    except FileNotFoundError:
        print("error: Code/config.toml missing — run the lit-vault-setup "
              "skill first", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser(
        description="Deterministic semantic search -> rendered report (no LLM).")
    ap.add_argument("--query", required=True, help="Natural-language question.")
    ap.add_argument("--min-score", type=float, default=cfg.min_score,
                    help=f"drop papers scoring below this (default {cfg.min_score})")
    ap.add_argument("--max-papers", type=int, default=30,
                    help="keep at most this many papers (default 30)")
    ap.add_argument("--scope", choices=("both", "fulltext", "abstracts"),
                    default="both", help="which vectors to search (default both)")
    ap.add_argument("--gaps", action="store_true",
                    help="then run citation_gaps.py on the archived report")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    result = run_search(args.query, args.min_score, args.max_papers,
                        args.scope, gaps=args.gaps)
    if not result["ok"]:
        print("error:", result["error"], file=sys.stderr)
        return 1
    print(result["render_summary"])
    print(f"{result['n_papers']} papers ({result['n_fulltext']} matched in "
          f"full text) at min score {result['min_score']:.2f}"
          + (" — capped" if result["capped"] else ""))
    if args.gaps:
        print(result.get("gaps_output", ""))
        if not result.get("gaps_ok"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

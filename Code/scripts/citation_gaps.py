"""citation_gaps.py — find papers a summary's cluster points at that are
missing from the Zotero library, via the OpenAlex citation graph.

Not a build step: run it after lit-vault-search has archived a survey. It
takes the cluster (every citekey the survey cites), resolves it to OpenAlex
works, and mines two directions:

  backward  papers the cluster REFERENCES that are not in the library,
            ranked by how many cluster papers cite them
  forward   papers CITING >= min_cluster_cites cluster papers that are not
            in the library, ranked by how many cluster papers they cite

"Not in the library" is decided against the BBT export plus every item in
zotero.sqlite (so a one-collection export still counts the whole of
Zotero): normalised-DOI comparison, with a normalised-title fallback only
for items without a DOI (lib/matching.py). Both directions are capped at max_per_direction and
tie-broken deterministically (cluster count, then global citations, then
OpenAlex ID). No LLM anywhere.

The result is written into the summary's own archived JSON as an optional
top-level "gaps" block, and the summary is re-rendered in place — the gaps
appear as one extra section at the bottom of the same page. Deleting the
block and re-rendering removes the section; nothing else changes.

Settings come from [openalex] in Code/config.toml (all optional; the
section may be absent). Work metadata is cached in Code/data/ with a TTL;
citation lists are always fetched fresh. Offline, the script stands down:
one line, exit 0.

Usage:
    uv run python scripts/citation_gaps.py [SUMMARY_JSON]
        [--check] [--measure] [--force] [--no-render]

SUMMARY_JSON defaults to the newest Summaries/*--survey--*.json.
--check    capability check only (is OpenAlex reachable?); exit 0
--measure  run the analysis and print what it found, but write nothing
--force    ignore the metadata cache TTL; refetch everything touched
--no-render  write the gaps block into the JSON but skip re-rendering
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from lib import bbt, openalex
from lib.config import load_config
from lib.logutil import setup_logging
from lib.matching import build_holdings, in_holdings, normalize_doi
from lib.openalex import (
    MAX_BACKWARD_RESOLVE, OpenAlexError, check_capability, citing_works,
    load_cache, load_openalex_config, reconstruct_abstract, save_cache, wid,
    work_authors, work_by_title, works_by_dois, works_by_ids,
)
from lib.store import LOG_DIR, ROOT
from lib.zotero_db import fetch_holdings, open_zotero_db

SUMMARIES_DIR = ROOT / "Summaries"
PAPERS_JSON = ROOT / "papers.json"
RENDER_SCRIPT = Path(__file__).resolve().parent / "render_report.py"
GAPS_SCHEMA = 1


def newest_survey(summaries: Path = SUMMARIES_DIR) -> Path | None:
    """Newest archived survey JSON; the date prefix makes stems sort."""
    found = [p for p in summaries.glob("*--survey--*.json") if p.is_file()]
    return max(found, key=lambda p: (p.stem, p.stat().st_mtime), default=None)


def cluster_citekeys(review: dict) -> list:
    """Every citekey the survey cites: cards plus 'also' lists, deduped,
    in first-appearance order."""
    seen, out = set(), []
    for g in review.get("groups", []):
        keys = [c.get("citekey") for c in g.get("cards", [])]
        keys.extend(g.get("also", []))
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    return out


def resolve_cluster(entries: list, cfg, cache, force: bool, log) -> tuple:
    """entries: [(citekey, doi, title)] -> (cluster rows, {short_id: work},
    notes). Works arrive with referenced_works for the backward pass."""
    by_doi = works_by_dois([d for _, d, _ in entries if d], cfg, cache,
                           with_refs=True, force=force)
    rows, works, title_only, unresolved = [], {}, [], []
    for citekey, doi, title in entries:
        work = by_doi.get(normalize_doi(doi)) if doi else None
        match = "doi" if work else "none"
        if work is None and title:
            work = work_by_title(title, cfg)
            if work is not None:
                match = "title"
        if work is None:
            rows.append({"citekey": citekey, "openalex_id": None, "match": "none"})
            unresolved.append(citekey)
            continue
        short = wid(work["id"])
        rows.append({"citekey": citekey, "openalex_id": short, "match": match,
                     "doi": normalize_doi(work.get("doi")) or None})
        works[short] = {**work, "citekey": citekey}
        if match == "title":
            title_only.append(citekey)
    notes = []
    if title_only:
        notes.append(f"{len(title_only)} cluster paper(s) matched by title "
                     f"only: {', '.join(sorted(title_only))}")
    if unresolved:
        notes.append(f"{len(unresolved)} cluster paper(s) not found on "
                     f"OpenAlex, excluded: {', '.join(sorted(unresolved))}")
    log.info("cluster: %d papers, %d resolved (%d by title), %d unresolved",
             len(entries), len(works), len(title_only), len(unresolved))
    return rows, works, notes


def candidate(work: dict, cluster_count: int, citekeys: list) -> dict:
    authors = work_authors(work)
    return {
        "title": work.get("title") or "",
        "authors": authors,
        "n_authors": len(work.get("authorships", []) or authors),
        "year": work.get("publication_year"),
        "doi": normalize_doi(work.get("doi")) or None,
        "openalex_id": work["id"],
        "openalex_url": f"https://openalex.org/{work['id']}",
        "cited_by_count": work.get("cited_by_count") or 0,
        "cluster_count": cluster_count,
        "cluster_citekeys": sorted(citekeys),
        "abstract": "",
    }


def rank_and_cap(cands: list, cap: int) -> list:
    cands.sort(key=lambda c: (-c["cluster_count"], -c["cited_by_count"],
                              c["openalex_id"]))
    return cands[:cap]


def backward_gaps(cluster_works: dict, holdings, cfg, cache,
                  force: bool, log) -> tuple:
    """Papers the cluster references, not in the library."""
    cited_by = defaultdict(list)   # ref short id -> [citekeys citing it]
    for short, work in cluster_works.items():
        for ref in work.get("referenced_works", []) or []:
            rid = wid(ref)
            if rid not in cluster_works:
                cited_by[rid].append(work["citekey"])
    multi = sorted(r for r, cks in cited_by.items() if len(cks) > 1)
    single = sorted(r for r, cks in cited_by.items() if len(cks) == 1)
    to_resolve = multi + single[:max(0, MAX_BACKWARD_RESOLVE - len(multi))]
    notes = []
    if len(to_resolve) < len(cited_by):
        notes.append(f"backward: resolved {len(to_resolve)} of "
                     f"{len(cited_by)} referenced works (singleton cap)")
    resolved = works_by_ids(to_resolve, cfg, cache, force=force)
    out = []
    for rid in to_resolve:
        work = resolved.get(rid)
        if work is None or work["id"] in cluster_works:
            continue   # unresolvable, or merged into a cluster work
        if in_holdings(holdings, work.get("doi"), work.get("title")):
            continue
        out.append(candidate(work, len(cited_by[rid]), cited_by[rid]))
    log.info("backward: %d unique refs, %d resolved, %d missing from library",
             len(cited_by), len(resolved), len(out))
    return rank_and_cap(out, cfg.max_per_direction), notes


def forward_gaps(cluster_works: dict, holdings, cfg, log) -> tuple:
    """Papers citing >= min_cluster_cites cluster papers, not in the library."""
    if len(cluster_works) < cfg.min_cluster_cites:
        return [], [f"forward: skipped — cluster has {len(cluster_works)} "
                    f"resolved paper(s), fewer than min_cluster_cites "
                    f"({cfg.min_cluster_cites})"]
    citers, truncated = citing_works(list(cluster_works), cfg)
    notes = []
    if truncated:
        notes.append("forward: citing-works listing truncated at "
                     f"{cfg.max_forward_pages * openalex.PER_PAGE:,} works")
    out = []
    for work in citers:
        if work["id"] in cluster_works:
            continue
        cited = sorted(cluster_works[rid]["citekey"]
                       for rid in set(map(wid, work.get("referenced_works", [])))
                       if rid in cluster_works)
        if len(cited) < cfg.min_cluster_cites:
            continue
        if in_holdings(holdings, work.get("doi"), work.get("title")):
            continue
        out.append(candidate(work, len(cited), cited))
    log.info("forward: %d citing works fetched, %d cite >=%d cluster papers "
             "and are missing from library",
             len(citers), len(out), cfg.min_cluster_cites)
    return rank_and_cap(out, cfg.max_per_direction), notes


def add_abstracts(cands: list, cfg, cache, force: bool) -> None:
    """One batch for the final candidates only (inverted indexes are big)."""
    ids = [c["openalex_id"] for c in cands]
    if not ids:
        return
    got = works_by_ids(ids, cfg, cache, select="id,abstract_inverted_index",
                       need=("abstract_inverted_index",), force=force)
    for c in cands:
        w = got.get(c["openalex_id"])
        if w:
            c["abstract"] = reconstruct_abstract(w.get("abstract_inverted_index"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find papers a summary's cluster cites or is cited by "
                    "that are missing from the Zotero library (OpenAlex)")
    ap.add_argument("summary", nargs="?", type=Path,
                    help="archived survey JSON (default: newest in Summaries/)")
    ap.add_argument("--check", action="store_true",
                    help="capability check only; exit 0 either way")
    ap.add_argument("--measure", action="store_true",
                    help="run the analysis but write nothing")
    ap.add_argument("--force", action="store_true",
                    help="ignore the metadata cache TTL")
    ap.add_argument("--no-render", action="store_true",
                    help="write the gaps block but skip re-rendering")
    args = ap.parse_args()
    log = setup_logging(LOG_DIR, "citation_gaps")

    try:
        cfg = load_openalex_config()
        vault_cfg = load_config()
    except FileNotFoundError:
        print("citation gaps skipped: Code/config.toml missing — run the "
              "lit-vault-setup skill first")
        return 0

    cap = check_capability(cfg)
    if args.check:
        print(cap["message"])
        return 0
    if cap["status"] != "ok":
        log.info("citation gaps skipped: %s", cap["message"])
        return 0

    # -- inputs ---------------------------------------------------------------
    summary = args.summary or newest_survey()
    if summary is None or not summary.exists():
        print("error: no archived survey found — run a lit-vault search "
              "first, or pass the Summaries/*.json path", file=sys.stderr)
        return 1
    review = json.loads(summary.read_text(encoding="utf-8"))
    if review.get("type") != "survey":
        print(f"error: {summary.name} is a {review.get('type')!r} report — "
              "citation gaps work on surveys", file=sys.stderr)
        return 1
    papers_data = json.loads(PAPERS_JSON.read_text(encoding="utf-8")) \
        if PAPERS_JSON.exists() else None
    if not papers_data:
        print("error: papers.json missing — run the pipeline (step 05) first",
              file=sys.stderr)
        return 1
    papers = {p["citekey"]: p for p in papers_data["papers"]}

    citekeys = cluster_citekeys(review)
    missing = [k for k in citekeys if k not in papers]
    if missing:
        print("error: summary cites citekeys not in papers.json: "
              + ", ".join(missing) + " — re-run the pipeline (lit-vault-"
              "refresh) so papers.json matches the library", file=sys.stderr)
        return 1
    entries = [(k, papers[k].get("doi") or "", papers[k].get("title") or "")
               for k in citekeys]

    # -- analysis -------------------------------------------------------------
    cache = load_cache()
    try:
        cluster_rows, cluster_works, notes = resolve_cluster(
            entries, cfg, cache, args.force, log)
        if not cluster_works:
            print("error: no cluster paper could be resolved on OpenAlex — "
                  "nothing to analyse", file=sys.stderr)
            return 1

        export = bbt.find_export(vault_cfg.export)
        # The export may cover one collection; the database covers all of
        # Zotero, so a paper held elsewhere is never reported as a gap.
        with open_zotero_db(vault_cfg.zotero_data) as conn:
            db_rows = fetch_holdings(conn) if conn is not None else []
        if not db_rows:
            notes.append("library membership checked against the export "
                         "only (zotero.sqlite unreadable) — a paper outside "
                         "the exported collection may be listed as missing")
        holdings = build_holdings(bbt.load_bibtex_export(export)["items"],
                                  extra=db_rows)
        log.info("holdings: %d DOIs, %d titles from %s + %d database items",
                 len(holdings.dois), len(holdings.titles), export.name,
                 len(db_rows))

        backward, b_notes = backward_gaps(cluster_works, holdings, cfg, cache,
                                          args.force, log)
        forward, f_notes = forward_gaps(cluster_works, holdings, cfg, log)
        add_abstracts(backward + forward, cfg, cache, args.force)
        notes += b_notes + f_notes
    except OpenAlexError as e:
        print(f"error: {e}", file=sys.stderr)
        # a non-retryable status is a malformed request: retrying won't help
        hint = ("this request is rejected every time — please report it"
                if e.status is not None else "try again later")
        print("error: OpenAlex failed mid-run; nothing written (a partial "
              f"gaps report would mislead) — {hint}", file=sys.stderr)
        return 1

    gaps = {
        "schema": GAPS_SCHEMA,
        "params": {"min_cluster_cites": cfg.min_cluster_cites,
                   "max_per_direction": cfg.max_per_direction,
                   "fetched": date.today().isoformat()},
        "cluster": cluster_rows,
        "backward": backward,
        "forward": forward,
        "notes": notes,
    }
    summary_line = (f"{len(backward)} backward + {len(forward)} forward gaps "
                    f"from {len(cluster_works)} cluster papers "
                    f"({openalex.CALLS} API calls)")
    if args.measure:
        print(summary_line + " — measure only, nothing written")
        return 0

    review["gaps"] = gaps
    summary.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8", newline="\n")
    save_cache(cache)
    log.info("%s -> %s", summary_line, summary.name)

    if args.no_render:
        print(f"{summary}: gaps block written ({summary_line}); not re-rendered")
        return 0
    rc = subprocess.run([sys.executable, str(RENDER_SCRIPT), str(summary)],
                        cwd=ROOT).returncode
    if rc == 0:
        print(f"gaps added to {summary.stem}: {summary_line}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

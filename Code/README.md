# The pipeline

Turns a Better BibTeX export of a Zotero library into the literature vault
at the repo root. The `lit-vault-setup` and `lit-vault-refresh` skills run
all of this for you; this page is for running it by hand or changing it.
`PIPELINE_PLAN.md` is the format spec and the reasoning behind it.

The build is fully deterministic — no LLM calls. Identical input gives
byte-identical output, and files are only rewritten when their content
changes, so git diffs show exactly what changed in your library.

## Setup (once)

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/),
   then run `uv sync` in `Code/` (it fetches Python 3.11 if needed).
2. Set up the Better BibTeX auto-export: `../docs/setup-bbt.md`.
3. Copy `config.example.toml` to `config.toml` and edit it — every key is
   commented. Forward slashes in paths work on every OS.
4. `python scripts/doctor.py --prereqs`, then build (below), then
   `python scripts/doctor.py`.

## Running (from `Code/`)

```sh
uv run python scripts/01_build_corpus.py        # export -> data/corpus.jsonl; --tags-report: papers per tag
uv run python scripts/02_extract_fulltext.py    # highlights + full text of key papers; --tier all for every PDF
uv run python scripts/03_scan_my_notes.py       # optional reading-notes vault; no-op if not configured
uv run python scripts/04_build_vault.py         # Home.md, INDEX.md, Tags/, Papers/
uv run python scripts/05_build_papers_json.py   # papers.json at the vault root
uv run python scripts/06_build_semantic_index.py  # optional; stands down without Ollama
python scripts/doctor.py                        # checks; exit 1 on any failure
```

Adding papers later: Zotero rewrites the export; re-run 01→06. Step 02
only extracts PDFs it hasn't seen (`--force` re-extracts) but always
refreshes highlights; step 06 only embeds new full text.

Useful step-02 flags:

- `--measure` — PDF/page counts and projected `Fulltext/` size; writes nothing.
- `--annotations-only` — refresh highlights, skip PDFs.
- `--citekey KEY` — one paper.

`doctor.py` flags: `--prereqs` (Python, uv, Zotero running, Better BibTeX
installed — before any config exists), `--compare OLD_PAPERS_JSON` (prints
`+N papers, M updated, K removed`), `--max-export-age DAYS`.

### Semantic index (optional)

Step 06 embeds one chunk per `Fulltext/` page (long pages split, near-empty
ones merged) into `data/semantic_index.sqlite`, through a local
[Ollama](https://ollama.com) model, and records its state in
`data/semantic_manifest.json` — the file the search skill reads to decide
whether to use it. Once, with Ollama running:
`uv run python scripts/06_build_semantic_index.py --setup` (pulls
`nomic-embed-text`, ~270 MB, then builds). After that each run embeds only
new or re-extracted papers. Flags: `--measure` (what would be embedded),
`--check` (refresh the status only), `--force` (rebuild after changing the
model or chunk settings), `--citekey KEY`.

Search it with:

```sh
uv run python scripts/semantic_search.py --query "how do operators plan under demand uncertainty"
uv run python scripts/semantic_search.py --like someCitekey:12   # related pages in other papers
```

Hits are leads to open in `Fulltext/`, never evidence in themselves.
Without Ollama both scripts print one status line and exit 0, and the
vault builds identically.

Unless `embed_abstracts = false` in `[semantic]`, step 06 also embeds every
abstract in the library (one vector per paper, incremental), so the free
search below can match papers whose full text isn't extracted.

### Free search UI (no LLM)

A deterministic alternative to the LLM-driven search skill — retrieval
only, completely free and offline:

```sh
uv run python scripts/serve_search.py        # opens http://127.0.0.1:8787/search
```

Type a question, set the score threshold and paper cap, submit: the query
is embedded through local Ollama, cosine-compared against every indexed
full-text page and abstract, and the top papers (grouped by whether the
best match was a full-text page or the abstract) are rendered through the
same report template as the surveys — archived in `Summaries/`, with the
stable `lit-vault.html` rewritten. Full-text matches deep-link the PDF at
the best-matching page. Ticking "gap analysis" runs `citation_gaps.py` on
the result as usual (that part needs the network).

No language model is involved, so the report ranks and quotes, but cannot
summarise: scores are cosine similarities ("near the query in embedding
space"), never statements of what a paper shows. Exact-term queries
(acronyms, author names) match less reliably than with the keyword funnel
the search skill uses.

The same search works headless:

```sh
uv run python scripts/semantic_report.py --query "..." [--min-score 0.6]
    [--max-papers 30] [--scope both|fulltext|abstracts] [--gaps]
```

**Calibrating.** `min_score` in `[semantic]` drops weak hits. The default
(0.45) is permissive — `nomic-embed-text` scores mostly fall between 0.70
and 0.80. Try a few questions whose right papers you know, note the scores
of right and wrong hits, and set `min_score` between them.

### Reports

The search skill renders its clickable report page with
`python Code/scripts/render_report.py review.json` (stdlib only — no uv
needed; the `review.json` schema is in its docstring). Each run archives
the report to `Summaries/` and rewrites `lit-vault.html` at the vault root,
whose dropdown opens any earlier report. Bookmark `lit-vault.html`.

### Global skills

`python Code/scripts/install_skills.py --user-name "Sam"` generates
personalised copies of the search and refresh skills into
`~/.claude/skills/` (setup runs this only after asking). `--dry-run` prints
them; `--repo OWNER/NAME` and `--linked-computer` add access routes.

## What gets generated where

| Path | Level | Written by |
|---|---|---|
| `Home.md` | landing page: counts, tags, how to use | 04 |
| `INDEX.md` | 1 — one line per paper, whole library | 04 |
| `Tags/{tag-slug}.md` | 1 — papers per manual Zotero tag, related tags | 04 |
| `Papers/@{citekey}.md` | 2 — metadata, one-line abstract, your highlights, links | 04 |
| `Fulltext/{citekey}.md` | 3 — page-marked plain text (`<!-- page N -->`) | 02 |
| `papers.json` | machine view of levels 1–2 | 05 |
| `lit-vault.html` | latest report + dropdown of past ones | render_report |
| `Summaries/` | every report: `YYYY-MM-DD--{type}--{slug}.json` + frozen `.html` | render_report |
| `Code/data/` | corpus, highlights, notes map, manifest, semantic index (gitignored) | 01–03, 06 |

Every generated file carries `generated: true`. **Don't edit anything in
the vault** — 04 rewrites `Papers/`, `Tags/`, `INDEX.md` and `Home.md` on
every run and deletes notes for papers or tags that no longer exist. Your
own notes on papers belong elsewhere (the optional `[main_vault]`); the
pipeline reads that vault but never writes to it, and never writes to
Zotero.

Highlights are grouped by colour under the headings in `[highlights]`
(`config.toml`); colours not listed appear under "Other highlights".

Page numbers in `Fulltext/` and in highlight links are the 1-based
**physical** page index (what `zotero://open-pdf/...?page=N` uses), not the
printed page number.

## Browsing in Obsidian (optional)

Open the repo root as a vault. Add `Fulltext/`, `Code/` and `logs/` to
*Settings → Files & links → Excluded files* so full text stays out of
search and the graph.

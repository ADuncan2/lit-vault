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
python scripts/doctor.py                        # checks; exit 1 on any failure
```

Adding papers later: Zotero rewrites the export; re-run 01→05. Step 02
only extracts PDFs it hasn't seen (`--force` re-extracts) but always
refreshes highlights.

Useful step-02 flags:

- `--measure` — PDF/page counts and projected `Fulltext/` size; writes nothing.
- `--annotations-only` — refresh highlights, skip PDFs.
- `--citekey KEY` — one paper.

`doctor.py` flags: `--prereqs` (Python, uv, Zotero running, Better BibTeX
installed — before any config exists), `--compare OLD_PAPERS_JSON` (prints
`+N papers, M updated, K removed`), `--max-export-age DAYS`.

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
| `Code/data/` | corpus, highlights, notes map, manifest (gitignored) | 01–03 |

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

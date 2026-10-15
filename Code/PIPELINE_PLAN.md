# lit-vault — vault format and pipeline (v3)

The spec of record for the **vault format** and the pipeline that builds it.
Skills and scripts follow this document; when they disagree with it, it is
a bug in one or the other.

## Goal

Make a Zotero library of hundreds or thousands of papers legible to an
agent, so a conversation can find what's in the library, read what papers
say, and point back to Zotero so every claim can be checked in one click.

The vault is built in three levels that mirror how a researcher finds
papers:

1. **Titles** — `INDEX.md` and the tag notes: scan for likely matches.
2. **Abstracts and highlights** — `Papers/` notes: read, then narrow down.
3. **Full text** — `Fulltext/` notes: go deep on the few that matter.

The search skill follows the same funnel, with one machine advantage added:
it searches across all abstracts at the first level instead of relying on
titles alone.

## The vault and your own notes

The literature vault is **generated and disposable**. If you keep reading
notes in an Obsidian vault (the optional **main vault**, `[main_vault]` in
config), the two are joined by citekey; without one, nothing else changes.

| | Literature vault (this repo) | Main vault (optional) |
|---|---|---|
| Purpose | What's in the library; what papers say | What you think |
| Written by | Pipeline scripts only | You |
| Regenerable | Yes — delete and rebuild from Zotero + scripts | No |
| Agent access | Read-only (except the report page) | Read-only |

Rules:

1. **Nothing human-written lives in the literature vault.** Your thinking
   about a paper goes in your own notes.
2. **Every generated file carries `generated: true`** in frontmatter, so
   generated and human notes stay separable if the two vaults ever merge.
3. **The pipeline reads the main vault but never writes to it**, and never
   writes to Zotero.

## Vault layout

```
Home.md                  landing page: counts, tag list, how to use
INDEX.md                 level 1: one line per paper, whole library
Tags/{tag-slug}.md       level 1: one note per Zotero tag, titles + links
Papers/@{citekey}.md     level 2: metadata, abstract, highlights, links
Fulltext/{citekey}.md    level 3: page-marked plain text, when a PDF exists
papers.json              machine view of levels 1–2: one object per paper
lit-vault.html           latest report page (written by the search skill)
Summaries/               archive of every report
Code/                    scripts, config, report template (data/ gitignored)
```

`papers.json` (step 05) duplicates the `Papers/` frontmatter plus abstracts
and highlight-section names in one file, with a precomputed
`display_author`. It exists so agents filter one JSON file instead of
parsing every note per session, and so the report renderer
(`render_report.py`, stdlib-only) can build the clickable `lit-vault.html`
page deterministically from a small typed `review.json` the agent writes —
the agent supplies judgment (grouping, one-line statements); the script
owns all formatting. Every render is also archived to `Summaries/`
(review.json + frozen HTML), and `lit-vault.html` carries a dropdown of
past reports. The `review.json` schema is documented in `render_report.py`'s
docstring; templates are frozen per report type in
`Code/templates/{type}.template.html`.

### Line format (shared by `INDEX.md` and tag notes)

```
- [[@citekey]] 2024 — Title of the paper · #tag-a #tag-b · ✎ ◆ ▤
```

Markers: `✎` you have a reading note in the main vault, `◆` key paper,
`▤` full text available. Sorted by year (newest first), then citekey.

### `INDEX.md`

Every paper in the library, tagged or not, in the line format above. Header
gives counts: papers, tagged, untagged, with full text, with reading notes.

### `Tags/{tag-slug}.md`

Deterministic, no summary. Contents:

- frontmatter: `generated`, `tag`, `count`
- **related tags**: the tags that most often co-occur with this one, with
  counts, linked to their own notes (`[tags] related_tags` in config)
- **member papers** in the line format above.

One note per manual tag, including tags with a single paper. Automatic
(publisher keyword) tags are ignored.

### `Papers/@{citekey}.md`

```yaml
---
generated: true
citekey: smith2024flex
zotero_key: ABCD1234
title: "…"
authors: ["Smith, J.", …]
year: 2024
item_type: journalArticle
doi: 10.…
tags: [flexibility-markets, dso]          # manual Zotero tags only, as slugs
collections: [Key papers]
key_paper: true
has_abstract: true
has_pdf: true
has_fulltext: true
fulltext_status: ok | needs_ocr | no_pdf | not_extracted
has_annotations: true
has_my_note: true
my_note: "obsidian://open?vault=Main&file=…"
zotero_item: "zotero://select/library/items/ABCD1234"
zotero_pdf: "zotero://open-pdf/library/items/EFGH5678"
---
```

Body, in this order:

1. Title linked to Zotero.
2. `## Abstract` — **the abstract on a single line**, never wrapped. A
   keyword search across `Papers/` then returns the whole abstract as the
   matching line.
3. `## My highlights` — Zotero annotations grouped by colour under the
   headings in `[highlights]` (config order; unlisted colours last, as
   "Other highlights"), each with a page-level `zotero://open-pdf` link.
   By convention a heading starting with ⛔ holds the reader's doubts and
   caveats; the search skill always surfaces those.
4. Links: tag notes, `Fulltext/{citekey}` (if present), the main-vault note
   (if present), Zotero item and PDF, DOI.

### `Fulltext/{citekey}.md`

Plain text, no callouts. Minimal frontmatter (`generated`, `citekey`,
`pages`, `status`, `extractor` version). A page marker before each page:

```
<!-- page 12 -->
```

Page numbers are the 1-based physical page index, which is what
`zotero://open-pdf/...?page=N` uses — not the printed page number. The
references section is cut at the first `References`/`Bibliography` heading
in the last third of the document.

## Pipeline (`Code/scripts/`)

No step calls an LLM. Step 06 calls a local embedding model, and only
to build a retrieval index outside the vault (see *Semantic index*).

| # | Script | Output |
|---|--------|--------|
| 01 | `01_build_corpus.py` | `data/corpus.jsonl` |
| 02 | `02_extract_fulltext.py` | `Fulltext/{citekey}.md`, `data/fulltext_manifest.json`, `data/annotations.jsonl` |
| 03 | `03_scan_my_notes.py` | `data/my_notes.json` (empty without a main vault) |
| 04 | `04_build_vault.py` | `Home.md`, `INDEX.md`, `Tags/`, `Papers/` |
| 05 | `05_build_papers_json.py` | `papers.json` |
| 06 | `06_build_semantic_index.py` | `data/semantic_index.sqlite`, `data/semantic_manifest.json` — optional, not part of the vault |
| — | `semantic_search.py` | JSON Lines hits to stdout (run by the search skill) |
| — | `doctor.py` | health checks; exit 1 on failure |
| — | `render_report.py` | `lit-vault.html`, `Summaries/` (run by the search skill) |
| — | `citation_gaps.py` | a "gaps" section added to an archived survey — optional, needs the network (see *Citation gaps*) |
| — | `install_skills.py` | personalised global skills (run by setup, with consent) |

Every setting comes from `config.toml`; the scripts take only a few
behaviour flags (`--tags-report`, `--measure`, `--tier`, `--force`, …).

**01 — build corpus.** Parses the Better BibTeX JSON export (whole library or
one collection; a configured folder must hold exactly one `.json`)
into one record per paper. Only manual tags (`type` absent or 0) are
kept; automatic publisher keywords are ignored. A paper is
a key paper if it carries `key_tag` or sits in `key_collection` (including
sub-collections). Fully rewritten every run; the export is the source of
truth.

**02 — extract full text and annotations.** Annotations: fetched from a
temporary copy of `zotero.sqlite` (Zotero locks the live one) for every
paper that has them, regardless of tier. Full text: pypdf text with page
markers, references stripped, for key papers (`--tier fulltext`, default)
or every PDF (`--tier all`). Each file's hash, page count and status go in
the manifest; existing files are skipped unless `--force`. Scanned PDFs are
flagged `needs_ocr`, not OCR'd.

**03 — scan reading notes.** Read-only walk of the main vault's
literature-notes folders: citekey (from frontmatter) → note path and an
`obsidian://` link. With no main vault configured it writes an empty map.

**04 — build vault.** Pure rendering from `data/` into the vault. Rewrites
`Papers/`, `Tags/`, `INDEX.md` and `Home.md` each run, removing notes for
papers or tags that no longer exist. Never touches `Fulltext/` (owned by
02) or `Code/`.

**05 — papers.json.** Parses the `Papers/` notes into one JSON file.
Stdlib only.

**06 — semantic index (optional).** See *Semantic index* below.

Shared code in `scripts/lib/`: `bbt.py` (export parsing), `zotero_db.py`
(annotations), `store.py` (paths, change-aware writes), `config.py`,
`rawmd.py` (frontmatter and the full-text format), `schema.py` (the corpus
record), `logutil.py`, `semantic.py` (step 06 and `semantic_search.py`),
`matching.py` (DOI/title normalisation and the library holdings set),
`openalex.py` (OpenAlex client for `citation_gaps.py`).
Third-party dependencies: `pypdf` (pinned) and `numpy` (semantic index).

## Semantic index

An optional embedding index over `Fulltext/`, so search can retrieve
*pages* that are about a topic even when they use different words, and walk
from one page to related pages in other papers. It is **retrieval-only and
outside the vault**, so design decision 3 stands:

1. **Derived, gitignored, regenerable.** `Code/data/semantic_index.sqlite`
   and `semantic_manifest.json`; delete and rebuild any time.
2. **Never evidence.** A hit locates a page; claims still cite the
   `Fulltext/` text at that page. Previews are never quoted.
3. **The vault build (01–05) never reads it.** A machine without Ollama
   builds a byte-identical vault. Embeddings are not bit-reproducible
   across models or hardware, which is why they live outside the vault.

**Chunk = page.** Page markers map 1:1 onto `zotero://open-pdf/…?page=N`,
so every hit is a clickable location. Pages over `max_words` split at
paragraph boundaries into parts (same page number); pages under
`min_words` merge into the next page's chunk. The chunker is versioned.

**Infrastructure.** Ollama at `[semantic] ollama_url`, model
`embed_model` (default `nomic-embed-text`, 768-dim, runs on CPU). The
capability check (Ollama reachable, model pulled, numpy importable) writes
`status: ok | no_ollama | no_model | no_numpy | stale | no_index` to the
manifest; anything but `ok` makes step 06 and the search stand down with
one line and exit 0. The model is only pulled by `06 --setup`.

**Storage.** SQLite with float32 vectors as blobs; search is brute-force
cosine similarity in numpy (~25k chunks for a 1,000-paper library is well
under 100 ms). No vector database.

**Incremental.** Keyed on each paper's full-text hash from
`fulltext_manifest.json`; a new or re-extracted paper is re-embedded, a
removed one pruned. A change of model, model digest or chunk settings
marks the index `stale` and needs `--force` to rebuild.

**Search** (`semantic_search.py`, JSON Lines out): `--query "…"` embeds
the question; `--like CITEKEY:PAGE` uses that page's own vector, excluding
its paper (works with Ollama down). `k`, `min_score` and `per_paper`
(cap per paper, so one long paper can't flood the list) come from config.

## Citation gaps

An optional, network-backed follow-up to a search report
(`citation_gaps.py`): given the cluster of papers a survey cites, the
OpenAlex citation graph (free, keyless; `[openalex] mailto` joins the
faster polite pool) yields two lists of papers **missing from the Zotero
library**:

- **backward** — works the cluster references, ranked by how many cluster
  papers cite them (a gap in the retrospective literature);
- **forward** — works citing at least `min_cluster_cites` cluster papers,
  ranked the same way (follow-on work the library hasn't caught up with).

Membership is decided deterministically against the full BBT export:
normalised-DOI equality, with a normalised-title fallback only for items
without a DOI (`lib/matching.py`). No LLM; ties break on global citation
count, then OpenAlex ID. Both lists cap at `max_per_direction`.

The result is written into the survey's own archived `review.json` as an
optional top-level `"gaps"` block (candidates carry their metadata inline —
they have no citekey, that being the point — plus an OpenAlex abstract),
and the page is re-rendered in place: one extra "Beyond your library"
section, cards linking to the DOI and OpenAlex rather than Zotero. Deleting
the block and re-rendering removes the section.

Like the semantic index, it is derived and optional: work metadata is
cached with a TTL in the gitignored `Code/data/openalex_cache.json`
(citation lists are always fetched fresh), and without the network the
script stands down with one line and exit 0. The vault build (01–05) never
touches it. A typical 15-paper cluster costs ~25–35 API calls (~10 s).

## Key design decisions

1. **Citekey is the universal identifier**, and the join key between vaults.
2. **The Better BibTeX JSON export is the source of truth** for metadata.
3. **Deterministic build.** No LLM calls between export and vault, so
   nothing is invented, nothing goes stale, and a run costs nothing. Any
   synthesis is done inside a conversation, aimed at the question asked.
4. **Tags, not clusters.** Your own tags carry your judgement and are stable
   between runs. Untagged papers stay visible in `INDEX.md` and reachable by
   search.
5. **Byte-identical output for identical input:** no dates or run IDs in
   generated vault files (they go in `logs/` and the manifest); stable sort
   orders; `pypdf` pinned to an exact version, recorded in each full-text
   file; files only rewritten when their content changed; LF line endings
   (`.gitattributes`).
6. **Evidence depth is explicit.** Every paper note says what the agent can
   rely on (`has_abstract`, `has_annotations`, `fulltext_status`), and the
   search skill labels every claim by source.
7. **Minimal PDF cleaning** — de-hyphenate, normalise whitespace, cut
   references, nothing cleverer.
8. **Conversation owns decisions, scripts own checks.** Skills decide and
   explain; `doctor.py` verifies, deterministically.

## Config

`Code/config.toml`, copied from `config.example.toml` (every key documented
there): `[zotero]` data directory, export path, key tag and collection;
optional `[main_vault]`; `[tags]`; `[highlights]` colour → heading.

## Git and data

Committed: `Home.md`, `INDEX.md`, `Papers/`, `Tags/`, `papers.json`,
`Summaries/`, `lit-vault.html`, `Code/` (minus data and the export).

Gitignored by default: `Fulltext/` (extracted publisher text — fully
regenerable), `Code/Export_data/` (absolute local paths), `Code/data/`,
`logs/`. PDFs are never committed; Zotero holds them.

---
name: lit-vault-search
description: "Search the user's Zotero library through their lit-vault (a literature vault generated from it): find papers on a topic, say what they show, cite with one-click Zotero links, and finish by rendering the local report page (lit-vault.html at the vault root, past reports archived in Summaries/) so the links are clickable. Use for 'what do I have on X', 'check my library/Zotero', 'which of my papers…'."
---

# Literature vault search

<!-- stamp:about -->
This is the in-repo copy of the skill. The vault is the repository you are
running in, and "the user" is whoever owns it. The lit-vault setup skill
generates a personalised global copy of this file from it.
<!-- /stamp:about -->

Answer questions from the user's own Zotero library, through the literature
vault generated from it (format defined in `Code/PIPELINE_PLAN.md`). Work
the way a researcher would — titles, then abstracts, then full text — but
use search where a human would have to skim.

**Stance.** The library is a curated candidate pool: the user saved these
papers because they looked relevant at the time. It is the first place to
look and is preferred over the open web for anything they will cite. It is
not vetted truth, and saving a paper is not endorsing it. Their highlights
and their own reading notes carry their judgement; weight them accordingly.

**Read-only.** This skill never writes to the user's notes or to the
generated vault files. The one exception is §6: rendering `lit-vault.html`
and its `Summaries/` archive at the vault root, through the renderer. If the
user wants a synthesis kept, that is a separate request — never put it in
`Papers/`, `Tags/` or `Fulltext/`, which every rebuild rewrites.

**Division of labour.** You supply judgment: query expansion, narrowing by
abstract, grouping, one-line statements of what each paper shows. Scripts
own all mechanics: `papers.json` (built by the pipeline) is the parsed
library, and `Code/scripts/render_report.py` renders the report page.
Never re-parse `Papers/` frontmatter by hand and never hand-write the
report HTML while those files exist — one-off parsers and renderers are
where runs lose time and introduce formatting bugs.

## 1. Locate the vault

**Literature vault** — a folder whose root contains `INDEX.md`, `Papers/`
and `Code/PIPELINE_PLAN.md`. Try these routes in order and use the first
that works:

<!-- stamp:routes -->
1. **The folder you are running in**, or a folder connected to the session,
   with those markers.
2. **None** — stop and say what is needed: run the session in the vault
   folder, or connect it.
<!-- /stamp:routes -->

If the vault exists but `INDEX.md` does not, it has not been built yet. As
a stopgap, search titles and abstracts in the Better BibTeX export (the
`export` path in `Code/config.toml` — a file, or a folder, by default
`Code/Export_data/`, holding the single `.json` export)
with a short Python script, label every result "export only, pre-build",
and suggest running the lit-vault-setup skill.

**Reading notes** (optional) — if `Code/config.toml` has a `[main_vault]`
`path`, the user keeps reading notes in that Obsidian vault; any note there
with `citekey:` in its frontmatter is their note on that paper. If it is not
configured, skip everything about reading notes without comment. If it is
configured but not reachable from this session, carry on and say their
notes were not checked.

## 2. What the vault contains

Check one file of each kind on first use; the build can lag this
description, so adapt to what is actually there.

- **`papers.json`** (vault root) — the machine view of the whole library:
  one object per paper with `citekey`, `title`, `authors`,
  `display_author` (preformatted "Gust et al."), `year`, `tags`,
  `key_paper`, `has_abstract`, `has_my_note`, `my_note` (link),
  `fulltext_status`, `has_annotations`, `highlight_sections` (the ###
  headings in the note), `zotero_item`, `zotero_pdf`, `doi`, and the full
  `abstract` text. **This is the primary search surface** — filter it with
  a short Python script. If it is missing (older build), fall back to the
  markdown files below and say so.
- **`INDEX.md`** — every paper, one line each, newest first:
  `- [[@citekey]] 2024 — Title · #tag-a #tag-b · ✎ ◆ ▤`.
  Markers: `✎` the user has a reading note, `◆` key paper, `▤` full text
  available. The header gives counts. **Do not read it whole** — for a large
  library it does not fit in context; take counts from the header and
  content from `papers.json`.
- **`Tags/{tag}.md`** — one per Zotero tag: related (co-occurring) tags,
  then member papers in the same line format. No summaries. Only tagged
  papers appear here.
- **`Papers/@{citekey}.md`** — frontmatter, then `## Abstract` **on a single
  line**, then `## My highlights`, then links. Open these to read a paper's
  highlights in full; the metadata is already in `papers.json`.
- **Highlight headings** follow the user's colour convention, set in the
  `[highlights]` section of `Code/config.toml` (colour → heading; read it
  once). The default is 💡 main ideas, ❔ questions, ⭐ important,
  🧩 definitions, ⛔ weaknesses and caveats. A heading starting with ⛔
  holds the user's own flagged doubts about a paper — always surface them
  when using that paper. `highlight_sections` in `papers.json` says which
  headings a note has, so only open notes that have a ⛔ or ⭐ heading.
- **`Fulltext/{citekey}.md`** — plain text with `<!-- page N -->` before
  each page. N is the physical page index (what Zotero's `?page=` uses),
  not the printed page number. References are stripped. Usually only key
  papers have full text.
- **Semantic index** (optional, not part of the vault) — an embedding
  index over `Fulltext/` pages in `Code/data/`. Read
  `Code/data/semantic_manifest.json` once: `"status": "ok"` and a shell on
  the computer that holds the vault means it is **on**. Anything else —
  file missing, another status, or no local shell (a GitHub clone, a cloud
  session) — means **off**: carry on with the keyword funnel and say
  "semantic index: off (<status or reason>)" in the trail. Never treat it
  as an error, never retry, never try to build it.

## 3. The funnel

**Stage 0 — expand the query.** Before searching, write down the terms the
question could appear under: synonyms, abbreviations, British/American
spellings, adjacent concepts (e.g. demand response / DSR / demand-side
response / load shifting / flexibility). Keyword search finds only the words
used, so this list decides recall. Include it in the search trail.

**Stage 1+2 — find and narrow candidates.** One Python script over
`papers.json`: take the union of (a) papers whose title **or abstract**
matches any expanded term, case-insensitive, and (b) members of clearly
relevant tags (check the matching `Tags/` notes for related tags). Print
compact lines — citekey, year, markers, title, first ~150 chars of
abstract — and judge relevance from those. Tag notes are a shortcut, never
the boundary: untagged papers are reached only through the term search.
If the semantic index is on, also run, from `Code/`,
`uv run python scripts/semantic_search.py --query "<the user's question>"`
— the question as asked, not each expanded term; the point is to stop
depending on the expansion. Each output line is a hit (`citekey`, `page`,
`score`, `preview`, `zotero_pdf_page`); a single `{"status": …}` line means
it stood down — treat the index as off. Add the hits' papers to the
union. Record the candidate count, and where each candidate came from:
`keyword`, `tag`, `semantic`, or a combination. Papers with an empty `abstract` cannot be
judged here: list them as "unchecked" rather than silently dropping or
keeping them. Weight papers with `✎` or highlights first, then key papers
(`◆`). Open the `Papers/` notes of kept papers that have ⛔ or ⭐
highlight sections, and surface ⛔ doubts. (Fallback without `papers.json`:
scan titles with grep on `INDEX.md`, then grep `-i "term1|term2"` over
`Papers/` — each hit on the abstract line returns the full abstract.)

**Stage 3 — go deep, only if the task needs it.**
- **Full text:** for long documents, search inside the `Fulltext/` file for
  the expanded terms rather than reading it all; the nearest
  `<!-- page N -->` above a match gives the page. A semantic hit already
  names its page: open the file there.
- **Related sections** (semantic index on):
  `uv run python scripts/semantic_search.py --like CITEKEY:PAGE` lists
  pages in *other* papers nearest to that page — use it to walk from one
  relevant section to related ones.
- **Reading notes** (if configured): for every kept paper marked `✎`, read
  the user's note (search the reading-notes vault for `citekey: <citekey>`).

**How deep to go is set by the task:**

| Task | Stop after |
|---|---|
| "What do I have on X?" / reading list | Stage 2 — titles, years, one line each |
| "Which papers argue / use / measure X?" | Stage 2, with stage 3 for borderline cases |
| "What did paper Y find?" / exact figures, methods, quotes | Stage 3 |
| Synthesis or literature-review drafting | Stage 3 for the core papers, stage 2 for the rest |

## 4. Citing and evidence labels

Use only keys and URIs read from the notes or `papers.json`; never construct
or guess one.

- **In chat:** `Author (Year)` `citekey` — [Zotero](zotero://select/library/items/KEY).
  For a claim from the full text, add a page link: take the paper's
  `zotero_pdf` URI and append `?page=N`, e.g.
  [p. 12](zotero://open-pdf/library/items/ATTKEY?page=12).
  Highlights already carry their own page-and-annotation link — reuse it
  verbatim. Write each URI out in full as well as linked, so the user can
  copy it if the chat doesn't render `zotero://` links as clickable. Chat
  interfaces usually strip them, which is why §6 delivers the clickable
  version.
- **In an Obsidian note:** `[[@citekey]]` plus the same Zotero links.
- **Never in a published artifact:** the claude.ai artifact sandbox blocks
  `zotero://` navigation and clicking such a link blanks the page. The
  clickable surface is the local page in §6.

Label every substantive claim with where it comes from:
`[abstract]`, `[full text p.N]`, `[my highlight p.N]` (the user's
annotation), or `[my note]` (their reading note). Never present an
abstract-level claim as a detailed finding. `INDEX.md`, tag notes and
semantic hits are maps, never evidence: a semantic hit is a lead to open
`Fulltext/` at that page and cite the text there, and its `preview` is never
quoted.

## 5. Show the search trail

End every answer with a short trail, so the user can tell "the library has
nothing" from "the search missed it":

```
Search trail
- Vault: <route used>, built/committed <date>
- Terms: <expanded list>
- Tags used: <tags>
- Semantic index: on | off (<reason>)
- Candidates: N (keyword a, tag b, semantic c; overlaps counted once) → kept M after abstracts
- Dropped: <one line on what was dropped and why>
- Unchecked (no abstract): <citekeys, or none>
- Read in full: <citekeys>; reading notes read: <citekeys, or "not configured">
```

## 6. Deliver the clickable report page

Chat strips `zotero://` links, so every multi-paper answer (a reading list,
overview, or synthesis — not a one-line factual answer with a single
citation) finishes by rendering a report into the vault:

- **`lit-vault.html`** at the vault root — the latest report plus a dropdown
  of every earlier one. Bookmark it; opened locally, it hands `zotero://`
  links to the OS, so they open Zotero directly, and it reloads itself when
  its tab regains focus.
- **`Summaries/YYYY-MM-DD--{type}--{slug}.json` + `.html`** — the archive
  pair for this run (the review.json verbatim and the page frozen as
  rendered). Never edit or delete these; history is never rewritten.

**Do not hand-write the HTML.** Write a `review.json` and render it:

1. Write `review.json` (in a scratch location, not the vault):
   `"schema": 1`, `"type": "survey"`, `topic`, `lede` (one or two
   sentences; the link-usage boilerplate is appended automatically), and
   `groups` — each with a `name`, `cards` for the anchor papers
   (`citekey`, `statement` — your one-to-two-line judgment of what it
   shows — `source` label such as `abstract` / `title` / `full text p.N` /
   `my highlight p.N`, optional `mine` for a ⛔ doubt or annotation worth
   surfacing, optional `pdf_page` + `pdf_label` to deep-link the PDF
   button), and an `also` list of citekeys for the rest. Full schema in the
   renderer's docstring. `survey` is the only type for now; any other
   `type` or `schema` fails by design.

   ```json
   {"schema": 1, "type": "survey", "topic": "...", "lede": "...",
    "groups": [{"name": "...", "cards": [{"citekey": "...", "statement": "...",
                "source": "abstract"}], "also": ["..."]}]}
   ```
2. Run `python Code/scripts/render_report.py review.json` from the vault
   root (stdlib only — no uv needed; `--papers` if papers.json is
   elsewhere, `-o` to move the stable page — the archive always goes in
   `Summaries/` beside it). It joins the review with `papers.json`, applies
   the frozen template `Code/templates/survey.template.html`, handles all
   escaping, author lines, chips, No-abstract flags and Zotero links,
   writes the archive pair and rewrites `lit-vault.html`. It **fails with a
   message** on an unknown type/schema, unknown citekeys or duplicates —
   fix the review.json and re-run rather than patching output.
3. Where to run it: on the user's machine, in their local vault, whenever a
   shell there is available — that writes everything in place. If you can
   only render elsewhere (e.g. a GitHub clone), render there and copy
   `lit-vault.html` to the vault root and the new archive pair into the
   vault's `Summaries/`, keeping the filenames. If the vault folder is not
   yet connected, request access to it (one request, this folder only).

Only if the renderer or template is missing (older clone) fall back to
hand-building one self-contained HTML file matching `lit-vault.html`'s
conventions: read the existing `lit-vault.html` first and keep its head,
styles and structure. Say in chat that this run was hand-built and not
archived.

After a successful render, offer (one line) to run
`uv run python scripts/citation_gaps.py` from `Code/` — it asks OpenAlex
which papers this review's cluster references or is cited by that are
missing from the Zotero library, and appends them to the same report page
as a "Beyond your library" section (~10–30 s, network required; it stands
down cleanly offline). Run it only on a yes.

The chat answer with its search trail must stand on its own; the page is
the clickable companion, not a replacement. End the chat answer with one
line saying `lit-vault.html` was updated (and the archive name). If the
computer is unreachable or access is declined, say so in one line and skip
the page — do not fall back to a published artifact for the links.

## 7. When the library has nothing

Say so plainly, with the search trail. Only then offer to search the web,
and keep anything found there clearly separate and labelled as outside the
library.

## 8. When something looks broken

Run `python Code/scripts/doctor.py` from the vault root before guessing. It
checks config, export, build and report machinery and prints a one-line fix
for each failure. If the vault is merely stale, the lit-vault-refresh skill
rebuilds it.

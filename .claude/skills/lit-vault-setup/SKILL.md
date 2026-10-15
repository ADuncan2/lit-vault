---
name: lit-vault-setup
description: "Set up this lit-vault: check prerequisites, guide the Better BibTeX auto-export, write Code/config.toml, build the vault, verify it with the doctor, and — only with explicit consent — install the global lit-vault-search and lit-vault-refresh skills. Use when the user has just cloned the repo, says 'set this up', 'configure lit-vault', or wants to re-run setup to change config or regenerate the global skills."
---

# Set up the literature vault

Walk the user through setup one step at a time, in order. The conversation
owns decisions; scripts own checks — whenever a step can be verified by
`Code/scripts/doctor.py`, run it rather than eyeballing. Run everything from
the repo root unless a step says `Code/`. Keep each message short: what this
step does, what you need from the user, then do it.

**Re-running setup** is supported: if `Code/config.toml` exists, show its
current values and ask which to change instead of re-interviewing; if the
vault is already built, skip to step 5.

## 1. Prerequisites

Run `python Code/scripts/doctor.py --prereqs` (use `python3` or
`uv run --no-project python` if `python` is not found). It checks Python
3.11+, `uv`, Zotero desktop running and Better BibTeX installed. Anything
missing → give the README link for it, ask the user to install it, and
stop until they say it's done. Do not install software for them.

## 2. Better BibTeX auto-export (the user does this)

You cannot click Zotero's dialogs. Walk the user through
`docs/setup-bbt.md` — the whole library or one collection, format
**Better BibTeX JSON**, **Keep updated** ticked, saved as the only `.json`
file in `Code/Export_data/` (any name), or at a path they prefer — note it
for step 3. Then check the file exists and parses:
`python -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print(len(d['items']), 'items')" <path>`.

## 3. Config interview

Copy `Code/config.example.toml` to `Code/config.toml` (never overwrite an
existing one without asking), then fill it in by asking, one or two
questions at a time:

- **Zotero data directory** — first look for `zotero.sqlite` in the usual
  place (`~/Zotero` on every OS) and propose it; otherwise ask them to read
  it from Zotero → Settings → Advanced → Files and Folders.
- **Export path** — leave the default (the `Export_data` folder) if the
  export is in `Code/Export_data/`; otherwise the file's full path.
- **Key papers** — a tag or a collection that marks the papers worth full
  text (either or both; "" disables). The example values (`key-paper`,
  `Key papers`) are placeholders, not Zotero conventions: ask what they
  actually use, and if they have no such marker, say so plainly — step 4
  then offers full text for every PDF instead. They can change it later.
- **Reading notes vault** (optional) — do they keep reading notes in an
  Obsidian vault, one note per paper with `citekey:` in the frontmatter?
  If yes, its path and Obsidian name. If no, leave `path = ""`; everything
  still works.
- **Highlight colours** — ask whether their highlight colours mean
  anything. Most people's don't: then leave the default colour-name
  headings. If some do, rename those headings in `[highlights]` to the
  meaning (e.g. "Methods", "Doubts and caveats") — the search skill reads
  them, and a heading that names their criticisms gets surfaced.
- **OpenAlex email** (optional, one line) — `[openalex] mailto` joins
  OpenAlex's faster "polite pool" for the citation-gaps feature
  (`citation_gaps.py`, run after search reports). Any email works; ""
  works too, just slower. Never fill it in without asking.

Then run `python Code/scripts/doctor.py` and fix any Config FAIL before
moving on (Build FAILs are expected at this point).

## 4. Build

From `Code/`: `uv sync`, then in order, stopping at the first failure:

```
uv run python scripts/01_build_corpus.py --tags-report
uv run python scripts/02_extract_fulltext.py --measure
uv run python scripts/02_extract_fulltext.py
uv run python scripts/03_scan_my_notes.py
uv run python scripts/04_build_vault.py
uv run python scripts/05_build_papers_json.py
```

After `--measure`, tell the user the number of key papers and projected
full-text size before extracting; `--tier all` (every PDF) is their call,
not the default. With zero key papers, the default extracts nothing — offer
`--tier all` (with its projected size) or setting a key tag/collection.

Report the step-01 summary in one or two lines, then check the tags — the
vault's `Tags/` notes assume manual tags are topics the user chose. From
the `--tags-report` list, point out anything that looks otherwise: no tags
at all (fine — search falls back to titles and abstracts), many tags on a
single paper (often keywords an import saved as manual tags), or workflow
tags such as `to-read`, `_inbox`, `#unread`. Offer to add those to
`[tags] ignore` (wildcards allowed) and re-run 01 before going on.

### Optional: semantic index

Offer once: "Also build a semantic index, so searches can find pages by
meaning and not only by the words used? It needs Ollama (a free local
model runner) and a ~270 MB model; it runs on your computer, and takes
roughly a second per page on a CPU." Skip if they decline. If yes and
Ollama isn't installed, give them https://ollama.com and wait. Then, from
`Code/`:

```
uv run python scripts/06_build_semantic_index.py --measure
uv run python scripts/06_build_semantic_index.py --setup
```

Tell them the chunk count from `--measure` before building (about one
second per chunk). The index lives in `Code/data/` and is never
committed. Without it everything works; with it, the search skill uses it
automatically.

## 5. Verify

Run `python Code/scripts/doctor.py`. Fix every FAIL using its hint and
re-run until it passes; mention WARNs. Then ask the user to open one
`Papers/@….md` note that has a PDF and click its Zotero link (in Obsidian,
or any markdown viewer) to confirm `zotero://` links resolve on their
machine. Optionally render a first report with the lit-vault-search skill
so they can bookmark `lit-vault.html`.

Data-policy note, one sentence: the `.gitignore` keeps `Fulltext/`
(publisher text) and `Code/Export_data/` (local file paths) out of git by
default; `Summaries/` is committed unless they uncomment its line.

## 6. Global skills — the consent moment

Ask explicitly, as its own question: **"Install `lit-vault-search` and
`lit-vault-refresh` into `~/.claude/skills/` so any Claude session on this
computer can search this library?"** Explain that they are generated
copies, personalised with their name and this vault's path, and that
deleting the folders uninstalls them. On no, stop here (skip step 7's
re-stamp too).

On yes, ask what name the skills should use for them, and whether they use
the Claude desktop app or claude.ai with this computer linked. Then run:

```
python Code/scripts/install_skills.py --user-name "<name>" [--linked-computer] [--repo OWNER/NAME]
```

(`--dry-run` first if they want to read it.) An existing install generated
by setup is regenerated in place; if the script refuses because a
same-named skill exists that it did not generate, show the user that file
and ask before passing `--force`.

## 7. Optional: a private GitHub repo

Offer once, never as a requirement: "Push this vault to a **private**
GitHub repo so cloud and claude.ai sessions can reach it? That puts your
library's abstracts and your highlights on GitHub, privately." If yes and
`gh` is available and logged in: `git init` if needed, commit, then
`gh repo create <name> --private --source . --push` — confirm the name and
that it is private before running it. Then, if the global skills were
installed, re-run `install_skills.py` with the same flags plus
`--repo OWNER/NAME` so the search skill gains the GitHub route.

## Finish

Summarise in a few lines: papers built, doctor status, which skills were
installed where, whether a repo was created, and how to refresh later
("ask Claude to refresh the lit vault"). Mention `README.md` for uninstall.

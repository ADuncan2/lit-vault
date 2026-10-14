# lit-vault

Turn your Zotero library into a **literature vault** an AI agent can search,
read and cite from — and ask Claude "what do I have on X?" from any chat.

The vault is plain markdown plus one `papers.json`: an index of every
paper, one note per tag, one note per paper (metadata, the abstract on one
line, your Zotero highlights with page links), and page-marked full text
for the papers that matter. The build is deterministic — no LLM calls, the
same library always gives byte-identical files — and every claim the agent
makes links back to Zotero in one click. Answers come with a local report
page (`lit-vault.html`) whose links open Zotero at the right item and page.

[Obsidian](https://obsidian.md) is **not required**. It is a good free way to
browse the vault, and if you keep reading notes in an Obsidian vault the
pipeline can link papers to them — but the agent reads the files directly.

> **Built for one researcher's workflow, shared as-is.** It works for me; it
> may need adapting for you. There is no support promise — issues and PRs
> are welcome but may sit.

## Prerequisites

All four, no substitutes in this version:

- **[Zotero 7](https://www.zotero.org/download/)** desktop
- **[Better BibTeX](https://retorque.re/zotero-better-bibtex/installation/)** for Zotero
- **[Claude Code](https://docs.claude.com/en/docs/claude-code/overview)** (CLI, desktop or IDE) — the setup is a conversation
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — it installs the right Python for you

## Quickstart

1. Click **Use this template** on GitHub (make the new repo **private**), or download it.
2. Clone it somewhere permanent, e.g. `~/Documents/lit-vault`.
3. Open that folder in Claude Code.
4. Say **"set up lit-vault"**.
5. Follow the conversation: one export step in Zotero, a few questions, then it builds and checks everything.

At the end, setup asks whether to install two skills globally so any Claude
session on your computer can search the library; nothing is installed
without that yes. Day to day, ask Claude things like "what do I have on
demand response?" or "refresh my lit vault".

## What's in the repo

| Path | What |
|---|---|
| `.claude/skills/` | `lit-vault-setup`, `lit-vault-search`, `lit-vault-refresh` |
| `Code/` | the pipeline (`scripts/`), its config, the report template — see `Code/README.md` |
| `Code/PIPELINE_PLAN.md` | the vault format spec |
| `docs/` | the Better BibTeX export walkthrough, the roadmap |
| `Papers/`, `Tags/`, `Summaries/` | empty until you build |

## Your data and git

The `.gitignore` is safe by default: `Fulltext/` (publisher text extracted
from your PDFs) and `Code/Export_data/` (paths from your computer) are never
committed. Paper notes, tags, `papers.json` and your report history are.
Keep your copy of the repo **private** — it holds your library's abstracts
and your highlights. Setup can create a private GitHub repo for you so
cloud and claude.ai sessions can reach the vault; it is optional.

## Updating

Pull script updates from the template without touching your library:

```sh
git remote add template https://github.com/OWNER/lit-vault.git   # once
git pull template main
```

## Uninstall

- Global skills: delete `~/.claude/skills/lit-vault-search/` and
  `~/.claude/skills/lit-vault-refresh/`. (Each file says so too.)
- Everything else: delete the folder. Zotero is never written to.

## Licence

MIT — see `LICENSE`.

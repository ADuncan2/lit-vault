# Roadmap

Not promised — this is a personal tool shared as-is — but these are the
directions already designed or under test.

- **Semantic index over full text** *(under test, not yet in the
  template).* An optional local embedding index (one chunk per `Fulltext/`
  page, via Ollama) plus a `semantic_search.py` the search skill can use to
  find passages keyword search misses. Hits are leads to open in
  `Fulltext/`, never evidence in themselves. It will land here once it has
  been evaluated against a gold set; until then the template is
  keyword-and-tags only.
- **More report types.** `deep-dive` (one topic, full-text depth) and
  `paper` (one paper) beside `survey`; the renderer is already typed for
  them.
- **Per-paper LLM extraction** — concepts, claims and methods pulled from
  a paper's full text into structured JSON. Removed until something (a
  report type, the search skill) actually consumes the output.
- **An `AGENTS.md`** so non-Claude agents can use the vault.
- **Import without Better BibTeX.**

Out of scope for v1: two vaults on one machine, and any update
notification beyond `git pull template main`.

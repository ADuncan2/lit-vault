# Roadmap

Not promised — this is a personal tool shared as-is — but these are the
directions already designed or under test.

- **Calibrated semantic defaults.** `[semantic] min_score` is a
  placeholder: nomic scores bunch around 0.70–0.80, so 0.45 filters
  nothing. A shipped evaluation script and better-founded defaults would
  follow from a few people's gold sets.
- **Semantic index over abstracts** for papers with no PDF, so hits are
  not limited to the full-text tier.
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

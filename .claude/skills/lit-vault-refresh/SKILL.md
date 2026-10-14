---
name: lit-vault-refresh
description: "Rebuild the user's lit-vault (literature vault) from their latest Zotero export and report what changed: re-run the pipeline, run the doctor, report '+N papers, M updated, K removed'. Use for 'refresh my library', 'update the lit vault', 'I added papers to Zotero', 'rebuild the vault'."
---

# Refresh the literature vault

<!-- stamp:about -->
This is the in-repo copy of the skill: the vault is the repository you are
running in. The lit-vault setup skill generates a global copy from it.
<!-- /stamp:about -->

Needs a shell on the computer that holds the vault and Zotero; a cloud
clone cannot rebuild (it has no Zotero). Run from the vault root:

1. Snapshot: copy `papers.json` to a scratch location (skip if absent —
   first build).
2. Rebuild, from `Code/`, stopping at the first failure:
   `uv run python scripts/01_build_corpus.py`, then
   `02_extract_fulltext.py`, `03_scan_my_notes.py`, `04_build_vault.py`,
   `05_build_papers_json.py` (same `uv run python scripts/…` form).
   Step 02 only extracts PDFs it has not seen; it also refreshes highlights.
3. From the vault root: `python Code/scripts/doctor.py --compare <snapshot>`.
4. Report in two or three lines: the delta line from the doctor
   (`+N papers, M updated, K removed`), any FAIL or WARN with its fix, and —
   if the vault is a git repo — offer to commit (never commit unasked).

If the doctor warns the export is stale, the Better BibTeX auto-export is probably off:
point the user at `docs/setup-bbt.md`. Deleting this skill's folder
uninstalls it.

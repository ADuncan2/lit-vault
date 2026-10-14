"""
05_build_papers_json.py — one machine-readable file for the whole library.

Parses every committed Papers/@{citekey}.md note and writes papers.json at
the vault root: one object per paper with the frontmatter fields an agent
needs (title, authors, year, tags, markers, Zotero URIs), a precomputed
`display_author` ("Gust et al."), the abstract text, and the names of any
highlight sections. Agents and the summary renderer consume this file
instead of re-parsing 1,000+ markdown notes per session — parsing happens
once, here, so author-format bugs can't recur downstream.

Like the rest of the build it is deterministic and fully rewritten on every
run. Stdlib only, so it also runs outside the uv project on a bare clone.

Usage:
    uv run python scripts/05_build_papers_json.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
ABSTRACT_RE = re.compile(r"^## Abstract\s*\n+(.+?)(?=\n## |\Z)", re.M | re.S)
HIGHLIGHT_HEAD_RE = re.compile(r"^### (.+)$", re.M)

# Frontmatter fields copied through verbatim (json-decoded).
FIELDS = [
    "citekey", "title", "authors", "year", "item_type", "doi", "tags",
    "key_paper", "has_abstract", "has_pdf", "has_fulltext",
    "fulltext_status", "has_annotations", "has_my_note", "my_note",
    "zotero_item", "zotero_pdf",
]

NO_ABSTRACT_SENTINEL = "*No abstract in Zotero.*"


def parse_frontmatter(text: str) -> dict:
    """Parse the note's YAML frontmatter.

    The build writes one `key: value` per line with JSON-compatible values
    (quoted strings, JSON lists, bare ints/bools), so each value can be
    json-decoded directly; a bare unquoted scalar falls back to the string.
    """
    m = FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if not raw:
            out[key.strip()] = None
            continue
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


def display_author(authors) -> str:
    """'Surname', 'Surname & Surname', or 'Surname et al.'"""
    if not authors:
        return ""
    surnames = [a.split(",")[0].strip() for a in authors if a and a.strip()]
    surnames = [s for s in surnames if s]
    if not surnames:
        return ""
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} & {surnames[1]}"
    return f"{surnames[0]} et al."


def parse_note(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if not fm.get("citekey"):
        return None
    rec = {k: fm.get(k) for k in FIELDS if k in fm}
    rec["display_author"] = display_author(fm.get("authors"))

    m = ABSTRACT_RE.search(text)
    abstract = m.group(1).strip() if m else ""
    if abstract == NO_ABSTRACT_SENTINEL:
        abstract = ""
    rec["abstract"] = re.sub(r"\s+", " ", abstract)

    rec["highlight_sections"] = HIGHLIGHT_HEAD_RE.findall(text)
    return rec


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    vault = Path(__file__).resolve().parents[2]

    papers_dir = vault / "Papers"
    if not papers_dir.is_dir():
        print(f"error: {papers_dir} not found", file=sys.stderr)
        return 1

    records = []
    skipped = 0
    for p in sorted(papers_dir.glob("@*.md")):
        rec = parse_note(p)
        if rec is None:
            skipped += 1
            continue
        records.append(rec)
    records.sort(key=lambda r: r["citekey"].lower())

    out_path = vault / "papers.json"
    payload = {
        "generated": True,
        "schema": 1,
        "papers": records,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=0) + "\n",
        encoding="utf-8", newline="\n",
    )

    n_abs = sum(1 for r in records if r.get("abstract"))
    n_key = sum(1 for r in records if r.get("key_paper"))
    n_note = sum(1 for r in records if r.get("has_my_note"))
    print(f"papers.json: {len(records)} papers "
          f"({n_abs} with abstracts, {n_key} key, {n_note} with my notes; "
          f"{skipped} notes skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

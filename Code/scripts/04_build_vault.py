"""
04_build_vault.py — render the literature vault from Code/data/.

Pure, deterministic templating — no LLM, no dates, stable sort orders, and
files are only rewritten when their content changed. Reads corpus.jsonl,
annotations.jsonl, my_notes.json and fulltext_manifest.json, and writes:

  Home.md               landing page: counts, tag list, how to use
  INDEX.md              level 1: one line per paper, whole library
  Tags/{tag-slug}.md    level 1: one note per manual Zotero tag
  Papers/@{citekey}.md  level 2: metadata, abstract, my highlights, links

Notes for papers or tags that no longer exist are removed. Fulltext/ (owned
by step 02) and Code/ are never touched.

Usage:
    uv run python scripts/04_build_vault.py
"""

import argparse
import re
from collections import Counter
from pathlib import Path

from lib.config import load_config
from lib.logutil import setup_logging
from lib.rawmd import frontmatter
from lib.store import (
    ANNOTATIONS_PATH,
    CORPUS_PATH,
    FULLTEXT_DIR,
    LOG_DIR,
    MANIFEST_PATH,
    MY_NOTES_PATH,
    ROOT,
    read_json,
    read_jsonl,
    write_if_changed,
)

MARK_NOTE, MARK_KEY, MARK_FULLTEXT = "✎", "◆", "▤"


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "tag"


def tag_slugs(tags: set[str]) -> dict[str, str]:
    """Tag -> unique slug; collisions get -2, -3… in a stable order."""
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for tag in sorted(tags, key=lambda t: (t.lower(), t)):
        base = slug = slugify(tag)
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        slugs[tag] = slug
    return slugs


def sort_key(r: dict):
    """Newest first, undated last, then citekey."""
    year = r.get("year", "")
    return (0 if year.isdigit() else 1, -int(year) if year.isdigit() else 0,
            r["citekey"].lower(), r["citekey"])


def fulltext_status(r: dict, manifest: dict) -> str:
    entry = manifest.get(r["citekey"], {})
    if (FULLTEXT_DIR / f"{r['citekey']}.md").exists() and entry.get("status", "ok") == "ok":
        return "ok"
    if entry.get("status") == "needs_ocr":
        return "needs_ocr"
    if not r["has_pdf"]:
        return "no_pdf"
    return "not_extracted"


def paper_line(r: dict, ctx: dict) -> str:
    ck = r["citekey"]
    line = f"- [[@{ck}]] {r.get('year') or 'n.d.'} — {r['title']}"
    if r["tags"]:
        line += " · " + " ".join(f"#{ctx['slugs'][t]}" for t in r["tags"])
    marks = ""
    if ck in ctx["my_notes"]:
        marks += MARK_NOTE
    if r["key_paper"]:
        marks += MARK_KEY
    if ctx["status"][ck] == "ok":
        marks += MARK_FULLTEXT
    if marks:
        line += " · " + " ".join(marks)
    return line


def tag_link(tag: str, ctx: dict) -> str:
    return f"[[Tags/{ctx['slugs'][tag]}|{tag}]]"


# ---------------------------------------------------------------------------
# Paper notes
# ---------------------------------------------------------------------------

def render_highlights(anns: list[dict], headings: dict[str, str]) -> list[str]:
    """Group by colour under the [highlights] headings, in config order;
    unmapped colours follow as "Other highlights"."""
    by_color: dict[str, list[dict]] = {}
    for ann in anns:
        by_color.setdefault(ann["color"], []).append(ann)
    groups = [(heading, by_color.pop(color)) for color, heading
              in headings.items() if color in by_color]
    groups += [(f"Other highlights ({color or 'no colour'})", by_color[color])
               for color in sorted(by_color)]

    lines: list[str] = []
    for heading, group in groups:
        lines += [f"### {heading}", ""]
        for ann in group:
            url = f"zotero://open-pdf/library/items/{ann['att_key']}"
            params = []
            if ann.get("page"):
                params.append(f"page={ann['page']}")
            params.append(f"annotation={ann['ann_key']}")
            label = ann.get("page_label") or ann.get("page") or "?"
            ref = f"[(p. {label})]({url}?{'&'.join(params)})"
            if ann["text"]:
                lines.append(f"- =={ann['text']}== {ref}")
                if ann["comment"]:
                    lines.append(f"\t- {ann['comment']}")
            else:
                lines.append(f"- {ann['comment']} {ref}")
        lines.append("")
    return lines


def render_paper(r: dict, ctx: dict) -> str:
    ck = r["citekey"]
    anns = ctx["annotations"].get(ck, [])
    note = ctx["my_notes"].get(ck)
    status = ctx["status"][ck]
    pdf_url = (f"zotero://open-pdf/library/items/{r['pdf_key']}"
               if r["pdf_key"] else None)

    fm = frontmatter({
        "generated": True,
        "citekey": ck,
        "zotero_key": r["zotero_key"] or None,
        "title": r["title"],
        "authors": r["authors"],
        "year": int(r["year"]) if r["year"].isdigit() else (r["year"] or None),
        "item_type": r["item_type"] or None,
        "doi": r["doi"] or None,
        "tags": [ctx["slugs"][t] for t in r["tags"]],
        "collections": r["collections"],
        "key_paper": r["key_paper"],
        "has_abstract": bool(r["abstract"]),
        "has_pdf": r["has_pdf"],
        "has_fulltext": status == "ok",
        "fulltext_status": status,
        "has_annotations": bool(anns),
        "has_my_note": note is not None,
        "my_note": note["link"] if note else None,
        "zotero_item": r["zotero_uri"] or None,
        "zotero_pdf": pdf_url,
    })

    title = f"[{r['title']}]({r['zotero_uri']})" if r["zotero_uri"] else r["title"]
    lines = [fm, "", f"# {title}", "", "## Abstract", "",
             r["abstract"] or "*No abstract in Zotero.*", "",
             "## My highlights", ""]
    lines += render_highlights(anns, ctx["highlights"]) if anns else ["*No highlights.*", ""]

    lines += ["## Links", ""]
    if r["tags"]:
        lines.append("- Tags: " + ", ".join(tag_link(t, ctx) for t in r["tags"]))
    if status == "ok":
        lines.append(f"- Full text: [[Fulltext/{ck}]]")
    else:
        lines.append(f"- Full text: none ({status})")
    if note:
        lines.append(f"- My note: [{note['path']}]({note['link']})")
    zot = []
    if r["zotero_uri"]:
        zot.append(f"[item]({r['zotero_uri']})")
    if pdf_url:
        zot.append(f"[PDF]({pdf_url})")
    if zot:
        lines.append("- Zotero: " + " · ".join(zot))
    if r["doi"]:
        lines.append(f"- DOI: [{r['doi']}](https://doi.org/{r['doi']})")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tag notes, index, home
# ---------------------------------------------------------------------------

def render_tag(tag: str, members: list[dict], cooc: Counter, n_related: int,
               ctx: dict) -> str:
    related = sorted(cooc.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:n_related]
    lines = [frontmatter({"generated": True, "tag": tag, "count": len(members)}),
             "", f"# {tag}", "", "## Related tags", ""]
    if related:
        lines += [f"- {tag_link(t, ctx)} ({n})" for t, n in related]
    else:
        lines.append("*None.*")
    lines += ["", f"## Papers ({len(members)})", ""]
    lines += [paper_line(r, ctx) for r in members]
    lines.append("")
    return "\n".join(lines)


def counts(papers: list[dict], ctx: dict) -> dict:
    return {
        "papers": len(papers),
        "tagged": sum(1 for r in papers if r["tags"]),
        "untagged": sum(1 for r in papers if not r["tags"]),
        "fulltext": sum(1 for r in papers if ctx["status"][r["citekey"]] == "ok"),
        "my_notes": sum(1 for r in papers if r["citekey"] in ctx["my_notes"]),
        "key": sum(1 for r in papers if r["key_paper"]),
        "highlights": sum(1 for r in papers if r["citekey"] in ctx["annotations"]),
    }


LEGEND = (f"Line format: `[[@citekey]] year — title · #tags · markers`. "
          f"Markers: {MARK_NOTE} I have a reading note in the main vault, "
          f"{MARK_KEY} key paper, {MARK_FULLTEXT} full text available.")


def render_index(papers: list[dict], c: dict, ctx: dict) -> str:
    lines = [frontmatter({"generated": True, **c}), "", "# Library index", "",
             f"{c['papers']} papers · {c['tagged']} tagged · {c['untagged']} "
             f"untagged · {c['fulltext']} with full text · {c['my_notes']} "
             f"with my notes", "", LEGEND, ""]
    lines += [paper_line(r, ctx) for r in papers]
    lines.append("")
    return "\n".join(lines)


def render_home(c: dict, tag_counts: list[tuple[str, int]], ctx: dict) -> str:
    lines = [
        frontmatter({"generated": True}), "",
        "# Literature vault", "",
        "Generated from Zotero by the scripts in `Code/` — every file here is "
        "disposable and rebuilt on each run. Nothing human-written lives in "
        "this vault; thinking about papers belongs in your own notes (the "
        "main vault, if one is configured).", "",
        f"- Papers: {c['papers']} ({c['tagged']} tagged, {c['untagged']} untagged)",
        f"- Key papers: {c['key']}",
        f"- With my highlights: {c['highlights']}",
        f"- With full text: {c['fulltext']}",
        f"- With my notes in the main vault: {c['my_notes']}",
        "",
        "## How to use", "",
        "1. **Titles** — [[INDEX]] lists every paper, one line each; the tag "
        "notes below list the papers under each of my Zotero tags.",
        "2. **Abstracts and highlights** — `Papers/@{citekey}` notes hold the "
        "metadata, the abstract (on one line, so a search hit shows all of it) "
        "and my Zotero highlights with page links.",
        "3. **Full text** — `Fulltext/{citekey}` notes hold page-marked text "
        "(`<!-- page N -->`, the physical page used by `zotero://open-pdf`). "
        "The folder is excluded from Obsidian search; open the files directly.",
        "",
        LEGEND, "",
        f"## Tags ({len(tag_counts)})", "",
    ]
    lines += [f"- {tag_link(t, ctx)} ({n})" for t, n in tag_counts]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def sync_dir(directory: Path, files: dict[str, str]) -> tuple[int, int]:
    """Write `files` (name -> content) and delete any other .md in the
    directory. Returns (written, removed)."""
    directory.mkdir(parents=True, exist_ok=True)
    written = sum(write_if_changed(directory / name, content)
                  for name, content in files.items())
    removed = 0
    for path in directory.glob("*.md"):
        if path.name not in files:
            path.unlink()
            removed += 1
    return written, removed


def main():
    cfg = load_config()
    argparse.ArgumentParser(description="Render the literature vault.").parse_args()
    vault = ROOT
    log = setup_logging(LOG_DIR, "build_vault")

    papers = read_jsonl(CORPUS_PATH)
    if not papers:
        raise SystemExit(f"No corpus at {CORPUS_PATH} — run 01_build_corpus.py first")
    papers.sort(key=sort_key)
    manifest = read_json(MANIFEST_PATH, {}) or {}

    all_tags = {t for r in papers for t in r["tags"]}
    ctx = {
        "slugs": tag_slugs(all_tags),
        "annotations": {row["citekey"]: row["annotations"]
                        for row in read_jsonl(ANNOTATIONS_PATH)},
        "my_notes": read_json(MY_NOTES_PATH, {}) or {},
        "status": {r["citekey"]: fulltext_status(r, manifest) for r in papers},
        "highlights": cfg.highlights,
    }
    if cfg.main_vault and not MY_NOTES_PATH.exists():
        log.warning("No my_notes.json — run 03_scan_my_notes.py for ✎ markers")

    # --- Papers/ ---
    paper_files = {f"@{r['citekey']}.md": render_paper(r, ctx) for r in papers}
    p_written, p_removed = sync_dir(vault / "Papers", paper_files)

    # --- Tags/ ---
    members: dict[str, list[dict]] = {t: [] for t in all_tags}
    cooc: dict[str, Counter] = {t: Counter() for t in all_tags}
    for r in papers:
        for t in r["tags"]:
            members[t].append(r)
            cooc[t].update(o for o in r["tags"] if o != t)
    tag_files = {f"{ctx['slugs'][t]}.md":
                 render_tag(t, members[t], cooc[t], cfg.related_tags, ctx)
                 for t in all_tags}
    t_written, t_removed = sync_dir(vault / "Tags", tag_files)

    # --- INDEX.md and Home.md ---
    c = counts(papers, ctx)
    tag_counts = sorted(((t, len(m)) for t, m in members.items()),
                        key=lambda kv: (-kv[1], kv[0].lower()))
    i_written = write_if_changed(vault / "INDEX.md", render_index(papers, c, ctx))
    h_written = write_if_changed(vault / "Home.md", render_home(c, tag_counts, ctx))

    summary = (
        f"\n{'='*50}\n"
        f"  Papers/:  {len(paper_files)} notes ({p_written} written, "
        f"{p_removed} removed)\n"
        f"  Tags/:    {len(tag_files)} notes ({t_written} written, "
        f"{t_removed} removed)\n"
        f"  INDEX.md: {'written' if i_written else 'unchanged'} · "
        f"Home.md: {'written' if h_written else 'unchanged'}\n"
        f"  {c['papers']} papers · {c['tagged']} tagged · {c['fulltext']} full "
        f"text · {c['highlights']} with highlights · {c['my_notes']} with my notes\n"
        f"{'='*50}"
    )
    log.debug(summary)
    print(summary)


if __name__ == "__main__":
    main()

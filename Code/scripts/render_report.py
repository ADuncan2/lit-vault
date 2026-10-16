"""
render_report.py — render a typed lit-vault report from a review.json.

Not a build step: the lit-vault-search skill runs this at the end of every
multi-paper search. The agent's only writing job is a small review.json —
the topic, a lede, thematic groups, and one statement per anchor paper.
This script joins that with papers.json (built by 05) and the frozen
template for the report's type in Code/templates/{type}.template.html,
and writes the finished page: author formatting, chips, Zotero links,
the contents list, collapsed abstracts (from papers.json), escaping, dedup and the footer are handled identically every run, so
formatting cannot drift and needs no test-and-fix pass.

Stdlib only; runs on a bare clone without uv.

Outputs (every run writes all three; nothing is overwritten except the
stable page):

  Summaries/YYYY-MM-DD--{type}--{slug}.json   the input review.json, verbatim
  Summaries/YYYY-MM-DD--{type}--{slug}.html   the page, frozen as rendered,
                                              with a "← all reports" link
  lit-vault.html                              the same page plus a dropdown
                                              of every archived report

Two output modes, one page each: the archived page (frozen, never
rewritten — later template changes do not touch it; re-render from its
.json if ever wanted) and the stable page (rewritten every run; bookmark
it). One exception to frozen: rendering a .json that already lives in
Summaries/ (citation_gaps.py augments one in place) keeps its stem and
overwrites its page instead of archiving a copy. The slug comes from the topic; a second run with the same date, type
and topic gets -2, -3, … The dropdown is built by scanning the archived
.json files (newest first, label "YYYY-MM-DD · type · topic") and uses
plain navigation, never fetch(), so it works from file://.

Supported types: survey (schema 1) and search (schema 1). An unknown type or
schema fails with a message naming the supported ones.

review.json schema, type "survey" (all strings are plain text — the script
escapes them):
{
  "schema": 1,                                          # required
  "type":   "survey",                                   # required
  "topic":  "Topic of the review — an overview",        # page h1
  "lede":   "One or two sentences framing the review.", # link-usage boilerplate is appended
  "groups": [
    {
      "name": "Theme of this group",
      "cards": [                       # anchor papers, full cards
        {
          "citekey":   "smithExampleCitekey2024",
          "statement": "One-to-two-line statement of what it shows.",
          "source":    "abstract",     # or "title", "full text p.12",
                                       #    "my highlight p.6", "my note"
          "mine":      "the user's own doubt/annotation, if worth surfacing",  # optional
          "pdf_page":  12,             # optional: deep-link the PDF button
          "pdf_label": "PDF at the framework table (p. 12)"                   # optional
        }
      ],
      "also": ["citekey", ...]         # compact one-line list, optional
    }
  ],
  "gaps": { ... }                      # optional: written by citation_gaps.py,
                                       # never by the agent. Renders as one
                                       # extra section ("Beyond your library");
                                       # delete the key and re-render to drop
                                       # it. Schema in scripts/citation_gaps.py.
}

review.json schema, type "search" (written deterministically by
semantic_report.py — no LLM; cards carry a similarity score and the
best-matching location instead of a statement):
{
  "schema": 1, "type": "search",
  "topic":  "the query as typed",
  "lede":   "what was searched and with which settings",
  "params": { ... },                   # kept for reproducibility; not rendered
  "groups": [
    {
      "name": "Matched in full text",  # or "Matched on the abstract"
      "cards": [
        {
          "citekey": "smithExampleCitekey2024",
          "score":   0.78,             # cosine similarity of the best hit
          "match":   "fulltext",       # or "abstract"
          "page":    12, "part": 1,    # fulltext matches only
          "excerpt": "preview of the best-matching chunk",   # fulltext only
          "pdf_page": 12               # optional: deep-link the PDF button
        }
      ]
    }
  ],
  "gaps": { ... }                      # optional, as for surveys
}

Usage:
    python Code/scripts/render_report.py review.json [-o lit-vault.html]
                                         [--papers papers.json]

-o sets the stable page; the archive goes in Summaries/ beside it.
"""

import argparse
import datetime
import html
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
TEMPLATES = VAULT / "Code" / "templates"
ARCHIVE_DIR = "Summaries"
SCHEMA = 1

LEDE_TAIL = (
    ' "Open in Zotero" selects the item; "PDF" opens the reader, at the'
    " flagged page where there is one. The first click may ask to open"
    ' Zotero — tick "always allow".'
)


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def slugify(text: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


# -- survey ---------------------------------------------------------------

def who_year(p: dict) -> str:
    who = p.get("display_author") or p.get("citekey")
    year = p.get("year") or "n.d."
    return f"{esc(who)} ({esc(year)})"


def chips(p: dict) -> str:
    out = []
    if p.get("key_paper"):
        out.append('<span class="chip">Key</span>')
    if p.get("has_my_note"):
        out.append('<span class="chip">Note</span>')
    if p.get("fulltext_status") == "ok":
        out.append('<span class="chip">Full text</span>')
    if not p.get("abstract"):
        out.append('<span class="chip flag">No abstract</span>')
    return "".join(out)


def actions(p: dict, card: dict) -> str:
    out = []
    if p.get("zotero_item"):
        out.append(f'<a class="primary" href="{esc(p["zotero_item"])}">Open in Zotero</a>')
    if p.get("zotero_pdf"):
        href = p["zotero_pdf"]
        label = "PDF"
        if card.get("pdf_page"):
            href = f'{href}?page={int(card["pdf_page"])}'
            label = card.get("pdf_label") or f'PDF at p. {card["pdf_page"]}'
        out.append(f'<a href="{esc(href)}">{esc(label)}</a>')
    return "\n        ".join(out)


def abstract_block(p: dict) -> str:
    """Collapsed abstract from papers.json; empty when there is none."""
    if not p.get("abstract"):
        return ""
    return (f'<details class="abs"><summary>Abstract</summary>'
            f'<p>{esc(p["abstract"])}</p></details>')


def render_card(card: dict, p: dict) -> str:
    source = card.get("source") or ("abstract" if p.get("abstract") else "title")
    srclab = "[title only]" if source == "title" else f"[{source}]"
    mine = ""
    if card.get("mine"):
        mine = f'\n      <p class="mine">{esc(card["mine"])}</p>'
    return f'''    <article class="paper">
      <div class="cite"><span class="who">{who_year(p)}</span><span class="key">{esc(p["citekey"])}</span><span class="chips">{chips(p)}</span></div>
      <h3 class="title">{esc(p.get("title", ""))}</h3>
      <p class="what">{esc(card["statement"])} <span class="src">{esc(srclab)}</span></p>{mine}
      {abstract_block(p)}
      <div class="actions">
        {actions(p, card)}
      </div>
    </article>'''


def render_also(citekeys: list, papers: dict) -> str:
    items = []
    for k in citekeys:
        p = papers[k]
        title = p.get("title", "")
        if len(title) > 90:
            title = title[:88].rstrip() + "…"
        link = f' · <a href="{esc(p["zotero_item"])}">Zotero</a>' if p.get("zotero_item") else ""
        items.append(f"      <li>{who_year(p)} — {esc(title)}{link}{abstract_block(p)}</li>")
    body = "\n".join(items)
    return f'''    <article class="paper">
      <div class="cite"><span class="who">Also in your library ({len(citekeys)})</span></div>
      <ul class="more">
{body}
      </ul>
    </article>'''


# -- gaps section (optional, written into the survey JSON by ---------------
# -- citation_gaps.py; everything below this line up to validate_survey ----
# -- is the modular seam — remove these two functions and their two call ----
# -- sites to lift the feature out) -----------------------------------------

def validate_gaps_block(gaps: dict, papers: dict) -> list:
    """Errors in an optional review["gaps"] block. Gap candidates carry
    their metadata inline and deliberately have no citekey."""
    errors = []
    for row in gaps.get("cluster", []):
        if row.get("citekey") not in papers:
            errors.append(f"gaps cluster: unknown citekey {row.get('citekey')!r}")
    for direction in ("backward", "forward"):
        seen = set()
        for c in gaps.get(direction, []):
            oid = c.get("openalex_id")
            if not c.get("title") or not oid:
                errors.append(f"gaps {direction}: candidate missing title "
                              f"or openalex_id ({oid or c.get('title')!r})")
            if not isinstance(c.get("cluster_count"), int) or c["cluster_count"] < 1:
                errors.append(f"gaps {direction} {oid}: bad cluster_count")
            if oid in seen:
                errors.append(f"gaps {direction}: duplicate {oid}")
            seen.add(oid)
    return errors


def render_gap_card(c: dict, verb: str, n_cluster: int) -> str:
    """One paper found through the citation graph; metadata is inline
    (no citekey — the whole point is that it is not in the library)."""
    authors = ", ".join(c.get("authors", []))
    if c.get("n_authors", 0) > len(c.get("authors", [])):
        authors += " et al."
    who = f'{esc(authors or "Unknown")} ({esc(c.get("year") or "n.d.")})'
    chip = (f'<span class="chip">{esc(verb)} {c["cluster_count"]}/{n_cluster}</span>'
            f'<span class="chip">Cited {c.get("cited_by_count", 0):,}×</span>')
    if not c.get("doi"):
        chip += '<span class="chip flag">No DOI</span>'
    links = "\n".join(f'        <li>{esc(k)}</li>'
                      for k in c.get("cluster_citekeys", []))
    abstract = ""
    if c.get("abstract"):
        abstract = (f'<details class="abs"><summary>Abstract</summary>'
                    f'<p>{esc(c["abstract"])}</p></details>')
    acts = []
    if c.get("doi"):
        acts.append(f'<a class="primary" href="https://doi.org/{esc(c["doi"])}">DOI</a>')
    acts.append(f'<a href="{esc(c["openalex_url"])}">OpenAlex</a>')
    actions_html = "\n        ".join(acts)
    return f'''    <article class="paper">
      <div class="cite"><span class="who">{who}</span><span class="chips">{chip}</span></div>
      <h3 class="title">{esc(c.get("title", ""))}</h3>
      <p class="what"><span class="src">{esc(verb)} in this review:</span></p>
      <ul class="srclist">
{links}
      </ul>
      {abstract}
      <div class="actions">
        {actions_html}
      </div>
    </article>'''


def render_gaps_section(gaps: dict) -> tuple:
    """(section html, toc entry) for the "Beyond your library" section —
    papers the citation graph connects to this review's cluster that are
    missing from the Zotero library (found by citation_gaps.py)."""
    n_cluster = sum(1 for r in gaps.get("cluster", []) if r.get("openalex_id"))
    blocks = []
    for direction, verb, head in (
            ("backward", "Referenced by", "Referenced by this review's papers"),
            ("forward", "Cites", "Citing this review's papers")):
        cards = gaps.get(direction, [])
        header = (f'    <article class="paper">\n      <div class="cite">'
                  f'<span class="who">{esc(head)} — not in your library '
                  f'({len(cards)})</span></div>\n    </article>')
        blocks.append(header)
        blocks.extend(render_gap_card(c, verb, n_cluster) for c in cards)
    notes = list(gaps.get("notes", []))
    p = gaps.get("params", {})
    notes.append(f'OpenAlex, fetched {p.get("fetched", "?")}; forward gaps '
                 f'cite ≥{p.get("min_cluster_cites", "?")} cluster papers; '
                 f'top {p.get("max_per_direction", "?")} per direction by '
                 'cluster count, then total citations.')
    tail = "\n".join(f'    <p class="mine">{esc(n)}</p>' for n in notes)
    n_total = len(gaps.get("backward", [])) + len(gaps.get("forward", []))
    section = ('  <section id="gaps">\n    <h2>Beyond your library</h2>\n\n'
               + "\n\n".join(blocks) + f"\n\n{tail}\n"
               + '    <a class="top" href="#top">↑ contents</a>\n  </section>')
    toc = (f'      <li><a href="#gaps">Beyond your library</a>'
           f' <span class="n">{n_total}</span></li>')
    return section, toc


def validate_survey(review: dict, papers: dict) -> list:
    """Return error strings; warn (not fail) on same-title duplicates."""
    cited, missing, errors = [], [], []
    for g in review.get("groups", []):
        for c in g.get("cards", []):
            cited.append(c["citekey"])
            if not c.get("statement"):
                errors.append(f"card {c['citekey']}: empty statement")
        cited.extend(g.get("also", []))
    seen = set()
    for k in cited:
        if k not in papers:
            missing.append(k)
        if k in seen:
            errors.append(f"{k}: listed more than once")
        seen.add(k)
    if missing:
        errors.append("unknown citekeys (not in papers.json): " + ", ".join(missing))
    if review.get("gaps"):
        errors.extend(validate_gaps_block(review["gaps"], papers))
    if errors:
        return errors

    by_title = {}
    for k in seen:
        t = (papers[k].get("title") or "").lower().strip()
        by_title.setdefault(t, []).append(k)
    for t, ks in by_title.items():
        if len(ks) > 1:
            print(f"warning: same title cited under {len(ks)} citekeys: "
                  + ", ".join(ks), file=sys.stderr)
    return []


def render_survey(review: dict, papers: dict, now: datetime.datetime) -> tuple:
    """Return (placeholder values, one-line summary)."""
    sections, toc = [], []
    n_cards = 0
    n_total = 0
    for i, g in enumerate(review["groups"], 1):
        sid = f"s{i}-{slugify(g['name'], 40)}"
        n_group = len(g.get("cards", [])) + len(g.get("also", []))
        toc.append(f'      <li><a href="#{sid}">{esc(g["name"])}</a>'
                   f' <span class="n">{n_group}</span></li>')
        parts = [f'  <section id="{sid}">\n    <h2>{esc(g["name"])}</h2>\n']
        for c in g.get("cards", []):
            parts.append(render_card(c, papers[c["citekey"]]))
            n_cards += 1
        if g.get("also"):
            parts.append(render_also(g["also"], papers))
        n_total += n_group
        parts.append('    <a class="top" href="#top">↑ contents</a>\n  </section>')
        sections.append("\n\n".join(parts))

    if review.get("gaps"):
        gaps_section, gaps_toc = render_gaps_section(review["gaps"])
        sections.append(gaps_section)
        toc.append(gaps_toc)

    footer = (f"Built from the literature vault on {now.strftime('%d %b %Y')} "
              f"({len(papers):,} papers in the library; {n_total} in this review).")
    values = {
        "LEDE": esc(review["lede"]) + LEDE_TAIL,
        "TOC": "\n".join(toc),
        "SECTIONS": "\n\n".join(sections),
        "FOOTER_NOTE": esc(footer),
    }
    summary = (f"{len(review['groups'])} sections, {n_cards} cards, "
               f"{n_total} papers")
    return values, summary


# -- search (deterministic semantic search, written by semantic_report.py) --

def validate_search(review: dict, papers: dict) -> list:
    errors, seen = [], set()
    for g in review.get("groups", []):
        for c in g.get("cards", []):
            k = c.get("citekey")
            if k not in papers:
                errors.append(f"unknown citekey (not in papers.json): {k!r}")
            if k in seen:
                errors.append(f"{k}: listed more than once")
            seen.add(k)
            if not isinstance(c.get("score"), (int, float)):
                errors.append(f"card {k}: missing or non-numeric score")
            if c.get("match") not in ("fulltext", "abstract"):
                errors.append(f"card {k}: match must be 'fulltext' or 'abstract'")
            if c.get("match") == "fulltext" and not c.get("page"):
                errors.append(f"card {k}: fulltext match without a page")
    if review.get("gaps"):
        errors.extend(validate_gaps_block(review["gaps"], papers))
    return errors


def render_search_card(card: dict, p: dict) -> str:
    score = f"{card['score']:.2f}"
    if card["match"] == "fulltext":
        srclab = f"[full text p.{card['page']} · {score}]"
        what = (f'Best-matching page: “{esc(card.get("excerpt", ""))}…” '
                f'<span class="src">{esc(srclab)}</span>')
    else:
        srclab = f"[abstract · {score}]"
        what = f'Matched on the abstract. <span class="src">{esc(srclab)}</span>'
    return f'''    <article class="paper">
      <div class="cite"><span class="who">{who_year(p)}</span><span class="key">{esc(p["citekey"])}</span><span class="chips">{chips(p)}</span></div>
      <h3 class="title">{esc(p.get("title", ""))}</h3>
      <p class="what">{what}</p>
      {abstract_block(p)}
      <div class="actions">
        {actions(p, card)}
      </div>
    </article>'''


def render_search(review: dict, papers: dict, now: datetime.datetime) -> tuple:
    """Return (placeholder values, one-line summary)."""
    sections, toc = [], []
    n_total = 0
    for i, g in enumerate(review["groups"], 1):
        sid = f"s{i}-{slugify(g['name'], 40)}"
        cards = g.get("cards", [])
        toc.append(f'      <li><a href="#{sid}">{esc(g["name"])}</a>'
                   f' <span class="n">{len(cards)}</span></li>')
        parts = [f'  <section id="{sid}">\n    <h2>{esc(g["name"])}</h2>\n']
        parts.extend(render_search_card(c, papers[c["citekey"]]) for c in cards)
        n_total += len(cards)
        parts.append('    <a class="top" href="#top">↑ contents</a>\n  </section>')
        sections.append("\n\n".join(parts))

    if review.get("gaps"):
        gaps_section, gaps_toc = render_gaps_section(review["gaps"])
        sections.append(gaps_section)
        toc.append(gaps_toc)

    footer = (f"Searched the literature vault on {now.strftime('%d %b %Y')} "
              f"({len(papers):,} papers in the library; {n_total} matched).")
    values = {
        "LEDE": esc(review["lede"]) + LEDE_TAIL,
        "TOC": "\n".join(toc),
        "SECTIONS": "\n\n".join(sections),
        "FOOTER_NOTE": esc(footer),
    }
    summary = f"{len(review['groups'])} sections, {n_total} papers"
    return values, summary


# type -> (validate, render); a new type is a template plus an entry here
TYPES = {
    "survey": (validate_survey, render_survey),
    "search": (validate_search, render_search),
}


# -- archive and dropdown ---------------------------------------------------

def archive_stem(archive: Path, date: str, rtype: str, slug: str) -> str:
    base = f"{date}--{rtype}--{slug}"
    stem, n = base, 1
    while (archive / f"{stem}.html").exists() or (archive / f"{stem}.json").exists():
        n += 1
        stem = f"{base}-{n}"
    return stem


def archived_reports(archive: Path) -> list:
    """(href stem, label) for every archived report, newest first."""
    rows = []
    for j in archive.glob("*.json"):
        if not j.with_suffix(".html").exists():
            continue
        try:
            r = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"warning: skipping {j.name} in dropdown: {e}", file=sys.stderr)
            continue
        date = j.stem.split("--", 1)[0]
        label = f"{date} · {r.get('type', '?')} · {r.get('topic', j.stem)}"
        rows.append((date, j.stat().st_mtime, j.stem, label))
    rows.sort(reverse=True)
    return [(stem, label) for _, _, stem, label in rows]


def dropdown(archive: Path, current: str) -> str:
    opts = []
    for stem, label in archived_reports(archive):
        sel = " selected" if stem == current else ""
        href = f"{ARCHIVE_DIR}/{stem}.html"
        opts.append(f'      <option value="{esc(href)}"{sel}>{esc(label)}</option>')
    body = "\n".join(opts)
    return f'''<nav class="reports">
    <label for="reports">Reports</label>
    <select id="reports" onchange="if (this.value) location.href = this.value;">
{body}
    </select>
  </nav>'''


def back_link(stable_name: str) -> str:
    return (f'<nav class="reports"><a href="../{esc(stable_name)}">'
            f'← all reports</a></nav>')


def fill(tpl: str, values: dict) -> str:
    # single pass, so placeholder-like text inside review strings is left alone
    unfilled = sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", tpl)) - set(values))
    if unfilled:
        raise SystemExit("error: template placeholders with no value: " + ", ".join(unfilled))
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values[m.group(1)], tpl)


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a lit-vault report from review.json")
    ap.add_argument("review", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=VAULT / "lit-vault.html",
                    help="stable page (default: lit-vault.html at the vault root); "
                         "the archive goes in Summaries/ beside it")
    ap.add_argument("--papers", type=Path, default=VAULT / "papers.json")
    args = ap.parse_args()

    review = json.loads(args.review.read_text(encoding="utf-8"))

    # -- type and schema ------------------------------------------------------
    rtype, schema = review.get("type"), review.get("schema")
    supported = ", ".join(sorted(TYPES))
    if rtype not in TYPES:
        print(f"error: unsupported report type {rtype!r} (supported: {supported}); "
              'set "type" in review.json', file=sys.stderr)
        return 1
    if schema != SCHEMA:
        print(f"error: unsupported schema {schema!r} for type {rtype!r} "
              f'(supported: {SCHEMA}); set "schema": {SCHEMA} in review.json',
              file=sys.stderr)
        return 1
    template = TEMPLATES / f"{rtype}.template.html"
    if not template.exists():
        print(f"error: no template for type {rtype!r}: {template}", file=sys.stderr)
        return 1
    validate, render = TYPES[rtype]

    corpus = json.loads(args.papers.read_text(encoding="utf-8"))
    papers = {p["citekey"]: p for p in corpus["papers"]}

    errors = validate(review, papers)
    if errors:
        for e in errors:
            print("error:", e, file=sys.stderr)
        return 1

    # -- render ---------------------------------------------------------------
    now = datetime.datetime.now()
    values, summary = render(review, papers, now)
    values.update({
        "TYPE": esc(rtype.replace("-", " ").capitalize()),
        "GENERATED": esc(now.strftime("%d %b %Y, %H:%M")),
        "TOPIC": esc(review["topic"]),
    })
    tpl = template.read_text(encoding="utf-8")

    stable = args.out.resolve()
    archive = stable.parent / ARCHIVE_DIR
    archive.mkdir(parents=True, exist_ok=True)
    # Re-rendering a review that already lives in the archive (e.g. after
    # citation_gaps.py adds its block) keeps its stem and overwrites its
    # page; anything else gets a fresh dated stem.
    in_archive = args.review.resolve().parent == archive
    if in_archive:
        stem = args.review.resolve().stem
    else:
        stem = archive_stem(archive, now.strftime("%Y-%m-%d"), rtype,
                            slugify(review["topic"]))

    archived_html = fill(tpl, {**values, "PAGE_KIND": "archived",
                               "TITLE": esc(review["topic"]),
                               "NAV": back_link(stable.name)})
    if not in_archive:
        shutil.copyfile(args.review, archive / f"{stem}.json")
    write(archive / f"{stem}.html", archived_html)

    stable_html = fill(tpl, {**values, "PAGE_KIND": "latest",
                             "TITLE": "Lit vault — latest",
                             "NAV": dropdown(archive, stem)})
    write(stable, stable_html)

    print(f"{stable}: {summary}, {len(stable_html):,} bytes")
    print(f"archived: {ARCHIVE_DIR}/{stem}.html (+ .json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

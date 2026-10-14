"""Generated-markdown helpers: YAML frontmatter, and the Fulltext/{citekey}.md
format (written by step 02, read by agents).

Fulltext format — plain text, minimal frontmatter, a marker before each page:

    ---
    generated: true
    citekey: smith2024flex
    pages: 14
    status: ok
    extractor: pypdf 6.12.1
    ---

    <!-- page 1 -->
    …text…

Page numbers are the 1-based physical page index (what
zotero://open-pdf/...?page=N uses), not the printed page label.
"""

import json

PAGE_MARKER = "<!-- page {n} -->"


def yaml_value(value) -> str:
    """One YAML scalar or flow list. Strings are JSON-quoted, which is valid
    YAML and never ambiguous (colons, #, leading @, 'yes', numbers…)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(yaml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def frontmatter(fields: dict) -> str:
    """Frontmatter block from an ordered dict; None values are omitted."""
    lines = ["---"]
    for key, value in fields.items():
        if value is not None:
            lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---")
    return "\n".join(lines)


def render_fulltext(citekey: str, pages: list[str], n_pages: int,
                    status: str, extractor: str) -> str:
    """`pages` may be shorter than `n_pages` when the references were cut."""
    parts = [frontmatter({
        "generated": True,
        "citekey": citekey,
        "pages": n_pages,
        "status": status,
        "extractor": extractor,
    }), ""]
    for n, text in enumerate(pages, start=1):
        parts.append(PAGE_MARKER.format(n=n))
        if text:
            parts.append(text)
        parts.append("")
    return "\n".join(parts)

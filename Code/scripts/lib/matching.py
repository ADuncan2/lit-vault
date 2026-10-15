"""Deterministic identity matching: DOI/title normalisation and the holdings
set built from the full BBT export.

Used by citation_gaps.py to decide whether a paper found in the citation
graph is already in the Zotero library. Matching is cleaning plus exact
comparison, never fuzzy: a normalised-DOI match counts first, a
normalised-title match only for items without a DOI.

Stdlib only. The doctests are the unit tests; from Code/scripts/:
    uv run python -m lib.matching
"""

import re
import unicodedata
from dataclasses import dataclass, field

from . import bbt

# itemTypes that are not papers (mirrors SKIP_ITEM_TYPES in 01_build_corpus.py)
SKIP_ITEM_TYPES = {"note", "annotation", "attachment"}

_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_doi(raw) -> str:
    """Canonical lowercase DOI, or "" when there is none.

    DOIs are case-insensitive; resolver prefixes and copy-paste trailing
    punctuation are stripped. A trailing ")" is NOT stripped — parentheses
    are legal inside DOIs (common in older Elsevier DOIs).

    >>> normalize_doi("https://doi.org/10.1016/J.ERSS.2021.102258")
    '10.1016/j.erss.2021.102258'
    >>> normalize_doi("  doi:10.1000/XYZ.,;")
    '10.1000/xyz'
    >>> normalize_doi("http://dx.doi.org/10.1002/(SICI)1099-0526")
    '10.1002/(sici)1099-0526'
    >>> normalize_doi(None)
    ''
    """
    if not raw:
        return ""
    s = str(raw).strip()
    s = _DOI_PREFIX_RE.sub("", s)
    return s.rstrip(".,;").lower()


def normalize_title(raw) -> str:
    """Title folded to lowercase ASCII words for exact comparison.

    >>> normalize_title("  Energy  Justice: A Conceptual Review ")
    'energy justice a conceptual review'
    >>> normalize_title("Café società — l'énergie")
    'cafe societa l energie'
    >>> normalize_title("")
    ''
    """
    if not raw:
        return ""
    # NFKD, drop combining accents (so "é" folds to "e", not "e "), then
    # replace every remaining non-[a-z0-9] rune — unicode dashes and quotes
    # included — with a space. Folding to ASCII before the replacement
    # would instead delete non-ASCII punctuation and join its neighbours.
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return _NON_ALNUM_RE.sub(" ", s).strip()


@dataclass
class Holdings:
    """Normalised identifiers of everything in the Zotero library."""
    dois: set = field(default_factory=set)
    titles: set = field(default_factory=set)


def build_holdings(items: list) -> Holdings:
    """Holdings from the FULL export item list: every item that is not a
    note/annotation/attachment and has a title. A citekey is not required —
    "already in Zotero" means the whole library, not just the built corpus.
    """
    h = Holdings()
    for item in items:
        if bbt.get_item_type(item) in SKIP_ITEM_TYPES:
            continue
        title = normalize_title(bbt.get_title(item))
        if not title or title == "untitled":
            continue
        h.titles.add(title)
        doi = normalize_doi(bbt.get_doi(item))
        if doi:
            h.dois.add(doi)
    return h


def in_holdings(holdings: Holdings, doi, title) -> bool:
    """Is a candidate already in the library? DOI membership decides when
    the candidate has a DOI; the title fallback is only for candidates
    without one (a title match must never override a DOI mismatch).

    >>> h = Holdings(dois={"10.1/a"}, titles={"some held paper"})
    >>> in_holdings(h, "10.1/A", "unrelated title")
    True
    >>> in_holdings(h, "10.9/other", "some held paper")   # DOI decides
    False
    >>> in_holdings(h, None, "Some  Held—Paper")
    True
    >>> in_holdings(h, None, "brand new paper")
    False
    """
    d = normalize_doi(doi)
    if d:
        return d in holdings.dois
    return normalize_title(title) in holdings.titles


if __name__ == "__main__":
    import doctest
    raise SystemExit(doctest.testmod(verbose=True).failed)

"""The per-paper record: one row of Code/data/corpus.jsonl."""

from dataclasses import dataclass, field


@dataclass
class PaperRecord:
    citekey: str
    title: str
    zotero_key: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    item_type: str = ""
    tags: list[str] = field(default_factory=list)         # manual Zotero tags
    collections: list[str] = field(default_factory=list)  # collection names
    key_paper: bool = False       # key_tag or key_collection (incl. children)
    abstract: str = ""
    zotero_uri: str = ""
    has_pdf: bool = False
    pdf_key: str = ""             # attachment key, for zotero://open-pdf
    pdf_path: str = ""

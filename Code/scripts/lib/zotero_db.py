"""Read-only access to Zotero's SQLite database: annotations, and the
library's holdings for citation_gaps.py."""

import json
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Zotero link prefix for an item's library: "library" for My Library,
# "groups/{groupID}" for a group — as in zotero://select/{prefix}/items/KEY.
_LIBRARY_SQL = "COALESCE('groups/' || g.groupID, 'library')"


@contextmanager
def open_zotero_db(zotero_data: Path) -> Iterator[sqlite3.Connection | None]:
    """A connection to a temporary copy of zotero.sqlite (Zotero locks the
    live file while running), or None if there is no database. The copy is
    deleted on exit."""
    db_path = zotero_data / "zotero.sqlite"
    if not db_path.exists():
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="lit-vault-") as tmp:
        copy = Path(tmp) / "zotero.sqlite"
        shutil.copy2(db_path, copy)
        conn = sqlite3.connect(copy)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def item_ref(library: str, key: str) -> str:
    """Library-qualified item key: item keys are unique only per library."""
    return f"{library}/{key}"


def _page_index(position: str | None) -> int | None:
    """1-based physical page from the annotation's position JSON (pageIndex
    is 0-based) — what zotero://open-pdf/...?page=N expects."""
    if not position:
        return None
    try:
        idx = json.loads(position).get("pageIndex")
    except (json.JSONDecodeError, AttributeError):
        return None
    return idx + 1 if isinstance(idx, int) else None


def fetch_all_annotations(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """All PDF annotations, keyed by item_ref(library, parent item key).

    Chain: parent item -> itemAttachments (PDF) -> itemAnnotations.
    Order within a paper is stable: attachment key, then Zotero's sortIndex
    (reading order), then annotation key.
    """
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT {_LIBRARY_SQL} AS library,
               parent.key AS item_key,
               att.key    AS att_key,
               ann.key    AS ann_key,
               ia.text, ia.comment, ia.color, ia.pageLabel, ia.position,
               ia.sortIndex
        FROM itemAnnotations ia
        JOIN items ann            ON ann.itemID = ia.itemID
        JOIN itemAttachments iat  ON iat.itemID = ia.parentItemID
        JOIN items att            ON att.itemID = iat.itemID
        JOIN items parent         ON parent.itemID = iat.parentItemID
        LEFT JOIN groups g        ON g.libraryID = parent.libraryID
        WHERE iat.contentType = 'application/pdf'
        ORDER BY library, parent.key, att.key, ia.sortIndex, ann.key
    """)

    by_item: dict[str, list[dict]] = {}
    for row in cursor.fetchall():
        text = " ".join((row["text"] or "").split())
        comment = " ".join((row["comment"] or "").split())
        if not text and not comment:
            continue  # image/ink annotations carry nothing to render
        ref = item_ref(row["library"], row["item_key"])
        by_item.setdefault(ref, []).append({
            "color": (row["color"] or "").lower(),
            "text": text,
            "comment": comment,
            "page_label": row["pageLabel"] or "",
            "page": _page_index(row["position"]),
            "att_key": row["att_key"],
            "ann_key": row["ann_key"],
        })
    return by_item


def fetch_holdings(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(title, DOI) for every regular item in every library, trash excluded
    — the whole of Zotero, whatever the export covers. DOI is "" if none."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.itemID,
               MAX(CASE WHEN f.fieldName = 'title' THEN v.value END) AS title,
               MAX(CASE WHEN f.fieldName = 'DOI'   THEN v.value END) AS doi
        FROM items i
        JOIN itemTypes t           ON t.itemTypeID = i.itemTypeID
        JOIN itemData d            ON d.itemID = i.itemID
        JOIN fields f              ON f.fieldID = d.fieldID
        JOIN itemDataValues v      ON v.valueID = d.valueID
        WHERE t.typeName NOT IN ('note', 'annotation', 'attachment')
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND f.fieldName IN ('title', 'DOI')
        GROUP BY i.itemID
    """)
    return [(row["title"] or "", row["doi"] or "") for row in cursor.fetchall()]

"""Read-only access to Zotero's SQLite database for annotation fetching."""

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path


def open_zotero_db(zotero_data: Path) -> sqlite3.Connection | None:
    """Copy zotero.sqlite to a temp file (avoids lock) and open read-only."""
    db_path = zotero_data / "zotero.sqlite"
    if not db_path.exists():
        return None
    tmp = Path(tempfile.mktemp(suffix=".sqlite"))
    shutil.copy2(db_path, tmp)
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row
    return conn


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
    """All PDF annotations in the library, keyed by parent item key.

    Chain: parent item -> itemAttachments (PDF) -> itemAnnotations.
    Order within a paper is stable: attachment key, then Zotero's sortIndex
    (reading order), then annotation key.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT parent.key AS item_key,
               att.key    AS att_key,
               ann.key    AS ann_key,
               ia.text, ia.comment, ia.color, ia.pageLabel, ia.position,
               ia.sortIndex
        FROM itemAnnotations ia
        JOIN items ann            ON ann.itemID = ia.itemID
        JOIN itemAttachments iat  ON iat.itemID = ia.parentItemID
        JOIN items att            ON att.itemID = iat.itemID
        JOIN items parent         ON parent.itemID = iat.parentItemID
        WHERE iat.contentType = 'application/pdf'
        ORDER BY parent.key, att.key, ia.sortIndex, ann.key
    """)

    by_item: dict[str, list[dict]] = {}
    for row in cursor.fetchall():
        text = " ".join((row["text"] or "").split())
        comment = " ".join((row["comment"] or "").split())
        if not text and not comment:
            continue  # image/ink annotations carry nothing to render
        by_item.setdefault(row["item_key"], []).append({
            "color": (row["color"] or "").lower(),
            "text": text,
            "comment": comment,
            "page_label": row["pageLabel"] or "",
            "page": _page_index(row["position"]),
            "att_key": row["att_key"],
            "ann_key": row["ann_key"],
        })
    return by_item

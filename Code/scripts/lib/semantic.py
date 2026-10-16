"""Semantic index over Fulltext/ — shared by 06_build_semantic_index.py and
semantic_search.py. See "Semantic index" in Code/PIPELINE_PLAN.md.

The index is derived, gitignored and retrieval-only: it locates pages, it is
never evidence. Embedding goes through Ollama's /api/embed (stdlib urllib);
vectors live as float32 blobs in SQLite and search is brute force in numpy.
numpy is imported lazily so the capability check can report its absence.
"""

import hashlib
import json
import math
import re
import sqlite3
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import CONFIG_PATH
from .store import DATA_DIR, FULLTEXT_DIR, read_json, write_json

INDEX_PATH = DATA_DIR / "semantic_index.sqlite"
SEMANTIC_MANIFEST_PATH = DATA_DIR / "semantic_manifest.json"

CHUNKER_VERSION = 1
PREVIEW_CHARS = 200

_PAGE_RE = re.compile(r"^<!-- page (\d+) -->$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SemanticConfig:
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    # nomic-embed-text is trained with task prefixes; other models want "".
    doc_prefix: str = "search_document: "
    query_prefix: str = "search_query: "
    max_words: int = 1000
    min_words: int = 25
    k: int = 12
    min_score: float = 0.45
    per_paper: int = 3
    batch: int = 16
    # Also embed every abstract from the corpus (for semantic_report.py /
    # serve_search.py, which search papers without full text too).
    embed_abstracts: bool = True


def load_semantic_config(path: Path = CONFIG_PATH) -> SemanticConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("semantic", {})
    default = SemanticConfig()
    return SemanticConfig(**{
        k: type(getattr(default, k))(v) for k, v in raw.items()
        if hasattr(default, k)
    })


# ---------------------------------------------------------------------------
# Capability check
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: float = 600.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _model_matches(name: str, wanted: str) -> bool:
    """`nomic-embed-text` matches `nomic-embed-text:latest`."""
    if ":" not in wanted:
        wanted += ":latest"
    if ":" not in name:
        name += ":latest"
    return name == wanted


def check_capability(cfg: SemanticConfig, need_ollama: bool = True) -> dict:
    """Returns {"status": ok|no_numpy|no_ollama|no_model, "model_digest",
    "message"}. With need_ollama=False only numpy is checked (for searches
    that need no query embedding)."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return {"status": "no_numpy", "model_digest": None,
                "message": "numpy not installed — run `uv sync` in Code/"}
    if not need_ollama:
        return {"status": "ok", "model_digest": None, "message": ""}
    try:
        tags = _get_json(f"{cfg.ollama_url.rstrip('/')}/api/tags")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"status": "no_ollama", "model_digest": None,
                "message": f"Ollama not reachable at {cfg.ollama_url} ({e}) — "
                           "install/start Ollama to enable the semantic index"}
    for m in tags.get("models", []):
        if _model_matches(m.get("name", ""), cfg.embed_model):
            return {"status": "ok", "model_digest": m.get("digest"), "message": ""}
    return {"status": "no_model", "model_digest": None,
            "message": f"embedding model {cfg.embed_model} not pulled — run "
                       "`uv run python scripts/06_build_semantic_index.py --setup`"}


def pull_model(cfg: SemanticConfig) -> None:
    _post_json(f"{cfg.ollama_url.rstrip('/')}/api/pull",
               {"model": cfg.embed_model, "stream": False}, timeout=3600)


# ---------------------------------------------------------------------------
# Chunker: one chunk per page, long pages split, short pages merged forward
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    citekey: str
    page: int
    part: int
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def preview(self) -> str:
        return " ".join(self.text.split())[:PREVIEW_CHARS]


def split_pages(fulltext: str) -> list[tuple[int, str]]:
    """[(page number, text)] from a Fulltext/ file; frontmatter dropped."""
    marks = list(_PAGE_RE.finditer(fulltext))
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(fulltext)
        pages.append((int(m.group(1)), fulltext[m.end():end].strip()))
    return pages


def _split_units(text: str, max_words: int) -> list[str]:
    """Paragraphs; any paragraph over max_words falls back to lines, then
    to fixed word windows."""
    units = []
    for para in re.split(r"\n\s*\n", text):
        if len(para.split()) <= max_words:
            units.append(para)
            continue
        for line in para.split("\n"):
            words = line.split()
            for i in range(0, len(words), max_words):
                units.append(" ".join(words[i:i + max_words]))
    return [u for u in units if u.strip()]


def _split_long(text: str, max_words: int) -> list[str]:
    """Split into ceil(words/max_words) roughly equal parts at unit
    boundaries."""
    n_words = len(text.split())
    if n_words <= max_words:
        return [text]
    n_parts = math.ceil(n_words / max_words)
    target = n_words / n_parts
    parts, cur, cur_words = [], [], 0
    for unit in _split_units(text, max_words):
        w = len(unit.split())
        if cur and (cur_words + w > max_words
                    or (cur_words >= target and len(parts) < n_parts - 1)):
            parts.append("\n\n".join(cur))
            cur, cur_words = [], 0
        cur.append(unit)
        cur_words += w
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def chunk_fulltext(citekey: str, fulltext: str, max_words: int,
                   min_words: int) -> list[Chunk]:
    merged: list[tuple[int, str]] = []
    carry_page, carry = None, ""
    for page, text in split_pages(fulltext):
        if carry:
            text = f"{carry}\n\n{text}".strip()
            page = carry_page
        if len(text.split()) < min_words:
            carry_page, carry = page, text
            continue
        carry_page, carry = None, ""
        merged.append((page, text))
    if carry:  # short trailing page(s): attach to the previous chunk
        if merged:
            p, t = merged[-1]
            merged[-1] = (p, f"{t}\n\n{carry}")
        elif carry.strip():
            merged.append((carry_page, carry))

    chunks = []
    for page, text in merged:
        for part, piece in enumerate(_split_long(text, max_words), start=1):
            chunks.append(Chunk(citekey, page, part, piece))
    return chunks


def load_chunks(citekey: str, cfg: SemanticConfig) -> list[Chunk]:
    path = FULLTEXT_DIR / f"{citekey}.md"
    return chunk_fulltext(citekey, path.read_text(encoding="utf-8"),
                          cfg.max_words, cfg.min_words)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed(texts: list[str], cfg: SemanticConfig, prefix: str = "",
          progress=None):
    """Unit-normalised float32 matrix, one row per text."""
    import numpy as np
    url = f"{cfg.ollama_url.rstrip('/')}/api/embed"
    vectors = []
    for i in range(0, len(texts), cfg.batch):
        batch = [prefix + t for t in texts[i:i + cfg.batch]]
        data = _post_json(url, {"model": cfg.embed_model, "input": batch,
                                "truncate": True})
        vectors.extend(data["embeddings"])
        if progress:
            progress(min(i + cfg.batch, len(texts)), len(texts))
    arr = np.array(vectors, dtype=np.float32).reshape(len(vectors), -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / np.clip(norms, 1e-12, None)


# ---------------------------------------------------------------------------
# Index I/O
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    citekey    TEXT NOT NULL,
    page       INTEGER NOT NULL,
    part       INTEGER NOT NULL,
    chunk_hash TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    preview    TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    PRIMARY KEY (citekey, page, part)
);
CREATE TABLE IF NOT EXISTS abstracts (
    citekey       TEXT PRIMARY KEY,
    abstract_hash TEXT NOT NULL,
    word_count    INTEGER NOT NULL,
    preview       TEXT NOT NULL,
    embedding     BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_index(path: Path = INDEX_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def read_meta(conn: sqlite3.Connection) -> dict:
    return dict(conn.execute("SELECT key, value FROM meta"))


def write_meta(conn: sqlite3.Connection, meta: dict) -> None:
    conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     [(k, str(v)) for k, v in meta.items()])


def indexed_papers(conn: sqlite3.Connection) -> dict[str, str]:
    """citekey -> text_hash of what is in the index."""
    return dict(conn.execute("SELECT DISTINCT citekey, text_hash FROM chunks"))


def replace_paper(conn: sqlite3.Connection, citekey: str, text_hash: str,
                  chunks: list[Chunk], vectors) -> None:
    conn.execute("DELETE FROM chunks WHERE citekey = ?", (citekey,))
    conn.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(c.citekey, c.page, c.part, c.chunk_hash, text_hash, c.word_count,
          c.preview, vec.astype("float32").tobytes())
         for c, vec in zip(chunks, vectors)],
    )


def delete_paper(conn: sqlite3.Connection, citekey: str) -> None:
    conn.execute("DELETE FROM chunks WHERE citekey = ?", (citekey,))


def indexed_abstracts(conn: sqlite3.Connection) -> dict[str, str]:
    """citekey -> abstract_hash of what is in the index."""
    return dict(conn.execute("SELECT citekey, abstract_hash FROM abstracts"))


def replace_abstract(conn: sqlite3.Connection, citekey: str,
                     abstract_hash: str, text: str, vector) -> None:
    preview = " ".join(text.split())[:PREVIEW_CHARS]
    conn.execute(
        "INSERT OR REPLACE INTO abstracts VALUES (?, ?, ?, ?, ?)",
        (citekey, abstract_hash, len(text.split()), preview,
         vector.astype("float32").tobytes()),
    )


def delete_abstract(conn: sqlite3.Connection, citekey: str) -> None:
    conn.execute("DELETE FROM abstracts WHERE citekey = ?", (citekey,))


def load_matrix(conn: sqlite3.Connection):
    """(rows, matrix): rows are dicts without the embedding, in matrix order."""
    import numpy as np
    rows, blobs = [], []
    for ck, page, part, wc, preview, blob in conn.execute(
            "SELECT citekey, page, part, word_count, preview, embedding "
            "FROM chunks ORDER BY citekey, page, part"):
        rows.append({"citekey": ck, "page": page, "part": part,
                     "word_count": wc, "preview": preview})
        blobs.append(np.frombuffer(blob, dtype=np.float32))
    matrix = np.vstack(blobs) if blobs else np.zeros((0, 0), dtype=np.float32)
    return rows, matrix


def load_abstract_matrix(conn: sqlite3.Connection):
    """(rows, matrix) over the abstracts table, in matrix order."""
    import numpy as np
    rows, blobs = [], []
    for ck, wc, preview, blob in conn.execute(
            "SELECT citekey, word_count, preview, embedding "
            "FROM abstracts ORDER BY citekey"):
        rows.append({"citekey": ck, "word_count": wc, "preview": preview})
        blobs.append(np.frombuffer(blob, dtype=np.float32))
    matrix = np.vstack(blobs) if blobs else np.zeros((0, 0), dtype=np.float32)
    return rows, matrix


# ---------------------------------------------------------------------------
# Manifest (what the skill reads)
# ---------------------------------------------------------------------------

def read_semantic_manifest() -> dict:
    return read_json(SEMANTIC_MANIFEST_PATH, {}) or {}


def write_semantic_manifest(**fields) -> dict:
    manifest = read_semantic_manifest()
    manifest.update(fields)
    manifest["last_run"] = datetime.now().isoformat(timespec="seconds")
    write_json(SEMANTIC_MANIFEST_PATH, manifest)
    return manifest

"""OpenAlex client for citation gap analysis — shared by citation_gaps.py.
See "Citation gaps" in Code/PIPELINE_PLAN.md.

Everything here is derived and optional: the data lives in the gitignored
Code/data/, the network is required, and without it the caller stands down.
HTTP goes through stdlib urllib (no new dependencies) with a polite delay,
retries with backoff, and the OpenAlex "polite pool" mailto when configured.

Work records are cached in Code/data/openalex_cache.json with a per-entry
TTL. Citation lists (the `cites:` queries) are never cached across runs:
new citers appearing is exactly what a re-run is for. JSON is fine at this
scale (~300 bytes per cached work); revisit as sqlite only past ~20k entries.

Doctests are the unit tests; from Code/scripts/:
    uv run python -m lib.openalex
"""

import json
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .config import CONFIG_PATH
from .matching import normalize_doi
from .store import DATA_DIR, read_json, write_json

API = "https://api.openalex.org"
CACHE_PATH = DATA_DIR / "openalex_cache.json"
CACHE_SCHEMA = 1

# Core fields for every work; passes that need more add to this.
WORK_SELECT = "id,doi,title,publication_year,authorships,cited_by_count"
CHUNK = 50          # ids/dois per pipe-OR filter (OpenAlex caps around 100)
CITES_CHUNK = 40    # cluster ids per cites: filter
PER_PAGE = 200
MAX_BASIC_PAGES = 10_000 // PER_PAGE   # page= pagination reaches 10k results
# Backward pass: refs cited by >=2 cluster papers are always resolved; refs
# cited once are resolved in ascending-ID order up to this cap, so a review
# paper with a 500-entry bibliography cannot blow the call budget. Rarely
# worth configuring; bump here if ever hit in practice (a note is emitted).
MAX_BACKWARD_RESOLVE = 500


class OpenAlexError(Exception):
    """A request that still failed after retries."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OpenAlexConfig:
    mailto: str = ""              # polite pool; "" works, slower lane
    max_per_direction: int = 10
    min_cluster_cites: int = 2
    timeout: float = 30.0
    delay: float = 0.15           # pause before each call (cap is 10/s)
    retries: int = 3
    cache_days: int = 30
    max_forward_pages: int = 50   # x PER_PAGE citing works, then truncate


def load_openalex_config(path: Path = CONFIG_PATH) -> OpenAlexConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("openalex", {})
    default = OpenAlexConfig()
    return OpenAlexConfig(**{
        k: type(getattr(default, k))(v) for k, v in raw.items()
        if hasattr(default, k)
    })


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

CALLS = 0   # API calls this process has made, for logs and --measure


def _get_json(url: str, cfg: OpenAlexConfig):
    """GET with polite delay, mailto, and backoff on 429/5xx/network errors."""
    global CALLS
    CALLS += 1
    if cfg.mailto:
        url += ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(cfg.mailto)
    last = None
    for attempt in range(cfg.retries + 1):
        time.sleep(cfg.delay if attempt == 0 else 0)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "lit-vault citation_gaps (https://github.com/ADuncan2/lit-vault)"})
            with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise OpenAlexError(f"HTTP {e.code} from OpenAlex: {url}") from e
            last = e
            retry_after = e.headers.get("Retry-After")
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            retry_after = None
        if attempt < cfg.retries:
            wait = 2 ** attempt
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            time.sleep(wait)
    raise OpenAlexError(f"OpenAlex request failed after {cfg.retries + 1} "
                        f"attempts ({last}): {url}")


def check_capability(cfg: OpenAlexConfig) -> dict:
    """{"status": "ok" | "no_network", "message": ...}. A missing mailto is
    a note in the ok message, never a failure."""
    probe = OpenAlexConfig(mailto=cfg.mailto, timeout=5.0, delay=0.0, retries=0)
    try:
        _get_json(f"{API}/works?per-page=1", probe)
    except OpenAlexError as e:
        return {"status": "no_network",
                "message": f"OpenAlex not reachable ({e}) — citation gaps "
                           "need the network; everything else works without"}
    note = "" if cfg.mailto else " (set [openalex] mailto for the faster polite pool)"
    return {"status": "ok", "message": f"OpenAlex reachable{note}"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def wid(url_or_id: str) -> str:
    """Short OpenAlex work ID.

    >>> wid("https://openalex.org/W3193942116")
    'W3193942116'
    >>> wid("W42")
    'W42'
    """
    return str(url_or_id).rstrip("/").rsplit("/", 1)[-1]


def reconstruct_abstract(inverted) -> str:
    """Plain text from OpenAlex's abstract_inverted_index; "" when absent.

    >>> reconstruct_abstract({"beta": [1], "Alpha": [0], "beta.": [2]})
    'Alpha beta beta.'
    >>> reconstruct_abstract(None)
    ''
    """
    if not inverted:
        return ""
    slots = {}
    for word, positions in inverted.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[p] for p in sorted(slots))


def work_authors(work: dict, cap: int = 6) -> list:
    """Author display names from authorships, at most `cap`."""
    names = [a.get("author", {}).get("display_name", "")
             for a in work.get("authorships", [])]
    return [n for n in names if n][:cap]


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache() -> dict:
    cache = read_json(CACHE_PATH, None)
    if not cache or cache.get("schema") != CACHE_SCHEMA:
        return {"schema": CACHE_SCHEMA, "works": {}, "doi_to_id": {}}
    return cache


def save_cache(cache: dict) -> None:
    write_json(CACHE_PATH, cache)


def _fresh(entry: dict, cfg: OpenAlexConfig) -> bool:
    try:
        fetched = date.fromisoformat(entry.get("fetched", ""))
    except ValueError:
        return False
    return date.today() - fetched <= timedelta(days=cfg.cache_days)


def _cache_get(cache: dict, short_id: str, cfg: OpenAlexConfig, need=()) -> dict | None:
    """A fresh cached work that carries every field in `need`, or None."""
    entry = cache["works"].get(short_id)
    if not entry or not _fresh(entry, cfg):
        return None
    work = entry["work"]
    if any(k not in work for k in need):
        return None
    return work


def _cache_put(cache: dict, work: dict) -> dict:
    """Store (merging into any prior fields) and return the merged record."""
    short = wid(work["id"])
    prior = cache["works"].get(short, {}).get("work", {})
    merged = {**prior, **work, "id": short}
    cache["works"][short] = {"fetched": date.today().isoformat(), "work": merged}
    d = normalize_doi(work.get("doi"))
    if d:
        cache["doi_to_id"][d] = short
    return merged


# ---------------------------------------------------------------------------
# API layer — every function returns plain work dicts with short "id"s
# ---------------------------------------------------------------------------

def _filter_query(field: str, values: list, select: str) -> str:
    joined = "|".join(urllib.parse.quote(v, safe="()/") for v in values)
    return (f"{API}/works?filter={field}:{joined}"
            f"&select={select}&per-page={max(len(values), 25)}")


def works_by_dois(dois: list, cfg: OpenAlexConfig, cache: dict, *,
                  with_refs: bool = False, force: bool = False) -> dict:
    """{normalised_doi: work} for every DOI OpenAlex knows. Unknown DOIs are
    simply absent from the result."""
    select = WORK_SELECT + (",referenced_works" if with_refs else "")
    need = ("referenced_works",) if with_refs else ()
    out, missing = {}, []
    for doi in dois:
        d = normalize_doi(doi)
        if not d or "|" in d:       # "|" is the filter separator; unqueryable
            continue
        work = None
        if not force:
            short = cache["doi_to_id"].get(d)
            work = _cache_get(cache, short, cfg, need) if short else None
        if work is not None:
            out[d] = work
        else:
            missing.append(d)
    for chunk in _chunks(sorted(set(missing)), CHUNK):
        data = _get_json(_filter_query("doi", chunk, select), cfg)
        for w in data.get("results", []):
            merged = _cache_put(cache, w)
            d = normalize_doi(w.get("doi"))
            if d:
                out[d] = merged
    return out


def works_by_ids(ids: list, cfg: OpenAlexConfig, cache: dict, *,
                 select: str = WORK_SELECT, need=(), force: bool = False) -> dict:
    """{queried_short_id: work}. Merged works are indexed under both the
    queried and the returned ID; IDs OpenAlex no longer resolves are absent."""
    out, missing = {}, []
    for raw in ids:
        short = wid(raw)
        work = None if force else _cache_get(cache, short, cfg, need)
        if work is not None:
            out[short] = work
        else:
            missing.append(short)
    for chunk in _chunks(sorted(set(missing)), CHUNK):
        data = _get_json(_filter_query("openalex_id", chunk, select), cfg)
        by_returned = {}
        for w in data.get("results", []):
            merged = _cache_put(cache, w)
            by_returned[merged["id"]] = merged
        for short in chunk:
            if short in by_returned:
                out[short] = by_returned[short]
        # merged-away IDs: OpenAlex redirects singles but not filter queries;
        # anything unreturned here is treated as unresolvable and dropped
    return out


def work_by_title(title: str, cfg: OpenAlexConfig) -> dict | None:
    """The work whose normalised title exactly equals the query's, or None.
    Deliberately never fuzzy — a wrong work would poison both passes."""
    from .matching import normalize_title
    want = normalize_title(title)
    if not want:
        return None
    q = urllib.parse.quote(title)
    data = _get_json(f"{API}/works?filter=title.search:{q}"
                     f"&select={WORK_SELECT},referenced_works&per-page=5", cfg)
    for w in data.get("results", []):
        if normalize_title(w.get("title")) == want:
            w = dict(w)
            w["id"] = wid(w["id"])
            return w
    return None


def citing_works(cluster_ids: list, cfg: OpenAlexConfig) -> tuple:
    """(works citing any cluster work, truncated?). Each work carries
    referenced_works, so the caller counts cluster coverage locally.
    Not cached: citation lists are the one thing that changes.

    Page-based pagination, not cursors: OpenAlex now answers unauthenticated
    cursor requests with 401, while page= works and reaches 10,000 results
    (MAX_BASIC_PAGES x PER_PAGE) — plenty, and max_forward_pages caps below
    that anyway. Past the cap the listing is truncated, never wrong: every
    fetched citer still gets an exact cluster count."""
    select = WORK_SELECT + ",referenced_works"
    seen, works, truncated = set(), [], False
    last_page = min(cfg.max_forward_pages, MAX_BASIC_PAGES)
    for chunk in _chunks(sorted(set(wid(i) for i in cluster_ids)), CITES_CHUNK):
        joined = "|".join(chunk)
        page = 1
        while True:
            data = _get_json(
                f"{API}/works?filter=cites:{joined}&select={select}"
                f"&per-page={PER_PAGE}&page={page}", cfg)
            results = data.get("results", [])
            for w in results:
                short = wid(w["id"])
                if short not in seen:
                    seen.add(short)
                    w = dict(w)
                    w["id"] = short
                    works.append(w)
            count = data.get("meta", {}).get("count", 0)
            if not results or page * PER_PAGE >= count:
                break
            if page >= last_page:
                truncated = True
                break
            page += 1
    return works, truncated


if __name__ == "__main__":
    import doctest
    raise SystemExit(doctest.testmod(verbose=True).failed)

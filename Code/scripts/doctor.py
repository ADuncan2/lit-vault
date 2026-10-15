"""
doctor.py — deterministic health checks for a lit-vault.

The conversation owns decisions; this script owns checks. The setup skill
ends with it, the refresh skill runs it after every rebuild, and any "it's
broken" report starts with it. Each check prints PASS, WARN or FAIL, and
every WARN/FAIL carries a one-line fix. Exit status is 1 if anything
FAILed, else 0.

Stdlib only, so it runs before `uv sync` and on a bare clone.

Modes:
  (default)        config, export, build, semantic-index and report checks
  --prereqs        only the prerequisites: Python, uv, Zotero running,
                   Better BibTeX installed (setup step 1, before any config)
  --compare OLD    also report the library delta against an earlier
                   papers.json ("+12 papers, 3 updated, 1 removed")

Usage:
    python Code/scripts/doctor.py [--prereqs] [--compare OLD_PAPERS_JSON]
                                  [--max-export-age DAYS]
"""

import argparse
import json
import re
import shutil
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

from lib.bbt import find_export, get_citekey, get_item_type, load_bibtex_export
from lib.config import CONFIG_PATH, load_config
from lib.store import CORPUS_PATH, DATA_DIR, ROOT, read_json, read_jsonl

ZOTERO_PING = "http://127.0.0.1:23119/connector/ping"
BBT_PROBE = "http://127.0.0.1:23119/better-bibtex/cayw?probe=true"
SKIP_ITEM_TYPES = {"note", "annotation", "attachment"}  # as 01_build_corpus.py
ITEM_URI_RE = re.compile(r"^zotero://select/(library|groups/\d+)/items/[A-Z0-9]{8}$")
PDF_URI_RE = re.compile(r"^zotero://open-pdf/(library|groups/\d+)/items/[A-Z0-9]{8}$")
COLOUR_RE = re.compile(r"^#[0-9a-f]{6}$")
# stdlib-only, so read the manifest directly rather than import lib.semantic
SEMANTIC_MANIFEST = DATA_DIR / "semantic_manifest.json"


class Report:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def ok(self, msg: str) -> None:
        print(f"  PASS  {msg}")

    def warn(self, msg: str, fix: str) -> None:
        self.warned += 1
        print(f"  WARN  {msg}\n        fix: {fix}")

    def fail(self, msg: str, fix: str) -> None:
        self.failed += 1
        print(f"  FAIL  {msg}\n        fix: {fix}")

    def info(self, msg: str) -> None:
        print(f"  ----  {msg}")

    def section(self, name: str) -> None:
        print(f"\n{name}")


def http_get(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.read().decode("utf-8", "replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_prereqs(rep: Report, strict: bool) -> None:
    """Prerequisites. FAIL in --prereqs mode, WARN otherwise (a refresh
    with Zotero closed is fine as long as the export is current)."""
    flag = rep.fail if strict else rep.warn
    rep.section("Prerequisites")
    if sys.version_info >= (3, 11):
        rep.ok(f"Python {sys.version.split()[0]}")
    else:
        rep.fail(f"Python {sys.version.split()[0]} is older than 3.11",
                 "install uv (it fetches a suitable Python): see README")
    if shutil.which("uv"):
        rep.ok("uv on PATH")
    else:
        flag("uv not found on PATH",
             "install uv: https://docs.astral.sh/uv/getting-started/installation/")
    if http_get(ZOTERO_PING) is not None:
        rep.ok("Zotero desktop is running")
        probe = http_get(BBT_PROBE)
        if probe and probe.strip() == "ready":
            rep.ok("Better BibTeX is installed")
        else:
            flag("Better BibTeX not detected",
                 "install it: https://retorque.re/zotero-better-bibtex/installation/")
    else:
        flag("Zotero desktop is not running (or is older than Zotero 7)",
             "start Zotero; install it from https://www.zotero.org/download/ if needed")


def check_config(rep: Report, max_age: float):
    rep.section("Config")
    if not CONFIG_PATH.exists():
        rep.fail(f"{CONFIG_PATH} not found",
                 "run the lit-vault-setup skill, or copy config.example.toml to config.toml")
        return None
    try:
        with open(CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)
        cfg = load_config()
    except (tomllib.TOMLDecodeError, ValueError, TypeError) as e:
        rep.fail(f"config.toml does not parse: {e}", "fix the line named above")
        return None
    rep.ok("config.toml parses")

    if (cfg.zotero_data / "zotero.sqlite").exists():
        rep.ok(f"Zotero data directory: {cfg.zotero_data}")
    else:
        rep.fail(f"no zotero.sqlite in {cfg.zotero_data}",
                 "set [zotero] data_dir to Zotero → Settings → Advanced → "
                 "Files and Folders → Data Directory Location")

    try:
        cfg.export = find_export(cfg.export)
        rep.ok(f"Better BibTeX export: {cfg.export}")
        age = (time.time() - cfg.export.stat().st_mtime) / 86400
        if age > max_age:
            rep.warn(f"export last written {age:.0f} days ago",
                     "fine if the library hasn't changed; otherwise check the "
                     "auto-export in Zotero → Settings → Better BibTeX → Automatic export")
    except ValueError as e:
        rep.fail(str(e), "see docs/setup-bbt.md, or set [zotero] export in config.toml")

    mv = raw.get("main_vault", {}).get("path", "")
    if not mv:
        rep.info("main vault: not configured (optional)")
    elif cfg.main_vault and cfg.main_vault.exists():
        rep.ok(f"main vault: {cfg.main_vault}")
    else:
        rep.fail(f"main vault not found: {mv}",
                 'fix [main_vault] path, or set it to "" to run without one')

    bad = [c for c in cfg.highlights if not COLOUR_RE.match(c)]
    if bad:
        rep.fail(f"[highlights] keys are not #rrggbb colours: {', '.join(bad)}",
                 'write each colour as a quoted hex string, e.g. "#ffd400"')
    else:
        rep.ok(f"[highlights]: {len(cfg.highlights)} colours mapped")
    return cfg


def export_citekeys(cfg) -> set[str] | None:
    """Citekeys 01_build_corpus.py would keep from the export."""
    try:
        items = load_bibtex_export(cfg.export)["items"]
    except (OSError, ValueError):
        return None
    return {get_citekey(it) for it in items
            if get_item_type(it) not in SKIP_ITEM_TYPES
            and get_citekey(it) and " ".join(it.get("title", "").split())}


def check_build(rep: Report, cfg) -> dict | None:
    rep.section("Build")
    rerun = "re-run the pipeline (the lit-vault-refresh skill, or 01→05 in Code/README.md)"
    corpus = read_jsonl(CORPUS_PATH)
    if not corpus:
        rep.fail("no corpus yet (Code/data/corpus.jsonl)", rerun)
        return None
    corpus_keys = {r["citekey"] for r in corpus}

    if cfg and cfg.export.is_file():
        exp = export_citekeys(cfg)
        if exp is None:
            rep.fail(f"export does not parse as Better BibTeX JSON: {cfg.export}",
                     "re-export with the format 'Better BibTeX JSON' (docs/setup-bbt.md)")
        elif exp == corpus_keys:
            rep.ok(f"corpus matches the export ({len(exp)} papers)")
        else:
            rep.fail(f"export has {len(exp)} papers, corpus has {len(corpus_keys)} "
                     f"(+{len(exp - corpus_keys)} / -{len(corpus_keys - exp)})", rerun)

    for name in ("INDEX.md", "Home.md"):
        if not (ROOT / name).exists():
            rep.fail(f"{name} missing", rerun)

    pj_path = ROOT / "papers.json"
    if not pj_path.exists():
        rep.fail("papers.json missing", rerun)
        return None
    try:
        papers = json.loads(pj_path.read_text(encoding="utf-8"))["papers"]
    except (ValueError, KeyError) as e:
        rep.fail(f"papers.json does not parse: {e}", rerun)
        return None
    pj = {p["citekey"]: p for p in papers}

    notes = {p.name[1:-3] for p in (ROOT / "Papers").glob("@*.md")}
    if notes == set(pj):
        rep.ok(f"Papers/ and papers.json agree ({len(notes)} papers)")
    else:
        rep.fail(f"Papers/ has {len(notes)} notes, papers.json {len(pj)} rows "
                 f"({len(notes - set(pj))} notes without a row, "
                 f"{len(set(pj) - notes)} rows without a note)",
                 "re-run 05_build_papers_json.py (after 04)")
    if set(pj) == corpus_keys:
        rep.ok("papers.json matches the corpus")
    else:
        rep.fail("papers.json and the corpus list different papers",
                 "re-run 04_build_vault.py then 05_build_papers_json.py")

    bad_item = [k for k, p in pj.items()
                if p.get("zotero_item") and not ITEM_URI_RE.match(p["zotero_item"])]
    bad_pdf = [k for k, p in pj.items()
               if p.get("zotero_pdf") and not PDF_URI_RE.match(p["zotero_pdf"])]
    no_item = [k for k, p in pj.items() if not p.get("zotero_item")]
    if bad_item or bad_pdf:
        rep.fail(f"malformed zotero:// links: {', '.join((bad_item + bad_pdf)[:5])}"
                 + (" …" if len(bad_item + bad_pdf) > 5 else ""),
                 "check the export is 'Better BibTeX JSON', then re-run the pipeline")
    else:
        rep.ok("zotero:// links well-formed")
    if no_item:
        rep.warn(f"{len(no_item)} papers have no zotero://select link",
                 "re-export as 'Better BibTeX JSON' — other formats omit it")

    n_key = sum(1 for p in pj.values() if p.get("key_paper"))
    n_ft = sum(1 for p in pj.values() if p.get("fulltext_status") == "ok")
    rep.info(f"{len(pj)} papers · {n_key} key · {n_ft} with full text · "
             f"{sum(1 for p in pj.values() if p.get('has_annotations'))} with highlights · "
             f"{sum(1 for p in pj.values() if p.get('has_my_note'))} with reading notes")
    return pj


def check_semantic(rep: Report) -> None:
    """Optional index: report its manifest status, never FAIL."""
    rep.section("Semantic index (optional)")
    m = read_json(SEMANTIC_MANIFEST, {}) or {}
    status = m.get("status")
    if status is None:
        rep.info("not set up — see [semantic] in config.example.toml")
    elif status == "ok":
        rep.ok(f"{m.get('papers')} papers, {m.get('chunks')} chunks, "
               f"{m.get('embed_model')}")
    elif status == "stale":
        rep.warn("index is stale (model or settings changed)",
                 "uv run python scripts/06_build_semantic_index.py --force")
    else:
        rep.info(f"off ({status}): {m.get('message', '')}")


def check_citation_gaps(rep: Report) -> None:
    """Optional OpenAlex gap analysis: reachability and config, never FAIL."""
    rep.section("Citation gaps (optional)")
    if http_get("https://api.openalex.org/works?per-page=1") is None:
        rep.info("off (OpenAlex not reachable) — citation_gaps.py stands down")
        return
    rep.ok("OpenAlex reachable")
    try:
        with open(CONFIG_PATH, "rb") as f:
            oa = tomllib.load(f).get("openalex", {})
    except OSError:
        oa = {}
    if oa.get("mailto"):
        rep.ok(f"polite pool: {oa['mailto']}")
    else:
        rep.info("no [openalex] mailto set — works, but the polite pool is "
                 "faster (config.example.toml)")


def check_reports(rep: Report) -> None:
    rep.section("Reports")
    for rel in ("Code/scripts/render_report.py", "Code/templates/survey.template.html"):
        if (ROOT / rel).exists():
            rep.ok(f"{rel} present")
        else:
            rep.fail(f"{rel} missing", "restore it: git checkout -- " + rel)
    summaries = ROOT / "Summaries"
    try:
        summaries.mkdir(exist_ok=True)
        probe = summaries / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rep.ok("Summaries/ writable")
    except OSError as e:
        rep.fail(f"Summaries/ not writable: {e}", "check the folder's permissions")


def compare(old_path: Path, new: dict) -> None:
    """Print the library delta against an earlier papers.json."""
    try:
        old = {p["citekey"]: p for p in
               json.loads(old_path.read_text(encoding="utf-8"))["papers"]}
    except (OSError, ValueError, KeyError):
        old = {}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    updated = sorted(k for k in set(new) & set(old) if new[k] != old[k])
    print(f"\nDelta: +{len(added)} papers, {len(updated)} updated, "
          f"{len(removed)} removed")
    for label, keys in (("added", added), ("removed", removed)):
        if keys:
            print(f"  {label}: {', '.join(keys[:20])}" + (" …" if len(keys) > 20 else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Health checks for a lit-vault.")
    ap.add_argument("--prereqs", action="store_true",
                    help="check prerequisites only (Python, uv, Zotero, Better BibTeX)")
    ap.add_argument("--compare", type=Path, metavar="OLD_PAPERS_JSON",
                    help="report the delta against an earlier papers.json")
    ap.add_argument("--max-export-age", type=float, default=30, metavar="DAYS",
                    help="warn if the export is older than this (default 30)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):  # Windows pipes default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rep = Report()
    print(f"lit-vault doctor — {ROOT}")
    check_prereqs(rep, strict=args.prereqs)
    if not args.prereqs:
        cfg = check_config(rep, args.max_export_age)
        pj = check_build(rep, cfg)
        check_semantic(rep)
        check_citation_gaps(rep)
        check_reports(rep)
        if args.compare and pj is not None:
            compare(args.compare, pj)

    print(f"\n{'FAILED' if rep.failed else 'OK'}: {rep.failed} failed, "
          f"{rep.warned} warnings")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

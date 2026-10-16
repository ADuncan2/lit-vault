"""
serve_search.py — local web UI for the deterministic semantic search.

Serves the vault root at http://127.0.0.1:8787 (so lit-vault.html and the
Summaries/ archive work as pages, with the dropdown and Zotero links) plus a
search form at /search. Submitting the form runs semantic_report.run_search —
embed the query through local Ollama, score every indexed page chunk and
abstract, render the report — and redirects the browser to the finished page.
No LLM, no network beyond localhost (unless the gap analysis is ticked,
which calls OpenAlex).

Local only: binds 127.0.0.1, never 0.0.0.0.

Usage (uv, for numpy):
    uv run python scripts/serve_search.py [--port 8787] [--no-browser]
"""

import argparse
import html
import threading
import urllib.parse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import semantic_report
from lib.semantic import check_capability, load_semantic_config, read_semantic_manifest
from lib.store import ROOT

FORM_PATHS = ("/search", "/search/")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lit vault — semantic search</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg: #f6f7f8; --surface: #ffffff; --fg: #1c2328; --muted: #5b6770;
  --line: #dde2e6; --accent: #0f5f6b; --accent-soft: #e2f0f1;
  --flag: #9a5b00; --flag-soft: #fbf0dc;
  --f-display: "Source Serif 4", Georgia, serif;
  --f-body: "IBM Plex Sans", system-ui, sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg: #12171a; --surface: #1a2125; --fg: #e3e8eb; --muted: #97a4ad; --line: #2c363c;
  --accent: #6cc3cd; --accent-soft: #173238; --flag: #e8b45c; --flag-soft: #3a2c14; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--fg); font-family: var(--f-body); font-size: 15px; line-height: 1.55; margin: 0; }}
.wrap {{ max-width: 640px; margin: 0 auto; padding: 32px 16px 56px; display: grid; gap: 20px; }}
h1 {{ font-family: var(--f-display); font-weight: 600; font-size: 1.7rem; margin: 0; }}
.lede {{ color: var(--muted); margin: 0; }}
form {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 18px; display: grid; gap: 14px; }}
label {{ display: grid; gap: 4px; font-size: .85rem; font-weight: 500; }}
label .hint {{ font-weight: 400; color: var(--muted); }}
textarea, input[type=number], select {{ font: 400 .95rem/1.4 var(--f-body); color: var(--fg); background: var(--bg); border: 1px solid var(--line); border-radius: 4px; padding: 8px 10px; width: 100%; }}
textarea {{ min-height: 72px; resize: vertical; }}
.row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
.check {{ display: flex; gap: 8px; align-items: baseline; font-size: .85rem; }}
.check input {{ width: auto; }}
button {{ font: 600 .9rem/1 var(--f-body); background: var(--accent); color: var(--surface); border: 0; border-radius: 4px; padding: 11px 18px; cursor: pointer; justify-self: start; }}
button:hover {{ filter: brightness(1.1); }}
.status {{ font-family: var(--f-mono); font-size: .76rem; color: var(--muted); }}
.error {{ background: var(--flag-soft); color: var(--flag); border-radius: 6px; padding: 12px 16px; font-size: .9rem; white-space: pre-wrap; }}
.note {{ border: 1px dashed var(--line); border-radius: 6px; padding: 12px 16px; font-size: .85rem; color: var(--muted); }}
.note p {{ margin: 0 0 6px; }} .note p:last-child {{ margin: 0; }}
a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Semantic search</h1>
  <p class="lede">Rank your library against a question using local embeddings only —
  no language model, nothing leaves this machine. The result is the usual
  report page with Zotero links.</p>
  {error}
  <form method="post" action="/search">
    <label>Question
      <textarea name="query" required placeholder="how do operators plan under demand uncertainty">{query}</textarea>
    </label>
    <div class="row">
      <label>Min score
        <input type="number" name="min_score" step="0.01" min="0" max="1" value="{min_score}">
        <span class="hint">cosine similarity; typical hits land ~0.60–0.80</span>
      </label>
      <label>Max papers
        <input type="number" name="max_papers" min="1" max="200" value="{max_papers}">
      </label>
      <label>Search in
        <select name="scope">
          <option value="both"{sel_both}>full text + abstracts</option>
          <option value="fulltext"{sel_fulltext}>full text only</option>
          <option value="abstracts"{sel_abstracts}>abstracts only</option>
        </select>
      </label>
    </div>
    <label class="check"><input type="checkbox" name="gaps" value="1"{gaps_checked}>
      Add OpenAlex gap analysis (needs the network; adds a minute or two)</label>
    <button>Search</button>
    <p class="status">{status}</p>
  </form>
  <div class="note">
    <p>Scores are leads, never evidence — open the papers. Results are grouped by
    where the best match was found (a full-text page, or the abstract when the
    full text isn't extracted or didn't match as well).</p>
    <p><a href="/lit-vault.html">Latest report</a> — every search is archived in
    its dropdown.</p>
  </div>
</div>
</body>
</html>
"""


def form_page(values: dict | None = None, error: str = "") -> str:
    v = {"query": "", "min_score": "", "max_papers": "30", "scope": "both",
         "gaps": False, **(values or {})}
    if not v["min_score"]:
        v["min_score"] = f"{load_semantic_config().min_score:g}"
    return PAGE.format(
        error=f'<div class="error">{html.escape(error)}</div>' if error else "",
        query=html.escape(str(v["query"])),
        min_score=html.escape(str(v["min_score"])),
        max_papers=html.escape(str(v["max_papers"])),
        sel_both=" selected" if v["scope"] == "both" else "",
        sel_fulltext=" selected" if v["scope"] == "fulltext" else "",
        sel_abstracts=" selected" if v["scope"] == "abstracts" else "",
        gaps_checked=" checked" if v["gaps"] else "",
        status=html.escape(index_status()),
    )


def index_status() -> str:
    m = read_semantic_manifest()
    if m.get("status") == "ok":
        line = (f"index: {m.get('papers', '?')} papers / "
                f"{m.get('chunks', '?')} chunks / "
                f"{m.get('abstracts', 0)} abstracts · {m.get('embed_model', '')}")
    else:
        line = f"index: {m.get('status', 'unknown')} — {m.get('message', 'run step 06')}"
    cap = check_capability(load_semantic_config())
    if cap["status"] != "ok":
        line += f" · {cap['message']}"
    return line


class Handler(SimpleHTTPRequestHandler):
    def send_page(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path in ("/",) + FORM_PATHS:
            return self.send_page(form_page())
        return super().do_GET()

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path not in FORM_PATHS:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        fields = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        get = lambda k, d="": (fields.get(k) or [d])[0]  # noqa: E731
        values = {"query": get("query"), "min_score": get("min_score"),
                  "max_papers": get("max_papers", "30"),
                  "scope": get("scope", "both"), "gaps": bool(get("gaps"))}
        try:
            min_score = float(values["min_score"]) if values["min_score"] else None
            max_papers = max(1, int(values["max_papers"] or 30))
        except ValueError:
            return self.send_page(form_page(values, "min score and max papers "
                                                    "must be numbers"), 400)

        result = semantic_report.run_search(
            values["query"], min_score, max_papers, values["scope"],
            gaps=values["gaps"])
        if not result["ok"]:
            return self.send_page(form_page(values, result["error"]), 200)
        target = "/" + urllib.parse.quote(result["archived_html"]
                                          or "lit-vault.html")
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> int:
    ap = argparse.ArgumentParser(description="Local web UI for semantic search.")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true",
                    help="don't open the form in the default browser")
    args = ap.parse_args()

    try:
        load_semantic_config()
    except FileNotFoundError:
        print("error: Code/config.toml missing — run the lit-vault-setup "
              "skill first")
        return 1

    handler = partial(Handler, directory=str(ROOT))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as e:
        print(f"error: cannot bind 127.0.0.1:{args.port} ({e}) — "
              "is another serve_search.py running? Try --port", flush=True)
        return 1
    url = f"http://127.0.0.1:{args.port}/search"
    print(f"Serving the vault at {url}  (Ctrl-C to stop)")
    print(index_status())
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

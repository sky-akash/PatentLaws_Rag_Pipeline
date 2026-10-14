"""Download the European patent law texts from epo.org: list every page from the site's own table of contents, fetch
each one politely (1 request a second, as robots.txt asks), and keep only the legal text."""
import json, re, sys, time
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from . import config

BASE = "https://www.epo.org"

# The corpora we know how to collect. 'pages' decides which entries of the table of contents are law text.
SOURCES = {
    # the European Patent Convention: Articles, Implementing Regulations (Rules), Rules relating to Fees, Protocol on Art. 69
    "epc": {
        "title": "European Patent Convention (17th edition, as in force)",
        "index": "/en/legal/epc/2020/index.html",
        "pages": re.compile(r"^/en/legal/epc/2020/(?:[arf]\d+[a-z]?|protinta69)\.html$"),
    },
    # the EPO's Guidelines for Examination: how the Office applies the EPC, with examples
    "guidelines": {
        "title": "Guidelines for Examination in the EPO (March 2026)",
        "index": "/en/legal/guidelines-epc/2026/index.html",
        "pages": re.compile(r"^/en/legal/guidelines-epc/2026/(?:[a-h]_[ivx]+(?:_[\d_]+)?|foreword(?:_[\d_]+)?)\.html$"),
    },
}

def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True)

def _get(client: httpx.Client, url: str, tries: int = 4) -> str:
    # one page, with a few patient retries (the EPO site sometimes answers 5xx under load)
    for attempt in range(tries):
        try:
            r = client.get(url)
            if r.status_code == 200: return r.text
            if r.status_code == 404: raise FileNotFoundError(url)
        except httpx.HTTPError:
            pass
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not download {url}")

def table_of_contents(html: str) -> list[tuple[str, str]]:
    # every EPO legal page carries the whole table of contents as a JavaScript array (var oMenu = [...])
    m = re.search(r"var oMenu = (\[.*?\]);</script>", html, re.S)
    if not m: raise ValueError("no table of contents found in the page")
    out = []
    def walk(items):
        for it in items:
            out.append((it[0], it[1]))           # (title, path)
            if len(it) > 3: walk(it[3])           # children
    walk(json.loads(m.group(1)))
    return out

def trim(html: str) -> str:
    # keep what the parser needs (page metadata, heading, legal text, footnotes) and drop ~98% of the page
    soup = BeautifulSoup(html, "lxml")
    metas = "".join(str(m) for m in soup.select("meta.elastic"))
    canon = soup.select_one('meta[name="currentmenuurl"]')
    h1 = soup.select_one("div.field--name-field-caption h1") or soup.find("h1")
    body = soup.select_one("div.epolegal-content")
    notes = soup.select_one("div.DOC4NET2-notes")
    parts = [str(x) for x in (h1, body) if x is not None]
    # footnotes sometimes sit inside the content div and sometimes after it
    if notes is not None and (body is None or notes not in body.descendants): parts.append(str(notes))
    return f"<html><head>{canon or ''}{metas}</head><body>{''.join(parts)}</body></html>"

def raw_path(corpus: str, path: str) -> Path:
    return config.RAW / corpus / Path(path).name

def fetch(corpus: str, limit: int | None = None, log=print) -> int:
    # download every page of a corpus that isn't on disk yet; safe to stop and run again (it resumes)
    src = SOURCES[corpus]
    (config.RAW / corpus).mkdir(parents=True, exist_ok=True)
    with _client() as client:
        # step 1: the table of contents, from the corpus' index page
        toc = table_of_contents(_get(client, BASE + src["index"]))
        # keep the titles too: Guidelines pages say only 'Chapter IV', the table of contents has its full title
        (config.RAW / corpus / "_toc.json").write_text(json.dumps({Path(p).name: t for t, p in toc}, ensure_ascii=False))
        pages = [p for _, p in toc if src["pages"].match(p)]
        pages = list(dict.fromkeys(pages))[:limit]           # keep order, drop repeats
        todo = [p for p in pages if not raw_path(corpus, p).exists()]
        log(f"{corpus}: {len(pages)} pages in the table of contents, {len(todo)} still to download")
        # step 2: each missing page, one a second
        for i, p in enumerate(todo, 1):
            try:
                html = _get(client, BASE + p)
            except FileNotFoundError:
                log(f"  missing on the site: {p}"); continue
            raw_path(corpus, p).write_text(trim(html), encoding="utf-8")
            if i % 25 == 0 or i == len(todo): log(f"  {i}/{len(todo)} downloaded")
            time.sleep(config.CRAWL_DELAY)
    return len(pages)

if __name__ == "__main__":
    # python -m patentlaw.fetch epc [limit]
    fetch(sys.argv[1] if len(sys.argv) > 1 else "epc", int(sys.argv[2]) if len(sys.argv) > 2 else None)


######
# Fetch module: collects the law from the EPO website. It reads the site's own table of contents (so no page is
# missed or guessed), downloads only pages that aren't saved yet (so it can resume), waits 1 second between requests
# (the site's robots.txt asks for that), and stores a trimmed copy of each page under data/raw/<corpus>/.

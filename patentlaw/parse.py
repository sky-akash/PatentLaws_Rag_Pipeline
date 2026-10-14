"""Turn a saved EPO page into a structured provision: id, title, where it sits in the law, its numbered paragraphs,
the provisions it refers to, and the Board of Appeal decisions its footnotes cite."""
import json, re
from pathlib import Path
from bs4 import BeautifulSoup
from . import refs

# the eight Parts of the Guidelines (their pages only say 'Part G')
GL_PARTS = {"A": "Formalities examination", "B": "Search", "C": "Procedural aspects of substantive examination",
            "D": "Opposition and limitation/revocation procedures", "E": "General procedural matters",
            "F": "The European patent application", "G": "Patentability", "H": "Amendments and corrections"}

BLOCKS = ["p", "li", "tr", "h2", "h3", "h4", "h5", "h6", "pre"]   # pieces of text that are read as one unit

def _clean(s: str) -> str:
    # one line of plain text: no non-breaking spaces or doubled blanks, the EPO's non-breaking hyphen made plain
    s = s.replace(" ", " ").replace("‑", "-")
    s = re.sub(r"\s+", " ", s)
    # joining the pieces of a line with spaces leaves 'Article 53(c) ,' - put punctuation back against its word
    s = re.sub(r"\s+([,.;:)\]])", r"\1", s)
    return re.sub(r"([(\[])\s+", r"\1", s).strip()

def _kind(pid: str) -> str:
    if pid.startswith("A"): return "article"
    if pid.startswith("R"): return "rule"
    if pid.startswith("F"): return "fees"
    if pid == "PROT69": return "protocol"
    return "guideline"

def parse_page(html: str, corpus: str, path: str | None = None) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    meta = {m["name"]: m.get("content", "") for m in soup.select("meta.elastic") if m.get("name")}
    url_meta = soup.select_one('meta[name="currentmenuurl"]')
    url_path = (url_meta["content"] if url_meta else None) or path or ""

    # step 1: which provision is this? (the canonical id comes from the page's own address)
    pid = refs.from_href(url_path)
    if not pid: return None

    # step 2: its title, without the 'Article 54 –' or '3.' in front
    raw_title = _clean(meta.get("title", "")) or _clean(soup.h1.get_text(" ") if soup.h1 else "")
    title = re.sub(r"^(?:Article|Rule)\s+\d+[a-z]?\s*[–-]\s*", "", raw_title)
    title = re.sub(r"^(?:Chapter\s+[IVX]+|Part\s+[A-H])\s*[–-]\s*", "", title)
    title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title).strip()

    # step 3: where it sits in the law (Part / Chapter); the Guidelines only give this in the breadcrumb trail
    part, chapter = _clean(meta.get("parttitle", "")), _clean(meta.get("chaptertitle", ""))
    if not part and meta.get("breadcrumb_json"):
        try:
            crumbs = [c.get("title", "") for c in json.loads(meta["breadcrumb_json"])]
            part = next((_clean(c) for c in crumbs if c.startswith("Part ")), "")
            chapter = next((_clean(c) for c in crumbs if c.startswith("Chapter ")), "")
        except (ValueError, AttributeError):
            pass
    if re.fullmatch(r"Part [A-H]", part): part += " – " + GL_PARTS[part[-1]]

    body = soup.select_one("div.epolegal-content")
    notes = soup.select_one("div.DOC4NET2-notes")
    if notes is not None: notes.extract()          # footnotes are read separately, never as law text

    # step 4: footnotes (amendment history, and the Enlarged Board decisions that interpret this provision)
    footnotes = [_clean(p.get_text(" ")) for p in notes.find_all("p")] if notes is not None else []
    footnotes = [re.sub(r"^\d+\s*", "", f) for f in footnotes if f]

    # step 5: the provisions this one refers to, from the links in its text (not counting itself)
    xrefs = []
    if body is not None:
        for a in body.find_all("a", href=True):
            r = refs.from_href(a["href"])
            if r and r != pid and r not in xrefs: xrefs.append(r)

    # step 6: the text, paragraph by paragraph; '(1)' / '(a)' numbering is kept so citations can point at it
    paras = []
    if body is not None:
        for fn in body.select("a.FootnoteRef"): fn.decompose()     # footnote numbers aren't part of the law
        for el in body.find_all(BLOCKS):
            if el.find_parent(BLOCKS) is not None: continue       # a list inside a paragraph is read with it
            num_el = el.find("span", class_="LM_Normal_p1")
            num = _clean(num_el.get_text(" ")) if num_el else ""
            if num_el: num_el.extract()
            text = _clean(el.get_text(" "))
            if text: paras.append({"num": num, "text": text})

    # step 7: number each paragraph the way lawyers cite it: '(3)' and its items '(3)(a)'
    current = ""
    for p in paras:
        n = p["num"]
        if re.fullmatch(r"\(\d+[a-z]?\)", n): current = n; p["pin"] = n
        elif re.fullmatch(r"\([a-z]\)", n): p["pin"] = current + n
        else: p["pin"] = n or current       # an unnumbered sentence belongs to the paragraph above it

    # step 8: a deleted provision has no text, but a question about it deserves the answer 'it was deleted'
    heading = _clean(soup.h1.get_text(" ")) if soup.h1 else ""
    if not paras and "deleted" in heading.lower():
        title = "Deleted"
        paras = [{"num": "", "pin": "", "text": "This provision has been deleted. " + " ".join(footnotes)}]

    text = "\n".join((p["num"] + " " if p["num"] else "") + p["text"] for p in paras)
    return {
        "id": pid, "corpus": corpus, "kind": _kind(pid), "label": refs.label(pid), "title": title,
        "part": part, "chapter": chapter, "url": "https://www.epo.org" + url_path if url_path.startswith("/") else url_path,
        "published": meta.get("date_publication", ""), "paragraphs": paras, "text": text,
        "xrefs": xrefs, "footnotes": footnotes, "cases": refs.find_cases(" ".join(footnotes) + " " + text),
        "keywords": _clean(meta.get("keywords", "")),
    }

def parse_folder(folder: Path, corpus: str) -> list[dict]:
    # every saved page of a corpus; pages with no legal text (part and chapter overviews) are left out
    toc_file = folder / "_toc.json"
    toc = json.loads(toc_file.read_text(encoding="utf-8")) if toc_file.exists() else {}
    out = []
    for f in sorted(folder.glob("*.html")):
        p = parse_page(f.read_text(encoding="utf-8"), corpus, f.name)
        if not (p and p["text"]): continue
        # 'Chapter IV' -> 'Chapter IV – Special provisions', from the chapter page's title in the table of contents
        m = re.match(r"([a-h])_([ivx]+)", f.name)
        if m and re.fullmatch(r"Chapter [IVX]+", p["chapter"]):
            p["chapter"] = _clean(toc.get(f"{m.group(1)}_{m.group(2)}.html", p["chapter"]))
        out.append(p)
    return out


######
# Parse module: reads a trimmed EPO page and returns one provision as a dict. It keeps the paragraph numbering
# ('(3)', '(3)(a)') so answers can cite exactly, collects the cross-references from the page's links (the backbone of
# 'which other provision applies here'), and pulls the Enlarged Board decisions out of the footnotes.

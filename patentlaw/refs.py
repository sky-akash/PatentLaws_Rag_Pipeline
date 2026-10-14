"""Legal references: turn 'Art. 54(3) EPC', 'Rule 56a', 'G-IV, 5.1' or an EPO link into one canonical id, and back."""
import re

# Canonical ids used everywhere in the pipeline (database keys, citations, the UI):
#   A54      Article 54 EPC              R56a     Rule 56a of the Implementing Regulations
#   F2       Article 2 Rules relating to Fees           PROT69   Protocol on the Interpretation of Article 69
#   GL:G-VI,3  Guidelines for Examination, Part G, Chapter VI, section 3
#   GL:GEN,2   Guidelines, General Part, section 2

ROMAN = r"(?:XI{0,3}|IX|IV|VI{0,3}|I{1,3}|V)"   # chapter numbers I..XIII in the Guidelines
DASH = "-‐‑‒–—"          # the EPO writes 'G‑VI' with a non-breaking hyphen (U+2011)

# ---- links in EPO pages ----
_HREF_EPC = re.compile(r"(?:^|/)([arf])(\d+[a-z]?)\.html$")
_HREF_GL = re.compile(r"(?:^|/)([a-h])_([ivx]+)(?:_([\d_]+))?\.html$")
_HREF_GL_GEN = re.compile(r"(?:^|/)foreword(?:_([\d_]+))?\.html$")

def from_href(href: str) -> str | None:
    # a link inside an EPO page -> canonical id ('a53.html' -> 'A53', '/en/.../g_iv_5_1.html' -> 'GL:G-IV,5.1')
    href = href.split("#")[0].split("?")[0]
    # Guidelines links only count inside the Guidelines (an 'a_...' link elsewhere is not a Guidelines page)
    if "protinta69" in href: return "PROT69"
    m = _HREF_EPC.search(href)
    if m and ("guidelines" not in href):
        return {"a": "A", "r": "R", "f": "F"}[m.group(1)] + m.group(2)
    m = _HREF_GL.search(href)
    if m and ("/epc/" not in href):
        return gl_id(m.group(1), m.group(2), m.group(3))
    m = _HREF_GL_GEN.search(href)
    if m and ("/epc/" not in href):
        return "GL:GEN" + ("," + m.group(1).replace("_", ".") if m.group(1) else "")
    return None

def gl_id(part: str, chapter: str, section: str | None) -> str:
    # pieces of a Guidelines file name -> 'GL:G-VI,3.1' (chapter-only pages have no section)
    base = f"GL:{part.upper()}-{chapter.upper()}"
    return base + ("," + section.replace("_", ".") if section else "")

# ---- references written in text (law texts, questions, model answers) ----
_NUM = r"(\d+[a-z]?)"
_PARA = r"((?:\s*\(\s*\d+[a-z]?\s*\))*(?:\s*\(\s*[a-z]\s*\))?)"                  # (3) or (1)(b)
_ART = re.compile(rf"\b(?:articles?|arts?\.?|articol[oi]|art\.?)\s*{_NUM}{_PARA}(\s*(?:of\s+the\s+)?(?:rfees|rules relating to fees))?",
                  re.I)
_RULE = re.compile(rf"\b(?:rules?|r\.|regol[ae])\s*{_NUM}{_PARA}", re.I)
# capitals only and no letter right after, so words like 'e-voting' are never read as Guidelines Part E, Chapter V
_GL = re.compile(rf"\b([A-H])\s*[{DASH}]\s*({ROMAN})(?![A-Za-z])(?:\s*,\s*(\d+(?:\.\d+)*))?")
_ARTICLES_RANGE = re.compile(r"\b(?:articles|arts\.?)\s*(\d+)\s*(?:to|-|–)\s*(\d+)\b", re.I)
_PARA_WORD = re.compile(r"\s*,?\s*paragraph\s*(\d+)", re.I)   # 'Article 90, paragraph 1'

def find_refs(text: str) -> list[str]:
    # every provision a text points at, as canonical ids, in order of first mention, without duplicates
    found: list[tuple[int, str]] = []
    # 'Articles 52 to 57' names every article in between
    for m in _ARTICLES_RANGE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < b - a <= 20: found += [(m.start(), f"A{n}") for n in range(a, b + 1)]
    for m in _ART.finditer(text):
        kind = "F" if m.group(3) else "A"
        found.append((m.start(), kind + m.group(1).lower()))
    for m in _RULE.finditer(text):
        found.append((m.start(), "R" + m.group(1).lower()))
    for m in _GL.finditer(text):
        # 'G-VI' alone is a chapter; with ', 3.1' it is a section
        found.append((m.start(), gl_id(m.group(1), m.group(2), m.group(3))))
    if re.search(r"protocol on the interpretation of article 69", text, re.I):
        found.append((text.lower().find("protocol on the interpretation"), "PROT69"))
    seen, out = set(), []
    for _, ref in sorted(found):
        if ref not in seen: seen.add(ref); out.append(ref)
    return out

def find_pinpoints(text: str) -> list[tuple[str, str]]:
    # like find_refs but keeps the paragraph: [('A54', '(3)'), ('R56', '(2)')] - used to show 'Art. 54(3)' in the UI
    out = []
    for m in _ART.finditer(text):
        para = re.sub(r"\s+", "", m.group(2) or "")
        if not para:
            w = _PARA_WORD.match(text, m.end())
            if w: para = f"({w.group(1)})"
        out.append((("F" if m.group(3) else "A") + m.group(1).lower(), para))
    for m in _RULE.finditer(text):
        para = re.sub(r"\s+", "", m.group(2) or "")
        if not para:
            w = _PARA_WORD.match(text, m.end())
            if w: para = f"({w.group(1)})"
        out.append(("R" + m.group(1).lower(), para))
    return out

def normalise(ref: str) -> str | None:
    # anything a person or a model might write for one provision -> canonical id ('Art. 54(3) EPC' -> 'A54')
    ref = (ref or "").strip()
    if re.fullmatch(r"(A|R|F)\d+[a-z]?|PROT69|GL:[A-Z]+(-[IVX]+)?(,[\d.]+)?", ref): return ref
    if re.fullmatch(r"[ARF]\d+[A-Za-z]?", ref, re.I):
        return ref[0].upper() + ref[1:].lower()
    found = find_refs(ref)
    return found[0] if found else None

def label(ref: str, para: str = "") -> str:
    # canonical id -> how a lawyer writes it ('A54', '(3)' -> 'Art. 54(3) EPC')
    if ref.startswith("A"): return f"Art. {ref[1:]}{para} EPC"
    if ref.startswith("R"): return f"Rule {ref[1:]}{para} EPC"
    if ref.startswith("F"): return f"Art. {ref[1:]}{para} RFees"
    if ref == "PROT69": return "Protocol on the Interpretation of Art. 69 EPC"
    if ref.startswith("GL:GEN"): return "Guidelines, General Part" + (", " + ref.split(",", 1)[1] if "," in ref else "")
    if ref.startswith("GL:"):
        body = ref[3:]
        return "Guidelines " + (body.replace(",", ", ") if "," in body else body)
    return ref

# Enlarged Board of Appeal decisions and opinions ('G 2/88'), cited in footnotes and in the Guidelines
_CASE = re.compile(r"\b([GTJR])\s?(\d{1,4})/(\d{2})\b")

def find_cases(text: str) -> list[str]:
    seen, out = set(), []
    for m in _CASE.finditer(text):
        c = f"{m.group(1)} {m.group(2)}/{m.group(3)}"
        if c not in seen: seen.add(c); out.append(c)
    return out


######
# References module: one naming scheme for every provision (A54, R56a, F2, PROT69, GL:G-VI,3), and functions that
# find references in EPO links, in law text, in a user's question (English or Italian: 'articolo 54', 'regola 56')
# and in a model's answer. This is what lets the app look a provision up exactly and check every citation.

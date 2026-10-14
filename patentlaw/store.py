"""The SQLite side of the index: provisions, their cross-references, passages, and a full-text (BM25) search index."""
import json, re, sqlite3
from pathlib import Path
from . import config

SCHEMA = """
create table if not exists provisions(
  id text primary key, corpus text, kind text, label text, title text, part text, chapter text, url text,
  published text, text text, paragraphs text, footnotes text, cases text, keywords text);
create table if not exists xrefs(src text, dst text, primary key(src, dst));
create index if not exists xrefs_dst on xrefs(dst);
create table if not exists chunks(id text primary key, provision_id text, seq int, pins text, header text, text text);
create index if not exists chunks_prov on chunks(provision_id);
create virtual table if not exists chunks_fts using fts5(chunk_id unindexed, header, text, keywords, tokenize='porter unicode61');
"""

def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # one connection per caller; check_same_thread off because FastAPI may answer from another thread
    path = Path(path or config.DB_PATH)
    if str(path) != ":memory:": path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db

def save(db: sqlite3.Connection, provisions: list[dict], chunks: list[dict]) -> None:
    # replace the provisions of these corpora (a rebuild must not leave stale law behind)
    corpora = sorted({p["corpus"] for p in provisions})
    for c in corpora:
        old = [r[0] for r in db.execute("select id from provisions where corpus=?", (c,))]
        for pid in old:
            db.execute("delete from xrefs where src=?", (pid,))
            db.execute("delete from chunks_fts where chunk_id in (select id from chunks where provision_id=?)", (pid,))
            db.execute("delete from chunks where provision_id=?", (pid,))
        db.execute("delete from provisions where corpus=?", (c,))
    # store each provision, then its links to other provisions
    for p in provisions:
        db.execute("insert or replace into provisions values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (p["id"], p["corpus"], p["kind"], p["label"], p["title"], p["part"], p["chapter"], p["url"],
                    p["published"], p["text"], json.dumps(p["paragraphs"], ensure_ascii=False),
                    json.dumps(p["footnotes"], ensure_ascii=False), json.dumps(p["cases"]), p["keywords"]))
        db.executemany("insert or ignore into xrefs values(?,?)", [(p["id"], d) for d in p["xrefs"]])
    # store the passages and put them in the full-text index; the EPO's index keywords are searchable too
    kw = {p["id"]: p["keywords"] for p in provisions}
    for c in chunks:
        db.execute("insert or replace into chunks values(?,?,?,?,?,?)",
                   (c["id"], c["provision_id"], c["seq"], c["pins"], c["header"], c["text"]))
        db.execute("insert into chunks_fts values(?,?,?,?)", (c["id"], c["header"], c["text"], kw.get(c["provision_id"], "")))
    db.commit()

def provision(db: sqlite3.Connection, pid: str) -> dict | None:
    r = db.execute("select * from provisions where id=?", (pid,)).fetchone()
    if not r: return None
    d = dict(r)
    for k in ("paragraphs", "footnotes", "cases"): d[k] = json.loads(d[k] or "[]")
    d["refers_to"] = [x[0] for x in db.execute("select dst from xrefs where src=? and dst in (select id from provisions)", (pid,))]
    d["referred_by"] = [x[0] for x in db.execute("select src from xrefs where dst=?", (pid,))]
    return d

def chunks_of(db: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids: return {}
    q = ",".join("?" * len(ids))
    return {r["id"]: dict(r) for r in db.execute(f"select * from chunks where id in ({q})", ids)}

# Everyday (and some Italian) words -> the words the EPC itself uses. People ask about 'deadlines' and 'adding'
# things; the law speaks of 'time limits' and 'amendments'. Keyword search only finds what is literally written.
THESAURUS = {
    "deadline": "time limit period", "deadlines": "time limit period", "late": "time limit", "missed": "time limit",
    "grace": "non-prejudicial disclosure", "add": "amended amendment", "adding": "amended amendment",
    "added": "amended amendment", "change": "amended amendment", "broaden": "extend protection amended",
    "broader": "extend protection amended", "lasts": "term", "long": "term", "duration": "term",
    "stole": "abuse", "stolen": "abuse", "fair": "exhibition", "show": "exhibition", "showed": "exhibition",
    "presented": "exhibition displayed", "competitor": "opposition", "challenge": "opposition revocation",
    "software": "programs computers", "employee": "employee entitled", "owner": "proprietor", "applicant": "applicant",
    "language": "languages", "translate": "translation", "fees": "fee", "inventor": "inventor designation",
    "attorney": "professional representative", "lawyer": "professional representative",
    # Italian
    "brevetto": "patent", "brevetti": "patent", "dura": "term", "durata": "term", "anni": "years",
    "domanda": "application", "deposito": "filing", "aggiungere": "amended amendment", "modificare": "amended amendment",
    "esempi": "examples", "novità": "novelty", "termine": "time limit", "ricorso": "appeal", "opposizione": "opposition",
    "farmaco": "substance composition", "medico": "medical method therapy", "uso": "use", "priorità": "priority",
}

def fts_query(text: str) -> str:
    # a question in plain words -> an FTS5 query: every word may match (OR), stop words dropped, EPC words added
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+", text.lower()) if len(w) > 2 and w not in STOP]
    words += [x for w in words for x in THESAURUS.get(w, "").split()]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))

def search_fts(db: sqlite3.Connection, text: str, k: int = 30) -> list[tuple[str, float]]:
    # BM25 ranking; the header (label + title) weighs most, then the text, then the EPO keywords
    q = fts_query(text)
    if not q: return []
    rows = db.execute("select chunk_id, bm25(chunks_fts, 0, 4.0, 1.0, 0.3) as s from chunks_fts "
                      "where chunks_fts match ? order by s limit ?", (q, k)).fetchall()
    return [(r["chunk_id"], -r["s"]) for r in rows]

STOP = set("""the and for are but not you your with this that from have has had was were will would can could should
what which when where who whom how why does did doing done into onto than then there their them they its it's our
out about any all also may might must shall such some only very just more most other same each per under over
been being upon whether while within without between after before during i me my we us he she him her his hers
is am be do a an of to in on at by or as if so no""".split())


######
# Store module: the database behind the app. Provisions are kept whole (for showing the real text and following
# cross-references both ways), passages are kept for retrieval, and an FTS5 index gives keyword search with BM25
# (exact legal words like 'priority' or 'divisional' that embeddings can blur).

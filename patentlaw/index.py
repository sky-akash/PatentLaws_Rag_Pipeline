"""Build (or refresh) the search index from the downloaded pages: parse -> chunk -> SQLite + full-text -> vectors."""
import sys, time
from . import chunk, config, embed, parse, store

def build(corpora: list[str], embedder=None, db_path=None, chroma_path=None, log=print) -> dict:
    t0 = time.time()
    # step 1: parse every saved page of each corpus into provisions
    provisions = []
    for c in corpora:
        folder = config.RAW / c
        if not folder.exists():
            log(f"{c}: nothing downloaded yet - run: python -m patentlaw.fetch {c}"); continue
        got = parse.parse_folder(folder, c)
        log(f"{c}: {len(got)} provisions parsed")
        provisions += got
    if not provisions: return {"provisions": 0, "chunks": 0, "embedded": 0}

    # step 2: cut them into passages along their paragraphs
    chunks = [c for p in provisions for c in chunk.chunk_provision(p)]
    log(f"{len(chunks)} passages")

    # step 3: save provisions, cross-references, passages and the full-text index
    db = store.connect(db_path)
    store.save(db, provisions, chunks)

    # step 4: embed the passages (only new or changed ones) into the vector store; all corpora share one collection,
    # so the full list of passages is passed and nothing from another corpus is deleted
    col = embed.collection(chroma_path)
    all_chunks = [dict(r) for r in db.execute("select * from chunks")]
    n = embed.index_chunks(col, embedder or embed.OllamaEmbedder(), all_chunks, log=log)
    stats = {"provisions": len(provisions), "chunks": len(chunks), "embedded": n, "seconds": round(time.time() - t0, 1)}
    log(f"done: {stats}")
    return stats

if __name__ == "__main__":
    # python -m patentlaw.index epc guidelines
    build(sys.argv[1:] or ["epc"])


######
# Index module: the build step. It parses what fetch.py downloaded, chunks it, writes the SQLite database with its
# full-text index, and embeds passages into Chroma. Running it again only re-embeds passages whose text changed.

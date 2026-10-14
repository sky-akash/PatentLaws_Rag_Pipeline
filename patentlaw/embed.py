"""Embeddings (local, via Ollama's nomic-embed-text) and the Chroma vector store that holds them."""
import hashlib, math, re
import httpx
from . import config

class EmbedError(Exception):
    pass

class OllamaEmbedder:
    # nomic-embed-text was trained with task prefixes: documents and queries must be marked, or retrieval gets worse
    def __init__(self, url: str = config.OLLAMA_URL, model: str = config.EMBED_MODEL):
        self.url, self.model = url.rstrip("/"), model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = httpx.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts}, timeout=300)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbedError(f"Ollama embeddings failed ({e}). Is Ollama running with '{self.model}' pulled?") from e
        return r.json()["embeddings"]

    def documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(["search_document: " + t for t in texts])

    def query(self, text: str) -> list[float]:
        return self._embed(["search_query: " + text])[0]

class HashEmbedder:
    # a stand-in for tests and CI (no Ollama there): bag of hashed words, so shared words still mean 'similar'
    dim = 256
    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    def documents(self, texts): return [self._vec(t) for t in texts]
    def query(self, text): return self._vec(text)

def collection(path=None, name: str = "passages"):
    # the persistent Chroma collection, cosine distance (what nomic embeddings are made for)
    import chromadb
    client = chromadb.PersistentClient(path=str(path or config.CHROMA_PATH)) if path != ":memory:" else chromadb.EphemeralClient()
    return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"}, embedding_function=None)

def index_chunks(col, embedder, chunks: list[dict], batch: int = 32, log=print) -> int:
    # embed only passages that are new or changed (the id + a hash of the text decide), so rebuilding is cheap
    have = {}
    got = col.get(include=["metadatas"])
    for i, m in zip(got["ids"], got["metadatas"]): have[i] = (m or {}).get("hash")
    todo = []
    for c in chunks:
        h = hashlib.sha1((c["header"] + c["text"]).encode()).hexdigest()[:16]
        if have.get(c["id"]) != h: todo.append((c, h))
    # passages that no longer exist leave the vector store too
    gone = set(have) - {c["id"] for c in chunks}
    if gone: col.delete(ids=sorted(gone))
    for s in range(0, len(todo), batch):
        part = todo[s:s + batch]
        vecs = embedder.documents([c["header"] + "\n" + c["text"] for c, _ in part])
        col.upsert(ids=[c["id"] for c, _ in part], embeddings=vecs,
                   metadatas=[{"provision_id": c["provision_id"], "hash": h} for c, h in part])
        log(f"  embedded {min(s + batch, len(todo))}/{len(todo)}")
    return len(todo)

def search_vec(col, embedder, text: str, k: int = 30) -> list[tuple[str, float]]:
    if col.count() == 0: return []
    res = col.query(query_embeddings=[embedder.query(text)], n_results=min(k, col.count()))
    return [(i, 1.0 - d) for i, d in zip(res["ids"][0], res["distances"][0])]


######
# Embed module: turns passages and questions into vectors with nomic-embed-text running locally in Ollama (free and
# private; Groq has no embedding API), stores them in a Chroma collection, and re-embeds only what changed.
# HashEmbedder is a tiny offline stand-in so the tests and CI run without any model.

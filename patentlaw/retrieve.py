"""Hybrid retrieval: provisions named in the question + keyword search (BM25) + meaning search (vectors), fused by
reciprocal rank, widened along cross-references, and cut to what the answering model can read."""
from collections import defaultdict
from . import config, embed, refs, store

RRF_K = 60            # the usual constant for reciprocal rank fusion: rank 1 -> 1/61, rank 10 -> 1/70
REWRITE_WEIGHT = 0.3  # weight of each planner query vs the question (1.0); tuned on eval/questions.jsonl
HINT_WEIGHT = 0.4     # weight of the planner's candidate provisions
MIN_RELATIVE = 0.5    # drop passages scoring below this share of the best one (tuned on the eval set) ...
MIN_SOURCES = 4       # ... but always keep at least this many

class Retriever:
    def __init__(self, db, col=None, embedder=None, use_fts: bool = True, use_vec: bool = True):
        self.db, self.col, self.embedder = db, col, embedder
        self.use_fts, self.use_vec = use_fts, use_vec          # switches for the evaluation (BM25 only / vectors only)
        self.rewrite_weight, self.hint_weight = REWRITE_WEIGHT, HINT_WEIGHT
        self.min_relative, self.min_sources = MIN_RELATIVE, MIN_SOURCES
        self.vectors_down = False

    def _first_chunks(self, pids: list[str]) -> list[str]:
        # the opening passage of each provision that exists in the corpus (unknown references are ignored)
        out = []
        for pid in pids:
            r = self.db.execute("select id from chunks where provision_id=? order by seq limit 1", (pid,)).fetchone()
            if r: out.append(r["id"])
        return out

    def search(self, question: str, queries: list[str] | None = None, hints: list[str] | None = None,
               max_sources: int = 10, budget_chars: int | None = None, corpora: list[str] | None = None) -> list[dict]:
        budget_chars = budget_chars or config.CONTEXT_TOKENS * 4
        self.vectors_down = False                # try the vector search again on every question
        lists: list[tuple[str, list[str], float]] = []    # (why, ranked chunk ids, weight)

        # step 1: provisions the user names ('Art. 123(2)', 'Rule 56') are looked up directly and weigh the most
        named = refs.find_refs(question)
        if named: lists.append(("named in your question", self._first_chunks(named), 2.0))
        # provisions the planner thinks are relevant (checked here against the real corpus, never trusted blindly)
        hinted = [h for h in (refs.normalise(x) for x in (hints or [])) if h]
        if hinted: lists.append(("suggested by the planner", self._first_chunks(hinted), self.hint_weight))

        # step 2: keyword (BM25) and meaning (vector) search, for the question and each rewritten search query
        # the user's own words weigh 1; each rewritten query weighs less, so five rewrites can't drown the question
        for q, w in [(question, 1.0)] + [(x, self.rewrite_weight) for x in (queries or [])]:
            if self.use_fts:
                lists.append(("keyword match", [c for c, _ in store.search_fts(self.db, q, 30)], w))
            if self.use_vec and self.col is not None and self.embedder is not None and not self.vectors_down:
                try:
                    lists.append(("similar meaning", [c for c, _ in embed.search_vec(self.col, self.embedder, q, 30)], w))
                except embed.EmbedError:
                    self.vectors_down = True     # Ollama is off: carry on with keyword search, and say so in /api/health

        # step 3: reciprocal rank fusion - a passage high in several lists rises to the top
        score, why = defaultdict(float), defaultdict(set)
        for reason, ranked, w in lists:
            for rank, cid in enumerate(ranked):
                score[cid] += w / (RRF_K + rank + 1)
                why[cid].add(reason)
        chunks = store.chunks_of(self.db, list(score))
        if corpora:
            keep = {r["id"] for r in self.db.execute(
                f"select id from provisions where corpus in ({','.join('?' * len(corpora))})", corpora)}
            chunks = {k: v for k, v in chunks.items() if v["provision_id"] in keep}

        # step 4: follow the law's own cross-references from the best passages (e.g. Art. 55 -> Art. 54, Rule 25)
        # at half the score of the passage that points to them: they explain or limit what was found
        best = sorted(chunks, key=lambda c: -score[c])[:4]
        for cid in best:
            for target in refs.find_refs(chunks[cid]["text"])[:4]:
                if target == chunks[cid]["provision_id"]: continue
                for tid in self._first_chunks([target]):
                    if tid not in chunks:
                        chunks.update(store.chunks_of(self.db, [tid]))
                    score[tid] = max(score[tid], score[cid] * 0.5)
                    why[tid].add(f"cross-reference from {refs.label(chunks[cid]['provision_id'])}")

        # step 5: pick passages best-first: at most 2 per provision, within the reading budget
        # passages far below the best one are dropped (after the first few): weak sources tempt the model to stretch
        # a provision to fit, and they cost tokens
        picked, per_prov, used = [], defaultdict(int), 0
        ranked = sorted(chunks, key=lambda c: -score[c])
        top = score[ranked[0]] if ranked else 0
        for cid in ranked:
            c = chunks[cid]
            size = len(c["header"]) + len(c["text"])
            if len(picked) >= self.min_sources and score[cid] < self.min_relative * top: break
            if per_prov[c["provision_id"]] >= 2 or used + size > budget_chars: continue
            picked.append(cid); per_prov[c["provision_id"]] += 1; used += size
            if len(picked) >= max_sources: break

        # step 6: number the sources [1], [2] ... with what the answer and the UI need about each
        out = []
        for n, cid in enumerate(picked, 1):
            c = chunks[cid]
            p = self.db.execute("select label, title, url, corpus, kind from provisions where id=?", (c["provision_id"],)).fetchone()
            out.append({"n": n, "chunk_id": cid, "provision_id": c["provision_id"], "label": p["label"], "title": p["title"],
                        "kind": p["kind"], "corpus": p["corpus"], "url": p["url"], "pins": c["pins"],
                        "header": c["header"], "text": c["text"], "score": round(score[cid], 5), "why": sorted(why[cid])})
        return out


######
# Retrieve module: finds the law that fits a question. Four signals are fused with reciprocal rank fusion
# (provisions named in the question, provisions the planner suggests, BM25 keyword hits, vector similarity), then the
# search follows the cross-references inside the best passages (that's how an exception or an implementing Rule is
# found), and finally picks a diverse set that fits the model's reading budget. Each source says why it was picked.

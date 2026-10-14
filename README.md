# Patent Law Navigator

Ask a question about European patent law, or describe your situation, and get back:

- **What you're asking**: your question restated as a precise legal question, with the facts that matter and any assumptions made
- **Which law applies**: the governing provisions, exceptions and procedural rules, down to the paragraph (`Art. 55(1)(b) EPC`, `Rule 25 EPC`)
- **What the law means**: each provision explained twice, once in plain words and once in legal-technical terms, next to the verbatim text
- **How it applies to your facts**: step-by-step reasoning, like a patent attorney's analysis
- **What not to confuse**: e.g. Art. 54(2) vs 54(3) prior art, further processing vs re-establishment
- **What if…**: variations of the facts that would change the outcome
- **What's missing**: the facts needed for a certain answer, and what the sources don't cover

Every citation is checked before it is shown, first in code and then by a second model (see [Trust](#trust-how-answers-are-checked)).

> Research prototype, not legal advice. Law texts © European Patent Office, reproduced from epo.org with attribution.

## Pipeline

```
question ─► PLAN ─────────► RETRIEVE ────────────────────► ANSWER ──────────► VERIFY ─────────► JUDGE
            gpt-oss-20b      named provisions (exact)       gpt-oss-120b       code: right      gpt-oss-20b:
            rewrites into    + BM25 (SQLite FTS5)           strict JSON        source? real     does the text
            EPC vocabulary,  + vectors (nomic / Chroma)     schema, cites      paragraph?       say what the
            guesses          + planner's guesses (checked)  numbered sources   attach verbatim  explanation
            provisions       ─► reciprocal rank fusion      only               law text         claims? relevant?
                             ─► follow cross-references
                             ─► prune weak, fit token budget
```

| Step | Where | Notes |
|---|---|---|
| Collect | `patentlaw/fetch.py` | Reads the EPO site's own table of contents and fetches each page at 1 request/s (the site's `robots.txt` crawl delay); resumable |
| Parse | `patentlaw/parse.py` | Keeps paragraph numbering (`(3)`, `(3)(a)`), cross-reference links, and Enlarged Board decisions from footnotes |
| References | `patentlaw/refs.py` | One canonical ID per provision (`A54`, `R56a`, `GL:G-VI,3`); reads EPO links, legal prose, Italian (`articolo 54`, `regola 25`) |
| Chunk | `patentlaw/chunk.py` | Splits along paragraphs (~350 tokens); each chunk carries a header like `Art. 54 EPC – Novelty \| Part II › Chapter I` |
| Index | `patentlaw/store.py`, `embed.py`, `index.py` | SQLite with FTS5/BM25 (plus a small EPC thesaurus); Chroma with local `nomic-embed-text` vectors; only re-embeds changed text |
| Retrieve | `patentlaw/retrieve.py` | Hybrid search fused with RRF, cross-reference expansion, relative-score pruning, a reading budget sized for Groq's free tier |
| Answer | `patentlaw/answer.py` | Plan → retrieve → answer → verify → judge; answers cached in SQLite, so a repeated question costs no tokens |
| Serve | `patentlaw/api.py`, `static/` | FastAPI (`/api/ask`, `/api/search`, `/api/provision/{ref}`, `/api/health`) and a single-page UI with no build step |

## Data

| Corpus | Source | Size | Status |
|---|---|---|---|
| EPC Articles, Implementing Regulations, Rules relating to Fees, Protocol on Art. 69 | [epo.org/en/legal/epc](https://www.epo.org/en/legal/epc/2020/index.html) (pages updated 2026-05-21) | 372 provisions, 438 passages | indexed |
| Guidelines for Examination (March 2026) | [epo.org/en/legal/guidelines-epc/2026](https://www.epo.org/en/legal/guidelines-epc/2026/index.html) | 1,858 pages | downloaded to `data/raw/guidelines`, not yet indexed (`python -m patentlaw.index epc guidelines`) |
| Case Law of the Boards of Appeal (2025) | epo.org/en/legal/case-law/2025 | | next |
| Unitary Patent / UPC Agreement | EUR-Lex (Reg. 1257/2012, 1260/2012), UPC Agreement | | planned |
| Italian Industrial Property Code (D.Lgs. 30/2005) | Normattiva | | planned |

Deleted articles are indexed too, so asking about Art. 102 gets "deleted by the EPC 2000 revision" instead of nothing.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ollama pull nomic-embed-text                 # local embeddings (free)
# paste your Groq key into .env:  GROQ_API_KEY=...   (free at console.groq.com/keys)
.venv/bin/python -m patentlaw.fetch epc      # ~6 min, 1 request/s
.venv/bin/python -m patentlaw.index epc      # ~15 s
.venv/bin/uvicorn patentlaw.api:create_app --factory --port 8010
```

Open http://localhost:8010. Without a Groq key, search and browsing still work, and only `/api/ask` needs the key. Without Ollama, retrieval falls back to BM25.

Docker: `docker compose up` (mounts `./data`, reads `.env`, reaches Ollama on the host).

## Evaluation

`eval/questions.jsonl` has 46 questions written from patent-attorney practice. Each lists the provisions that must be found (`expected`) and others that are acceptable. It includes 2 Italian questions and 2 deliberately out-of-scope ones (US law, trademarks).

```bash
.venv/bin/python eval/run_eval.py retrieval          # local, no tokens
.venv/bin/python eval/run_eval.py retrieval --plan   # + LLM planner (~25K tokens, plans cached)
.venv/bin/python eval/run_eval.py answers --n 10     # full answers (~6K tokens each)
```

**Retrieval, tuning set** (44 answerable questions, EPC corpus; recall@10 = all sources passed to the model):

| Config | recall@1 | recall@3 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|
| BM25 only | 0.409 | 0.636 | 0.727 | 0.898 | 0.563 |
| Vectors only | 0.489 | 0.682 | 0.727 | 0.761 | 0.602 |
| Hybrid (RRF + cross-refs) | 0.511 | 0.739 | 0.898 | 0.966 | 0.667 |
| Hybrid + LLM planner | 0.580 | 0.818 | 0.920 | 0.943 | 0.720 |

**Retrieval, held-out set** (`eval/heldout.jsonl`, 17 questions written after tuning and never used to tune anything):

| Config | recall@1 | recall@3 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|
| BM25 only | 0.294 | 0.529 | 0.529 | 0.529 | 0.412 |
| Vectors only | 0.412 | 0.647 | 0.647 | 0.647 | 0.520 |
| Hybrid (RRF + cross-refs) | 0.294 | 0.412 | 0.706 | 0.765 | 0.430 |
| Hybrid + LLM planner | 0.353 | 0.529 | 0.765 | 0.882 | 0.494 |

What it shows: the thesaurus, fusion weights and pruning threshold were tuned on the first set, and the gap to the held-out set is the overfitting. On unseen questions the LLM planner matters most (recall@10 rises from 0.77 to 0.88, and it is what makes Italian questions work). Both sets are small: one question moves recall by 0.02 to 0.06.

**Answers** (11 questions from the tuning set, gpt-oss-120b, `eval/results/answers-*.json`):

| Metric | Value |
|---|---|
| Expected provisions cited and verified (recall) | 0.94 (miss: Art. 122 re-establishment for "I missed a time limit") |
| Cited provisions that are expected or acceptable (precision) | 0.74 (partly an artifact: e.g. Rule 17 is relevant to Art. 61 but wasn't on the list) |
| Citations matching a retrieved source and a real paragraph (grounding) | 1.00 |
| Out-of-scope questions answered by admitting the gap (US law, trademarks) | 2 / 2 |
| Latency / tokens per answer | 5.1 s / ~5.9K tokens (plan + answer; the judge adds ~1.5K on the small model) |

## Trust: how answers are checked

1. **Strict JSON schema** (Groq constrained decoding): the answer always has the same structure, so each field can be checked.
2. **Citation check, in code:** every provision the answer relies on must be one of the retrieved sources (a wrong source number is corrected; a provision that wasn't retrieved is moved out of the answer), and every pinpoint must exist (`Art. 54(7)` is caught). The verbatim law text is attached, so the reader can check the explanation.
3. **Prose check:** provisions mentioned in free text that weren't retrieved are listed. A provision that doesn't exist (e.g. `Art. 999`) raises a warning.
4. **Judge:** the small model reads each explanation next to the full provision text and rates it *supported / partly / no* and *relevant / partly / no*. Flagged items sink to the bottom with a visible note.
5. **"It depends":** when the outcome turns on a fact you didn't give, the short answer must say so and give both outcomes. Confidence is capped at "medium" in code.

## Design decisions

- **Groq free tier** (`openai/gpt-oss-120b` answers, `gpt-oss-20b` plans and judges): limited to 8K tokens/min and 200K tokens/day per model. Hence a ~3.2K-token reading budget, pruning, a separate model for planning and judging, a fallback to the small model on rate limits, and an answer cache.
- **Local embeddings** (Ollama `nomic-embed-text`, with its `search_document:`/`search_query:` prefixes): Groq has no embedding API, and local embeddings are free, private and reproducible.
- **Chunks follow the law's structure**, never cutting mid-paragraph, so a passage is always a citable unit.
- **Cross-references are data:** the EPO's own links (Art. 55 → Art. 54, Rule 25) are stored both ways and followed during retrieval. That is how exceptions and implementing Rules are found.
- **The model never quotes the law.** The UI shows the real text from the database, so a quote can't be fabricated.

## Tests and CI

`pytest` (23 tests) runs on saved EPO pages with a hashing embedder and a fake LLM, so it needs no network, Ollama or API key. GitHub Actions runs the tests, then builds the Docker image and smoke-tests it.

## Roadmap

- Index the Guidelines (downloaded) and the Case Law book; let answers distinguish *law* (EPC) from *practice* (Guidelines) from *interpretation* (Boards of Appeal)
- Grow the held-out set; have a patent professional review answer correctness against the judge's ratings
- Unitary Patent / UPC and the Italian IP Code (Normattiva) as further corpora
- Reranker (cross-encoder) once the corpus grows with the Guidelines

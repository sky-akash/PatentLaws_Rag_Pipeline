"""Evaluate the pipeline on eval/questions.jsonl.

  python eval/run_eval.py retrieval            # BM25 vs vectors vs hybrid (local, no tokens)
  python eval/run_eval.py retrieval --plan     # + hybrid with the LLM search planner (gpt-oss-20b, ~30K tokens)
  python eval/run_eval.py answers --n 10       # full answers from the big model (~6K tokens each)
  add --set heldout to score the questions that were never looked at while tuning
"""
import argparse, json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patentlaw import embed, llm, store                       # noqa: E402
from patentlaw.answer import Pipeline                       # noqa: E402
from patentlaw.retrieve import Retriever                    # noqa: E402

RESULTS = ROOT / "eval" / "results"

SET = "questions"   # 'questions' (used while tuning) or 'heldout' (never looked at while tuning)

def load() -> list[dict]:
    return [json.loads(l) for l in (ROOT / "eval" / f"{SET}.jsonl").read_text().splitlines() if l.strip()]

def ranked_provisions(sources: list[dict]) -> list[str]:
    # the order in which distinct provisions appear in the retrieved sources
    return list(dict.fromkeys(s["provision_id"] for s in sources))

def score(expected: list[str], ranked: list[str]) -> dict:
    # recall@k: share of the expected provisions found in the first k; rr: 1/rank of the first expected one
    out = {f"recall@{k}": len(set(expected) & set(ranked[:k])) / len(expected) for k in (1, 3, 5, 10)}
    out["rr"] = next((1 / (i + 1) for i, p in enumerate(ranked) if p in expected), 0.0)
    return out

def plans_for(qs, client) -> dict:
    # planner output per question, cached on disk so re-running the evaluation costs nothing
    path = RESULTS / "plans.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    pipe = Pipeline(store.connect(), None, client)
    for q in qs:
        if q["id"] in cache: continue
        plan, _ = pipe.plan(q["question"])
        if plan: cache[q["id"]] = plan
        path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
        time.sleep(2.5)                                   # 30 requests a minute on the free tier
    return cache

def retrieval(with_plan: bool) -> dict:
    db = store.connect()
    col, emb = embed.collection(), embed.OllamaEmbedder()
    qs = [q for q in load() if q["answerable"]]
    configs = {"bm25": Retriever(db, None, None, use_vec=False),
               "vector": Retriever(db, col, emb, use_fts=False),
               "hybrid": Retriever(db, col, emb)}
    plans = plans_for(qs, llm.ChatClient()) if with_plan else {}
    report, misses = {}, {}
    for name, r in configs.items():
        for planned in ([False, True] if with_plan and name == "hybrid" else [False]):
            label = name + ("+plan" if planned else "")
            rows = []
            for q in qs:
                p = plans.get(q["id"], {}) if planned else {}
                src = r.search(q["question"], p.get("search_queries"), p.get("candidate_provisions"))
                ranked = ranked_provisions(src)
                s = score(q["expected"], ranked); rows.append(s)
                if s["recall@10"] < 1: misses.setdefault(label, []).append({"id": q["id"], "expected": q["expected"], "got": ranked[:6]})
            report[label] = {k: round(statistics.mean(x[k] for x in rows), 3) for k in rows[0]} | {"n": len(rows)}
            report[label]["mrr"] = report[label].pop("rr")
    out = {"kind": "retrieval", "set": SET, "when": time.strftime("%Y-%m-%d %H:%M"), "report": report, "misses": misses}
    (RESULTS / f"retrieval-{SET}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(out, indent=1))
    print(f"{'config':<14}" + "".join(f"{k:>11}" for k in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr")))
    for label, m in report.items():
        print(f"{label:<14}" + "".join(f"{m[k]:>11.3f}" for k in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr")))
    return out

def answers(n: int, ids: list[str] | None, pause: float) -> dict:
    db = store.connect()
    pipe = Pipeline(db, Retriever(db, embed.collection(), embed.OllamaEmbedder()))
    qs = [q for q in load() if not ids or q["id"] in ids][:n]
    rows = []
    for i, q in enumerate(qs):
        t = time.time()
        try:
            r = pipe.ask(q["question"])
        except llm.LLMError as e:
            print(f"{q['id']}: {e}"); continue
        a, c = r.get("answer") or {}, r.get("checks") or {}
        cited = c.get("verified", [])
        row = {"id": q["id"], "model": r.get("model"), "cached": r.get("cached", False),
               "seconds": round(time.time() - t, 1), "grounding": c.get("grounding"),
               "tokens": sum((u or {}).get("total_tokens", 0) for u in (r.get("usage") or {}).values()),
               "cited": cited, "warnings": len(c.get("warnings", [])), "confidence": a.get("confidence")}
        if q["answerable"]:
            row["provision_recall"] = len(set(q["expected"]) & set(cited)) / len(q["expected"])
            row["provision_precision"] = (len(set(cited) & set(q["expected"] + q["acceptable"])) / len(set(cited))) if cited else 0.0
        else:
            # a good answer admits the gap: out of scope, or says what the sources don't cover
            row["abstained"] = (a.get("in_scope") is False) or bool(a.get("not_in_sources", "").strip())
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if not row["cached"] and i < len(qs) - 1: time.sleep(pause)    # stay under ~8K tokens a minute
    ans = [r for r in rows if "provision_recall" in r]
    summary = {"n": len(rows),
               "provision_recall": round(statistics.mean(r["provision_recall"] for r in ans), 3) if ans else None,
               "provision_precision": round(statistics.mean(r["provision_precision"] for r in ans), 3) if ans else None,
               "grounding": round(statistics.mean(r["grounding"] for r in rows if r["grounding"] is not None), 3) if rows else None,
               "abstention": [r["abstained"] for r in rows if "abstained" in r],
               "mean_seconds": round(statistics.mean(r["seconds"] for r in rows if not r["cached"]), 1) if any(not r["cached"] for r in rows) else None,
               "mean_tokens": round(statistics.mean(r["tokens"] for r in rows if not r["cached"])) if any(not r["cached"] for r in rows) else None}
    out = {"kind": "answers", "set": SET, "when": time.strftime("%Y-%m-%d %H:%M"), "summary": summary, "rows": rows}
    (RESULTS / f"answers-{SET}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print("summary:", json.dumps(summary))
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["retrieval", "answers"])
    ap.add_argument("--plan", action="store_true", help="retrieval: also evaluate hybrid + LLM planner")
    ap.add_argument("--n", type=int, default=10, help="answers: how many questions")
    ap.add_argument("--ids", nargs="*", help="answers: only these question ids")
    ap.add_argument("--pause", type=float, default=40, help="answers: seconds between uncached questions")
    ap.add_argument("--set", default="questions", choices=["questions", "heldout"])
    a = ap.parse_args()
    SET = a.set
    RESULTS.mkdir(parents=True, exist_ok=True)
    retrieval(a.plan) if a.what == "retrieval" else answers(a.n, a.ids, a.pause)


######
# Evaluation script. Retrieval is scored locally against the provisions a patent attorney would expect (recall@k,
# MRR) for BM25 alone, vectors alone, the hybrid, and the hybrid with the LLM planner - the ablation that shows what
# each part adds. Answers are scored on whether the expected provisions were cited and verified, on precision, on
# grounding, and on admitting gaps for out-of-scope questions (US law, trademarks).

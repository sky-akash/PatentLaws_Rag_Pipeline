import copy
from patentlaw import answer, llm

GOOD = {
    "language": "en", "in_scope": True, "question_type": "scenario",
    "understanding": {"restated": "r", "legal_question": "q", "key_facts": ["f"], "assumptions": []},
    "short_answer": "Yes, under Art. 55 EPC if filed within six months; see also Article 999.",
    "applicable_law": [
        {"source": 1, "provision": "Art. 55 EPC", "pinpoint": "(1)(b)", "role": "exception",
         "why_it_applies": "w", "plain_meaning": "p", "legal_meaning": "l"},
        {"source": 1, "provision": "Art. 54 EPC", "pinpoint": "(7)", "role": "governing",
         "why_it_applies": "w", "plain_meaning": "p", "legal_meaning": "l"},
        {"source": 2, "provision": "Art. 87 EPC", "pinpoint": "", "role": "related",
         "why_it_applies": "w", "plain_meaning": "p", "legal_meaning": "l"}],
    "application": [{"step": "s", "sources": [1, 9]}], "distinctions": [], "scenarios": [],
    "missing_facts": [], "not_in_sources": "", "confidence": "medium"}

class FakeClient:
    ready = True
    def __init__(self): self.calls = []
    def json(self, model, system, user, schema, name, **kw):
        self.calls.append(name)
        if name == "search_plan":
            return {"language": "en", "in_scope": True, "restated": "r",
                    "search_queries": ["non-prejudicial disclosure exhibition"], "candidate_provisions": ["Art. 55 EPC"]}, {"total_tokens": 10}
        if name == "explanation_check":
            # first item fine, every other one flagged as not supported
            n = user.count("\nLAW TEXT:")
            return {"items": [{"index": i, "supported": "yes" if i == 0 else "no", "relevant": "yes",
                               "note": "" if i == 0 else "misdescribes the provision"} for i in range(n)]}, {"total_tokens": 20}
        return copy.deepcopy(GOOD), {"total_tokens": 100}

def test_verify_fixes_and_flags(db, retriever):
    sources = retriever.search("international exhibition disclosure Art. 55 and Art. 54")
    n = {s["provision_id"]: s["n"] for s in sources}
    a = copy.deepcopy(GOOD)
    checks = answer.verify(db, a, sources)
    kept = {x["provision_id"]: x for x in a["applicable_law"]}
    assert kept["A55"]["source"] == n["A55"]                      # wrong source number corrected
    assert kept["A55"]["pinpoint"] == "(1)" or kept["A55"]["pinpoint"] == "(1)(b)"
    assert kept["A55"]["law_text"] and kept["A55"]["verified"]
    assert kept["A54"]["pinpoint"] == ""                          # paragraph (7) does not exist
    assert any("(7)" in w for w in checks["warnings"])
    assert [u["provision_id"] for u in checks["unverified"]] == ["A87"]   # not retrieved: kept out of the answer
    assert any("Art. 999 EPC" in w for w in checks["warnings"])   # invented provision in the prose
    assert a["application"][0]["sources"] == [1]                  # citation [9] matches no source
    assert checks["grounding"] == round(2 / 3, 2)

def test_pipeline_end_to_end_and_cache(db, retriever):
    fake = FakeClient()
    pipe = answer.Pipeline(db, retriever, fake)
    r = pipe.ask("I displayed my invention at an exhibition. Can I still patent it?")
    assert fake.calls == ["search_plan", "legal_explanation", "explanation_check"]
    law = r["answer"]["applicable_law"]
    assert law and r["checks"]["verified"] and r["checks"]["judged"]
    assert law[0]["judge"]["supported"] == "yes" and law[-1]["judge"]["supported"] == "no"   # flagged ones sink
    assert any("misdescribes" in w for w in r["checks"]["warnings"])
    assert "suggested by the planner" in {w for s in r["sources"] for w in s["why"]}
    again = pipe.ask("I displayed my invention at an exhibition. Can I still patent it?")
    assert again["cached"] and len(fake.calls) == 3               # no new model calls

def test_rate_limit_falls_back_to_small_model(db, retriever):
    class Limited(FakeClient):
        def json(self, model, *a, **kw):
            if model == "big": raise llm.RateLimited("limited", 300)
            return super().json(model, *a, **kw)
    pipe = answer.Pipeline(db, retriever, Limited(), answer_model="big", plan_model="small")
    assert pipe.ask("exhibition disclosure", use_cache=False)["model"] == "small"

def test_no_key_message():
    c = llm.ChatClient(url="https://api.groq.com/openai/v1", key="")
    assert not c.ready
    try:
        c.json("m", "s", "u", {}, "n"); assert False
    except llm.LLMError as e:
        assert "GROQ_API_KEY" in str(e)

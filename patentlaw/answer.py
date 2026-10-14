"""The question-answering pipeline: plan -> retrieve -> answer -> verify -> judge.
1. a small model rewrites the question in the EPC's own vocabulary and guesses the relevant provisions;
2. hybrid retrieval finds the law (and checks the guesses against the real corpus);
3. the big model explains it in a fixed structure, citing only numbered sources;
4. every citation is checked in code against the sources and the law text, and the small model then checks that each
   explanation says what the verbatim law says and that the provision is relevant, before anything is shown."""
import hashlib, json, re, time
from . import config, llm, refs, store
from .retrieve import Retriever

# ---- JSON schemas (Groq strict mode: every field required, no extra fields) ----
def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
S, B = {"type": "string"}, {"type": "boolean"}
LS, LI = {"type": "array", "items": S}, {"type": "array", "items": {"type": "integer"}}

PLAN_SCHEMA = _obj({
    "language": S, "in_scope": B, "restated": S, "search_queries": LS, "candidate_provisions": LS})

ANSWER_SCHEMA = _obj({
    "language": S,
    "in_scope": B,
    "question_type": {"type": "string", "enum": ["scenario", "meaning_of_law", "procedure_or_deadline", "comparison", "out_of_scope"]},
    "understanding": _obj({"restated": S, "legal_question": S, "key_facts": LS, "assumptions": LS}),
    "short_answer": S,
    "applicable_law": {"type": "array", "items": _obj({
        "source": {"type": "integer"}, "provision": S, "pinpoint": S,
        "role": {"type": "string", "enum": ["governing", "exception", "related", "procedural"]},
        "why_it_applies": S, "plain_meaning": S, "legal_meaning": S})},
    "application": {"type": "array", "items": _obj({"step": S, "sources": LI})},
    "distinctions": {"type": "array", "items": _obj({"this": S, "versus": S, "difference": S, "sources": LI})},
    "scenarios": {"type": "array", "items": _obj({"if": S, "then": S, "sources": LI})},
    "missing_facts": LS,
    "not_in_sources": S,
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
})

JUDGE_SCHEMA = _obj({"items": {"type": "array", "items": _obj({
    "index": {"type": "integer"},
    "supported": {"type": "string", "enum": ["yes", "partly", "no"]},
    "relevant": {"type": "string", "enum": ["yes", "partly", "no"]},
    "note": S})}})

JUDGE_SYSTEM = """You check explanations of European patent law provisions. For each numbered item you get the
QUESTION, the verbatim LAW TEXT of one provision, and an EXPLANATION of it written for that question.
- supported: does the law text itself say what the explanation claims it says? "yes", "partly" (some claim goes beyond
  or distorts the text), "no" (the explanation misdescribes the provision).
- relevant: does this provision actually bear on the question? "yes", "partly", "no".
- note: one short sentence naming the problem, or "" if supported and relevant.
Judge only against the law text given; be strict about time limits, conditions and the subject of the provision."""

PLAN_SYSTEM = """You plan searches over European patent law: the European Patent Convention (EPC) Articles, its
Implementing Regulations (Rules), the Rules relating to Fees, and the EPO Guidelines for Examination.
Given a user's question or factual scenario (in any language):
- language: the ISO 639-1 code of the user's language.
- in_scope: true if it concerns patents or European patent procedure, false otherwise.
- restated: one sentence stating the legal question in English.
- search_queries: 3 to 5 short English search queries in the EPC's own vocabulary (e.g. "showed it at a trade fair"
  -> "non-prejudicial disclosure officially recognised international exhibition"; "grace period" -> "non-prejudicial
  disclosures six months"; "add new example after filing" -> "amendments added subject-matter content as filed").
- candidate_provisions: up to 6 provisions you believe govern the question, written like "Art. 55 EPC",
  "Rule 25 EPC", "G-IV, 7". They will be checked against the real texts, so list only ones you are fairly sure of."""

ANSWER_SYSTEM = """You explain European patent law (EPC and EPO practice) to someone who brought a question or a
factual scenario. You receive numbered SOURCES: the only law you may rely on.

Rules:
- Every legal statement must come from the sources; cite them by number in the "source"/"sources" fields.
- applicable_law: the provisions that decide the question, most important first. "provision" is the label exactly as
  in the source header (e.g. "Art. 54 EPC", "Rule 56 EPC", "Guidelines G-IV, 7.2"); "pinpoint" is the paragraph or item
  that matters, written like "(3)" or "(1)(b)", or "" if the whole provision applies. "plain_meaning" explains it to a
  non-lawyer in one or two sentences. "legal_meaning" gives the precise legal-technical reading: conditions,
  defined terms, time limits and legal effect, as the text states them.
- understanding: restate what the person is really asking, then the precise legal question, the facts that matter
  legally, and any assumption you had to make.
- application: apply the provisions to the facts step by step, like a patent attorney would.
- distinctions: concepts or provisions that are easily confused here (e.g. Art. 54(2) vs 54(3) prior art; novelty vs
  inventive step; further processing vs re-establishment) and the exact difference.
- scenarios: 2 to 4 "if ... then ..." variations of the facts that would change the outcome.
- missing_facts: what you would need to know to answer with certainty.
- not_in_sources: what the question needs that the sources do not contain (e.g. case law, national law, fee
  amounts not shown, US law). Never fill such gaps from memory; leave them here.
- Hierarchy: the Articles prevail over the Rules (Art. 164(2) EPC); the Guidelines describe EPO practice and are not
  legally binding. Say so when it matters.
- Do not invent provisions, paragraph numbers, decision numbers, deadlines or amounts.
- List only provisions that actually bear on the question. A source being provided does not make it relevant;
  ignore sources that don't apply. Describe each provision only by what its text says, never by what you'd expect.
- Facts not in the sources (which authority issues a document, which fairs are recognised) go in not_in_sources or
  missing_facts, not in the explanation.
- Never let an assumption decide the outcome. If the answer turns on a fact the person did not give, short_answer
  starts with "It depends on ..." and states both outcomes, the decisive fact goes first in missing_facts, and
  confidence is at most "medium".
- If the question is not about patents, set in_scope=false, question_type="out_of_scope", leave the lists empty and
  say briefly what this tool covers.
- Write all text fields in the user's language (field names stay in English). Be precise and concise."""

def _sources_block(sources: list[dict]) -> str:
    # what the answering model reads: '[3] Art. 55 EPC – Non-prejudicial disclosures | Part II › ... (1)–(2)' + text
    return "\n\n".join(f"[{s['n']}] {s['header']}" + (f"  (paragraphs {s['pins']})" if s["pins"] else "") + f"\n{s['text']}"
                       for s in sources)

class Pipeline:
    def __init__(self, db, retriever: Retriever, client: llm.ChatClient | None = None,
                 answer_model: str = config.ANSWER_MODEL, plan_model: str = config.PLAN_MODEL):
        self.db, self.retriever = db, retriever
        self.client = client or llm.ChatClient()
        self.answer_model, self.plan_model = answer_model, plan_model
        self.use_judge = True
        db.execute("create table if not exists answer_cache(key text primary key, value text, created real)")

    # ---- step 1: plan ----
    def plan(self, question: str) -> tuple[dict | None, dict]:
        # a failed plan is not fatal: retrieval then works from the question alone
        try:
            return self.client.json(self.plan_model, PLAN_SYSTEM, question, PLAN_SCHEMA, "search_plan", max_tokens=700)
        except llm.LLMError:
            return None, {}

    # ---- step 4b: judge ----
    def judge(self, question: str, answer: dict, checks: dict) -> dict:
        items = answer.get("applicable_law", [])
        if not items: return {}
        # the judge reads the whole provision (a pinpointed item alone can lack the conditions around it)
        full = {p["provision_id"]: (store.provision(self.db, p["provision_id"]) or {}).get("text", p["law_text"]) for p in items}
        user = f"QUESTION:\n{question}\n\n" + "\n\n".join(
            f"ITEM {i}: {p['label']} - {p['title']}\nLAW TEXT:\n{full[p['provision_id']][:1800]}\nEXPLANATION:\n"
            f"Why it applies: {p['why_it_applies']}\nMeaning: {p['legal_meaning']}" for i, p in enumerate(items))
        try:
            out, usage = self.client.json(self.plan_model, JUDGE_SYSTEM, user, JUDGE_SCHEMA, "explanation_check", max_tokens=900)
        except llm.LLMError:
            checks["judged"] = False; return {}
        # attach each verdict to its provision; a 'no' on either question is a visible warning, never hidden
        for v in out.get("items", []):
            if 0 <= v.get("index", -1) < len(items):
                p = items[v["index"]]
                p["judge"] = {"supported": v["supported"], "relevant": v["relevant"], "note": v["note"]}
                if v["supported"] == "no" or v["relevant"] == "no":
                    checks["warnings"].append(f"{p['label']}: {v['note'] or 'explanation not supported by the text'}")
        ok = [p for p in items if p.get("judge", {}).get("supported") == "yes" and p["judge"].get("relevant") != "no"]
        checks["judged"] = True
        checks["faithfulness"] = round(len(ok) / len(items), 2)
        # flagged provisions sink to the bottom of the list
        answer["applicable_law"] = sorted(items, key=lambda p: (p.get("judge", {}).get("supported") == "no"
                                                                 or p.get("judge", {}).get("relevant") == "no"))
        return usage

    # ---- the whole pipeline ----
    def ask(self, question: str, use_plan: bool = True, use_cache: bool = True) -> dict:
        question = question.strip()
        key = hashlib.sha1(f"{self.answer_model}|{use_plan}|{question}".encode()).hexdigest()
        if use_cache:
            hit = self.db.execute("select value from answer_cache where key=?", (key,)).fetchone()
            if hit: return {**json.loads(hit[0]), "cached": True}
        t0, timings, usage = time.time(), {}, {}

        # step 1: plan (rewrite the question into EPC vocabulary + candidate provisions)
        plan, u = self.plan(question) if (use_plan and self.client.ready) else (None, {})
        usage["plan"] = u; timings["plan"] = round(time.time() - t0, 2)

        # step 2: retrieve the law
        t1 = time.time()
        sources = self.retriever.search(question, (plan or {}).get("search_queries"), (plan or {}).get("candidate_provisions"))
        timings["retrieve"] = round(time.time() - t1, 2)
        if not sources:
            return {"question": question, "answer": None, "sources": [], "plan": plan,
                    "error": "Nothing relevant was found in the indexed law. Try naming a provision or rephrasing."}

        # step 3: answer from the numbered sources (falls back to the smaller model if the big one is rate-limited)
        t2, model = time.time(), self.answer_model
        user = f"QUESTION:\n{question}\n\nSOURCES:\n{_sources_block(sources)}"
        try:
            answer, usage["answer"] = self.client.json(model, ANSWER_SYSTEM, user, ANSWER_SCHEMA, "legal_explanation",
                                                       max_tokens=config.ANSWER_TOKENS)
        except llm.RateLimited:
            model = self.plan_model
            answer, usage["answer"] = self.client.json(model, ANSWER_SYSTEM, user, ANSWER_SCHEMA, "legal_explanation",
                                                       max_tokens=config.ANSWER_TOKENS)
        timings["answer"] = round(time.time() - t2, 2)

        # step 4: verify every citation against the sources and the law text (code), then let a second model check
        # that each explanation says what the law text says and that the provision is relevant (judge)
        checks = verify(self.db, answer, sources)
        # an answer that depends on an unknown fact can't be 'high' confidence, whatever the model says
        if re.match(r"\s*(it depends|dipende|cela dépend|es kommt darauf an|depende)", answer.get("short_answer", ""), re.I) \
                and answer.get("confidence") == "high":
            answer["confidence"] = "medium"
        t3 = time.time()
        usage["judge"] = self.judge(question, answer, checks) if self.use_judge else {}
        timings["judge"] = round(time.time() - t3, 2)
        result = {"question": question, "answer": answer, "sources": sources, "plan": plan, "checks": checks,
                  "model": model, "usage": usage, "timings": {**timings, "total": round(time.time() - t0, 2)}}
        if answer.get("in_scope") is not False:
            self.db.execute("insert or replace into answer_cache values(?,?,?)", (key, json.dumps(result), time.time()))
            self.db.commit()
        return result

# ---- step 4: verification, in plain code (no model involved) ----
_PIN = re.compile(r"\((\d+[a-z]?)\)(?:\s*\(([a-z])\))?")

def _pin(text: str) -> str:
    # '54(3)', '(3)', 'paragraph (1)(b)' -> '(3)' / '(1)(b)'
    m = _PIN.search(text or "")
    return "" if not m else f"({m.group(1)})" + (f"({m.group(2)})" if m.group(2) else "")

def verify(db, answer: dict, sources: list[dict]) -> dict:
    by_n = {s["n"]: s for s in sources}
    retrieved = {}
    for s in sources: retrieved.setdefault(s["provision_id"], s)
    warnings, verified, unverified = [], [], []

    # 1. each provision the answer relies on must be one of the retrieved sources, and its pinpoint must exist
    kept = []
    for item in answer.get("applicable_law", []):
        src = by_n.get(item.get("source"))
        pid = refs.normalise(item.get("provision", "")) or (src["provision_id"] if src else None)
        if pid and src and pid != src["provision_id"] and pid in retrieved:
            src = retrieved[pid]; item["source"] = src["n"]          # right provision, wrong number: fix the number
        elif pid and pid not in retrieved and src and refs.normalise(item.get("provision", "")) is None:
            pid = src["provision_id"]                               # label unreadable: trust the cited source
        if not pid or pid not in retrieved:
            exists = bool(pid and db.execute("select 1 from provisions where id=?", (pid,)).fetchone())
            unverified.append({**item, "provision_id": pid, "exists_in_corpus": exists,
                               "issue": "not among the retrieved sources" if exists else "unknown provision"})
            continue
        prov = store.provision(db, pid)
        pin = _pin(item.get("pinpoint", ""))
        pins = {p.get("pin", "") for p in prov["paragraphs"]}
        if pin and pin not in pins and pin.split(")")[0] + ")" in pins:
            pin = pin.split(")")[0] + ")"                         # '(1)(b)' written inside paragraph (1): cite (1)
        if pin and pin not in pins:
            warnings.append(f"{refs.label(pid)}: paragraph {pin} does not exist; showing the whole provision.")
            pin = ""
        # the verbatim law for what is cited, so the reader can check the explanation against it
        # an item like '(1)(b)' only makes sense with the opening words of its paragraph '(1)', so those come too
        parent = pin.split(")")[0] + ")" if pin.count("(") > 1 else ""
        paras = [p for p in prov["paragraphs"] if not pin or p.get("pin", "").startswith(pin) or p.get("pin") == parent]
        item.update({"provision_id": pid, "pinpoint": pin, "label": refs.label(pid, pin) if prov["kind"] != "guideline"
                     else prov["label"], "title": prov["title"], "url": prov["url"], "verified": True,
                     "law_text": "\n".join(((p["num"] + " ") if p["num"] else "") + p["text"] for p in paras)[:2500]})
        kept.append(item); verified.append(pid)
    answer["applicable_law"] = kept

    # 2. source numbers in the other sections must point at real sources
    for sec in ("application", "distinctions", "scenarios"):
        for it in answer.get(sec, []):
            bad = [n for n in it.get("sources", []) if n not in by_n]
            if bad: warnings.append(f"{sec}: removed citation(s) {bad} that match no source.")
            it["sources"] = [n for n in it.get("sources", []) if n in by_n]

    # 3. provisions named anywhere in the prose but not retrieved: show them as 'mentioned, not checked'
    prose = " ".join([answer.get("short_answer", "")] +
                     [x.get("step", "") for x in answer.get("application", [])] +
                     [f"{x.get('this','')} {x.get('versus','')} {x.get('difference','')}" for x in answer.get("distinctions", [])] +
                     [f"{x.get('if','')} {x.get('then','')}" for x in answer.get("scenarios", [])] +
                     [f"{x.get('plain_meaning','')} {x.get('legal_meaning','')}" for x in kept])
    mentioned = []
    for pid in refs.find_refs(prose):
        if pid in retrieved or pid.startswith("GL:") and "," not in pid: continue
        exists = bool(db.execute("select 1 from provisions where id=?", (pid,)).fetchone())
        mentioned.append({"provision_id": pid, "label": refs.label(pid), "exists_in_corpus": exists})
        if not exists: warnings.append(f"{refs.label(pid)} is mentioned but does not exist in the indexed law.")

    total = len(verified) + len(unverified)
    return {"verified": verified, "unverified": unverified, "mentioned_not_retrieved": mentioned,
            "warnings": warnings, "grounding": round(len(verified) / total, 2) if total else None}


######
# Answer module: the agentic part of the app. A small model plans the search (EPC vocabulary + candidate
# provisions), hybrid retrieval finds the law, a large model explains it in a fixed JSON structure (what you're asking,
# which provisions apply and why, plain vs legal meaning, application to the facts, distinctions, what-if scenarios,
# missing facts), verify() checks every citation in code (right source, real paragraph, verbatim law attached), and
# judge() has the small model check each explanation against that verbatim text, flagging anything stretched.
# Answers are cached so a repeated question costs no tokens.

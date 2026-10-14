"""The web API (FastAPI) and the page that uses it. Start with: uvicorn patentlaw.api:create_app --factory --port 8010"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, embed, llm, refs, store
from .answer import Pipeline
from .retrieve import Retriever

class AskIn(BaseModel):
    # what the page sends: the question, and whether to let the planner rewrite it first
    question: str = Field(min_length=3, max_length=4000)
    use_plan: bool = True

def create_app(db=None, retriever: Retriever | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    # the parts can be handed in (the tests do that with fakes); otherwise the real ones are built from config
    db = db or store.connect()
    if retriever is None:
        retriever = Retriever(db, embed.collection(), embed.OllamaEmbedder())
    pipeline = pipeline or Pipeline(db, retriever)
    app = FastAPI(title="Patent Law Navigator", version="0.1",
                  description="Explains European patent law (EPC + EPO Guidelines) for a question or scenario, with checked citations.")

    @app.get("/api/health")
    def health():
        # what is indexed and what is configured, so problems are visible at a glance
        counts = {r[0]: r[1] for r in db.execute("select corpus, count(*) from provisions group by corpus")}
        return {"status": "ok" if counts else "empty index", "provisions": counts,
                "passages": db.execute("select count(*) from chunks").fetchone()[0],
                "llm_ready": pipeline.client.ready, "answer_model": pipeline.answer_model,
                "plan_model": pipeline.plan_model, "vector_search": not retriever.vectors_down}

    @app.post("/api/ask")
    def ask(body: AskIn):
        # the full pipeline: plan -> retrieve -> answer -> verify
        try:
            return pipeline.ask(body.question, use_plan=body.use_plan)
        except llm.RateLimited as e:
            raise HTTPException(429, str(e))
        except llm.LLMError as e:
            raise HTTPException(503, str(e))

    @app.get("/api/search")
    def search(q: str, k: int = 10):
        # retrieval only (no model, no tokens): 'browse the law'
        return {"query": q, "results": retriever.search(q, max_sources=min(max(k, 1), 25), budget_chars=60000)}

    @app.get("/api/provision/{ref:path}")
    def provision(ref: str):
        # one provision in full, with what it refers to and what refers to it (labels included for the page)
        pid = refs.normalise(ref)
        p = store.provision(db, pid) if pid else None
        if not p: raise HTTPException(404, f"No provision '{ref}' in the index.")
        p["refers_to"] = [{"id": x, "label": refs.label(x)} for x in p["refers_to"]]
        p["referred_by"] = [{"id": x, "label": refs.label(x)} for x in p["referred_by"]]
        p.pop("keywords", None)
        return p

    # the page itself
    app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(config.ROOT / "static" / "index.html")

    return app


######
# API module: four endpoints - /api/ask (the whole pipeline), /api/search (retrieval only, free), /api/provision/{ref}
# (the full text of 'Art. 54', 'R56a', 'G-VI, 3' ... with its cross-references both ways) and /api/health - plus the
# single-page interface at /. create_app() takes its parts as arguments, so tests run it with fakes and no network.

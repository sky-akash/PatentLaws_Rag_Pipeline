from fastapi.testclient import TestClient
from patentlaw.answer import Pipeline
from patentlaw.api import create_app
from test_answer import FakeClient

def client(db, retriever):
    return TestClient(create_app(db, retriever, Pipeline(db, retriever, FakeClient())))

def test_health_and_page(db, retriever):
    c = client(db, retriever)
    h = c.get("/api/health").json()
    assert h["provisions"]["epc"] == 5 and h["llm_ready"]
    assert "Patent Law Navigator" in c.get("/").text

def test_ask(db, retriever):
    r = client(db, retriever).post("/api/ask", json={"question": "Exhibition disclosure: can I still patent?"})
    assert r.status_code == 200 and r.json()["checks"]["verified"]

def test_ask_validates_input(db, retriever):
    assert client(db, retriever).post("/api/ask", json={"question": ""}).status_code == 422

def test_search_and_provision(db, retriever):
    c = client(db, retriever)
    assert c.get("/api/search", params={"q": "missing drawings"}).json()["results"][0]["provision_id"] == "R56"
    p = c.get("/api/provision/Art. 54 EPC").json()
    assert p["id"] == "A54" and {"id": "A53", "label": "Art. 53 EPC"} not in p["refers_to"]   # A53 isn't indexed here
    assert any(x["id"] == "A55" for x in p["referred_by"])
    assert c.get("/api/provision/Rule 999").status_code == 404

def ids(src): return [s["provision_id"] for s in src]

def test_named_provision_comes_first(retriever):
    src = retriever.search("What does Article 123(2) mean?")
    assert ids(src)[0] == "A123" and "named in your question" in src[0]["why"]

def test_keyword_scenario(retriever):
    src = retriever.search("I showed my invention at an international exhibition before filing")
    assert "A55" in ids(src)[:2]

def test_cross_references_followed(retriever):
    src = retriever.search("disclosure at an officially recognised international exhibition")
    # Art. 55 points to Art. 54 and Rule 25: both come along as cross-references
    assert {"A54", "R25"} <= set(ids(src))
    assert any(w.startswith("cross-reference") for s in src for w in s["why"])

def test_planner_hints_are_checked(retriever):
    src = retriever.search("grace period", hints=["Art. 55 EPC", "Art. 999 EPC"])
    assert "A55" in ids(src) and "A999" not in ids(src)

def test_budget_and_numbering(retriever):
    src = retriever.search("patent application", budget_chars=1500)
    assert sum(len(s["header"]) + len(s["text"]) for s in src) <= 1500
    assert [s["n"] for s in src] == list(range(1, len(src) + 1))

def test_bm25_only_without_vectors(db):
    from patentlaw.retrieve import Retriever
    src = Retriever(db).search("missing drawings filed later")
    assert ids(src)[0] == "R56"

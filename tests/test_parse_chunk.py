from patentlaw import chunk

def test_article_parsed(provisions):
    a54 = provisions[0]
    assert a54["id"] == "A54" and a54["title"] == "Novelty" and a54["kind"] == "article"
    assert a54["part"].startswith("Part II") and a54["chapter"].startswith("Chapter I")
    assert [p["pin"] for p in a54["paragraphs"]] == ["(1)", "(2)", "(3)", "(4)", "(5)"]
    assert "A53" in a54["xrefs"] and "G 2/08" in a54["cases"]
    assert "53(c)," in a54["text"]                      # no stray space before punctuation

def test_rule_items_and_guidelines(provisions):
    r56, gl = provisions[1], provisions[2]
    pins = [p["pin"] for p in r56["paragraphs"]]
    assert "(3)(a)" in pins and "" not in pins          # unnumbered sentences inherit their paragraph
    assert {"A90", "R56a", "R40"} <= set(r56["xrefs"])
    assert gl["id"] == "GL:G-VI,3" and gl["part"] == "Part G – Patentability"
    assert gl["xrefs"] == ["A54", "GL:G-IV,5.1"]

def test_chunks_respect_paragraphs(provisions):
    r56 = provisions[1]
    cs = chunk.chunk_provision(r56, max_chars=600)
    assert len(cs) > 1 and all(c["header"].startswith("Rule 56 EPC") for c in cs)
    assert cs[0]["pins"].startswith("(1)")
    assert "".join(c["text"] for c in cs).replace("\n", "") == r56["text"].replace("\n", "")

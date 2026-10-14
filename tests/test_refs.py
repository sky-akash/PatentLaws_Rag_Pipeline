from patentlaw import refs

def test_find_refs_articles_rules_and_guidelines():
    t = "See Art. 54(3) EPC, Rule 56a and Article 123(2); also G‑IV, 5.1 and R. 137(3)."
    assert refs.find_refs(t) == ["A54", "R56a", "A123", "GL:G-IV,5.1", "R137"]

def test_ranges_and_italian():
    assert refs.find_refs("Articles 52 to 57") == ["A52", "A53", "A54", "A55", "A56", "A57"]
    assert refs.find_refs("ai sensi dell'articolo 54 CBE e della regola 25") == ["A54", "R25"]

def test_no_false_guidelines_hits():
    assert refs.find_refs("e-voting and a-v equipment") == []

def test_normalise_and_label():
    assert refs.normalise("Art. 54(3) EPC") == "A54"
    assert refs.normalise("a105A") == "A105a"
    assert refs.normalise("Guidelines G-VI, 3") == "GL:G-VI,3"
    assert refs.normalise("nonsense") is None
    assert refs.label("A54", "(3)") == "Art. 54(3) EPC"
    assert refs.label("GL:G-VI,3") == "Guidelines G-VI, 3"
    assert refs.label("F2") == "Art. 2 RFees"

def test_from_href():
    assert refs.from_href("a53.html") == "A53"
    assert refs.from_href("/en/legal/epc/2020/r56a.html#R56a_1") == "R56a"
    assert refs.from_href("/en/legal/guidelines-epc/2026/g_iv_5_1.html") == "GL:G-IV,5.1"
    assert refs.from_href("protinta69.html") == "PROT69"
    assert refs.from_href("https://example.org/page.html") is None

def test_pinpoints_and_cases():
    assert refs.find_pinpoints("Article 90, paragraph 1, and Rule 56(2)") == [("A90", "(1)"), ("R56", "(2)")]
    assert refs.find_cases("See G 2/88, G 1/03 and T 641/00.") == ["G 2/88", "G 1/03", "T 641/00"]

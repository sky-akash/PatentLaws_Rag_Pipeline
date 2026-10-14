import sys, uuid
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patentlaw import chunk, embed, parse, store          # noqa: E402
from patentlaw.retrieve import Retriever                # noqa: E402

FIX = ROOT / "tests" / "fixtures"

def _prov(pid, label, title, paras, xrefs=(), corpus="epc", kind="article"):
    # a hand-made provision in the same shape parse.py produces
    ps = [{"num": n, "text": t, "pin": n} for n, t in paras]
    return {"id": pid, "corpus": corpus, "kind": kind, "label": label, "title": title, "part": "Part II", "chapter": "",
            "url": f"https://example.org/{pid}", "published": "2026-05-21", "paragraphs": ps,
            "text": "\n".join(f"{n} {t}" for n, t in paras), "xrefs": list(xrefs), "footnotes": [], "cases": [], "keywords": ""}

@pytest.fixture(scope="session")
def provisions():
    out = [parse.parse_page((FIX / f).read_text(), c, f) for f, c in
           [("a54.html", "epc"), ("r56.html", "epc"), ("g26_vi_3.html", "guidelines")]]
    out += [
        _prov("A55", "Art. 55 EPC", "Non-prejudicial disclosures", [
            ("(1)", "For the application of Article 54, a disclosure of the invention shall not be taken into consideration "
                    "if it occurred no earlier than six months preceding the filing of the European patent application and if "
                    "it was due to, or in consequence of: (a) an evident abuse in relation to the applicant or his legal "
                    "predecessor, or (b) the fact that the applicant or his legal predecessor has displayed the invention at an "
                    "official, or officially recognised, international exhibition."),
            ("(2)", "In the case of paragraph 1(b), paragraph 1 shall apply only if the applicant states, when filing the "
                    "European patent application, that the invention has been so displayed and files a supporting certificate "
                    "within the time limit and under the conditions laid down in the Implementing Regulations.")], ["A54", "R25"]),
        _prov("R25", "Rule 25 EPC", "Certificate of exhibition", [
            ("", "The applicant shall, within four months of filing the European patent application, file the certificate "
                 "referred to in Article 55, paragraph 2, issued at the exhibition.")], ["A55"], kind="rule"),
        _prov("A123", "Art. 123 EPC", "Amendments", [
            ("(2)", "The European patent application or European patent may not be amended in such a way that it contains "
                    "subject-matter which extends beyond the content of the application as filed."),
            ("(3)", "The European patent may not be amended in such a way as to extend the protection it confers.")]),
    ]
    return out

@pytest.fixture()
def db(provisions):
    d = store.connect(":memory:")
    store.save(d, provisions, [c for p in provisions for c in chunk.chunk_provision(p)])
    return d

@pytest.fixture()
def retriever(db):
    col = embed.collection(":memory:", name="t" + uuid.uuid4().hex[:8])
    embed.index_chunks(col, embed.HashEmbedder(), [dict(r) for r in db.execute("select * from chunks")], log=lambda *_: None)
    return Retriever(db, col, embed.HashEmbedder())

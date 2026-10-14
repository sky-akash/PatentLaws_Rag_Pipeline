"""Split provisions into passages for search, along the law's own structure (paragraphs), never mid-sentence."""

MAX_CHARS = 1400   # about 350 tokens: one short article whole, or a few paragraphs of a long one

def header(p: dict) -> str:
    # the line that tells the reader (and the embedding) what this text is and where it sits in the law
    where = " › ".join(x for x in (p.get("part"), p.get("chapter")) if x)
    return f"{p['label']} – {p['title']}" + (f" | {where}" if where else "")

def chunk_provision(p: dict, max_chars: int = MAX_CHARS) -> list[dict]:
    # step 1: each paragraph as one line, keeping its number ('(3)(a) ...')
    lines = [((q["num"] + " ") if q["num"] else "") + q["text"] for q in p["paragraphs"]]
    pins = [q.get("pin", "") for q in p["paragraphs"]]
    # step 2: fill passages paragraph by paragraph until the next one would make it too long
    groups, cur, cur_pins = [], [], []
    for line, pin in zip(lines, pins):
        if cur and sum(len(x) for x in cur) + len(line) > max_chars:
            groups.append((cur, cur_pins)); cur, cur_pins = [], []
        # one paragraph longer than a whole passage is cut at sentence ends
        while len(line) > max_chars:
            cut = line.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            groups.append(([line[:cut]], [pin])); line = line[cut:].strip()
        cur.append(line); cur_pins.append(pin)
    if cur: groups.append((cur, cur_pins))
    # step 3: one record per passage; 'pins' says which paragraphs it covers, e.g. '(1)–(3)'
    h = header(p)
    out = []
    for i, (body, ps) in enumerate(groups):
        named = [x for x in ps if x]
        span = "" if not named else (named[0] if named[0] == named[-1] else f"{named[0]}–{named[-1]}")
        out.append({"id": f"{p['id']}#{i}", "provision_id": p["id"], "seq": i, "pins": span,
                    "header": h, "text": "\n".join(body)})
    return out


######
# Chunk module: cuts each provision into passages of ~350 tokens along paragraph boundaries, so a passage is always
# a complete piece of law with its paragraph numbers, and adds a header line (label, title, Part › Chapter) that is
# embedded and searched together with the text.

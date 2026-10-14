// Patent Law Navigator - the page: ask, show the structured explanation, open any provision in a side drawer.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const EXAMPLES = [
  "I presented my invention at a trade fair in Munich four months ago and haven't filed yet. Can I still get a European patent?",
  "My European application claims priority from a US provisional filed 13 months ago. Is the priority claim valid?",
  "During examination I want to add a new embodiment that wasn't in the application as filed. Is that allowed?",
  "What is the difference between Article 54(2) and Article 54(3) prior art?",
  "I missed the time limit to reply to an EPO communication. What can I do?",
  "Ho scoperto un nuovo uso medico di un farmaco già noto: posso brevettarlo all'EPO?",
];

// ---- tabs ----
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $("#tab-ask").hidden = b.dataset.tab !== "ask";
  $("#tab-browse").hidden = b.dataset.tab !== "browse";
}));

// ---- health line in the header ----
async function health() {
  try {
    const h = await (await fetch("/api/health")).json();
    const n = Object.entries(h.provisions).map(([k, v]) => `${v} ${k}`).join(" · ") || "nothing indexed";
    $("#health").innerHTML = `<span class="dot ${h.llm_ready ? "" : "off"}"></span>${esc(n)} · ${h.passages} passages<br>` +
      (h.llm_ready ? `model ${esc(h.answer_model)}` : "add GROQ_API_KEY to .env to get answers") +
      (h.vector_search ? "" : " · vector search off (start Ollama)");
  } catch { $("#health").textContent = "API not reachable"; }
}

// ---- examples ----
EXAMPLES.forEach((e) => {
  const b = document.createElement("button"); b.type = "button"; b.textContent = e.length > 70 ? e.slice(0, 68) + "…" : e;
  b.title = e; b.onclick = () => { $("#q").value = e; $("#q").focus(); };
  $("#examples").appendChild(b);
});

// ---- small renderers ----
const refBtn = (id, label) => `<button class="ref" data-ref="${esc(id)}">${esc(label)}</button>`;
const cites = (ns) => (ns || []).map((n) => `<button class="cite" data-src="${n}">[${n}]</button>`).join("");
const list = (xs) => xs && xs.length ? `<ul class="tight">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
const card = (title, body, count) => body ? `<section class="card"><h2>${esc(title)}${count ? ` <span class="n">${count}</span>` : ""}</h2>${body}</section>` : "";

function render(r) {
  const a = r.answer, c = r.checks || {};
  if (!a) return `<div class="status err">${esc(r.error || "No answer.")}</div>`;
  let h = "";

  // 1. the short answer, with how sure and how well-grounded it is
  const g = c.grounding;
  const gBadge = g === null || g === undefined ? "" :
    `<span class="badge ${g === 1 ? "ok" : g >= 0.5 ? "warn" : "bad"}">${c.verified.length} of ${c.verified.length + c.unverified.length} cited provisions verified</span>`;
  h += `<section class="card answer-box"><h2>Short answer</h2><p class="short">${esc(a.short_answer)}</p>
    <div class="badges"><span class="badge">${esc(a.question_type.replaceAll("_", " "))}</span>
    <span class="badge ${a.confidence === "high" ? "ok" : a.confidence === "medium" ? "warn" : "bad"}">confidence: ${esc(a.confidence)}</span>
    ${gBadge}${c.judged ? `<span class="badge ${c.faithfulness === 1 ? "ok" : c.faithfulness >= 0.5 ? "warn" : "bad"}">explanations faithful to the text: ${Math.round(c.faithfulness * 100)}%</span>` : ""}
    ${r.cached ? '<span class="badge">cached</span>' : ""}</div></section>`;

  // 2. what you're asking
  const u = a.understanding || {};
  h += card("What you're asking", `<dl class="kv">
    <dt>In other words</dt><dd>${esc(u.restated)}</dd>
    <dt>The legal question</dt><dd>${esc(u.legal_question)}</dd>
    ${u.key_facts?.length ? `<dt>Facts that matter</dt><dd>${list(u.key_facts)}</dd>` : ""}
    ${u.assumptions?.length ? `<dt>Assumptions made</dt><dd>${list(u.assumptions)}</dd>` : ""}</dl>`);

  // 3. which law applies, and what it means (plain vs legal-technical), with the verbatim text
  if (a.applicable_law?.length) h += card("Which law applies", a.applicable_law.map((p) => `
    <div class="prov">
      <div class="prov-head">${refBtn(p.provision_id, p.label)}<span class="prov-title">${esc(p.title)}</span>
        <span class="role ${p.role === "exception" ? "exception" : ""}">${esc(p.role)}</span>${cites([p.source])}${judgeBadge(p.judge)}</div>
      ${p.judge?.note ? `<p class="judge-note">Check: ${esc(p.judge.note)}</p>` : ""}
      <p class="why">${esc(p.why_it_applies)}</p>
      <div class="meanings"><div><h4>In plain words</h4>${esc(p.plain_meaning)}</div>
        <div><h4>Legal-technical meaning</h4>${esc(p.legal_meaning)}</div></div>
      <details class="law"><summary>Read the law${p.pinpoint ? ` (${esc(p.pinpoint)})` : ""}</summary>
        <div class="lawtext">${esc(p.law_text)}</div>
        <a href="${esc(p.url)}" target="_blank" rel="noopener" class="hint">Open on epo.org ↗</a></details>
    </div>`).join(""), a.applicable_law.length);

  // 4. applied to the facts
  if (a.application?.length) h += card("Applied to your situation",
    `<ol class="steps">${a.application.map((s) => `<li>${esc(s.step)}${cites(s.sources)}</li>`).join("")}</ol>`);

  // 5. what not to confuse
  if (a.distinctions?.length) h += card("Don't confuse", `<table class="dist"><tr><th>This</th><th>versus</th><th>The difference</th></tr>
    ${a.distinctions.map((d) => `<tr><td>${esc(d.this)}</td><td>${esc(d.versus)}</td><td>${esc(d.difference)}${cites(d.sources)}</td></tr>`).join("")}</table>`);

  // 6. what-if scenarios
  if (a.scenarios?.length) h += card("What if…", `<div class="scen">${a.scenarios.map((s) =>
    `<div><div class="if">If ${esc(s.if.replace(/^if\s+/i, ""))}</div><div class="then">→ ${esc(s.then)}${cites(s.sources)}</div></div>`).join("")}</div>`);

  // 7. gaps
  h += card("To be certain, we'd need to know", list(a.missing_facts));
  if (a.not_in_sources) h += card("Not covered by the sources", `<p>${esc(a.not_in_sources)}</p>`);

  // 8. checks the code made on the answer
  const warn = [...(c.warnings || []),
    ...(c.unverified || []).map((x) => `${x.provision}: ${x.issue}`),
    ...(c.mentioned_not_retrieved || []).filter((x) => x.exists_in_corpus).map((x) => `${x.label} is mentioned but was not among the sources - open it to check.`)];
  if (warn.length) h += card("Checks", `<div class="warnbox">${list(warn)}</div>`);

  // 9. the sources the answer was allowed to use
  h += card("Sources", r.sources.map(srcHtml).join(""), r.sources.length);
  const tok = Object.values(r.usage || {}).reduce((s, u) => s + (u?.total_tokens || 0), 0);
  h += `<p class="meta">${esc(r.model || "")} · ${r.timings?.total ?? "?"} s${tok ? ` · ${tok} tokens` : ""}
    ${r.plan ? ` · searched as: ${esc(r.plan.search_queries.join(" | "))}` : ""}</p>`;
  return h;
}

function judgeBadge(j) {
  // the second model's verdict: does the law text say what the explanation claims, and does the provision apply here?
  if (!j) return "";
  if (j.supported === "no" || j.relevant === "no") return `<span class="badge bad">${j.supported === "no" ? "not supported by the text" : "probably not relevant"}</span>`;
  if (j.supported === "partly" || j.relevant === "partly") return `<span class="badge warn">partly supported</span>`;
  return `<span class="badge ok">text supports this</span>`;
}

function srcHtml(s) {
  return `<div class="src" id="src-${s.n}"><div class="src-head"><span class="src-n">[${s.n}]</span>${refBtn(s.provision_id, s.label)}
    <span class="prov-title">${esc(s.title)}${s.pins ? ` · ${esc(s.pins)}` : ""}</span>
    ${s.why.map((w) => `<span class="tag">${esc(w)}</span>`).join("")}</div>
    <details class="law"><summary>Show passage</summary><div class="lawtext">${esc(s.text)}</div></details></div>`;
}

// ---- asking ----
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = $("#q").value.trim();
  if (question.length < 3) return;
  $("#ask-btn").disabled = true; $("#result").innerHTML = "";
  const st = $("#status"); st.hidden = false; st.className = "status";
  st.textContent = "Planning the search, finding the law, writing the explanation… (10–30 s)";
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, use_plan: $("#use-plan").checked }) });
    const r = await res.json();
    if (!res.ok) throw new Error(r.detail || res.statusText);
    st.hidden = true; $("#result").innerHTML = render(r);
  } catch (err) { st.className = "status err"; st.textContent = String(err.message || err); }
  finally { $("#ask-btn").disabled = false; }
});

// ---- browsing ----
$("#browse-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#bq").value.trim(); if (!q) return;
  const r = await (await fetch(`/api/search?q=${encodeURIComponent(q)}&k=12`)).json();
  $("#browse-result").innerHTML = card(`Results for "${q}"`, r.results.map(srcHtml).join("") || "<p>Nothing found.</p>", r.results.length);
});

// ---- clicks: provision labels open the drawer, [n] jumps to the source ----
document.addEventListener("click", (e) => {
  const ref = e.target.closest("[data-ref]"); if (ref) { openProvision(ref.dataset.ref); return; }
  const c = e.target.closest("[data-src]");
  if (c) {
    const el = document.getElementById("src-" + c.dataset.src);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); }
  }
});
$("#d-close").onclick = () => { $("#drawer").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });

async function openProvision(id) {
  const res = await fetch(`/api/provision/${encodeURIComponent(id)}`);
  const d = $("#drawer"); d.hidden = false;
  if (!res.ok) { $("#d-label").textContent = id; $("#d-title").textContent = ""; $("#d-body").innerHTML = "<p>Not in the index.</p>"; return; }
  const p = await res.json();
  $("#d-label").textContent = p.label; $("#d-title").textContent = p.title;
  const chips = (xs) => xs.length ? `<div class="chips">${xs.map((x) => refBtn(x.id, x.label)).join("")}</div>` : "<p class='hint'>none</p>";
  $("#d-body").innerHTML = `<p class="hint">${esc([p.part, p.chapter].filter(Boolean).join(" › "))}</p>
    <div class="lawtext">${esc(p.text)}</div>
    <p><a href="${esc(p.url)}" target="_blank" rel="noopener">Open on epo.org ↗</a> <span class="hint">· as published ${esc(p.published)}</span></p>
    <h4>Refers to</h4>${chips(p.refers_to)}
    <h4>Referred to by</h4>${chips(p.referred_by.slice(0, 40))}
    ${p.cases.length ? `<h4>Decisions cited</h4><p>${p.cases.map(esc).join(", ")}</p>` : ""}
    ${p.footnotes.length ? `<h4>Footnotes</h4>${list(p.footnotes)}` : ""}`;
  $("#d-body").scrollTop = 0;
}

health();

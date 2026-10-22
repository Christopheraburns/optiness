// New investigation: pick or write a question, choose the workers, see the exact root prompt, start it.
import { api, esc, icon, post } from "../ui.js";

const form = { preset: null, question: "", scope: "", subagents: 3 };

function bridgeCard(info) {
  const b = info.bridge || {};
  if (!b.reachable) {
    return `<div class="side-card warn"><div class="side-title"><span class="dot bad"></span>Bridge not ready</div>
      <ul class="plain">${(b.problems || ["unknown"]).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <p class="muted small">Set the project variables and restart the console.</p></div>`;
  }
  const runner = b.runner || {};
  const busy = runner.current ? `Running ${esc(runner.current)}` : "Idle";
  const queued = runner.queued ? ` · ${runner.queued} queued` : "";
  return `<div class="side-card"><div class="side-title"><span class="dot ok"></span>Bridge ready</div>
    <dl class="facts">
      <dt>Runner</dt><dd>${busy}${queued}</dd>
      <dt>Root agent</dt><dd>${esc(info.root_model)}</dd>
      <dt>Workers</dt><dd>${esc(info.child_model)}</dd>
    </dl>
    ${runner.current ? `<p class="muted small">Investigations run one at a time; a new one waits in the queue.</p>` : ""}</div>`;
}

function presetCards(presets) {
  return presets.map((p) => `
    <button type="button" class="preset ${form.preset === p.id ? "on" : ""}" data-preset="${esc(p.id)}">
      <span class="preset-title">${esc(p.title)}</span>
      <span class="preset-desc">${esc(p.description)}</span>
      <span class="preset-meta">${p.subagents} ${p.subagents === 1 ? "worker" : "workers"}</span>
    </button>`).join("");
}

export async function render(view) {
  view.innerHTML = `<div class="muted">Loading…</div>`;
  let info;
  try { info = await api("/api/launch"); }
  catch (err) { view.innerHTML = `<div class="empty"><h3>Could not load</h3><p class="error">${esc(err.message)}</p></div>`; return; }

  const presets = info.presets || [];
  if (!form.question && presets.length) {
    form.preset = presets[0].id; form.question = presets[0].question; form.subagents = presets[0].subagents;
  }
  form.subagents = Math.min(form.subagents, info.max_children);

  view.innerHTML = `
    <a class="crumb" href="#/investigations">${icon.back}All investigations</a>
    <header class="page-head"><div>
      <div class="eyebrow">Prime Agent</div>
      <h1 class="page-title">New investigation</h1>
      <p class="page-sub">Ask a question about the data. The root agent plans, splits the work across parallel workers that query the warehouse, and reports findings backed by the SQL it ran.</p>
    </div></header>
    <div class="launch">
      <form class="launch-form" id="launch-form" autocomplete="off">
        <section>
          <div class="field-label">Start from</div>
          <div class="presets" id="presets">${presetCards(presets)}</div>
        </section>
        <section>
          <label class="field-label" for="q">Question</label>
          <textarea id="q" rows="5" class="input" spellcheck="true">${esc(form.question)}</textarea>
        </section>
        <section class="row2">
          <div>
            <label class="field-label" for="scope">Scope <span class="muted">(optional)</span></label>
            <input id="scope" class="input" placeholder="Limits for the agent, e.g. tables, dates or a segment" value="${esc(form.scope)}">
          </div>
          <div>
            <div class="field-label">Parallel workers</div>
            <div class="stepper">
              <button type="button" data-step="-1" aria-label="Fewer workers">−</button>
              <output id="workers">${form.subagents}</output>
              <button type="button" data-step="1" aria-label="More workers">+</button>
              <span class="muted small">max ${info.max_children}</span>
            </div>
          </div>
        </section>
        <div class="launch-actions">
          <button type="submit" class="btn primary" id="start" ${info.bridge?.reachable ? "" : "disabled"}>Start investigation</button>
          <span class="error" id="launch-error" role="alert"></span>
        </div>
      </form>
      <aside class="launch-side">
        ${bridgeCard(info)}
        <div class="side-card prompt-card">
          <div class="side-title">What the root agent receives</div>
          <p class="muted small">Your question inside the investigation method, exactly as the bridge sends it.</p>
          <pre class="prompt" id="prompt">…</pre>
        </div>
      </aside>
    </div>`;

  const $ = (sel) => view.querySelector(sel);
  let timer;
  const refreshPreview = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const { prompt } = await post("/api/launch/preview", { question: form.question, scope: form.scope, subagents: form.subagents });
        $("#prompt").textContent = prompt;
      } catch (err) { $("#prompt").textContent = err.message; }
    }, 200);
  };

  $("#presets").addEventListener("click", (e) => {
    const card = e.target.closest("[data-preset]");
    if (!card) return;
    const p = presets.find((x) => x.id === card.dataset.preset);
    Object.assign(form, { preset: p.id, question: p.question, subagents: Math.min(p.subagents, info.max_children) });
    $("#q").value = form.question; $("#workers").textContent = form.subagents;
    $("#presets").innerHTML = presetCards(presets);
    refreshPreview();
  });
  $("#q").addEventListener("input", (e) => {
    form.question = e.target.value;
    if (form.preset && form.question !== presets.find((x) => x.id === form.preset)?.question) {
      form.preset = null; $("#presets").innerHTML = presetCards(presets);
    }
    refreshPreview();
  });
  $("#scope").addEventListener("input", (e) => { form.scope = e.target.value; refreshPreview(); });
  view.querySelector(".stepper").addEventListener("click", (e) => {
    const step = Number(e.target.closest("[data-step]")?.dataset.step || 0);
    if (!step) return;
    form.subagents = Math.max(1, Math.min(info.max_children, form.subagents + step));
    $("#workers").textContent = form.subagents;
    refreshPreview();
  });
  $("#launch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#start");
    button.disabled = true; button.textContent = "Starting…"; $("#launch-error").textContent = "";
    try {
      const started = await post("/api/investigations", { question: form.question, scope: form.scope, subagents: form.subagents });
      form.question = ""; form.scope = ""; form.preset = null;
      location.hash = `#/investigations/${started.id}`;
    } catch (err) {
      $("#launch-error").textContent = err.message;
      button.disabled = false; button.textContent = "Start investigation";
    }
  });
  refreshPreview();
}

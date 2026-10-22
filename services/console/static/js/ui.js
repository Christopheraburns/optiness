// Small helpers shared by the views: escaping, formatting, API access.

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

export async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    let detail = `${response.status}`;
    try { detail = (await response.json()).error || detail; } catch { /* not JSON */ }
    throw new Error(`${path}: ${detail}`);
  }
  return response.json();
}

// Actions carry X-Console-Action: the server refuses POSTs without it (cross-site requests cannot set it).
export async function post(path, body = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Console-Action": "1" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await response.json(); } catch { /* empty or not JSON */ }
  if (!response.ok) throw new Error(data.error || `${path}: ${response.status}`);
  return data;
}

// A small, safe Markdown renderer for agent-written reports: everything is escaped first, then a
// fixed set of block and inline forms is applied (headings, lists, tables, fences, bold, italic, code).
export function markdown(src) {
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:]|$)/g, "$1<em>$2</em>");
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++;
      out.push(`<pre class="md-pre"><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { const n = Math.min(h[1].length + 1, 5); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line); i += 2;
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(cells(lines[i]));
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      for (; i < lines.length && (/^\s*([-*]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length)); i++) {
        if (/^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        else items[items.length - 1] += " " + lines[i].trim();
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*]|\d+\.)\s|\s*\|)/.test(lines[i]); i++) para.push(lines[i].trim());
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    else { out.push(`<p>${inline(line)}</p>`); i++; }
  }
  return out.join("\n");
}

export const fmtInt =(n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString("en-US"));

export function fmtCompact(n) {
  if (n === null || n === undefined) return "–";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}K`;
  return fmtInt(n);
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${r}s`;
}

export function fmtWhen(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

// Seconds since a run started, counting to now while it is still running.
export function elapsed(run) {
  if (run.elapsed_s !== null && run.elapsed_s !== undefined) return run.elapsed_s;
  if (run.status === "running" && run.started) return (Date.now() - new Date(run.started)) / 1000;
  return null;
}

export const STATUS_LABEL = { running: "Running", done: "Findings", ended: "Ended", stalled: "Stalled" };

export const statusPill = (status) =>
  `<span class="status ${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;

// Model chips: the root model, then each worker model with its count.
export function modelTags(run, { stacked = false } = {}) {
  const tags = [];
  if (run.root_model) {
    tags.push(`<span class="tag model-root">${esc(run.root_model)}<span class="x">root</span></span>`);
  }
  for (const [name, count] of Object.entries(run.child_models || {})) {
    tags.push(`<span class="tag">${esc(name)}<span class="x">× ${count}</span></span>`);
  }
  return stacked ? tags.join("") : tags.join(" ");
}

export const icon = {
  plus: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>`,
  back: `<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  chevron: `<svg class="chevron" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  search: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5" stroke-linecap="round"/></svg>`,
};

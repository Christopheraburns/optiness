// Investigation Console: hash router and shell. Views render into <main id="view">.
import { esc } from "./ui.js";
import * as investigations from "./views/investigations.js";
import * as investigation from "./views/investigation.js";

const placeholder = (title, eyebrow, text) => ({
  render(view) {
    view.innerHTML = `
      <header class="page-head"><div><div class="eyebrow">${esc(eyebrow)}</div><h1 class="page-title">${esc(title)}</h1>
      <p class="page-sub">${esc(text)}</p></div></header>
      <div class="empty fade-in"><h3>${esc(title)}</h3><p>Arrives in a later build phase.</p></div>`;
  },
});

const ROUTES = [
  [/^investigations\/(inv-\d{8}-\d{6})$/, "investigations", investigation],
  [/^investigations$/, "investigations", investigations],
  [/^overview$/, "overview", placeholder("Payment health", "Meridian Financial", "Authorization success over time, with anomalies detected from the warehouse.")],
  [/^dataset$/, "dataset", placeholder("The lakehouse", "Cloudera Data Warehouse", "The Iceberg tables the agents query: size, shape and relationships.")],
  [/^architecture$/, "architecture", placeholder("How it works", "Architecture", "Mistral Studio, the Prime Agent harness on Cloudera AI Workbench, and the warehouse, with live status.")],
];

let timers = [];
function clearTimers() { timers.forEach(clearInterval); timers = []; }

async function route() {
  clearTimers();
  const path = location.hash.replace(/^#\/?/, "") || "investigations";
  const match = ROUTES.map(([re, nav, view]) => [path.match(re), nav, view]).find(([m]) => m);
  if (!match) { location.hash = "#/investigations"; return; }
  const [m, nav, module] = match;
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === nav));
  const view = document.getElementById("view");
  view.scrollTo?.(0, 0); window.scrollTo(0, 0);
  await module.render(view, {
    params: m.slice(1),
    onTimer: (fn, ms) => { if (ms > 0) timers.push(setInterval(fn, ms)); },
  });
}


window.addEventListener("hashchange", route);
route();

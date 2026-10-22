// Investigation Console: hash router and shell. Views render into <main id="view">.
import * as investigations from "./views/investigations.js";
import * as investigation from "./views/investigation.js";
import * as launch from "./views/new.js";
import * as overview from "./views/overview.js";
import * as dataset from "./views/dataset.js";
import * as architecture from "./views/architecture.js";

const ROUTES = [
  [/^investigations\/(inv-\d{8}-\d{6})$/, "investigations", investigation],
  [/^investigations$/, "investigations", investigations],
  [/^new$/, "investigations", launch],
  [/^overview$/, "overview", overview],
  [/^dataset$/, "dataset", dataset],
  [/^architecture$/, "architecture", architecture],
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

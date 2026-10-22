// Overview: bankwide payment health from the warehouse, with the degradation detected from the data.
// Charts follow the dataviz method: one series per chart (no legend box, the title names it),
// no dual axis (success rate and volume are two aligned charts), 2px line, columns <= 24px with
// a 4px rounded data-end, hairline solid grid, crosshair tooltip, and a table view of every value.
import { api, esc, fmtCompact, fmtInt, icon } from "../ui.js";

const C = {  // validated against the #161729 surface (dataviz validate_palette.js, dark mode)
  line: "#7383f5", bars: "#5865d6", band: "rgba(245, 184, 61, 0.12)", bandEdge: "rgba(245, 184, 61, 0.55)",
  grid: "#262842", axis: "#34365a", text: "#eceef8", text2: "#b3b6d0", text3: "#7d80a0", surface: "#161729",
};
const charts = [];
let showTable = false;

const fmtPct = (v, digits = 2) => (v === null || v === undefined ? "–" : `${v.toFixed(digits)}%`);
const fmtPts = (v) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)} pts`;
const fmtDay = (iso, opts = { month: "short", day: "numeric" }) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });

function banner(d) {
  const a = d.anomaly, r = d.reference;
  if (!a) {
    return `<section class="health ok"><div class="health-badge"><span class="status done">Healthy</span></div>
      <div class="health-text">Authorization success held near its reference of ${fmtPct(r?.success_rate)} across all ${d.days.length} days.</div></section>`;
  }
  const when = `${fmtDay(a.start)} – ${fmtDay(a.end)}`;
  return `
    <section class="health warn fade-in">
      <div class="health-badge"><span class="status stalled"><span class="sr">Status:</span>Anomaly detected</span></div>
      <div class="health-text">
        <strong>Authorization success fell ${Math.abs(a.drop_pp).toFixed(2)} points for ${a.days} days</strong>
        (${esc(when)}), from ${fmtPct(r.success_rate)} to ${fmtPct(a.success_rate)}: about ${fmtCompact(a.excess_declines)} more declined
        payments than usual. ${a.recovered ? "It has since recovered." : "It has not recovered."}
      </div>
      <a class="btn primary" href="#/new">${icon.search}Investigate this drop</a>
    </section>`;
}

function tiles(d) {
  const a = d.anomaly, r = d.reference;
  return `
    <section class="kpis">
      <div class="kpi"><div class="kpi-label">Authorization success</div><div class="kpi-value">${fmtPct(d.success_rate)}</div>
        <div class="kpi-note">all ${d.days.length} days · reference ${fmtPct(r?.success_rate)}</div></div>
      <div class="kpi warn"><div class="kpi-label">Success during the anomaly</div><div class="kpi-value">${a ? fmtPct(a.success_rate) : "–"}</div>
        <div class="kpi-note">${a ? `${fmtPts(-a.drop_pp)} vs reference · worst day ${fmtPct(a.peak.success_rate)}` : "no anomaly detected"}</div></div>
      <div class="kpi warn"><div class="kpi-label">Extra declined payments</div><div class="kpi-value">${a ? fmtCompact(a.excess_declines) : "–"}</div>
        <div class="kpi-note">${a ? `over ${a.days} days, above the reference rate` : "–"}</div></div>
      <div class="kpi data"><div class="kpi-label">Transactions analysed</div><div class="kpi-value">${fmtCompact(d.total_attempts)}</div>
        <div class="kpi-note">${d.days.length} days, measured on the warehouse</div></div>
    </section>`;
}

function table(d) {
  return `<div class="table-wrap"><table class="data">
    <thead><tr><th>Day</th><th class="num">Attempts</th><th class="num">Declined</th><th class="num">Success rate</th></tr></thead>
    <tbody>${d.days.map((x) => {
      const inBand = d.anomaly && x.day >= d.anomaly.start && x.day <= d.anomaly.end;
      return `<tr class="${inBand ? "flag" : ""}"><td>${esc(fmtDay(x.day, { weekday: "short", month: "short", day: "numeric" }))}</td>
        <td class="num">${fmtInt(x.attempts)}</td><td class="num">${fmtInt(x.declines)}</td><td class="num">${fmtPct(x.success_rate)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function baseOptions(d) {
  return {
    backgroundColor: "transparent", animationDuration: 900, animationEasing: "cubicOut",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: C.text3 },
    grid: { left: 64, right: 96, top: 28, bottom: 36 },
    xAxis: {
      type: "category", data: d.days.map((x) => x.day), boundaryGap: true,
      axisLine: { lineStyle: { color: C.axis } }, axisTick: { show: false },
      axisLabel: { color: C.text3, fontSize: 12, interval: (i) => i % 7 === 0, formatter: (v) => fmtDay(v) },
    },
    tooltip: {
      trigger: "axis", axisPointer: { type: "line", lineStyle: { color: C.text3, width: 1 } },
      backgroundColor: "#0f1020", borderColor: "rgba(255,255,255,.12)", borderWidth: 1, padding: [10, 12],
      textStyle: { color: C.text, fontSize: 13 },
      formatter: (items) => {
        const x = d.days[items[0].dataIndex];
        const inBand = d.anomaly && x.day >= d.anomaly.start && x.day <= d.anomaly.end;
        const row = (v, l) => `<div class="tt-row"><b>${v}</b><span>${l}</span></div>`;
        const box = document.createElement("div");
        box.innerHTML = `<div class="tt-day"></div>${row(fmtPct(x.success_rate), "success rate")}${row(fmtInt(x.attempts), "attempts")}${row(fmtInt(x.declines), "declined")}${inBand ? `<div class="tt-flag">In the detected anomaly</div>` : ""}`;
        box.querySelector(".tt-day").textContent = fmtDay(x.day, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
        return box.innerHTML;
      },
    },
  };
}

function band(d, withLabel) {
  if (!d.anomaly) return undefined;
  return {
    silent: true, itemStyle: { color: C.band, borderColor: C.bandEdge, borderWidth: 0 },
    label: withLabel ? { show: true, position: "insideTop", color: C.text2, fontSize: 12, fontWeight: 600, distance: 8,
      formatter: `Anomaly · ${d.anomaly.days} days` } : { show: false },
    // Day indices, not date strings, so both charts place the band on exactly the same days.
    data: [[{ xAxis: d.days.findIndex((x) => x.day === d.anomaly.start) }, { xAxis: d.days.findIndex((x) => x.day === d.anomaly.end) }]],
  };
}

function successChart(el, d) {
  const values = d.days.map((x) => +x.success_rate.toFixed(3));
  const lo = Math.floor(Math.min(...values) - 0.6), hi = Math.ceil(Math.max(...values) + 0.4);
  const chart = echarts.init(el, null, { renderer: "svg" });
  const last = values.length - 1;
  chart.setOption({
    ...baseOptions(d),
    yAxis: {
      type: "value", min: lo, max: hi, interval: hi - lo > 4 ? 1 : 0.5, splitLine: { lineStyle: { color: C.grid, width: 1 } },
      axisLabel: { color: C.text3, fontSize: 12, formatter: (v) => `${v.toFixed(1)}%` },
    },
    series: [{
      type: "line", data: values, smooth: false, symbol: "circle", symbolSize: 8, showSymbol: false,
      lineStyle: { color: C.line, width: 2, cap: "round", join: "round" },
      itemStyle: { color: C.line, borderColor: C.surface, borderWidth: 2 },
      emphasis: { focus: "none", scale: false },
      areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [
        { offset: 0, color: "rgba(115,131,245,0.16)" }, { offset: 1, color: "rgba(115,131,245,0)" }] } },
      markArea: band(d, true),
      markLine: d.reference ? {
        silent: true, symbol: "none", lineStyle: { color: C.text3, width: 1, type: "solid", opacity: 0.6 },
        label: { show: false },  // named in the subtitle; an in-plot label collides with the data
        data: [{ yAxis: +d.reference.success_rate.toFixed(3) }],
      } : undefined,
      markPoint: {
        symbol: "circle", symbolSize: 9, itemStyle: { color: C.line, borderColor: C.surface, borderWidth: 2 },
        label: { show: true, position: "right", color: C.text, fontSize: 12, fontWeight: 600, distance: 8, formatter: fmtPct(values[last]) },
        data: [{ coord: [last, values[last]] }],
      },
    }],
  });
  return chart;
}

function volumeChart(el, d) {
  const chart = echarts.init(el, null, { renderer: "svg" });
  chart.setOption({
    ...baseOptions(d),
    grid: { left: 64, right: 96, top: 16, bottom: 36 },
    yAxis: {
      type: "value", splitNumber: 3, splitLine: { lineStyle: { color: C.grid, width: 1 } },
      axisLabel: { color: C.text3, fontSize: 12, formatter: (v) => fmtCompact(v) },
    },
    series: [{
      type: "bar", data: d.days.map((x) => x.attempts), barMaxWidth: 24, barCategoryGap: "18%",
      itemStyle: { color: C.bars, borderRadius: [4, 4, 0, 0] },
      emphasis: { itemStyle: { color: C.line } },
    }, {
      // The band rides an invisible line series: ECharts offsets a bar series' markArea start,
      // and the two charts must shade exactly the same days.
      type: "line", data: d.days.map(() => null), silent: true, showSymbol: false, tooltip: { show: false },
      lineStyle: { opacity: 0 }, markArea: band(d, false),
    }],
  });
  return chart;
}

function sqlPanel(d) {
  const q = d.query;
  const cached = d.cached_s ? `${Math.max(1, Math.round(d.cached_s / 60))} min ago (cached)` : "just now";
  return `<details class="sql-panel"><summary>SQL behind this view <span class="muted">· ${fmtInt(q.rows)} rows in ${fmtInt(q.elapsed_ms)} ms · measured ${cached}</span></summary>
    <pre><code class="language-sql"></code></pre></details>`;
}

export async function render(view) {
  charts.splice(0).forEach((c) => c.dispose());
  view.innerHTML = `
    <header class="page-head">
      <div><div class="eyebrow">Meridian Financial</div><h1 class="page-title">Payment health</h1>
        <p class="page-sub">Card authorization outcomes across every transaction in the lakehouse, measured live from the warehouse. Anomalies are detected from the data itself.</p></div>
      <div class="head-actions"><button class="btn" id="refresh">Refresh</button></div>
    </header>
    <div id="ov"><div class="muted">Measuring on the warehouse…</div></div>`;

  const load = async (refresh) => {
    const box = view.querySelector("#ov");
    box.classList.add("refetch");
    let d;
    try { d = await api(`/api/overview${refresh ? "?refresh=1" : ""}`); }
    catch (err) { d = { available: false, reason: err.message }; }
    box.classList.remove("refetch");
    if (!d.available) {
      box.innerHTML = `<div class="empty"><h3>Payment data is not available</h3><p>${esc(d.reason)}</p></div>`;
      return;
    }
    charts.splice(0).forEach((c) => c.dispose());
    box.innerHTML = `${banner(d)}${tiles(d)}
      <section class="panel chart-card">
        <div class="chart-head"><div><h2 class="chart-title">Daily authorization success rate</h2>
          <p class="chart-sub">${showTable
            ? `Every day's attempts and outcomes. Highlighted rows fall in the detected anomaly.`
            : `Share of authorization attempts approved, per day.${d.reference ? ` Gray line: the reference, ${fmtPct(d.reference.success_rate)} over the first ${d.reference.days} days.` : ""}`}</p></div>
          <button class="btn ghost" id="toggle-table">${showTable ? "Show charts" : "View as table"}</button></div>
        <div id="charts" ${showTable ? "hidden" : ""}>
          <div class="chart" id="c-success" style="height:380px"></div>
          <div class="chart-divider"><h3 class="chart-title small">Daily authorization attempts</h3><span class="muted small">volume per day, same days as above</span></div>
          <div class="chart" id="c-volume" style="height:190px"></div>
        </div>
        <div id="table" ${showTable ? "" : "hidden"}>${table(d)}</div>
      </section>
      ${sqlPanel(d)}`;
    const code = box.querySelector(".sql-panel code");
    code.textContent = d.query.sql;
    if (window.hljs) window.hljs.highlightElement(code);
    if (!showTable) {
      charts.push(successChart(box.querySelector("#c-success"), d), volumeChart(box.querySelector("#c-volume"), d));
      echarts.connect(charts);
    }
    box.querySelector("#toggle-table").addEventListener("click", () => { showTable = !showTable; load(false); });
  };

  view.querySelector("#refresh").addEventListener("click", () => load(true));
  window.addEventListener("resize", () => charts.forEach((c) => c.resize()));
  await load(false);
}

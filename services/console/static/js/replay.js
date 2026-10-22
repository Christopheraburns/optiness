// One clock for a run's timeline. Finished runs open at the end (everything drawn) and can be
// replayed from zero at a chosen speed; live runs follow the wall clock. Views subscribe and
// show what had happened by time `t` (seconds from the run's start).
import { esc } from "./ui.js";

export const SPEEDS = [10, 20, 40, 80, 160];
export const AGENT_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"];  // dataviz dark slots 1-7, validated on #161729
export const agentColor = (i) => AGENT_COLORS[i % AGENT_COLORS.length];

export const clockText = (s) => {
  if (s === null || s === undefined || Number.isNaN(s)) return "–";
  const v = Math.max(0, Math.round(s));
  return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")}`;
};

export class Replay {
  constructor() {
    this.t = 0; this.duration = 0; this.speed = 20; this.playing = false; this.live = false;
    this.listeners = new Set(); this._frame = null; this._last = null;
  }
  load(timeline, live) {
    this.duration = timeline.duration_s || 0;
    this.live = live;
    clearInterval(this._tick);
    if (live && timeline.started) {
      // Live: the cursor is the wall clock since the run started, ticking every second.
      const t0 = Date.parse(timeline.started);
      const tick = () => { this.t = Math.max(this.duration, (Date.now() - t0) / 1000); this.duration = this.t; this.emit(); };
      this._tick = setInterval(tick, 1000);
      this._wasLive = true;
      tick();
      return;
    }
    if (!this._initialized || this._wasLive) this.t = this.duration;  // finished runs open fully drawn
    this._initialized = true;
    this._wasLive = live;
    this.emit();
  }
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit() { this.listeners.forEach((fn) => fn(this.t)); }
  seek(t) { this.t = Math.max(0, Math.min(this.duration, t)); this.emit(); }
  play() {
    if (this.live) return;
    if (this.t >= this.duration) this.t = 0;
    this.playing = true; this._last = performance.now();
    const step = (now) => {
      if (!this.playing) return;
      this.t = Math.min(this.duration, this.t + ((now - this._last) / 1000) * this.speed);
      this._last = now;
      this.emit();
      if (this.t >= this.duration) { this.playing = false; this.emit(); return; }
      this._frame = requestAnimationFrame(step);
    };
    this._frame = requestAnimationFrame(step);
    this.emit();
  }
  pause() { this.playing = false; cancelAnimationFrame(this._frame); this.emit(); }
  dispose() { this.pause(); clearInterval(this._tick); this.listeners.clear(); }
}

// The control bar shared by the Investigation and Swarm tabs.
export function controls(replay, el) {
  if (replay.live) {
    el.innerHTML = `<div class="replay"><span class="status running">Live</span><span class="muted">Following the run as it happens · <span id="rp-time"></span></span></div>`;
    const time = el.querySelector("#rp-time");
    const off = replay.on((t) => { time.textContent = clockText(t); });
    replay.emit();
    return off;
  }
  el.innerHTML = `
    <div class="replay">
      <button class="btn primary rp-play" id="rp-play"></button>
      <input type="range" id="rp-scrub" min="0" max="${replay.duration}" step="0.1" value="${replay.t}" aria-label="Replay position">
      <span class="rp-time mono" id="rp-time"></span>
      <label class="rp-speed">Speed
        <select id="rp-speed">${SPEEDS.map((s) => `<option value="${s}" ${s === replay.speed ? "selected" : ""}>${s}×</option>`).join("")}</select>
      </label>
    </div>`;
  const play = el.querySelector("#rp-play"), scrub = el.querySelector("#rp-scrub"), time = el.querySelector("#rp-time");
  play.addEventListener("click", () => (replay.playing ? replay.pause() : replay.play()));
  // Read the position before pausing: pause() notifies this bar, which would reset the slider.
  scrub.addEventListener("input", () => { const to = Number(scrub.value); replay.pause(); replay.seek(to); });
  el.querySelector("#rp-speed").addEventListener("change", (e) => { replay.speed = Number(e.target.value); });
  const off = replay.on((t) => {
    scrub.value = t;
    time.textContent = `${clockText(t)} / ${clockText(replay.duration)}`;
    play.innerHTML = replay.playing ? `${esc("Pause")}` : (t >= replay.duration ? "▶ Replay" : "▶ Play");
  });
  replay.emit();
  return off;
}

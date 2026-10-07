// Top right: system vitals with btop-style braille graphs, Clef throughput and health alerts.
import { esc } from "../util.js";

const body = document.getElementById("vitals-body");
const HIST = 240;
const hist = { gpu_util: [], gpu_temp: [], cpu_util: [], cpu_temp: [], dpm: [] };
let alert = null;
let lastCheck = null;

body.innerHTML = `
  <div class="vitals">
    <div class="big-row">
      <div class="big" id="v-gpu">
        <div class="big-head"><span class="k">gpu load</span><span class="big-v"><b id="v-gpu-util">–</b><small>%</small></span></div>
        <pre class="braille" id="g-gpu-util"></pre>
      </div>
      <div class="big" id="v-temp">
        <div class="big-head"><span class="k">gpu temp</span><span class="big-v"><b id="v-gpu-temp">–</b><small>°C</small></span></div>
        <pre class="braille" id="g-gpu-temp"></pre>
      </div>
    </div>
    <div class="small-grid" id="v-small"></div>
    <div class="clef-row" id="v-clef"></div>
    <div class="health" id="v-health"></div>
  </div>`;
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- braille graph

// Each braille char is a 2x4 dot grid. Dot bits, top row first: left column 1,2,4,64; right column 8,16,32,128.
const LEFT = [0x01, 0x02, 0x04, 0x40];
const RIGHT = [0x08, 0x10, 0x20, 0x80];

function braille(values, cols, rows, min, max) {
  const dotsH = rows * 4;
  const data = values.slice(-cols * 2);
  while (data.length < cols * 2) data.unshift(null);
  const heights = data.map((v) => (v == null ? 0 : Math.round(Math.max(0, Math.min(1, (v - min) / (max - min))) * dotsH)));
  const lines = [];
  for (let r = 0; r < rows; r++) {
    let line = "";
    // Dot rows covered by this text row, counted from the bottom of the graph.
    const rowBottom = (rows - 1 - r) * 4;
    for (let c = 0; c < cols; c++) {
      let code = 0x2800;
      for (const [h, bits] of [[heights[c * 2], LEFT], [heights[c * 2 + 1], RIGHT]]) {
        for (let d = 0; d < 4; d++) {
          if (h > rowBottom + (3 - d)) code |= bits[d]; // d=0 is the top dot of the cell
        }
      }
      line += String.fromCharCode(code);
    }
    lines.push(line);
  }
  return lines;
}

// Color rows like btop: brighter toward the top of the graph.
function graphHTML(values, el, min, max, hotAt) {
  const cs = getComputedStyle(el);
  const charW = parseFloat(cs.fontSize) * 0.6;
  const cols = Math.max(8, Math.floor(el.clientWidth / charW));
  const rows = Math.max(2, Math.floor(el.clientHeight / parseFloat(cs.lineHeight)));
  const lines = braille(values, cols, rows, min, max);
  const last = values.at(-1) ?? 0;
  return lines.map((l, i) => {
    const level = 1 - i / rows;
    const hot = hotAt != null && last >= hotAt && i === 0;
    return `<span class="${hot ? "hot" : `lv${Math.round(level * 3)}`}">${l}</span>`;
  }).join("\n");
}

// ---------------------------------------------------------------- formatting

const tempClass = (t, warn, crit) => (t == null ? "" : t >= crit ? "crit" : t >= warn ? "warn" : "");
const gb = (v) => (v == null ? "–" : v.toFixed(1));

function rate(bps) {
  if (bps < 1024) return `${bps.toFixed(0)}B`;
  if (bps < 1024 ** 2) return `${(bps / 1024).toFixed(0)}K`;
  return `${(bps / 1024 ** 2).toFixed(1)}M`;
}

function uptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function bar(frac, width = 10) {
  const n = Math.round(Math.max(0, Math.min(1, frac)) * width);
  return `<span class="meter"><b>${"■".repeat(n)}</b>${"■".repeat(width - n)}</span>`;
}

function stat(k, v, extra = "") {
  return `<div class="stat-s"><span class="k">${k}</span><span class="v">${v}</span>${extra}</div>`;
}

function sparkline(values, width = 24) {
  const ticks = "▁▂▃▄▅▆▇█";
  const data = values.slice(-width);
  const max = Math.max(1, ...data.filter((v) => v != null));
  return data.map((v) => ticks[Math.min(7, Math.floor(((v ?? 0) / max) * 7.99))]).join("");
}

// ---------------------------------------------------------------- render

function render(s) {
  $("v-gpu-util").textContent = s.gpu_util ?? "–";
  const t = $("v-gpu-temp");
  t.textContent = s.gpu_temp ?? "–";
  t.className = tempClass(s.gpu_temp, 80, 88);
  $("g-gpu-util").innerHTML = graphHTML(hist.gpu_util, $("g-gpu-util"), 0, 100, 95);
  $("g-gpu-temp").innerHTML = graphHTML(hist.gpu_temp, $("g-gpu-temp"), 30, 95, 85);

  const fans = s.fans?.length ? s.fans.map((f) => `${f}%`).join(" · ") : `<span class="dim">n/a</span>`;
  $("v-small").innerHTML = [
    stat("vram", `${gb(s.vram_used)}<small>/${gb(s.vram_total)}G</small>`, bar((s.vram_used ?? 0) / (s.vram_total || 1))),
    stat("cpu", `${Math.round(s.cpu_util)}<small>%</small> <span class="${tempClass(s.cpu_temp, 88, 96)}">${Math.round(s.cpu_temp ?? 0)}<small>°C</small></span>`,
      `<span class="mini">${sparkline(hist.cpu_util, 14)}</span>`),
    stat("ram", `${gb(s.ram_used)}<small>/${gb(s.ram_total)}G</small>`, bar(s.ram_used / s.ram_total)),
    stat("fans", fans),
    stat("power", s.gpu_power != null ? `${s.gpu_power}<small>W</small> <small>${s.gpu_clock}MHz P${s.gpu_pstate}</small>` : "–"),
    stat("nvme", `${Math.round(s.nvme_temp ?? 0)}<small>°C</small> <small>disk ${Math.round(s.disk_pct)}%</small>`),
    stat("net", `<small>↓</small>${rate(s.net_rx)} <small>↑</small>${rate(s.net_tx)}`),
    stat("batt", `${s.battery}<small>%</small> <small>${s.ac ? "AC" : "BATTERY"} · up ${uptime(s.uptime)}</small>`),
  ].join("");

  $("v-clef").innerHTML = `
    <span class="status-dot ${s.clef_up ? "up" : ""}"></span><span class="k">clef</span>
    <span><b>${s.dpm}</b> <small>dec/min</small></span>
    <span><b>${s.avg_ms ?? "–"}</b> <small>ms</small></span>
    <span><b>${s.queue}</b> <small>queued</small></span>
    <span class="mini grow">${sparkline(hist.dpm, 30)}</span>`;

  renderHealth();
}

function renderHealth() {
  const el = $("v-health");
  if (alert) {
    el.className = "health alert";
    el.innerHTML = `<b>⚠ ${esc(alert.subsystem.replace("_", " ").toUpperCase())}</b> ${esc(alert.message)}
      <span class="dim">· ${alert.by === "limit" ? "hard limit" : "flagged by clef"}</span>`;
  } else {
    const ago = lastCheck ? `${Math.round(Date.now() / 1000 - lastCheck.ts)}s ago · p(wrong) ${lastCheck.p_wrong.toFixed(2)}` : "warming up";
    el.className = "health";
    el.innerHTML = `<span class="ok">✓ nominal</span> <span class="dim">· clef health check ${ago}</span>`;
  }
}

function push(s) {
  for (const k of Object.keys(hist)) {
    hist[k].push(s[k] ?? null);
    if (hist[k].length > HIST) hist[k].shift();
  }
}

export async function init() {
  const snap = await fetch("/api/vitals").then((r) => r.json());
  for (const k of Object.keys(hist)) hist[k] = snap.history[k] ?? [];
  alert = snap.alert;
  lastCheck = snap.last_check;
  if (snap.latest) render(snap.latest);
}

export function onEvent(ev) {
  if (ev.t === "vitals") {
    push(ev);
    render(ev);
  } else if (ev.t === "alert") {
    alert = ev.alert;
    renderHealth();
  } else if (ev.t === "hello") {
    alert = ev.alert;
  } else if (ev.t === "decision" && ev.source === "system") {
    lastCheck = { ts: ev.ts, p_wrong: ev.flag };
    renderHealth();
  }
}

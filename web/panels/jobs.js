// Top right: internship matches. "latest" is every match, newest first (anything found in the
// last 30 min is highlighted); "best today" is today's matches sorted by fit + company tier.
// Ticking the box marks a match applied, which removes it here and on /jobs.
import { ago, debounce, esc, etDateTime, meter, setApplied } from "../util.js";

const body = document.getElementById("jobs-body");
const meta = document.getElementById("jobs-meta");
const tabs = document.getElementById("jobs-tabs");
const NEW_S = 30 * 60;

let view = "latest";
try { view = localStorage.getItem("clef.jobs.view") || "latest"; } catch {}

function chip(j) {
  if (j.tier === "top") return `<span class="tier top">${j.pushed ? "★ " : ""}${esc(j.tag || "top")}</span>`;
  if (j.tier === "startup") return `<span class="tier">${esc(j.tag)}</span>`;
  return "";
}

function row(j, i) {
  const isNew = view === "latest" && Date.now() / 1000 - j.found < NEW_S;
  return `<li class="job-li"><button class="apply" data-id="${esc(j.id)}" title="mark applied (removes it)"></button><a class="row job-row${isNew ? " new" : ""}${j.tier === "top" ? " top" : ""}" href="${esc(j.url)}" target="_blank" rel="noopener">
    <div class="job-top">
      ${view === "best" ? `<span class="pos">${i + 1}</span>` : ""}
      <span class="company">${esc(j.company)}</span>
      <span class="role-title">${esc(j.title)}</span>
      <span class="grow"></span>
      <span class="age" title="found ${etDateTime(j.found)}">${ago(j.found)}</span>
    </div>
    <div class="job-sub">${chip(j)}${j.term ? `<span class="term">${esc(j.term)}</span>` : ""}
      <span class="loc">${esc(j.location || "location not listed")}</span>
      <span class="grow"></span>${j.fit != null ? `<span title="fit ${j.fit.toFixed(1)} / 4">${meter(j.fit)}</span>` : ""}
    </div></a></li>`;
}

function footer(st, pushed) {
  const n = (st.boards.hot || 0) + (st.boards.cold || 0);
  const sweep = st.last_sweep.hot ? `hot sweep ${ago(st.last_sweep.hot)} ago` : "first sweep running";
  return `${n.toLocaleString()} boards · ${sweep} · ${st.checked_24h} checked today · ` +
    (st.ntfy ? `${pushed} pushed today (★)` : "phone push OFF");
}

let last = null;
let undo = null;   // { job, timer } after ticking a box

function markApplied(id) {
  const job = last?.matches.find((m) => m.id === id);
  if (!job) return;
  job.applied = Date.now() / 1000;
  clearTimeout(undo?.timer);
  undo = { job, timer: setTimeout(() => { undo = null; render(); }, 8000) };
  render();
  setApplied(id, true);
}

function render() {
  if (!last) return;
  const { matches, status } = last;
  const todayAll = matches.filter((m) => m.found >= status.today_start);
  const applied = todayAll.filter((m) => m.applied).length;
  const pushed = todayAll.filter((m) => m.pushed).length;
  const today = todayAll.filter((m) => !m.applied);
  meta.textContent = `${today.length} to apply${applied ? ` · ${applied} applied` : ""} ·`;
  tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  const list = view === "best" ? [...today].sort((a, b) => b.rank - a.rank) : matches.filter((m) => !m.applied);
  const empty = view === "best"
    ? `<div class="empty">nothing left to apply to today</div>`
    : `<div class="empty">nothing left to apply to<br><span class="dim">top matches push to your phone the moment clef approves them</span></div>`;
  body.innerHTML = `<div class="jobs-wrap">${list.length ? `<ul class="list">${list.map(row).join("")}</ul>` : empty}` +
    `<div class="jobs-foot">${undo
      ? `<span class="undo-msg">✓ applied: ${esc(undo.job.company)}</span> · <button class="undo">undo</button>`
      : footer(status, pushed)}</div></div>`;
}

async function refresh() {
  last = await fetch("/api/jobs?hours=48").then((r) => r.json());
  render();
}

const refreshSoon = debounce(refresh, 400);

export function init() {
  body.addEventListener("click", (e) => {
    const box = e.target.closest("button.apply");
    if (box) return markApplied(box.dataset.id);
    if (e.target.closest("button.undo") && undo) {
      clearTimeout(undo.timer);
      undo.job.applied = null;
      setApplied(undo.job.id, false);
      undo = null;
      render();
    }
  });
  tabs.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    view = b.dataset.view;
    try { localStorage.setItem("clef.jobs.view", view); } catch {}
    render();
  });
  refresh();
  setInterval(refresh, 30_000);
}

export function onEvent(ev) {
  if (ev.t === "jobs_changed" || ev.t === "jobs_status" || (ev.t === "decision" && ev.source === "jobs" && ev.show)) refreshSoon();
}

export const onConnect = refresh;

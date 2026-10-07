// Top right: fresh internship matches. Newest first; anything found in the last 30 min is highlighted.
import { ago, debounce, esc, etDateTime } from "../util.js";

const body = document.getElementById("jobs-body");
const meta = document.getElementById("jobs-meta");
const NEW_S = 30 * 60;

function row(j) {
  const isNew = Date.now() / 1000 - j.found < NEW_S;
  return `<li><a class="row job-row${isNew ? " new" : ""}" href="${esc(j.url)}" target="_blank" rel="noopener">
    <div class="job-top">
      <span class="company">${esc(j.company)}</span>
      <span class="role-title">${esc(j.title)}</span>
      <span class="grow"></span>
      <span class="age" title="found ${etDateTime(j.found)}">${ago(j.found)}</span>
    </div>
    <div class="job-sub">${j.term ? `<span class="term">${esc(j.term)}</span>` : ""}
      <span class="loc">${esc(j.location || "location not listed")}</span>
      <span class="grow"></span><span>${esc(j.via)}</span>
    </div></a></li>`;
}

function footer(st) {
  const n = (st.boards.hot || 0) + (st.boards.cold || 0);
  const sweep = st.last_sweep.hot ? `hot sweep ${ago(st.last_sweep.hot)} ago` : "first sweep running";
  return `${n.toLocaleString()} boards (${(st.boards.hot || 0).toLocaleString()} hot) · ${sweep} · ` +
    `${st.checked_24h} checked today · ${st.ntfy ? "phone push on" : "phone push OFF"}`;
}

async function refresh() {
  const { matches, status } = await fetch("/api/jobs?hours=48").then((r) => r.json());
  const today = matches.filter((m) => Date.now() / 1000 - m.found < 86400).length;
  meta.textContent = `${today} today ·`;
  const list = matches.length
    ? `<ul class="list">${matches.map(row).join("")}</ul>`
    : `<div class="empty">no fresh matches yet<br><span class="dim">new postings push to your phone the moment clef approves them</span></div>`;
  body.innerHTML = `<div class="jobs-wrap">${list}<div class="jobs-foot">${footer(status)}</div></div>`;
}

const refreshSoon = debounce(refresh, 400);

export function init() {
  refresh();
  setInterval(refresh, 30_000);
}

export function onEvent(ev) {
  if (ev.t === "jobs_changed" || ev.t === "jobs_status" || (ev.t === "decision" && ev.source === "jobs" && ev.show)) refreshSoon();
}

export const onConnect = refresh;

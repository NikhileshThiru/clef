// Top left: Gmail triage. Not a mail client: what to read today, then what's worth a glance.
import { ago, debounce, esc, etDateTime } from "../util.js";

const body = document.getElementById("mail-body");
const meta = document.getElementById("mail-meta");
let seen = new Set();
let first = true;

const GROUPS = [
  ["read today", (i) => i.unread && i.label === "read_today"],
  ["fyi", (i) => i.unread && i.label === "fyi"],
  ["read", (i) => !i.unread],
];

function row(it) {
  const ts = it.published;
  const icons = [
    it.needs_reply >= 0.6 ? `<span class="ico" title="expects a reply">↩</span>` : "",
    it.deadline >= 0.6 ? `<span class="ico warn" title="deadline soon">⏰</span>` : "",
  ].join("");
  const cls = ["row", "mail-row", it.label === "read_today" && it.unread ? "hot" : "", it.unread ? "" : "done",
    !first && !seen.has(it.id) ? "fresh" : ""].join(" ");
  return `<li><a class="${cls}" href="${esc(it.url)}" target="_blank" rel="noopener">
    <div class="mail-top">
      <span class="sender">${esc(it.origin)}</span>
      <span class="subject">${esc(it.title)}</span>
      <span class="grow"></span>${icons}
      <span class="age" title="${etDateTime(ts)}">${ago(ts)}</span>
    </div>
    <div class="snippet">${esc(it.summary)}</div></a></li>`;
}

function emptyState(data) {
  if (data.status === "not_connected") {
    return `<div class="empty">gmail not connected<br><br><span class="dim">run on the laptop:</span><br>
      <code>uv run python -m clefd.gmail_auth</code></div>`;
  }
  if (data.status === "auth_error") {
    return `<div class="empty">gmail login expired or revoked<br><br><code>uv run python -m clefd.gmail_auth</code></div>`;
  }
  if (data.pending) return `<div class="empty">clef is triaging ${data.pending} emails…</div>`;
  return `<div class="empty">inbox zero for today ✓</div>`;
}

async function refresh() {
  const data = await fetch("/api/mail").then((r) => r.json());
  const items = data.items;
  const toRead = items.filter(GROUPS[0][1]).length;
  const fyi = items.filter(GROUPS[1][1]).length;
  meta.innerHTML = data.status === "ok"
    ? `${toRead} to read · ${fyi} fyi${data.pending ? ` · triaging ${data.pending}` : ""}`
    : data.status === "error" ? `<span class="warn">gmail error, retrying</span>` : "";
  if (!items.length) {
    body.innerHTML = emptyState(data);
    return;
  }
  let html = "";
  for (const [name, test] of GROUPS) {
    const group = items.filter(test);
    if (!group.length) continue;
    html += `<li class="group">${name} <span>${group.length}</span></li>` + group.map(row).join("");
  }
  body.innerHTML = `<ul class="list">${html}</ul>`;
  seen = new Set(items.map((i) => i.id));
  first = false;
}

const refreshSoon = debounce(refresh, 400);

export function init() {
  refresh();
  setInterval(refresh, 60_000);
}

export function onEvent(ev) {
  if (ev.t === "mail_changed" || (ev.t === "decision" && ev.source === "mail")) refreshSoon();
}

export const onConnect = refresh;

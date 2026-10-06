// Bottom left: Clef-filtered market / AI / startup news.
import { ago, debounce, esc, etDateTime, meter } from "../util.js";

const TAGS = { ai: "AI", stocks: "MKT", startups: "STARTUP" };
const body = document.getElementById("news-body");
const meta = document.getElementById("news-meta");
let seen = new Set();
let first = true;

async function refresh() {
  const items = await fetch("/api/news").then((r) => r.json());
  if (!items.length) {
    body.innerHTML = `<div class="empty">waiting for news…</div>`;
    return;
  }
  const hot = items.filter((i) => i.hot).length;
  meta.textContent = `${items.length} stories${hot ? ` · ${hot} hot` : ""}`;
  body.innerHTML = `<ul class="list">${items.map(row).join("")}</ul>`;
  seen = new Set(items.map((i) => i.id));
  first = false;
}

function row(it) {
  const ts = it.published || it.ingested;
  const outlets = it.dup_count > 1 ? `<span title="${esc(it.dup_origins.join(", "))}">+${it.dup_count - 1}</span>` : "";
  const fresh = !first && !seen.has(it.id) ? " fresh" : "";
  return `<li><a class="row${it.hot ? " hot" : ""}${fresh}" href="${esc(it.url)}" target="_blank" rel="noopener">
    <div class="headline">${it.hot ? `<span class="badge">HOT</span>` : ""}${esc(it.title)}</div>
    <div class="sub">
      <span class="tag ${it.label}">${TAGS[it.label] ?? it.label}</span>
      <span class="origin">${esc(it.origin)}</span>${outlets}
      <span class="grow"></span>
      ${meter(it.score)}
      <span title="${etDateTime(ts)}">${ago(ts)}</span>
    </div></a></li>`;
}

const refreshSoon = debounce(refresh, 400);

export function init() {
  refresh();
  setInterval(refresh, 60_000); // keeps the "5m ago" labels honest
}

export function onEvent(ev) {
  if (ev.t === "news_changed" || (ev.t === "decision" && ev.source === "news" && ev.show)) refreshSoon();
}

export const onConnect = refresh;

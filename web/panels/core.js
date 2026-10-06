// Bottom right: placeholder ticker of real events. The particle core replaces this in step 4.
import { esc } from "../util.js";

const body = document.getElementById("core-body");
const meta = document.getElementById("core-meta");
const ticker = document.createElement("div");
ticker.className = "ticker";
body.append(ticker);

function push(html, color) {
  const line = document.createElement("div");
  line.innerHTML = html;
  line.style.color = color;
  ticker.prepend(line);
  while (ticker.children.length > 40) ticker.lastChild.remove();
}

export function onEvent(ev) {
  if (ev.t === "decision") {
    meta.textContent = ev.path;
    push(`${esc(ev.path)}  <span style="color:var(--fg-dim)">${ev.latency_ms}ms · ${esc(ev.title.slice(0, 70))}</span>`,
      ev.show ? `var(--src-${ev.source})` : "var(--muted)");
  }
}

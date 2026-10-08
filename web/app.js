// Wires the live event socket to the three panels.
import * as news from "./panels/news.js";
import * as jobs from "./panels/jobs.js";
import * as core from "./panels/core.js";
import { etTime } from "./util.js";

const panels = [news, jobs, core];

// In the kiosk (opened by the `clef` command), send link clicks to the desktop's default
// browser. Viewed from anywhere else (e.g. the Mac over SSH), links open normally.
if (new URLSearchParams(location.search).has("kiosk")) {
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || !/^https?:/.test(a.href)) return;
    e.preventDefault();
    fetch("/api/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: a.href }) });
  });
}
panels.forEach((p) => p.init?.());

let build = null;

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    // The backend restarted with (possibly) new code: reload so the kiosk never runs stale JS.
    if (ev.t === "hello") {
      if (build && ev.build !== build) return location.reload();
      build = ev.build;
    }
    for (const p of panels) p.onEvent?.(ev);
  };
  ws.onopen = () => panels.forEach((p) => p.onConnect?.());
  // The backend restarts on deploys and crashes; keep retrying quietly.
  ws.onclose = () => {
    panels.forEach((p) => p.onDisconnect?.());
    setTimeout(connect, 2000);
  };
}
connect();

const clock = document.getElementById("clock");
const tick = () => (clock.textContent = etTime(Date.now() / 1000, true));
tick();
setInterval(tick, 1000);

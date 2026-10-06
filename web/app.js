// Wires the live event socket to the four panels.
import * as news from "./panels/news.js";
import * as vitals from "./panels/vitals.js";
import * as core from "./panels/core.js";
import * as mail from "./panels/mail.js";
import { etTime } from "./util.js";

const panels = [news, vitals, core, mail];
panels.forEach((p) => p.init?.());

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
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

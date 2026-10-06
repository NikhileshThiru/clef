// Top right: system vitals + Clef stats. GPU/CPU sensors arrive in step 5.
const body = document.getElementById("vitals-body");

function render(s) {
  body.innerHTML = `
    <div style="margin-bottom:10px"><span class="status-dot ${s.clef_up ? "up" : ""}"></span>clef-flash ${s.clef_up ? "online" : "offline"}</div>
    <div class="stat-grid">
      <div class="stat"><div class="k">decisions/min</div><div class="v">${s.dpm}</div></div>
      <div class="stat"><div class="k">avg latency</div><div class="v">${s.avg_ms ?? "–"}<small> ms</small></div></div>
      <div class="stat"><div class="k">queue</div><div class="v">${s.queue}</div></div>
    </div>`;
}

export function onEvent(ev) {
  if (ev.t === "stats" || ev.t === "hello") render(ev);
}

// All displayed times are US Eastern, whatever the machine's timezone.
const TZ = "America/New_York";

export function etTime(ts, withSeconds = false) {
  return new Date(ts * 1000).toLocaleTimeString("en-US", {
    timeZone: TZ, hour: "numeric", minute: "2-digit",
    ...(withSeconds && { second: "2-digit" }), timeZoneName: "short",
  });
}

export function etDateTime(ts) {
  return new Date(ts * 1000).toLocaleString("en-US", {
    timeZone: TZ, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
}

export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Score on a 0..max scale as a row of block glyphs.
export function meter(value, max = 4, width = 5) {
  const filled = Math.round((value / max) * width);
  return `<span class="meter"><b>${"▰".repeat(filled)}</b>${"▱".repeat(width - filled)}</span>`;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Mark a job match applied (it leaves the lists everywhere) or undo that.
export function setApplied(id, applied) {
  return fetch("/api/jobs/applied", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, applied }),
  });
}

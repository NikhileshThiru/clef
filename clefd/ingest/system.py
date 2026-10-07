"""System vitals: sampled every second for the panel, judged by Clef every minute.

The health check gives Clef a few minutes of history plus what "normal" means
on this box (the GPU is always busy, VRAM is always nearly full) and only
raises an alert after two consecutive "something is actually wrong" verdicts.
A handful of hard limits alert regardless, as a safety net.
"""
import asyncio
import json
import os
import shutil
import statistics
import time
from collections import deque
from pathlib import Path

from .. import bus, db
from ..decider import Spec, decider, register

try:
    import pynvml
    pynvml.nvmlInit()
    _gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
    _power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(_gpu) / 1000
except Exception as e:  # no NVIDIA driver: keep the rest of the panel working
    print(f"[system] NVML unavailable: {e!r}", flush=True)
    _gpu = None
    _power_limit = 0

HISTORY = deque(maxlen=600)  # 10 min of 1 s samples
FANS_FILE = Path("/run/clef/fans.json")
CHECK_SECONDS = 60


def _hwmon(name: str) -> Path | None:
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        if (h / "name").read_text().strip() == name:
            return h
    return None


_coretemp = _hwmon("coretemp")
_nvme = _hwmon("nvme")
_bat = Path("/sys/class/power_supply/BAT0")
_ac = Path("/sys/class/power_supply/AC/online")


def _read(path: Path, default=None):
    try:
        return path.read_text().strip()
    except OSError:
        return default


class Counters:
    """Turns cumulative /proc counters into per-second rates."""

    def __init__(self):
        self.cpu = None
        self.net = None
        self.t = None

    def cpu_percent(self) -> float:
        fields = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        idle, total = fields[3] + fields[4], sum(fields[:8])
        prev, self.cpu = self.cpu, (idle, total)
        if not prev or total == prev[1]:
            return 0.0
        return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def net_rates(self) -> tuple[float, float]:
        rx = tx = 0
        for line in open("/proc/net/dev").readlines()[2:]:
            iface, data = line.split(":", 1)
            if iface.strip() == "lo" or iface.strip().startswith(("docker", "veth", "br-")):
                continue
            cols = data.split()
            rx += int(cols[0])
            tx += int(cols[8])
        now = time.monotonic()
        prev, prev_t = self.net, self.t
        self.net, self.t = (rx, tx), now
        if not prev:
            return 0.0, 0.0
        dt = now - prev_t
        return (rx - prev[0]) / dt, (tx - prev[1]) / dt


_counters = Counters()


def sample() -> dict:
    s: dict = {"ts": time.time()}
    if _gpu:
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
            mem = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
            power = pynvml.nvmlDeviceGetPowerUsage(_gpu) / 1000
            s |= {
                "gpu_util": util.gpu,
                "gpu_temp": pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU),
                "vram_used": mem.used / 2**30,
                "vram_total": mem.total / 2**30,
                # The laptop driver occasionally reports nonsense (752 W on an 80 W part).
                "gpu_power": round(power, 1) if 0 < power < _power_limit * 1.5 else None,
                "gpu_clock": pynvml.nvmlDeviceGetClockInfo(_gpu, pynvml.NVML_CLOCK_SM),
                "gpu_pstate": pynvml.nvmlDeviceGetPerformanceState(_gpu),
            }
        except pynvml.NVMLError as e:
            s["gpu_error"] = str(e)

    s["cpu_util"] = _counters.cpu_percent()
    if _coretemp:
        s["cpu_temp"] = int(_read(_coretemp / "temp1_input", "0")) / 1000
    if _nvme:
        s["nvme_temp"] = int(_read(_nvme / "temp1_input", "0")) / 1000

    mem = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        mem[k] = int(v.split()[0]) * 1024
    s["ram_used"] = (mem["MemTotal"] - mem["MemAvailable"]) / 2**30
    s["ram_total"] = mem["MemTotal"] / 2**30

    disk = shutil.disk_usage("/")
    s["disk_pct"] = round(100 * disk.used / disk.total, 1)
    s["net_rx"], s["net_tx"] = _counters.net_rates()
    s["load"] = os.getloadavg()[0]
    s["uptime"] = float(open("/proc/uptime").read().split()[0])

    s["battery"] = int(_read(_bat / "capacity", "0"))
    s["battery_status"] = _read(_bat / "status", "Unknown")
    s["ac"] = _read(_ac) == "1"

    try:
        fans = json.loads(FANS_FILE.read_text())
        if time.time() - fans["ts"] < 10:
            s["fans"] = [f["percent"] for f in fans["fans"]]
    except (OSError, ValueError, KeyError):
        pass

    stats = decider.stats()
    s |= {"clef_up": stats["clef_up"], "dpm": stats["dpm"], "avg_ms": stats["avg_ms"], "queue": stats["queue"]}
    return s


# ---------------------------------------------------------------- health check

class Health:
    def __init__(self):
        self.alert: dict | None = None
        self.strikes = 0
        self.clear_streak = 0
        self.last_check: dict | None = None
        self.llama_down_since: float | None = None


health = Health()


def _window(key: str, seconds: int) -> list[float]:
    cutoff = time.time() - seconds
    return [s[key] for s in HISTORY if s["ts"] >= cutoff and s.get(key) is not None]


def _agg(key: str, seconds: int = 300) -> dict:
    vals = _window(key, seconds)
    if not vals:
        return {}
    return {"now": round(vals[-1], 1), "avg": round(statistics.fmean(vals), 1), "max": round(max(vals), 1)}


def _trend(key: str) -> float | None:
    old, new = _window(key, 600)[:60], _window(key, 60)
    if not old or not new:
        return None
    return round(statistics.fmean(new) - statistics.fmean(old), 1)


def hard_limits(now: dict) -> tuple[str, str] | None:
    """Deterministic safety net. Returns (subsystem, message) if a limit is crossed."""
    gpu = _window("gpu_temp", 60)
    if len(gpu) >= 50 and min(gpu) >= 92:
        return "gpu", f"GPU at {gpu[-1]:.0f}°C for over a minute"
    cpu = _window("cpu_temp", 60)
    if len(cpu) >= 50 and min(cpu) >= 98:
        return "cpu", f"CPU at {cpu[-1]:.0f}°C for over a minute"
    if now.get("disk_pct", 0) >= 95:
        return "disk", f"Disk {now['disk_pct']:.0f}% full"
    ram = _window("ram_used", 120)
    if ram and now.get("ram_total") and min(ram) / now["ram_total"] >= 0.95:
        return "memory", f"RAM {ram[-1]:.1f}/{now['ram_total']:.1f} GB for 2 minutes"
    if health.llama_down_since and time.time() - health.llama_down_since > 120:
        return "clef_service", f"llama-server down for {(time.time() - health.llama_down_since) / 60:.0f} min"
    return None


def describe(subsystem: str, now: dict) -> str:
    """A human line for an alert. Clef picks the subsystem; the numbers come from the sensors."""
    g, c = _agg("gpu_temp"), _agg("cpu_temp")
    match subsystem:
        case "gpu":
            return f"GPU {g.get('now', '?')}°C (5 min avg {g.get('avg', '?')}°C), load {now.get('gpu_util', '?')}%"
        case "cpu":
            return f"CPU {c.get('now', '?')}°C (5 min avg {c.get('avg', '?')}°C), load {now.get('cpu_util', '?')}%"
        case "memory":
            return f"RAM {now['ram_used']:.1f}/{now['ram_total']:.1f} GB, trend {(_trend('ram_used') or 0):+.1f} GB/10 min"
        case "disk":
            return f"Disk {now['disk_pct']:.0f}% full"
        case "cooling":
            fans = now.get("fans")
            return f"Fans {fans}% with GPU {g.get('now', '?')}°C / CPU {c.get('now', '?')}°C" if fans else "Cooling looks off"
        case "clef_service":
            return f"Clef queue {now.get('queue')} · {now.get('dpm')} decisions/min · {'up' if now.get('clef_up') else 'DOWN'}"
        case "battery":
            return f"Battery {now.get('battery')}% {now.get('battery_status', '').lower()}, AC {'on' if now.get('ac') else 'OFF'}"
    return subsystem


SUBSYSTEMS = {
    "none": "nothing is wrong",
    "gpu": "GPU overheating or GPU errors",
    "cpu": "CPU overheating or runaway CPU load",
    "memory": "RAM running out or leaking",
    "disk": "disk filling up",
    "cooling": "fans not keeping up with temperatures",
    "clef_service": "the decision model or its queue is down or stuck",
    "battery": "unplugged from power, or battery problem",
}

QUESTIONS = {
    "wrong": {"type": "noul",
              "instructions": "Based on these observations, is something wrong with the machine that the owner "
                              "should be alerted about now?"},
    "subsystem": {"type": "choice", "instructions": "Which part of the system has a problem, if any?",
                  "criteria": SUBSYSTEMS},
    "severity": {"type": "score", "instructions": "How serious is the machine's current condition?",
                 "criteria": ["fine", "worth a look", "needs attention soon", "urgent"]},
}

# Clef judges plain-language observations far better than raw nested numbers (tested:
# raw JSON missed a sustained 91°C GPU, a RAM leak and a stuck queue). So the code does
# the arithmetic and tags what is outside normal; Clef weighs it in context.
CONTEXT = ("Always-on laptop server running a local AI model on its GPU. It should always be plugged in. "
           "Heavy GPU load and nearly full VRAM are expected.")

GPU_TEMP_NORMAL = 85
CPU_TEMP_NORMAL = 90


def state(item: dict) -> dict:
    return item["meta"]


def interpret(item: dict, answers: dict) -> dict:
    p_wrong = answers["wrong"]["noul"]
    sub = answers["subsystem"]["choice"]
    sev = answers["severity"]["score"]
    sub_conf = answers["subsystem"]["confidence"]
    flagged = sub != "none" and sub_conf >= 0.6 and (p_wrong >= 0.5 or sev >= 1.2)
    health.last_check = {"ts": time.time(), "p_wrong": p_wrong, "subsystem": sub, "severity": sev}
    _update_alert(HISTORY[-1] if HISTORY else sample(), ("clef", sub) if flagged else None)
    label = sub if flagged else "nominal"
    p_label = p_wrong if flagged else 1 - p_wrong
    return {"label": label, "confidence": p_wrong, "score": sev, "flag": p_wrong,
            "path": f"SYSTEM > {label.upper()} > {p_label:.2f}", "show": flagged}


register("system", Spec(QUESTIONS, state, interpret))


def _update_alert(now: dict, verdict: tuple[str, str] | None) -> None:
    """Hysteresis: two strikes to raise, three clean checks to clear. Hard limits raise at once."""
    hard = hard_limits(now)
    if hard:
        sub, msg = hard
        _raise(sub, msg, "limit")
        return
    if verdict:
        health.strikes += 1
        health.clear_streak = 0
        if health.strikes >= 2:
            _raise(verdict[1], describe(verdict[1], now), "clef")
    else:
        health.strikes = 0
        health.clear_streak += 1
        if health.alert and health.clear_streak >= 3:
            health.alert = None
            bus.publish({"t": "alert", "alert": None})


def _raise(subsystem: str, message: str, by: str) -> None:
    new = not health.alert or health.alert["subsystem"] != subsystem
    since = time.time() if new else health.alert["since"]
    health.alert = {"subsystem": subsystem, "message": message, "by": by, "since": since}
    bus.publish({"t": "alert", "alert": health.alert})
    if new:
        print(f"[system] ALERT {subsystem}: {message} ({by})", flush=True)
        log = db.kv_get("alerts", [])[-49:]
        db.kv_set("alerts", log + [health.alert])


def _minutes_above(key: str, threshold: float, seconds: int = 600) -> float:
    return sum(1 for v in _window(key, seconds) if v > threshold) / 60


def observations(now: dict) -> list[str]:
    obs = []
    g = _agg("gpu_temp")
    if g:
        line = f"GPU temperature: {g['now']:.0f}°C now, {g['avg']:.0f}°C average over 5 min (normal: under {GPU_TEMP_NORMAL}°C)"
        tr = _trend("gpu_temp")
        if tr and abs(tr) >= 5:
            line += f", {'up' if tr > 0 else 'down'} {abs(tr):.0f}°C in 10 min"
        mins = _minutes_above("gpu_temp", GPU_TEMP_NORMAL)
        obs.append(line + (f". ABOVE NORMAL for {mins:.0f} min." if g["avg"] > GPU_TEMP_NORMAL else "."))
    c = _agg("cpu_temp")
    if c:
        line = (f"CPU temperature: {c['now']:.0f}°C now, {c['avg']:.0f}°C average over 5 min, peak {c['max']:.0f}°C "
                f"(normal: under {CPU_TEMP_NORMAL}°C, brief spikes OK)")
        mins = _minutes_above("cpu_temp", CPU_TEMP_NORMAL)
        obs.append(line + (f". ABOVE NORMAL for {mins:.0f} min." if c["avg"] > CPU_TEMP_NORMAL else "."))
    u = _agg("gpu_util")
    if u:
        obs.append(f"GPU load: {u['now']:.0f}% now, {u['avg']:.0f}% average over 5 min (expected: anywhere 0-100%).")

    pct = 100 * now["ram_used"] / now["ram_total"]
    ram_tr = _trend("ram_used") or 0
    line = f"RAM: {now['ram_used']:.1f} of {now['ram_total']:.1f} GB used ({pct:.0f}%)"
    line += f", {'up' if ram_tr > 0 else 'down'} {abs(ram_tr):.1f} GB in 10 min" if abs(ram_tr) >= 0.5 else ", steady over 10 min"
    if pct > 90 or (ram_tr > 1.5 and pct > 75):
        line += ". ABOVE NORMAL and rising" if ram_tr > 0.5 else ". ABOVE NORMAL"
    obs.append(line + ".")

    obs.append(f"Disk: {now['disk_pct']:.0f}% full{'. ABOVE NORMAL' if now['disk_pct'] > 90 else ''}.")

    fans = now.get("fans")
    if fans:
        hottest = max(now.get("gpu_temp") or 0, now.get("cpu_temp") or 0)
        line = "Fans: " + " and ".join(f"{f}%" for f in fans)
        if max(fans) == 0 and hottest > 75:
            line += f" while the hottest chip is {hottest:.0f}°C. NOT SPINNING"
        obs.append(line + ".")

    q = _agg("queue")
    dpm_5 = len([1 for s in HISTORY if s["ts"] >= time.time() - 300 and s.get("dpm")])
    q_tr = _trend("queue") or 0
    line = f"Decision queue: {now['queue']} waiting, {now['dpm']} decisions per minute"
    if q_tr > 20:
        line += f", grew by {q_tr:.0f} in 10 min"
    elif q_tr < -20:
        line += ", draining"
    else:
        line += ", steady"
    if now["queue"] > 50 and dpm_5 == 0:
        line += ". STUCK"
    obs.append(line + ".")

    if now.get("ac"):
        obs.append(f"Power: plugged in, battery {now['battery']}%.")
    else:
        obs.append(f"Power: UNPLUGGED, running on battery, {now['battery']}% and {now['battery_status'].lower()}.")
    return obs


def check_state(now: dict) -> dict:
    return {"machine": CONTEXT, "observations": observations(now)}


# ---------------------------------------------------------------- loops

async def sample_loop() -> None:
    while True:
        s = sample()
        HISTORY.append(s)
        if s.get("clef_up"):
            health.llama_down_since = None
        elif health.llama_down_since is None:
            health.llama_down_since = time.time()
        bus.publish({"t": "vitals", **s})
        await asyncio.sleep(1)


async def check_loop() -> None:
    await asyncio.sleep(90)  # let a few minutes of history build before judging
    while True:
        now = HISTORY[-1] if HISTORY else sample()
        if not now.get("clef_up"):
            # Clef can't judge its own outage; the hard limits cover it.
            _update_alert(now, None)
        else:
            item_id = f"system:{int(time.time())}"
            db.insert_item({"id": item_id, "source": "system", "title": "health check", "origin": "clefd",
                            "published": time.time(), "meta": check_state(now)})
            decider.enqueue(item_id, "system", time.time())
            bus.publish({"t": "ingest", "source": "system", "id": item_id})
        db.prune("system", time.time() - 86400)
        await asyncio.sleep(CHECK_SECONDS)


def snapshot() -> dict:
    keys = ("gpu_util", "gpu_temp", "cpu_util", "cpu_temp", "dpm")
    hist = list(HISTORY)[-240:]
    return {
        "latest": HISTORY[-1] if HISTORY else None,
        "history": {k: [s.get(k) for s in hist] for k in keys},
        "alert": health.alert,
        "last_check": health.last_check,
    }

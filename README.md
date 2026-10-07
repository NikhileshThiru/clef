# Clef

A homelab decision dashboard. Ingest workers pull mail, news and system events into a queue; a local
[Clef-flash](https://huggingface.co/ggml-org/Clef-Flash-GGUF) decision model (llama.cpp `/v1/systemone`)
makes typed decisions about each item; results land in SQLite and stream live to a four-panel kiosk page.

```
ingest (HN, RSS, Google News, Finnhub, ...) ─▶ priority queue ─▶ Clef-flash on GPU ─▶ SQLite ─▶ WebSocket ─▶ UI
```

## Pieces

| Service | What | Port |
|---|---|---|
| `clef-llama` | llama-server with Clef-flash Q4_K_M | 127.0.0.1:8090 |
| `clefd` | Python backend: ingest, decision queue, web UI | 127.0.0.1:8077 |
| `clef-fans` | root helper: Clevo fan duty → `/run/clef/fans.json` (system service, read-only ioctls) | – |

`clef-llama` and `clefd` are systemd **user** services: start at boot (linger is on), restart on crash.

```bash
systemctl --user status clefd clef-llama     # health
systemctl --user restart clefd               # after editing Python code
journalctl --user -u clefd -f                # live logs
```

## The `clef` command

```bash
clef            # open the dashboard fullscreen on workspace 10 (Super+0); starts the services if they're down
clef status     # services, model, queue, health, battery, gmail
clef restart    # restart clefd + clef-llama
clef logs       # follow backend logs
```

It opens `http://clef.localhost:8077/?kiosk=1` as a Chromium app with its own profile
(`~/.local/share/clef/chromium`). Chromium names that window `chrome-clef.localhost__-Default`, and
`hypr/clef.lua` (symlinked to `~/.config/hypr/clef.lua`, loaded from `hyprland.lua`) pins it to workspace 10,
fullscreen, no border, screen kept awake. In the kiosk, clicked links open in your default browser instead.
Works over SSH too: it attaches to the running Hyprland session.

From the Mac: `ssh -L 8077:localhost:8077 nikhilesh@omarchy`, then open http://localhost:8077.

## Configure news

Edit `config/sources.toml`. It reloads on save, no restart needed: add or remove RSS feeds and Google News
queries, change the reader profile Clef filters for, or tune `min_relevance`.
For Finnhub market news, add `FINNHUB_API_KEY=...` to `.env` (free key at finnhub.io) and restart `clefd`.

## Job watcher

Goal: a phone push within minutes of a relevant internship going live.

- **Sources:** ~2,350 company job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday) polled directly,
  mined daily from every board Simplify has ever listed an internship on. Boards with an internship in the last
  120 days are polled every 3 min, the rest every 15 min, with ETags so unchanged boards cost a 304.
  Plus SimplifyJobs' `listings.json` (refreshes ~every 30 min) as a catch-all.
- **Only new postings:** the first look at a board records what's there silently; after that a posting alerts
  only if it's new since the last check and fresh by its own timestamp (24 h for boards, 6 h for Simplify).
- **Clef decides** (intern-looking titles only): internship vs full-time/new grad, role, term, US, bachelor's
  eligibility. Accepted roles/terms and thresholds live in `config/jobs.toml`.
- **Where matches go:** ntfy push to the phone (tap opens the posting), the Jobs panel, and
  `http://omarchy:8077/jobs` from any device on the tailnet.
- `NTFY_TOPIC` in `.env` is the secret ntfy topic. Health alerts and the weekly Gmail re-login reminder use it too.

## Health alerts

`clefd` samples GPU (NVML), CPU, RAM, disk, network, battery and fans every second (no panel; use btop). Every 60 s Clef gets
plain-language observations (code does the math and tags anything outside normal ranges) and decides whether
something is actually wrong. An alert needs two "wrong" checks in a row and clears after three clean ones;
a few hard limits (GPU ≥92°C for a minute, disk ≥95%, on battery 3+ min, llama-server down >2 min) alert regardless. Alerts push to the phone and show in the core HUD.
Fan speed is duty % only: the Clevo interface on this chassis doesn't report RPM.
`sudo ./scripts/setup-fans.sh` installs the fan reader.

## Model setup (one time)

- `sudo ./scripts/setup-system.sh`: lid does nothing, never suspend, no battery charge limit, CUDA/build deps.
- llama.cpp v0.6.0 built with CUDA in `~/.local/src/llama.cpp`, model in `~/.local/share/clef/models/`.
- Config: all layers on GPU except the vocab head (`-ot "^output\.weight$=CPU"`), which the decision head never
  reads. ~5.5 GB VRAM, ~320 ms per ~330-token decision. A single request is capped at 2048 tokens (`-ub`).
- `scripts/bench_clef.py` re-measures latency and VRAM against the running server.

## Dev

```bash
uv sync
systemctl --user stop clefd && uv run python -m clefd    # run in the foreground
```

Data lives in `~/.local/share/clef/clef.db`. Secrets go in `.env` (git-ignored).

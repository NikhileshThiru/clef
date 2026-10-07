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

Open the dashboard at http://localhost:8077. From the Mac: `ssh -L 8077:localhost:8077 <laptop>` then open the same URL.

## Configure news

Edit `config/sources.toml`. It reloads on save, no restart needed: add or remove RSS feeds and Google News
queries, change the reader profile Clef filters for, or tune `min_relevance`.
For Finnhub market news, add `FINNHUB_API_KEY=...` to `.env` (free key at finnhub.io) and restart `clefd`.

## Vitals and health alerts

`clefd` samples GPU (NVML), CPU, RAM, disk, network, battery and fans every second. Every 60 s Clef gets
plain-language observations (code does the math and tags anything outside normal ranges) and decides whether
something is actually wrong. An alert needs two "wrong" checks in a row and clears after three clean ones;
a few hard limits (GPU ≥92°C for a minute, disk ≥95%, llama-server down >2 min) alert regardless.
Fan speed is duty % only: the Clevo interface on this chassis doesn't report RPM.
`sudo ./scripts/setup-fans.sh` installs the fan reader.

## Model setup (one time)

- `sudo ./scripts/setup-system.sh`: lid does nothing, never suspend, battery stops at 80%, CUDA/build deps.
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

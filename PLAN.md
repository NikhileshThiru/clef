# Clef: homelab decision dashboard

This is a loose plan, not a spec. It captures what I (Nikhilesh) talked through and decided. You have creative freedom on how to build it. If you find a better approach, a library that does it cleaner, or a cooler idea while building, propose it and go. Check in with me after each meaningful chunk so I can see it running.

## The machine

- Gigabyte G5 KE (RC55) gaming laptop: 12th gen i5, 16GB RAM, RTX 3060 laptop GPU (6GB VRAM)
- Running Omarchy (Arch + Hyprland). I'm still learning Linux, so explain anything I need to do by hand
- It sits open in my room 24/7, plugged in, never sleeps. I control it from my Mac over SSH/Tailscale
- Fan noise does not matter. Let them run
- Theme is cyan. Keyboard backlight is cyan. Keep everything visually consistent with that
- All displayed times in Eastern (EST/EDT)
- Everything must be free. No paid APIs

## Background: what Clef is

- **Jev** (TypeSafe AI, launched Sept 15 2026) is a "System One" decision model. It does not write text. You give it state plus typed questions (choice / score / bool) and it returns structured answers with calibrated probabilities. Closed, hosted only
- **Clef / Clef-flash** (Cloudflare, Oct 1 2026) are open source (Apache 2.0) decision models that are Jev-API compatible. Clef-flash is built on Qwen 3.5-9B, has vision, 64k context
- Full Clef is way too big for this GPU. **Clef-flash quantized is the target**
  - `ggml-org/Clef-Flash-GGUF` (official, has Q4_K_M) via llama.cpp
  - `Livesport/clef-flash-GGUF` (Q5_K_M ~6.66GB, keeps LM head in host RAM, tested on an 8GB card)
  - Recent llama.cpp reportedly has SystemOne support in llama-server. Verify against the actual llama.cpp docs/README, don't trust my notes
- Goal: get it fully on the GPU if possible. Partial offload to RAM is acceptable if needed. Measure VRAM and latency and tell me what you picked and why
- Quantization may hurt the calibrated probabilities. If easy, sanity check a handful of decisions against the hosted Clef on Workers AI (free tier) or just eyeball results with me
- Note: it's not magically cheaper compute than a normal 9B model. It's fast because it doesn't generate text. That's the whole point of using it as the brain here: lots of tiny, cheap, typed decisions

## System setup

- Lid close must do nothing (logind `HandleLidSwitch=ignore`, `HandleLidSwitchExternalPower=ignore`). Never suspend
- Battery sits at 100% forever. If this Gigabyte supports a charge limit on Linux, set it. If not, tell me
- Always-on pieces (Clef server, ingest workers, web server) run as systemd user services, auto start on boot, auto restart on crash
- I'll need to type my sudo password for some steps. Tell me when

## The `clef` command

- I already have a `jarvis` command that opens Spotify, a Kafka visualizer, and btop in a layout. Run `type jarvis` to find it and match its style
- `clef` opens the dashboard on its own workspace in the 4-box layout below. It does not start the backend (systemd does that), but it can check the services are healthy and start them if not
- Hyprland window rules to pin the layout

## Layout: four boxes

```
+-------------+-------------+
|   MAIL      |   VITALS    |
+-------------+-------------+
|   NEWS      |  CLEF CORE  |
+-------------+-------------+
```

Could be 4 windows or one fullscreen web app split into 4. Your call. One kiosk browser window with 4 panels is probably simplest and lets everything share one style. If the core needs its own window for performance, split it.

### Top left: Mail
- What I need to read today. Not a mail client, a triage list
- Source: Gmail API (OAuth, I'll set up the Google Cloud project with your guidance; walk me through it step by step)
- My Georgia Tech Outlook is forwarded into Gmail (I'm setting that up manually: Outlook web > Settings > Mail > Forwarding). Those arrive in Gmail with a `GT` label. Badge them in the UI
- Clef tags every email: `read_today / fyi / ignore` plus confidence. Ignore never shows. Read_today on top
- Click opens the email in Gmail
- No spam filter needed

### Bottom left: News
- Only stock/market news, AI news, startup news
- Clickable, opens the article
- **More sources is not better.** Don't hammer 20 APIs every minute. That just gives the same story 15 times and rate limit bans. Coverage comes from good sources + dedupe + Clef filtering
- Free source ideas (adjust as you see fit):
  - Hacker News API (Firebase, free, real time). Poll ~30s
  - Google News RSS search feeds for topics/companies/tickers (aggregates thousands of outlets)
  - Finnhub free tier for market news (check current limits)
  - RSS: TechCrunch, The Verge AI, company blogs (OpenAI, Anthropic, Cloudflare, Nvidia, etc.)
  - Anything better and free you find
- Poll RSS every 1-2 min. Dedupe by URL and near-duplicate headlines
- Clef scores each item: category (stocks / AI / startups / irrelevant), relevance, breaking or not. Breaking + high relevance jumps to the top and gets visual emphasis
- Make the source list and topics easy for me to edit (a config file)

### Top right: Vitals
- Custom panel, not btop. Small box, so only what matters
- Big: GPU load and GPU temp
- Smaller: VRAM, CPU temp, fan RPM, anything else cool that fits
- Clef stats btop can't show: decisions per minute, avg latency, queue size
- btop-ish feel (braille/sparkline graphs), cyan
- Clef also watches system health in the background and only alerts me when something is actually wrong (not normal spikes)

### Bottom right: Clef core
- The showpiece. Should feel like a living AI, Jarvis from Iron Man / arc reactor vibe
- **No cards.** Think glowing plasma or particle core, rotating HUD rings, shaders (Three.js or whatever looks best)
- Real data flows into it live as particle streams from the edges, colored by source (mail, news, jobs, system)
- Idle: slow breathing. Each real decision: the core flares and ripples outward in the decision's color
- Small HUD readout ticking the latest decision, e.g. `NEWS > AI > 0.94`
- Driven by actual events, never a fake loop
- Keep GPU usage reasonable since Clef shares the GPU

## Job watcher (phase 2, want to test it)

- Get notified within minutes of relevant internship postings
- Most startups post on Greenhouse, Lever, or Ashby, which have public JSON job board endpoints. Poll a watchlist of companies every few minutes, diff for new postings
- Also watch the Simplify internships GitHub repo for new commits/rows
- Clef decides: is this a SWE / AI / ML intern role, summer 2027, US, realistic for a CS sophomore (Georgia Tech, grad May 2028)? Plus score
- Matches push to my phone. ntfy is a good free option, open to better
- Do NOT scrape LinkedIn or Indeed (ToS, bot blocking)
- Make the company watchlist easy to edit. Help me seed it with startups and companies that hire sophomore interns
- Maybe surface recent matches somewhere on the dashboard too. Your call

## Rough order (flexible)

1. Lid/power fixes + Clef-flash running on the GPU, measured
2. Core pipeline: ingest workers > queue > Clef decisions > SQLite > live push to the UI
3. News panel (no auth, fastest win)
4. Clef core visual hooked to real events
5. Vitals panel
6. Gmail + GT label
7. `clef` command + Hyprland layout + systemd services
8. Job watcher + phone notifications

## How I want you to work

- Terse updates, no fluff. Execution over explanation
- Propose ideas and better approaches as you find them
- When something needs me (sudo, OAuth, forwarding test), give me exact steps
- Put it in a git repo. I may make it public later, so no secrets committed (use a .env and .gitignore)
- Keep a short README with how to run, configure sources, and restart services

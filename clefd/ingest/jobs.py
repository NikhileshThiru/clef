"""Internship watcher: get a phone push within minutes of a relevant posting going live.

Two kinds of source:
  * Company job boards polled directly. The board list is every Greenhouse / Lever /
    Ashby / SmartRecruiters / Workday board that has ever posted an internship
    according to Simplify's listings (~2,300 boards), refreshed daily. Boards that
    posted an internship in the last 120 days are polled every few minutes, the
    rest every 15 minutes. Conditional GETs (ETag) make unchanged boards nearly free.
  * Simplify's listings.json as a catch-all for custom career sites.

A posting alerts only if it is new since the last check (the first check of a board
records what's there silently) and fresh by its own timestamp. Only titles that look
like internships reach Clef, which then decides role, term, US, and degree level.
"""
import asyncio
import html
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .. import bus, config, db
from ..notify import ntfy
from ..decider import Spec, decider, register
from .companies import directory, refresh_loop as companies_loop

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) clef-jobwatch/0.1 (personal internship alerts)"
INTERN_TITLE = re.compile(
    r"(?i)\b(intern(ship)?s?|co-?op|student|apprentice(ship)?|summer (analyst|associate|engineer|scholar|fellow)|"
    r"university|campus|early career)\b")
HOT_DAYS = 120
ALERT_KEEP_DAYS = 14

db.conn.executescript("""
CREATE TABLE IF NOT EXISTS job_applied (
    id TEXT PRIMARY KEY,                -- items.id of the match
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS job_boards (
    key        TEXT PRIMARY KEY,        -- "<ats>:<name>"
    company    TEXT,
    last_intern REAL,                   -- newest internship Simplify saw on this board
    ids        TEXT NOT NULL DEFAULT '[]',
    etag       TEXT,
    seeded     INTEGER NOT NULL DEFAULT 0,
    failures   INTEGER NOT NULL DEFAULT 0,
    skip_until REAL NOT NULL DEFAULT 0
);
""")


@dataclass
class Posting:
    board: str          # "greenhouse:cloudflare"
    job_id: str
    company: str
    title: str
    url: str
    location: str = ""
    posted: float | None = None     # epoch seconds, when the board says it went live
    description: str = ""
    extra: dict = field(default_factory=dict)


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(s: str | None) -> str:
    s = html.unescape(html.unescape(s or ""))  # Greenhouse double-escapes its HTML
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


EXCERPT_KEYS = re.compile(
    r"(?i)(intern|co-?op|summer|spring|fall|20(26|27|28|29)|graduat|degree|bachelor|master|ph\.?d|pursuing|enrolled|"
    r"sophomore|junior|senior|freshman|year of|class of|new grad|location|remote|united states|u\.s\.|citizen|"
    r"sponsor|clearance|python|machine learning|frontend|front-end|backend|full[- ]stack)")


def excerpt(desc: str, limit: int = 1300) -> str:
    """Opening of the description plus every line that says who/when/where."""
    lines = [l.strip() for l in desc.splitlines() if l.strip()]
    head = " ".join(lines)[:350]
    keyed = [l[:300] for l in lines if EXCERPT_KEYS.search(l)]
    return (head + "\n" + "\n".join(keyed)).strip()[:limit]


# ---------------------------------------------------------------- adapters

class NotModified(Exception):
    pass


async def _get(client: httpx.AsyncClient, url: str, etag: str | None, **kw) -> httpx.Response:
    headers = {"If-None-Match": etag} if etag else {}
    r = await client.get(url, headers=headers, **kw)
    if r.status_code == 304:
        raise NotModified
    r.raise_for_status()
    return r


async def fetch_greenhouse(c, name, etag):
    r = await _get(c, f"https://boards-api.greenhouse.io/v1/boards/{name}/jobs", etag)
    return r.headers.get("etag"), [
        Posting(f"greenhouse:{name}", str(j["id"]), "", j["title"], j["absolute_url"],
                (j.get("location") or {}).get("name", ""), _ts(j.get("first_published") or j.get("updated_at")))
        for j in r.json().get("jobs", [])]


async def detail_greenhouse(c, p: Posting):
    name = p.board.split(":", 1)[1]
    r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{name}/jobs/{p.job_id}")
    if r.is_success:
        p.description = _text(r.json().get("content"))


async def fetch_lever(c, name, etag):
    r = await _get(c, f"https://api.lever.co/v0/postings/{name}?mode=json", etag)
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        desc = j.get("descriptionPlain", "") + "\n" + "\n".join(
            f"{x.get('text', '')}\n{_text(x.get('content'))}" for x in j.get("lists", [])) + "\n" + j.get("additionalPlain", "")
        out.append(Posting(f"lever:{name}", j["id"], "", j["text"], j.get("hostedUrl", ""),
                           cat.get("location") or ", ".join(cat.get("allLocations") or []),
                           (j.get("createdAt") or 0) / 1000 or None, desc, {"commitment": cat.get("commitment")}))
    return r.headers.get("etag"), out


async def fetch_ashby(c, name, etag):
    r = await _get(c, f"https://api.ashbyhq.com/posting-api/job-board/{name}", etag)
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        out.append(Posting(f"ashby:{name}", j["id"], "", j["title"], j.get("jobUrl", ""), "; ".join(l for l in locs if l),
                           _ts(j.get("publishedAt")), j.get("descriptionPlain", ""),
                           {"employment": j.get("employmentType"), "remote": j.get("isRemote")}))
    return r.headers.get("etag"), out


async def fetch_smartrecruiters(c, name, etag):
    r = await _get(c, f"https://api.smartrecruiters.com/v1/companies/{name}/postings?limit=100", etag)
    return r.headers.get("etag"), [
        Posting(f"smartrecruiters:{name}", j["id"], "", j["name"], f"https://jobs.smartrecruiters.com/{name}/{j['id']}",
                (j.get("location") or {}).get("fullLocation", ""), _ts(j.get("releasedDate")))
        for j in r.json().get("content", [])]


async def detail_smartrecruiters(c, p: Posting):
    name = p.board.split(":", 1)[1]
    r = await c.get(f"https://api.smartrecruiters.com/v1/companies/{name}/postings/{p.job_id}")
    if r.is_success:
        sections = (r.json().get("jobAd") or {}).get("sections") or {}
        p.description = _text("\n".join((s or {}).get("text", "") for s in sections.values()))


WORKDAY_AGO = re.compile(r"(?i)posted (today|yesterday|(\d+)\+? days? ago)")


def _workday_posted(s: str) -> float | None:
    m = WORKDAY_AGO.search(s or "")
    if not m:
        return None
    days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
    return time.time() - days * 86400


async def fetch_workday(c, name, etag):
    host, site = name.split("|", 1)
    tenant = host.split(".")[0]
    # An empty search returns the newest postings first.
    r = await c.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                     json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    r.raise_for_status()
    return None, [
        Posting(f"workday:{name}", j["externalPath"], "", j["title"], f"https://{host}/{site}{j['externalPath']}",
                j.get("locationsText", ""), _workday_posted(j.get("postedOn", "")))
        for j in r.json().get("jobPostings", []) if j.get("externalPath")]


async def detail_workday(c, p: Posting):
    host, site = p.board.split(":", 1)[1].split("|", 1)
    r = await c.get(f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}{p.job_id}")
    if r.is_success:
        info = r.json().get("jobPostingInfo") or {}
        p.description = _text(info.get("jobDescription"))


ADAPTERS = {
    "greenhouse": (fetch_greenhouse, detail_greenhouse),
    "lever": (fetch_lever, None),
    "ashby": (fetch_ashby, None),
    "smartrecruiters": (fetch_smartrecruiters, detail_smartrecruiters),
    "workday": (fetch_workday, detail_workday),
}
# Concurrent requests allowed per platform, so no single host gets hammered.
CONCURRENCY = {"greenhouse": 3, "lever": 2, "ashby": 3, "smartrecruiters": 2, "workday": 3}


# ---------------------------------------------------------------- board list

def board_key_from_url(url: str) -> str | None:
    u = urlsplit(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if "greenhouse.io" in host and parts:
        if parts[0] == "embed":
            m = re.search(r"for=([^&]+)", u.query)
            return f"greenhouse:{m.group(1).lower()}" if m else None
        return f"greenhouse:{parts[0].lower()}"
    if host == "jobs.lever.co" and parts:
        return f"lever:{parts[0].lower()}"
    if host == "jobs.ashbyhq.com" and parts:
        return f"ashby:{parts[0]}"
    if host == "jobs.smartrecruiters.com" and parts:
        return f"smartrecruiters:{parts[0]}"
    if host.endswith("myworkdayjobs.com"):
        segs = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        return f"workday:{host}|{segs[0]}" if segs else None
    return None


def refresh_boards(listings: list[dict]) -> int:
    """Upsert every board Simplify has seen an internship on, plus [extra_boards] from jobs.toml."""
    best: dict[str, tuple[str, float]] = {}
    for l in listings:
        key = board_key_from_url(l.get("url", ""))
        if key and l.get("date_posted", 0) > best.get(key, ("", 0))[1]:
            best[key] = (l.get("company_name", ""), l["date_posted"])
    extra = config.jobs.get().get("extra_boards", {})
    for ats in ("greenhouse", "lever", "ashby"):
        for name in extra.get(ats, []):
            best.setdefault(f"{ats}:{name}", (name, time.time()))  # extras count as hot
    for key, (company, last) in best.items():
        db.conn.execute(
            "INSERT INTO job_boards (key, company, last_intern) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET company = excluded.company, "
            "last_intern = MAX(COALESCE(last_intern, 0), excluded.last_intern)", (key, company, last))
    return len(best)


# ---------------------------------------------------------------- state

class Watch:
    def __init__(self):
        self.client = httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
        self.sems = {ats: asyncio.Semaphore(n) for ats, n in CONCURRENCY.items()}
        self.simplify_etag: str | None = None
        self.simplify_seen: set[str] = set(db.kv_get("simplify_seen", []))
        self.simplify_seeded = bool(self.simplify_seen)
        self.last_sweep: dict[str, float] = {}   # tier -> time the last full sweep finished
        self.boards_total = 0
        self.polls = 0


watch = Watch()


def _seen_recently(company: str, title: str) -> bool:
    """Same company + title already handled in the last 2 weeks (cross-source dedupe)."""
    row = db.conn.execute(
        "SELECT 1 FROM items WHERE source='jobs' AND lower(origin)=lower(?) AND lower(title)=lower(?) AND ingested > ?",
        (company, title, time.time() - ALERT_KEEP_DAYS * 86400)).fetchone()
    return row is not None


async def candidate(p: Posting, via: str) -> None:
    """A new, fresh, intern-titled posting: fetch its details if needed and hand it to Clef."""
    item_id = f"jobs:{p.board}:{p.job_id}"[:200]
    if db.exists(item_id) or _seen_recently(p.company, p.title):
        return
    ats = p.board.split(":", 1)[0]
    detail = ADAPTERS.get(ats, (None, None))[1]
    if detail and not p.description and via != "simplify":  # Simplify ids aren't the board's job ids
        try:
            async with self_sem(ats):
                await detail(watch.client, p)
        except httpx.HTTPError:
            pass
    db.insert_item({"id": item_id, "source": "jobs", "title": p.title, "url": p.url, "origin": p.company,
                    "summary": p.location, "published": p.posted or time.time(),
                    "meta": {"location": p.location, "via": via, "board": p.board,
                             "excerpt": excerpt(p.description), **p.extra}})
    decider.enqueue(item_id, "jobs", p.posted or time.time())
    bus.publish({"t": "ingest", "source": "jobs", "id": item_id, "title": p.title})
    print(f"[jobs] candidate: {p.company} | {p.title} | {via}", flush=True)


def self_sem(ats: str) -> asyncio.Semaphore:
    return watch.sems.get(ats, watch.sems["greenhouse"])


async def poll_board(row) -> None:
    key = row["key"]
    ats, name = key.split(":", 1)
    fetch = ADAPTERS[ats][0]
    try:
        async with self_sem(ats):
            etag, postings = await fetch(watch.client, name, row["etag"])
    except NotModified:
        return
    except (httpx.HTTPError, ValueError, KeyError) as e:
        failures = row["failures"] + 1
        # Dead or moved boards back off: 1 h, 4 h, then a day.
        skip = time.time() + (3600 if failures < 3 else 4 * 3600 if failures < 6 else 86400) if failures >= 2 else 0
        db.conn.execute("UPDATE job_boards SET failures=?, skip_until=? WHERE key=?", (failures, skip, key))
        if failures == 3:
            print(f"[jobs] {key} failing ({e!r}); backing off", flush=True)
        return
    watch.polls += 1
    now_ids = {p.job_id for p in postings}
    prev = set(json.loads(row["ids"]))
    # Workday only shows the newest 20, so remember a rolling window instead of the full list.
    keep = (now_ids | prev) if ats == "workday" else now_ids
    if ats == "workday" and len(keep) > 500:
        keep = now_ids | set(list(prev)[:400])
    db.conn.execute("UPDATE job_boards SET ids=?, etag=?, seeded=1, failures=0, skip_until=0 WHERE key=?",
                    (json.dumps(sorted(keep)), etag, key))
    if not row["seeded"]:
        return  # first look at this board: record what exists, alert on nothing
    max_age = config.jobs.get().get("freshness", {}).get("board_max_age_hours", 24) * 3600
    for p in postings:
        if p.job_id in prev or not INTERN_TITLE.search(p.title):
            continue
        if p.posted and time.time() - p.posted > max_age:
            continue  # new to us, but the board says it's old (reposted / unhidden)
        p.company = row["company"] or name
        await candidate(p, ats)


async def sweep_loop(tier: str) -> None:
    """Poll every board in a tier once per cycle, spread evenly across the cycle."""
    await asyncio.sleep(random.uniform(5, 20))
    while True:
        cfg = config.jobs.get().get("polling", {})
        cycle = cfg.get("hot_cycle_seconds" if tier == "hot" else "cold_cycle_seconds", 180 if tier == "hot" else 900)
        cutoff = time.time() - HOT_DAYS * 86400
        cond = "last_intern >= ?" if tier == "hot" else "COALESCE(last_intern, 0) < ?"
        rows = db.conn.execute(f"SELECT * FROM job_boards WHERE {cond} AND skip_until < ?",
                               (cutoff, time.time())).fetchall()
        random.shuffle(rows)
        start = time.time()
        gap = cycle / max(len(rows), 1)
        tasks = []
        for i, row in enumerate(rows):
            tasks.append(asyncio.create_task(poll_board(row)))
            await asyncio.sleep(max(0.0, start + (i + 1) * gap - time.time()))
        await asyncio.gather(*tasks, return_exceptions=True)
        watch.last_sweep[tier] = time.time()
        bus.publish({"t": "jobs_status"})
        await asyncio.sleep(max(0.0, start + cycle - time.time()))


async def simplify_loop() -> None:
    """Catch-all: Simplify's listings (also the source of the board list, refreshed daily)."""
    last_board_refresh = db.kv_get("boards_refreshed", 0)
    while True:
        cfg = config.jobs.get()
        try:
            headers = {"If-None-Match": watch.simplify_etag} if watch.simplify_etag else {}
            r = await watch.client.get(SIMPLIFY_URL, headers=headers)
            if r.status_code != 304:
                r.raise_for_status()
                watch.simplify_etag = r.headers.get("etag")
                listings = r.json()
                if time.time() - last_board_refresh > 86400 or not watch.boards_total:
                    watch.boards_total = refresh_boards(listings)
                    last_board_refresh = time.time()
                    db.kv_set("boards_refreshed", last_board_refresh)
                    print(f"[jobs] watching {watch.boards_total} boards", flush=True)
                await _simplify_new(listings, cfg)
        except (httpx.HTTPError, ValueError) as e:
            print(f"[jobs] simplify: {e!r}", flush=True)
        await asyncio.sleep(cfg.get("polling", {}).get("simplify_poll_seconds", 300))


async def _simplify_new(listings: list[dict], cfg: dict) -> None:
    max_age = cfg.get("freshness", {}).get("simplify_max_age_hours", 6) * 3600
    ids = {l["id"] for l in listings}
    if not watch.simplify_seeded:
        watch.simplify_seen, watch.simplify_seeded = ids, True
        db.kv_set("simplify_seen", sorted(ids))
        return
    for l in listings:
        if l["id"] in watch.simplify_seen:
            continue
        watch.simplify_seen.add(l["id"])
        if not l.get("active") or not l.get("is_visible", True) or time.time() - l.get("date_posted", 0) > max_age:
            continue
        key = board_key_from_url(l["url"]) or "simplify"
        p = Posting(key if key != "simplify" else "simplify:all", l["id"], l["company_name"], l["title"], l["url"],
                    "; ".join(l.get("locations") or []), l.get("date_posted"),
                    extra={"terms": l.get("terms"), "category": l.get("category"), "degrees": l.get("degrees"),
                           "sponsorship": l.get("sponsorship")})
        await candidate(p, "simplify")
    watch.simplify_seen &= ids  # forget listings Simplify dropped; the freshness check covers re-adds
    db.kv_set("simplify_seen", sorted(watch.simplify_seen))


# ---------------------------------------------------------------- Clef

ROLES = {
    "swe": "software engineering: backend, full-stack, general SWE, platform, developer tools",
    "ai_ml": "AI / machine learning engineering or research",
    "data": "data science, analytics or data engineering",
    "infra": "infrastructure, cloud, DevOps, SRE, security or systems",
    "quant": "quantitative trading, quant developer or quant research",
    "frontend": "frontend-only, UI or web design engineering",
    "mobile": "iOS or Android mobile app development",
    "hardware": "hardware, electrical, mechanical, firmware or robotics hardware",
    "non_engineering": "not an engineering role: business, sales, marketing, finance, operations, design",
}
TERMS = {
    "spring_2027": "spring 2027 (January to May 2027)",
    "summer_2027": "summer 2027",
    "fall_2027": "fall 2027 (August to December 2027)",
    "year_2028_or_later": "2028 or later",
    "earlier": "fall 2026 or earlier, or already started",
    "unclear": "the posting doesn't say when",
}
QUESTIONS = {
    "internship": {"type": "noul", "instructions": "Is this an internship or co-op program for current students "
                                                   "(not a full-time, new grad or contract role, and not a part-time "
                                                   "on-campus student-worker job at a university)?"},
    "role": {"type": "choice", "instructions": "What kind of role is this?", "criteria": ROLES},
    "term": {"type": "choice", "instructions": "When does this internship take place?", "criteria": TERMS},
    "us": {"type": "noul", "instructions": "Can this role be done in the United States (a US location or US remote)?"},
    "bachelors": {"type": "noul",
                  "instructions": "Is it open to students currently pursuing a bachelor's degree "
                                  "(not PhD-only, master's-only, or for people who have already graduated)?"},
    "fit": {"type": "score",
            "instructions": "How well does the actual work in this internship fit the candidate's skills and interests?",
            "criteria": ["poor: a different field (mechanical, civil, chemistry, sales, quality, construction, etc.)",
                         "weak: engineering-adjacent but not software, data or AI work",
                         "okay: some coding or analysis, but mostly IT, support, business analysis or reporting",
                         "good: real software engineering, data science, ML or infrastructure work",
                         "great: core SWE, AI/ML, infra or quant engineering that closely matches the candidate's background"]},
}


def _ago(ts: float | None) -> str:
    if not ts:
        return "unknown"
    m = (time.time() - ts) / 60
    return f"{m:.0f} minutes ago" if m < 90 else f"{m / 60:.1f} hours ago"


def state(item: dict) -> dict:
    m = item["meta"]
    s = {
        "candidate": config.jobs.get().get("profile", {}).get("candidate", ""),
        "company": item["origin"],
        "title": item["title"],
        "location": m.get("location") or "not stated",
        "posted": _ago(item["published"]),
    }
    for k in ("terms", "category", "degrees", "commitment", "employment"):
        if m.get(k):
            s[k] = m[k]
    if m.get("excerpt"):
        s["description_excerpt"] = m["excerpt"]
    return s


def interpret(item: dict, a: dict) -> dict:
    cfg = config.jobs.get().get("match", {})
    role, term = a["role"]["choice"], a["term"]["choice"]
    checks = {
        "internship": a["internship"]["noul"] >= cfg.get("min_internship", 0.75),
        "role": role in cfg.get("roles", ["swe", "ai_ml", "data", "infra", "quant"]),
        "term": term in cfg.get("terms", list(TERMS)),
        "us": a["us"]["noul"] >= cfg.get("min_us", 0.5),
        "bachelors": a["bachelors"]["noul"] >= cfg.get("min_bachelors", 0.4),
    }
    match = all(checks.values())
    failed = [k for k, ok in checks.items() if not ok]
    fit = a["fit"]["score"]
    if match:
        tier, tag = directory.classify(item["origin"], item["meta"].get("board", ""))
        rank = config.jobs.get().get("ranking", {})
        if tier == "top" and fit >= rank.get("push_min_fit", 2.5):
            asyncio.get_running_loop().create_task(notify_match(item, term, tag))
        bus.publish({"t": "jobs_changed"})
    return {
        "label": "match" if match else "skip",
        "confidence": a["role"]["probabilities"][role],
        "score": fit,
        "flag": 1.0 if match else 0.0,
        "path": f"JOBS > {role.upper()} > {'MATCH' if match else 'SKIP ' + failed[0].upper()}",
        "show": match,
    }


register("jobs", Spec(QUESTIONS, state, interpret))


# ---------------------------------------------------------------- notifications

TERM_LABEL = {"spring_2027": "Spring '27", "summer_2027": "Summer '27", "fall_2027": "Fall '27",
              "year_2028_or_later": "2028+", "unclear": ""}


async def notify_match(item: dict, term: str, tag: str) -> None:
    m = item["meta"]
    when = TERM_LABEL.get(term, "")
    await ntfy(f"{item['origin']}: {item['title']}",
               " · ".join(x for x in (when, tag, m.get("location"), f"posted {_ago(item['published'])}") if x),
               click=item["url"], tags=["briefcase"], priority=4)


# ---------------------------------------------------------------- panel

def matches(hours: float = 48) -> list[dict]:
    """Newest first. Each match carries its company tier and a rank for the "today's best" list."""
    rows = db.conn.execute(
        "SELECT i.id, title, origin, url, published, ingested, meta, answers, a.at AS applied FROM items i "
        "LEFT JOIN job_applied a ON a.id = i.id "
        "WHERE source='jobs' AND label='match' AND ingested > ? ORDER BY ingested DESC",
        (time.time() - hours * 3600,)).fetchall()
    cfg = config.jobs.get().get("ranking", {})
    bonus = {"top": cfg.get("top_bonus", 1.5), "startup": cfg.get("startup_bonus", 0.75)}
    out = []
    for r in rows:
        meta, ans = json.loads(r["meta"]), json.loads(r["answers"] or "{}")
        tier, tag = directory.classify(r["origin"], meta.get("board", ""))
        fit = ans.get("fit", {}).get("score")
        out.append({"id": r["id"], "company": r["origin"], "title": r["title"], "url": r["url"],
                    "location": meta.get("location", ""), "via": meta.get("via", ""),
                    "posted": r["published"], "found": r["ingested"],
                    "role": ans.get("role", {}).get("choice"), "term": TERM_LABEL.get(ans.get("term", {}).get("choice"), ""),
                    "tier": tier, "tag": tag, "fit": fit, "applied": r["applied"],
                    "rank": round((2.0 if fit is None else fit) + bonus.get(tier, 0), 3),
                    "pushed": tier == "top" and fit is not None and fit >= cfg.get("push_min_fit", 2.5)})
    return out


def set_applied(item_id: str, applied: bool) -> bool:
    if not db.conn.execute("SELECT 1 FROM items WHERE id=? AND source='jobs' AND label='match'", (item_id,)).fetchone():
        return False
    if applied:
        db.conn.execute("INSERT OR IGNORE INTO job_applied (id, at) VALUES (?, ?)", (item_id, time.time()))
    else:
        db.conn.execute("DELETE FROM job_applied WHERE id=?", (item_id,))
    bus.publish({"t": "jobs_changed"})
    return True


def today_start() -> float:
    """Local midnight (the laptop's timezone), where "today's best" starts."""
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def status() -> dict:
    counts = dict(db.conn.execute(
        "SELECT CASE WHEN last_intern >= ? THEN 'hot' ELSE 'cold' END, COUNT(*) FROM job_boards GROUP BY 1",
        (time.time() - HOT_DAYS * 86400,)).fetchall())
    seeded = db.conn.execute("SELECT COUNT(*) FROM job_boards WHERE seeded=1").fetchone()[0]
    today = db.conn.execute("SELECT COUNT(*) FROM items WHERE source='jobs' AND ingested > ?",
                            (time.time() - 86400,)).fetchone()[0]
    return {"boards": counts, "seeded": seeded, "last_sweep": watch.last_sweep, "checked_24h": today,
            "today_start": today_start(),
            "ntfy": bool(os.environ.get("NTFY_TOPIC"))}


def run_tasks() -> list:
    return [simplify_loop(), sweep_loop("hot"), sweep_loop("cold"), companies_loop()]

"""News ingest: Hacker News, RSS/Atom, Google News search and Finnhub.

Every poller funnels into submit(), which drops exact repeats (same URL) and
near-duplicate headlines. A duplicate bumps the original's outlet count
instead, which is itself a strong "this is big" signal.
"""
import asyncio
import calendar
import hashlib
import html
import math
import os
import random
import re
import time
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from rapidfuzz import fuzz, process

from .. import bus, config, db
from ..decider import Spec, decider, register

UA = "Mozilla/5.0 (X11; Linux x86_64) clef-dashboard/0.1 (personal news reader)"
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_|cmpid|guccounter|src$|ncid)")
DUP_CUTOFF = 85

# Recent headlines for near-duplicate matching: id -> normalized title.
_titles: dict[str, str] = {}


# ---------------------------------------------------------------- normalizing

def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k.lower())])
    return urlunsplit(("https", host, parts.path.rstrip("/"), query, ""))


def clean_text(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def entry_time(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        if entry.get(key):
            return calendar.timegm(entry[key])
    return None


def ago(ts: float | None) -> str:
    if not ts:
        return "unknown"
    mins = max(0, (time.time() - ts) / 60)
    if mins < 60:
        return f"{mins:.0f} minutes ago"
    return f"{mins / 60:.1f} hours ago"


# ---------------------------------------------------------------- submit

def submit(title: str, url: str, origin: str, published: float | None,
           summary: str = "", meta: dict | None = None) -> None:
    cfg = config.sources.get().get("news", {})
    if published and time.time() - published > cfg.get("max_age_hours", 24) * 3600:
        return
    title = clean_text(title)
    if not title or not url:
        return
    item_id = "news:" + hashlib.sha1(normalize_url(url).encode()).hexdigest()[:16]
    if item_id in _titles or db.exists(item_id):
        return

    key = title_key(title)
    match = process.extractOne(key, _titles, scorer=fuzz.token_sort_ratio, score_cutoff=DUP_CUTOFF)
    if match:
        dup_of = match[2]
        count = db.add_duplicate(dup_of, origin)
        bus.publish({"t": "ingest", "source": "news", "id": dup_of, "dup": True, "count": count})
        bus.publish({"t": "news_changed"})
        return

    db.insert_item({"id": item_id, "source": "news", "title": title, "url": url, "origin": origin,
                    "summary": clean_text(summary)[:600], "published": published, "meta": meta or {}})
    _titles[item_id] = key
    decider.enqueue(item_id, "news", published)
    bus.publish({"t": "ingest", "source": "news", "id": item_id, "title": title, "origin": origin})


def load_recent_titles() -> None:
    hours = config.sources.get().get("news", {}).get("keep_hours", 48)
    _titles.clear()
    for item_id, title in db.recent_titles("news", time.time() - hours * 3600):
        _titles[item_id] = title_key(title)


# ---------------------------------------------------------------- pollers

class FeedPoller:
    """Polite RSS polling: conditional GETs so unchanged feeds cost a 304."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.cache: dict[str, dict] = {}

    async def fetch(self, url: str) -> feedparser.FeedParserDict | None:
        headers = {}
        c = self.cache.get(url, {})
        if c.get("etag"):
            headers["If-None-Match"] = c["etag"]
        if c.get("modified"):
            headers["If-Modified-Since"] = c["modified"]
        r = await self.client.get(url, headers=headers)
        if r.status_code == 304:
            return None
        r.raise_for_status()
        self.cache[url] = {"etag": r.headers.get("etag"), "modified": r.headers.get("last-modified")}
        return feedparser.parse(r.content)


async def poll_rss(poller: FeedPoller, feed: dict) -> None:
    parsed = await poller.fetch(feed["url"])
    if not parsed:
        return
    entries = sorted(parsed.entries, key=lambda e: entry_time(e) or 0, reverse=True)[:30]
    for e in entries:
        submit(e.get("title", ""), e.get("link", ""), feed["name"], entry_time(e), e.get("summary", ""))


async def poll_google_news(poller: FeedPoller, feed: dict) -> None:
    url = f"https://news.google.com/rss/search?q={quote_plus(feed['query'])}&hl=en-US&gl=US&ceid=US:en"
    parsed = await poller.fetch(url)
    if not parsed:
        return
    entries = sorted(parsed.entries, key=lambda e: entry_time(e) or 0, reverse=True)[:30]
    for e in entries:
        outlet = (e.get("source") or {}).get("title", "Google News")
        title = e.get("title", "").removesuffix(f" - {outlet}")
        # Google's summary is just the headline again as a link, so skip it.
        submit(title, e.get("link", ""), outlet, entry_time(e), meta={"via": "google_news"})


_hn_seen: set[int] = set()


async def poll_hackernews(client: httpx.AsyncClient, feed: dict) -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    ids = (await client.get(f"{base}/topstories.json")).json()[: feed.get("top_n", 60)]
    new = [i for i in ids if i not in _hn_seen]
    sem = asyncio.Semaphore(8)

    async def one(story_id: int):
        async with sem:
            item = (await client.get(f"{base}/item/{story_id}.json")).json()
        _hn_seen.add(story_id)
        if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
            return
        hn_url = f"https://news.ycombinator.com/item?id={story_id}"
        submit(item.get("title", ""), item.get("url") or hn_url, "Hacker News", item.get("time"),
               meta={"points": item.get("score"), "comments": item.get("descendants"), "hn_url": hn_url})

    await asyncio.gather(*(one(i) for i in new), return_exceptions=True)


async def poll_finnhub(client: httpx.AsyncClient, feed: dict) -> None:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return
    params = {"category": "general", "token": key}
    min_id = db.kv_get("finnhub_min_id")
    if min_id:
        params["minId"] = min_id
    r = await client.get("https://finnhub.io/api/v1/news", params=params)
    r.raise_for_status()
    items = r.json()
    for n in items:
        submit(n.get("headline", ""), n.get("url", ""), n.get("source") or "Finnhub", n.get("datetime"),
               n.get("summary", ""))
    if items:
        db.kv_set("finnhub_min_id", max(n["id"] for n in items))


# ---------------------------------------------------------------- scheduling

def desired_feeds(cfg: dict) -> dict[str, tuple]:
    """Map a stable key to (poll function, feed config, interval) from sources.toml."""
    feeds = {}
    hn = cfg.get("hackernews", {})
    if hn.get("enabled", True):
        feeds["hn"] = ("hn", hn, hn.get("poll_seconds", 30))
    for f in cfg.get("rss", []):
        feeds["rss:" + f["url"]] = ("rss", f, f.get("poll_seconds", 120))
    for f in cfg.get("google_news", []):
        feeds["gn:" + f["query"]] = ("gn", f, f.get("poll_seconds", 120))
    fh = cfg.get("finnhub", {})
    if fh.get("enabled", True) and os.environ.get("FINNHUB_API_KEY"):
        feeds["finnhub"] = ("finnhub", fh, fh.get("poll_seconds", 120))
    return feeds


async def run_feed(key: str, kind: str, client: httpx.AsyncClient, poller: FeedPoller) -> None:
    await asyncio.sleep(random.uniform(0, 10))  # spread the first round out
    failures = 0
    while True:
        spec = desired_feeds(config.sources.get()).get(key)
        if not spec:
            return
        _, feed, interval = spec
        try:
            if kind == "hn":
                await poll_hackernews(client, feed)
            elif kind == "rss":
                await poll_rss(poller, feed)
            elif kind == "gn":
                await poll_google_news(poller, feed)
            elif kind == "finnhub":
                await poll_finnhub(client, feed)
            failures = 0
        except Exception as e:  # one broken feed must never take down the rest
            failures += 1
            print(f"[news] {key}: {e!r}", flush=True)
        # Back off exponentially on repeated failures (rate limits, outages), max 30 min.
        await asyncio.sleep(min(interval * (2 ** min(failures, 4)), 1800) if failures else interval)


async def run() -> None:
    load_recent_titles()
    client = httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    poller = FeedPoller(client)
    tasks: dict[str, asyncio.Task] = {}
    while True:  # supervisor: picks up feeds added or removed in sources.toml
        wanted = desired_feeds(config.sources.get())
        for key, (kind, _, _) in wanted.items():
            if key not in tasks or tasks[key].done():
                tasks[key] = asyncio.create_task(run_feed(key, kind, client, poller))
        for key in list(tasks):
            if key not in wanted:
                tasks.pop(key).cancel()
        await asyncio.sleep(15)


async def prune_loop() -> None:
    while True:
        hours = config.sources.get().get("news", {}).get("keep_hours", 48)
        cutoff = time.time() - hours * 3600
        db.prune("news", cutoff)
        for item_id in [k for k in _titles if not db.exists(k)]:
            del _titles[item_id]
        await asyncio.sleep(600)


# ---------------------------------------------------------------- Clef

CATEGORIES = {
    "stocks": "stock market, earnings, macro economy, specific stocks or tickers",
    "ai": "AI models, AI labs, AI research, AI products and chips",
    "startups": "startup funding, launches, acquisitions, founders, YC",
    "irrelevant": "anything else",
}

QUESTIONS = {
    "category": {"type": "choice", "instructions": "Which topic is this news item about?", "criteria": CATEGORIES},
    "relevance": {"type": "score",
                  "instructions": "How relevant is this news item to the reader described in the state?",
                  "criteria": ["not relevant", "slightly relevant", "relevant", "very relevant", "must read"]},
    "breaking": {"type": "noul",
                 "instructions": "Is this breaking news: a significant event that happened in the last few hours, "
                                 "not an opinion piece, tutorial, analysis or evergreen article?"},
}


SAME_STORY_MIN_P = 0.75


def candidates(item: dict, limit: int = 8) -> list[dict]:
    """Earlier, already-decided headlines that loosely resemble this one."""
    key = title_key(item["title"])
    pool = {k: v for k, v in _titles.items() if k != item["id"]}
    hits = process.extract(key, pool, scorer=fuzz.token_set_ratio, score_cutoff=55, limit=limit * 3)
    out = []
    for _, _, cand_id in hits:
        cand = db.get_item(cand_id)
        if cand and cand["status"] == "decided" and cand["label"] not in ("dup", "irrelevant"):
            out.append(cand)
        if len(out) == limit:
            break
    return out


def questions(item: dict) -> dict:
    qs = dict(QUESTIONS)
    cands = candidates(item)
    if cands:
        qs["same_story"] = {
            "type": "choice",
            "instructions": "Does this headline report the same news event as one of these earlier headlines?",
            "criteria": {"none": "no, a different event",
                         **{f"s{i}": c["title"] for i, c in enumerate(cands)}},
        }
        item["_candidates"] = {f"s{i}": c["id"] for i, c in enumerate(cands)}
    return qs


def state(item: dict) -> dict:
    s = {
        "reader": config.sources.get().get("profile", {}).get("reader", ""),
        "headline": item["title"],
        "outlet": item["origin"],
        "published": ago(item["published"]),
        "outlets_covering": item["dup_count"],
    }
    if item.get("summary"):
        s["summary"] = item["summary"]
    if item["meta"].get("points") is not None:
        s["hacker_news_points"] = item["meta"]["points"]
    return s


def interpret(item: dict, answers: dict) -> dict:
    same = answers.get("same_story")
    if same and same["choice"] != "none" and same["probabilities"][same["choice"]] >= SAME_STORY_MIN_P:
        root = item["_candidates"][same["choice"]]
        db.add_duplicate(root, item["origin"])
        bus.publish({"t": "news_changed"})
        p = same["probabilities"][same["choice"]]
        return {"label": "dup", "confidence": p, "score": None, "flag": None,
                "path": f"NEWS > SAME STORY > {p:.2f}", "show": False}
    cat = answers["category"]
    rel = answers["relevance"]["score"]
    min_rel = config.sources.get().get("news", {}).get("min_relevance", 1.5)
    return {
        "label": cat["choice"],
        "confidence": cat["confidence"],
        "score": rel,
        "flag": answers["breaking"]["noul"],
        "path": f"NEWS > {cat['choice'].upper()} > {cat['probabilities'][cat['choice']]:.2f}",
        "show": cat["choice"] != "irrelevant" and rel >= min_rel,
    }


register("news", Spec(questions, state, interpret))


# ---------------------------------------------------------------- panel query

def is_hot(item: dict, now: float) -> bool:
    age_h = (now - (item["published"] or item["ingested"])) / 3600
    rel = item["score"] or 0
    return rel >= 2.5 and ((item["flag"] or 0) >= 0.7 and age_h < 4 or item["dup_count"] >= 4 and age_h < 3)


def panel(limit: int = 60) -> list[dict]:
    cfg = config.sources.get().get("news", {})
    now = time.time()
    rows = db.conn.execute(
        "SELECT * FROM items WHERE source='news' AND status='decided' AND label != 'irrelevant'"
        " AND label != 'dup' AND score >= ? AND COALESCE(published, ingested) >= ?",
        (cfg.get("min_relevance", 1.5), now - cfg.get("keep_hours", 48) * 3600),
    ).fetchall()
    out = []
    for r in rows:
        it = db.row_to_dict(r)
        age_h = (now - (it["published"] or it["ingested"])) / 3600
        hot = is_hot(it, now)
        it["hot"] = hot
        it["rank"] = it["score"] + (2.5 if hot else 0) + 0.4 * math.log2(it["dup_count"]) - 0.15 * age_h
        it.pop("answers", None)
        out.append(it)
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:limit]

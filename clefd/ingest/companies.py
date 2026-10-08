"""Company tiers for ranking job matches. Clef judges the role; this judges the company.

  * top:     the hand-kept list in jobs.toml (big tech, quant, AI labs, strong startups),
             plus YC companies that YC marks as top companies or that are established
             (20+ people) in the Bay Area.
  * startup: any other active YC company.
  * "":      everyone else.

YC's company directory comes from the public yc-oss dataset, refreshed weekly.
"""
import asyncio
import json
import re
import time

import httpx

from .. import config

YC_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_FILE = config.DATA_DIR / "yc_companies.json"
BAY_AREA = re.compile(
    r"San Francisco|Palo Alto|Mountain View|Menlo Park|Redwood City|San Mateo|Berkeley|Oakland|Sunnyvale|"
    r"San Jose|Santa Clara|Burlingame|Cupertino|Los Altos|Emeryville|Foster City|San Carlos|Belmont")
SUFFIXES = {"inc", "llc", "llp", "ltd", "lp", "corp", "corporation", "co", "company", "com", "plc", "gmbh", "the",
            "technologies", "technology", "holdings", "group", "international", "services", "hq", "us", "usa", "americas"}


def norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("&", " and ")).split()
    return " ".join(w for w in words if w not in SUFFIXES)


def board_slug(board: str) -> str:
    """greenhouse:anthropic -> anthropic; workday:nvidia.wd5.myworkdayjobs.com|Site -> nvidia."""
    ats, _, name = (board or "").partition(":")
    if ats in ("", "simplify"):
        return ""
    return re.sub(r"[^a-z0-9]", "", name.split(".")[0].split("|")[0].lower())


class Directory:
    def __init__(self):
        self.yc: dict[str, dict] = {}       # normalized name / slug / domain -> record
        self._top_src = None
        self._top: set[str] = set()
        self._top_slugs: set[str] = set()

    def load_yc(self) -> None:
        try:
            records = json.loads(YC_FILE.read_text())
        except (FileNotFoundError, ValueError):
            return
        index = {}
        for r in records:
            for key in r.pop("keys"):
                index.setdefault(key, r)
        self.yc = index

    def top_names(self) -> set[str]:
        src = config.jobs.get().get("companies", {}).get("top", [])
        if src is not self._top_src:
            self._top_src, self._top = src, {norm(n) for n in src}
            self._top_slugs = {t.replace(" ", "") for t in self._top}
        return self._top

    def classify(self, company: str, board: str = "") -> tuple[str, str]:
        """(tier, tag) for a company, e.g. ("top", "YC S12 · SF") or ("", "")."""
        n, slug = norm(company), board_slug(board)
        top = self.top_names()
        # Board names are often a short form ("cadence" for Cadence Design Systems), so only trust
        # one for YC when it spells the same name.
        yc = self.yc.get(n) or (self.yc.get(slug) if slug and slug == n.replace(" ", "") else None)
        tag = ""
        if yc:
            tag = yc["batch"] + (" · SF" if yc["bay"] else "")
        if n in top or (slug and slug in self._top_slugs):
            return "top", tag
        if yc:
            min_team = config.jobs.get().get("ranking", {}).get("startup_min_team", 20)
            if yc["top"] or (yc["bay"] and yc["team"] >= min_team):
                return "top", tag
            return "startup", tag
        return "", ""


directory = Directory()
directory.load_yc()


def _batch(b: str) -> str:
    """'Summer 2024' -> 'S24'."""
    m = re.match(r"(\w)\w*\s+(\d{4})", b or "")
    return f"{m[1].upper()}{m[2][2:]}" if m else "YC"


async def refresh_loop() -> None:
    while True:
        age = time.time() - (YC_FILE.stat().st_mtime if YC_FILE.exists() else 0)
        if age > 7 * 86400:
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                    r = await c.get(YC_URL)
                    r.raise_for_status()
                out = []
                for x in r.json():
                    if x.get("status") not in ("Active", "Public"):
                        continue
                    keys = {norm(x["name"]), re.sub(r"[^a-z0-9]", "", x.get("slug", ""))}
                    keys |= {norm(f) for f in x.get("former_names") or []}
                    domain = re.sub(r"^(https?://)?(www\.)?", "", x.get("website") or "").split(".")[0].lower()
                    if domain:
                        keys.add(domain)
                    out.append({"keys": sorted(k for k in keys if len(k) > 2), "name": x["name"],
                                "batch": "YC " + _batch(x.get("batch", "")), "team": x.get("team_size") or 0,
                                "bay": bool(BAY_AREA.search(x.get("all_locations") or "")),
                                "top": bool(x.get("top_company"))})
                YC_FILE.write_text(json.dumps(out))
                directory.load_yc()
                print(f"[companies] {len(out)} active YC companies", flush=True)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                print(f"[companies] YC refresh failed: {e!r}", flush=True)
                await asyncio.sleep(3600)
                continue
        await asyncio.sleep(6 * 3600)

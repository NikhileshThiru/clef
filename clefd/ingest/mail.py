"""Gmail triage: read-only Gmail API, Clef decides read_today / fyi / ignore.

Every poll lists the inbox for the lookback window, fetches only messages it
hasn't seen, and refreshes which ones are still unread / still in the inbox
so the panel can drop what you've already dealt with.
"""
import asyncio
import base64
import html
import re
import time
from datetime import datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from .. import bus, config, db
from ..notify import ntfy
from ..decider import Spec, decider, register

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_FILE = config.DATA_DIR / "gmail_client.json"
TOKEN_FILE = config.DATA_DIR / "gmail_token.json"
LOGIN_FILE = config.DATA_DIR / "gmail_login_at"   # the OAuth app is in Testing mode: logins expire after 7 days
LOGIN_DAYS = 7
API = "https://gmail.googleapis.com/gmail/v1/users/me"
ET = ZoneInfo("America/New_York")
MAX_BODY = 1500  # emails run ~2.8-3.7 chars/token; the whole request must fit 2048 tokens
CATEGORIES = {"CATEGORY_PROMOTIONS": "promotions", "CATEGORY_SOCIAL": "social", "CATEGORY_UPDATES": "updates",
              "CATEGORY_FORUMS": "forums", "CATEGORY_PERSONAL": "primary"}


class Gmail:
    def __init__(self):
        self.creds: Credentials | None = None
        self.client = httpx.AsyncClient(timeout=30)
        self.status = "not_connected"   # not_connected | ok | auth_error | error
        self.error = ""
        self.email = ""
        # id -> (unread, in_inbox), refreshed every poll
        self.state: dict[str, tuple[bool, bool]] = {}

    def load(self) -> bool:
        if not TOKEN_FILE.exists():
            return False
        self.creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        return True

    async def _headers(self, force: bool = False) -> dict:
        if force or not self.creds.valid:
            await asyncio.to_thread(self.creds.refresh, Request())
            TOKEN_FILE.write_text(self.creds.to_json())
        return {"Authorization": f"Bearer {self.creds.token}"}

    async def get(self, path: str, **params) -> dict:
        r = await self.client.get(API + path, params=params, headers=await self._headers())
        if r.status_code == 401:
            r = await self.client.get(API + path, params=params, headers=await self._headers(force=True))
        # Gmail answers 403/429 when one user sends requests too fast; back off and retry.
        for delay in (2, 5, 15):
            if r.status_code not in (403, 429) or "ateLimit" not in r.text:
                break
            await asyncio.sleep(delay)
            r = await self.client.get(API + path, params=params, headers=await self._headers())
        if r.is_error:
            raise GmailError(f"{r.status_code} {path}: {r.text[:300]}")
        return r.json()

    async def list_ids(self, query: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"q": query, "maxResults": 100}
            if token:
                params["pageToken"] = token
            page = await self.get("/messages", **params)
            out += page.get("messages", [])
            token = page.get("nextPageToken")
            if not token or len(out) >= 300:
                return out


class GmailError(Exception):
    pass


gmail = Gmail()


# ---------------------------------------------------------------- parsing

def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def _body(payload: dict) -> tuple[str, list[str]]:
    plain, rich, files = [], [], []

    def walk(part: dict):
        mime = part.get("mimeType", "")
        if part.get("filename"):
            files.append(part["filename"])
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_html_to_text(_decode(data)))
        for p in part.get("parts", []):
            walk(p)

    walk(payload)
    text = "\n".join(plain) or "\n".join(rich)
    text = re.sub(r"https?://\S{40,}", "[link]", text)   # tracking URLs eat the token budget
    text = re.sub(r"[ \t ‌]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:MAX_BODY], files


def parse(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    body, files = _body(msg["payload"])
    labels = msg.get("labelIds", [])
    category = next((v for k, v in CATEGORIES.items() if k in labels), "primary")
    return {
        "id": f"mail:{msg['id']}",
        "source": "mail",
        "title": headers.get("subject", "(no subject)").strip() or "(no subject)",
        "origin": name or addr,
        "summary": html.unescape(msg.get("snippet", "")),
        "url": f"https://mail.google.com/mail/?authuser={gmail.email}#all/{msg['threadId']}",
        "published": int(msg["internalDate"]) / 1000,
        "meta": {
            "gmail_id": msg["id"], "thread_id": msg["threadId"], "from_addr": addr, "labels": labels,
            "category": category, "important": "IMPORTANT" in labels, "starred": "STARRED" in labels,
            "mailing_list": "list-unsubscribe" in headers or "list-id" in headers,
            "attachments": files, "body": body,
        },
    }


# ---------------------------------------------------------------- polling

async def poll() -> None:
    cfg = config.mail.get().get("gmail", {})
    days = cfg.get("lookback_days", 3)
    inbox = await gmail.list_ids(f"in:inbox newer_than:{days}d")
    unread = {m["id"] for m in await gmail.list_ids(f"in:inbox is:unread newer_than:{days}d")}
    inbox_ids = {m["id"] for m in inbox}

    changed = False
    for gid in list(gmail.state):
        new = (gid in unread, gid in inbox_ids)
        if gmail.state[gid] != new:
            gmail.state[gid] = new
            changed = True
    for m in inbox:
        gid = m["id"]
        gmail.state.setdefault(gid, (gid in unread, True))
        if db.exists(f"mail:{gid}"):
            continue
        try:
            item = parse(await gmail.get(f"/messages/{gid}", format="full"))
        except GmailError as e:
            # Skip just this message; it is retried on the next poll.
            print(f"[mail] fetch failed, will retry: {e}", flush=True)
            continue
        await asyncio.sleep(0.1)  # stay well under Gmail's per-user rate limit during a backlog
        db.insert_item(item)
        decider.enqueue(item["id"], "mail", item["published"])
        bus.publish({"t": "ingest", "source": "mail", "id": item["id"], "title": item["title"]})
    if changed:
        bus.publish({"t": "mail_changed"})


async def run() -> None:
    failures = 0
    while True:
        if gmail.creds is None:
            if not gmail.load():
                gmail.status = "not_connected"
                await asyncio.sleep(30)   # waits for `python -m clefd.gmail_auth` to drop a token
                continue
            try:
                gmail.email = (await gmail.get("/profile"))["emailAddress"]
            except Exception as e:
                gmail.status, gmail.error = "auth_error", repr(e)
                print(f"[mail] {gmail.error}", flush=True)
                gmail.creds = None
                await asyncio.sleep(300)
                continue
            gmail.status = "ok"
            bus.publish({"t": "mail_changed"})
        try:
            await poll()
            if gmail.status != "ok" or gmail.error:
                gmail.status, gmail.error = "ok", ""
                bus.publish({"t": "mail_changed"})
            failures = 0
        except Exception as e:
            failures += 1
            gmail.status = "auth_error" if "invalid_grant" in repr(e) else "error"
            gmail.error = repr(e)
            print(f"[mail] poll failed: {e!r}", flush=True)
            bus.publish({"t": "mail_changed"})
            if gmail.status == "auth_error":
                gmail.creds = None   # token revoked/expired: wait for a fresh login
                await ntfy("Clef: Gmail login expired", "Mail triage is paused until you re-run "
                           "uv run python -m clefd.gmail_auth", tags=["email"], priority=4)
        interval = config.mail.get().get("gmail", {}).get("poll_seconds", 60)
        await asyncio.sleep(min(interval * 2 ** min(failures, 4), 1800))


def login_age_days() -> float | None:
    try:
        return (time.time() - float(LOGIN_FILE.read_text())) / 86400
    except (OSError, ValueError):
        return None


async def reminder_loop() -> None:
    """Push a re-login reminder a day before Google expires the Testing-mode login."""
    while True:
        age = login_age_days()
        reminded = db.kv_get("gmail_reminded_for")
        login_at = LOGIN_FILE.read_text().strip() if LOGIN_FILE.exists() else None
        if age is not None and age >= LOGIN_DAYS - 1 and reminded != login_at:
            await ntfy("Clef: Gmail login expires tomorrow",
                       "Re-run: ssh -L 8765:localhost:8765 <user>@<laptop>, then "
                       "cd ~/projects/clef && uv run python -m clefd.gmail_auth", tags=["email"], priority=3)
            db.kv_set("gmail_reminded_for", login_at)
        await asyncio.sleep(3600)


async def prune_loop() -> None:
    while True:
        days = config.mail.get().get("gmail", {}).get("lookback_days", 3)
        db.prune("mail", time.time() - (days + 4) * 86400)
        await asyncio.sleep(3600)


# ---------------------------------------------------------------- Clef

QUESTIONS = {
    "triage": {
        "type": "choice",
        "instructions": "How should the reader handle this email today?",
        "criteria": {
            "read_today": "needs the reader's attention today: a real person writing to them, school, professors, "
                          "TAs or GT offices, recruiters or interviews, deadlines soon, account security, money owed",
            "fyi": "worth a glance but not urgent: receipts, shipping, confirmations, newsletters worth skimming, "
                   "event announcements",
            "ignore": "promotions, marketing, sales, social media notifications, automated noise",
        },
    },
    "needs_reply": {"type": "noul", "instructions": "Does the sender expect a reply from the reader?"},
    "deadline": {"type": "noul",
                 "instructions": "Does this email mention a deadline, due date or time-sensitive action in the next 3 days?"},
    "one_time_code": {"type": "noul",
                      "instructions": "Is this email a one-time verification code, login code, magic sign-in link or "
                                      "password reset, which is only useful for a few minutes?"},
}

# Fallback for emails triaged before the one_time_code question existed.
CODE_SUBJECT = re.compile(r"(?i)\b(verification|verify|one[- ]time|otp|passcode|login code|sign[- ]in (code|link)|"
                          r"security code|confirm(ation)? code|reset your password|\d{6})\b")


def effective_label(label: str, confidence: float, answers: dict, title: str, published: float) -> str:
    """Panel-time rules on top of Clef's call, tunable in mail.toml without re-triaging."""
    cfg = config.mail.get().get("gmail", {})
    code = answers.get("one_time_code", {}).get("noul")
    is_code = code >= 0.6 if code is not None else bool(CODE_SUBJECT.search(title))
    if is_code and time.time() - published > cfg.get("code_stale_minutes", 15) * 60:
        return "ignore"
    if label == "read_today" and confidence < cfg.get("read_today_min", 0.6):
        return "fyi"
    return label


def _when(ts: float) -> str:
    mins = (time.time() - ts) / 60
    if mins < 60:
        ago = f"{mins:.0f} minutes ago"
    elif mins < 2880:
        ago = f"{mins / 60:.1f} hours ago"
    else:
        ago = f"{mins / 1440:.0f} days ago"
    return f"{ago} ({datetime.fromtimestamp(ts, ET):%a %b %d, %I:%M %p} ET)"


def state(item: dict) -> dict:
    m = item["meta"]
    s = {
        "reader": config.mail.get().get("profile", {}).get("reader", ""),
        "from": f"{item['origin']} <{m['from_addr']}>",
        "subject": item["title"],
        "received": _when(item["published"]),
        "gmail_category": m["category"],
        "gmail_marked_important": m["important"],
        "mailing_list": m["mailing_list"],
    }
    if m["starred"]:
        s["starred"] = True
    if m["attachments"]:
        s["attachments"] = m["attachments"][:5]
    s["body"] = m["body"] or item["summary"]
    return s


def interpret(item: dict, answers: dict) -> dict:
    t = answers["triage"]
    p = t["probabilities"][t["choice"]]
    shown_as = effective_label(t["choice"], p, answers, item["title"], item["published"])
    return {
        "label": t["choice"],
        "confidence": p,
        "score": t["probabilities"]["read_today"],
        "flag": max(answers["needs_reply"]["noul"], answers["deadline"]["noul"]),
        "path": f"MAIL > {shown_as.upper()} > {p:.2f}",
        "show": shown_as != "ignore",
    }


register("mail", Spec(QUESTIONS, state, interpret))


# ---------------------------------------------------------------- panel query

def panel() -> dict:
    cfg = config.mail.get().get("gmail", {})
    since = time.time() - cfg.get("lookback_days", 3) * 86400
    rows = db.conn.execute(
        "SELECT * FROM items WHERE source='mail' AND status='decided' AND label IN ('read_today', 'fyi')"
        " AND published >= ? AND confidence >= ?", (since, cfg.get("min_confidence", 0.0))).fetchall()
    items = []
    for r in rows:
        it = db.row_to_dict(r)
        gid = it["meta"]["gmail_id"]
        unread, in_inbox = gmail.state.get(gid, (True, True))
        if not in_inbox:
            continue  # archived or deleted: dealt with
        a = it.pop("answers") or {}
        label = effective_label(it["label"], it["confidence"], a, it["title"], it["published"])
        if label == "ignore":
            continue
        items.append({
            "id": it["id"], "title": it["title"], "origin": it["origin"], "summary": it["summary"],
            "url": it["url"], "label": label, "confidence": it["confidence"], "published": it["published"],
            "unread": unread, "category": it["meta"]["category"],
            "needs_reply": a.get("needs_reply", {}).get("noul", 0),
            "deadline": a.get("deadline", {}).get("noul", 0),
        })
    # Unread read_today first, most confident on top; then unread fyi and already-read mail, newest first.
    def key(i):
        if i["unread"] and i["label"] == "read_today":
            return (0, -i["confidence"])
        return (1 if i["unread"] else 2, -i["published"])
    items.sort(key=key)
    pending = db.conn.execute("SELECT COUNT(*) FROM items WHERE source='mail' AND status='pending'").fetchone()[0]
    age = login_age_days()
    expires_in = round(LOGIN_DAYS - age, 1) if age is not None else None
    return {"status": gmail.status, "error": gmail.error, "email": gmail.email, "pending": pending, "items": items,
            "login_expires_days": expires_in}

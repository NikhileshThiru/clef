"""SQLite storage. One connection, WAL mode; every query here is small and fast."""
import json
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,          -- "<source>:<stable key>"
    source      TEXT NOT NULL,             -- news | jobs | system
    title       TEXT NOT NULL,
    url         TEXT,
    origin      TEXT,                      -- publisher / sender / company
    summary     TEXT,
    meta        TEXT NOT NULL DEFAULT '{}',
    published   REAL,
    ingested    REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | decided | error
    label       TEXT,
    confidence  REAL,
    score       REAL,
    flag        REAL,                      -- breaking / urgent probability
    answers     TEXT,
    decided_at  REAL,
    dup_count   INTEGER NOT NULL DEFAULT 1,
    dup_origins TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS items_source_time ON items(source, published);
CREATE INDEX IF NOT EXISTS items_status ON items(status);

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    item_id    TEXT NOT NULL,
    source     TEXT NOT NULL,
    label      TEXT,
    confidence REAL,
    latency_ms REAL,
    tokens     INTEGER,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


conn = connect()


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("meta", "answers", "dup_origins"):
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def get_item(item_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return row_to_dict(row) if row else None


def insert_item(item: dict) -> bool:
    """Insert a new item. Returns False if the id already exists."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO items (id, source, title, url, origin, summary, meta, published, ingested)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item["id"], item["source"], item["title"], item.get("url"), item.get("origin"),
         item.get("summary"), json.dumps(item.get("meta", {})), item.get("published"), time.time()),
    )
    return cur.rowcount == 1


def exists(item_id: str) -> bool:
    return conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is not None


def add_duplicate(item_id: str, origin: str) -> int:
    """Record that another outlet ran the same story. Returns the new count."""
    item = get_item(item_id)
    if not item:
        return 0
    origins = item["dup_origins"]
    if origin and origin not in origins and origin != item["origin"]:
        origins.append(origin)
    conn.execute("UPDATE items SET dup_count = dup_count + 1, dup_origins = ? WHERE id = ?",
                 (json.dumps(origins), item_id))
    return item["dup_count"] + 1


def save_decision(item_id: str, source: str, *, label, confidence, score, flag, answers,
                  latency_ms: float, tokens: int) -> None:
    now = time.time()
    conn.execute(
        "UPDATE items SET status='decided', label=?, confidence=?, score=?, flag=?, answers=?, decided_at=?"
        " WHERE id = ?",
        (label, confidence, score, flag, json.dumps(answers), now, item_id),
    )
    conn.execute(
        "INSERT INTO decisions (item_id, source, label, confidence, latency_ms, tokens, ts)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, source, label, confidence, latency_ms, tokens, now),
    )


def mark_error(item_id: str) -> None:
    conn.execute("UPDATE items SET status='error' WHERE id = ?", (item_id,))


def pending_ids() -> list[tuple[str, str, float]]:
    return [tuple(r) for r in conn.execute(
        "SELECT id, source, COALESCE(published, ingested) FROM items WHERE status='pending'")]


def recent_titles(source: str, since: float) -> list[tuple[str, str]]:
    return [tuple(r) for r in conn.execute(
        "SELECT id, title FROM items WHERE source = ? AND COALESCE(published, ingested) >= ?",
        (source, since))]


def kv_get(key: str, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(key: str, value) -> None:
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))


def prune(source: str, older_than: float) -> None:
    conn.execute("DELETE FROM items WHERE source = ? AND COALESCE(published, ingested) < ?", (source, older_than))
    conn.execute("DELETE FROM decisions WHERE ts < ?", (time.time() - 7 * 86400,))

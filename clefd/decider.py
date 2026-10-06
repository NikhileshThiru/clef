"""The decision loop: one queue, one worker, one llama-server.

llama-server evaluates a single sequence per batch, so a serial worker is the
fastest it gets. Items wait in a priority queue (mail before jobs before
system before news, newest first within a source) and every answer is saved
and broadcast so the UI can react to the real decision as it happens.
"""
import asyncio
import itertools
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import httpx

from . import bus, config, db

PRIORITY = {"mail": 0, "jobs": 1, "system": 2, "news": 3}
# ~4 chars per token; leaves room for the questions inside the 2048-token batch.
MAX_STATE_CHARS = 5000


@dataclass
class Spec:
    """How to ask Clef about one kind of item."""
    questions: dict | Callable[[dict], dict]  # static, or built per item
    state: Callable[[dict], object]
    interpret: Callable[[dict, dict], dict]  # -> label, confidence, score, flag, path, show


SPECS: dict[str, Spec] = {}


def register(source: str, spec: Spec) -> None:
    SPECS[source] = spec


def truncate(value, limit: int):
    """Shrink the longest strings in a state until it fits the token budget."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {k: truncate(v, limit) for k, v in value.items()}
    return value


class Decider:
    def __init__(self):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.seq = itertools.count()
        self.queued: set[str] = set()
        self.recent: deque = deque(maxlen=2000)  # (ts, latency_ms, tokens, source)
        self.up = False
        self.last: dict | None = None
        self.client = httpx.AsyncClient(base_url=config.LLAMA_URL, timeout=30)

    def enqueue(self, item_id: str, source: str, published: float | None) -> None:
        if item_id in self.queued:
            return
        self.queued.add(item_id)
        self.queue.put_nowait((PRIORITY.get(source, 9), -(published or time.time()), next(self.seq), item_id))

    def restore_pending(self) -> None:
        for item_id, source, published in db.pending_ids():
            self.enqueue(item_id, source, published)

    async def _ask(self, state, questions: dict) -> dict:
        limit = MAX_STATE_CHARS
        while True:
            r = await self.client.post("/v1/systemone", json={"state": truncate(state, limit), "questions": questions})
            if r.status_code == 500 and "too large" in r.text and limit > 250:
                limit //= 2
                continue
            r.raise_for_status()
            return r.json()

    async def run(self) -> None:
        while True:
            prio, neg_pub, seq, item_id = await self.queue.get()
            item = db.get_item(item_id)
            spec = SPECS.get(item["source"]) if item else None
            if not item or not spec or item["status"] != "pending":
                self.queued.discard(item_id)
                continue
            t0 = time.perf_counter()
            try:
                questions = spec.questions(item) if callable(spec.questions) else spec.questions
                resp = await self._ask(spec.state(item), questions)
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    print(f"[decider] {item_id}: {e.response.status_code} {e.response.text[:200]}", flush=True)
                    db.mark_error(item_id)
                    self.queued.discard(item_id)
                    continue
                # Clef is down or restarting: put the item back and wait.
                if self.up:
                    self.up = False
                    print(f"[decider] llama-server unavailable: {e!r}", flush=True)
                self.queue.put_nowait((prio, neg_pub, seq, item_id))
                await asyncio.sleep(3)
                continue
            latency = (time.perf_counter() - t0) * 1000
            self.up = True
            self.queued.discard(item_id)

            item["questions"] = questions
            verdict = spec.interpret(item, resp["answers"])
            tokens = resp.get("usage", {}).get("input_tokens", 0)
            db.save_decision(item_id, item["source"], label=verdict["label"], confidence=verdict["confidence"],
                             score=verdict.get("score"), flag=verdict.get("flag"), answers=resp["answers"],
                             latency_ms=latency, tokens=tokens)
            self.recent.append((time.time(), latency, tokens, item["source"]))
            self.last = {
                "t": "decision", "id": item_id, "source": item["source"], "title": item["title"],
                "label": verdict["label"], "confidence": verdict["confidence"],
                "score": verdict.get("score"), "flag": verdict.get("flag"),
                "path": verdict["path"], "show": verdict["show"],
                "latency_ms": round(latency, 1), "tokens": tokens, "ts": time.time(),
            }
            bus.publish(self.last)

    async def health_loop(self) -> None:
        """Notice llama-server coming back even when the queue is empty."""
        while True:
            try:
                r = await self.client.get("/health", timeout=3)
                self.up = r.status_code == 200
            except httpx.TransportError:
                self.up = False
            await asyncio.sleep(5)

    def stats(self) -> dict:
        now = time.time()
        last_min = [r for r in self.recent if now - r[0] <= 60]
        lat = [r[1] for r in last_min] or [r[1] for r in list(self.recent)[-20:]]
        return {
            "t": "stats",
            "clef_up": self.up,
            "queue": self.queue.qsize(),
            "dpm": len(last_min),
            "avg_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "tokens_per_min": sum(r[2] for r in last_min),
            "by_source": {s: sum(1 for r in last_min if r[3] == s) for s in PRIORITY},
            "total": len(self.recent),
        }

    async def stats_loop(self) -> None:
        while True:
            bus.publish(self.stats())
            await asyncio.sleep(1)


decider = Decider()

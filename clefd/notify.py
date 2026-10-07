"""Phone notifications through ntfy (https://ntfy.sh). The topic in .env works like a password."""
import os

import httpx

_client = httpx.AsyncClient(timeout=15)


async def ntfy(title: str, message: str, click: str | None = None, tags: list[str] | None = None,
               priority: int = 3) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    body = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": tags or []}
    if click:
        body["click"] = click
        body["actions"] = [{"action": "view", "label": "Open", "url": click, "clear": True}]
    try:
        r = await _client.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh"), json=body)
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[ntfy] failed: {e!r}", flush=True)

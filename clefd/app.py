"""FastAPI app: serves the dashboard, a small JSON API and the live event socket."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import bus, config
from .decider import decider
from .ingest import news, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    decider.restore_pending()
    tasks = [asyncio.create_task(c, name=c.__qualname__) for c in (
        decider.run(), decider.health_loop(), decider.stats_loop(),
        news.run(), news.prune_loop(),
        system.sample_loop(), system.check_loop(),
    )]
    for t in tasks:
        t.add_done_callback(_crash_on_task_death)
    yield
    for t in tasks:
        t.cancel()


def _crash_on_task_death(task: asyncio.Task) -> None:
    """Background loops should run forever. If one dies, log why and exit so systemd restarts us clean."""
    if task.cancelled():
        return
    exc = task.exception()
    print(f"[clefd] background task {task.get_name()} died: {exc!r}", flush=True)
    if exc:
        import traceback
        traceback.print_exception(exc)
    os._exit(1)


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/news")
async def api_news():
    return news.panel()


@app.get("/api/vitals")
async def api_vitals():
    return system.snapshot()


@app.get("/api/stats")
async def api_stats():
    return decider.stats() | {"last": decider.last}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q = bus.subscribe()
    try:
        await sock.send_json(decider.stats() | {"t": "hello", "last": decider.last, "alert": system.health.alert})
        while True:
            await sock.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)

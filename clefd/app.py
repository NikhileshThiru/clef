"""FastAPI app: serves the dashboard, a small JSON API and the live event socket."""
import asyncio
import os
import subprocess
import time
from urllib.parse import urlsplit
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import bus, config
from .decider import decider
from .ingest import jobs, mail, news, system


# CLEF_DEMO=1: serve whatever is already in the database and poll nothing (used for README screenshots).
DEMO = os.environ.get("CLEF_DEMO") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    loops = [decider.health_loop()]
    if DEMO:
        jobs.watch.last_sweep = {"hot": time.time() - 70, "cold": time.time() - 400}
    else:
        decider.restore_pending()
        loops += [decider.run(), decider.stats_loop(), news.run(), news.prune_loop(), system.sample_loop(), system.check_loop(),
                  mail.run(), mail.prune_loop(), mail.reminder_loop(), *jobs.run_tasks()]
    tasks = [asyncio.create_task(c, name=c.__qualname__) for c in loops]
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


BUILD = str(time.time())  # changes on every restart; open pages reload when it does

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Without this, Chromium heuristically caches the JS/CSS and the kiosk keeps running old code
    after an update. no-cache still allows a cheap 304 when nothing changed."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/news")
async def api_news():
    return news.panel()


@app.post("/api/open")
async def api_open(body: dict, request: Request):
    """Kiosk link clicks: open in the desktop's default browser instead of inside the kiosk.

    POST with a JSON body, so a random web page can't trigger it cross-origin
    (that needs a CORS preflight, which this app never grants)."""
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "only the kiosk on this laptop can open links here")
    url = str(body.get("url", ""))
    if urlsplit(url).scheme not in ("http", "https") or any(c in url for c in "\n\r"):
        raise HTTPException(400, "http(s) URLs only")
    subprocess.Popen(["setsid", "xdg-open", url], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


@app.get("/jobs")
async def jobs_page():
    """Standalone list of fresh matches, for the Mac (http://omarchy:8077/jobs over Tailscale)."""
    return FileResponse(config.WEB_DIR / "jobs.html", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs")
async def api_jobs(hours: float = 48):
    return {"matches": jobs.matches(hours), "status": jobs.status()}


@app.get("/api/mail")
async def api_mail():
    return mail.panel()


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
        await sock.send_json(decider.stats() | {"t": "hello", "build": BUILD, "last": decider.last,
                                                "alert": system.health.alert})
        while True:
            await sock.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)

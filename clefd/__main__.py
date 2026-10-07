import asyncio
import socket
import subprocess

import uvicorn

from . import config

config.load_env()


def tailscale_ip() -> str | None:
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        return out[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def bind(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(128)
    return s


async def serve_tailscale() -> None:
    """Also listen on the Tailscale address (for http://omarchy:8077/jobs from the Mac).
    At boot Tailscale may come up after us, so keep trying. Lifespan is off: the
    background loops already run in the loopback server."""
    while not (ts := tailscale_ip()):
        await asyncio.sleep(30)
    try:
        sock = bind(ts, config.PORT)
    except OSError as e:
        print(f"[clefd] tailscale bind failed: {e}", flush=True)
        return
    print(f"[clefd] also serving on tailscale {ts}:{config.PORT}", flush=True)
    server = uvicorn.Server(uvicorn.Config("clefd.app:app", log_level="warning", lifespan="off"))
    await server.serve(sockets=[sock])


async def main() -> None:
    # Loopback for the kiosk plus the Tailscale address. Nothing listens on LAN / Wi-Fi.
    server = uvicorn.Server(uvicorn.Config("clefd.app:app", log_level="warning"))
    tasks = [server.serve(sockets=[bind(config.HOST, config.PORT)])]
    if config.HOST in ("127.0.0.1", "localhost"):
        tasks.append(serve_tailscale())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

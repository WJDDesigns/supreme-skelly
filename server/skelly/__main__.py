import asyncio
import logging
import os
import socket

import uvicorn

log = logging.getLogger("skelly")


def _listen(host: str, port: int) -> socket.socket | None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        log.info("Not serving on port %s too (%s)", port, exc.strerror or exc)
        return None
    sock.set_inheritable(True)
    return sock


async def _serve(host: str, port: int, extra: list[int]) -> None:
    main_server = uvicorn.Server(uvicorn.Config("skelly.api:app", host=host, port=port, log_level="info"))
    servers, tasks = [], []
    # Extra ports (80 by default) serve the same app so a plain http://skelly.local works.
    # The service itself only starts once, on the main server; these skip startup/shutdown.
    for p in extra:
        if p != port and (sock := _listen(host, p)):
            cfg = uvicorn.Config("skelly.api:app", host=host, port=p, log_level="warning", lifespan="off")
            server = uvicorn.Server(cfg)
            server.install_signal_handlers = lambda: None
            servers.append(server)
            tasks.append(server.serve(sockets=[sock]))
            log.info("Also serving the app on port %s", p)
    extras = [asyncio.create_task(t) for t in tasks]
    try:
        await main_server.serve()  # returns on Ctrl+C / docker stop
    finally:
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*extras, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=os.environ.get("SKELLY_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("SKELLY_HOST", "0.0.0.0")
    port = int(os.environ.get("SKELLY_PORT", "8420"))
    extra = [int(p) for p in os.environ.get("SKELLY_EXTRA_PORTS", "").replace(",", " ").split()]
    asyncio.run(_serve(host, port, extra))


if __name__ == "__main__":
    main()

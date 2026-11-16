"""A tiny round-robin load balancer, standing in for Nginx or a cloud load balancer.

    python -m merch_platform.devtools.balancer --port 8080 \\
        --backends http://127.0.0.1:8001,http://127.0.0.1:8002

Each incoming request goes to the NEXT backend in turn (a, b, a, b, ...), with no
"stickiness": the same client is deliberately bounced between copies. If the server
kept anything in memory between requests, this is where it would break. Because it
is stateless (and the copies share their request-state keys), it does not.

Kept deliberately small: it buffers each request and response, which is fine because
the server replies with plain JSON (json_response=True). A real deployment would use
a real load balancer.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx2
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger("merch_platform.balancer")

# Headers that describe one hop of a connection and must not be copied to the next.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def create_balancer(backends: Sequence[str]) -> Starlette:
    if not backends:
        raise ValueError("at least one backend is needed")
    order = itertools.cycle([b.rstrip("/") for b in backends])
    clients: dict[str, httpx2.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with httpx2.AsyncClient(timeout=60.0) as client:
            clients["http"] = client
            yield

    async def forward(request: Request) -> Response:
        backend = next(order)
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
        try:
            upstream = await clients["http"].request(
                request.method,
                backend + request.url.path,
                params=request.query_params,
                headers=headers,  # Host is passed through, so the server checks the real one
                content=await request.body(),
            )
        except httpx2.TransportError as err:
            log.warning("backend %s unreachable: %s", backend, err)
            return JSONResponse({"error": f"backend {backend} unreachable"}, status_code=502)
        log.info("%s %s -> %s %s", request.method, request.url.path, backend, upstream.status_code)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP},
        )

    return Starlette(
        routes=[Route("/{path:path}", forward, methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Round-robin load balancer for local testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--backends", required=True, help="Comma-separated backend URLs.")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # our own line per request is enough
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    log.info("balancing http://%s:%d -> %s", args.host, args.port, ", ".join(backends))
    uvicorn.run(create_balancer(backends), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

"""Phase 6: the same MCP server as a web service, over Streamable HTTP.

    python -m merch_platform.server.http                      # http://127.0.0.1:8000/mcp
    python -m merch_platform.server.http --port 8001 --instance a

STDIO vs HTTP. Over stdio (Phases 2-5) the client starts the server as its own child
process: one client, one server, same computer. Over Streamable HTTP the server is a
normal web service: every MCP message is an HTTP POST to one address (/mcp), so many
clients can share it, and several copies can run behind a load balancer.

STATELESS. Under the 2026-07-28 spec every request carries everything needed to
handle it, and this app runs with `stateless_http=True`: no session, no memory of a
client between requests. That is what lets ANY copy answer ANY request.
(The services object created at start-up is shared wiring, a database connection pool,
not per-client state. Anything that must outlive a request belongs in Postgres.)

The one thing that does travel between requests is a confirmation round trip: the
server answers "input required" with a sealed `requestState`, and the client sends
it back with the answer. The copy that receives the answer must be able to unseal
what another copy sealed, so every copy needs the SAME keys
(MERCH_REQUEST_STATE_KEYS). tests/server/test_http.py proves both sides of this.

SECURITY ON A LOCAL MACHINE.
* Bind to 127.0.0.1, so only this computer can connect.
* Check Host and Origin headers. A web page in your browser can try to send requests
  to local servers ("DNS rebinding"); requests whose Host/Origin is not localhost are
  refused with 403 or 421. Non-browser clients send no Origin and are unaffected.

LOGGING. Each request is logged on one line with the MCP routing headers the client
sends (Mcp-Method, Mcp-Name), so logs and, in Phase 8, rate limits can tell
"tools/call search_products" from "tools/call set_price" without reading the body.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence

import uvicorn
from mcp.server import MCPServer
from mcp.server.request_state import RequestStateSecurity
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from merch_platform.config import get_settings
from merch_platform.server.app import ServicesFactory, create_server, postgres_services

log = logging.getLogger("merch_platform.http")

MCP_PATH = "/mcp"
INSTANCE_HEADER = "x-merch-instance"

# Only these may appear in the Host / Origin headers. ":*" means any port.
LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def request_state_security(keys: Sequence[str]) -> RequestStateSecurity:
    """Shared keys when configured; otherwise a random key for this process only."""
    if keys:
        return RequestStateSecurity(keys=list(keys))
    log.warning(
        "MERCH_REQUEST_STATE_KEYS is empty: using a random key for this process. Fine for "
        "one copy; with several copies, confirmations will fail on the other copies."
    )
    return RequestStateSecurity.ephemeral()


def transport_security() -> TransportSecuritySettings:
    """Always on, whatever address we bind to: anything not localhost is refused."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=LOCAL_HOSTS,
        allowed_origins=LOCAL_ORIGINS,
    )


class RequestLog:
    """ASGI middleware: one log line per HTTP request, and an instance header.

    ASGI is the standard way Python web servers (uvicorn) talk to web apps: the app
    is a function called with (scope, receive, send). Middleware wraps the app, sees
    every request on its way in and every response on its way out.
    """

    def __init__(self, app: ASGIApp, instance: str) -> None:
        self.app = app
        self.instance = instance

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        started = time.perf_counter()
        status = 500

        async def send_with_instance(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (INSTANCE_HEADER.encode(), self.instance.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_instance)
        finally:
            log.info(
                "request instance=%s method=%s path=%s mcp_method=%s mcp_name=%s "
                "protocol=%s status=%s ms=%d",
                self.instance,
                scope["method"],
                scope["path"],
                headers.get("mcp-method", "-"),
                headers.get("mcp-name", "-"),
                headers.get("mcp-protocol-version", "-"),
                status,
                (time.perf_counter() - started) * 1000,
            )


def build_http_app(mcp: MCPServer, *, instance: str) -> ASGIApp:
    """Wrap an already-built server as a web app: /mcp and /health, with logging."""
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,  # no sessions: any copy can answer any request
        json_response=True,  # plain JSON replies (no streaming needed: no long calls)
        transport_security=transport_security(),
    )
    return RequestLog(app, instance)


def create_http_app(
    services_factory: ServicesFactory = postgres_services,
    *,
    keys: Sequence[str] | None = None,
    instance: str | None = None,
) -> ASGIApp:
    """The full web app with settings from MERCH_* variables (or the arguments)."""
    settings = get_settings()
    key_list = settings.request_state_key_list() if keys is None else list(keys)
    name = instance or settings.instance_name or f"port-{settings.http_port}"
    mcp = create_server(services_factory, request_state_security=request_state_security(key_list))
    return build_http_app(mcp, instance=name)


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the merch MCP server over HTTP.")
    parser.add_argument("--host", default=settings.http_host)
    parser.add_argument("--port", type=int, default=settings.http_port)
    parser.add_argument("--instance", default=settings.instance_name)
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,  # replace the SDK's default handler, so every line has a timestamp
    )
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        log.warning("Binding to %s: other computers may reach this server.", args.host)
    instance = args.instance or f"port-{args.port}"
    app = create_http_app(instance=instance)
    log.info("serving instance=%s on http://%s:%d%s", instance, args.host, args.port, MCP_PATH)
    # access_log off: RequestLog writes a better line, with the MCP headers.
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

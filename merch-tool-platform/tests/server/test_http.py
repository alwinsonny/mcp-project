"""Phase 6: the server over Streamable HTTP, as one copy and as two behind a balancer.

Most tests start real HTTP servers (uvicorn) on free ports inside this test process,
over the in-memory shop, so they need no database. The last test starts the real
`cluster` command (three processes) against the Postgres test database.

What they prove, in the guide's words:
* the agent's tasks work over HTTP through the load balancer,
* a confirmation started on one copy completes on the other, WITH shared keys,
  and fails without them,
* no request depends on in-process memory (any copy can answer any request).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import httpx2
import pytest
import uvicorn
from cryptography.exceptions import InvalidTag
from mcp import Client, MCPError
from mcp.client.context import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Elicit, ElicitationResult, Resolve
from mcp.server.request_state import InvalidRequestState, RequestStateSecurity
from mcp.types import (
    ElicitRequestParams,
    ElicitResult,
    PromptReference,
    TextContent,
    TextResourceContents,
)
from pydantic import BaseModel
from starlette.types import ASGIApp

from merch_platform.devtools.balancer import create_balancer
from merch_platform.seed.generator import SeedData
from merch_platform.server.app import SERVER_VERSION, create_server
from merch_platform.server.http import build_http_app, request_state_security
from tests.server.conftest import in_memory_factory

pytestmark = pytest.mark.anyio

KEY = "test-key-shared-by-every-copy-0001"
OTHER_KEY = "a-different-key-on-the-other-copy"


# ---------------------------------------------------------------- helpers
@contextmanager
def serving(app: ASGIApp) -> Iterator[str]:
    """Run an ASGI app with uvicorn on a free port, in a background thread."""
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("test server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


class Confirm(BaseModel):
    confirm: bool


def ask_to_apply(change_set_id: str) -> Elicit[Confirm]:
    return Elicit(f"Apply change set {change_set_id}?", Confirm)


def apply_change_set(
    change_set_id: str, answer: Annotated[ElicitationResult[Confirm], Resolve(ask_to_apply)]
) -> str:
    """TEST-ONLY tool that needs a person's confirmation (real ones arrive in Phase 8)."""
    if isinstance(answer, AcceptedElicitation) and answer.data.confirm:
        return f"applied {change_set_id}"
    return f"not applied ({answer.action})"


def http_app(seed_data: SeedData, instance: str, keys: list[str]) -> ASGIApp:
    """One copy of the server, as `create_http_app` builds it, plus the test tool."""
    mcp: MCPServer = create_server(
        in_memory_factory(seed_data), request_state_security=request_state_security(keys)
    )
    mcp.tool()(apply_change_set)
    return build_http_app(mcp, instance=instance)


@contextmanager
def cluster(seed_data: SeedData, keys_a: list[str], keys_b: list[str]) -> Iterator[str]:
    """Copies a and b behind the round-robin balancer. Yields the balancer's /mcp URL."""
    with (
        serving(http_app(seed_data, "a", keys_a)) as a,
        serving(http_app(seed_data, "b", keys_b)) as b,
        serving(create_balancer([a, b])) as balancer,
    ):
        yield f"{balancer}/mcp"


async def approve(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    return ElicitResult(action="accept", content={"confirm": True})


def first_text(result: Any) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


# ---------------------------------------------------------------- one copy
async def test_tools_resources_and_prompts_work_over_http(seed_data: SeedData) -> None:
    with serving(http_app(seed_data, "solo", [KEY])) as base:
        async with Client(f"{base}/mcp") as client:
            assert client.server_info is not None
            assert client.server_info.version == SERVER_VERSION
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"search_products", "explain_price"} <= tools
            result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})
            assert "final price £13.00" in first_text(result)
            taxonomy = (await client.read_resource("catalog://taxonomy")).contents[0]
            assert isinstance(taxonomy, TextResourceContents) and "Bath towels" in taxonomy.text
            prompt = await client.get_prompt("weekly_margin_review", {"category_id": "C-110"})
            assert "C-110 (Bath towels)" in str(prompt.messages[0].content)


async def test_health_reports_ok_and_reads_the_database(seed_data: SeedData) -> None:
    with serving(http_app(seed_data, "solo", [KEY])) as base:
        response = httpx2.get(f"{base}/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "server": f"merch-tool-platform {SERVER_VERSION}",
        "top_level_categories": 6,
    }
    assert response.headers["x-merch-instance"] == "solo"


async def test_health_is_503_when_the_shop_cannot_be_read(
    seed_data: SeedData, monkeypatch: pytest.MonkeyPatch
) -> None:
    from merch_platform.services.catalogue import CatalogueService

    async def broken(self: CatalogueService) -> Any:
        raise ConnectionError("database is down")

    monkeypatch.setattr(CatalogueService, "taxonomy", broken)
    with serving(http_app(seed_data, "solo", [KEY])) as base:
        response = httpx2.get(f"{base}/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "ConnectionError"}


@pytest.mark.parametrize(
    ("header", "value"),
    [("Origin", "http://evil.example"), ("Host", "evil.example:8000")],
    ids=["foreign origin (DNS rebinding)", "foreign host"],
)
async def test_requests_from_outside_localhost_are_refused(
    seed_data: SeedData, header: str, value: str
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with serving(http_app(seed_data, "solo", [KEY])) as base:
        response = httpx2.post(
            f"{base}/mcp",
            json=body,
            headers={header: value, "Accept": "application/json, text/event-stream"},
        )
    assert response.status_code in {403, 421}


async def test_every_request_is_logged_with_mcp_routing_headers(
    seed_data: SeedData, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="merch_platform.http")
    with serving(http_app(seed_data, "solo", [KEY])) as base:
        async with Client(f"{base}/mcp") as client:
            await client.call_tool("get_product", {"sku": "TRAP-RULES-001"})
    lines = [r.getMessage() for r in caplog.records if r.name == "merch_platform.http"]
    call = next(line for line in lines if "mcp_method=tools/call" in line)
    assert "instance=solo" in call and "mcp_name=get_product" in call
    assert "protocol=2026-07-28" in call and "status=200" in call


# ---------------------------------------------------------------- two copies
async def test_the_balancer_alternates_between_copies(seed_data: SeedData) -> None:
    with cluster(seed_data, [KEY], [KEY]) as url:
        base = url.removesuffix("/mcp")
        seen = [httpx2.get(f"{base}/health").headers["x-merch-instance"] for _ in range(4)]
    assert sorted(seen) == ["a", "a", "b", "b"]
    assert seen[0] != seen[1] and seen[1] != seen[2]


async def test_answers_through_the_balancer_match_a_single_copy(seed_data: SeedData) -> None:
    """Statelessness: a conversation bounced between copies gets the same answers."""

    async def conversation(client: Client) -> list[str]:
        out = [first_text(await client.call_tool("search_products", {"category_id": "C-110"}))]
        out.append(first_text(await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})))
        out.append(first_text(await client.call_tool("get_product", {"sku": "NOPE-0001"})))
        resource = (await client.read_resource("pricing://rules/active")).contents[0]
        assert isinstance(resource, TextResourceContents)
        out.append(resource.text)
        ref = PromptReference(type="ref/prompt", name="clearance_plan")
        done = await client.complete(ref, {"name": "category_id", "value": "tow"})
        out.append(",".join(done.completion.values))
        return out

    async with Client(create_server(in_memory_factory(seed_data))) as single:
        expected = await conversation(single)
    with cluster(seed_data, [KEY], [KEY]) as url:
        async with Client(url) as bounced:
            assert await conversation(bounced) == expected


async def test_confirmation_started_on_one_copy_finishes_on_the_other(
    seed_data: SeedData, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="merch_platform.http")
    with cluster(seed_data, [KEY], [KEY]) as url:
        async with Client(url, elicitation_callback=approve) as client:
            caplog.clear()
            result = await client.call_tool("apply_change_set", {"change_set_id": "CS-1"})
    assert first_text(result) == "applied CS-1"
    calls = [
        r.getMessage()
        for r in caplog.records
        if r.name == "merch_platform.http" and "mcp_name=apply_change_set" in r.getMessage()
    ]
    # First attempt -> "input required" on one copy; the retry with the answer -> the other.
    assert len(calls) == 2
    assert {"instance=a" in c for c in calls} == {True, False}


@pytest.mark.parametrize(
    ("keys_a", "keys_b"),
    [([KEY], [OTHER_KEY]), ([], [])],
    ids=["different keys", "no keys (a random key per process)"],
)
async def test_confirmation_fails_across_copies_without_shared_keys(
    seed_data: SeedData, keys_a: list[str], keys_b: list[str]
) -> None:
    with cluster(seed_data, keys_a, keys_b) as url:
        async with Client(url, elicitation_callback=approve) as client:
            with pytest.raises(MCPError, match="Invalid or expired requestState"):
                await client.call_tool("apply_change_set", {"change_set_id": "CS-1"})


def test_shared_keys_can_unseal_each_others_state() -> None:
    """The mechanism under the two tests above, without any HTTP."""
    sealed = request_state_security([KEY]).codec.seal(b"resume here")
    assert request_state_security([KEY]).codec.unseal(sealed) == b"resume here"
    with pytest.raises((InvalidRequestState, InvalidTag)):
        request_state_security([OTHER_KEY]).codec.unseal(sealed)
    # Rotation: a copy holding [new, old] still unseals what the old key sealed.
    assert request_state_security([OTHER_KEY, KEY]).codec.unseal(sealed) == b"resume here"


def test_missing_keys_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="merch_platform.http")
    assert isinstance(request_state_security([]), RequestStateSecurity)
    assert "MERCH_REQUEST_STATE_KEYS is empty" in caplog.text


# ---------------------------------------------------------------- the real thing
def start_cluster(database_url: str, ports: tuple[int, int, int]) -> subprocess.Popen[bytes]:
    env = {**os.environ, "MERCH_DATABASE_URL": database_url, "MERCH_REQUEST_STATE_KEYS": KEY}
    command = [sys.executable, "-m", "merch_platform.devtools.cluster", "--port", str(ports[2])]
    command += ["--backend-ports", f"{ports[0]},{ports[1]}"]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 60
    while True:
        assert process.poll() is None, process.stdout.read().decode() if process.stdout else ""
        try:
            if httpx2.get(f"http://127.0.0.1:{ports[2]}/health", timeout=1).status_code == 200:
                return process
        except httpx2.TransportError:
            pass
        assert time.monotonic() < deadline, "cluster did not start"
        time.sleep(0.3)


def assert_all_stopped(process: subprocess.Popen[bytes], ports: tuple[int, ...]) -> None:
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    for port in ports:  # every process stopped and released its port
        with pytest.raises(httpx2.TransportError):
            httpx2.get(f"http://127.0.0.1:{port}/health", timeout=1)


@pytest.mark.integration
async def test_cluster_command_end_to_end_against_postgres(seeded_test_db: str) -> None:
    """`make cluster`: three real processes, the Postgres store, round-robin, Ctrl+C."""
    ports = (18001, 18002, 18080)
    process = start_cluster(seeded_test_db, ports)
    base = f"http://127.0.0.1:{ports[2]}"
    try:
        seen = {httpx2.get(f"{base}/health").headers["x-merch-instance"] for _ in range(4)}
        assert seen == {"a", "b"}
        async with Client(f"{base}/mcp") as client:
            for _ in range(2):  # once on each copy
                result = await client.call_tool("explain_price", {"sku": "TRAP-RULES-001"})
                assert "final price £13.00" in first_text(result)
    finally:
        process.send_signal(signal.SIGINT)  # what Ctrl+C sends
    assert_all_stopped(process, ports)


@pytest.mark.integration
def test_terminating_the_cluster_leaves_no_orphans(seeded_test_db: str) -> None:
    """SIGTERM (`kill`, or a closed terminal) must stop the copies too, not only Ctrl+C."""
    ports = (18003, 18004, 18081)
    process = start_cluster(seeded_test_db, ports)
    process.terminate()  # SIGTERM to the launcher only
    assert_all_stopped(process, ports)

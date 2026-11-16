# ADR-005: Serve over stateless Streamable HTTP, with shared request-state keys

Status: accepted (Phase 6)

## Context

Over stdio the client starts the server as its own child process: one client, one
server, one computer. That cannot be shared by several agents, protected by a login
(Phase 7) or deployed. HTTP can.

The 2026-07-28 MCP spec makes the protocol stateless: each request carries everything
needed to handle it. A multi-step exchange (a confirmation) carries its progress in a
`requestState` value that the server seals and the client sends back.

## Decision

1. Serve `/mcp` with `stateless_http=True` (no sessions) and `json_response=True`
   (plain JSON replies; no call is long enough to need streaming yet).
2. No request handler may keep anything in process memory between requests. The
   services object made at start-up (a database connection pool) is wiring, not
   per-client state. Anything that must outlive a request goes in Postgres
   (Phase 8: change sets and idempotency keys).
3. Every copy of the server loads the same `MERCH_REQUEST_STATE_KEYS`, from the
   environment, never from code. The first key seals; all keys unseal, so keys can
   be rotated without downtime. The cluster launcher refuses to start without keys.
4. Always check Host and Origin (DNS-rebinding protection) and bind to 127.0.0.1 by
   default. Binding elsewhere logs a warning, and non-local Host headers are still
   refused until deployment (Phase 11) configures real host names.
5. Log one line per request with the MCP routing headers (`Mcp-Method`, `Mcp-Name`,
   protocol version) and the instance name; Phase 8's rate limits will key on them.
6. Without Docker, the local "deployment" is three processes: two copies and a small
   round-robin balancer (`merch_platform.devtools`), started by `make cluster`.

## Consequences

- Any copy can answer any request, so scaling is "run another copy".
  `test_answers_through_the_balancer_match_a_single_copy` checks this.
- A confirmation started on one copy finishes on another only with shared keys:
  `test_confirmation_started_on_one_copy_finishes_on_the_other` passes, and with
  different or missing keys the client gets `Invalid or expired requestState`.
- The balancer buffers whole requests, which is fine for JSON replies but not for
  streaming. A real deployment uses a real load balancer.
- Claude Desktop keeps using stdio; HTTP is for agents and, later, remote hosts.
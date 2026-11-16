# ADR-003: How tool contracts are versioned and changed

- Status: accepted
- Date: 2026-11-15

## Context
An MCP tool's contract is its name, description, input schema, output schema and
annotations. Agents depend on all of it: a model chooses tools from the description alone,
so rewording a description changes agent behaviour as much as changing code does. Several
agents (Project 01 now, Project 05's workforce later) will share this server, so a change
that suits one can silently break another.

## Decision
1. Every tool publishes `contract_version` (and its `risk_class`) in its `_meta`.
2. The full `tools/list` output is pinned in `tests/server/snapshots/tool_catalogue.json`.
   Any change fails CI until someone runs `make snapshot` and the diff is reviewed in the
   pull request. Descriptions are reviewed like code, never edited as "just a typo fix".
3. **Compatible changes keep the version**: clearer wording, a new OPTIONAL argument with a
   safe default, a new output field. They still go through the snapshot review.
4. **Breaking changes create a new tool** named `<tool>_v2` with `contract_version` "2",
   published alongside v1. Breaking means: renaming or removing an argument, making an
   argument required, changing a type, removing an output field, or changing meaning.
5. During the deprecation window v1's description starts with
   "Deprecated: use <tool>_v2 instead (removed after <date>)." The window is 90 days for
   this project (the MCP specification itself uses a 12-month window for its own features).
6. From Phase 9 every audit record stores the tool's `contract_version`, so usage of v1 can
   be measured before it is removed.

## Consequences
- No agent breaks without warning; every behavioural change leaves a reviewed diff.
- Two versions briefly cost extra tokens in `tools/list`. Acceptable for a short window.
- The snapshot test also defends against tool poisoning (OWASP MCP03): an unreviewed change
  to a description cannot ship.
# ADR-004: What becomes a tool, a resource or a prompt

Status: accepted (Phase 4)

## Context

MCP offers three ways to give a model something. They differ in **who decides to use it**:

| Primitive | Who is in control | Typical use |
| --- | --- | --- |
| Tool | the model, mid-conversation | an action or a query with arguments the model chooses |
| Resource | the application or the user | reference data loaded into context, often once |
| Prompt | the user, by picking it | a reusable workflow that starts a conversation |

Without a rule, everything tends to become a tool, which makes the tool list long (it is
sent with every model request) and makes the model spend calls fetching data that never
changes during a conversation.

## Decision

1. **Read-only data that is the same for the whole conversation and small enough to load
   whole is a resource.** The category tree, the rules in force today and a month's
   promotions qualify. A product search does not: the model picks the arguments.
2. **Anything with arguments the model must choose stays a tool.**
3. **A method a person repeats (a review, a plan, a check) is a prompt.** Prompts carry the
   method, the formulas and the output format, so every user gets the same careful work.
4. **Facts that code can compute exactly are computed by the server and embedded in the
   prompt**, not left to the model (promo_conflict_check embeds the overlaps). The model is
   given judgement, not arithmetic over data it cannot see.
5. Resources and prompts follow the tool conventions: contract version and risk class in
   `_meta`, coded errors with hints, private cache hints, and a pinned snapshot.

## Consequences

- The tool list stays at four read tools; reference data costs nothing until a host loads it.
- Not every host shows resources or prompts (some only use tools), so the tools must still
  work without them: category IDs come back in `CATEGORY_NOT_FOUND` details, and the rules
  that affect a SKU show in explain_price. The promotion calendar is the one gap: no tool
  lists promotions until Phase 8, so for now it reaches the model only as a resource or
  embedded in `promo_conflict_check`.
- Prompt text is part of the contract: a reworded prompt changes behaviour, so it is
  snapshot-tested like a tool description.
- Phase 5 measures the saving: the same task with and without the resources attached.
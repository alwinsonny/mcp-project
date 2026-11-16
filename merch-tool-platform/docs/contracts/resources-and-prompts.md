# Resources and prompts (Phase 4)

Contract version 1, risk class `read` for everything here. Nothing in this file changes data.
The exact names, descriptions and arguments are pinned in
`tests/server/snapshots/reference_catalogue.json`; that snapshot is the source of truth.

## Resources

A resource is read-only data with an address (a URI). The **application or user** decides
to load it, usually once, at the start of a conversation. Resource content is compact JSON
(no spaces or indentation) because it goes straight into the model's context.

| URI | What it holds | Size (test seed) | Service method |
| --- | --- | --- | --- |
| `catalog://taxonomy` | Every category ID and name, as a tree | ~1.2k chars | `CatalogueService.taxonomy` |
| `pricing://rules/active` | Price rules active today, in evaluation order: id, name, priority, what it does, which products, stop flag, end date | ~1.8k chars | `PricingService.rules_in_force` |
| `promotions://calendar/{month}` | TEMPLATE. Promotions running at any point in a month (`YYYY-MM`) | ~0.2-0.4k chars | `PromotionService.calendar` |

Errors (resources have no "error result", so these are protocol errors, code -32602):

| Code | When | Text the client sees |
| --- | --- | --- |
| `INVALID_MONTH` | month is not `YYYY-MM` with month 01-12 | `INVALID_MONTH: '2026-13' is not a month.` / `Hint: Use YYYY-MM, for example promotions://calendar/2026-12.` |
| (SDK) | unknown URI | `Unknown resource: ...` |

Cache hints: `resources/list` and `resources/templates/list` 5 minutes, `resources/read`
1 minute (prices and promotions change), all `private`.

## Prompts

A prompt is a reusable workflow the **user** picks, like a slash command. It runs nothing:
it returns one user message with careful instructions, and the model then uses the tools.
All arguments arrive as strings (an MCP rule) and are parsed and range-checked by the server.

| Prompt | Arguments (required in bold) | What the message tells the model |
| --- | --- | --- |
| `weekly_margin_review` | **category_id**, floor_pct (0-90, default 15) | search at or below the floor, check real prices with explain_price, recommend `cost / (1 - floor/100)` rounded up, reply as a table |
| `clearance_plan` | **category_id**, min_available (default 100), max_discount_pct (1-90, default 30) | find stock with get_inventory_position, recommend a discount no larger than the cap or the below-cost limit, reply as a table |
| `promo_conflict_check` | **category_id**, **starts**, **ends** (YYYY-MM-DD), exclusive (true/false, default false) | the server computes which promotions overlap and how many SKUs each shares, embeds those facts, and asks the model to judge conflicts and suggest dates |

Every prompt ends with the same notice: recommend only, change nothing.

Errors (protocol errors, code -32602, same `CODE: message / Hint: / Details:` format as tools):

| Code | When |
| --- | --- |
| `CATEGORY_NOT_FOUND` | category_id is not a known category; Details lists every valid ID and name |
| `INVALID_ARGUMENTS` | a number, whole number, date or true/false argument cannot be read or is out of range; the message names the argument |
| `INVALID_DATE_RANGE` | `ends` is before `starts` |

## Completions

As the user types an argument, the host may ask for suggestions:

| Where | Argument | Suggestions |
| --- | --- | --- |
| any prompt | `category_id` | category IDs whose ID starts with, or whose name contains, what was typed (`tow` -> C-110, C-140, C-630) |
| `promotions://calendar/{month}` | `month` | months from 3 months ago to 12 months ahead that start with what was typed |

Other arguments get no suggestions.
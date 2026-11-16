"""Phase 3: how every tool result and error is shaped.

A tool result has two audiences, so it carries two copies of the answer:

* `content`            - short readable text. This is what the MODEL reads, and every
                          character costs tokens and context, so it is a compact summary.
* `structured_content` - the complete typed data, matching the tool's output schema.
                          Programs, tests and later phases read this, never the prose.

Errors are returned (not raised) so that we control their exact text. The text always
starts with a stable CODE, then a line saying what would work instead:

    SKU_NOT_FOUND: SKU NOPE-001 not found.
    Hint: Use search_products to find valid SKUs.

The code is also put in the result's `_meta`, so programs can read it without parsing.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent
from pydantic import BaseModel, ValidationError

from merch_platform.domain.errors import DomainError

ERROR_CODE_META_KEY = "merch/error_code"


def error_text(
    code: str, message: str, hint: str = "", details: dict[str, Any] | None = None
) -> str:
    """One format for every error in the server: CODE: message / Hint: ... / Details: {...}"""
    lines = [f"{code}: {message}"]
    if hint:
        lines.append(f"Hint: {hint}")
    if details:
        lines.append(f"Details: {json.dumps(details, separators=(',', ':'), default=str)}")
    return "\n".join(lines)


def protocol_error(
    code: str, message: str, hint: str = "", details: dict[str, Any] | None = None
) -> MCPError:
    """For RESOURCES and PROMPTS, which have no is_error result: a protocol error.

    Any other exception raised in a resource or prompt reaches the client only as
    "Internal server error", so bad input must be reported with MCPError (INVALID_PARAMS)
    for the message to get through.
    """
    return MCPError(INVALID_PARAMS, error_text(code, message, hint, details))


def error_result(
    code: str, message: str, hint: str = "", details: dict[str, Any] | None = None
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=error_text(code, message, hint, details))],
        is_error=True,
        meta={ERROR_CODE_META_KEY: code},
    )


async def respond[M: BaseModel](
    call: Callable[[], Awaitable[M]], summarise: Callable[[M], str]
) -> CallToolResult:
    """Run a service call and turn its outcome into a tool result.

    Success: summary text for the model + full structured data for programs.
    Business error (DomainError): an error result with the code, message and hint.
    Rejected request (ValidationError raised while building a request model): an
    INVALID_ARGUMENTS error naming every problem.
    """
    try:
        result = await call()
    except DomainError as err:
        return error_result(err.code, err.message, err.hint, err.details)
    except ValidationError as err:
        problems = "; ".join(_describe(e) for e in err.errors())
        return error_result(
            "INVALID_ARGUMENTS", problems, "Correct the listed arguments and call the tool again."
        )
    return CallToolResult(
        content=[TextContent(type="text", text=summarise(result))],
        structured_content=result.model_dump(mode="json"),
    )


def _describe(error: Any) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
    message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message

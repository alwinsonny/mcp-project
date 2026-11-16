"""Phase 3 + 4: the whole catalogue is pinned. Nothing the model reads can change silently.

The model chooses tools from their names, descriptions and schemas alone, so changing a
description changes behaviour as much as changing code does. Resource descriptions and
prompt arguments are read the same way. These tests save the catalogue to two files and
fail on ANY difference, printing the exact lines that changed:

* snapshots/tool_catalogue.json        tools/list                         (Phase 3)
* snapshots/reference_catalogue.json   resources, templates and prompts    (Phase 4)

When a change is intentional:  make snapshot   (then review the diff in git, and commit).
This is also a defence against "tool poisoning": an unreviewed edit to a description
cannot reach users without showing up here.
"""

from __future__ import annotations

import difflib
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from pydantic import BaseModel

pytestmark = pytest.mark.anyio

SNAPSHOTS = Path(__file__).parent / "snapshots"


def dump(items: Sequence[BaseModel], key: str) -> list[dict[str, Any]]:
    dumped = [i.model_dump(mode="json", by_alias=True, exclude_none=True) for i in items]
    return sorted(dumped, key=lambda d: str(d[key]))


async def tool_catalogue(client: Client) -> Any:
    return dump((await client.list_tools()).tools, "name")


async def reference_catalogue(client: Client) -> Any:
    return {
        "resources": dump((await client.list_resources()).resources, "uri"),
        "resource_templates": dump(
            (await client.list_resource_templates()).resource_templates, "uriTemplate"
        ),
        "prompts": dump((await client.list_prompts()).prompts, "name"),
    }


CATALOGUES: dict[str, Callable[[Client], Awaitable[Any]]] = {
    "tool_catalogue.json": tool_catalogue,
    "reference_catalogue.json": reference_catalogue,
}


@pytest.mark.parametrize("filename", sorted(CATALOGUES))
async def test_catalogue_matches_snapshot(client: Client, filename: str) -> None:
    snapshot = SNAPSHOTS / filename
    current = json.dumps(await CATALOGUES[filename](client), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOT") == "1":
        snapshot.write_text(current)
        pytest.skip(f"snapshot rewritten: {snapshot}")
    assert snapshot.exists(), f"No {filename} yet: run `make snapshot` and commit the file."
    saved = snapshot.read_text()
    if current != saved:
        diff = "".join(
            difflib.unified_diff(
                saved.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile="snapshot (approved)",
                tofile="server (now)",
            )
        )
        pytest.fail(
            f"The catalogue in {filename} changed. If intended, run `make snapshot`, "
            "review and commit.\n" + diff
        )

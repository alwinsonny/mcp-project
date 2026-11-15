"""Run the MCP server over stdio:  python -m merch_platform.server

stdio rule: standard OUTPUT carries the protocol messages, so nothing else may ever
print to it. A stray print() corrupts the stream and the client disconnects. All
logging therefore goes to standard ERROR, which the client shows as server logs.
"""

from __future__ import annotations

import logging
import sys

from merch_platform.server.app import mcp


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

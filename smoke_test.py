#!/usr/bin/env python3
"""Smoke test: prove mcp_server loads and its 3 tools list + execute.

Two layers:
  1. In-process: import the server, list tools, assert the name SET is exactly
     {show_defun, ts_refactor, sx_mine}. (Set, not count -- a count passes with
     wrong names.)
  2. Stdio round-trip: spawn `python mcp_server.py` via the SDK's own client,
     initialize, list tools over the wire, and CALL show_defun once against the
     in-repo fixture. This is the layer that actually exercises the transport
     and the __file__-relative bin resolution; the in-process one cannot.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "tests" / "fixture.py"
EXPECTED = {"show_defun", "ts_refactor", "sx_mine"}


async def layer1_in_process() -> set[str]:
    import mcp_server  # noqa: F401  -- proves it imports/loads

    tools = await mcp_server.server.list_tools()
    names = {t.name for t in tools}
    print(f"[layer1] in-process list_tools -> {sorted(names)}")
    assert names == EXPECTED, f"tool name set mismatch: {names} != {EXPECTED}"
    return names


async def layer2_stdio_roundtrip() -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "mcp_server.py")],
        cwd=str(Path("/")),  # arbitrary cwd: proves bin resolves off __file__, not cwd
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            resp = await session.list_tools()
            names = {t.name for t in resp.tools}
            print(f"[layer2] stdio list_tools   -> {sorted(names)}")
            assert names == EXPECTED, f"stdio tool set mismatch: {names} != {EXPECTED}"

            # Execute one tool end-to-end through the real shell-out path.
            call = await session.call_tool(
                "show_defun", {"file": str(FIXTURE), "name": "Box.collect"}
            )
            out = "".join(
                getattr(c, "text", "") for c in call.content if getattr(c, "text", None)
            )
            print(f"[layer2] call show_defun(Box.collect) ->\n{out.rstrip()}")
            assert "def collect" in out, f"show_defun did not return the defun: {out!r}"
            return out


async def main() -> int:
    await layer1_in_process()
    await layer2_stdio_roundtrip()
    print("PASS: mcp_server loaded; 3 tools {show_defun, ts_refactor, sx_mine} "
          "listed in-process AND over stdio; show_defun executed end-to-end.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    raise SystemExit(asyncio.run(main()))

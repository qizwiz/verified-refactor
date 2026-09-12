#!/usr/bin/env python3
"""Stdio MCP server exposing the verified-refactor CLIs as three tools.

Each tool shells to a standalone CLI in ./bin. The bin dir is resolved relative
to THIS file, not the cwd: an MCP client spawns the server from an arbitrary
working directory, so a literal "bin/ts-edit" would FileNotFoundError the moment
anything but a same-dir smoke test launched it.

Shell-outs pass argv as a LIST (never shell=True): test_cmd must reach
ts-refactor's --test as ONE argument, and sx-mine's before/after are code that
carries quotes and newlines -- a list preserves them verbatim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BIN = Path(__file__).resolve().parent / "bin"

# Named `server` (not `mcp`) so it never shadows the `mcp` package -- a foot-gun
# on the `import mcp_server` smoke path.
server = FastMCP("verified-refactor")


def _run(argv: list[str], timeout: int) -> str:
    """Run argv as a subprocess list and return a text result.

    On success returns stdout. On a non-zero exit, returns the exit code plus
    stderr/stdout so the caller sees WHY it failed rather than an empty string.
    A timeout is reported explicitly -- hanging the server is the failure mode
    to avoid.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        return f"ERROR: command not found: {argv[0]} ({e})"
    except subprocess.TimeoutExpired:
        return f"ERROR: timed out after {timeout}s running {argv[0]}"
    if proc.returncode != 0:
        return (
            f"ERROR: {argv[0]} exited {proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}\n"
            f"--- stdout ---\n{proc.stdout}"
        )
    return proc.stdout


@server.tool()
def show_defun(file: str, name: str) -> str:
    """Show the source of one definition, addressed by NAME (byte-precise).

    Shells to `bin/ts-edit --defun FILE NAME`. NAME is a definition name such as
    `add` or `Box.collect`; the CLI finds it via tree-sitter and returns exactly
    that definition's bytes (comments and formatting preserved). Read-only.
    """
    return _run([str(BIN / "ts-edit"), "--defun", file, name], timeout=30)


@server.tool()
def ts_refactor(file: str, test_cmd: str) -> str:
    """Apply VERIFIED behavior-preserving refactorings to FILE, in place.

    Shells to `bin/ts-refactor FILE --test TEST_CMD`. Structure proposes each
    candidate refactoring; TEST_CMD is the SOUND ORACLE that disposes -- it must
    PASS on the unmodified file, and every accepted candidate must keep it
    passing. WARNING: this modifies FILE on disk (no dry-run in this form).
    Returns the JSON event log (applied/rejected/saved).
    """
    # Generous timeout: the oracle runs once per candidate.
    return _run([str(BIN / "ts-refactor"), file, "--test", test_cmd], timeout=300)


@server.tool()
def sx_mine(before: str, after: str, lang: str) -> str:
    """Mine a structural rewrite PATTERN from a BEFORE/AFTER code-edit pair.

    Shells to `bin/sx-mine BEFORE AFTER --lang LANG`. BEFORE and AFTER are code
    snippets (they may contain quotes and newlines -- passed verbatim). LANG is
    a tree-sitter language name (e.g. python, go, bash). Returns the mined
    pattern/query/template as JSON.
    """
    return _run([str(BIN / "sx-mine"), before, after, "--lang", lang], timeout=30)


if __name__ == "__main__":
    # Live stdio MCP server.
    server.run()

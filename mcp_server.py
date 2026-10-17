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

import importlib.util
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

BIN = Path(__file__).resolve().parent / "bin"

# The repo's declared oracle (.oracle-gate.json) is resolved with the SAME code the PreToolUse hook
# uses, so the hook's baseline check and the tool's per-candidate grading can never disagree.
_spec = importlib.util.spec_from_file_location(
    "oracle_gate", Path(__file__).resolve().parent / "hooks" / "oracle-gate.py"
)
oracle_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle_gate)

# After a timeout the child gets SIGTERM first and this long to restore the file before SIGKILL:
# ts-refactor writes each candidate to disk to grade it, and SIGKILL would strand it there.
_TERM_GRACE = 10

# Named `server` (not `mcp`) so it never shadows the `mcp` package -- a foot-gun
# on the `import mcp_server` smoke path.
server = FastMCP("verified-refactor")


def _run(argv: list[str], timeout: int, cwd: str | None = None) -> str:
    """Run argv as a subprocess list and return a text result.

    On success returns stdout. On a non-zero exit, returns the exit code plus
    stderr/stdout so the caller sees WHY it failed rather than an empty string.
    A timeout is reported explicitly -- hanging the server is the failure mode
    to avoid.
    """
    try:
        # stdin is the JSON-RPC stream: a child (or a test it runs) must never read from it.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        return f"ERROR: command not found: {argv[0]} ({e})"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=_TERM_GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        return (
            f"ERROR: timed out after {timeout}s running {argv[0]}\n"
            f"--- stdout ---\n{stdout}"
        )
    if proc.returncode != 0:
        return (
            f"ERROR: {argv[0]} exited {proc.returncode}\n"
            f"--- stderr ---\n{stderr}\n"
            f"--- stdout ---\n{stdout}"
        )
    return stdout


@server.tool()
def show_defun(file: str, name: str) -> str:
    """Show the source of one definition, addressed by NAME (byte-precise).

    Shells to `bin/ts-edit --defun FILE NAME`. NAME is a definition name such as
    `add` or `Box.collect`; the CLI finds it via tree-sitter and returns exactly
    that definition's bytes (comments and formatting preserved). Read-only.
    """
    return _run([str(BIN / "ts-edit"), "--defun", file, name], timeout=30)


@server.tool()
def ts_refactor(file: str, test_cmd: str = "") -> str:
    """Apply VERIFIED behavior-preserving refactorings to FILE, in place.

    Shells to `bin/ts-refactor FILE --test TEST_CMD`. Structure proposes each
    candidate refactoring; the test command is the SOUND ORACLE that disposes --
    it must PASS on the unmodified file, and every accepted candidate must keep
    it passing. WARNING: this modifies FILE on disk (no dry-run in this form).
    Returns the JSON event log (applied/rejected/saved).

    If a `.oracle-gate.json` governs FILE, ITS test_cmd is the oracle and runs in
    that repo's directory; TEST_CMD may be omitted, and any other value is
    refused (the caller must not be able to swap in a weaker oracle). Without
    one, TEST_CMD is required and runs in the server's working directory.
    """
    target = Path(file).resolve()
    try:
        resolved = oracle_gate.find_oracle(str(target.parent))
    except ValueError as e:
        return f"ERROR: the repo's verification oracle is misconfigured -- {e}"
    timeout = oracle_gate.DEFAULT_TIMEOUT
    if resolved:
        repo_dir, oracle_cmd, timeout = resolved
        if test_cmd.strip() and test_cmd.strip() != oracle_cmd:
            return (
                f"ERROR: refusing test_cmd {test_cmd!r}: {file} is governed by "
                f"{Path(repo_dir) / oracle_gate.ORACLE_FILE}, whose oracle is {oracle_cmd!r}. "
                "Omit test_cmd (or pass exactly that) -- a caller cannot choose its own oracle."
            )
        test_cmd, cwd = oracle_cmd, repo_dir
    elif test_cmd.strip():
        cwd = None  # caller's own oracle: keep the server's cwd, as the documented usage assumes
    else:
        return f"ERROR: no {oracle_gate.ORACLE_FILE} governs {file}; test_cmd is required."
    argv = [str(BIN / "ts-refactor"), str(target), "--test", test_cmd, "--timeout", str(timeout)]
    # Generous timeout: the oracle runs once per candidate.
    return _run(argv, timeout=300, cwd=cwd)


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

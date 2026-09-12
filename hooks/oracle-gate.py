#!/usr/bin/env python3
"""oracle-gate.py -- the VERIFICATION MOAT as a Claude Code PreToolUse hook.

STRUCTURE proposes, the ORACLE disposes -- at the HARNESS level. `ts-refactor` already gates each
rewrite on its own test oracle; this hook is the INDEPENDENT moat one layer up: before Claude Code
is permitted to run a mutating `ts_refactor` MCP tool call, the TARGET REPO's declared test command
must currently PASS. A red tree is an untrustworthy oracle -- you cannot attribute a later failure
to the refactor if the suite was already broken -- so the gate BLOCKS the mutation and names the
fix. Read-only calls (`show_defun`) are never gated and never trigger a test run.

  SEMANTICS (stated plainly, not overclaimed):
    This is a PreToolUse gate, so it runs BEFORE the edit. It enforces a GREEN-BASELINE
    precondition (the oracle is trustworthy == passing) -- the same `pristine` control ts-refactor
    runs internally. Verifying the AFTER state of the refactor is the tool's own job (it applies +
    tests + reverts per candidate) or a PostToolUse companion's; this hook does not claim to do it.

  THE ORACLE IS UN-FAKEABLE BY CONSTRUCTION:
    The test command is read from the REPO (`.oracle-gate.json` at / above the target file's dir),
    NEVER from `tool_input`. tool_input is authored by the same model that wants to refactor; a
    caller-supplied `test_cmd: "true"` would make the moat theater (the self-grant shape). If a
    caller DOES pass an oracle-shaped key, it is IGNORED and the refusal says so.

  PROTOCOL (Claude Code PreToolUse):
    stdin  : one JSON object -- {session_id, cwd, hook_event_name, tool_name, tool_input, ...}
    stdout : EXACTLY one JSON object, or nothing.
             block  -> {"decision": "block",   "reason": "..."}   (shown to Claude; tool not run)
             allow  -> {"decision": "approve", "reason": "..."}   (verified pass) OR empty (reads)
    exit   : 0 (the decision rides stdout; we never use exit-2 blocking here)

  FAIL-CLOSED on the refactor path: no resolvable oracle, a misconfigured oracle, a timeout, or an
  internal exception all BLOCK -- a moat that cannot evaluate must not wave an unverified mutation
  through. Every refusal names WHY and HOW to fix it, so a block is actionable, not a mystery wall.
  Reads and unrecognized tools are always ALLOWED (the gate only asserts on what it understands).

  SELF-TEST (proves discrimination BOTH directions, driving the hook as a real subprocess):
    python3 oracle-gate.py --selftest
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ORACLE_FILE = ".oracle-gate.json"
# Keys a caller might try to smuggle an oracle through. We read NONE of them for the decision;
# we only detect their presence to say "ignored" in the reason.
_CALLER_ORACLE_KEYS = (
    "test_cmd",
    "test",
    "oracle",
    "testCommand",
    "test_command",
    "cmd",
)
# tool_input keys that may name the file being refactored.
_FILE_KEYS = ("file", "path", "FILE", "filename", "file_path", "target", "src")

DEFAULT_TIMEOUT = (
    120  # seconds for the test command itself; keep below the hooks.json timeout.
)
MAX_TIMEOUT = 280
_OUTPUT_TAIL = 1500  # chars of captured test output carried into a block reason.
_WALK_CAP = 40  # directory levels to walk up looking for the oracle file.


# ---------------------------------------------------------------------------- classification


def classify_tool(name):
    """'refactor' (mutating, gate it) | 'read' (allow) | 'other' (allow). Coarse, by tool name.

    The hooks.json matcher is the first filter; this is the real decision and must stand alone, so
    it recognizes the tool whether the name is bare (`ts_refactor`) or MCP-qualified
    (`mcp__srv__ts_refactor`)."""
    n = (name or "").lower()
    if "show_defun" in n or "showdefun" in n:
        return "read"
    if "ts_refactor" in n or "refactor" in n:
        return "refactor"
    return "other"


def find_target_file(tool_input):
    if not isinstance(tool_input, dict):
        return None
    for k in _FILE_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def caller_supplied_oracle(tool_input):
    if not isinstance(tool_input, dict):
        return None
    for k in _CALLER_ORACLE_KEYS:
        if k in tool_input:
            return k
    return None


# ---------------------------------------------------------------------------- oracle resolution


def find_oracle(start_dir):
    """Walk UP from start_dir looking for ORACLE_FILE. Returns (repo_dir, test_cmd, timeout) or
    raises ValueError('<human reason>') if a file is found but malformed, or returns None if no
    oracle file exists anywhere up the tree (the caller decides fail-closed for that).
    """
    d = os.path.abspath(start_dir)
    seen = 0
    while seen < _WALK_CAP:
        cand = os.path.join(d, ORACLE_FILE)
        if os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (ValueError, OSError) as e:
                raise ValueError(f"{cand} is not valid JSON: {e}") from e
            if not isinstance(cfg, dict):
                raise ValueError(f"{cand} must be a JSON object")
            tc = cfg.get("test_cmd")
            if not isinstance(tc, str) or not tc.strip():
                raise ValueError(f'{cand} must set a non-empty string "test_cmd"')
            to = cfg.get("timeout", DEFAULT_TIMEOUT)
            try:
                to = max(1, min(MAX_TIMEOUT, int(to)))
            except (ValueError, TypeError):
                to = DEFAULT_TIMEOUT
            return (d, tc.strip(), to)
        parent = os.path.dirname(d)
        if parent == d:  # filesystem root
            break
        d = parent
        seen += 1
    return None


def run_oracle(test_cmd, cwd, timeout):
    """Run the repo's test command. Returns (ok: bool, rc: int|None, tail: str)."""
    try:
        r = subprocess.run(
            test_cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (False, None, f"[oracle TIMED OUT after {timeout}s]")
    except OSError as e:  # could not launch the shell/command at all
        return (False, None, f"[oracle could not be launched: {e}]")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if len(out) > _OUTPUT_TAIL:
        out = "...\n" + out[-_OUTPUT_TAIL:]
    return (r.returncode == 0, r.returncode, out)


# ---------------------------------------------------------------------------- decision


def decide(payload):
    """Return a decision dict to emit, or None to allow silently (no interference).

    The branching is all here so the self-test can drive every path; the only I/O is find_oracle /
    run_oracle, which read the repo -- exactly what a moat must do."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    kind = classify_tool(tool_name)

    if kind != "refactor":
        # Reads (show_defun) and anything we don't understand: never gate, never run a test.
        return None

    # --- a mutating refactor: the oracle must be GREEN before we permit it. ---
    cwd = payload.get("cwd") or os.getcwd()
    target = find_target_file(tool_input)
    start_dir = cwd
    if target:
        t = target if os.path.isabs(target) else os.path.join(cwd, target)
        start_dir = t if os.path.isdir(t) else os.path.dirname(t) or cwd

    ignored = caller_supplied_oracle(tool_input)
    ign_note = ""
    if ignored:
        ign_note = (
            f" (NOTE: ignored caller-supplied {ignored!r} -- the oracle is read from the repo's "
            f"{ORACLE_FILE}, never from the tool call, so it cannot be faked)"
        )

    try:
        resolved = find_oracle(start_dir)
    except ValueError as e:
        return {
            "decision": "block",
            "reason": f"oracle-gate: the verification oracle is MISCONFIGURED -- {e}. "
            f"Fix {ORACLE_FILE} before refactoring.{ign_note}",
        }
    except Exception as e:  # noqa: BLE001  (fail-closed: any fault blocks)
        return {
            "decision": "block",
            "reason": f"oracle-gate: internal error resolving the oracle ({e}); blocking the "
            f"refactor fail-closed until it can be evaluated.{ign_note}",
        }

    if resolved is None:
        return {
            "decision": "block",
            "reason": f"oracle-gate: NO verification oracle found for {target or start_dir!r}. A "
            f"refactor is only behavior-preserving if a test suite says so. Add {ORACLE_FILE} at "
            f'the repo root with {{"test_cmd": "<your test command>"}} and ensure it passes, then '
            f"retry.{ign_note}",
        }

    repo_dir, test_cmd, timeout = resolved
    try:
        ok, rc, tail = run_oracle(test_cmd, repo_dir, timeout)
    except Exception as e:  # noqa: BLE001  (fail-closed: any fault blocks)
        return {
            "decision": "block",
            "reason": f"oracle-gate: internal error running the oracle {test_cmd!r} in {repo_dir} "
            f"({e}); blocking fail-closed.{ign_note}",
        }

    if ok:
        return {
            "decision": "approve",
            "reason": f"oracle-gate: verification oracle PASSED (exit 0) -- `{test_cmd}` in "
            f"{repo_dir}. Baseline is green; refactor permitted.{ign_note}",
        }
    return {
        "decision": "block",
        "reason": f"oracle-gate: verification oracle FAILED (exit {rc}) -- `{test_cmd}` in "
        f"{repo_dir}. Refusing the refactor: a red tree cannot certify a behavior-preserving "
        f"change. Fix the suite (or the pre-existing breakage) first.{ign_note}\n"
        f"--- oracle output (tail) ---\n{tail or '(no output)'}",
    }


def emit(decision):
    """Write EXACTLY one JSON object to stdout, or nothing for a silent allow."""
    if decision is None:
        return
    sys.stdout.write(json.dumps(decision))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:  # noqa: BLE001 -- any parse fault; decide fail-closed below
        # Could not even parse the event. Fail-closed only if it LOOKS like a refactor we can see
        # in the raw text; otherwise allow (never wedge the agent over a malformed unrelated event).
        if "refactor" in raw.lower() and "show_defun" not in raw.lower():
            emit(
                {
                    "decision": "block",
                    "reason": f"oracle-gate: could not parse the PreToolUse event ({e}); blocking a "
                    "possible refactor fail-closed.",
                }
            )
        return 0
    try:
        emit(decide(payload))
    except Exception as e:  # noqa: BLE001  (any fault: refactors fail-closed)
        if classify_tool(payload.get("tool_name", "")) == "refactor":
            emit(
                {
                    "decision": "block",
                    "reason": f"oracle-gate: internal hook error ({e}); blocking the refactor "
                    "fail-closed.",
                }
            )
    return 0


# ---------------------------------------------------------------------------- self-test


def _run_hook(payload):
    """Drive THIS script as a real subprocess with JSON on stdin; return (stdout, exit_code).

    Subprocess, never an in-process call -- a stray print is invisible in-process and fatal on the
    wire, so the test must exercise the exact path Claude Code uses."""
    p = subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return p.stdout, p.returncode


def _parse_decision(stdout):
    """Return the decision dict, or None for an empty (silent-allow) stdout. Asserts stdout is
    EXACTLY one JSON object when non-empty (the wire contract)."""
    s = stdout.strip()
    if not s:
        return None
    return json.loads(
        s
    )  # raises if stdout is not exactly one JSON object -> a real failure


def selftest():
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'ok ' if cond else 'FAIL'} {name}", file=sys.stderr)

    py = sys.executable
    pass_cmd = f'{py} -c "assert 1 == 1"'  # real process, exit 0
    fail_cmd = f'{py} -c "assert 1 == 2"'  # real process, exit 1 (AssertionError)

    td = tempfile.mkdtemp(prefix="oracle-gate-selftest-")
    try:
        # One target repo; a real source file. The ONLY thing that varies between the block run and
        # the allow run is the oracle file's test_cmd -- so an opposite verdict is CAUSED by the
        # oracle, nothing else (the discrimination control).
        repo = os.path.join(td, "targetrepo")
        os.makedirs(repo)
        src = os.path.join(repo, "mod.py")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("def f(x):\n    x = x + 1\n    return x\n")
        oracle_path = os.path.join(repo, ORACLE_FILE)

        # The byte-identical refactor tool call used for BOTH the pass and fail runs.
        refactor_payload = {
            "hook_event_name": "PreToolUse",
            "cwd": repo,
            "tool_name": "mcp__tsrefactor__ts_refactor",
            "tool_input": {"file": "mod.py"},
        }

        # 1) FAILING oracle -> BLOCK
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"test_cmd": fail_cmd}, fh)
        out_fail, rc_fail = _run_hook(refactor_payload)
        dec_fail = _parse_decision(out_fail)
        check(
            "refactor + FAILING oracle -> block",
            dec_fail is not None and dec_fail.get("decision") == "block",
        )
        check(
            "block stdout is exactly one JSON object",
            rc_fail == 0 and dec_fail is not None,
        )
        check(
            "block reason names the failing command",
            bool(dec_fail) and fail_cmd in dec_fail.get("reason", ""),
        )

        # 2) PASSING oracle -> ALLOW (approve). SAME payload; only the oracle file changed.
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"test_cmd": pass_cmd}, fh)
        out_pass, _rc_pass = _run_hook(refactor_payload)
        dec_pass = _parse_decision(out_pass)
        check(
            "refactor + PASSING oracle -> allow (not block)",
            dec_pass is None or dec_pass.get("decision") != "block",
        )
        check(
            "passing run explicitly approves",
            bool(dec_pass) and dec_pass.get("decision") == "approve",
        )

        # 2b) THE CONTROL, stated as one fact: identical input, oracle flipped -> verdict flipped.
        check(
            "SAME refactor input, oracle pass->fail FLIPS the verdict",
            bool(dec_fail)
            and dec_fail.get("decision") == "block"
            and (dec_pass is None or dec_pass.get("decision") != "block"),
        )

        # 3) show_defun (read) is ALWAYS allowed -- even with a FAILING oracle present, and it must
        #    NOT run the test at all. Proves discrimination by tool TYPE, not just oracle result.
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"test_cmd": fail_cmd}, fh)
        read_payload = {
            "hook_event_name": "PreToolUse",
            "cwd": repo,
            "tool_name": "mcp__tsedit__show_defun",
            "tool_input": {"file": "mod.py", "name": "f"},
        }
        out_read, rc_read = _run_hook(read_payload)
        dec_read = _parse_decision(out_read)
        check(
            "show_defun allowed despite a FAILING oracle (reads never gated)",
            rc_read == 0 and (dec_read is None or dec_read.get("decision") != "block"),
        )

        # A read with a test command that WOULD write a marker file proves the oracle never ran.
        marker = os.path.join(repo, "ORACLE_RAN.marker")
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"test_cmd": f"{py} -c \"open(r'{marker}','w').close()\""}, fh)
        _run_hook(read_payload)
        check(
            "a read does NOT execute the repo's test command",
            not os.path.exists(marker),
        )

        # 4) A refactor with NO oracle file anywhere -> BLOCK (fail-closed), reason names the fix.
        bare = os.path.join(td, "no_oracle_repo")
        os.makedirs(bare)
        with open(os.path.join(bare, "m.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        out_none, _ = _run_hook(
            {
                "hook_event_name": "PreToolUse",
                "cwd": bare,
                "tool_name": "ts_refactor",
                "tool_input": {"file": "m.py"},
            }
        )
        dec_none = _parse_decision(out_none)
        check(
            "refactor + NO oracle -> block fail-closed",
            bool(dec_none) and dec_none.get("decision") == "block",
        )
        check(
            "no-oracle block reason names the fix (.oracle-gate.json)",
            bool(dec_none) and ORACLE_FILE in dec_none.get("reason", ""),
        )

        # 5) ANTI-FAKE: a caller that smuggles test_cmd:"true" in tool_input is IGNORED; the repo's
        #    FAILING oracle still wins and the refactor is blocked.
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"test_cmd": fail_cmd}, fh)
        out_fake, _ = _run_hook(
            {
                "hook_event_name": "PreToolUse",
                "cwd": repo,
                "tool_name": "ts_refactor",
                "tool_input": {"file": "mod.py", "test_cmd": "true"},
            }
        )
        dec_fake = _parse_decision(out_fake)
        check(
            "caller-supplied test_cmd:'true' is IGNORED -> still blocked by repo oracle",
            bool(dec_fake) and dec_fake.get("decision") == "block",
        )
        check(
            "block reason says the caller oracle was ignored",
            bool(dec_fake) and "ignored caller-supplied" in dec_fake.get("reason", ""),
        )

        # 6) MISCONFIGURED oracle (present but no test_cmd) -> BLOCK, not a crash.
        with open(oracle_path, "w", encoding="utf-8") as fh:
            json.dump({"nope": 1}, fh)
        out_bad, _ = _run_hook(refactor_payload)
        dec_bad = _parse_decision(out_bad)
        check(
            "misconfigured oracle -> block (MISCONFIGURED)",
            bool(dec_bad)
            and dec_bad.get("decision") == "block"
            and "MISCONFIGURED" in dec_bad.get("reason", ""),
        )

        # 7) An unrelated tool is ALLOWED silently (the gate only asserts on what it understands).
        out_other, rc_other = _run_hook(
            {
                "hook_event_name": "PreToolUse",
                "cwd": repo,
                "tool_name": "Read",
                "tool_input": {"file_path": "mod.py"},
            }
        )
        check(
            "unrelated tool (Read) allowed silently (empty stdout)",
            rc_other == 0 and out_other.strip() == "",
        )

    finally:
        shutil.rmtree(td, ignore_errors=True)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    ok_all = passed == total
    print(
        f"RESULT: {'PASS' if ok_all else 'FAIL'} -- {passed}/{total} cases",
        file=sys.stderr,
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

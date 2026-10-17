#!/usr/bin/env python3
"""Hardening regressions: each case is a way a rewrite could LAND while the oracle should refuse it
(or a way the generator proposes code it must never propose). Every test drives the real binaries
as subprocesses, the way an agent or the MCP server does.

    python3 -m unittest -v tests.test_hardening
"""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_REFACTOR = ROOT / "bin" / "ts-refactor"
SX_MINE = ROOT / "bin" / "sx-mine"
GATE = ROOT / "hooks" / "oracle-gate.py"
PY = sys.executable

sys.path.insert(0, str(ROOT))
import mcp_server  # noqa: E402

# The selftest's own behavior-breaker: an inconsistent __ne__ makes `not (a == b)` differ from
# `a != b`, so the neg-cmp candidate MUST be refused by any honest oracle.
BREAKER = (
    "class W:\n"
    "    def __eq__(self, o): return True\n"
    "    def __ne__(self, o): return True\n"
    "def g():\n"
    "    return not (W() == W())\n"
)
BREAKER_TEST = "import b\nassert b.g() is False\n"


def _ts_refactor(*args, cwd=None, timeout=60):
    return subprocess.run(
        [PY, str(TS_REFACTOR), *map(str, args)],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def _events(stdout):
    out = []
    for line in stdout.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


class _TmpRepo(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def breaker_repo(self, oracle=True):
        (self.d / "b.py").write_text(BREAKER)
        (self.d / "test_b.py").write_text(BREAKER_TEST)
        cmd = "%s -B test_b.py" % PY
        if oracle:
            (self.d / ".oracle-gate.json").write_text(json.dumps({"test_cmd": cmd}))
        return self.d / "b.py", cmd

    def repo_oracle_green(self):
        return subprocess.run([PY, "-B", "test_b.py"], cwd=self.d, capture_output=True).returncode == 0


# ------------------------------------------------------------------------------------------------
# FINDING 1: the hook certifies the REPO oracle, but ts_refactor grades every candidate with the
# CALLER's test_cmd -- so `test_cmd: "true"` passes the gate and lands a behavior-breaker.


class CallerOracleBypass(_TmpRepo):
    def test_hook_blocks_caller_test_cmd_that_differs_from_repo_oracle(self):
        self.breaker_repo()
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.d),
            "tool_name": "mcp__plugin_verified-refactor_verified-refactor__ts_refactor",
            "tool_input": {"file": "b.py", "test_cmd": "true"},
        }
        p = subprocess.run([PY, str(GATE)], input=json.dumps(payload), capture_output=True, text=True)
        dec = json.loads(p.stdout)
        self.assertEqual(dec["decision"], "block", dec)

    def test_hook_still_approves_when_caller_test_cmd_matches(self):
        _, cmd = self.breaker_repo()
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": str(self.d),
            "tool_name": "ts_refactor",
            "tool_input": {"file": "b.py", "test_cmd": cmd},
        }
        p = subprocess.run([PY, str(GATE)], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(json.loads(p.stdout)["decision"], "approve")

    def test_mcp_tool_refuses_caller_oracle_that_differs_from_repo_oracle(self):
        # No hook in front (Cursor / raw MCP client): the tool layer itself must hold the line.
        src, _ = self.breaker_repo()
        out = mcp_server.ts_refactor(str(src), "true")
        self.assertEqual(src.read_text(), BREAKER, "behavior-breaker landed:\n" + out)
        self.assertTrue(self.repo_oracle_green())
        self.assertIn("ERROR", out)

    def test_mcp_tool_runs_repo_oracle_in_repo_dir(self):
        # The repo oracle is relative to its repo; the server's own cwd is arbitrary.
        (self.d / "a.py").write_text("def f(x):\n    x = x + 1\n    return x\n")
        (self.d / "test_a.py").write_text("import a\nassert a.f(1) == 2\n")
        (self.d / ".oracle-gate.json").write_text(json.dumps({"test_cmd": "%s -B test_a.py" % PY}))
        old = os.getcwd()
        os.chdir("/")
        try:
            out = mcp_server.ts_refactor(str(self.d / "a.py"), "")
        finally:
            os.chdir(old)
        self.assertIn("x += 1", (self.d / "a.py").read_text(), out)


# ------------------------------------------------------------------------------------------------
# FINDING 2: a candidate is written to disk BEFORE it is graded. If ts-refactor dies mid-grade
# (SIGTERM, or the MCP server's timeout, which is SIGKILL) the UNGRADED candidate stays on disk.

SLOW_SECOND_RUN = (
    "if [ -e ran_once ]; then touch in_slow; sleep 40; fi\n"
    "touch ran_once\n"
    "%s -B test_b.py\n" % PY
)


class KilledMidGrade(_TmpRepo):
    def _start(self):
        src, _ = self.breaker_repo(oracle=False)
        (self.d / "t.sh").write_text(SLOW_SECOND_RUN)
        proc = subprocess.Popen(
            [PY, str(TS_REFACTOR), str(src), "--test", "sh t.sh"],
            cwd=self.d,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.time() + 30
        while not (self.d / "in_slow").exists():
            self.assertLess(time.time(), deadline, "candidate grade never started")
            time.sleep(0.05)
        self.assertNotEqual(src.read_text(), BREAKER, "precondition: the breaker is on disk")
        return src, proc

    def test_sigterm_mid_grade_restores_last_verified_bytes(self):
        src, proc = self._start()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=20)
        self.assertEqual(src.read_text(), BREAKER, "ungraded candidate left on disk after SIGTERM")
        self.assertTrue(self.repo_oracle_green())

    def test_sigkill_mid_grade_leaves_a_recovery_backup_and_next_run_refuses(self):
        src, proc = self._start()
        proc.kill()
        proc.communicate(timeout=20)
        backups = list(self.d.glob("b.py.ts-refactor-*"))
        self.assertEqual(len(backups), 1, "no record of the last verified bytes after SIGKILL")
        self.assertEqual(backups[0].read_text(), BREAKER)
        r = _ts_refactor(src, "--test", "true", cwd=self.d)
        self.assertNotEqual(r.returncode, 0, "a second run must not build on an ungraded file")
        self.assertIn(backups[0].name, r.stdout + r.stderr)

    def test_mcp_timeout_does_not_leave_ungraded_candidate(self):
        src, _ = self.breaker_repo(oracle=False)
        (self.d / "t.sh").write_text(SLOW_SECOND_RUN)
        old = os.getcwd()
        os.chdir(self.d)
        try:
            out = mcp_server._run([str(TS_REFACTOR), str(src), "--test", "sh t.sh"], timeout=5)
        finally:
            os.chdir(old)
        self.assertIn("timed out", out)
        self.assertEqual(src.read_text(), BREAKER, "MCP timeout left the ungraded breaker on disk")


# ------------------------------------------------------------------------------------------------
# FINDING 3: mined tree-sitter queries are unanchored, so `y == False -> not y` also "matches"
# `a == b == False` (skipping `b`) and proposes `not a`.


class MinedQueryAnchoring(_TmpRepo):
    def _rule(self, before, after):
        r = subprocess.run([PY, str(SX_MINE), before, after], capture_output=True, text=True)
        p = self.d / "rule.jsonl"
        p.write_text(r.stdout)
        return p

    def test_mined_rule_does_not_fire_on_chained_comparison(self):
        rule = self._rule("y == False", "not y")
        src = self.d / "c.py"
        src.write_text("def h(a, b):\n    return a == b == False\n")
        r = _ts_refactor(src, "--rules", rule, "--test", "true", cwd=self.d)
        self.assertIn("a == b == False", src.read_text(), r.stdout)

    def test_mined_rule_still_fires_on_its_own_shape_once_per_site(self):
        # Control: the anchoring fix must not stop the rule matching what it was mined from.
        rule = self._rule("y == False", "not y")
        src = self.d / "c.py"
        src.write_text("def h(flag):\n    return flag == False\n")
        r = _ts_refactor(src, "--rules", rule, "--dry", cwd=self.d)
        cands = [e for e in _events(r.stdout) if e.get("event") == "candidate"]
        self.assertEqual(len(cands), 1, r.stdout)


# ------------------------------------------------------------------------------------------------
# FINDING 4: the generator is claimed correct-by-construction, yet aug-assign proposes syntax
# errors / drops annotations, and nothing re-parses a candidate before it can land.


class GeneratorEmitsValidCode(_TmpRepo):
    def _run_true(self, text):
        src = self.d / "d.py"
        src.write_text(text)
        _ts_refactor(src, "--test", "true", cwd=self.d)
        return src.read_text()

    def test_chained_assignment_is_not_turned_into_a_syntax_error(self):
        got = self._run_true("y = x = x + 1\n")
        compile(got, "d.py", "exec")  # raises SyntaxError on `y = x += 1`
        self.assertEqual(got, "y = x = x + 1\n")

    def test_annotated_assignment_keeps_its_annotation(self):
        got = self._run_true("x: int = x + 1\n")
        self.assertEqual(got, "x: int = x + 1\n")


if __name__ == "__main__":
    unittest.main()

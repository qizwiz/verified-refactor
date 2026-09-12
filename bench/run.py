#!/usr/bin/env python3
"""bench/run.py -- sealed-benchmark harness SEED for ts-refactor.

WHAT. Refactor the functions in a target repo COPY, then assert 0 test regressions
vs the baseline -- PLUS a negative control that deliberately breaks behavior and
asserts the harness DETECTS >=1 regression, so a "0 regressions" verdict can never
be the 0-leaks-across-0-answered vacuum.

DETACHMENT. ts-refactor and the test oracle are invoked via sys.executable -- never
a shebang, never a substrate path -- so the whole run uses only requirements.txt deps.

SEALED (seed level). The GATE is declared in code BEFORE any measurement; the receipt
pins sha256 of the tool files + requirements + the gate itself, so a later edit to any
of them changes the receipt. A REAL sealed sweep needs more (printed at the end).

Usage:  python bench/run.py          # local sanity case + negative control
        python bench/run.py --json   # receipt JSON only, to stdout
"""
import hashlib
import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin"
TS_REFACTOR = BIN / "ts-refactor"

# --- GATE: declared BEFORE any measurement (sealed discipline) ---------------
GATE = {
    "positive": {"min_applied": 1, "max_regressions": 0},
    "negative": {"min_regressions": 1},
}

RAN_RE = re.compile(r"Ran (\d+) test")


def sha256_file(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError as e:
        return "ERR:%s" % e


def run_suite(targetdir):
    """Run the target's unittest suite with the VENV python. Returns (rc, ran).

    -B + PYTHONDONTWRITEBYTECODE + wiping __pycache__ is load-bearing: a rewrite in
    the SAME wall-clock second as the baseline compile leaves a stale .pyc whose
    recorded source-mtime (1s resolution) still matches, so unittest silently runs
    the OLD code -- which made this detector vacuous until the negative control caught it.
    """
    shutil.rmtree(Path(targetdir) / "__pycache__", ignore_errors=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-q"],
        cwd=targetdir, capture_output=True, text=True, env=env,
    )
    m = RAN_RE.search(r.stderr + r.stdout)
    ran = int(m.group(1)) if m else -1
    return (r.returncode, ran)


def build_target(root):
    """A tiny local target repo: 3 functions, each holding an aug-assign site."""
    (root / "calc.py").write_text(
        "def inc(x):\n"
        "    x = x + 1\n"
        "    return x\n\n"
        "def add_n(x, n):\n"
        "    x = x + n\n"
        "    return x\n\n"
        "def accumulate(items):\n"
        "    total = 0\n"
        "    for it in items:\n"
        "        total = total + it\n"
        "    return total\n"
    )
    (root / "test_calc.py").write_text(
        "import unittest\n"
        "import calc\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_inc(self): self.assertEqual(calc.inc(1), 2)\n"
        "    def test_add_n(self): self.assertEqual(calc.add_n(10, 5), 15)\n"
        "    def test_accumulate(self): self.assertEqual(calc.accumulate([1, 2, 3]), 6)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n"
    )
    return root / "calc.py"


def refactor_file(src, oracle_cmd):
    """Drive ts-refactor via sys.executable. Returns (applied, rc)."""
    r = subprocess.run(
        [sys.executable, str(TS_REFACTOR), str(src), "--test", oracle_cmd],
        capture_output=True, text=True,
    )
    applied = 0
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "done":
            applied = ev.get("applied", 0)
    return (applied, r.returncode)


def _regressions(base_rc, base_ran, after_rc, after_ran):
    return 0 if (base_rc == 0 and after_rc == 0 and after_ran == base_ran) else 1


def positive_case():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = build_target(root)
        before_hash = sha256_file(src)
        base_rc, base_ran = run_suite(root)
        oracle = "cd %s && %s -B -m unittest -q" % (td, sys.executable)
        applied, _rc = refactor_file(src, oracle)
        after_rc, after_ran = run_suite(root)
        after_hash = sha256_file(src)
        return {
            "name": "positive/aug-assign",
            "baseline_rc": base_rc, "baseline_ran": base_ran,
            "applied": applied, "file_changed": before_hash != after_hash,
            "after_rc": after_rc, "after_ran": after_ran,
            "regressions": _regressions(base_rc, base_ran, after_rc, after_ran),
        }


def negative_control():
    """Prove the harness's regression detector is NOT vacuous: inject a
    behavior-breaking edit and assert it is DETECTED as >=1 regression."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = build_target(root)
        base_rc, base_ran = run_suite(root)
        src.write_text(src.read_text().replace("    x = x + 1\n", "    x = x + 2\n", 1))
        after_rc, after_ran = run_suite(root)
        return {
            "name": "negative/injected-breaker",
            "baseline_rc": base_rc, "baseline_ran": base_ran,
            "after_rc": after_rc, "after_ran": after_ran,
            "regressions": _regressions(base_rc, base_ran, after_rc, after_ran),
        }


def main():
    pos = positive_case()
    neg = negative_control()
    pos_ok = (pos["applied"] >= GATE["positive"]["min_applied"]
              and pos["regressions"] <= GATE["positive"]["max_regressions"])
    neg_ok = neg["regressions"] >= GATE["negative"]["min_regressions"]
    passed = bool(pos_ok and neg_ok)
    receipt = {
        "harness": "bench/run.py (seed)",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "gate": GATE,
        "gate_sha": hashlib.sha256(json.dumps(GATE, sort_keys=True).encode()).hexdigest()[:16],
        "hashes": {
            "ts-refactor": sha256_file(BIN / "ts-refactor"),
            "sx-mine": sha256_file(BIN / "sx-mine"),
            "ts-edit": sha256_file(BIN / "ts-edit"),
            "requirements.txt": sha256_file(HERE.parent / "requirements.txt"),
        },
        "cases": [pos, neg],
        "pos_ok": pos_ok, "neg_ok": neg_ok, "pass": passed,
    }
    if "--json" in sys.argv:
        print(json.dumps(receipt, indent=2))
        return 0 if passed else 1
    out = HERE / "last-receipt.json"
    out.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    print()
    print("POSITIVE: applied=%d regressions=%d -> %s"
          % (pos["applied"], pos["regressions"], "OK" if pos_ok else "FAIL"))
    print("NEGATIVE: regressions=%d (expect >=1) -> %s"
          % (neg["regressions"], "OK" if neg_ok else "FAIL"))
    print("RESULT: %s  (receipt -> %s)" % ("PASS" if passed else "FAIL", out))
    print()
    print("A REAL sealed sweep still needs:")
    print(" (1) a fresh public target repo the harness has never seen;")
    print(" (2) a real per-repo --test oracle (the repo's own suite), not a generated one;")
    print(" (3) N>=30 refactor sites across real code for a meaningful rate;")
    print(" (4) independent labeling of 'regression' + a ONE-SHOT burn (run-once, refuse")
    print("     re-run) so the number cannot be tuned against the seen set.")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

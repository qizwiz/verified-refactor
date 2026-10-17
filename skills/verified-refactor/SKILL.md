---
name: verified-refactor
description: Oracle-gated, behavior-preserving refactoring. Read a definition's exact bytes with show-defun, then rewrite it with ts-refactor, which keeps a change only if your test suite still passes AND the code shrinks, otherwise reverts it. Slash-only; invoke deliberately when you want a verified rewrite, not an un-graded edit.
disable-model-invocation: true
---

# verified-refactor

**Structure proposes, the sound test oracle disposes.** Use this skill when you want a refactor
that is *certified* — applied only after your test command still passes and the code gets
smaller — rather than an edit you hope is equivalent.

This skill is **slash-only** (`disable-model-invocation: true`): it will not fire on its own.
Invoke it when you have decided you want a verified rewrite.

## Two surfaces: MCP tools and CLI binaries

The plugin exposes three **MCP tools** (underscored names — what an MCP client calls):
`show_defun(file, name)`, `ts_refactor(file, test_cmd)`, and `sx_mine(before, after, lang)`.
Each shells to the matching **CLI binary** under `bin/` (hyphenated: `ts-edit --defun`,
`ts-refactor`, `sx-mine`), which carries the full flag set shown below. Note: the MCP
`ts_refactor(file, test_cmd)` form **always applies in place with the given test** — there is no
`--dry`, `--rules`, or `--max` through MCP; use the CLI binary when you want those.

## The two-step loop

### 1. `show_defun` (CLI: `ts-edit --defun`) — read the exact bytes first

Before rewriting anything, serve the *current, byte-precise* source of the definition by name.
This is how you avoid guessing an anchor or rewriting a site you didn't mean to.

```
# MCP tool:  show_defun(file="path/to/module.py", name="MyClass.collect")
# CLI binary:
bin/ts-edit --defun path/to/module.py MyClass.collect
bin/ts-edit --defun path/to/service.go Server.Handle
```

It addresses code by the **name** of a definition, not by quoting bytes a formatter might
reflow, and it is **read-only**. Read the output, confirm it's the definition you intend, then
rewrite.

### 2. `ts_refactor` (CLI: `ts-refactor`) — propose, then let the oracle dispose

Tree-sitter finds candidate rewrite sites; each candidate is applied and graded. Your `--test`
command is the oracle: it must still **pass**, and byte length must **drop**, or the candidate
is **reverted**.

```
# dry-run: see what would change, touch nothing (CLI only)
bin/ts-refactor path/to/module.py --dry

# apply, gated by YOUR tests (the oracle)
bin/ts-refactor path/to/module.py --test 'pytest -q tests/test_module.py'

# bound the work and the oracle's time budget
bin/ts-refactor path/to/module.py --test 'pytest -q' --max 10 --timeout 120
```

`--test 'CMD'` is **required** for a real CLI run (it's the oracle); a run without it must be
`--dry`. Through the MCP tool, `ts_refactor(file, test_cmd)` always applies in place with the
given test — the `--dry`, `--rules`, `--max`, and `--selftest` flags are **CLI-only**.

If a `.oracle-gate.json` governs the file, **its** `test_cmd` is the oracle: the MCP tool runs it
from that repo's directory, `test_cmd` may be omitted, and a different `test_cmd` is refused (as is
the hook). A run killed mid-grade leaves `<file>.ts-refactor-verified` holding the last verified
bytes; `ts-refactor` refuses to run on that file until you restore or remove it.

### Optional: mine a rule, then certify it (`sx-mine` → `ts-refactor --rules`)

To teach a new rewrite from a single example, mine a rule and feed it back in:

```
# learn a rule from ONE (before, after) pair -> rule JSON on stdout
# (MCP tool: sx_mine(before="x = x + 1", after="x += 1", lang="python"))
bin/sx-mine "x = x + 1" "x += 1" > /tmp/rule.jsonl

# apply the mined rule at NEW sites, still oracle-certified (CLI only: --rules)
bin/ts-refactor path/to/module.py --rules /tmp/rule.jsonl --test 'pytest -q'
```

The mined rule generalizes over its operands (so `total = total + 3` also matches), but keeps
the transformed structure literal — mine one rule per operator.

## Verifying the engine itself

```
bin/ts-refactor --selftest
```

This self-check exercises both directions: it **applies** a valid rewrite and **rejects** a
deliberately behavior-breaking one — including that a rule mined by `sx-mine` applies to a new
instance and is oracle-certified. A PASS is the engine demonstrating its own invariant. (It is
the tool's own developer check and references the author's local `sx-mine`/python paths, so run
it as a check rather than treating any printed count as a portable number.)

## Caveats (read these)

- **The oracle is only as sound as your test suite.** A rewrite that passes weak tests can still
  change untested behavior. If coverage is thin, strengthen the tests *before* trusting a run.
- The cost metric is **byte length**. Smaller is not always more readable — review the diff.
- Rules are **structurally-motivated, not proven equivalences** (program equivalence is
  undecidable). `not (a < b)` style ordered-comparison negations are deliberately excluded
  (NaN-unsafe); `== ↔ !=` is allowed but test-gated because a custom `__ne__` can differ — the
  oracle is what catches it.
- **Python rules only** in this build; the tree-sitter chassis extends to other grammars as rule
  sets are added (`--lang`).
- Always: `show-defun` first (read), `--dry` second (preview), `--test` third (certify). Never
  land a rewrite you haven't gated on a test.

## No efficiency claims

This skill asserts a **verification invariant** (every emitted rewrite passed your test and
shrank the code), not a speed or token-savings claim. Any such number would require a sealed,
held-out-repo benchmark; none is claimed here.

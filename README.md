# verified-refactor

**Structure proposes, the sound test oracle disposes.**

`verified-refactor` is a refactoring engine built around one discipline: a rewrite is only
allowed to land if an un-fakeable oracle certifies it. Tree-sitter walks the parse tree and
proposes candidate rewrites (the *correct-by-construction generator*). Each candidate is then
**applied and graded**: your own test command must still **pass**, *and* a cost metric (byte
length) must **drop**. A candidate that breaks behavior fails the test and is **reverted**.

The consequence is the whole point: the tool can *only ever emit test-passing, smaller code*.

## Why a test oracle, not "proven equivalence"

Behavioral equivalence of arbitrary programs is **undecidable**. So the rewrite rules are not
claimed to be proven equivalences — they are *structurally-motivated* rewrites (e.g.
`x = x + y` → `x += y`, or `not (a == b)` → `a != b`). The claim to safety lives entirely in
the test suite you supply:

> The rules are the generator. **The test suite is the equivalence oracle. The oracle is the
> achievement.**

If your tests are weak, a rewrite that passes them can still change untested behavior. The tool
is exactly as sound as the suite you point it at, and it says so rather than pretending
otherwise.

## Honest numbers

**No performance or token-savings claim is made here.** Not "refactors faster", not "uses fewer
tokens", not "smaller diffs than a human". The only property asserted is the *verification
invariant above* — that every emitted rewrite passed your test command and reduced byte count.
Run `bin/ts-refactor --selftest` to exercise that invariant in **both** directions: it applies a
valid rewrite and **rejects** a deliberately behavior-breaking one. (That selftest is the tool's
own developer check and references the author's local `sx-mine`/python paths, so treat its result
as a check you run, not a number quoted here.)

Any quantitative efficiency claim will appear here **only** once it is backed by a **sealed,
held-out-repo benchmark**: a fixed set of repositories the tuning never saw, gates declared
before the numbers exist, run once, reported including refutations. Until that benchmark exists,
this README deliberately quotes no speed, no token, and no "beats X" figure. A number measured on
data whose failures were inspected during tuning is a memory, not a measurement.

## What's in the box

| Component | Role |
|---|---|
| `bin/ts-refactor` | **Certify + apply.** Tree-sitter proposes rewrites; the `--test` oracle disposes. `--selftest` proves it applies a valid rewrite and rejects a behavior-breaker. |
| `bin/ts-edit` (`--defun NAME`) | Serve a definition's **exact current source by name**, byte-precise — read before you rewrite, so you never guess an anchor. Exposed as the MCP tool `show_defun`. |
| `bin/sx-mine` | **Generate.** Learn a rewrite *rule* from one `(before, after)` example via s-expr anti-unification; feed the rule to `ts-refactor --rules` to certify + apply it at new sites. Exposed as the MCP tool `sx_mine`. |
| `mcp_server.py` | stdio MCP server exposing three tools (underscored names): `show_defun(file, name)`, `ts_refactor(file, test_cmd)`, and `sx_mine(before, after, lang)`. Each shells to the matching `bin/` binary. |
| `hooks/hooks.json` | A `PreToolUse` hook (matcher `.*(ts_refactor\|show_defun).*`) that blocks a refactor when the target repo's declared test oracle is red, or when the call carries a `test_cmd` other than the repo's, so an un-graded rewrite can't land. |

> Note: `mcp_server.py` and `hooks/hooks.json` are the integration layer over the `bin/` tools;
> see **Status** below for which pieces are present in this build. The MCP tool names are
> underscored (`show_defun`, `ts_refactor`, `sx_mine`); the CLI binaries are hyphenated
> (`ts-edit`, `ts-refactor`, `sx-mine`).

## Install

### (a) Claude Code — as a plugin via the bundled marketplace

This repo ships both a plugin (`.claude-plugin/plugin.json`) and a one-plugin marketplace
(`marketplace.json`).

```
/plugin marketplace add /absolute/path/to/verified-refactor
/plugin install verified-refactor@verified-refactor-marketplace
```

Then restart the session so the MCP server, the `PreToolUse` hook, and the skill load. The skill
is **slash-only** (it will not auto-invoke); call it with its namespaced command:

```
/verified-refactor:verified-refactor
```

> Discovery note: Claude Code's canonical marketplace file is `.claude-plugin/marketplace.json`.
> This repo ships `marketplace.json` at the repo root (per this build's spec). If
> `/plugin marketplace add` does not discover the root copy, the reliable fix is to copy
> `marketplace.json` into `.claude-plugin/` and add the directory again.

### (b) A generic MCP client / Cursor — consuming the raw stdio server

The MCP server is a plain stdio server, so any MCP-capable client can run it directly. For
**Cursor**, add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "verified-refactor": {
      "command": "python3",
      "args": ["/absolute/path/to/verified-refactor/mcp_server.py"]
    }
  }
}
```

For any other MCP client, register the same `command` + `args` stdio transport. (The
`${CLAUDE_PLUGIN_ROOT}` form used inside the plugin is Claude-Code-specific — use an absolute
path elsewhere.)

### Requirements

```
pip install -r requirements.txt
```

Installs `tree-sitter`, `tree-sitter-language-pack`, and the `mcp` SDK (`mcp_server.py` uses
`mcp.server.fastmcp`) into the `python3` the server/tools run under. The CLI tools themselves
read/write files directly and depend only on tree-sitter — no editor, no network.

## Usage sketch

```
# 1. See exactly what you're about to touch (no guessing anchors)
#    MCP: show_defun(file="path/to/module.py", name="MyClass.collect")
bin/ts-edit --defun path/to/module.py MyClass.collect

# 2. Dry-run the proposed rewrites (CLI only)
bin/ts-refactor path/to/module.py --dry

# 3. Apply, gated by YOUR test command as the oracle
#    MCP: ts_refactor(file="path/to/module.py", test_cmd="pytest -q tests/test_module.py")
bin/ts-refactor path/to/module.py --test 'pytest -q tests/test_module.py'
```

Today's rule set is Python (`--lang python`); the tree-sitter chassis generalizes to other
grammars as rule sets are added.

## Caveats

- **The oracle is only as sound as your tests.** Weak tests → unsafe rewrites can slip through.
- The cost metric is **byte length**; a smaller rewrite is not always a *more readable* one.
- Rules are structurally-motivated, not proven equivalences (equivalence is undecidable).
- Python rules only in this build.

## Status

This build ships:

- **Present:** the `bin/` refactoring tools (`ts-refactor`, `ts-edit`, `sx-mine`, `sexpr_lib.py`),
  `requirements.txt`, and the plugin packaging (`.claude-plugin/plugin.json`, `marketplace.json`,
  this README, and the slash-only skill).
- **Integration layer (declared by `plugin.json`):** `mcp_server.py` — the stdio MCP server that
  exposes `show_defun` (over `ts-edit --defun NAME`), `ts_refactor` (over `bin/ts-refactor`), and
  `sx_mine` (over `bin/sx-mine`); and `hooks/hooks.json` plus `hooks/oracle-gate.py` — the
  `PreToolUse` hook config and its gate script.

`claude plugin validate .` passes on this build — the `mcpServers` and `hooks` manifest shapes
validate, and `mcp_server.py`, `hooks/hooks.json`, and `hooks/oracle-gate.py` are present.

## License

MIT. This is intended to be permissively reusable; add a `LICENSE` file with the MIT text when
publishing.

#!/opt/homebrew/bin/python3
"""sexpr_lib -- the ONE s-expr reader shared by the *-do tools (arc-do, mac-do).

Extracted 2026-07-11 (aggressively-macroize rule: the reader appeared twice).
Tiny closed reader: nesting, double-quoted strings with backslash escapes, atoms.
No eval, no symbols-with-behavior -- consumers whitelist heads themselves.

Also carries `known_heads`: what a *-do tool can actually DO, derived from the
dispatcher itself rather than from a list anyone maintains by hand.
"""

import ast as _ast
import re as _re


def head_docs(path):
    """{head: one-line purpose} read off the COMMENT ON THE BRANCH THAT COMPILES IT.

    A head's meaning lives in its branch, so that is where the description is derived from --
    never a second table mapping head to prose, which would drift from the dispatcher exactly
    the way mac-do's hand-written `know:` string drifted five heads behind its own code.

    Only branches that already carry a substantive comment produce an entry. Measured
    2026-08-01: 13 of 42 (10 mac-do, 3 arc-do). The other 29 are not invented here -- an
    undescribed head is reported as undescribed, because a capability index that fabricates
    summaries is worse than one that admits the gap.
    """
    try:
        src = open(path, errors="replace").read().splitlines()
    except OSError:
        return {}
    out = {}
    for i, line in enumerate(src):
        m = _re.match(
            r"\s*(?:el)?if head (?:==|in)\s*\(?[\"']([a-z][a-z0-9-]*)[\"']", line
        )
        if not m:
            continue
        # A GROUPED branch describes EVERY head it dispatches. `if head in ("msg-read",
        # "msg-threads", "msg-last")` compiles all three the same way, so its comment is true of
        # all three -- taking only the first left heads undescribed while the description sat two
        # lines above them. The tuple often spans lines (the formatter wraps it), so literals are
        # collected until the closing paren rather than read off the first line.
        heads = [m.group(1)]
        if _re.match(r"\s*(?:el)?if head in", line):
            blob, k = line, i
            while (
                ")" not in blob.split(" in ", 1)[-1] and k + 1 < len(src) and k - i < 24
            ):
                k += 1
                blob += src[k]
            heads = _re.findall(r"[\"']([a-z][a-z0-9-]*)[\"']", blob) or heads
        # A TRAILING comment on the `if` line itself is the third convention in this file:
        # `if head == "wait":  # inter-action delay inside a (batch ...)`. Looking only BELOW the
        # branch missed it, so `wait` counted as undescribed while its description sat on the
        # same line as its name.
        inline = line.split("#", 1)[1].strip() if "#" in line.split(":", 1)[-1] else ""
        if len(inline) >= 14:
            for h in heads:
                out[h] = inline
            continue
        for j in range(i + 1, min(i + 4, len(src))):
            s = src[j].strip()
            # STOP AT THE NEXT BRANCH, not at the first code line. Scanning three lines down
            # crossed branch boundaries and adopted the NEXT head description: `title` (which
            # is just document.title) carried `sense` summary of the whole actionable surface,
            # and msg-read / msg-threads / msg-last carried `self` PROPRIOCEPTION line. This
            # function docstring says an index that fabricates summaries is worse than one
            # admitting the gap -- and it was fabricating four.
            #
            # The boundary is the NEXT branch, NOT the first statement: several heads open with
            # a guard (`if not app: raise ...`) and put their description after it, so cutting
            # at the first code line silently dropped a11y-activate and imsg. Measured both
            # ways -- code-line cut gave 37/43 and lost two real ones; branch cut gives 39/43.
            if _re.match(r"\s*(?:el)?if head\b", src[j]) or _re.match(
                r"\s*(?:el)?if \(\s*$", src[j]
            ):
                break
            if not s.startswith("#"):
                continue
            t = s.lstrip("# ").strip()
            # `(head ...) -- purpose` is the common shape; keep only the purpose half.
            t = _re.sub(r"^\(?%s[^)]*\)?\s*(?:--|—)\s*" % _re.escape(m.group(1)), "", t)
            if len(t) >= 14:
                for h in heads:
                    out[h] = t
            break
    # WRAPPED BRANCHES, and the trap is that WRITING a description can destroy it. The scan
    # above needs `if head == "x"` on ONE line, and a long trailing comment is exactly what
    # makes the formatter wrap the condition:
    #     if (
    #         head == "text"
    #     ):  # visible innerText of the page, capped at N chars
    # Measured 2026-08-02: I described 17 heads and 12 of those descriptions vanished on the
    # next format. known_heads never had this problem because it reads the AST; this did, so
    # now it does too. ADDITIVE -- setdefault, so every answer the line scan already found is
    # preserved and only the wrapped branches are filled in.
    try:
        tree = _ast.parse("\n".join(src))
    except Exception:
        return out  # unparseable: keep what the line scan found, never claim more
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.If) or not node.body:
            continue
        heads2 = []
        for cmp_ in _ast.walk(node.test):
            if not (
                isinstance(cmp_, _ast.Compare) and isinstance(cmp_.left, _ast.Name)
            ):
                continue
            if cmp_.left.id != "head":
                continue
            for c in cmp_.comparators:
                if isinstance(c, _ast.Constant) and isinstance(c.value, str):
                    heads2.append(c.value)
                elif isinstance(c, (_ast.Tuple, _ast.List, _ast.Set)):
                    heads2 += [
                        e.value
                        for e in c.elts
                        if isinstance(e, _ast.Constant) and isinstance(e.value, str)
                    ]
        if not heads2:
            continue
        # Only the HEADER span -- the `if` line through the `):` that closes it. The body is
        # deliberately excluded: an unwrapped branch whose comment sits BELOW it is already
        # the line scan job, and reaching into the body would adopt unrelated comments.
        for j in range(node.lineno - 1, max(node.lineno, node.body[0].lineno - 1)):
            s = src[j] if j < len(src) else ""
            k = s.find("#")
            if k < 0:
                continue
            t2 = s[k + 1 :].strip()
            if len(t2) >= 14:
                for h in heads2:
                    out.setdefault(h, t2)
                break
    return out


def known_keys(path):
    """Every :keyword `path` actually READS, off its own source. None if unparseable.

    THE DEFECT, 2026-08-02. mac-do validates the HEAD and never the KEYS, so
    `(msg-read :handle "+1..." :n 6)` silently ignored :n -- the reader wants :limit -- and
    returned the default 10 rows. The caller believes it constrained the read and it did not.
    That is the same class as a silent zero: an argument that does nothing, reported as
    success. I then piped the un-narrowed result through `tail`, read the OLDEST rows, and
    told JH his message store was broken. It was not.

    UNION ACROSS THE WHOLE FILE, deliberately, not per-head. A per-head set would need the
    control flow that reaches each kv access, and getting that wrong REFUSES A VALID KEY --
    strictly worse than the silence it replaces. The union can never refuse a key the tool
    reads somewhere, and still catches invented ones and typos, which is the actual error.

    Derived from the kv[":x"] / kv.get(":x") accesses themselves, so a key added to a head is
    accepted the moment it is read, with no edit here.
    """
    try:
        tree = _ast.parse(open(path, errors="replace").read())
    except Exception:
        return None
    out = set()
    for n in _ast.walk(tree):
        k = None
        if isinstance(n, _ast.Subscript):  # kv[":x"]
            if getattr(n.value, "id", "") == "kv" and isinstance(
                n.slice, _ast.Constant
            ):
                k = n.slice.value
        elif isinstance(n, _ast.Call):  # kv.get(":x", ...)
            f = n.func
            if (
                isinstance(f, _ast.Attribute)
                and f.attr == "get"
                and getattr(f.value, "id", "") == "kv"
                and n.args
                and isinstance(n.args[0], _ast.Constant)
            ):
                k = n.args[0].value
        if isinstance(k, str) and k.startswith(":"):
            out.add(k)
    return out


def known_heads(path):
    """Every head `path`'s dispatcher accepts, read off its own source. None if unparseable.

    WHY (2026-08-01). mac-do raised `unknown head 'x' (know: ...)` from a HARDCODED string, and
    that string had drifted FIVE heads behind the code: `flick`, `select-all`, `wait`,
    `win-move` and `windows` were dispatchable but advertised nowhere. `windows` is one the
    agent actively uses -- a capability it depends on was undiscoverable from the tool's own
    help. Nothing forced the two to agree, so they diverged, exactly as the two-lists rule says.

    Derived from the `head == "x"` / `head in ("x", "y")` comparisons the dispatcher actually
    makes, so a new head is advertised the moment it is dispatched, with no edit here. Used by
    BOTH the tools' error messages and `verbs`, so there is one answer to "what can this do".

    Cost is an ast.parse of a ~40KB file, and only on the error path or when enumerating.
    """
    try:
        tree = _ast.parse(open(path, errors="replace").read())
    except Exception:
        return None
    out = set()
    for n in _ast.walk(tree):
        if not (isinstance(n, _ast.Compare) and isinstance(n.left, _ast.Name)):
            continue
        if n.left.id != "head":
            continue
        for c in n.comparators:
            if isinstance(c, _ast.Constant) and isinstance(c.value, str):
                out.add(c.value)
            elif isinstance(c, (_ast.Tuple, _ast.List, _ast.Set)):
                for e in c.elts:
                    if isinstance(e, _ast.Constant) and isinstance(e.value, str):
                        out.add(e.value)
    return sorted(out)


def tokenize(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif c in "()":
            out.append(c)
            i += 1
        elif c == ";":
            # Lisp end-of-line comment. Added 2026-07-30 when effects.sexpr -- a POLICY file whose
            # every line needs a stated reason -- failed to parse with "trailing tokens after form",
            # because `;` was being read as an atom. A data format that cannot carry its own rationale
            # forces the rationale into some other file, where it drifts from the data it explains.
            # Safe by ordering: the string branch below consumes quoted text whole, so a `;` inside a
            # string never reaches here.
            while i < len(s) and s[i] != "\n":
                i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < len(s) and s[j] != '"':
                if s[j] == "\\" and j + 1 < len(s):
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            out.append(("str", "".join(buf)))
            i = j + 1
        else:
            j = i
            while j < len(s) and not s[j].isspace() and s[j] not in '()"':
                j += 1
            out.append(("atom", s[i:j]))
            i = j
    return out


def parse(toks, pos=0):
    if toks[pos] == "(":
        lst, pos = [], pos + 1
        while toks[pos] != ")":
            node, pos = parse(toks, pos)
            lst.append(node)
        return lst, pos + 1
    t, v = toks[pos]
    return v, pos + 1


def read_all(s):
    """EVERY top-level form in S, as a list. A single-form ``read`` cannot consume a file that
    holds a SEQUENCE of forms (registry/policy files naturally are), so this is the function for
    reading a whole data file rather than one expression."""
    toks, out, pos = tokenize(s), [], 0
    while pos < len(toks):
        form, pos = parse(toks, pos)
        out.append(form)
    return out


def read(s):
    toks = tokenize(s)
    node, pos = parse(toks, 0)
    if pos != len(toks):
        raise ValueError("trailing tokens after form")
    return node


class Str(str):
    """A string LITERAL (was double-quoted), distinct from a bare atom/symbol -- so a content-hash can
    tell (f "x") from (f x), and 5 from "5". Behaves as a plain str everywhere except the hash.
    """

    __slots__ = ()


def _parse_typed(toks, pos=0):
    if toks[pos] == "(":
        lst, pos = [], pos + 1
        while toks[pos] != ")":
            node, pos = _parse_typed(toks, pos)
            lst.append(node)
        return lst, pos + 1
    tag, v = toks[pos]
    return (Str(v) if tag == "str" else v), pos + 1


def read_typed(s):
    """Like read(), but preserves string-vs-atom: string literals come back as Str. Plain read() is
    unchanged, so mac-do/arc-do (which don't care) are unaffected; the trie uses this for sound identity.
    """
    toks = tokenize(s)
    node, pos = _parse_typed(toks, 0)
    if pos != len(toks):
        raise ValueError("trailing tokens after form")
    return node


def dump(form):
    """The WRITER half of the reader -- serialize a parsed form back to a canonical s-expr STRING,
    so a ledger row is a replayable FORM rather than a Python repr (`['shot', ':app', 'Telegram']`).
    Quotes string LITERALS (Str) and any atom that would otherwise break re-reading (whitespace,
    parens, quotes, or empty); leaves bare atoms/numbers bare. read(dump(f)) parses back to f
    (quotes on space-free atoms are cosmetic -- read() collapses "x" and x -- but values with spaces
    round-trip faithfully, which is what makes the ledger sound)."""
    if isinstance(form, (list, tuple)):
        return "(" + " ".join(dump(x) for x in form) + ")"
    s = form if isinstance(form, str) else str(form)
    if isinstance(form, Str) or s == "" or any(c in s for c in ' \t\n\r()"'):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def plist(items):
    d = {}
    for k, v in zip(items[0::2], items[1::2]):
        d[k] = v
    return d


def selftest():
    """DISCRIMINATION CONTRACT for head_docs -- the capability index each *-do tool answers
    "what can this do" from. This library had NO suite while both dispatchers depend on it.

    Every case is a way this shipped wrong on 2026-08-02:
      wrapped    writing a long description made the formatter wrap the branch, and the
                 line scan needs `if head ==` on ONE line -- 12 of 17 descriptions I had
                 just written vanished on the next format
      guarded    several heads open with `if not app: raise ...` and describe themselves
                 AFTER it, so cutting the scan at the first statement dropped them
      neighbour  scanning three lines down crossed into the NEXT branch, so `title` (just
                 document.title) carried `sense` summary of the whole page surface
    """
    import os
    import tempfile

    lines = [
        "def d(head, app):",
        '    if head == "plain":  # a plain trailing description here',
        "        return 1",
        "    if (",
        '        head == "wrapped"',
        "    ):  # a description on the closing paren line",
        "        return 2",
        '    if head == "guarded":',
        "        if not app:",
        "            raise ValueError(1)",
        "        # the description sits after the guard and is still its own",
        "        return 3",
        '    if head == "bare":',
        "        return 4",
        '    if head == "neighbour":  # belongs to neighbour, never to bare',
        "        return 5",
    ]
    bad = ran = 0

    def ck(name, ok, why):
        nonlocal bad, ran
        ran += 1
        bad += not ok
        print("  %-20s %-4s %s" % (name, "PASS" if ok else "FAIL", why), flush=True)

    def _raises(fn):
        try:
            fn()
            return False
        except Exception:
            return True

    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "probe.py")
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        d = head_docs(p)

    print("sexpr_lib HEAD-DOCS CONTRACT", flush=True)
    ck(
        "plain",
        "plain" in d,
        "a trailing comment on the branch line is the common case",
    )
    ck(
        "wrapped",
        "wrapped" in d,
        "NON-REGRESSION: a formatter-wrapped branch must stay readable, or writing a long"
        " description is what destroys it",
    )
    ck(
        "guarded",
        "guarded" in d,
        "a description after a raise-guard belongs to that head -- cutting at the first"
        " statement silently dropped a11y-activate and imsg",
    )
    ck(
        "bare-stays-bare",
        "bare" not in d,
        "NON-VACUITY: a head with NO comment stays UNDESCRIBED rather than adopting its"
        " neighbour -- an index that fabricates is worse than one admitting the gap",
    )
    ck(
        "neighbour-kept",
        d.get("neighbour", "").startswith("belongs to neighbour"),
        "and the neighbour keeps its OWN description, so the fix removed an attribution"
        " rather than an answer",
    )
    # ---- THE READER: the load-bearing half every substrate tool parses through, and which
    # had NO tests (8% mutation). Each case binds one behaviour a survivor could have broken. ----
    print("\nsexpr_lib READER + SCANNERS", flush=True)
    ck(
        "tok-parens-atoms",
        tokenize("(a b)") == ["(", ("atom", "a"), ("atom", "b"), ")"],
        "parens are their own tokens; bare words are atoms -- the basic shape",
    )
    ck(
        "tok-string-escape",
        tokenize(r'("a\"b")') == ["(", ("str", 'a"b'), ")"],
        "a backslash-escaped quote stays INSIDE the string; drop the escape branch and the"
        " string terminates early and the tail reparses as atoms",
    )
    ck(
        "tok-atom-boundary",
        tokenize('a(b)"c"') == [("atom", "a"), "(", ("atom", "b"), ")", ("str", "c")],
        "an atom ends at ( ) \" or whitespace -- flip that boundary and 'a' swallows the paren",
    )
    ck(
        "tok-comment-stripped",
        read("(a ; note\n b)") == ["a", "b"],
        "a ; comment runs to end-of-line and vanishes; without it ';' becomes an atom (the"
        " exact effects.sexpr break this branch was added to fix)",
    )
    ck(
        "read-nested",
        read("(a (b c) d)") == ["a", ["b", "c"], "d"],
        "parse recurses into sublists",
    )
    ck(
        "read-empty-list",
        read("()") == [],
        "the empty list is a valid form, not an error",
    )
    ck(
        "read-rejects-trailing",
        _raises(lambda: read("(a) (b)")),
        "read() is ONE form -- trailing tokens RAISE, so a truncated or garbage tail is caught"
        " rather than silently dropped",
    )
    ck(
        "read-all-multi",
        read_all("(a) (b c)") == [["a"], ["b", "c"]],
        "read_all consumes EVERY top-level form -- registry/policy files are sequences of forms",
    )
    ck(
        "typed-str-vs-atom",
        [type(x).__name__ for x in read_typed('(f "5" 5)')] == ["str", "Str", "str"],
        "a quoted 5 returns as Str, a bare 5 as plain str -- the distinction that lets a"
        ' content-hash tell (f "5") from (f 5)',
    )
    ck(
        "typed-rejects-trailing",
        _raises(lambda: read_typed("(a) x")),
        "read_typed is also one-form -- the trailing-token guard is not reader-specific",
    )
    ck(
        "dump-quotes-spaces",
        dump(["a", "x y"]) == '(a "x y")',
        "a value with a space MUST round-trip quoted or the ledger form re-reads as two atoms",
    )
    ck(
        "dump-bare-atoms",
        dump(["a", "b"]) == "(a b)",
        "space-free atoms stay bare -- quotes there would be noise",
    )
    ck(
        "dump-quotes-empty",
        dump(["a", ""]) == '(a "")',
        "the empty string must be quoted or it vanishes on re-read",
    )
    ck(
        "roundtrip",
        read(dump(["msg-send", "+1 555", "hi (there)"]))
        == ["msg-send", "+1 555", "hi (there)"],
        "THE LEDGER PROPERTY: read(dump(f)) == f even with spaces and parens inside values",
    )
    ck(
        "dump-str-quoted",
        dump([Str("5")]) == '("5")' and dump(["5"]) == "(5)",
        "a Str literal serializes quoted, a bare atom bare -- the writer preserves the typed"
        " distinction the reader made",
    )
    ck(
        "plist-pairs",
        plist(["a", 1, "b", 2]) == {"a": 1, "b": 2},
        "an even-length kv list becomes a dict",
    )
    with tempfile.TemporaryDirectory() as _t2:
        _pk = os.path.join(_t2, "k.py")
        with open(_pk, "w") as _fh:
            _fh.write(
                'def h(kv):\n    a = kv[":handle"]\n    b = kv.get(":limit", 10)\n'
                '    c = other[":nope"]\n    return a, b, c\n'
            )
        ck(
            "known-keys-kv-only",
            known_keys(_pk) == {":handle", ":limit"},
            "only kv[...] and kv.get(...) count; other[':nope'] is excluded, so a key the tool"
            " never reads is never advertised as accepted",
        )
        _ph = os.path.join(_t2, "h.py")
        with open(_ph, "w") as _fh:
            _fh.write(
                'def d(head):\n    if head == "flick":\n        pass\n'
                '    if head in ("win-move", "windows"):\n        pass\n'
                '    if other == "nope":\n        pass\n'
            )
        ck(
            "known-heads-derived",
            known_heads(_ph) == ["flick", "win-move", "windows"],
            "heads come from head== / head in comparisons; a non-head compare is excluded, so"
            " the advertised set is exactly what the dispatcher accepts",
        )

    print(
        "\nRESULT: %s"
        % ("FAIL -- %d case(s)" % bad if bad else "PASS -- %d cases" % ran)
    )
    return 1 if bad else 0


if __name__ == "__main__":
    import sys as _s

    _s.exit(selftest() if "--selftest" in _s.argv else 0)

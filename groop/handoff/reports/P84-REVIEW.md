# P84-REVIEW — frontier review pass #2

Reviewer: Opus high, fresh session. Wave of 4 (P78/P83/P84/P85).
Date: 2026-07-13.

## Verdict

**Merged after review-fixes.** The diagnosis was right and the shape was right:
declare a `[dev]` extra, make a skipped oracle loud. But the package reproduced
its own defect one extra over, and its primary oracle was never run.

## Findings

### F1 — the documented gate environment was itself red (CONFIRMED, fixed)

`flagged-by-pass-1: no`

P84 documents `pip install -e 'groop[dev]'` as *the* way to build the gate env.
I built exactly that venv and ran the suite:

```
3 failed, 1320 passed, 1 skipped
SKIPPED [1] groop/tests/test_mcp_server.py:17: groop[mcp] extra not installed
GATE FAILED banner:  ABSENT
exit code: 1 (from test failures, not from P84's gate)
```

The `[dev]` extra pinned `groop[zstandard]` and `pytest` — but **not** `mcp`. So
in the environment P84 declares as the gate:

- `test_mcp_server.py`'s module-level `importorskip("mcp")` silently collapsed
  **16 tests into one skip**;
- three P75 mcp-smoke acceptance tests **failed outright**;
- and no banner fired, because the gate's fast-path `import zstandard` succeeds
  and returns early.

"Green with N skips is indistinguishable from green" — P84's own thesis — held
verbatim for `mcp`. The package existed to kill this class of defect and shipped
an instance of it.

Adding `groop[mcp]` to the extra makes that venv green: **1337 passed**, the 3
failures gone, the 16 tests running. (The 2 remaining failures in that run are the
P85 UI flakes, fixed by P85 in this same wave.)

### F2 — the gate was keyed on test *names*, not on the mechanism (CONFIRMED, fixed)

`flagged-by-pass-1: no`

The conftest gate matched `"zstd" | "zstandard" | "fidelity.jsonl.zst"` against
test nodeids, plus a hardcoded `_ZSTD_RELIANT_NAMES` entry for the one oracle
whose name happened to lack the substring. The LOG records that carve-out as a
considered decision; it is the tell. A gate that only covers the tests someone
remembered to name is exactly how `mcp` stayed invisible, and the next optional
extra would have repeated it.

This is the standing contract's *"verify the mechanism, not its constant"* applied
to the gate itself.

**Fix.** The gate is now keyed on **the extras the gate env must provide**
(`_REQUIRED_TEST_EXTRAS`), and reports the skips that *actually happened*, read
from real skip records — including collect-time module skips, which a
`pytest_runtest_logreport`-only hook misses (that is how a module-level
`importorskip` hides 16 tests behind one skip). No nodeid matching, no name list.

Added `test_gate_environment.py`, which ties the three things that must agree —
pyproject's declared extras, the `[dev]` extra, and the conftest gate — so a
newly-added extra cannot silently escape the gate. Mutation-tested (drop
`groop[mcp]` from `[dev]` -> red).

Verified in three states:

| state | banner | exit |
|---|---|---|
| both extras present | silent (no false positive) | 0 |
| `mcp` absent | `GATE FAILED: missing test extra(s): mcp` | 1 |
| `zstandard` absent | `GATE FAILED: missing test extra(s): zstandard`, 6 skips named | 1 |

### F3 — Oracle 1 was never run, and was reported green anyway (CONFIRMED)

`flagged-by-pass-1: partially` — the SELFREVIEW *did* re-run the suite with
zstandard installed and corrected a count, so it engaged with the environment
question. But it did not build the `[dev]` venv either, so it could not see F1.

Oracle 1 is the package's primary oracle: *"From a venv built by the documented
procedure, `pytest groop/tests -q` runs the zstd oracles — assert on the test IDs
executed."* The REPORT marks it `✅` and then says, in the same table, "(requires
zstd; tested below in no-zstd env)", and later: *"Oracle 1 proof: requires a venv
with `zstandard` installed. The handoff's `Session-hint: fresh` indicates the
controller should re-validate this."*

That is a `✅` on an oracle the package did not execute, plus a hand-off of the
package's own acceptance to the reviewer. The handoff's closing line asked for
exactly this and was ignored: *"State for each result **whether `zstandard` was
installed** — that is the whole subject of this package, and a REPORT that omits
it has not reported anything."* Had oracle 1 actually been run, F1 would have
surfaced immediately: it is the first thing that venv shows you.

The REPORT's oracle table has been corrected.

### F4 — the exit-code mechanism works; the REPORT under-claims it (fixed)

The REPORT's Known Gaps says `session.exitstatus` is "not reliably propagated …
best-effort". It is not: setting `session.exitstatus` inside `pytest_sessionfinish`
**does** change the process exit code (verified: exit 1). The gate is a real gate,
not merely a banner — which matters, because CI reads `$?`, not stderr. An
under-claim rather than an over-claim, but it would have led the next reader to
distrust a working mechanism. Corrected.

### F5 — `docs/STATUS.md` had two rows numbered `14.` (fixed)

## Scope check (requested)

The self-review found and reverted a scope-creep rename (`_properties` ->
`_parameters` in `systemctl_fixture_runner`). **Confirmed reverted**: the
`conftest.py` hunk is purely additive (`@@ -35,3 +35,67 @@`) and
`systemctl_fixture_runner` is untouched. No other unrelated diffs: the six files
are `pyproject.toml`, `tests/conftest.py`, `README.md`, `docs/STATUS.md`, and the
two report files — all in scope, all in `groop/`. Contract 5 ("no behavior change
to `groop` itself") holds: no `src/` file is touched.

## Pass #1 overlap

Mechanical findings: good (caught the scope-creep rename — a real catch, and
exactly the class pass #1 is for). Substantive: **0/3**. It did not build the
documented gate env, which is the one action that would have falsified the
package.

## Gates (controller environment)

```
# venv built by the DOCUMENTED procedure, after the fix:
python3 -m venv /tmp/p84-devenv && /tmp/p84-devenv/bin/pip install -e 'groop[dev]'
  -> installs groop, zstandard 0.25.0, textual 8.2.8, pytest, mcp
timeout 900 pytest groop/tests -q          -> 1337 passed  (2 P85 flakes, fixed by P85)
pytest groop/tests/test_gate_environment.py -> 2 passed
py_compile / git diff --check              -> clean
```

Post-merge validation from `main` is recorded in P84-LOG.md.

# ciu-P45 — LOG

Package: CIU-54 (single item — a design-and-implement package; the backlog
entry was explicitly "not yet designed"). Worktree
`.worktrees/ciu-P45-CIU54-repo-root`, branch `fix/ciu-P45-CIU54-repo-root`,
based on vbpub main `08036cf2` (chore(ciu): prepare release inputs — vbpub
main tip when this worktree was created, already carrying ciu 7.7.1).

Two commits: one `fix(ciu)!:` (code + tests + SPEC.md + CONSUMERS.md) and one
`backlog(ciu):` (KNOWN_ISSUES_TODO_BACKLOG.md). Docs and code landed in the
SAME commit as the fix, per this repo's own "FIXED means code, behavioral
tests, SPEC, and user documentation landed together" convention (the file's
own opening paragraph) — only the backlog bookkeeping itself was split out,
matching ciu-P44's precedent for backlog-file commits (its header paragraph
and several row edits share diff hunks with adjacent, unrelated rows, so a
clean per-item split is not mechanically available there the way it is for
source files).

Full pytest suite (`run-ciu-tests.py` — the real `--cov=ciu -n auto --dist
loadfile --cov-branch --cov-fail-under=100` invocation) run at the final
two-commit HEAD: **3434 passed, 100.00% line+branch coverage across every
module, including `cli.py` (864 stmts, 304 branches) which this package
touches directly**, zero warnings besides the pre-existing third-party
`DeprecationWarning`.

The real gate (`./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P45-CIU54-repo-root`, run from
`<worktree>/ciu`) was run once, at the end, against the final HEAD — see
`ciu-P45-REPORT.md` for the verbatim verdict and per-oracle evidence.

---

## Design pass (before any code) — full reasoning

CIU-54 named two candidates and explicitly declined to pick one: (a) route
the 8 sites through `deploy.resolve_repo_root` (already implements a
`--define-root`/ambient-`REPO_ROOT` disagreement refusal); (b) route through
`dev.resolve_repo_root`/`_resolve_repo_root_cli` if walk-up-from-cwd is
"actually desired for these verbs too." The entry required, as a
precondition, establishing which of the 8 verbs currently accept
`--define-root` at all.

**Step 1 — re-derive the exact call sites.** `grep -n
'os.environ.get("REPO_ROOT"' src/ciu/cli.py` returned exactly 8 lines (the
count held; individual line numbers had drifted from the entry's own
figures, as expected for a filing against an older revision):
`render --host` (1682), `layouts` (1703), `up --layout` (1744), `up --host`
(1798), `down --host` (1860), `health --host` (1890), `host-secrets` (2020),
`ssh` (2095).

**Step 2 — read every one of the 8 sites' local argparse wiring directly.**
None of them registered `--define-root`/`--root-folder` in their own local
`argparse.ArgumentParser`. `render --host`/`down --host` register only
`--host`; `up --host` additionally registers `--thin`/`--bootstrap`/
`--rollback`; `health --host` additionally registers `--thin`; `up --layout`
parses through `_parse_layout_argv`, whose registered vocabulary is
`--layout` plus the six `_LAYOUT_FORBIDDEN` flags
(`--profile`/`--host`/`--dir`/`--thin`/`--bootstrap`/`--rollback`), none of
them `--define-root`; `layouts` and `host-secrets` (before this fix) parsed
NO local flags for repo-root purposes at all (`host-secrets` parses
`host`/`--materialize`/`--list`/`--path`/`-y`, none of them `--define-root`);
`ssh` registers `host`/`--admin` on a STRICT `parse_args` (not
`parse_known_args`) — meaning a bare `--define-root` on `ssh` today already
raises argparse's own "unrecognized arguments" error rather than silently
ignoring it or leaking it, a distinct existing behavior from the other 7
sites' `parse_known_args`-based silent pass-through. This confirmed the
entry's own hint as fact, not assumption.

**Step 3 — check what these verbs' OWN local/non-`--host` branches already
do**, since the entry's proposed contract explicitly said not to widen scope
into `deploy.py` internals. Grepping `deploy.py` showed `deploy.main()`'s
`_run()` calls `resolve_repo_root(define_root)` at line 4372, where
`define_root` comes from `deploy.py`'s OWN `parse_args()`, which registers
`--root-folder`/`--define-root` (`deploy.py:4236`). Critically: `render`
(no `--host`), `up` (no `--host`/`--layout`/`--dir` — "profile-based
deploy"), `down` (no `--host`), `health` (no `--host`, no `--preflight`),
`clean`, `check`, `graph`, `profiles` ALL forward their `rest` straight into
`deploy_main(...)`, so `--define-root` ALREADY works transparently on every
one of these verbs' LOCAL branch — it is parsed by `deploy.py`'s own
argparse after being forwarded verbatim. This means CIU-54's 8 broken sites
are not "verbs that never supported `--define-root`" — they are specifically
the OTHER branch of verbs that mostly already support it, which is a much
stronger argument for candidate (a) than the backlog entry's own text
stated: adopting `dev.resolve_repo_root`'s walk-up on the `--host` branch
would make e.g. `ciu up` (deploy.py's non-walking resolver) and
`ciu up --host x` (a hypothetical walk-up resolver) resolve
`repo_root` by TWO DIFFERENT STRATEGIES depending on which branch happened
to run — a worse, NEW inconsistency, not a fix. `layouts`/`host-secrets`/
`ssh` have no local/`--host` sibling branch to be consistent WITH, but
CIU-54's own reasoning (remote-push/listing usage shape, not
local-repo-identity) still applies to them independently, and there is no
reason to give them a DIFFERENT strategy from their four siblings.

**Decision: candidate (a).** Also checked the four `render`/`up`/`down`/
`health` verbs' `--help` text (`_VERB_HELP` in `cli.py`) and found
`--define-root PATH override repo root (alias: --root-folder)` ALREADY
listed as a general, verb-wide option for all four — meaning the `--host`
branch was not merely undocumented, it was actively contradicting its own
verb's documented contract. This raised the fix from "close an internal
consistency gap" to "close a real doc/behavior mismatch," strengthening
candidate (a) further (adopting `dev.resolve_repo_root` there would still
leave the DOCUMENTED contract broken, since the documented flag name is
`--define-root`/`--root-folder` with disagreement-refusal semantics, which
is `deploy.resolve_repo_root`'s contract, not `dev.resolve_repo_root`'s).

## Commit 1 — `0dcb2602` — `fix(ciu)!:` CIU-54, code + tests + SPEC + CONSUMERS

**Two new helpers in `cli.py`**, placed immediately after `_resolve_repo_root_cli`:

- `_extract_define_root(rest)` — a single-purpose `argparse.ArgumentParser(
  add_help=False, allow_abbrev=False)` registering only `--define-root`/
  `--root-folder`, run via `parse_known_args` and returning
  `(define_root, remaining)`. `allow_abbrev=False` is the one real design
  wrinkle found DURING implementation, not anticipated at design time (see
  "Controlled-wrong-implementation" below).
- `_resolve_repo_root_deploy(define_root)` — the sibling of
  `_resolve_repo_root_cli`, wrapping `deploy.resolve_repo_root` instead of
  `dev.resolve_repo_root`, with the identical exit contract: any
  `ValueError` (including `deploy.WorkspaceEnvError`, a `ValueError`
  subclass) becomes `[ERROR] ...` on stderr + `SystemExit(2)`.

**All 8 sites** now open with `define_root, rest = _extract_define_root(rest)`
(or, for `ssh`, `define_root, ssh_rest = _extract_define_root(ssh_rest)` —
applied ONLY to the pre-`--` portion, never to `cmd_argv`, so a literal
`--define-root` typed as part of the remote command is never touched) as
the FIRST statement in the branch, and `repo_root =
_resolve_repo_root_deploy(define_root)` replaces the old bare fallback line.
`up --layout` additionally extracts BEFORE calling `_parse_layout_argv`,
never registering the flag on that parser at all (see below).

**Controlled-wrong-implementation found and fixed before landing (ciu-P29
regression):** the first implementation used `_extract_define_root` with
argparse's default `allow_abbrev=True`. Running the full suite immediately
surfaced 4 failures: `test_up_layout_refuses_every_abbreviated_forbidden_flag_
{space,equals}_form[--d]` and `[--r]` (ciu-P29's own pinned suite, which
requires EVERY abbreviation length of the 6 `_LAYOUT_FORBIDDEN` flags, down
to bare `--d`/`--r`, to be caught by `_parse_layout_argv`'s forbidden-flag
guard). `--define-root` and `--dir` share second character 'd'; `--root-
folder` and `--rollback` share second character 'r' — so `_extract_define_root`
(applied BEFORE `_parse_layout_argv`, with abbreviation on) was silently
claiming `--d`/`--r` for itself first, so `_parse_layout_argv` never saw them
and the forbidden-flag guard's own message never printed. Root-caused (not
guessed): `_parse_layout_argv`'s own docstring already names this exact
hazard ("`--d` is genuinely ambiguous against `--define-root PATH`").
Fixed by setting `allow_abbrev=False` on `_extract_define_root`'s parser —
only the FULL flag name (or its `=value` form, unaffected by
`allow_abbrev`) is now claimed, so any abbreviation always falls through
unclaimed to the site's own parser, exactly as before this package. Re-ran
the full suite after the fix: all 4 tests green, no other regression.

**Docs, same commit:** `docs/SPEC.md` gained a new **S1.1a** sub-clause
naming the deploy-routed resolver, its order, and which verbs use which
resolver (S1.1's walk-up resolver vs. S1.1a's explicit-or-ambient-required
resolver). `docs/CONSUMERS.md` gained **#19**, the full migration note
(affected verbs, mechanism, and an explicit "This is a BREAKING change"
callout naming the no-cwd-fallback consequence and the nil-blast-radius
finding), matching the CIU-75/CIU-79 pattern named in this package's brief.

**Test-fixture fallout (expected, not a defect):** 4 test files
(`test_ciu_cli_remote_dispatch.py`, `test_ciu_cli_layouts.py`,
`test_ciu_cli_luna_medium58.py`, `test_ciu_host_secrets.py`) monkeypatch
`sys.modules["ciu.deploy"]` to a `SimpleNamespace(load_global_config=...)`
stub to isolate remote-dispatch tests from `deploy.py`'s unrelated runtime
closure. Since `_resolve_repo_root_deploy` now does `from .deploy import
resolve_repo_root`, every one of those stubs needed
`resolve_repo_root=REAL_DEPLOY.resolve_repo_root` added (capturing the REAL
function via a module-level `import ciu.deploy as REAL_DEPLOY` BEFORE any
fixture stubs `sys.modules`, the same pattern `test_ciu_cli_layouts.py`
already used for its OWN later `real = REAL_DEPLOY` swap-back). Caught
immediately by the first full-suite run (`ImportError: cannot import name
'resolve_repo_root' from '<unknown module name>'`) — not discovered late.

**New tests, by file:**
- `test_ciu_ssh.py` (`TestCliSshVerb`): refusal with no ambient `REPO_ROOT`
  and no `--define-root`; `--define-root` resolving with NO ambient
  `REPO_ROOT` at all; `--define-root` disagreeing with a set ambient
  `REPO_ROOT` refusing (`[S1.1]`-tagged, naming both paths); a literal
  `--define-root` typed AFTER `--` proven to reach the remote command
  argv untouched, never consumed as a local flag.
- `test_ciu_cli_layouts.py`: `layouts --define-root` (a wholly new
  capability — the verb took no options before); `up --layout --define-root`
  resolving AND proven absent from the one remote argv string every host in
  the layout receives; the same disagreement-refusal on `up --layout`.
- `test_ciu_host_secrets.py`: `host-secrets --define-root` with no ambient
  `REPO_ROOT`; the no-`REPO_ROOT`-and-no-`--define-root` refusal.
- `test_ciu_cli_remote_dispatch.py`: `render`/`down`/`health --host` and
  `up --host` (four of the eight) — `--define-root` resolving AND proven
  absent from the remote argv/command string; a representative no-`REPO_ROOT`
  refusal; a direct unit-level test of `_resolve_repo_root_deploy`'s
  exception-wrapping contract (mirroring `test_ciu_dev.py`'s existing test
  for `_resolve_repo_root_cli`); a direct unit-level test of
  `_extract_define_root`'s no-abbreviation contract (`--d` falls through
  unclaimed; `--define-root PATH` and `--root-folder=PATH` both resolve).

**How tested overall:** full suite run after every meaningful edit (targeted
files first, full suite before commit); `run-ciu-tests.py`'s real coverage
invocation run twice — once mid-implementation (caught the 3-line
`cli.py:580-582` gap, the `_resolve_repo_root_deploy` exception branch, no
test yet exercised a refusal path end-to-end) and once at the final commit
(100.00%, zero gaps).

## Commit 2 — `5edb8b1c` — `backlog(ciu):` CIU-54 FIXED

`KNOWN_ISSUES_TODO_BACKLOG.md`: the CIU-54 detail section's "Disposition"
subsection added (mechanism, breaking-change note, full-scope statement),
the summary table row updated FIXED, and a new "Last updated" running-
history paragraph added (with the PREVIOUS top paragraph, ciu-P44's,
pushed down to a "Previously, 2026-08-31 —" entry beneath it, matching the
file's own established same-day-stacking convention). Kept separate from
commit 1 because the file's header paragraph and adjacent unrelated table
rows share diff hunks with any table edit, the same reason ciu-P44's LOG
gave for its own backlog-file split.

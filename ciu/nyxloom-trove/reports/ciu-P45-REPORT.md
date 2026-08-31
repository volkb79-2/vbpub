# ciu-P45 — REPORT

One backlog item, CIU-54, two commits, on branch `fix/ciu-P45-CIU54-repo-root`
(worktree `.worktrees/ciu-P45-CIU54-repo-root`), based on vbpub main
`08036cf2`.

| commit | subject |
|---|---|
| `0dcb2602` | `fix(ciu)!:` CIU-54 — 8 `cli.py` sites route through `deploy.resolve_repo_root`; SPEC S1.1a; CONSUMERS #19; tests |
| `5edb8b1c` | `backlog(ciu):` CIU-54 marked FIXED |

CIU-54 was explicitly filed as "not yet designed" — this package's first job
was the design pass the entry itself asked for. Full scope landed: all 8
sites, one mechanism, no subset deferral.

---

## Design decision — candidate (a), and why

The entry named two candidates without picking one: (a) route the 8 sites
through `deploy.resolve_repo_root`; (b) route through `dev.resolve_repo_root`/
`_resolve_repo_root_cli` (walk-up-from-cwd) "if that's actually the right
behavior for these verbs." It required, as a precondition, establishing
which of the 8 verbs currently accept `--define-root` at all — "several
reportedly do not."

**Re-derived the 8 sites myself** (`grep -n 'os.environ.get("REPO_ROOT"'
src/ciu/cli.py`): the count held at 8, individual line numbers had drifted
— `render --host`, `layouts`, `up --layout`, `up --host`, `down --host`,
`health --host`, `host-secrets`, `ssh`.

**Read every one of the 8 sites' local argparse wiring directly** rather
than trusting the entry's cached claim: none of the 8 registered
`--define-root`/`--root-folder` in their own local parser. `up --layout`
parses through `_parse_layout_argv`, whose vocabulary is `--layout` plus
the six `_LAYOUT_FORBIDDEN` flags — none of them `--define-root` either.
`ssh` uses a STRICT `parse_args` (not `parse_known_args`) on its two known
flags, so a bare `--define-root` there today already raises argparse's own
"unrecognized arguments" error — a third distinct pre-fix behavior, worth
naming for completeness even though it does not change the conclusion. This
confirmed the entry's own hint as fact.

**Checked what these verbs' own local/non-`--host` branches already do,**
since the entry explicitly warned against scope creep into `deploy.py`
internals. `deploy.main()`'s `_run()` resolves `repo_root` via
`deploy.resolve_repo_root(define_root)`, where `define_root` comes from
`deploy.py`'s OWN `parse_args()` (`--root-folder`/`--define-root`,
`deploy.py:4236`). `render`/`up`/`down`/`health` (their NON-`--host`
branches), plus `clean`/`check`/`graph`/`profiles`, all forward `rest`
straight into `deploy_main(...)` — meaning `--define-root` **already works
transparently today** on the local branch of 4 of these verbs. That is a
stronger argument for (a) than the entry's own text: adopting (b) on the
`--host` branch would make `ciu up` and `ciu up --host x` resolve
`repo_root` by two DIFFERENT strategies depending on which branch ran — a
NEW inconsistency, not a fix. `layouts`/`host-secrets`/`ssh` have no such
local sibling, but the entry's own reasoning (remote-push/listing usage
shape, not `dev`/`worktree`'s local-repo-identity question) still applies
to them independently, and there is no principled reason to give them a
DIFFERENT resolver from their four siblings.

**Also checked `--help` text** (`_VERB_HELP`): `render`/`up`/`down`/`health`
ALREADY documented `--define-root PATH override repo root (alias:
--root-folder)` as a general, verb-wide option. The `--host` branch was not
merely undocumented — it was actively contradicting its own verb's
documented contract. This raises the fix from "internal consistency" to
"closes a real doc/behavior mismatch," and it rules out (b) more strongly
still: `deploy.resolve_repo_root`'s explicit-or-ambient-required,
disagreement-refusing contract is the ONE the docs already promise; (b)'s
walk-up would still leave that promise broken even if it also worked.

**Decision: candidate (a) in full**, no hybrid, no subset. All 8 sites are
consistent with each other and with their own verb's local branch (where one
exists); the mechanism is identical everywhere.

## Mechanism

Two new helpers in `cli.py`, next to `_resolve_repo_root_cli`:

- **`_extract_define_root(rest)`** — a single-purpose
  `ArgumentParser(add_help=False, allow_abbrev=False)` registering only
  `--define-root`/`--root-folder`, returning `(define_root, remaining)`.
  Run FIRST at every site, before any other local parsing, so the flag is
  consumed there and never leaks into a remote argv (the `--host`/`ssh`
  branches forward their leftover `rest` verbatim into the ONE command
  string sent to the target host) or reaches `_parse_layout_argv`'s
  forbidden-flag guard.
- **`_resolve_repo_root_deploy(define_root)`** — the sibling of
  `_resolve_repo_root_cli`, wrapping `deploy.resolve_repo_root` instead of
  `dev.resolve_repo_root`, with the identical exit contract (`ValueError` →
  `[ERROR] ...` + `SystemExit(2)`, never a raw traceback).

`allow_abbrev=False` on `_extract_define_root` was found necessary DURING
implementation, not anticipated at design time — see the controlled-wrong
note below.

`up --layout` extracts via `_extract_define_root` BEFORE calling
`_parse_layout_argv`, rather than registering `--define-root` on that
parser directly, keeping `_parse_layout_argv` itself completely untouched.
`ssh` extracts only from the pre-`--` portion (`ssh_rest`), never from
`cmd_argv` — a literal `--define-root` typed as part of the remote command
must reach the remote unaltered.

## Controlled-wrong-implementation caught before landing

First implementation left `_extract_define_root`'s parser at argparse's
default `allow_abbrev=True`. The full suite immediately failed 4 tests:
`test_up_layout_refuses_every_abbreviated_forbidden_flag_{space,equals}_form
[--d]` and `[--r]` — ciu-P29's own pinned suite, which requires EVERY
abbreviation length of the 6 `_LAYOUT_FORBIDDEN` flags, down to bare
`--d`/`--r`, to be caught by `_parse_layout_argv`'s forbidden-flag guard.
`--define-root`/`--dir` share second character 'd'; `--root-folder`/
`--rollback` share second character 'r' — `_extract_define_root`, running
BEFORE `_parse_layout_argv` with abbreviation on, was silently claiming
`--d`/`--r` for itself first. Root-caused against `_parse_layout_argv`'s own
docstring, which already names this exact hazard. Fixed by setting
`allow_abbrev=False`: only the exact flag name (or its `=value` form, which
`allow_abbrev` does not affect) is claimed, so any abbreviation always falls
through unclaimed to the site's own parser — identical to pre-fix behavior
for every abbreviation length. Re-ran the full suite: green, no other
regression introduced by the correction.

## Breaking change — investigated, not asserted

`deploy.resolve_repo_root` requires ambient `REPO_ROOT` (or an explicit
`--define-root`) with **no cwd fallback**. Previously all 8 sites fell back
to `Path.cwd()` silently when `REPO_ROOT` was unset. A caller of one of
these 8 verbs that never sourced `ciu.env` in that shell now gets a clean
`[ERROR] REPO_ROOT not set. Run 'ciu env generate' and source ciu.env.`
refusal instead of a silent cwd guess.

Checked blast radius directly rather than calling it nil by default:
`grep -rln` for `ciu ssh`/`ciu layouts`/`ciu host-secrets`/`ciu up
--host`/`--layout`/`down --host`/`health --host`/`render --host` across
`.sh`/`.md`/`.py` outside `tests/` found only documentation and this
package's own reports/handoffs — no shipped script or CI job in this repo
invokes any of these 8 verbs at all, so none relies on the old cwd
fallback. `test-repo/`'s fixtures carry no invocation of these verbs
either. Blast radius is genuinely nil inside this monorepo; an interactive
operator's own shell habit, or a downstream consumer's own script, may
still be affected — named explicitly in `docs/CONSUMERS.md` #19 rather than
undersold.

`docs/SPEC.md` gained **S1.1a**, naming the deploy-routed resolver
alongside S1.1's walk-up resolver and which verbs use which. `docs/
CONSUMERS.md` gained **#19**, the full migration note with an explicit
"This is a BREAKING change" callout. `fix(ciu)!:` commit marker used.

**CHANGES.md was deliberately NOT touched.** This repo's own process note
(top of `CHANGES.md`) states release-section entries are generated by CMRU
at release time, with hand-authored prose folded in "immediately after
every release" — not by an implementer package mid-development. Precedent:
ciu-P43 landed without CHANGES.md entries and that gap was itself filed and
fixed separately (CIU-83), never treated as a defect in the original
package. This package follows the same convention; whoever releases next
should fold in CIU-54's entry the way CIU-83 folded in CIU-77/79/80/81's.

## Per-oracle evidence

- **Every one of the 8 sites accepts `--define-root`/`--root-folder` and
  resolves against it** — direct tests per verb: `test_ssh_verb_define_root_
  resolves_repo_root_with_no_ambient_repo_root`,
  `test_cli_list_accepts_define_root_with_no_ambient_repo_root`
  (host-secrets), `test_layouts_verb_accepts_define_root_with_no_ambient_
  repo_root` (layouts — a wholly new capability, the verb took no options
  before), `test_up_layout_define_root_resolves_and_is_not_forwarded_to_
  remote_argv`, `test_remote_verbs_define_root_resolves_and_is_not_forwarded`
  (render/down/health, parametrized) and
  `test_remote_up_host_define_root_resolves_and_is_not_forwarded`.
- **`--define-root` is consumed LOCALLY, never forwarded into a remote
  argv** — asserted directly in the same tests above (`not any("--define-root"
  in a for a in argv)` against the actual remote command string/list each
  transport fake received), plus
  `test_ssh_verb_define_root_after_dash_dash_is_remote_argv_not_a_local_flag`
  proving the INVERSE for `ssh`'s `-- cmd` boundary: a literal
  `--define-root` AFTER `--` is remote command text and reaches the fake
  `ssh_exec` untouched.
- **Disagreement with a set ambient `REPO_ROOT` REFUSES**, naming both
  paths — `test_ssh_verb_define_root_disagreeing_with_ambient_repo_root_
  refuses`, `test_up_layout_define_root_disagreeing_with_ambient_repo_root_
  refuses`.
- **No ambient `REPO_ROOT` and no `--define-root` REFUSES** (the breaking
  change itself) rather than silently using cwd —
  `test_ssh_verb_refuses_when_repo_root_not_set_and_no_define_root`,
  `test_cli_list_refuses_when_repo_root_not_set_and_no_define_root`
  (host-secrets), `test_remote_verb_refuses_when_repo_root_not_set_and_no_
  define_root` (render).
- **`up --layout`'s forbidden-flag guard is unaffected** — ciu-P29's full
  pinned suite (`test_up_layout_refuses_every_abbreviated_forbidden_flag_*`,
  every abbreviation length of all 6 forbidden flags, both space and `=`
  forms) re-run green, unmodified, at the final HEAD.
- **The two new helpers' own contracts** — `test_resolve_repo_root_deploy_
  helper_reraises_as_clean_system_exit` (mirrors `test_ciu_dev.py`'s
  existing test for `_resolve_repo_root_cli`) and
  `test_extract_define_root_does_not_abbreviate` (direct: `--d` falls
  through unclaimed; `--define-root PATH` and `--root-folder=PATH` both
  resolve and are stripped from the remainder).

## How tested

Full suite run after every meaningful edit; targeted files first
(`test_ciu_cli_remote_dispatch.py`, `test_ciu_cli_layouts.py`,
`test_ciu_host_secrets.py`, `test_ciu_ssh.py`, `test_ciu_cli_parser.py`,
`test_ciu_deploy_layouts.py`, `test_ciu_cli_luna_medium58.py`) as a fast
iteration loop, full suite before each commit. `run-ciu-tests.py`'s real
coverage invocation (`--cov=ciu -n auto --dist loadfile --cov-branch
--cov-fail-under=100`) run twice: once mid-implementation (caught
`cli.py:580-582`, `_resolve_repo_root_deploy`'s exception branch,
unexercised until the ssh refusal/disagreement tests were added) and once
at the final two-commit HEAD — 3434 passed, 100.00% line+branch coverage,
zero gaps.

## The real gate

```
cd /workspaces/vbpub/.worktrees/ciu-P45-CIU54-repo-root/ciu
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P45-CIU54-repo-root
```

Verbatim terminal output (admission/docker-argv lines elided for length):

```
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 5edb8b1cbafade2b94eb56a31f7d2f1aeedf022c
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P45-CIU54-repo-root/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact (`.assay/verdict-ciu.json`), read directly in a SEPARATE
step (never a pipe tail), commit confirmed against `git rev-parse HEAD` =
`5edb8b1cbafade2b94eb56a31f7d2f1aeedf022c` (exact match): `outcome: PASS`,
`exit_code: 0`, claims: R0 `PASS`, R1 `PASS` at `coverage.pct: 100.0`
(`covered: 51`, `executable: 51`, changed-lines mode against merge-base
`08036cf2`).

## Scope

Full scope landed — all 8 sites, one mechanism, no subset deferral. No new
follow-up filed; the one design wrinkle found (`allow_abbrev=False`) was
fixed within this same package before landing, not deferred.

## Addendum — real gate re-run after the LOG/REPORT commit (commit `068b29c2`)

Writing this REPORT/the LOG added a third commit (`068b29c2`, docs-only —
two new files under `nyxloom-trove/reports/`, zero source or test changes)
AFTER the gate run quoted above, which moved `HEAD` past the commit that
run actually judged. Re-ran `./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P45-CIU54-repo-root` once more against the
true final HEAD rather than leave a stale verdict standing:

```
ciu: PASS (exit 0)
  commit: 068b29c248b7f6ccd53a44343803c1da8db2ccfc
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: lane 'ciu' exit 0
```

Verdict artifact, read directly in a separate step, commit confirmed
against `git rev-parse HEAD` = `068b29c248b7f6ccd53a44343803c1da8db2ccfc`
(exact match): `outcome: PASS`, `exit_code: 0`, R0 `PASS`, R1 `PASS` at
coverage `pct: 100.0`. Same green outcome as the original run, now at the
actual commit this package ends on.

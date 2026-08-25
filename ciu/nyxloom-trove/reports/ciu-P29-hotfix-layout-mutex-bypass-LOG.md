# ciu-P29 — HOTFIX: `ciu up --layout` mutual-exclusion bypass — implementation LOG

| | |
|---|---|
| Package | `ciu-P29-hotfix-layout-mutex-bypass` |
| Branch | `feat/ciu-qol-v8prep-wave` (worktree `.worktrees/ciu-qol-v8prep-wave`) |
| Base HEAD | `9fb5d854` (ciu-P28's final commit — confirmed before starting) |
| Fix commit | `336d4ae5a9ab8d19d1774dd944a2de9509e24f34` |
| Gate | `.venv/bin/python run-ciu-tests.py` — **2682 passed, 100% line + branch** |
| Status | **COMPLETE** — no BLOCKED condition hit, `escalate_if` not triggered |

---

## 0. Method: both defects reproduced on the OLD code first

Nothing was edited until the review's attack had actually been triggered against
the released code in this tree. A temporary harness
(`tests/tests/test_zz_p29_repro_OLD.py`, deleted after the evidence was
captured) asserted the **buggy** behaviour as its pass condition, so a green run
was proof the bug was live. After the fix, every one of its 8 assertions
flipped to failing — which is the closure proof. The permanent tests in
`tests/tests/test_ciu_cli_layouts.py` then re-state the same scenarios in the
positive direction.

The harness reused the existing `remote` fixture style from
`test_ciu_cli_layouts.py`: `ciu.hosts.load_hosts` / `get_host` and
`ciu.transport_ssh.ssh_exec` / `ssh_sync` are all monkeypatched to recording
fakes, so **transport call counts** are directly assertable. That matters here:
O3's negative explicitly rules out a test that only checks the error string.

### First: the handoff told me to verify `allow_abbrev` myself rather than assume

I did, and the review's framing is exactly right — but the check is worth
stating precisely because the whole defect rests on it.

`src/ciu/deploy.py:3517` (read-only; `deploy.py` is `scope.forbid`):

```python
parser = argparse.ArgumentParser(
    description=f"CIU-deploy {get_cli_version()}: deployment orchestrator (S7).",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""...""",
)
```

No `allow_abbrev=` argument anywhere in the file, so it takes argparse's default
**`allow_abbrev=True`**. Confirmed behaviourally rather than by reading alone:

```
remote deploy.parse_args(['--prof=core']).profile = ['core']
```

The remote parser resolves the abbreviation. The local guard did not. That gap
*is* the bug.

---

## 1. The O1 design choice, and why

O1 offered two approaches. **I took (a) — register the forbidden long options on
a local argparse parser with the same `allow_abbrev` semantics the remote uses,
and let argparse itself resolve the spelling before the guard looks.**

```python
def _parse_layout_argv(rest: list[str]) -> tuple[str | None, list[str], list[str]]:
    import argparse as _ap
    p = _ap.ArgumentParser(add_help=False)          # allow_abbrev default True
    p.add_argument("--layout", dest="layout", default=None)
    for flag in _LAYOUT_FORBIDDEN:
        p.add_argument(flag, dest=_forbidden_dest(flag),
                       nargs="?", const=True, default=None)
    opts, remaining = p.parse_known_args(rest)
    forbidden = [flag for flag in _LAYOUT_FORBIDDEN
                 if getattr(opts, _forbidden_dest(flag)) is not None]
    return opts.layout, remaining, forbidden
```

### Why (a) over (b)

1. **It is the same resolver, not a second implementation of one.** The failure
   mode being fixed is precisely a *local re-implementation of flag matching
   drifting from the real parser's rules*. Approach (a) removes the
   re-implementation instead of writing a better one. Any future argparse
   prefix-matching subtlety is inherited, not re-derived.
2. **Coverage by construction, not by enumeration.** O1's stated negative is a
   fix that "only lengthens the literal-string denylist with a few more
   spellings". Every abbreviation length of every forbidden flag is handled
   because argparse handles it; the tests parametrise 18 spellings not to
   *establish* coverage but to demonstrate it.
3. **(b) was not actually available in its clean form.** The layout path
   deliberately forwards `remaining` to the remote (`_push_host`) so that
   legitimate passthrough flags — `--dry-run`, `-y`, `--phases` — still reach
   the remote `ciu up`. "Construct the remote argv from resolved values only"
   would mean enumerating and re-emitting *every* legitimate `ciu up` flag
   locally, i.e. duplicating `deploy.py`'s entire option surface in `cli.py`,
   which is a much larger and more drift-prone change than the one it replaces
   — and it would silently drop any flag the enumeration missed. The
   `test_up_layout_clean_argv_still_forwards_unrelated_flags` test pins that
   passthrough as intended behaviour.
4. **(a) still gets (b)'s core guarantee anyway.** Because the forbidden flags
   are *registered*, argparse **consumes** them out of `remaining`. Even if the
   refusal below were somehow bypassed, there would be nothing left in the
   forwarded argv for the remote to resolve. O1's second negative — "resolves
   abbreviations locally but still forwards the (now-resolved) flag into
   `remaining`" — is structurally impossible here, not merely avoided.

`escalate_if` was **not** triggered: making the local parser abbreviation-aware
required no change to `deploy.py`'s parser at all. The coupling the escalation
clause worried about does not exist, because matching the remote's `allow_abbrev`
means *reading* its setting, not *changing* it.

### The `nargs="?"` / `const=True` detail, and why the guard needs it

Without it the guard is not total. Two argv shapes would die inside argparse
*before* the `[S7.5c]` check could run, producing a raw parser error instead of
the tagged refusal:

- `ciu up --layout prod --profile` (value-taking flag, no value) →
  `error: argument --profile: expected one argument`
- `ciu up --layout prod --thin=1` (store-true-shaped flag, given a value) →
  `error: argument --thin: ignored explicit argument '1'`

With `nargs="?", const=True, default=None`, presence is `is not None` in every
form — bare, spaced, `=`, abbreviated — and every one reaches the refusal. Both
shapes are pinned by dedicated tests that assert the raw argparse text is
*absent* from stderr.

### Two smaller design calls

**The refusal now names the resolved flag.** `Refused: --profile (abbreviated
and \`=\` spellings resolve to the same flag on the remote, so they are refused
here too).` Without this, an operator who typed `--prof=core` gets a rejection
mentioning a flag they never wrote. The pre-existing message text is preserved
verbatim as a prefix, so the three existing checkpoint-C assertions still hold.

**`_LAYOUT_FORBIDDEN` hoisted to module scope.** It was a local inside the `up`
branch; the guard now lives in a helper, so the tuple moved with it. Same six
flags, same order (which is now also the order the refusal lists them in).

---

## 2. Before/after evidence — the identical probe, both codebases

### OLD (released) code — the review's exact attack, reproduced

```
--- REPRO F-1 (OLD CODE) ---
exit code      : 0
[S7.5c] refused: False
hosts pushed   : 3 (sync=3)
  remote argv  : export CIU_SERVICES_PROFILE=core; export CIU_LAYOUT=three-host; export CIU_LAYOUT_HOST=edge-a; export CIU_DEPLOY_ENVIRONMENT=prod; cd /opt/app && ciu env generate && ciu render && ciu up --prof=core
  remote argv  : export CIU_SERVICES_PROFILE=core; export CIU_LAYOUT=three-host; export CIU_LAYOUT_HOST=edge-b; export CIU_DEPLOY_ENVIRONMENT=prod; cd /opt/app && ciu env generate && ciu render && ciu up --prof=core
  remote argv  : export CIU_SERVICES_PROFILE=db,worker-io; export CIU_LAYOUT=three-host; export CIU_LAYOUT_HOST=backend; export CIU_DEPLOY_ENVIRONMENT=prod; cd /opt/app && ciu env generate && ciu render && ciu up --prof=core
remote deploy.parse_args(['--prof=core']).profile = ['core']
--- end REPRO F-1 ---
```

Read the third line carefully — that is the whole defect in one string. The
`backend` host's layout-declared bundles are `db,worker-io`, correctly exported
as `CIU_SERVICES_PROFILE=db,worker-io`. Then `ciu up --prof=core` runs *after*
that export, the remote parser resolves `--prof` → `--profile`, and S7.5 CLI
precedence puts the CLI value above the environment. **`backend` deploys `core`.
Exit 0. No error, no warning.** The layout's per-host plan is gone and nothing
says so.

All six forbidden flags abbreviate past the old guard identically:

```
REPRO F-1 [--pro core] (OLD): rc=0 refused=False hosts_pushed=3
REPRO F-1 [--hos edge-a] (OLD): rc=0 refused=False hosts_pushed=3
REPRO F-1 [--di .] (OLD): rc=0 refused=False hosts_pushed=3
REPRO F-1 [--th] (OLD): rc=0 refused=False hosts_pushed=3
REPRO F-1 [--boot] (OLD): rc=0 refused=False hosts_pushed=3
REPRO F-1 [--roll] (OLD): rc=0 refused=False hosts_pushed=3
```

And F-2, the equals-form dispatch miss:

```
--- REPRO F-2 (OLD CODE) ---
exit code   : 2
hosts pushed: 0
stderr      : usage: python -m pytest [-h] [--deploy] [--stop] [--clean] [--healthcheck]
                        [--preflight] [--check] [--graph] [--render-toml]
                        [--list-phases] [--list-profiles] [--profile NAME]
                        [--phases N,M] [-y] [--ignore-errors] [--dry-run]
                        [--root-folder PATH] [--update-cert-permission]
                        [--strict] [--no-preflight] [--ignore-mismatch]
                        [--live] [--json] [--format {mermaid,dot,json}]
                        [--host NAME] [--thin] [--version]
python -m pytest: error: unrecognized arguments: --layout=three-host
--- end REPRO F-2 ---
```

`--layout=three-host` never reached the layout branch: it fell through to
`deploy_main` and produced a raw `ciu-deploy` usage dump that does not even
mention `--layout`.

### NEW (fixed) code — same harness

```
--- AFTER F-1 (FIXED CODE) ---
exit code      : 2
hosts pushed   : 0 (sync=0)
stderr         : [S7.5c] --layout is mutually exclusive with --host and --profile (and with --dir/--thin/--bootstrap/--rollback, which only apply to the --host push path) — the layout owns the host order and the bundles. Refused: --profile (abbreviated and `=` spellings resolve to the same flag on the remote, so they are refused here too).
--- end AFTER F-1 ---

AFTER F-1 [--pro core]: rc=2 pushes=0 resolved=--profile
AFTER F-1 [--hos edge-a]: rc=2 pushes=0 resolved=--host
AFTER F-1 [--di .]: rc=2 pushes=0 resolved=--dir
AFTER F-1 [--th]: rc=2 pushes=0 resolved=--thin
AFTER F-1 [--boot]: rc=2 pushes=0 resolved=--bootstrap
AFTER F-1 [--roll]: rc=2 pushes=0 resolved=--rollback

--- AFTER F-2 (FIXED CODE) ---
--layout=three-host : rc=0 pushes=3
--layout three-host : rc=0 pushes=3
identical push sequences: True
--- end AFTER F-2 ---
```

`hosts pushed: 0 (sync=0)` is the assertion that matters, and the permanent test
tightens it further: `seen["hosts"] == []`, i.e. the refusal lands before the
layout's hosts are even looked up in the inventory, so no transport is opened
and no inventory record is read.

---

## 3. A third finding, in scope and worse than described

O2 asked me to grep for the same plain-membership dispatch pattern on
`--host`/`--dir` before deciding. I did, and found five sites:

| line | verb | check |
|---|---|---|
| 1256 | `render` | `if "--host" in rest:` |
| 1296 | `up` | `if "--layout" in rest:` |
| 1376 | `up` | `elif "--host" in rest:` |
| 1428 | `up` | `elif "--dir" in rest:` |
| 1442 | `down` | `if "--host" in rest:` |
| 1466 | `health` | `if "--host" in rest:` |

The `--host` case is **worse than the layout case**, and this was not called out
in the handoff. `deploy.py` declares `--host` at line 3592 —

```python
control.add_argument("--host", default=None, metavar="NAME",
                     help="Remote host name (from hosts inventory): push-deploy via SSH (SPEC J)")
```

— purely so it appears in `ciu-deploy --help`. **Nothing in `deploy.py` ever
reads `args.host`** (verified: no `args.host` / `.host` consumer anywhere in the
file). So `ciu up --host=web` did not error like `--layout=` did. It fell
through to `deploy_main`, parsed *cleanly*, had `--host` silently discarded, and
ran a **local deploy of the active profile** — while the operator believed they
had pushed to `web`. Exit 0, no warning. Same for `down`, `health`, and
`render`.

That is a second silent-wrong-target defect, sitting in the same bug class O2
scoped, so it is fixed here rather than deferred.

**Scope judgment on `health`:** O2 names `up`/`down`/`render`. `health` carries
the identical pattern on the identical S10.4 `--host` modifier with the
identical consequence. Fixing three of four would leave the class half-closed
against O2's own negative, so `health` is included; the change is one predicate
swap with a comment saying exactly this, not a refactor of that branch.

### Why dispatch is exact-or-`=` and NOT abbreviation-aware

This is the one place my two checks deliberately differ, so I want it on the
record for the reviewer.

- **The guard** (`_parse_layout_argv`) is abbreviation-aware, because its flags
  would otherwise be **forwarded to a remote parser that resolves them**. That
  is the safety-critical path.
- **The dispatch** (`_flag_given`) is exact-or-`=` only, because it **selects a
  code path**. Widening it to abbreviations would make `ciu up --d /srv` mean
  `--dir` locally while `--d` remains *ambiguous* on `deploy.py`'s own parser
  (`--deploy`, `--dry-run`, `--define-root`) — inventing a new local/remote
  divergence while closing one.

Crucially this leaves **no hole**. The layout path is entered only via exact
`--layout` or `--layout=`, and once inside, the guard is abbreviation-proof. An
abbreviated `ciu up --lay prod --prof core` does not reach the layout branch —
it falls to `deploy.py`'s parser, where `--lay` is unrecognised, and **fails
loudly with zero transport**. Pinned by
`test_up_layout_abbreviation_does_not_dispatch_but_cannot_deploy`. A loud local
failure is an acceptable outcome; a silent wrong deploy is not, and that is the
only distinction the two checks encode.

---

## 4. Oracle-by-oracle evidence table

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** abbreviation-proof | **MET** | Approach **(a)**: `_parse_layout_argv` (`src/ciu/cli.py`) registers the six forbidden long options on a local parser at argparse's default `allow_abbrev=True` — the setting `deploy.parse_args` runs with (verified at `deploy.py:3517`, no `allow_abbrev=` present) — so argparse resolves the spelling before the check. Not an enumerated denylist: coverage is by construction. Resolved flags are *consumed*, so nothing survives into the forwarded `remaining`. Review's exact reproduction (3-host prod layout, `--prof=core`) refused with `[S7.5c]`, exit 2, `sync == exec == hosts == []`. Both O1 negatives structurally excluded (see §1.4). |
| **O2** equals-form dispatch | **MET** | One shared `_flag_given` predicate at the *dispatch decision point* for `--layout` (`up`), `--host` (`up`/`down`/`health`/`render`) and `--dir` (`up`). Grepped first per the oracle: 6 sites found, all listed in §3. `--layout=NAME` now enters the **same branch** as `--layout NAME` — asserted by identical push sequences, not by parallel implementation. O2's negative (fixing `--layout=` only) explicitly avoided; `health` included beyond the named three with the reasoning recorded in-code. |
| **O3** tests | **MET** | 64 tests added to `tests/tests/test_ciu_cli_layouts.py` (19 → **83**). (a) `--prof=core` + 18 abbreviations × 2 forms of all six flags, each asserting exit 2, `[S7.5c]`, and **zero** `ssh_sync`/`ssh_exec` calls via the mocked transport — O3's negative (message-only assertion) avoided throughout; the review's 3-host scenario additionally asserts `seen["hosts"] == []`. (b) `--layout=prod` vs `--layout prod` asserted to produce *identical* recorded push sequences and host-resolution sequences. (c) `--host=`/`--dir=` equals-form dispatch tests for all four verbs, each also asserted byte-identical to its space form. |
| **O4** docs | **MET** | **SPEC S7.5c**: the prior claim — "`--layout` is mutually exclusive with `--host` and `--profile`" — did not name the other four flags and said nothing about spelling; now normatively requires abbreviation-resolved exclusion in both forms, a named refusal, and refusal *before* inventory lookup. **SPEC S10.4**: new rule that path-selecting modifiers route identically in both forms, naming the `--host=` silent-local-deploy consequence. **CHANGES.md** Unreleased/Fixed: `fix(ciu)!:` **HOTFIX** entry, stated plainly as a silent-wrong-deploy in already-released code, naming v6.3.0 as the origin and telling `--layout` operators to audit prior deploys — O4's negative (downplaying) avoided. **KNOWN_ISSUES_TODO_BACKLOG.md**: CIU-34 summary row amended + a new CIU-34 hotfix block that **explicitly names and corrects the prior over-claim** ("prefix-aware, so `--profile=core` is caught too"). |

---

## 5. Files changed

| File | Change |
|---|---|
| `src/ciu/cli.py` | `_LAYOUT_FORBIDDEN` hoisted to module scope; new `_forbidden_dest`, `_flag_given`, `_parse_layout_argv` helpers; layout branch rewritten to use them; 5 dispatch sites switched from plain membership to `_flag_given`. |
| `tests/tests/test_ciu_cli_layouts.py` | +64 tests (19 → 83). |
| `docs/SPEC.md` | S7.5c exclusion rule corrected/strengthened; S10.4 gains the both-forms dispatch rule. |
| `CHANGES.md` | Unreleased/Fixed hotfix entry. |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-34 row + hotfix detail block + follow-up note. |
| `nyxloom-trove/reports/ciu-P29-...-LOG.md` | This file. |

**`scope.forbid` respected:** `deploy_pkg/layouts.py`, `deploy.py` and
`engine.py` are untouched — `deploy.py` was read only, to confirm `allow_abbrev`
and the unread `--host` stub. `nyxloom-trove/{backlog,decisions,roadmap}.md`
untouched. Confirmed by the commit's file list (5 files).

---

## 6. Gate output (verbatim)

```
$ .venv/bin/python run-ciu-tests.py
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
plugins: xdist-3.8.0, cov-7.1.0
...
src/ciu/cli.py                                     739      0    262      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8554      0   3406      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2682 passed in 17.29s =============================
```

**2682 passed, 0 failed, 100% line + branch**, green on the first run after the
fix and stable across three runs. `src/ciu/cli.py`: 739 statements / 262
branches, 0 missing.

The package-local file, separately:

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_cli_layouts.py -p no:randomly -q
83 passed in 0.26s
```

---

## 7. Notes for the reviewer

1. **The `ciu bake` (ciu-P17) comparison you were briefed on — and a finding it
   turns up.** `_bake`'s mutual exclusion is `any(a == "--profile" or
   a.startswith("--profile=") for a in positional)` — exactly the predicate
   ciu-P29 has just *replaced* in the layout guard. ciu-P17 was correct for the
   failure mode it was reviewed against (equals-form), and its zero-build
   discipline in every conflict case is the right shape and is preserved here as
   zero-transport. But it is **not** abbreviation-proof: I verified
   `any(a == "--profile" or a.startswith("--profile=") for a in ["--prof=core", "web"])`
   returns `False`, so `ciu bake --prof=core web` does not trip the conflict and
   `--prof=core` is passed to `docker buildx bake` as a build *target*. That is
   a loud failure, not a silent wrong deploy, and `bake` is outside this
   package's `scope.touch` for source changes — so per the wave's
   stop-and-document pattern I did **not** widen scope. It is recorded as a
   named follow-up in the CIU-34 hotfix block in
   `KNOWN_ISSUES_TODO_BACKLOG.md`. My fix should be judged as *stronger* than
   the P17 precedent, not merely equal to it: same zero-side-effect discipline,
   but the match delegated to argparse rather than hand-rolled.
2. **Re-running the reproduction.** The temporary harnesses were deleted (they
   assert *buggy* behaviour and would fail the gate by design). To re-derive:
   `git stash` this branch's `cli.py` back to `9fb5d854`, or read §2 — every
   number there came from the fixtures now living permanently in
   `test_ciu_cli_layouts.py`.
3. **The one behaviour change beyond the two findings** is that `ciu up
   --host=web` / `down` / `health` / `render` now *work* (push remotely) where
   they previously ran a silent local deploy. Anyone who had unknowingly come to
   depend on the broken local behaviour will see a real remote push. That is the
   fix, not a regression, but it is a behaviour change on a released path and is
   why the CHANGES.md entry is marked `!`.
4. **Not touched, deliberately:** `"--ksm"`/`"--no-ksm"` (1233), `"--no-cache"`
   (842) and `"--preflight"` (1499) are also plain membership tests, but all
   three are store-true flags with no `=` form, and none selects between
   local/remote execution. `"--" in rest` (1665) is the `ssh` argv separator,
   not a flag. Widening to those would be the unrelated refactor O2 warned
   against.

# ciu-P43 — LOG

Package: CIU-79 (item 1), CIU-80 (item 2), CIU-81 (item 3), CIU-77 (item 4).
Worktree `.worktrees/ciu-P43-loose-ends`, branch `fix/ciu-P43-loose-ends`,
based on vbpub main `332af5a1` (docs(ciu): CIU-75 -- retarget its release
version from 7.6.0 to 7.7.0).

Four independent, non-overlapping-in-file backlog items, four separate
commits. Full pytest suite run locally (devcontainer venv, not the gate)
after each item — 3367/3368/3371 passed respectively, all green, before
moving to the next item. The real gate (`./run-gate.py ciu`) was run once,
at the very end, against the full four-commit HEAD — see
`ciu-P43-REPORT.md` for the verbatim verdict.

---

## Commit 1 — `7b2d288b` — item 1, CIU-79

`fix(ciu): CIU-79 -- ciu dev's _build_dev_image resolves build.context
against repo_root`

`src/ciu/dev.py`'s `_build_dev_image` resolved `build.context` (and,
transitively, `dockerfile`) relative to `stack_dir`, never `repo_root` —
the same defect class CIU-71 fixed for `docker compose`'s `build.context`,
but `ciu dev` runs a plain `docker build` with no `--project-directory`
equivalent. Fix: `_build_dev_image` gained a `repo_root` kwarg (`run_dev`,
its only caller, already had it in scope) and resolves
`(Path(repo_root) / context).resolve()` before joining `dockerfile` onto
that and appending it as the build's trailing positional argument.

Tests: `test_build_context_resolves_against_repo_root_not_stack_dir`
(new) is the controlled-wrong-implementation proof the backlog entry
specified — a Dockerfile `COPY`ing a repo-root-relative path, `context =
"."`, asserting the COPY source is reachable from the resolved build
context; manually confirmed this fails against the pre-fix stack-dir
resolution. The two pre-existing tests
(`test_build_profile_runs_build_then_dev_container_with_selected_network`,
`test_build_failure_does_not_launch_dev_container`) that pinned the OLD,
buggy stack-dir-relative argv shape were updated to the corrected
repo-root-relative one — this IS the point of the fix, not a test being
weakened.

**Round-1 review found two real defects here, both fixed in a later
commit (see "Review round 1 fixes" below):** (1) this REPORT/LOG's
original framing of the whole bundle as non-breaking was wrong — CIU-79
IS a breaking change for any `[<root>.dev].build` profile whose
Dockerfile lives in the stack dir (the only shape that ever worked
pre-fix), left undocumented; (2) the new test's fixture wrote the
Dockerfile at the STACK dir while asserting the resolved `-f` pointed at
the REPO ROOT — a path the fixture never created — so it only passed
because the fake `build_run_fn` never touches the filesystem; a real
`docker build` on that exact argv is the reviewer's live `rc=1` repro.

Docs: SPEC S5a.1 (+ its S8.1a cross-reference), CONSUMERS.md #18, README's
DooD bullet now document `ciu dev` sharing S8.1a's repo-root-relative
`build.context`/`dockerfile` convention.

Local suite after this commit (dev-focused files only, then full suite):
57 passed (dev files); 3367 passed (full `tests/`).

---

## Commit 2 — `cd5fadea` — item 2, CIU-80

`fix(ciu): CIU-80 -- HookContext.identity_unreadable disambiguates
unmanaged from unparseable ciu.env`

Per the controller's explicit ruling in the handoff (shape (b), additive
for THIS item — CIU-75 is this wave's one deliberately-breaking release,
and items 2-4 of this bundle stay non-breaking; item 1/CIU-79 is a
separate, genuine breaking change, see Commit 1 above and "Review round 1
fixes" below):
`HookContext` (`hooks_runner.py`) gains `identity_unreadable: bool =
False`. Both S3.12 identity readers —
`deploy._workspace_identity` (the `ciu check` preflight's HookContext) and
`engine.main_execution`'s STEP-12 real-run read — set it `True` only when
`ciu.env` is PRESENT but unreadable (an `OSError`, `UnicodeDecodeError` or
`WorkspaceEnvError` while reading it), `False` on a genuinely absent
`ciu.env` (same as before). Changed as the entry's MANDATORY pair: fixing
one site and not the other would reintroduce the exact preflight-vs-real-
run divergence CIU-62 was careful to avoid.

`deploy._workspace_identity`'s return type changed from a bare `dict` to
`tuple[dict, bool]`; the new bool threads through its two intermediate
callers (`_check_stack_config`, `_check_hooks_for_stack`) down to the
`HookContext(...)` construction site. `engine.py`'s STEP-12 read gained a
local `_hook_identity_unreadable` flag set in the same `except` clause
that already handles the three-way exception union (CIU-62).

Tests: `test_identity_unreadable_agrees_between_check_preflight_and_real_run`
(new, `test_ciu_render_selection_context.py`) is the MANDATORY-pair proof
— it drives ONE malformed `ciu.env` fixture through both
`deploy._workspace_identity` (unit call) and a real
`engine.main_execution` run with a probe hook, asserting
`ctx.identity_unreadable` agrees between them (both `True`). The
legitimate-absent state stays distinct and `False` in both
`test_workspace_identity_degradation_warns_on_stderr`'s `payload is None`
branch and `test_hookcontext_identity_fields_default_none`'s bare
construction. `_identity_probe_stack`'s hook (in
`test_ciu_render_selection_context.py`) extended to also write
`ctx.identity_unreadable` to its marker file; the one pre-existing test
using it was updated (`"None|None"` -> `"None|None|True"`).

Docs: SPEC S9.3's HookContext contract, CONSUMERS.md (both the S3.12
selection-facts bullet list and the `validate_config` how-to's field
enumeration), and CONFIG.md's hook-facts paragraph all name the new
field.

Local suite after this commit (hook/identity-focused files, then full
suite): 243 passed; 3368 passed (full `tests/`).

---

## Commit 3 — `597ce58d` — item 3, CIU-81

`fix(ciu): CIU-81 -- scaffold.py's two Jinja render paths adopt
StrictUndefined`

Before touching code: read every shipped scaffold template
(`src/ciu/templates/global.defaults.toml.j2`, `stack.defaults.toml.j2`,
`stack.compose.yml.j2`) as the backlog entry required. Finding: the two
TOML templates carry ZERO Jinja `{{ }}`/`{% %}` syntax by the time
`build_files` renders them for validation — every `@@PLACEHOLDER@@` is
substituted by a plain `str.replace` BEFORE the Jinja env ever sees the
text; the `$VAR`-style tokens that remain are a DIFFERENT, later
substitution mechanism (ciu.env expansion at real deploy time), not
Jinja. `stack.compose.yml.j2` (the one file with real `{{ }}` refs) is
shipped verbatim and is NEVER Jinja-rendered by `scaffold.py` at all — it
renders for real, under `config_model.render_jinja2_text`'s
`StrictUndefined`, only at the consumer's own `ciu up`. Conclusion: no
legitimate lenient-Undefined use exists anywhere in the shipped scaffold
surface, so `StrictUndefined` is safe to adopt at both named sites with
no follow-up needed.

`_render_jinja` and `build_files`'s inline `Environment` both switched to
`Environment(undefined=StrictUndefined, keep_trailing_newline=True)`,
matching `config_model.render_jinja2_text`'s exact construction. Both
preflight render call sites (global template, per-stack
`ciu.defaults.toml.j2`) additionally gained a `try/except TemplateError`
converting a genuine future undefined-reference defect into a clean
`SystemExit` naming the template and the Jinja error, instead of leaving
a raw traceback as the failure mode a StrictUndefined flip would
otherwise introduce with no exception handling anywhere above it in
`init_main`/`cli.py`. This is the natural completion of "this preflight
exists to catch scaffold-template defects" (the row's own framing), not
scope creep — it's the exact code path the fix touches.

Tests: `test_render_jinja_strict_undefined_raises_on_typo` (the helper,
direct). `test_build_files_global_preflight_catches_undefined_reference`
+ `test_build_files_stack_preflight_catches_undefined_reference`
(`tests/test_init_scaffolding.py`) inject an undefined-reference template
via the SAME `monkeypatch.setattr(scaffold, "_template", ...)` pattern
`test_build_files_guard_rejects_global_without_shared_vars` already uses,
and assert the clean `SystemExit` at each of the two named sites — this
is the controlled-wrong-implementation proof that `build_files`'s inline
`Environment` genuinely exercises `StrictUndefined`, not just that
`_render_jinja` does in isolation. 100% line+branch coverage confirmed
for `src/ciu/scaffold.py` against just this file's own tests
(`--cov=ciu.scaffold --cov-branch`).

Docs: SPEC S19 documents the StrictUndefined fidelity and records the
verification that no shipped template needed the lenient default.

Local suite after this commit (scaffold files, then full suite): 22
passed (`tests/test_init_scaffolding.py`); 3371 passed (full `tests/`).

---

## Commit 4 — `b81d6c3b` — item 4, CIU-77

`fix(ciu): CIU-77 -- bump vendored gate judge assay-2.3.0.pyz ->
assay-3.2.0.pyz`

Read the entirety of `assay/CHANGES.md` from 2.4.0 through 3.2.0 (six
releases: 2.4.0, 2.4.1, 2.4.2, 3.0.0, 3.1.0, 3.2.0) before touching
anything, per the entry's own explicit instruction not to treat this as a
one-line pin bump. Investigation, in order:

1. **CLI compatibility** — `assay run --help` (the real installed 3.2.0,
   confirmed `pip show assay` -> Version 3.2.0) shows `assay run <lane>
   --file PATH --verdict-json PATH` is byte-identical to the argv the
   gate's shell harness (`run-gate-project/run-gate.py`'s
   `build_assay_inner`) already constructs. No CLI change needed.
2. **Config schema compatibility** — `assay lanes --json --file
   assay.toml`, run against ciu's UNMODIFIED `assay.toml` under the real
   3.2.0 binary, parsed clean: `base_source: "declared"`, `enforcement:
   "gate"`, `scope: "S1"`, `rigor: ["R0","R1"]` all round-tripped exactly
   as declared. README.md's own compatibility note distinguishes
   `LANE_SCHEMA_VERSION` (still 2, unchanged since before 2.3.0) from
   `VERDICT_SCHEMA_VERSION` (7 -> 8 at the v7->v8 cut) — only the OUTPUT
   verdict schema moved; the INPUT lane-file schema `assay.toml` targets
   did not. `assay.toml`'s body needed zero changes.
3. **The three named risks, checked one at a time:**
   - Withdrawn mutation-operator spellings (A-331, `feat(assay)!: drop
     the withdrawn operator spellings at the v8 cut`) — R2/mutation-only
     (`python:uuid-equality-swap`, `python:enum-comparison-swap`); ciu's
     lane declares only `rigor = ["R0", "R1"]`, never R2. Inapplicable.
   - Judge provenance (B018) — opt-in via `--require-judge-provenance`,
     confirmed by `assay run --help`'s own flag description ("Without
     this flag an unidentifiable invocation ... still runs"); the gate
     harness never passes this flag. The verdict now CARRIES a
     `judge_provenance` block unconditionally (additive JSON field), but
     nothing consumes it programmatically — `run-gate.py` never parses
     verdict JSON, only the process exit status.
   - Request-supplied base (B019) — opt-in via `judge.base_source =
     "request"`; ciu's lane keeps its static `judge.base = "origin/main"`
     (confirmed `base_source: "declared"` in step 2's probe output). The
     shared `run-gate.py` harness (already RG-25/RG-26-updated, ahead of
     and independent of this fix) only appends `--request-base` to a
     lane whose OWN inventory reports `base_source == "request"` — ciu's
     lane never receives it.
4. **Verdict-schema consumption** — `run-gate.py`'s shell harness never
   parses `.assay/verdict-ciu.json` itself; the gate's pass/fail IS
   `assay run`'s own exit status, under `set -euo pipefail`. The v7->v8
   verdict shape change therefore has zero ciu-side blast radius.

Given all of the above, decided this was safe to actually bump (not defer
CIU-77 as OPEN) — the entry's own escape hatch ("completely acceptable to
STOP ... if genuinely risky") did not apply once verified.

**Vendoring**: rather than manually copying an artifact from an unknown
source (the entry's own complaint — "appears to be manually copied in"),
built the zipapp from the EXACT `assay-v3.2.0` TAGGED commit: `git
worktree add --detach <scratch> assay-v3.2.0` (a throwaway worktree,
outside my ciu-P43 worktree, so it could not interfere with the
concurrently-running assay Wave B producer work), then ran assay's own
release builder from inside it: `python3 gate/distribution/build_release.py
--repo .. --outdir <scratch-dist>` (the exact command `assay/cmru.toml`'s
own `[steps.build]` uses, fully offline — `assay/gate/distribution/
build-wheelhouse/` vendors its own five-wheel closure). Output:
`ASSAY_RELEASE_MANIFEST=created tag=assay-v3.2.0` confirmed a genuine
TAGGED build (not an SCM dev-version fallback), `assay-3.2.0.pyz --version`
printed exactly `assay 3.2.0`, and `sha256sum -c` verified. The scratch
worktree was removed afterward (`git worktree remove --force`).

Vendored `assay-3.2.0.pyz` + `.pyz.sha256` into `ciu/tools/assay/`;
deleted the three orphaned older copies (`assay-2.1.0.pyz`,
`assay-2.2.0.pyz`, `assay-2.3.0.pyz`, each with its `.sha256`) after
confirming with a repo-wide grep that nothing outside their own sha256
sidecars and one historical, do-not-edit LOG file referenced them by
name — this IS the drift-recurrence mechanism the entry flagged (nothing
had ever pruned a prior version on bump).

Updated: `run-gate.toml [lanes.ciu]` / `[lanes.ciu.pins.assay]`,
`assay.toml`'s top comment, `README.md`'s Assay-backed-gate paragraph,
`docs/CONSUMERS.md` #12's worked example, `nyxloom-trove/nyxloom.toml`'s
comment — all six mentions of `assay-2.3.0.pyz` in ciu repointed to
`assay-3.2.0.pyz`. `assay.toml`'s BODY (the `[lanes.ciu]`/
`[lanes.ciu.judge]` tables) was left byte-for-byte unchanged, per step 2's
finding that nothing there needed to change.

**Scope decision — no new refresh tooling.** The entry called automating
the vendor-refresh step a nice-to-have, not mandatory, and explicitly
sanctioned skipping it with a documented reason. Given this was already
the largest and highest-risk item in a four-item bundle, and given the
manual SOP above is now fully verified and reproducible (worktree-at-tag
+ `build_release.py` + `sha256sum -c` + vendor + prune), I recorded that
SOP here and in the backlog row rather than writing new automation code
(which would itself need tests under this repo's 100%-coverage gate). A
dedicated follow-up package can script it if the manual SOP proves to
recur often enough to be worth automating.

Local check (no ciu Python source changed by this commit, so no pytest
run needed for it specifically) — confirmed via grep that no
`.toml`/`.py` file under `ciu/` still references `assay-2.3.0` and that
no test in `tests/` hardcodes an assay pin/version literal.

**Real gate, run once at the end against the final four-commit HEAD**
(`b81d6c3b`): `./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P43-loose-ends` from inside `ciu/`.
Verbatim verdict in `ciu-P43-REPORT.md`. Outcome: **PASS**, commit
`b81d6c3bd9d45008dfc0a63e214515b2466c3015` (confirmed equal to `git
rev-parse HEAD`, read from `.assay/verdict-ciu.json` in a separate step
from the terminal log), R0 PASS, R1 PASS at 100% changed-line coverage,
`schema_version: 8` (the new verdict schema), `judge_provenance` recording
the exact sha256 I vendored.

---

## Commit 5 — review round 1 fixes (CIU-79 blockers only)

An independent fresh adversarial reviewer verified items 2-4 (CIU-80,
CIU-81, CIU-77) as fully correct — including a forensic byte-level
verification of the CIU-77 judge bump (sha256/tag/CHANGES.md/schema all
confirmed) and an independent paired-probe re-test of CIU-80's
`identity_unreadable` flag across 5 `ciu.env` states. Two real blockers on
item 1 (CIU-79), both documentation/test-only — no `src/ciu/dev.py`
change needed, since the fix itself was already correct; only its
documentation and one test's fixture were wrong.

**Blocker 1 — CIU-79 IS a breaking change; the REPORT's claim that the
whole bundle stays non-breaking was false for item 1.** Reviewer proved it
live: a stack-local Dockerfile with `build = { context = "." }` and no
explicit `dockerfile` key built fine (`rc=0`) before the fix and fails
(`rc=1`, `failed to read dockerfile: open Dockerfile: no such file or
directory`) after it — the fix correctly applies CIU-71's repo-root-relative
rule, exactly as intended, but nothing documented that this breaks every
existing `ciu dev` profile whose Dockerfile lives in the stack dir (the
only shape that ever worked under the old, buggy resolution). Fixed:
`ciu-P43-REPORT.md` and this LOG both corrected to scope the non-breaking
claim to items 2-4 and name item 1 as breaking explicitly;
`docs/CONSUMERS.md` #18 gained a migration blockquote for `ciu dev`
mirroring the existing compose-side one — naming the break, the concrete
repair (`dockerfile = "<stack-path>/Dockerfile"`), and confirming (grepped)
that no doc example or fixture in this monorepo declares
`[<root>.dev].build`, so the blast radius inside vbpub itself is nil.

**Blocker 2 — the CIU-79 test encoded the BROKEN path and could not
actually see the fix work.**
`test_ciu_workspace_dev_remaining_boundaries.py`'s
`test_build_context_resolves_against_repo_root_not_stack_dir` wrote the
fixture's Dockerfile at `repo/apps/builder/Dockerfile` (the stack dir)
while asserting the resolved `-f` argument pointed at `repo/Dockerfile`
(the repo root) — a file the fixture never created. It only passed because
`build_run_fn` is a fake stub that unconditionally returns 0 without
touching the filesystem; a REAL `docker build` on that exact argv is the
reviewer's `rc=1` repro above. Exactly the "a check is only as strong as
what it actually compares" trap (AGENTS.md). Fixed: moved the fixture's
Dockerfile to `repo/Dockerfile`, matching where the CORRECTED argv
actually names it. Manually re-verified the controlled-wrong-implementation
claim by reverting `_build_dev_image` to the pre-fix code and re-running:
the test now fails at `context_arg == repo_resolved` (confirmed by pytest's
own traceback line number), not at the trailing `copy_target.is_file()`
check the docstring previously (and wrongly) named — docstring corrected
to say so. Added a NEW sibling test,
`test_ciu79_is_a_deliberate_break_for_stack_local_dockerfiles`, that
deliberately pins the breaking change itself: a stack-local Dockerfile +
`context = "."`, asserting the resolved `-f` no longer points into the
stack dir AND that the resolved path is not even a real file (proving a
real `docker build` on that argv would fail the same way the reviewer's
live repro did) — with a comment naming CIU-79 as the deliberate semantic
break. Also removed the reviewer's third, smaller note (the trailing
`copy_target.is_file()` check "adds no independent power since it's
already entailed by" the `dockerfile_arg` equality check) by leaving it in
place as an independent, still-meaningful oracle: it is the only assertion
that actually opens and reads the resolved COPY-source file rather than
merely comparing path strings, so it stays.

Verified: reverting the fix now makes 4 tests fail (the two pre-existing
tests, the corrected proof test, and the new breaking-change-pin test),
confirming genuine, non-overlapping coverage of the fix from every angle
this bundle's item 1 needed. Restored the real fix immediately after;
`git diff --stat src/ciu/dev.py` against the pre-revert state shows zero
diff, confirming the revert-and-restore round-trip touched nothing
permanently.

`tests/tests/test_ciu_workspace_dev_remaining_boundaries.py::TestDevProfileAndExecutionBoundaries`
now has 13 tests (was 12), all green; `src/ciu/dev.py` coverage confirmed
100% line+branch, unchanged (no source edit in this commit).

Real gate re-run against this commit's HEAD — verdict pasted verbatim in
`ciu-P43-REPORT.md`'s addendum section below.

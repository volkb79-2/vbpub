---
schema_version: 1
id: ciu-P14-qol11-eager-s11-validation
project: ciu
component: config_model+engine+deploy
title: "QOL-11: eagerly validate declared layouts, exec-targets, and vendor_images shape on every render path, not only when the specific feature is invoked this run"
tier: implement-2
input_revision: "3d2531ab"
source: {kind: backlog, ref: "docs/BACKLOG-2026-08-24.md#CIU-QOL-11"}
stack: none
depends_on: [ciu-P13-exit-on-doc-drift-and-env-scrub]
session: fresh
scope:
  touch:
    - "src/ciu/config_model.py"
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "tests/tests/test_ciu_config_model_layouts_eager.py"
    - "docs/SPEC.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P14-qol11-eager-s11-validation-LOG.md"
  forbid:
    - "src/ciu/deploy_pkg/layouts.py"
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_deploy_layouts.py"
    - "tests/tests/test_ciu_cli_layouts.py"
oracles:
  - id: O1-new-function
    observable: "A new function `config_model.validate_declared_features(global_cfg: dict, hosts_cfg: dict) -> None` exists. It does exactly three things, in this order, letting each step's own ValueError propagate unmodified (do not catch-and-rewrap; the existing functions already produce correctly tagged, self-explanatory messages naming what's wrong):
      1. For every `name` in `global_cfg.get('deploy', {}).get('layouts', {})`, call `deploy_pkg.layouts.resolve_layout(global_cfg, hosts_cfg, name)`. **Import-cycle warning (confirmed at carve-amendment time by tracing the actual import graph, not assumed):** `deploy_pkg/layouts.py` imports `from .profiles import resolve_profiles`, and `deploy_pkg/profiles.py` imports `from ..config_model import deep_merge` — so `config_model.py` importing `deploy_pkg.layouts` at MODULE scope is circular (`config_model -> deploy_pkg.layouts -> deploy_pkg.profiles -> config_model`) and will raise `ImportError: cannot import name 'deep_merge' from partially initialized module` the moment `config_model.py` itself is first imported. Use a function-LOCAL `from .deploy_pkg.layouts import resolve_layout` inside `validate_declared_features`'s own body (same technique step 2 below already requires for `worktree` — apply it here too, cli.py's own module-level `from .deploy_pkg.layouts import resolve_layout` works fine ONLY because cli.py is not itself imported by anything in that cycle; config_model.py is in a different position in the graph and cannot copy that precedent verbatim). An empty/absent layouts table is a no-op (zero iterations).
      2. Call `worktree.resolve_exec_targets_config(global_cfg)` unconditionally (it already validates the whole `exec_targets` table shape and is a no-op-safe call when the table is absent/empty — verify this by reading its body before relying on it; if it is NOT safe to call unconditionally when the table is absent, name that as a BLOCKED finding rather than adding a guard that changes its documented behavior).
      3. Validate `global_cfg.get('deploy', {}).get('provenance', {}).get('vendor_images')`: if present, it MUST be a list where every element is a non-empty `str`; otherwise raise `ValueError` tagged `[S17.5]` naming the exact offending value/type and its position (index) in the list, or naming that the whole key is not a list if it isn't one. Absent key is a no-op."
    negative: "a rewritten/duplicated validation body instead of reusing resolve_layout/resolve_exec_targets_config; a vendor_images check that accepts a bare string (Python will happily iterate a string's characters, which is exactly the silent-wrong-answer shape this fixes); a caught-and-swallowed exception from either reused function"
    gate: "tester-unified"
  - id: O2-wired-into-single-stack
    observable: "src/ciu/engine.py's `main_execution`, Step 5 (currently ~lines 1258-1261, right after the existing `validate_stack_shape`/`validate_stack_provisioning` pair), gains one new call: `config_model.validate_declared_features(global_config, load_hosts(repo_root))` (or the exact equivalent using whatever the real local variable names are at that point — `global_config` and `repo_root` are both already in scope earlier in this same function; re-verify their exact names by reading the function, do not assume). Add whatever import `load_hosts` needs (mirror cli.py's `from .hosts import get_host, load_hosts` style)."
    negative: "calling this with the per-stack `stack_config`/`merged` dict instead of the workspace's `global_config` (layouts/exec_targets/vendor_images are ALL global-scope keys under `[deploy]`/`[ciu]`, never stack-scoped) — get this wrong and every single-stack `ciu up --dir X` run breaks on a config value that has nothing to do with the stack being deployed"
    gate: "tester-unified"
  - id: O3-wired-into-action-check
    observable: "src/ciu/deploy.py's `action_check` (currently ~lines 1688-1766) calls `config_model.validate_declared_features(profile.config, load_hosts(repo_root))` exactly once, early (before or alongside the existing per-stack shape/provisioning loop — your choice, but it must run even when `selection` is empty, since a malformed globally-declared layout is a real defect regardless of what's selected THIS run). `action_check` already receives `repo_root` and `profile` as parameters — `profile.config` is the global config (same accessor used elsewhere in this file, e.g. `profile.config.get('ciu', {})` for `auto_connect_network`)."
    negative: "wiring this into action_graph instead of (or in addition to) action_check — action_graph's job is rendering the requires/provides graph and is out of scope for this package; do not touch it"
    gate: "tester-unified"
  - id: O4-tests
    observable: "New test file `tests/tests/test_ciu_config_model_layouts_eager.py` covers `validate_declared_features` directly (no CLI/engine involvement needed for these): a good global_cfg with zero/one/multiple valid layouts passes; a layout referencing an unknown host raises (reusing the exact fixture shape `test_ciu_deploy_layouts.py` uses for that case — read it for the fixture pattern, do not copy its file, this package's forbid list excludes editing it); a malformed exec_targets table raises (mirror whatever `worktree.py`'s own exec-target tests use as a malformed fixture); vendor_images as a bare string raises `[S17.5]`, as a list containing a non-string raises `[S17.5]`, as a list of valid strings passes, absent key passes. THEN one integration-level test each for O2 (engine.main_execution surfaces a bad globally-declared layout even on a `ciu up --dir` run that never uses `--layout`) and O3 (action_check surfaces the same for a profile-mode `ciu check` run) — these two may live in the existing engine/deploy test files if that fits their existing structure better than the new file (your call)."
    negative: "hollow tests that only check 'no exception' on the happy path without a corresponding negative case; a test that patches `resolve_layout`/`resolve_exec_targets_config` to a stub instead of exercising the real function"
    gate: "tester-unified"
  - id: O5-docs-and-backlog
    observable: "docs/SPEC.md's S11 catalog entry (search for where layout/exec-target/vendor_images shape are listed as validated — per the backlog text this entry already exists but undersells that the checks were not actually wired everywhere) gains a short correction noting these three now run eagerly on EVERY render path (single-stack and profile-mode), not only when the specific feature's own command runs. `docs/BACKLOG-2026-08-24.md`'s CIU-QOL-11 row updated to a closed/implemented state with a one-line evidence pointer. `CHANGES.md` gets one `### Fixed` bullet under an `[Unreleased]`-style heading (match whatever heading convention the top of CHANGES.md currently uses — read it first)."
    negative: "a docs claim that these were 'newly added' checks (they already existed and were correct for their original call site — the fix is eagerness/coverage, not correctness of the underlying logic)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "resolve_exec_targets_config is NOT safe to call when the exec_targets table is absent (e.g. it assumes a caller only invokes it after confirming the table exists) — BLOCKED naming the exact unsafe assumption, do not add a workaround guard without checking whether that changes its documented contract"
  - "global_config/repo_root are not actually in scope by Step 5 in engine.py's main_execution, or profile.config is not actually the right accessor in action_check — BLOCKED naming the actual variable names/types you find, do not guess a plausible-looking accessor"
mutexes: [merge-lane]
review_focus:
  - "confirm O2 is wired against the workspace GLOBAL config, not a per-stack merged config — this is the single most likely place for a convenience mistake that silently does nothing (validating an empty/wrong dict never raises, so a lazy wiring bug looks identical to success in every existing test)"
  - "confirm the vendor_images check treats a bare string input as invalid rather than iterating its characters (a classic Python footgun: `for v in \"nginx\"` silently 'validates' four single-character non-empty strings and reports success)"
  - "confirm action_check's new call actually runs before returning early on an empty selection, if action_check has any early-return path for that case"
---

# ciu-P14 — QOL-11: eager S11 validation for layouts, exec-targets, vendor_images

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#CIU-QOL-11` (already in your context via the source ref) — the ask.
2. `git show 51d5d4f7 -- src/ciu/engine.py` — the PRECEDENT this package extends: QOL-1 added
   exactly one call (`config_model.validate_stack_provisioning(...)`) at the same Step-5 seam
   in `main_execution` this package is targeting. Follow that shape.
3. `src/ciu/engine.py:1140-1265` (`main_execution`, through Step 5) — confirm the real names of
   `global_config` and `repo_root` at the point Step 5 runs; do not assume they match this
   handoff's prose exactly without checking.
4. `src/ciu/deploy.py:1688-1766` (`action_check`) and `src/ciu/deploy.py:2790-2840` (its caller,
   showing `profile.config` used elsewhere as the global-config accessor).
5. `src/ciu/deploy_pkg/layouts.py:62-168` (`resolve_layout`, `list_layouts`) — read in full; this
   is a FORBIDDEN file (read-only), you are calling `resolve_layout`, not modifying it.
6. `src/ciu/worktree.py:1318-1400` (`parse_exec_targets`, `resolve_exec_targets_config`) —
   FORBIDDEN, read-only; confirm whether it's safe to call with an absent/empty table before
   relying on that in O1.
7. `src/ciu/deploy.py:744-880` (`_image_reference_name`, `_normalized_image_reference`, and the
   provenance function that reads `vendor_images` at `~794-895`) — confirms there is currently NO
   shape/type check on `vendor_images` before these functions iterate it.
8. `tests/tests/test_ciu_deploy_layouts.py` (FORBIDDEN, read-only) — mirror its fixture shape for
   "layout references unknown host" in your new test file; do not edit this file.
9. `docs/SPEC.md` — search for the S11 validation catalog entry covering layout/exec-target/
   vendor_images shape (per the backlog text, it already documents these as "validated" — your
   doc fix corrects it to say "eagerly, on every render path").

## Why the second single-stack path (`engine.py` ~line 1874, inside the `secrets` action) is
## deliberately NOT touched by this package

That path backs `ciu secrets list`/`ciu secrets reset` — a read-only introspection command that
needs only stack shape + secret directive discovery, never a deploy. Requiring globally-declared
layouts/exec-targets/vendor_images to be well-formed just to list a stack's secrets would add
friction unrelated to that command's actual job (an operator mid-refactor on an unrelated layout
declaration should still be able to list secrets). This is a deliberate scope decision, not an
oversight — do not add validation there.

## Work

1. Add `validate_declared_features` to `src/ciu/config_model.py` per O1.
2. Wire it into `engine.main_execution` per O2.
3. Wire it into `deploy.action_check` per O3.
4. Write tests per O4.
5. Update docs/backlog/changelog per O5.
6. Run the iteration signal (below) to 100% line+branch, zero failures.

## Environment setup

Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`, branch
`feat/ciu-qol-v8prep-wave`, venv at `.venv/`.

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

(ciu-P13, dispatched just before this package, adds a `tests/conftest.py` autouse fixture that
scrubs this devcontainer's ambient dstdns-identity env vars — if that package has landed on this
branch already, the bare command above is sufficient; if you find the gate failing with
`"--define-root ... does not match REPO_ROOT (/workspaces/dstdns)"`-style errors instead, P13
hasn't landed yet, and you should fall back to prefixing with
`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN`
— that is a pre-existing environmental artifact, not something this package caused or must fix.)

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a file outside
`scope.touch`, STOP: write `BLOCKED: <mechanical reason>` to
`nyxloom-trove/reports/ciu-P14-qol11-eager-s11-validation-LOG.md`, commit, and exit.

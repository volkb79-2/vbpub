# ciu-P44 — LOG

Package: CIU-59 (item 1), CIU-61 (item 2), CIU-84 (item 3), CIU-85 (item 4).
Worktree `.worktrees/ciu-P44-small-followups`, branch
`fix/ciu-P44-small-followups`, based on vbpub main `1967783a` (chore(ciu):
prepare release inputs — vbpub main tip when this worktree was created,
already carrying ciu 7.7.0).

Four independent-but-adjacent backlog items, four `fix(ciu)` commits plus
one final `docs(ciu)` backlog commit — five commits total. Two items
(CIU-59/CIU-85) both touch `src/ciu/worktree.py` in non-overlapping regions;
split via `git add -p` hunk selection (verified per-commit with
`git diff --cached <file> | grep '^@@'` before each commit) rather than
landing the whole file in one commit. `KNOWN_ISSUES_TODO_BACKLOG.md` was
landed as its own final commit rather than split per item — its header
paragraph summarizes the whole bundle, and several row edits share diff
hunks with adjacent, unrelated table rows, so a clean per-item split was not
mechanically available the way it was for `worktree.py`/`SPEC.md`.

Full pytest suite (`run-ciu-tests.py` — the real `--cov=ciu -n auto --dist
loadfile --cov-branch --cov-fail-under=100` invocation, not a plain
`pytest`) run ONCE at the full five-commit HEAD, after all edits and before
any commit was made: **3418 passed, 100.00% line+branch coverage across
every module including the five this package touches
(`deploy.py`, `provisioning.py`, `scaffold.py`, `workspace_env.py`,
`worktree.py`), zero warnings besides one pre-existing third-party
`DeprecationWarning`**. Individual item's own test files were also run in
isolation while writing each fix (noted per item below) as a faster
iteration loop; the full-suite run is the one that actually gates.

The real gate (`./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P44-small-followups`) was run once, at the
very end, against the final five-commit HEAD — see `ciu-P44-REPORT.md` for
the verbatim verdict.

**A process note, not a defect**: while investigating CIU-84, a dispatched
read-only fork sub-agent (asked to sweep the `ciu check --json` call graph)
became confused about its own identity relative to this session mid-task,
mistaking the parent session's own concurrent edits to this worktree for a
second, colliding implementer, and never delivered its findings. It
correctly never edited any file (confirmed) despite the confusion. The
actual CIU-84 investigation and fix were done directly by this session
rather than relying on the fork's output — see item 3 below for the real
sweep. No worktree state was affected by the incident.

---

## Commit 1 — `22cd3dee` — item 1, CIU-59

`fix(ciu): CIU-59 -- factor detect_devcontainer_name() out of four
duplicates`

`os.environ.get("DEVCONTAINER_NAME") or os.environ.get("HOSTNAME", "")` (or
a semantically-close nested form) was duplicated four times. Verified line
numbers myself per the backlog row's own instruction — actual lines at fix
time were `workspace_env.py:697` (`_detect_host_mdt_tmp`),
`workspace_env.py:834` (`_connect_devcontainer_to_network`),
`workspace_env.py:1445` (`generate_ciu_env`'s `ciu env print` report row —
the nested form), and `worktree.py:395` (`_host_identity`) — not the row's
`:598`/`:742`/`:964`, which had drifted since the row was filed.

Factored `workspace_env.detect_devcontainer_name()`, placed immediately
before its first call site. All four sites now call it — three directly,
`worktree._host_identity` via a local deferred import (matching that
module's own established convention for `workspace_env` imports; verified
no circular import exists via a direct AST scan of `workspace_env.py` for
any `worktree` import, then a live double-import smoke test in both
orders).

Real semantic difference found and fixed, per the row's own "check it
matches semantically" caveat: `generate_ciu_env`'s nested
`os.environ.get("DEVCONTAINER_NAME", os.environ.get("HOSTNAME", ""))`
returns an explicitly-empty `DEVCONTAINER_NAME` verbatim (key present, so
the nested default is never consulted), unlike the other three `or`-based
sites, which fall through to `HOSTNAME` for ANY falsy value including an
explicit empty string. The helper standardizes on the majority `or` shape;
documented as a deliberate behavior fix in the helper's own docstring, not
an incidental change.

Tests: four new direct unit tests in `test_ciu_workspace_env_branch106.py`
— prefers the container name; falls back to `HOSTNAME` when absent; treats
an explicitly-empty `DEVCONTAINER_NAME` as absent (the semantic-fix
regression proof, would have failed against the old nested-form behavior);
empty string when neither is set. The three pre-existing `_host_identity`
tests in `test_ciu_worktree_lease.py` (prefers container name / falls back
to hostname / never produces an empty holder) continue to exercise the
refactored call site's two main branches plus its own `or "unknown-host"`
fallback, unchanged.

Local suite after this commit (workspace_env + worktree files): 407 passed
(`test_ciu_worktree_lease.py test_ciu_worktree.py test_ciu_worktree_reap.py
test_ciu_worktree_shared_infra.py`), 24 passed
(`test_ciu_workspace_env_branch106.py` alone via a multi-file invocation to
avoid the import-path caveat noted below).

**Caveat found and worked around, not a defect in the fix**:
`test_ciu_deploy_direct79.py` (used later, for item 3) does not
`sys.path.insert(0, .../src)` the way most other test files do, so running
it ALONE resolves `import ciu` against the venv's installed 7.7.0 site-
packages copy, not this worktree's `src/`. Irrelevant to the real gate
(which always collects the whole `tests/` dir, so an earlier-collected
`sys.path`-inserting file always wins the module-cache race first) but
worth knowing for anyone iterating on that one file in isolation — always
pair it with at least one other test file, or run the full suite.

---

## Commit 2 — `191a2b47` — item 2, CIU-61

`fix(ciu): CIU-61 -- reconcile ciu init's _GITIGNORE_ENTRIES against
.gitignored.ciu`

Read both files fully before deciding, per the row's own instruction. Found
`.gitignored.ciu` (the file the row treats as the reference to reconcile
against) had a real, independent bug: it listed `ciu.global.toml.j2` and
`**/ciu.toml.j2` as gitignored, under a stale "override templates
(auto-created when missing)" comment. Cross-checked against SPEC S3.1a/
CIU-8 ("Committed overrides are not auto-created: CIU never generates the
committed global or stack override... create it manually") and against
ciu's OWN real `.gitignore` in this repo, which already has an explicit,
correctly-reasoned comment block excluding exactly these two patterns
("COMMITTED, OPTIONAL sparse override... CIU never auto-creates it; like
ciu.global.toml.j2 it is hand-authored, sparse, and tracked"). Confirmed:
`.gitignored.ciu`'s claim was simply wrong, predating CIU-8 (or never
updated for it).

Given this, deriving `_GITIGNORE_ENTRIES` directly from the OLD
`.gitignored.ciu` (the row's literal suggestion) would have propagated a
worse bug into every future `ciu init` — a scaffolded consumer would
gitignore their own committed, hand-authored sparse override templates,
silently dropping real config from `git add -A`. Fixed `.gitignored.ciu`
FIRST (removed the two wrong entries, restructured to one-comment-per-
pattern so it is mechanically parseable, added an explanatory callout
mirroring `.gitignore`'s own reasoning), then reconciled
`_GITIGNORE_ENTRIES` against the corrected file.

Three real entries added to `_GITIGNORE_ENTRIES`: `ciu.worktree-instance.json`
(S16, confirmed also gitignored at the monorepo root's own `.gitignore`,
just not `ciu/`'s — this file is written at a `<target-ciu-root>` which for
a scaffolded consumer repo IS the repo root, so the entry belongs in the
scaffolded `.gitignore` even though it doesn't need to be in ciu's own),
`ciu.global.worktree.toml.j2` (S3.1b, the sole instance-identity source
since CIU-75/7.7.0), `**/ciu.toml` (rendered per-stack config, S1.8).
`ciu.global.toml.j2`/`**/ciu.toml.j2` deliberately NOT added, pinned by a
dedicated test naming them.

Mechanism decision: kept `_GITIGNORE_ENTRIES` as the hand-maintained
RUNTIME source (it executes inside the installed wheel — `ciu init` must
work under a plain `pip install ciu` with no repo-root file to read) and
`.gitignored.ciu` as the documentation source, rather than making one
literally derive from the other at runtime. Enforced the "no future drift"
half of the row's own request with a TEST instead:
`test_gitignore_entries_match_gitignored_ciu_sample` parses
`.gitignored.ciu`'s real (non-comment) patterns at test time and asserts
the set is IDENTICAL to `_GITIGNORE_ENTRIES`'s patterns, in both
directions — this fails loudly the next time either file drifts, closing
the actual recurrence mechanism the row flagged, without a fragile runtime
coupling to a file that may not exist post-install.

Tests: the reconciliation test above;
`test_gitignore_entries_omit_the_committed_override_templates` pins the
two deliberate exclusions by name;
`test_gitignore_fully_satisfied_appends_nothing`'s pre-seeded `.gitignore`
fixture (a pre-existing test asserting a rerun appends nothing when every
entry is already present) updated from 4 to 7 entries to match.

Docs: SPEC S19 rewritten to list all 7 entries and name the deliberate
exclusion.

Local suite after this commit (`test_init_scaffolding.py` alone): 26
passed.

---

## Commit 3 — `4e913a52` — item 3, CIU-84

`fix(ciu): CIU-84 -- full sweep of stdout writes reachable on the ciu check
--json path`

Traced the reachable call graph from `deploy._run()`'s check-action branch
by hand (a dispatched investigation fork was also asked to do this sweep in
parallel but never delivered usable findings — see the process note above;
this section reflects the direct investigation actually used for the fix).

**Already clean, verified, not touched**: `action_check` itself gates every
line of its own prose through local `say`/`complain` closures that check
`json_output` — confirmed by reading the full function body.
`_check_stack_config`, `_check_hooks_for_stack` — no raw stdout write of any
kind (grepped their bodies). `render_selected_stacks`, `config_model.py` —
no `print(` anywhere in `config_model.py`. `bootstrap_workspace_env`'s own
deprecation-notice regeneration path (CIU-75) — confirmed it already passes
`notice_stream=sys.stderr` unconditionally at its one call site
(`workspace_env.py:1635`), independent of this fix. `engine.py`'s
`check_runtime_dependencies` — confirmed NOT reachable from `action_check`'s
path at all (its docstring's "Docker is never contacted" claim holds:
`action_check` and its whole callee tree never import or call into
`engine.py`).

**Bad site 1 — `deploy._run()`'s own top-level prose.** FOUR unconditional
`info()` calls print straight to stdout regardless of `--json`:
`"Active service profile(s): ..."` (the row's originally-named site),
`"No action specified; defaulting to --deploy"`, an S7.7 health-gate note,
and `">>> action: {action}"` inside the dispatch loop — the last one fires
for EVERY action including `"check"`, immediately before `action_check` is
even called. Fixed with a new local closure, `_run_info`, defined once at
the top of `_run` and used at all four sites: prints to stderr instead of
stdout whenever `getattr(args, "json_output", False)` (or, see below,
`graph_format == "json"`) — `getattr`, not `args.json_output` directly,
matching this function's own established idiom for this exact attribute
elsewhere, and needed for real: several existing unit tests
(`test_ciu_deploy_direct79.py`'s `_args()` helper) build a hand-rolled
`argparse.Namespace` that does not set `json_output` at all, and
`args.json_output` would have raised `AttributeError` on every one of them.

Chose the simpler "no `_run`-level prose on stdout under a json-shaped
flag, full stop" invariant over precisely tracking which action combination
would actually reach `_emit_check_report` — more robust to a future action
combination changing which lines run before `check` dispatches, and the
S7.7/health-gate line is only reachable when `"deploy"` is ALSO among the
actions (an off-label `--check --deploy --json` combo), so gating it
narrowly would have meant either leaving it out (a latent gap) or writing
brittle "will check actually run in json mode" logic. The global gate is
one line simpler and cannot regress the same way.

**Bad site 2 — a SECOND, independent class, in a different module.**
Tracing `action_check`'s `--live` branch (`provisioning_pkg.probe_ref`,
called per `requires` ref) into `provisioning.py`'s `_probe_stack` found
two unconditional `print(f"[WARN] ...", flush=True)` calls — the
`one_shot`/`:completed` migration deprecation notices from V8-PREP-5,
unrelated to CIU-75/CIU-84's own earlier fixes and not previously flagged
in the backlog row. Neither `probe_ref` nor `_probe_stack` takes a
`json_output`/quiet parameter at all — threading one through would touch a
public-ish probe API with its own existing test suite for a narrow gain, so
instead both prints route to `file=sys.stderr` UNCONDITIONALLY (not gated
on `--json`), matching the established idiom that a deprecation warning
belongs on stderr regardless of output mode (same reasoning as CIU-75's own
stderr-routing decision for its bootstrap notice).

Five existing tests in `test_ciu_provisioning.py` asserted `[WARN]` IN
`capsys.readouterr().out` for these two sites
(`test_probe_stack_healthy_oneshot_fallback_warns_deprecated`,
`test_probe_stack_healthy_warns_when_target_declares_one_shot`,
`test_probe_stack_healthy_warns_via_bare_selector_matching_one_shot_stack`,
plus two negative tests asserting `[WARN]` NOT in stdout when no warning
should fire) — all five updated to check `.err` instead of (or in addition
to, for the two negative tests, which now also assert absence from `.err`)
`.out`. No test anywhere else in the suite referenced this literal text
(grepped first, before touching the source).

**A structurally surprising finding, documented rather than silently
patched around**: SPEC S13.4a's OWN prose explicitly documented the
pre-fix leak as INTENDED behavior — "Under `--json` the action emits no
prose of its own; the orchestrator's own `[INFO]` lines still precede the
document on stdout, exactly as for `ciu graph --format json`." That same
sentence named a real sibling defect: `ciu graph --format json` shares the
identical `_run`-level leak (a DIFFERENT flag, `args.graph_format`, not
`args.json_output`), and S13.5's own contract text ("only the graph itself
goes to stdout") was equally false for the same `_run`-level reason.
`_run_info`'s gate condition extended to `getattr(args, "graph_format",
None) == "json"` too — a two-line change, same closure, same fix shape,
directly closing the exact defect SPEC's own (now-corrected) text pointed
at. Went one level deeper and found `action_graph` ITSELF (not `_run`) also
has ungated `info()`/`error()` calls on its empty-graph note and two
validation-error paths — a genuinely SEPARATE surface (a different
function, needing its own parameter threading, its own tests) rather than
a `_run`-level leak. Per the package brief's own explicit permission to
scope down and file a narrower follow-up rather than silently
under-deliver: filed as **CIU-86**, not folded into this commit.

Tests: `test_check_json_stdout_is_exactly_one_json_document`
(`test_ciu_deploy_direct79.py`) is the row's own required oracle, built
literally — drives `deploy._run(["--check", "--json"])` end to end with the
REAL `action_check` (not mocked, unlike this file's other `_run` tests)
against an empty `selection` (nothing to check, isolating the test to the
stdout-purity contract rather than any config's specific validation
outcome — already covered elsewhere), and asserts `json.loads()` on the
ENTIRE captured stdout succeeds — `json.loads` fails outright on ANYTHING
else sharing stdout with the document, which is the strictly stronger
oracle the row asked for over a substring check. Needed
`deploy.hosts_pkg.load_hosts` stubbed to `{}` so the test's outcome cannot
depend on the invoking machine's own `~/.ciu/hosts.toml`.
`test_run_info_routes_to_stderr_under_json_output` is the narrower unit
proof for the two `_run_info` sites reachable without `check_preflight`'s
own (deliberately unrelated, deploy-path-only) prose in the way — first
attempt at this test failed because the default-to-`"deploy"` action path
drags in a REAL `check_preflight` call ahead of dispatch, whose own prose
is legitimately NOT gated by the outer `--json` (a deploy-time side effect,
not the `--check` verb `--json` is documented against); fixed by stubbing
`check_preflight` alongside the sibling preflight functions the test
already stubbed.

Docs: SPEC S13.4a's stale sentence corrected to state the real, now-true
contract; S13.5 gained a cross-reference naming both the fix and the
CIU-86 remainder. CONSUMERS.md's `ciu check --json` walkthrough, which had
also documented "read the JSON object at the end of stdout" as accepted
guidance, corrected to state the new true contract and cross-reference
CIU-86 for `ciu graph --format json`'s own remaining gap.

Local suite after this commit: `test_ciu_provisioning*.py` — 219 passed;
`test_ciu_deploy_actions.py test_ciu_deploy_direct79.py
test_ciu_deploy_direct80.py test_ciu_deploy_orchestration_boundaries.py
test_ciu_identity_cutover_ciu75.py test_ciu_test_repo.py
test_ciu_cli_status.py test_ciu_diagnose_remaining.py` — 243 passed.

---

## Commit 4 — `d89514aa` — item 4, CIU-85

`fix(ciu): CIU-85 -- _clean_in gains the identity strip its two siblings
perform`

`worktree._clean_in` built its child environment as `dict(os.environ)` +
`identity` (the target's overlay facts), with no strip of the CALLER's own
identity keys first — unlike its two siblings, `_sanitized_target_env` and
`_resolve_budget_candidates`, which both build `{k: v for k, v in
os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS}` before overlaying.
Confirmed the practical effect: `_CIU_IDENTITY_ENV_KEYS` carries
`CIU_SERVICES_PROFILE`, which is NOT a `[ciu.instance.generated]` overlay
fact and therefore never appears in `identity` — so without the strip, the
CALLING process's own service-profile selection reached the child `ciu
clean`'s environment unchanged, where the two siblings would have removed
it.

Fixed `_CIU_IDENTITY_ENV_KEYS`'s OWN definition first, per the row's
suggested mechanism: derives its identity half from
`GENERATED_FACT_ENV_KEYS.values()` (`workspace_env.py`'s canonical
fact-name -> env-name table, the same table `workspace_env.
LEGACY_IDENTITY_ENV_KEYS` already derives from for the identical reason)
instead of a second, independently hand-maintained six-item literal. This
adds `PUBLIC_FQDN` BY CONSTRUCTION — one of the six identity facts since
CIU-47, silently absent from the old hand-written list — with
`CIU_SERVICES_PROFILE` (not a fact) kept as the one explicit hand-added
member on top, exactly as the row specified. Required a new top-level
import (`from .workspace_env import GENERATED_FACT_ENV_KEYS`) in
`worktree.py` — verified safe first: an AST scan of `workspace_env.py`
confirmed it never imports `worktree.py` (no cycle), then a live
double-import smoke test confirmed both modules import cleanly in either
order.

`_clean_in` itself: `env = dict(os.environ); env.update(identity)` became
`env = {k: v for k, v in os.environ.items() if k not in
_CIU_IDENTITY_ENV_KEYS}; env.update(identity)`, matching its two siblings
byte-for-byte in shape.

**A third sibling builder found while doing this, not named in the
backlog row, folded in rather than left inconsistent**: `_generate_env_in`
(strips the caller's identity before running `ciu env generate` inside a
worktree, so the child derives its OWN identity rather than inheriting the
caller's) carried its own SEPARATE hand-written six-key literal, identical
in content to the pre-fix `_CIU_IDENTITY_ENV_KEYS` — meaning it was ALSO
missing `PUBLIC_FQDN`. Traced the practical consequence: `_detect_public_
fqdn` (the function `ciu env generate` calls to derive `PUBLIC_FQDN`)
explicitly adopts an ambient pre-set value when nothing independently
derived contradicts it ("no independently sourced value: the ambient FQDN
is kept silently... the legitimate manual-override mechanism") — exactly
the CIU-47 cross-checkout leak shape, just at a different call site than
CIU-47 originally fixed. Left unfixed, a stale `PUBLIC_FQDN` in the calling
process's environment could have been silently adopted as a freshly
`ciu env generate`d worktree's own FQDN. Fixed identically:
`_generate_env_in` now references `_CIU_IDENTITY_ENV_KEYS` instead of its
own literal.

Tests, all in `test_ciu_worktree.py`'s `TestWorktreeSubprocessEnvironment`:
`test_identity_env_keys_match_the_canonical_fact_table_plus_profile` pins
the derivation itself (`set(_CIU_IDENTITY_ENV_KEYS) ==
set(GENERATED_FACT_ENV_KEYS.values()) | {"CIU_SERVICES_PROFILE"}`, plus a
direct `PUBLIC_FQDN in ...` assertion).
`test_clean_strips_the_callers_service_profile_selection` is the
controlled-wrong-implementation proof for the CIU_SERVICES_PROFILE leak —
sets `CIU_SERVICES_PROFILE=primary-profile` in the caller's ambient env,
asserts it is ABSENT from `_clean_in`'s captured child env; manually
confirmed this fails (assertion error, key present) against the pre-fix
`dict(os.environ)` implementation before finalizing.
`test_clean_strips_a_stale_ambient_public_fqdn_not_carried_by_target`
proves an FQDN-less target (`write_instance_facts(..., public_fqdn="")`)
does not inherit the caller's stale ambient `PUBLIC_FQDN` — the child env's
`PUBLIC_FQDN` is the empty string the TARGET carries, not the caller's
`primary.example.com`. The pre-existing `_IDENTITY_KEYS` test fixture (used
by `test_generate_env_strips_primary_instance_identity`, a real, non-mocked
existing test that loops over every identity key asserting it is absent
from the stripped child env) gained `PUBLIC_FQDN`, extending that existing
test's own loop assertion to cover the new key for free — no new test
needed there, just the fixture correction.

Docs: SPEC S16.6 (`worktree up`/`worktree exec`'s own env-building rule)
updated to name all seven keys (six derived + one hand-added) instead of
the stale hand-listed six, and to state the derivation explicitly.

Local suite after this commit:
`test_ciu_worktree_lease.py test_ciu_worktree.py test_ciu_worktree_reap.py
test_ciu_worktree_shared_infra.py` — 407 passed.

---

## Commit 5 — `7201224f` — backlog

`docs(ciu): backlog -- CIU-59/61/84/85 FIXED (ciu-P44), CIU-86 filed`

All four table rows marked FIXED with the depth this file's own existing
FIXED rows (CIU-77/80/81, read before writing these) established as the
house bar. The three items that carry a dedicated `## CIU-NN —` section
(CIU-61, CIU-84, CIU-85 — CIU-59 does not; it was always table-row-only,
matching CIU-77/79/80/81's own precedent) gained a `FIXED (ciu-P44)`
heading annotation, mirroring CIU-75's own precedent for a filed item with
a dedicated section that later got fixed. A new table row filed for CIU-86
(row-only, no dedicated "## CIU-86 --" section -- matching CIU-59's own
row-only precedent, not the CIU-61/84/85 shape). "Last updated" header
paragraph rewritten to lead with this
package, the prior paragraph demoted to "Previously, 2026-08-31 —".

Landed as its own commit, not split per item, for a mechanical reason: `git
diff --stat` showed the header paragraph touches lines that describe the
whole bundle (cannot honestly be split into four independent fragments),
and two of the table-row hunks each combine an edited row with an adjacent,
untouched-but-context-included row from a DIFFERENT, unrelated backlog
item — `git add -p` could not cleanly isolate them the way it did for
`worktree.py`/`docs/SPEC.md` in the four fix(ciu) commits above (verified
those splits precisely via `git diff --cached <file> | grep '^@@'` before
each commit — see items 1 and 4 above). Each item's own CODE, TESTS, and
SPEC/CONSUMERS documentation already landed in that item's own commit; this
commit is backlog-tracker bookkeeping only, touching zero source or test
files (`git diff --stat` for this commit: `KNOWN_ISSUES_TODO_BACKLOG.md |
34 ++++++++++++++---`, nothing else).

No test run needed for this commit specifically (no code changed); the
final full-suite run and the real gate below both ran against this commit's
HEAD, confirming nothing about the backlog edit broke the build (it
couldn't have — it's markdown — but the gate's own dirty-tree check would
have caught a malformed commit either way).

---

## The real gate — run once, at the end, against the final five-commit HEAD

```
cd /workspaces/vbpub/.worktrees/ciu-P44-small-followups/ciu
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P44-small-followups
```

Outcome: **PASS**, commit `7201224fd9f385135b4c75af9f0d56e7f292a653`
(confirmed equal to `git rev-parse HEAD`, read from
`.assay/verdict-ciu.json` in a separate step from the terminal log — never
a pipe tail). R0 PASS, R1 PASS at 100.0% changed-line coverage (43/43
executable lines, 2/2 branches — the five-commit diff against merge-base
`1967783a`). Full verbatim verdict in `ciu-P44-REPORT.md`.

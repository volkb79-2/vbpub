# ciu-P44 — REPORT

Four bundled backlog items, five commits (four `fix(ciu)` + one final
`docs(ciu)` backlog commit), on branch `fix/ciu-P44-small-followups`
(worktree `.worktrees/ciu-P44-small-followups`), based on vbpub main
`1967783a`.

| item | entry | outcome | commit |
|---|---|---|---|
| 1 | CIU-59 | **DONE** | `22cd3dee` |
| 2 | CIU-61 | **DONE** | `191a2b47` |
| 3 | CIU-84 | **DONE** (CIU-86 filed for a narrower, out-of-scope remainder) | `4e913a52` |
| 4 | CIU-85 | **DONE** | `d89514aa` |
| — | backlog | bookkeeping | `7201224f` |

All four items shipped; none deferred. Per-item full reasoning, oracle
design, and controlled-wrong-implementation notes are in `ciu-P44-LOG.md`,
one section per commit — this file states outcomes, per-item evidence, and
the real gate verdict.

---

## Item 1 — CIU-59: factor `detect_devcontainer_name()` out of four duplicates

**What I did.** `os.environ.get("DEVCONTAINER_NAME") or
os.environ.get("HOSTNAME", "")` (or a nested variant) was duplicated across
`workspace_env.py` (three sites) and `worktree.py` (one site, added by
ciu-P26). Verified every line number myself — they had drifted from the
backlog row's own figures. Factored into `workspace_env.
detect_devcontainer_name()`; all four sites now call it.

**Real bug found and fixed, not just refactored around.** One of the four
sites (`generate_ciu_env`'s `ciu env print` report row) used a differently-
shaped nested `.get(..., .get(..., ""))` that returned an explicitly-empty
`DEVCONTAINER_NAME` verbatim instead of falling through to `HOSTNAME` like
the other three `or`-based sites. The shared helper standardizes on the
majority (`or`) semantics — documented in its own docstring as a deliberate
fix, with a dedicated regression test
(`test_detect_devcontainer_name_treats_an_explicit_empty_value_as_absent`)
proving it.

**How tested.** Four new direct unit tests for the helper itself
(`test_ciu_workspace_env_branch106.py`); three pre-existing `_host_identity`
tests (`test_ciu_worktree_lease.py`) continue to cover the refactored call
site unchanged, including its own `or "unknown-host"` fallback (not shared
by the other three sites).

---

## Item 2 — CIU-61: reconcile `_GITIGNORE_ENTRIES` against `.gitignored.ciu`

**What I did.** `scaffold.py`'s `_GITIGNORE_ENTRIES` (4 entries) had
drifted from CIU's own published `.gitignored.ciu` sample-rules file.

**A bug in the reference file itself, found before trusting it.** Read both
files fully, per the row's own instruction, and found `.gitignored.ciu`
wrongly listed `ciu.global.toml.j2` and `**/ciu.toml.j2` as gitignored —
both are COMMITTED, hand-authored sparse override templates per SPEC
S3.1a/CIU-8, confirmed by cross-checking against ciu's own real
`.gitignore` in this repo, which already gets this exact distinction right
with an explicit comment block. Deriving `_GITIGNORE_ENTRIES` from the OLD
`.gitignored.ciu`, as the row's literal suggestion would have done, would
have shipped a WORSE bug — a scaffolded consumer's `ciu init` gitignoring
their own operator-authored config.

**Mechanism.** `.gitignored.ciu` corrected first (removed the two wrong
entries, restructured to one-pattern-per-comment for mechanical
parseability, added an explanatory callout). `_GITIGNORE_ENTRIES` then
reconciled against the corrected file: three real entries added
(`ciu.worktree-instance.json`, `ciu.global.worktree.toml.j2`,
`**/ciu.toml`); the two committed-template patterns deliberately NOT added,
pinned by a dedicated test naming them. `_GITIGNORE_ENTRIES` stays the
hand-maintained RUNTIME source (must work inside an installed wheel with no
repo-root file present); `.gitignored.ciu` stays the documentation source.
A new test, `test_gitignore_entries_match_gitignored_ciu_sample`, parses
`.gitignored.ciu`'s real patterns and asserts the two sets are IDENTICAL in
both directions — a TESTED reconciliation that fails loudly on future
drift, rather than a runtime coupling.

**How tested.** The reconciliation test above;
`test_gitignore_entries_omit_the_committed_override_templates` pins the
deliberate exclusion; the pre-existing
`test_gitignore_fully_satisfied_appends_nothing` fixture updated to the new
7-entry list. `docs/SPEC.md` S19 rewritten to match.

---

## Item 3 — CIU-84: full sweep of stdout writes on the `ciu check --json` path

**What I did.** Traced the full reachable call graph from `deploy._run()`'s
check-action branch by hand. Confirmed clean and untouched:
`action_check`'s own prose (already gated via local `say`/`complain`),
`_check_stack_config`, `_check_hooks_for_stack`, `render_selected_stacks`,
`config_model.py` (no raw `print` anywhere), `bootstrap_workspace_env`'s own
CIU-75 deprecation-notice stderr routing, and confirmed `engine.py`'s
`check_runtime_dependencies` is genuinely unreachable from this path (its
own docstring's claim holds).

**Two independent bad classes found and fixed.**

1. `deploy._run()`'s own top-level prose: FOUR unconditional `info()` calls
   (the row's originally-named "Active service profile(s)" line, plus three
   more found in the same sweep — "No action specified", an S7.7 health-gate
   note, and the `">>> action: {action}"` dispatch-loop line). All four now
   route through a new `_run_info` closure, printing to stderr instead of
   stdout whenever `--json` (or `--format json`, see below) is set. Chose
   the simpler "no `_run`-level prose on stdout under a json-shaped flag,
   full stop" invariant over precisely tracking which action combination
   reaches `_emit_check_report`.

2. A SECOND, independent class in `provisioning.py`, found by tracing
   `action_check`'s `--live` branch into `probe_ref`/`_probe_stack`: two
   unconditional `[WARN]` deprecation notices (the `one_shot`/`:completed`
   migration warnings) with no `json_output` awareness at all. Neither
   function takes that parameter; both prints now route to stderr
   UNCONDITIONALLY (not gated on `--json` at all) rather than threading a
   new parameter through a probe layer that has no other json-mode concept
   — matching the established "a deprecation warning belongs on stderr,
   full stop" idiom from CIU-75/CIU-62.

**A structurally surprising finding, scoped down rather than silently
expanded.** SPEC S13.4a's own text had documented the pre-fix leak as
INTENDED, and in doing so named a real sibling defect: `ciu graph --format
json` shares the identical `_run`-level leak (S13.5's "only the graph
itself goes to stdout" claim was equally false). Fixed via the same
`_run_info` gate, now also keyed on `graph_format == "json"`. Going one
level deeper found `action_graph`'s OWN internal `info()`/`error()` calls
are STILL ungated — a genuinely separate surface (different function,
needs its own parameter threading and tests). Per this package's own
explicit license to scope down and document rather than silently
under-deliver: filed as **CIU-86**, left unfixed here.

**How tested.** `test_check_json_stdout_is_exactly_one_json_document`
drives `deploy._run(["--check", "--json"])` end to end with the REAL
`action_check` (not mocked) and asserts `json.loads()` on the ENTIRE
captured stdout succeeds — the row's own required oracle, strictly
stronger than a substring check. `test_run_info_routes_to_stderr_under_
json_output` is the narrower `_run_info` unit proof. Five existing
`test_ciu_provisioning.py` tests updated from asserting `[WARN]` in stdout
to asserting it in stderr (two negative tests now also assert absence from
both streams). SPEC S13.4a's stale text corrected; S13.5 cross-referenced;
CONSUMERS.md's "read the JSON object at the end of stdout" workaround
guidance corrected to state the new true contract.

---

## Item 4 — CIU-85: `_clean_in` gains the identity strip its two siblings perform

**What I did.** `worktree._clean_in` built its child environment as
`dict(os.environ)` + target identity, with no strip of the caller's own
identity keys first, unlike its two siblings (`_sanitized_target_env`,
`_resolve_budget_candidates`). `_CIU_IDENTITY_ENV_KEYS` now derives its
identity half from `GENERATED_FACT_ENV_KEYS.values()` (the canonical
fact->env-name table `workspace_env.LEGACY_IDENTITY_ENV_KEYS` already
derives from for the same reason) instead of a hand-written literal — this
adds `PUBLIC_FQDN` by construction, the fact the row named as missing,
without a second list to keep in sync. `CIU_SERVICES_PROFILE` (not an
overlay fact) stays the one hand-added member. `_clean_in` now strips
before overlaying, matching its two siblings exactly.

**A third sibling builder found, folded in rather than left half-fixed.**
`_generate_env_in` carried its OWN separate hand-written six-key literal —
identical to the pre-fix `_CIU_IDENTITY_ENV_KEYS`, so also missing
`PUBLIC_FQDN`. Traced the real consequence: `_detect_public_fqdn` adopts an
ambient pre-set value when nothing independently derived contradicts it —
the exact CIU-47 cross-checkout leak shape, at a different call site than
CIU-47 originally closed. Not named in the backlog row, but the identical
fix, so fixed in the same commit rather than left for a future package to
rediscover.

**How tested.** `test_identity_env_keys_match_the_canonical_fact_table_
plus_profile` pins the derivation.
`test_clean_strips_the_callers_service_profile_selection` is the
controlled-wrong-implementation proof for the `CIU_SERVICES_PROFILE` leak —
manually confirmed it fails against the pre-fix `dict(os.environ)`
implementation. `test_clean_strips_a_stale_ambient_public_fqdn_not_carried_
by_target` proves an FQDN-less target does not inherit the caller's stale
`PUBLIC_FQDN`. The pre-existing `_IDENTITY_KEYS` fixture (used by an
existing, real, non-mocked loop-assertion test) gained `PUBLIC_FQDN` for
free coverage of the third sibling's fix. SPEC S16.6 updated to name all
seven keys and the derivation.

---

## Full local suite — run once, at the end, against the full five-commit HEAD

```
cd /workspaces/vbpub/.worktrees/ciu-P44-small-followups/ciu
python3 run-ciu-tests.py
```

**3418 passed, 8 warnings** (all the same pre-existing third-party
`jsonschema`/`schemathesis` `DeprecationWarning`, unrelated to this
package). **100.00% total coverage, line AND branch, across every module**
— including all five this package touches:

```
src/ciu/deploy.py           1741      0    748      0   100%
src/ciu/provisioning.py      471      0    240      0   100%
src/ciu/scaffold.py          137      0     44      0   100%
src/ciu/workspace_env.py     568      0    208      0   100%
src/ciu/worktree.py         1709      0    694      0   100%
...
TOTAL                        9906      0   4018      0   100%
Required test coverage of 100% reached. Total coverage: 100.00%
```

---

## The real gate — run once, at the end, against the final five-commit HEAD

```
cd /workspaces/vbpub/.worktrees/ciu-P44-small-followups/ciu
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P44-small-followups
```

Verbatim terminal output:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 30 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P44-small-followups/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 7201224fd9f385135b4c75af9f0d56e7f292a653
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P44-small-followups/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

**Verdict artifact, read directly in a separate step**
(`ciu/.assay/verdict-ciu.json`), commit confirmed against `git rev-parse
HEAD` = `7201224fd9f385135b4c75af9f0d56e7f292a653` (exact match):

```json
{
  "commit": "7201224fd9f385135b4c75af9f0d56e7f292a653",
  "outcome": "PASS",
  "exit_code": 0,
  "schema_version": 8,
  "scope": "S1",
  "declared_rigor": ["R0", "R1"],
  "claims": [
    { "rigor": "R0", "status": "PASS", "verified_by_assay": true },
    {
      "rigor": "R1", "status": "PASS", "verified_by_assay": true,
      "coverage": {
        "pct": 100.0, "considered": 5, "covered": 43, "executable": 43,
        "branches_covered": 2, "branches_total": 2,
        "files_missing_coverage": [], "missing_lines": {}, "missing_branch_lines": {}
      }
    }
  ],
  "judgment": {
    "r1": { "fail_under": 100.0, "mode": "changed_lines", "require_branch": true },
    "resolved": { "base": "1967783aeeeb26acf2e52b0da0232a2489ab320e", "base_resolution": "merge-base" }
  },
  "judge_provenance": {
    "artifact": "zipapp", "name": "assay", "version": "3.2.0",
    "digest_algorithm": "sha256",
    "digest": "bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6"
  }
}
```

`judge_provenance.digest` matches `ciu/tools/assay/assay-3.2.0.pyz.sha256`
exactly — judged by the artifact this repo actually vendors, not an ambient
install. `outcome: "PASS"`, `exit_code: 0`, both R0 and R1 `PASS`, R1
changed-line coverage **100.0%** (43/43 executable lines, 2/2 branches)
against `base = 1967783aeeeb26acf2e52b0da0232a2489ab320e` (the merge-base
with `origin/main`, i.e. this whole five-commit package's diff).

**Claim, not summary: this is the real, registered gate — the same
`./run-gate.py ciu` command a merge reviewer would run — judged against
this package's actual five-commit HEAD, and it is green.**

---

## Follow-up filed

**CIU-86** (Low, OPEN): `action_graph`'s own `info()`/`error()` calls (the
empty-graph note, the two shape/provisioning-validation error paths) are
not gated on `--format json` — a narrower, `action_graph`-internal-only
remainder of the CIU-84 sweep. Full detail in
`KNOWN_ISSUES_TODO_BACKLOG.md`.

## What was NOT done / scope notes

Nothing was scoped down within the four assigned items themselves — all
four landed in full, including two real defects found beyond the backlog
rows' own text (CIU-59's semantic nested-`.get` difference; CIU-85's third
sibling builder, `_generate_env_in`) and one sibling class found while
fixing CIU-84 (`ciu graph --format json`'s shared `_run`-level leak). The
one deliberate scope-down is CIU-86, filed rather than folded in, per this
package's own explicit license to do so for a discovery that turned out
structurally separate from the assigned item.

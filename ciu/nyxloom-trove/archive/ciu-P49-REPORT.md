# ciu-P49 — REPORT

Package: `nyxloom-trove/handoffs/ciu-P49-ciu89-probe-container-override-ciu90-governance-cpu-quota.md`
(CIU-89 Part A + CIU-90 Part B). Branch
`feat/ciu-P49-probe-container-and-governance-cpu`, based on vbpub `main` at
`af98e1f0`. Final HEAD at gate time: **`69c573a0`**. **Not merged to
`main`** — a fresh adversarial reviewer verifies first.

## 1. The real gate — verbatim verdict

Command (run from `<worktree>/ciu`):

```
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/feat/ciu-P49-probe-container-and-governance-cpu
```

**Run twice.** First run, at commit `66c990ba` (the feature commit before
the coverage-gap fix below), FAILED — full transcript and diagnosis in §4.
Second run, at `69c573a0` (feature commit + one coverage-only test added),
**PASSED**. Verdict read in a **separate step** from each run (redirected
to a log file, then the verdict JSON artifact read on its own), never off a
piped tail.

Run 1 (`66c990ba`) — tail of the run:

```
run-gate: lane 'ciu' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-ciu-1307506-1788376721.log
run-gate: lane 'ciu' exit 1
```

Verdict artifact for run 1 (`.assay/verdict-ciu.json`, read separately):

```json
{"rigor": "R0", "status": "FAIL", "reason_code": "COMMAND_FAILED", "verified_by_assay": true}
{"rigor": "R1", "status": "FAIL", "reason_code": "UNCOVERED_LINES", "verified_by_assay": true,
 "coverage": {"pct": 97.95918367346938, "executable": 80, "covered": 78,
              "branches_total": 18, "branches_covered": 18,
              "missing_lines": {"ciu/src/ciu/provisioning.py": [462, 463]}}}
"commit": "66c990ba7fdc63b435f07e136ecbbae73b1d0c90", "outcome": "FAIL", "exit_code": 1
```

Two independent findings, addressed separately (§4/§5 below):
1. R1: a real, self-caused coverage gap — the `except (ValueError, KeyError)`
   fallback branch in my new `provides_container` override code
   (`src/ciu/provisioning.py:462-463`) had no test.
2. R0: a test FAILURE in `test_ciu_render_selection_context.py`, in a file
   this package never touches — investigated and determined to be a
   pre-existing, unrelated flake (§4).

Run 2 (`69c573a0`, after fixing the coverage gap) — tail of the run:

```
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 69c573a0c5d2daa2f1d4231aa542b177f1d81048
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
```

Verdict artifact for run 2, in full:

```json
{
  "claims": [
    {"rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": true},
    {"rigor": "R1", "source": "computed", "status": "PASS", "verified_by_assay": true,
     "coverage": {"branches_total": 18, "branches_covered": 18,
                  "considered": 4, "covered": 80, "executable": 80,
                  "missing_lines": {}, "missing_branch_lines": {}, "pct": 100.0}}
  ],
  "commit": "69c573a0c5d2daa2f1d4231aa542b177f1d81048",
  "outcome": "PASS",
  "exit_code": 0,
  "judgment": {"r1": {"mode": "changed_lines", "fail_under": 100.0, "require_branch": true,
                       "allow_excluded": false}},
  "lane": "ciu"
}
```

100% of the changed lines (80 executable, 18 branches, both sides of every
branch) covered — no pragma exclusions.

Full local suite (both parts, run outside the gate container too, for a
faster inner loop): `nice ionice -c3 python3 -m pytest tests/ -q` —
**3582 passed** before the coverage fix, **3583 passed** after (one test
added). Local `--cov=ciu --cov-branch` run independently confirms 100%
total coverage (`TOTAL 10188 0 4106 0 100%`).

## 2. Part A (CIU-89) — behavioral oracle evidence

All five oracle rows from the handoff, run for real
(`tests/tests/test_ciu_provisioning_ciu70_probe_container.py` +
`tests/tests/test_ciu_provisioning.py`, both dedicated CIU-89 sections):

**Row 1 — without `provides_container`, the multi-service `infra/foo-core`
fixture still resolves the WRONG container (regression guard, pinned):**

```
$ python3 -m pytest tests/tests/test_ciu_provisioning_ciu70_probe_container.py::test_ciu89_multi_service_stack_without_override_is_still_wrong -q
.                                                                                [100%]
1 passed
```
Assertion pinned: `[c for c, _ in seen] == ["dstdns-dev-foo-core"]` (the
docker-exec target actually reached is the WRONG container — unchanged,
today's behavior).

**Row 2 — with `provides_container = {"pg:db/bar": "postgres"}`, resolution
is now the REAL service:**

```
$ python3 -m pytest tests/tests/test_ciu_provisioning_ciu70_probe_container.py::test_ciu89_provides_container_override_resolves_the_real_service -q
.                                                                                [100%]
1 passed
```
Assertion pinned: `[c for c, _ in seen] == ["dstdns-dev-postgres"]`.

**Row 3 — controlled wrong implementation, actually run (mutation
CIU89-M1)**: with the override check physically removed from
`_resolve_probe_container` (temporarily editing the source, not merely
described), exactly the 3 override-dependent tests fail, the 2 unaffected
regression guards stay green:

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py tests/tests/test_ciu_provisioning_ciu70_probe_container.py -q -k "provides_container or ciu89 or resolve_probe_container"
FAILED tests/tests/test_ciu_provisioning.py::test_resolve_probe_container_with_override_uses_declared_service_key
FAILED tests/tests/test_ciu_provisioning.py::test_probe_ref_pg_honors_provides_container_override_end_to_end
FAILED tests/tests/test_ciu_provisioning_ciu70_probe_container.py::test_ciu89_provides_container_override_resolves_the_real_service
3 failed, 13 passed, 187 deselected
```
Fix restored, re-ran the same selection: 16 passed, 0 failed.

**Row 4 — slash-free selector byte-identical (pinned, unaffected either
way):**

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py::test_resolve_probe_container_slash_free_selector_byte_identical -q
.                                                                                [100%]
1 passed
```

**Row 5 — an override for a ref NOT in that stack's own `provides` is
rejected, `[S13.2]`-tagged (mutation CIU89-M2, actually run)**: with
`config_model.validate_stack_provisioning`'s new `provides_container` block
disabled (the line `provides_container = root_section.get(...)` replaced
with `provides_container = None`), exactly 5 tests in
`test_ciu_provisioning.py` and 1 in the dedicated CIU-89 file fail:

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py -q -k "provides_container"
FAILED test_validate_stack_provisioning_rejects_provides_container_not_a_table
FAILED test_validate_stack_provisioning_rejects_provides_container_key_not_in_provides
FAILED test_validate_stack_provisioning_rejects_provides_container_empty_value
FAILED test_validate_stack_provisioning_rejects_provides_container_non_string_value
FAILED test_validate_stack_provisioning_provides_container_absent_key_still_reports_other_violations
5 failed, 4 passed, 172 deselected

$ python3 -m pytest tests/tests/test_ciu_provisioning_ciu70_probe_container.py -q -k "ciu89"
FAILED test_ciu89_config_model_rejects_an_override_for_an_undeclared_ref
1 failed, 2 passed, 19 deselected
```
Fix restored; both selections back to fully green.

**Regression on the OTHER two threaded builders** (not in the handoff's own
oracle list, added because tracing real `probe_ref(..., stacks=...)` call
sites found `action_check`'s own builder is a second real feeder,
independent of `provisioning_graph`):

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py::test_action_check_live_mode_threads_provides_container_to_probe_ref -q
.                                                                                [100%]
1 passed
```

**Deliberately NOT threaded** — `action_graph`'s own builder. First attempt
threaded it too (for dict-shape consistency); a REAL test caught this as
wrong:

```
FAILED tests/tests/test_ciu_deploy_deeper2.py::test_graph_emits_machine_readable_edges_for_valid_rendered_topology
AssertionError: assert {'applications/api': {'provides': [], 'provides_container': {}, 'requires': [...]}} == {'applications/api': {'provides': [], 'requires': [...]}}
```
`render_graph`'s `fmt="json"` path echoes the `stacks` dict verbatim into
`--graph --fmt json`'s public `"stacks"` key; `--graph` never resolves a
probe container, so the extra key was an unasked-for shape change to an
external contract. Reverted; the test above is unchanged/green again with
the revert in place.

## 3. Part B (CIU-90) — behavioral oracle evidence

All oracle rows, plus the live `docker inspect` verification.

**Oracle 1 — unset default injects no `cpus` key (regression guard):**

```
$ python3 -m pytest tests/tests/test_ciu_governance.py::TestBuildInjections::test_cpus_unset_injects_no_cpus_key -q
.                                                                                [100%]
1 passed
```

**Oracle 2 — `governance.cpus = "2"` injects `cpus: "2"`:**

```
$ python3 -m pytest tests/tests/test_ciu_governance.py::TestBuildInjections::test_cpus_configured_injects_the_key -q
.                                                                                [100%]
1 passed
```
Type chosen for the injected value: **string** (`"2"`), matching
`mem_limit`'s own convention and Compose's accepted `cpus` shape — the
author's exact decimal text reaches the overlay unmodified, not coerced
through a Python float.

**Oracle 3 — author-set `cpus:` key is left untouched:**

```
$ python3 -m pytest tests/tests/test_ciu_governance.py::TestBuildInjections::test_cpus_author_set_key_is_left_untouched -q
.                                                                                [100%]
1 passed
```

**Oracle 4 — `"0"` and `"-1"` both raise `[S15.21]`:**

```
$ python3 -m pytest tests/tests/test_ciu_governance.py -q -k "test_cpus_zero_raises_s15_21 or test_cpus_negative_raises_s15_21"
..                                                                               [100%]
2 passed
```

**Controlled wrong implementation, actually run (mutation CIU90-M1)** —
disabled the `build_injections` cpus conditional (`if "cpus" not in
author_keys and cpus:` → `if False and ...`):

```
$ python3 -m pytest tests/tests/test_ciu_governance.py -q -k "cpus"
FAILED TestBuildInjections::test_cpus_configured_injects_the_key
1 failed, 9 passed, 152 deselected
```
Exactly the "configured injects the key" oracle failed, as the handoff
predicted. Restored; re-ran: 11 passed, 0 failed.

**Controlled wrong implementation, actually run (mutation CIU90-M2)** —
disabled `resolve_config`'s cpus validation (`if cpus:` → `if False and
cpus:`):

```
$ python3 -m pytest tests/tests/test_ciu_governance.py -q -k "cpus"
FAILED TestResolveConfig::test_cpus_zero_raises_s15_21
FAILED TestResolveConfig::test_cpus_negative_raises_s15_21
FAILED TestResolveConfig::test_cpus_non_numeric_raises_s15_21
3 failed, 7 passed, 152 deselected
```
Exactly the three raise-oracles failed. Restored; re-ran: 10 passed, 0
failed.

**Live verification — real `docker compose up` + real `docker inspect`,
mirroring the CIU-90 backlog entry's own method:**

Step 1, the real computed fragment (`governance.build_injections()`, not
hand-written):

```python
cfg = gov.resolve_config({"enabled": True, "cgroup_parent": "dev-background.slice", "cpus": "1.5"})
injections, notes = gov.build_injections(
    {"probe": {"image": "alpine:3.19", "command": ["sleep", "3600"]}}, cfg)
```
Output:
```json
{"probe": {"cgroup_parent": "dev-background.slice", "mem_limit": "1g",
           "memswap_limit": "17g", "mem_reservation": "256m", "cpus": "1.5"}}
```
`notes` includes `cpus=1.5` (device autodetect failed on this host, so
`blkio_config` correctly absent — unrelated to CPU governance).

Step 2, fed the exact fragment into a real compose file and brought up a
real container:

```
$ docker compose -p ciu-p49-cpu-livecheck up -d probe
 Container ciu-p49-cpu-livecheck-probe-1  Started

$ docker inspect --format '{{.HostConfig.NanoCpus}}' ciu-p49-cpu-livecheck-probe-1
1500000000
```
`1500000000 == 1.5 * 1e9` — Docker's own `NanoCpus` representation of the
`cpus: "1.5"` compose key `build_injections()` computed. `CpuQuota`/
`CpuPeriod` both read `0` — confirms the modern single-key form was used,
not the legacy `cpu_quota`/`cpu_period` pair, as the handoff's design call
specified.

Step 3, the unset-default case on the identical harness (no `cpus` key in
the compose file at all):

```
$ docker compose -f docker-compose-unset.yml -p ciu-p49-cpu-livecheck-unset up -d probe
$ docker inspect --format '{{.HostConfig.NanoCpus}}' ciu-p49-cpu-livecheck-unset-probe-1
0
```

Both containers and their networks torn down immediately after
(`docker compose down` on each), confirmed clean with a post-hoc filter:

```
$ docker ps -a --filter "name=ciu-p49-cpu-livecheck" --format '{{.Names}}'
$ docker network ls --filter "name=ciu-p49-cpu-livecheck" --format '{{.Name}}'
(both empty)
```

## 4. R0 failure investigation (run 1, commit `66c990ba`) — pre-existing,
unrelated flake, not this package's own defect

`test_engine_threads_selection_into_configfiles_and_hooks`
(`tests/tests/test_ciu_render_selection_context.py`) failed inside the
assay-sandboxed run with:

```
shutil.Error: [('.../test-repo/applications/app-config/ciu.toml',
  '.../repo/applications/app-config/ciu.toml',
  "[Errno 2] No such file or directory: '.../ciu.toml'")]
```

This package's diff never touches `test-repo/`, `test_ciu_test_repo.py`, or
`test_ciu_render_selection_context.py`. Investigated rather than dismissed:

- `test-repo/**/ciu.toml` is gitignored (`.gitignore:6 **/ciu.toml`),
  generated, never committed.
- `tests/tests/test_ciu_test_repo.py` renders it **directly into the
  committed `test-repo/` tree** (not a `tmp_path` copy) —
  `test_render_global_and_stack_configs` (renders all four stacks) and
  `test_app_config_full_pipeline_runs_under_dry_run` (whose own
  `_clean_stack_artifacts` helper UNLINKS `ciu.toml` at the START of that
  test, as prep for a fresh render).
- `test_ciu_render_selection_context.py`'s `_add_stack` helper
  `shutil.copytree`s that same `test-repo/applications/app-config`
  directory as a template, implicitly depending on the OTHER file's render
  having already happened.
- pytest-xdist `--dist loadfile` only orders tests WITHIN one file; two
  DIFFERENT files sharing the SAME on-disk `test-repo/` directory (not
  per-worker copies) is a genuine cross-worker TOCTOU race — a real
  pre-existing test-suite hygiene gap, not a hypothesis.
- **Did not reproduce on retry**: an immediate second gate run at the very
  next commit (`69c573a0`, code-identical aside from one unrelated
  coverage-only test) PASSED cleanly — consistent with a genuine
  intermittent race, not a deterministic regression this package caused.

Filed as **CIU-91** in `KNOWN_ISSUES_TODO_BACKLOG.md` (severity Medium,
OPEN, proposed fix directions recorded, not implemented — out of this
package's own disjoint-files scope). Neither CIU-89 nor CIU-90's own rows
were touched by this filing.

## 5. Doc/backlog updates

- `docs/SPEC.md`: S13.2 gained a `provides_container` subsection; S15.1's
  declaration block, S15.3's injection table (`cpus` row) and "five keys" →
  "six keys" language; new S15.21 section.
- `docs/CONFIG.md`: `provides_container` worked example under
  requires/provides; `governance.cpus` paragraph + worked example under
  `[<root>.governance]`.
- `KNOWN_ISSUES_TODO_BACKLOG.md`: CIU-89 and CIU-90 rows OPEN → FIXED with
  the real shipped mechanism and file:line citations from this diff; new
  CIU-91 row filed (§4).
- `CHANGES.md`: `## [7.11.0] - UNRELEASED` draft section added at the top,
  framed as `feat(ciu):` (both keys are additive/opt-in — see the commit
  message for the full framing rationale), with an explicit note asking the
  releaser to fold CMRU's generated digest into the SAME section rather
  than leaving a stale separate `[Unreleased]` header (the recurring gap
  this estate's own memory already flags).

## 6. Commits (this package)

1. `66c990ba` — `feat(ciu): CIU-89 -- provides_container probe-container
   override + CIU-90 -- governance.cpus quota key (ciu-P49)` — all source,
   test, and doc changes for both parts, the two backlog rows, and the
   CHANGES.md draft.
2. `69c573a0` — `test(ciu): ciu-P49 -- cover _resolve_probe_container's
   unresolvable-config fallback for a provides_container override` — the
   R1 coverage-gap fix (§1).
3. `880a6efb` — `backlog(ciu): file CIU-91 -- test-suite flake found
   during ciu-P49's own gate run` — the CIU-91 filing (§4).
4. `efcd7260` (superseded by this commit) — `docs(ciu): ciu-P49 --
   LOG/REPORT for CIU-89 + CIU-90` — this LOG/REPORT pair.

## 7. Closing discipline

Every number above is from a command actually run in this session, not
paraphrased: both gate runs' verdict JSON was read as a separate step from
the run (never a piped tail); every mutation was applied by editing the
real source, running the targeted tests, observing the predicted failure
set exactly, then restoring the fix and re-confirming green; the live
`docker inspect` values are copy-pasted from the actual terminal output,
and the containers/networks created for that check were confirmed torn
down with a post-hoc filter.

---

# REPORT addendum — review-fix pass (commit `57348c00`)

Adversarial review of `13046641` returned **ACCEPT-conditional**: real
gate re-run clean in the reviewer's own control worktree (R0/R1 PASS,
100% coverage, matched this REPORT's own numbers), all 4 original
mutation tests independently confirmed real, the live `docker inspect`
numbers reproduced exactly, the `action_graph` exclusion (§2 above)
verified true and correctly pinned, scope clean (13 files, all
authorized), CIU-91's root cause independently confirmed correct. One
blocker + several strongly-recommended items came back; all addressed in
one commit, `57348c00`.

## A. Blocker — `TypeError` regression, real reproduction

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py -q -k "nested_list"
2 passed
```
Mutation (the buggy `set(declared_provides)` form, without the string
filter, restored temporarily):
```
E   TypeError: cannot use 'list' as a set element (unhashable type: 'list')
    src/ciu/config_model.py:1308: TypeError
FAILED test_validate_stack_provisioning_provides_containing_a_nested_list_raises_valueerror_not_typeerror
FAILED test_validate_stack_provisioning_provides_containing_a_nested_list_maps_to_exit_2_via_engine
2 failed, 187 deselected
```
Restored the fix; re-ran: 2 passed, 0 failed. The reproduced traceback is
the EXACT `TypeError` the review reported.

## B. Strongly recommended #1 — `provides_container` kind gate

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py -q -k "kind_override or malformed_ref_does_not_double"
5 passed
```
Mutation (kind check disabled via `if False and ref_kind not in (...)`):
```
FAILED test_validate_stack_provisioning_rejects_vault_kind_override
FAILED test_validate_stack_provisioning_rejects_consul_kind_override
FAILED test_validate_stack_provisioning_rejects_stack_kind_override
3 failed, 1 passed, 185 deselected
```
`test_validate_stack_provisioning_accepts_minio_kind_override` stayed
green under the mutation, confirming the mutation is precise (only the
NEW gate is disabled, nothing else). Restored; re-ran: 5 passed, 0
failed.

## C. Should-fix — real `tomllib` round-trip, permanent regression

```
$ python3 -m pytest tests/tests/test_ciu_provisioning_ciu70_probe_container.py -q -k "real_toml_round_trip"
1 passed
```
The test parses:
```toml
[db_core]
provides = ["pg:db/dstdns", "pg:role/controller"]
provides_container = { "pg:db/dstdns" = "postgres" }
```
with `tomllib.loads`, then drives it through
`config_model.validate_stack_provisioning` (passes clean) ->
`deploy.provisioning_graph` (confirms `provides_container` survives the
graph-builder round-trip) -> `provisioning._resolve_probe_container`,
asserting:
- `pg:db/dstdns` (overridden) -> `p-t-postgres`
- `pg:role/controller` (sibling, un-overridden) -> `p-t-db-core`

Both numbers match the reviewer's own hand-verified figures exactly.

## D. `docs/FEATURES.md` + CIU-91 correction

Doc-only; no behavioral test. `docs/FEATURES.md:262-268`'s "keyed
anything" bullet now carries the same qualifying clause + `provides_container`
pointer SPEC.md's S13.2 has. `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-91 row
corrected: the wrong "ran before generation" alternative-mechanism claim
struck (my own captured `entries` list from the first gate run already
proved `ciu.toml` WAS present at `os.scandir` time — a vanish-mid-copy
TOCTOU race, not an absence), fix direction (c) flagged as not actually a
fix for the confirmed mechanism, (b) flagged as the real one.

## E. Full suite + real gate, both re-run at `57348c00`

```
$ python3 -m pytest tests/ -q --cov=ciu --cov-branch --cov-report=term-missing:skip-covered
TOTAL   10195      0   4108      0   100%
3591 passed, 31 warnings in 68.61s
```

Real gate, verdict read in a separate step (never a piped tail):

```
$ ./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/feat/ciu-P49-probe-container-and-governance-cpu
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 57348c00afca1006bf40ff385b1a63c180b36b41
```

`.assay/verdict-ciu.json`, in full:
```json
{
  "claims": [
    {"rigor": "R0", "status": "PASS", "verified_by_assay": true},
    {"rigor": "R1", "status": "PASS", "verified_by_assay": true,
     "coverage": {"branches_total": 20, "branches_covered": 20,
                  "executable": 94, "covered": 94,
                  "missing_lines": {}, "missing_branch_lines": {},
                  "pct": 100.0}}
  ],
  "commit": "57348c00afca1006bf40ff385b1a63c180b36b41",
  "outcome": "PASS",
  "exit_code": 0
}
```

## F. Commits, this addendum

6. `57348c00` — `fix(ciu): ciu-P49 review fix pass -- TypeError->ValueError
   blocker + provides_container kind gate + FEATURES.md/CIU-91
   corrections` — all six review items above, one commit.
7. `9385168d` — this REPORT/LOG addendum.

## G. Items explicitly NOT done (controller ruling, recorded here per
instruction, not filed as new backlog entries)

- `governance.cpus` string validation is looser than Docker Compose's own
  `cpus` parser would be (accepts any `float(...) > 0`-parseable string,
  not Compose's own stricter grammar).
- `provides_container` is not gated by `ciu check`'s `if requires or
  provides:` guard when `provides` itself is empty/absent for that stack.
- `composefile.py`/`SPEC.md` S15.7/S15.8 carry `INJECTED_KEYS`-adjacent
  enumerations that already omitted `memswap_limit` before this package
  (pre-existing, not introduced or worsened here).

All three: reviewer's own call was "can ride a later package," controller
agreed — noted here for the controller's own tracking decision.

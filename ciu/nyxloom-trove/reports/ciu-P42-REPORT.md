# ciu-P42 — REPORT (CIU-75: the overlay becomes the sole instance-fact source)

Worktree: `/workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2`
Branch: `fix/ciu-P42-cutover-identity-f2`, cut from `main` @ `332af5a1`,
**rebased onto `main` @ `815c50d6`** (the ciu-P43 merge) — §11.
Commits: `ebcc7ad7` (code + tests), `f8f29778` (docs + backlog + LOG),
`67a588f8` (this REPORT) — pre-rebase `c1985542` / `788908e2`.
Ships as: **ciu 7.7.0** — BREAKING, a deliberate, recorded override of the
estate's "breaking waits for the next major" convention.

**Read §11 first if you are reviewing after ciu-P43.** That package landed
`HookContext.identity_unreadable` in the two functions this one rewrites; §11
is the reconciliation, and the one place a silent regression was possible.

---

## 1. What was done

`[ciu.instance.generated]` in `<ciu-root>/ciu.global.worktree.toml.j2` —
written by every `ciu env generate` since CIU-60 — is now the **only** record
CIU itself reads instance identity from. `ciu.env` is demoted to a legacy,
**write-only** export: still written, unchanged key set and format, from the
same in-memory values, and never read back by any CIU internal.

New in `workspace_env.py` (SPEC **S3.1c**, a new normative section):

| function | job |
|---|---|
| `read_generated_facts(ciu_root)` | the read side of S3.1b. `{}` for absent overlay / absent table; `WorkspaceEnvError` for a PRESENT record that cannot be read |
| `has_generated_facts(ciu_root)` | presence, never readability — the post-cutover readiness signal |
| `identity_env_from_facts(facts)` | the one snake_case → SCREAMING_CASE translation |
| `read_instance_identity_env(ciu_root)` | the two composed |

### Why a text-level scan, not `render_global_chain`

The task summary said fact-reads move to "the merged overlay/rendered instance
config". They read the merged value — but by scanning the block the S3.1b
writer owns, not by rendering the chain. A reviewer should hold me to this, so
the reasoning is stated plainly:

1. the overlay is a **Jinja template**; rendering it needs the merged config it
   is itself a layer of, and CIU-74 made an undefined name a hard error — while
   the CIU-owned block is plain TOML by construction (`json.dumps`'d strings),
   readable with no context at all;
2. six of the twelve sites read a checkout that is **not** this process's repo
   root (a shared-infra reference, a budget candidate, a reap group), whose
   committed config chain may legitimately be absent or broken. The `ciu.env`
   read they replace had no such dependency, and a replacement that acquired
   one would be a behaviour change disguised as a refactor;
3. the block is merged **last** and written by CIU alone, so its own bytes ARE
   the merged value. A chain render could only agree with it.

`OSError`, `UnicodeDecodeError` and malformed TOML — CIU-62's three unrelated
failure types — are normalized to one `WorkspaceEnvError` at the reader, so no
call site has to re-derive that the last two are sibling `ValueError`
subclasses and that neither covers the first.

---

## 2. Per-site classification — all 12

Sites re-derived from source, **not** taken from the entry's line numbers
(which had moved, as the entry warned):
`grep -rn '"ciu\.env"\|WORKSPACE_ENV' src/ciu/` → exactly twelve exact-path
constructions across the three named modules.

| # | site (function) | file:line (pre-fix) | classification | what it read | disposition |
|---|---|---|---|---|---|
| 1 | `_preflight_shared_infra_for_add` | `worktree.py:1137` | existence check **+ fact-read** | reference checkout's `DOCKER_NETWORK_INTERNAL` | → `read_generated_facts(ref_ciu_root)["network"]`. The two separate refusals (file absent / key absent) collapse into one honest message: both mean "this reference declares no network". Also rebuilds `ref_env` (the environment the reference's own chain renders against) as ambient-minus-identity + the reference's facts |
| 2 | `_clean_in` | `worktree.py:1232` | existence check **+ fact-read** | the whole parsed `ciu.env`, overlaid on ambient to build the child `ciu clean` environment | → `read_instance_identity_env(worktree)`. Only the IDENTITY keys are overlaid now (see §3 delta 1) |
| 3 | `_reap_uses_clean` | `worktree.py:2652` | **pure existence check** | `(ciu_root/"ciu.env").is_file()` as "can this checkout still clean itself?" | → `has_generated_facts(ciu_root)`. **Did not stay as-is** — see §4 |
| 4 | `_sanitized_target_env` | `worktree.py:2901` | fact-read | the target instance's 5 required identity keys, validated against the record | → `read_generated_facts(record.ciu_root)`; `_REQUIRED_TARGET_ENV_KEYS` → `_REQUIRED_TARGET_FACTS` (`public_fqdn` deliberately excluded: CIU derives it only when the workspace declares one, so requiring it would refuse every FQDN-less instance) |
| 5 | `_runtime_identity` | `worktree.py:3267` | fact-read | `INSTANCE_ID` + `DOCKER_NETWORK_INTERNAL` right after a generate | → `instance_id` + `network` from the table |
| 6 | `connect_shared_infra_after_up` | `worktree.py:3876` | existence check **+ fact-read** | reference's CURRENT `DOCKER_NETWORK_INTERNAL`, compared to the recorded intent | → `read_generated_facts(ref_ciu_root)["network"]`. The absent-file refusal merges into the existing "reference network changed (recorded X, now (absent))" guard, which already covered the empty case |
| 7 | `_resolve_budget_candidates` | `worktree.py:4293` | existence check **+ fact-read** | each candidate's `DOCKER_NETWORK_INTERNAL`, and the candidate's whole `ciu.env` as `environ=` for its own chain render | → the table; "not a CIU instance, skip" is now "no generated facts". Render environment reshaped (see §3 delta 1) |
| 8 | `identity_compose_project_name` | `engine.py:971` | existence check **+ fact-read** | `REPO_NAME` + `INSTANCE_ID` → the workspace-identity compose project | → `repo_name` + `instance_id`. This is the one both `up` and `clean` call, so they cannot drift |
| 9 | `workspace_ownership_labels` | `engine.py:1221` | fact-read | `INSTANCE_ID` + `PHYSICAL_REPO_ROOT` → the S16.9 label pair | → `instance_id` + `physical_repo_root` |
| 10 | `main_execution` S3.12 HookContext | `engine.py:1538` | existence check **+ fact-read** | `INSTANCE_ID` + `DOCKER_NETWORK_INTERNAL` for a REAL run's hook context | → the table; the `{}`-degradation-plus-WARN contract is unchanged. Post-rebase it also sets ciu-P43's `identity_unreadable` (§11) |
| 11 | `_workspace_identity` | `deploy.py:2220` | existence check **+ fact-read** | the `ciu check` twin of #10 | → the table. Must move as a PAIR with #10 (S3.12/CIU-44), and did. Post-rebase it returns ciu-P43's `(facts, identity_unreadable)` tuple (§11) |
| 12 | `_workspace_identity_network` | `deploy.py:3285` | existence check **+ fact-read** | `DOCKER_NETWORK_INTERNAL` for `clean`'s identity-network removal + survivor check | → `network`. CIU-62's absent-vs-indeterminate distinction preserved exactly |

**Totals: 11 fact-reads migrated, 1 existence check re-pointed.**

Four further `ciu.env` uses live in `cli.py` (`_env_show`, `_env_print`). Those
are **not** internal identity reads — they are the legacy export's own
shell-facing surface, which contract item 2 keeps. Both gained the deprecation
notice instead.

---

## 3. Behaviour deltas a reviewer should scrutinize

Both are deliberate, both are in the code comments, `CHANGES.md` and SPEC
S3.1c, and both changed an existing test's *claim* rather than its fixture.

**Delta 1 — the child/render environment at sites 2, 4 and 7 is now
ambient-minus-identity + the target's own facts.** Pre-cutover, site 2
overlaid every `ciu.env` key onto ambient, and site 7 substituted the
candidate's `ciu.env` *wholesale* (no ambient at all). The overlay carries
identity facts only, so passing just those six as the entire environment would
have broken any candidate template referencing a machine fact — which, under
CIU-74's `StrictUndefined`, is now a hard error rather than an empty string.
The rule adopted instead is the one site 4 already used: strip every CIU
identity key from ambient, put the target's own facts back. Identity still
comes exclusively from the target checkout — the property those functions
exist to hold — while machine facts (`USER_UID`, `DOCKER_GID`,
`PYTHON_EXECUTABLE`, `HOST_MDT_TMP`) come live from this process instead of a
file that may predate a rebuild. On one host they are the same values.

`test_ciu_worktree_budget.py::test_candidate_env_isolation_ambient_leak_does_not_apply`
asserted the strictly-stronger old property and could not survive; it is now
`…_ambient_identity_never_leaks` and proves the property that matters — an
ambient `INSTANCE_ID` set to a third value reaches neither candidate.

**Delta 2 — a DIRECTORY where the identity record belongs is now
indeterminate, not "absent".** `ciu.env`'s reads were guarded by `is_file()`,
which folded "exists and cannot be read" into "never provisioned" — the
estate's absence-for-emptiness anti-pattern.
`deploy._workspace_identity` now warns and degrades (it never raises, by
contract); the genuinely-absent case stays silent, and that legitimate state is
constructed as its own test
(`test_workspace_identity_absent_record_stays_silent`) so this cannot become a
superset refusal.

**One consequence worth naming, which is not mine but surfaces here.** Because
the overlay is *also* a config-chain layer, corrupting the overlay FILE now
fails `render_global_chain` before a run reaches the S3.12 identity read. Two
`test_ciu_render_selection_context.py` arcs therefore monkeypatch the reader
rather than the file; the three file-level corruption forms are proven against
the reader itself in the new cutover suite. Relatedly, a checkout with no
committed config now renders (its gitignored overlay alone is a legal layer —
true since CIU-60), so the shared-infra "no global config at all" refusal
arrives one step later, from `container_name`; the render-failure arm is still
covered, by a genuinely unparseable template.

---

## 4. The one existence check, and why it moved

`worktree._reap_uses_clean` asked "can this checkout still clean itself?" by
looking for `ciu.env`. Post-cutover `ciu clean` derives **both** its identity
network (`deploy._workspace_identity_network`) and its identity compose project
(`engine.identity_compose_project_name`) from the overlay table. A checkout
carrying only a legacy `ciu.env` therefore can no longer clean itself, and the
old check would have certified a readiness that no longer holds — AGENTS.md's
"a check is only as strong as what it actually compares".

It now asks for the table's **presence**, and deliberately not its
readability: a present-but-corrupt table still answers `True`, so `_clean_in`
refuses loudly (and `reap` reports that refusal as a partial) instead of the
predicate quietly demoting indeterminacy into a bare `docker rm` of a MANAGED
instance — which is exactly what the ciu-P28 hotfix lesson forbids.

---

## 5. Evidence: the delete-`ciu.env`-after-generate test

`ciu/tests/tests/test_ciu_identity_cutover_ciu75.py` (24 tests). It builds a
real `git init` repo plus a real `git worktree add` linked worktree, runs the
**real** `generate_ciu_env` in both (only its host-fact detectors pinned), and
then parametrizes the whole site sweep over two manglings of the legacy export:

```python
_MANGLERS = {
    "deleted": lambda p: p.unlink(),
    "corrupt": lambda p: p.write_bytes(b'\xff\xfe not = shell "at all\n'),
}
```

Every site is measured **before** the mangle and asserted identical after:

| test | sites covered |
|---|---|
| `test_cutover_engine_sites_ignore_a_broken_ciu_env` | 8, 9 |
| `test_cutover_deploy_sites_ignore_a_broken_ciu_env` | 11, 12 (and asserts nothing was announced — no silent degradation) |
| `test_cutover_worktree_identity_sites_ignore_a_broken_ciu_env` | 3, 4, 5, 2 (the child env is captured and compared byte-for-byte) |
| `test_cutover_shared_infra_sites_ignore_a_broken_ciu_env` | 1, 6 (docker-free oracles: the preflight's network is captured at the `_check_reference_network_and_projects` seam; the post-up guard names the current network in its refusal) |
| `test_cutover_budget_candidates_ignore_a_broken_ciu_env` | 7 (both networks and both derived compose projects) |
| `test_cutover_leaves_the_overlay_as_the_only_load_bearing_record` | the **converse** — remove the OVERLAY instead and the same sites stop answering |

That last one is the load-bearing test. Without it, "still works with `ciu.env`
deleted" would also be satisfied by a site that reads neither record.

Site 10 (`engine.main_execution`'s HookContext identity) is the real-run twin
of site 11 and is covered end-to-end by
`test_ciu_render_selection_context.py`'s S3.12 arcs plus the reader suite here;
its `{}`-degradation contract is asserted to remain identical to site 11's.

Verbatim, post-rebase (the suite was updated for ciu-P43's tuple — §11):

```
$ PYTHONPATH=src python3 -m pytest tests/tests/test_ciu_identity_cutover_ciu75.py -q
24 passed, 1 warning in 1.11s
```

---

## 6. Gate verdict — verbatim

Command (from `/workspaces/vbpub/ciu`, worktree ROOT, not `<worktree>/ciu`):

```
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2
```

Verdict lines, pasted verbatim from the **post-rebase** run's own output (the
one line elided is `run-gate: docker argv: …`, the ~1.5 kB container
invocation; it is in the raw capture and says nothing about the verdict):

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 30 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 8cc79745f281087fe5dceba2422df33ace300e34
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
run-gate: WARNING: lane history not recorded: /workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2/ciu/.run-gate is not fully git-ignored, and writing there would leave the judged tree dirty for the NEXT lane's clean-tree check — add '.run-gate/' to the .gitignore covering /workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2
```

```
GATE_EXIT=0
```

Two things in that output are new since this package's first run and are
**not** verdict problems, so they are named rather than left to be wondered
about:

- `rev 30`, not `rev 29`. `main` gained run-gate-P03 (RG-27, lane invocation
  history) between the runs, and `run-gate.py` is invoked from the primary
  checkout. The lane, environment, judge and verdict artifact are unchanged.
- the `WARNING: lane history not recorded` line is RG-27's brand-new
  book-keeping declining to write `.run-gate/` into a tree whose `.gitignore`
  does not cover it — this branch is based on `815c50d6`, which predates the
  `.gitignore` line main added alongside RG-27. It is a warning about the
  gate's own history file, it did not write anything, `git status` in the
  worktree is clean, and the lane still exits 0. It resolves itself on merge.

`GATE_EXIT` was captured with its own marker and read in a separate step, never
from a pipe tail.

This is a **genuinely new** gate run, not a re-quote: the rebase produced code
no previous run had ever executed (CIU-75's readers under CIU-80's tuple), and
ciu-P43 also bumped the judge, so this verdict is the first for this package
under **assay 3.2.0** (verdict schema 8) rather than 2.3.0. The superseded
pre-rebase verdict at `788908e2` survives only as prose, in LOG Entry 2; the
artifact on disk is this run's, and nothing about it is inherited.

### Commit-hash match (the ciu-P40 lesson)

```
$ git -C /workspaces/vbpub/.worktrees/ciu-P42-cutover-identity-f2 rev-parse HEAD
8cc79745f281087fe5dceba2422df33ace300e34
```

`.assay/verdict-ciu.json` (verbatim, abridged only where noted):

```json
{
  "argv_declared": ["/opt/tester-venv/bin/python", "run-ciu-tests.py"],
  "argv_effective": ["/opt/tester-venv/bin/python", "run-ciu-tests.py"],
  "argv_modified": false,
  "argv_appended": [],
  "assay_version": "3.2.0",
  "claims": [
    { "rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": true },
    {
      "coverage": {
        "branch_capability": "reported",
        "branches_covered": 18,
        "branches_total": 18,
        "considered": 8,
        "covered": 236,
        "excluded_lines": {},
        "exclusion_capability": "reported",
        "executable": 236,
        "files_missing_coverage": [],
        "files_with_excluded_lines": [],
        "files_with_missing_branch_lines": [],
        "files_with_unclassified_lines": [],
        "missing_branch_lines": {},
        "missing_lines": {},
        "pct": 100.0,
        "unclassified_lines": {}
      },
      "rigor": "R1", "source": "computed", "status": "PASS", "verified_by_assay": true
    }
  ],
  "commit": "8cc79745f281087fe5dceba2422df33ace300e34",
  "declared_evidence": [],
  "declared_rigor": ["R0", "R1"],
  "enforcement": "gate",
  "env_declared": { "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "src" },
  "evidence": [],
  "exit_code": 0,
  "judge_provenance": {
    "artifact": "zipapp", "name": "assay", "version": "3.2.0",
    "digest_algorithm": "sha256",
    "digest": "bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6"
  },
  "judgment": {
    "r1": { "allow_excluded": false, "coverage_artifact": "coverage.json",
            "coverage_format": "coverage-py-json", "fail_under": 100.0,
            "mode": "changed_lines", "require_branch": true },
    "resolved": { "base": "815c50d6723e038f97f28e3dc4cc7de2016bf845",
                  "base_resolution": "merge-base",
                  "language": "python", "source_roots": ["src"] }
  },
  "lane": "ciu",
  "outcome": "PASS",
  "schema_version": 8,
  "scope": "S1",
  "snapshot_policy": { "selection": "repository-minus-unsafe-symlinks",
                       "unsafe_symlink_omissions": ["… 3 topos fixture links …"] },
  "started": "2026-08-31T04:12:51.377665+00:00",
  "ended":   "2026-08-31T04:13:27.999704+00:00"
}
```

(Abridgements, all marked: `env_effective` — `env_declared` plus `HOME`,
`LANG`, `PATH`, `CGROUP_PARENT_DEV_BACKGROUND` — and the three
`unsafe_symlink_omissions` paths, which are `topos/` fixtures unrelated to
this lane.)

`verdict-ciu.json.commit` == `HEAD` at that run == `8cc79745f281087fe5dceba2422df33ace300e34`. **Match confirmed.**

**No baseline or comparison gate was run at any point in this package**, so
there is nothing that could have overwritten this artifact. (The R1
`considered: 8 / covered: 236 / pct 100.0` figures are the *changed-line*
judgment; the 100% whole-source line+branch floor is enforced inside R0's own
argv — `run-ciu-tests.py` carries `--cov-fail-under=100 --cov-branch` — and its
exit 0 is that floor being met.)

R1's resolved `base` is `815c50d6`, the ciu-P43 merge — correct for a branch
rebased onto it, and worth checking rather than assuming, because it is the
gate-base trap: judge against the wrong base and `changed_lines` silently
measures the wrong set. (The first post-rebase run, at `67a588f8`, resolved
`a1d1dec3` instead — `main`'s ref had not yet advanced past the P43 merge at
that moment — which made its changed-line set a superset, the conservative
direction. Both runs pass; this one is the one on disk.)

### Final confirmation run (against the last commit on this branch)

The verdict quoted above was produced at `8cc79745`, the branch tip when it
ran. This LOG/REPORT update is a later, report-only commit, so the gate was
**re-run against that final commit** after it was written, and
`ciu/.assay/verdict-ciu.json` on disk is that run's artifact. Its `"commit"`
therefore equals `git rev-parse HEAD` exactly; a reviewer can check that in one
step without needing a hash quoted here (which no file can carry about its own
commit). That run is also the LAST gate invocation of this package — no
baseline or comparison run followed it, so nothing overwrote the artifact.

Local confirmation of the same floor before the gate, for what it is worth:

```
$ python3 run-ciu-tests.py -q
3399 passed, 8 warnings in 30.72s
TOTAL   9876   0   4016   0   100%
Required test coverage of 100% reached. Total coverage: 100.00%
```

(`3393`/`9865` pre-rebase; the deltas are ciu-P43's own tests and lines,
merged in — see §11.)

---

## 7. Consumer sweep across dstdns

**dstdns WAS reachable** at `/workspaces/dstdns` (HEAD `dstdns@a1098ad8`). The
entry's "plausible but unconfirmed" read-back risk is **CONFIRMED, not ruled
out**. Four shapes, all still working this release, all documented in
`docs/CONSUMERS.md` §11b:

1. **`dstdns/scripts/ciu/workspace_env.py`** — the important one. A *vendored
   stub* that PARSES `ciu.env` into a dict (`load_workspace_env` /
   `ensure_workspace_env`) for `scripts/config_helper.py` inside the
   test-runner container, which mounts only the dstdns repo and has no `ciu`
   on `PYTHONPATH`. It is a **second implementation of a read CIU has now
   moved**, so the two can drift with nothing detecting it.
   `dstdns/tests/smoke/test-deployment-validation.py` reaches
   `DOCKER_NETWORK_INTERNAL` through it;
   `scripts/config_helper.py:148` and `scripts/url_builder.py:69` carry error
   text prescribing `source ciu.env`.
2. **`env-workspace-setup-generate.sh:254-258`** and
   **`.devcontainer/finalize.post.d/10-dstdns-ciu.sh:26-31,66-68`** — both run
   `ciu env generate`, then require and `source` `ciu.env`, erroring if absent.
   Generate-then-read-back, but through `source`, so correct today.
3. **`nyxloom-trove/handoffs/dstdns-P147-vertical-corpus-e2e.md:473`** —
   `grep -oP '(?<=^CIU_INSTANCE_ID=).*' ciu.env`, the exact grep-the-file shape
   the entry predicted. It is *already* wrong (the key is `INSTANCE_ID`).
4. **`.github/workflows/ciu-env-cicd-test.yml:56-57,80`** — `cat ciu.env`,
   uploaded as a CI artifact. Diagnostic only, but it no longer shows what CIU
   reads.

**dstdns was NOT edited.** That is outside this worktree's scope, and estate
convention files a finding about a consumer in the consumer's own backlog.
**CIU-82** was filed in ciu's backlog so the notification is not lost between
repos, with the concrete replacement (parse the overlay's
`[ciu.instance.generated]` table with `tomllib` — already present in every
checkout, no `ciu` import needed) and the ordering constraint: no later ciu
release may stop WRITING `ciu.env` until these consumers are re-pointed.

**Action required by whoever merges/releases:** file the dstdns-side item in
`dstdns`'s own backlog. CIU-82's first checkbox tracks exactly that.

---

## 8. Acceptance criteria (the entry's own four)

- [x] **all 12 sites individually classified and, for fact-reads, migrated** —
      §2. 11 fact-reads migrated; the 1 existence check was re-pointed, with
      the reason in §4 (it did not simply "stay as-is").
- [x] **`ciu env generate` still writes `ciu.env`, byte-identical key set, one
      release of WARN pointing at `ciu env print`** —
      `test_generate_still_writes_ciu_env_with_the_same_key_set` asserts the
      key set against `REQUIRED_KEYS_CORE` + `GENERATED_IDENTITY_KEYS`;
      `test_generate_warns_once_that_ciu_env_is_now_write_only` and
      `test_env_show_nudges_on_stderr_and_keeps_stdout_parseable` assert the
      WARN, its wording, and that `ciu env`'s notice goes to **stderr** so the
      verb's `key=value` stdout stays parseable. Shape matches the codebase's
      existing precedent (`provisioning.py`'s `stack:<x>:healthy` exit-0
      fallback: always-on, names the forward path, names what a future release
      does).
- [x] **the delete-`ciu.env`-after-generate test, green, covering every verb
      touched** — §5, run twice (deleted AND corrupt), with a converse case.
- [x] **CONSUMERS.md migration section + CHANGES.md BREAKING entry** —
      `docs/CONSUMERS.md` §11b (dedicated, with the four real dstdns shapes and
      a paste-able replacement per pattern); `CHANGES.md` `[7.7.0]` with
      `feat(ciu)!:` markers and an Adoption / Migration Notes block, not folded
      into an ordinary bullet. **Release as 7.7.0 is still pending** — the tag
      and release belong to the merge step, not to this package.
- [x] **grep across dstdns** — §7. Confirmed rather than ruled out; CIU-82
      filed; the dstdns-side filing is owed by the merger.

`docs/SPEC.md` **S3.1c** now owns identity-source precedence, resolving the
entry's "no current SPEC section owns this" note.

---

## 9. Files touched

Source: `workspace_env.py`, `worktree.py`, `engine.py`, `deploy.py`, `cli.py`,
`config_model.py`, `composefile.py`, `hooks_runner.py`.
(The last three are message/docstring corrections only — the two prescriptive
`"source ciu.env"` remedies now name `eval "$(ciu env print)"`, and
`HookContext`'s field docs name the new source. AGENTS.md: an error message
that prescribes a fix must prescribe a correct one.)

Tests: `tests/conftest.py` (+`write_instance_facts`), 32 migrated test files,
1 new file.

Docs: `docs/SPEC.md`, `docs/CONSUMERS.md`, `CHANGES.md`, `README.md`,
`KNOWN_ISSUES_TODO_BACKLOG.md`.

Nothing outside `ciu/` was touched. No dstdns file was modified.

## 10. What I would look at first if I were reviewing this

1. **`_reap_uses_clean`'s new semantics** (§4) — it is the one site where I
   changed a check rather than a source, and the "presence, not readability"
   choice is a judgment call.
2. **Delta 1** (§3) — widening the budget-candidate render environment from
   "the candidate's `ciu.env` alone" to "ambient minus identity plus the
   candidate's facts" is the biggest behavioural change in the package, and it
   is the one that made an existing test's claim untrue.
3. **`read_generated_facts`'s slice boundary** — it mirrors the writer's, but
   the two are separate code. `test_table_stops_at_the_next_table` is the test
   that would catch them diverging.
4. **CONSUMERS §11b** — a breaking change with a bad migration note is worse
   than not shipping it. The four dstdns shapes are real greps, quoted with
   file:line; the `tomllib` one-liner is paste-able; the sanity check actually
   deletes `ciu.env`.
5. **The two `HookContext(...)` sites** (§11) — post-rebase, the one place
   where a dropped argument would have regressed ciu-P43 silently.

---

## 11. Rebase onto ciu-P43 (CIU-77/79/80/81) — the merged identity contract

ciu-P43 merged to `main` as `815c50d6` while this package was in flight. Its
CIU-80 added `HookContext.identity_unreadable: bool = False`, set at **both**
S3.12 readers — the same two functions CIU-75 re-points at a different record.
The rebase is therefore a reconciliation, not a mechanical replay.

### The contract after the merge

`deploy._workspace_identity(repo_root) -> tuple[dict, bool]` — CIU-80's shape,
CIU-75's source:

| state on disk | returns | announced? |
|---|---|---|
| overlay absent, or present with no `[ciu.instance.generated]` table | `({}, False)` | silent — a legitimately unmanaged checkout |
| table present but unreadable (`OSError` / non-UTF-8 / malformed TOML / **a directory where the file belongs**) | `({}, True)` | `[WARN] [S3.12] …` naming the path, the cause, and `ciu env generate` |

`engine.main_execution`'s STEP-12 inline reader is the real-run twin and
answers identically; `test_identity_unreadable_agrees_between_check_preflight_
and_real_run` is the test that fails if they diverge (S3.12/CIU-44's whole
point).

**CIU-75 does not merely survive CIU-80 — it repairs it.** P43 drew the
absent-vs-unreadable line with `ciu.env.is_file()`, which answers *absent* for
a DIRECTORY named `ciu.env`: the estate's absence-for-emptiness anti-pattern,
still live inside the very field introduced to defeat it. The overlay reader
raises `WorkspaceEnvError` for anything **present** it cannot read, directory
included, so `identity_unreadable=True` now covers the case that motivated it.
P43's directory-case early return in `test_ciu_deploy_actions.py` was therefore
deleted rather than preserved: all three parametrized manglings now warn and
set the flag.

### The regression that was possible here, and how it is excluded

`hooks_runner.py` auto-merges cleanly. Nothing about that file conflicts, so a
resolution that quietly dropped the `identity_unreadable=` argument at the two
`HookContext(...)` constructions would compile, pass most tests, and leave the
field permanently `False` — a hook told "identity is fine" about a corrupt
record, forever, with no failure anywhere.

Both halves are present at both sites, each with a comment naming the two ways
the pair can rot (drop the renamed key → `None` forever; drop the flag →
`False` forever):

```
$ grep -n "HookContext(" src/ciu/*.py
src/ciu/engine.py:1566:        ctx = hooks_runner.HookContext(
src/ciu/deploy.py:2554:            ctx = hooks_runner.HookContext(
```

Both pass `instance_id=…("instance_id")`, `network=…("network")` (CIU-75's
snake_case overlay keys, **not** P43's `identity.get("INSTANCE_ID")`) and
`identity_unreadable=…`. The end-to-end oracle is the hook probe in
`test_ciu_render_selection_context.py`, which writes
`f'{ctx.instance_id!r}|{ctx.network!r}|{ctx.identity_unreadable!r}'` from
inside a real hook and is asserted to equal `None|None|True` — a marker file
written by the hook itself, so no amount of wiring elsewhere can fake it.

`hooks_runner.py`'s own docstring for the field was re-pointed off `ciu.env`
onto the overlay table by hand, since no conflict would have prompted it.

### Conflicts, file by file

`deploy.py`, `engine.py` (both sides kept, P43's stale `is_file()` comment
corrected), `test_ciu_render_selection_context.py`, `test_ciu_deploy_actions.py`,
`test_ciu_identity_cutover_ciu75.py`, `KNOWN_ISSUES_TODO_BACKLOG.md`,
`CHANGES.md`, `docs/SPEC.md`, `docs/CONSUMERS.md`, `README.md` — the per-file
resolutions are tabulated in `ciu-P42-LOG.md` Entry 4. Two worth naming here:

- **P43's pairing test corrupted the identity record.** Post-cutover,
  corrupting `ciu.env` is a no-op, and corrupting the overlay *file* fails
  `render_global_chain` before STEP 12 — either way the oracle stops testing
  what it exists to test. Resolved with a **non-string fact**
  (`[ciu.instance.generated]\ninstance_id = 7`): valid TOML the config chain
  renders happily, which the identity reader refuses. The test stays
  end-to-end, with no monkeypatching on either side — which is what makes it a
  pairing test rather than two mocked assertions.
- **The backlog's two "Last updated" headers** were chained rather than
  merged-into-one, and my own now-false "Previously: CIU-81 FILED" paragraph
  was dropped: P43 FIXED CIU-81. CIU-82 (this package's dstdns follow-up) was
  already numbered around that collision.

### Docs that had to move because both ship in 7.7.0

SPEC **S9.3** and CONSUMERS **§10** describe `identity_unreadable` to hook
authors; both said `ciu.env`. Re-pointed at the overlay's generated table with
a cross-reference to S3.1c clause 4 for where the absent/unreadable line falls
and why CIU-75 moved it. `CHANGES.md` `[7.7.0]` gains an explicit CIU-75 ×
CIU-80 entry — an adopter reading one release's notes should not have to infer
the interaction of two entries in it. The consumer-facing conclusion is stated
plainly: *a hook that was branching on `identity_unreadable` needs no change;
it just stops being lied to.*

### A finding the rebase surfaced, filed as CIU-83 (not fixed here)

`git show 815c50d6 --stat` does not list `ciu/CHANGES.md`. ciu-P43's four items
landed with SPEC, CONSUMERS, CONFIG and report updates but **no changelog
entry**, and `## [7.7.0]` is the section all five of this checkpoint's items
ship in. As it stands the release notes describe CIU-75 only — plus the one
CIU-75 × CIU-80 bullet added here, which is currently CIU-80's sole changelog
mention — while **CIU-79 is breaking by ciu-P43's own merge message**. A
release whose notes open by declaring its one deliberate breaking change, and
which contains two, is exactly the kind of thing a reviewer should catch before
the tag rather than after.

Filed as **CIU-83** rather than fixed here: those entries are ciu-P43's to
write, this worktree's dispatch is the cutover, and writing another package's
changelog from its merge message is how release notes become fiction. The entry
carries both checkboxes (write the four entries; check the section against the
checkpoint's merge list, not one package's report).

### `main` moved again, and was deliberately not chased

`main` is now `7c47a707` (run-gate-P03 / RG-27). `comm -12` over
`git diff --name-only 815c50d6..HEAD` and `815c50d6..main` returns **empty** —
that work is entirely `run-gate-project/`, disjoint from this branch's 39
files — so there is nothing to reconcile and the merge is trivial. Rebasing
again would have invalidated the gate run for no reconciliation value.

# ciu-P43 — REPORT

Four bundled backlog items, four separate commits, on branch
`fix/ciu-P43-loose-ends` (worktree `.worktrees/ciu-P43-loose-ends`), based on
vbpub main `332af5a1`.

| item | entry | outcome | commit |
|---|---|---|---|
| 1 | CIU-79 | **DONE** | `7b2d288b` |
| 2 | CIU-80 | **DONE** | `cd5fadea` |
| 3 | CIU-81 | **DONE** | `597ce58d` |
| 4 | CIU-77 | **DONE** | `b81d6c3b` |

All four items shipped; none deferred. Per-item detail (full reasoning,
oracle design, controlled-wrong-implementation notes) is in
`ciu-P43-LOG.md`, one section per commit — this file states outcomes and
the real gate evidence.

**Correction (post-review, round 1): item 1 (CIU-79) IS a breaking change.**
An earlier version of this REPORT claimed the whole bundle stays
non-breaking; that is true for items 2-4 only. CIU-79 correctly applies
CIU-71's repo-root-relative rule to `ciu dev`, but that is a genuine
behavior change for any existing `[<root>.dev].build` profile whose
Dockerfile lives in the stack directory (the only shape that ever worked
under the pre-fix, stack-dir-relative resolution) — see item 1's section
below and `docs/CONSUMERS.md` #18's new migration blockquote.

---

## Item 1 — CIU-79: `ciu dev`'s `_build_dev_image` context-resolution defect

**What I did.** `_build_dev_image` (`src/ciu/dev.py`) resolved
`build.context`/`dockerfile` relative to `stack_dir`, never `repo_root` —
the same defect class CIU-71 fixed for `docker compose`. `ciu dev` runs a
plain `docker build` with no `--project-directory` equivalent, so the fix
resolves `context` to an absolute repo-root-relative path itself:
`(Path(repo_root) / context).resolve()`, before joining `dockerfile` onto
it. `run_dev` (the only caller) already had `repo_root` in scope, so no new
plumbing was needed beyond threading it into the one function.

New test `test_build_context_resolves_against_repo_root_not_stack_dir`
reproduces the exact controlled-wrong-implementation shape the backlog
entry specified: a real Dockerfile `COPY`ing a repo-root-relative path,
`context = "."`, and asserts the COPY source is reachable from the
resolved build context — manually confirmed this fails (source not
reachable) if `context` is resolved against `stack_dir` instead. The two
pre-existing tests that encoded the OLD buggy stack-dir-relative argv shape
were updated to the corrected repo-root-relative one.

SPEC S5a.1/S8.1a, CONSUMERS.md #18, and README's DooD bullet document that
`ciu dev` now shares S8.1a's repo-root-relative convention.

**This item is a BREAKING change, not additive — flagged in review round 1
and corrected here.** The pre-fix code never resolved `build.context` to an
absolute path at all; it passed the literal string through and relied on
`cwd=stack_dir` for docker's own resolution, which only ever worked for a
Dockerfile living IN the stack directory. Reviewer live-verified: a
stack-local Dockerfile with `build = { context = "." }` and no explicit
`dockerfile` built fine (`rc=0`) before this fix and fails (`rc=1`, `failed
to read dockerfile: open Dockerfile: no such file or directory`) after it
— exactly the correct, intended behavior (matching CIU-71's rule for `ciu
up`), but undocumented as a break in the original version of this REPORT.
`docs/CONSUMERS.md` #18 now carries a migration blockquote naming the
break and the concrete repair (`dockerfile = "<stack-path>/Dockerfile"`).
Blast radius inside vbpub itself is nil — no doc example or fixture in
this monorepo declares `[<root>.dev].build` — so this was a documentation
gap, not live damage.

---

## Item 2 — CIU-80: `HookContext.identity_unreadable`

**Controller's ruling followed exactly**: shape (b), additive
(`identity_unreadable: bool = False`), never a break for THIS item — CIU-75
is this wave's one deliberately-breaking release; items 2-4 of this bundle
stay non-breaking (item 1, CIU-79, is a genuine breaking change — see its
own section above).

Both S3.12 identity readers (`deploy._workspace_identity`, the `ciu check`
preflight's HookContext; `engine.main_execution`'s STEP-12 real-run read)
now set `identity_unreadable = True` only when `ciu.env` is PRESENT but
unreadable, `False` on a genuinely absent `ciu.env` — changed as the
entry's MANDATORY pair. `deploy._workspace_identity`'s return type changed
from bare `dict` to `tuple[dict, bool]`, threaded through its two
intermediate callers to the `HookContext(...)` construction site.

New test `test_identity_unreadable_agrees_between_check_preflight_and_real_run`
is the direct MANDATORY-pair proof: it drives ONE malformed `ciu.env`
fixture through both readers and asserts they agree (both `True`), with
the legitimate-absent state proven distinct (`False`) elsewhere in the same
file and in `test_ciu_deploy_actions.py`.

SPEC S9.3, CONSUMERS.md (both the field-enumeration paragraph and the
`validate_config` how-to), and CONFIG.md's hook-facts paragraph document
the new field.

---

## Item 3 — CIU-81: `scaffold.py`'s two Jinja render paths adopt `StrictUndefined`

**Verified first, per the entry's own requirement, before touching code.**
Read every shipped scaffold template
(`src/ciu/templates/global.defaults.toml.j2`, `stack.defaults.toml.j2`,
`stack.compose.yml.j2`). Finding: the two TOML templates carry ZERO Jinja
`{{ }}`/`{% %}` syntax by render time — every `@@PLACEHOLDER@@` is
substituted by plain `str.replace` before the Jinja env sees the text; the
remaining `$VAR`-style tokens are a different, later substitution
mechanism (real-deploy `ciu.env` expansion), not Jinja. `stack.compose.yml.j2`
(the one template with real Jinja refs) is shipped verbatim and is never
Jinja-rendered by `scaffold.py` at all — it renders for real, under
`StrictUndefined`, only at the consumer's own `ciu up`. **No legitimate
lenient-Undefined use exists anywhere in the shipped scaffold surface**, so
adoption at both named sites (`_render_jinja`, `build_files`'s inline
`Environment`) was safe with no follow-up needed — matching
`config_model.render_jinja2_text`'s exact `Environment(undefined=
StrictUndefined, keep_trailing_newline=True)` construction.

Both preflight render call sites also gained a `try/except TemplateError`
converting a genuine future undefined-reference defect into a clean
`SystemExit` naming the template and the Jinja error, instead of leaving a
raw traceback as the only failure mode a bare StrictUndefined flip would
otherwise introduce.

Two new tests inject an undefined-reference template via the same
`monkeypatch.setattr(scaffold, "_template", ...)` pattern the existing
`test_build_files_guard_rejects_global_without_shared_vars` uses, proving
`build_files`'s inline `Environment` genuinely exercises `StrictUndefined`
(not just `_render_jinja` in isolation) at BOTH named sites (global
template, per-stack `ciu.defaults.toml.j2`). 100% line+branch coverage
confirmed for `scaffold.py` against just this file's own tests.

SPEC S19 documents the fidelity fix and the verification finding.

---

## Item 4 — CIU-77: vendored gate judge `assay-2.3.0.pyz` -> `assay-3.2.0.pyz`

**The highest-risk item; not treated as a one-line pin bump.** Read all of
`assay/CHANGES.md` from 2.4.0 through 3.2.0. Verified against 3.2.0's real
CLI/config contract, live, before bumping:

- `assay lanes --json --file assay.toml` against ciu's UNMODIFIED
  `assay.toml`, run under a real installed assay 3.2.0, parsed clean —
  `base_source: "declared"`, `enforcement: "gate"`, `scope: "S1"`, `rigor:
  ["R0","R1"]` all round-tripped exactly. `LANE_SCHEMA_VERSION` (2) has
  been unchanged since before 2.3.0 — only `VERDICT_SCHEMA_VERSION` moved
  (7 -> 8). `assay.toml`'s body needed **zero** changes.
- `assay run --help` at 3.2.0 confirmed `assay run <lane> --file PATH
  --verdict-json PATH` is byte-identical to the gate harness's existing
  argv construction.
- The three risks the entry named were checked one at a time and are all
  inapplicable to ciu's specific lane: withdrawn mutation operators
  (A-331) are R2-only and ciu declares only R0/R1; judge provenance (B018)
  is opt-in via a flag the harness never passes; request-supplied base
  (B019) is opt-in and ciu's lane keeps its static `judge.base =
  "origin/main"` (confirmed `base_source: "declared"`).
- `run-gate.py`'s shell harness never parses verdict JSON itself — the
  gate's pass/fail is `assay run`'s own exit status — so the v7->v8
  verdict-schema move has zero ciu-side blast radius either.

**Vendoring.** Built the zipapp from the EXACT `assay-v3.2.0` tagged
commit, in a throwaway detached `git worktree` outside this worktree (so
it could not interfere with the concurrently-running assay Wave B producer
work), using assay's own release builder
(`gate/distribution/build_release.py`, fully offline). Confirmed a genuine
tagged build (`ASSAY_RELEASE_MANIFEST=created tag=assay-v3.2.0`, not an
SCM dev-version fallback); `--version` reports exactly `assay 3.2.0`;
sha256-verified. Deleted the three orphaned older vendored copies (2.1.0,
2.2.0, 2.3.0) after confirming nothing outside their own sha256 sidecars
and one historical LOG file referenced them — closing the actual
drift-recurrence mechanism the entry flagged.

Six references across `run-gate.toml`, `assay.toml`'s comment,
`README.md`, `docs/CONSUMERS.md` #12, and `nyxloom-trove/nyxloom.toml`
repointed from `assay-2.3.0.pyz` to `assay-3.2.0.pyz`.

**Scope decision: no new refresh-automation tooling was built.** The entry
named this a nice-to-have, explicitly sanctioning skipping it with a
documented reason. I recorded the verified, reproducible manual SOP
(worktree-at-tag + `build_release.py` + `sha256sum -c` + vendor + prune)
in the backlog row and the LOG instead of adding new code+tests to what
was already this bundle's largest item.

---

## The real gate — run once, at the end, against the final four-commit HEAD

```
cd /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu
./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P43-loose-ends
```

Verbatim terminal output:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 29 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-827988-1788146422 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '"'"'*'"'"' && cd /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu && (cd /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu/tools/assay && sha256sum -c assay-3.2.0.pyz.sha256) && { reported=$(/opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz --version) || { echo "run-gate: pin '"'"'assay'"'"': version probe failed: /opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz --version" >&2; exit 2; }; hit=0; for tok in $reported; do tok=${tok#"${tok%%[![:punct:]]*}"}; tok=${tok%"${tok##*[![:punct:]]}"}; case "$tok" in v[0-9]*) tok=${tok#v} ;; esac; if [ "$tok" = 3.2.0 ]; then hit=1; fi; done; if [ "$hit" != 1 ]; then echo "run-gate: pin '"'"'assay'"'"' version mismatch: declared 3.2.0, artifact reports: $reported — fix pins.assay.version or republish the artifact" >&2; exit 2; fi; } && mkdir -p .assay && /opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: b81d6c3bd9d45008dfc0a63e214515b2466c3015
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

**Verdict artifact, read directly in a separate step** (`ciu/.assay/verdict-ciu.json`),
confirmed against `git rev-parse HEAD` = `b81d6c3bd9d45008dfc0a63e214515b2466c3015`
(exact match):

```json
{
  "argv_declared": ["/opt/tester-venv/bin/python", "run-ciu-tests.py"],
  "argv_effective": ["/opt/tester-venv/bin/python", "run-ciu-tests.py"],
  "argv_modified": false,
  "assay_version": "3.2.0",
  "claims": [
    { "rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": true },
    {
      "rigor": "R1", "source": "computed", "status": "PASS", "verified_by_assay": true,
      "coverage": {
        "pct": 100.0, "considered": 5, "covered": 48, "executable": 48,
        "files_missing_coverage": [], "missing_lines": {}, "missing_branch_lines": {}
      }
    }
  ],
  "commit": "b81d6c3bd9d45008dfc0a63e214515b2466c3015",
  "declared_rigor": ["R0", "R1"],
  "enforcement": "gate",
  "exit_code": 0,
  "judge_provenance": {
    "artifact": "zipapp",
    "digest": "bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6",
    "digest_algorithm": "sha256",
    "name": "assay",
    "version": "3.2.0"
  },
  "judgment": {
    "r1": {
      "allow_excluded": false, "coverage_artifact": "coverage.json",
      "coverage_format": "coverage-py-json", "fail_under": 100.0,
      "mode": "changed_lines", "require_branch": true
    },
    "resolved": {
      "base": "124a5bff4f0efea71a3f11d183a4b22166ae6d2e",
      "base_resolution": "merge-base", "language": "python", "source_roots": ["src"]
    }
  },
  "lane": "ciu",
  "outcome": "PASS",
  "schema_version": 8,
  "scope": "S1"
}
```

`judge_provenance.digest` (`bbbed3ef...`) matches the sha256 in
`ciu/tools/assay/assay-3.2.0.pyz.sha256` exactly — the verdict itself
proves it was judged by the artifact this package vendored, not an
ambient install. `outcome: "PASS"`, `exit_code: 0`, both R0 and R1 claims
`PASS`, R1 changed-line coverage 100.0% against `base =
124a5bff4f0efea71a3f11d183a4b22166ae6d2e` (the merge-base with
`origin/main`), `schema_version: 8` (the new v8 verdict schema, confirming
it round-trips through `run-gate.py`'s harness without incident).

**Claim, not summary: this is the real, registered gate — the same
`./run-gate.py ciu` command a merge reviewer would run — judged by the
NEW 3.2.0 pin against this package's actual four-commit HEAD, and it is
green.**

---

## Addendum — real gate re-run after review round 1 fixes (commit `274230af`)

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P43-loose-ends`,
run again after the blocker-1/blocker-2 fix commit above (`274230af`,
doc+test-only, no source change — confirmed the coverage floor and gate
outcome stayed green rather than assumed).

Verbatim terminal output:

```
run-gate: rev 29 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-998656-1788148071 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'set -euo pipefail && export GIT_CONFIG_GLOBAL=/tmp/run-gate-gitconfig && git config --global --replace-all safe.directory '"'"'*'"'"' && cd /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu && (cd /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu/tools/assay && sha256sum -c assay-3.2.0.pyz.sha256) && { reported=$(/opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz --version) || { echo "run-gate: pin '"'"'assay'"'"': version probe failed: /opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz --version" >&2; exit 2; }; hit=0; for tok in $reported; do tok=${tok#"${tok%%[![:punct:]]*}"}; tok=${tok%"${tok##*[![:punct:]]}"}; case "$tok" in v[0-9]*) tok=${tok#v} ;; esac; if [ "$tok" = 3.2.0 ]; then hit=1; fi; done; if [ "$hit" != 1 ]; then echo "run-gate: pin '"'"'assay'"'"' version mismatch: declared 3.2.0, artifact reports: $reported — fix pins.assay.version or republish the artifact" >&2; exit 2; fi; } && mkdir -p .assay && /opt/tester-venv/bin/python tools/assay/assay-3.2.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-3.2.0.pyz: OK
ciu: PASS (exit 0)
  commit: 274230af839a6797a7a29d00e2437de7805659c2
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P43-loose-ends/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict artifact (`ciu/.assay/verdict-ciu.json`), read directly in a
separate step, commit confirmed against `git rev-parse HEAD` =
`274230af839a6797a7a29d00e2437de7805659c2` (exact match):

```
commit:          274230af839a6797a7a29d00e2437de7805659c2
outcome:         PASS
exit_code:       0
schema_version:  8
R0:              PASS
R1:              PASS  (coverage pct: 100.0)
judge_provenance: {artifact: zipapp, name: assay, version: 3.2.0,
                    digest_algorithm: sha256,
                    digest: bbbed3ef35cb8ac3e62075c62fcdb801b7a668b6fc72aa0180419ac4996b84d6}
```

Same green outcome as the original gate run, at the true post-review-fix
HEAD. `judge_provenance.digest` still matches the vendored
`tools/assay/assay-3.2.0.pyz.sha256` exactly.

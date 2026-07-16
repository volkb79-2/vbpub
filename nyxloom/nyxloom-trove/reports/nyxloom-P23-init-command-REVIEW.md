# nyxloom-P23-init-command — Independent Frontier Review

**Verdict: APPROVE** (3 small defects found and fixed by the reviewer on this branch)
**Reviewer:** independent frontier review (merge gate) · **Date:** 2026-07-16
**Branch:** `feat/nyxloom-P23-init-command` · **Reviewed at:** `1066b5e`

> Report path deviation: the packet specifies `topos/handoff/reports/<task>-REVIEW.md`.
> That is a stale path from the packet template — `topos/` is a *different* project in
> this monorepo, and writing a nyxloom review into it would pollute another project's
> trove. This review is filed in nyxloom's own declared `reports_dir`
> (`nyxloom-trove/reports`, per `nyxloom.toml`), following the existing precedent
> `groop/handoff/reports/groop-P90-bounded-process-sampler-REVIEW.md`.

## Git state (verified directly — receipts not trusted)

- `git log main..feat/nyxloom-P23-init-command` → 2 commits (`1066b5e` P23 work,
  `078db4f` a lint-L1 naming reconcile also present on main under a different sha).
- Worktree clean at the branch tip; no uncommitted implementer work (packet agreed).
- Diff stat matches the packet exactly (5 files, +268).
- **Scope compliant:** `git diff main...HEAD` touches none of the forbidden
  daemon-core files (`daemon.py`, `reconcile.py`, `storage.py`, `config.py`) — verified
  by explicit path-scoped diff, empty.

## Gate — re-run by the reviewer, not trusted from the report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

- At `1066b5e` (as submitted): **exit 0**, 440 tests.
- After reviewer fixes: **exit 0**, 441 tests.

The report's pasted gate tail reproduced byte-for-byte in my own run — including the
absent pytest summary line (an environment quirk, not a doctored paste). The claim
"440 tests collected, all passing (3 new + 437 pre-existing)" is accurate. The 3 new
tests were confirmed to actually execute (`-k init` → `3 passed, 36 deselected`), not
silently skipped or deselected.

## Per-oracle verification (adversarial, beyond the tests)

| Oracle | Verdict | How I verified (independent of the implementer's tests) |
| --- | --- | --- |
| O1 scaffold + valid `nyxloom.toml` | **PASS** | Ran `nyxloom init` into a clean tmpdir: full tree present; **and** `ProjectConfig.load()` succeeds on the result (stronger than the oracle — see F3). |
| O1-negative refuse existing, no overwrite | **PASS** | Second `init` → exit 1, `error: ... already exists`; pre-existing marker file byte-identical; nothing written. |
| O2 `exec-nyxloom.py` routes `init` | **PASS (with caveat)** | Forwarding is genuinely generic and reaches `nyxloom init`. Caveat in F1/OB1 — the docker-exec leg is dead, but that is pre-existing, not P23's doing. |
| O2-negative no arg → exit 2 + usage | **PASS** | `nyxloom init` → exit 2, stderr carries `usage: nyxloom init [-h] project_folder`. The implementer's test asserted only the exit code — fixed in F2. |

The generated `nyxloom.toml` was checked against nyxloom's real loader, not just
`tomllib`: all seven `[policy]` keys it emits exist on the `Policy` dataclass, and the
`[project]`-section layout matches the canonical trove config, so
`Policy(**data["policy"])` does not explode.

## Findings fixed on this branch

**F1 — `exec-nyxloom.py` docstring asserted an access check that cannot happen today.**
The added prose claimed forwarding `init` "proves the target instance can reach
`<project_folder>` (a built-in access check)". The *wording* is faithful to
STANDARD.md (I checked — it is verbatim spec intent, not implementer spin), but it was
stated as current behaviour. In reality the docker-exec leg cannot run **any**
subcommand: the controller image supplies nyxloom via `PYTHONPATH` over bind-mounted
`src/` and installs no `nyxloom` entrypoint, so `docker exec <c> nyxloom ...` exits 127.
Proven: forcing the leg via `NYXLOOM_CONTAINER=nyxloom-prod-nyxloomd` fails identically
for `init` **and** for `version` — i.e. pre-existing, **not** a P23 regression. Fixed by
rewriting the docstring to state where the access check does and does not hold.

**F2 — O2-negative test did not assert the usage message.** `test_init_missing_project_folder_exits_2`
took `capsys` and never read it, while the oracle requires "exit 2 **with a usage
message**". Asserted `usage:` and `project_folder` on stderr.

**F3 — the load-ability claim had no regression guard.** The REPORT states
`ProjectConfig.load()` succeeds on a fresh trove but was "verified manually, not
asserted in a test". That is the contract that makes `init` worth anything: a trove
nyxloom itself cannot read is a broken scaffold. Added
`test_init_scaffolds_a_loadable_trove`. **Mutation-tested to prove it is not hollow:**
deleting `handoff_globs` from the template makes the new guard fail (`KeyError`) while
the implementer's original O1 test still **passes** — confirming the original test could
not catch an unloadable template.

## Non-blocking observations (NOT fixed — outside P23's touch scope)

- **OB1 (recommend a backlog item).** Two pre-existing defects make STANDARD.md's
  "`init` runs through the running instance" design inoperative: (a) no `nyxloom`
  executable in the controller image → docker-exec leg exits 127 for every subcommand;
  (b) `_find_controller_container()` matches only names containing both "nyxloom" *and*
  "controller", but the deployed container is `nyxloom-prod-nyxloomd`, so the heuristic
  never matches and the host fallback silently always wins. Today `init` works *only*
  because of (b). Fixing these needs `nyxloom/nyxloomd/*`, outside P23's declared touch
  scope — correctly left alone.
- **OB2 (packaging).** Templates resolve via `__file__/../../..`, which works in a src
  checkout and in the container (bind-mounts `src/`) but not for a pip-installed
  nyxloom. The handoff explicitly permitted "the repo's canonical
  `nyxloom/nyxloom-trove/`", so this is not a defect — flagging for whoever packages.
- **OB3 (P24 interlock).** P24 (config-schema+lint) is admitted on main as a *handoff
  only*; no config schema exists yet, so there is no conflict today. Whoever implements
  P24 must ensure the schema accepts `init`'s output — empty `[refs]` and **no**
  `[gates.*]` — or freshly-scaffolded projects will fail `nyxloom lint` immediately.
- **OB4 (minor robustness).** If a `copyfile` fails after `mkdir`, a partial trove is
  left behind and every later `init` refuses it. The template-existence precheck runs
  *before* `mkdir`, so the realistic case is safe; not worth churn.

## Reasoning for the verdict

Every named oracle holds under independent adversarial testing, not merely under the
implementer's own tests — I re-derived each one by driving the CLI and the wrapper
end-to-end rather than reading assertions. Scope was respected (no forbidden file
touched), and the report is unusually honest: it proactively disclosed that O2 needed
no code change and that the load-ability check was manual-only, and both disclosures
checked out. The one claim that overreached (F1) traces to spec language rather than
invention, and the underlying weakness is pre-existing and outside this package's
scope. The three defects found were all small and are fixed here, with the gate green
at 441 tests. Nothing architectural is wrong. **APPROVE.**

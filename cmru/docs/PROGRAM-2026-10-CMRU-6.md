# Program 2026-10: cmru fix-up, agent/controller retirement, full cli-extended adoption (cmru 6.0.0)

Status: **active** (planned 2026-10-05). Integration branch `cmru-wave-2026-10`
(worktree `.worktrees/cmru-wave-2026-10`). One cmru release at the end: **6.0.0**
(breaking: two console scripts removed, CLI grammar renames). It also ships the 170
commits since `cmru-v5.5.0`.

## Inputs

- Four Opus area reviews (2026-10-05), with finding ids `REL-`, `INS-`, `BG-` and `CLI-`. They are
  kept in the controller session's scratchpad (`cmru-review/<area>/REVIEW-RESULT.md`), and each
  package REPORT copies the findings it closes, with evidence.
- Backlog triage and KI-51 inventory surveys (same scratchpad, `cmru-survey-*.md`).
- Agent/controller investigation memo (`cmru-review/AGENT-CONTROLLER-MEMO.md`).

## Operator decisions (2026-10-05 interviews)

| # | Decision |
|---|---|
| O1 | Fix first, then new work. The adoption starts only after Waves 0 and 1 have merged. |
| O2 | Single release at the end. |
| O3 | Tests may run only while host memory PSI `full avg60` is below 10. Run them serially under `nice`/`ionice`, and serialise all pytest runs through one shared lock. |
| O4 | `get.py` splits in two. **(A)** The generic project installer stays in cmru: install, update, status and rollback, fail-closed, with safe rollback, HTTPS-only (redirects included), sha256 mandatory for every file, per-release directories, cli-extended installed first with `--no-index --find-links`, and a working `--config`. A signature is optional and is verified when a key is configured. **(B)** Host enrollment becomes a **ciu-owned fragment**, inlined at render through a generic `[project.installer] extensions` mechanism (still one file, one digest). KI-49 and KI-50 move to ciu's backlog. |
| O5 | **Retire** `cmru-agent` and `cmru-controller` (dstdns D-097 declined them; superseded by ciu push-over-SSH). Delete the code, console scripts, unit file and tests. Mark the SPEC-G doc RETIRED but keep it. |
| O6 | Approve the CLI redesign (cli-surface review §B, D, E, F) with **hard renames**. Only `release --ref` keeps a deprecated alias for one release. Exit code 4 means refused by policy. |
| O7 | REL-04 is fixed in code: merge-promote with bounded retries, and tag rollback after a failed non-publishing build (REL-05). |
| O8 | Version source: `CliIdentity.resolve(distribution="cmru")`, installed metadata only. Library gap N1 (a version probe) is filed for cli-extended 0.3.0. |
| O9 | File a ciu backlog entry for `ciu host upgrade <host> --version X` (v8.2). |
| O10 | Cleanup is done: 24 failed transactions abandoned and 4 stale worktrees removed. The legacy `xm4okg` transaction waits for REL-03. |

## Waves

### Wave 0: fix-up (parallel packages, merged one at a time into the integration branch)

| Pkg | Scope (findings) | Main files |
|---|---|---|
| W0-REL | REL-01 (both handoff guards, KI-54), REL-12 (split the release block first), REL-02 (data: drop the stale `[5.6.0]`; code: regenerate when stale), REL-10/KI-30, REL-03 (legacy abandon), REL-04 (merge-promote), REL-05 (tag rollback, recovery text, doc), REL-06, REL-13, REL-14, REL-15 (real-git end-to-end release test) | `cli.py` release/abandon, `transaction.py`, `changelog.py`, `CHANGES.md`, `docs/RELEASE-TRANSACTIONS.md` |
| W0-GATE | BG-03 (no `--maxfail` outside mutation, junitxml, FAILED lines surfaced), BG-04 (child `PYTHONPATH` / `cmru handler` argv, REL-07), BG-11, REL-11 (`_git_scope`), BG-10, BG-09 | `assay.toml`, `run-gate.toml`, `cmru.toml`, `tools/run_release_gate.py`, `runner.py`, `config.py:_git_scope`, `build-initial-standalone.sh`, `handlers.py` (env) |
| W0-TESTER | KI-52/BG-01 (`--init`, a required pids limit, a `pids.events` wrapper), BG-02 (names, signals, exact-name cleanup), BG-07 (DinD limits, probe timeout), BG-12, BG-13, BG-06 (digest pins, `--pull=never`), BG-05 and KI-52(b) in the `tester-unified` image (no PyPI for estate-internal packages, `maintenance.autoDetach=false`) | `tester_gate.py`, `tester-unified/Dockerfile` and `.dockerignore`, `cmru.orchestration.toml` image pins |
| W0-RETIRE | O5 retirement (memo §5), CLI-04 (`default_projects` dead key), CLI-T1 (cleanup dry-run tests), CLI-01 (status truncates the log), CLI-05 (cleanup ignores the target), CLI-14 (`--repack`), CLI-18 (template loads), CLI-D3/D4/D5 docs drift, the ciu SPEC stale line, dstdns notice draft | `agent/`, `controller/`, tests, `pyproject.toml`, `cli.py` status/cleanup, docs |

### Wave 1: the `get.py` split (O4)

| Pkg | Scope |
|---|---|
| W1-INSTALLER | cmru template = generic installer (A): GETPY-REDESIGN R2 (optional signature, verified when a key is configured), R3 (`--version` pin and `manifest.tag` check when a manifest exists), R5 (fail closed), R7 (per-release directories, rollback), R8 (launchers), R9 (cli-extended first, `--no-index --find-links`, hash-locked), R10 (transport), R11 (rendering grammar, `--config`). Also INS-01 parts that apply to A (verified manifest used, adapter hashed), INS-02/06/07/08/09/10/11/13/18/21/24, and the `extensions` mechanism. |
| W1-CIU-ENROLL | Move the enrollment code unchanged into `ciu/installer/enroll.py`, declare it as a ciu extension, re-render `ciu/get.py` and `tls-edge/get.py` (which loses enroll), move KI-49/50 into CIU-122/123, file `ciu host upgrade` (O9), fix the ciu docs' `--project` callers (CLI-D2). |

### Wave 2: cli-extended adoption (KI-51) plus the CLI redesign (O6)

Root `cmru` plus the nine delegate registries only (agent and controller are retired).

- PKG-0: registry factory and selector (`cli_support.py`).
- PKG-1: root CLI, redesign §B1-7, exception narrowing (§C), exit taxonomy (§E), CLI-06/09/11/12/13/15/16/19/21/22.
- PKG-2: delegates, redesign §B8-12, CLI-10/17/20, `resolve --repo/--prefix`.
- PKG-4: packaging (`cli-extended>=0.2.0`, no vendoring, skills as package data, gate `PYTHONPATH` per CX-D3, D10).
- PKG-5: surface lifecycle (review catalog, manifest, findings), SPEC S-CLI.9 rewrite, `doctor`, pytest plugin, CLI-D1 skill rewrite, CLI-T3/T4/T5.

### Wave 3: release

- KI-35/REL-09: remote-only retire and sidecar sweep; REL-08 docs; abandon `xm4okg`.
- Full cmru gate including mutation (R2) on the final integration tree.
- KI-42 live tester-gate probe (BG review checklist).
- `cmru release cmru --set-version 6.0.0`, devcontainer install, `get.py` render check, dstdns notice (`resolve --prefix` caller, cmru Consul ACL leftovers).

### Deferred (not in this program)

KI-04, KI-06 beyond REL-05, KI-02, KI-01, ciu CIU-99 (bundle and dependency closure), the signed
manifest producer (GETPY-REDESIGN R4) and enrollment hardening (now in ciu), R1 trust file (ciu, v8.2).

## Process

- Implementers and package reviewers are fresh Sonnet sessions; the review step is never skipped.
  Opus was used for the area reviews at the operator's request.
- Every package: its own worktree from the integration branch; Edit/Write only; tests under O3;
  gate lanes are run through the shared gate lock with the verdict read in a separate step; a REPORT
  at `cmru/nyxloom-trove/reports/PROGRAM-2026-10-<pkg>-REPORT.md`.
- Merge order inside Wave 0: W0-RETIRE first (largest deletion), then W0-GATE, W0-TESTER, W0-REL.

---
kind: backlog-entry
schema_version: 1
id: CP-47
title: "cgpctl: the cli-extended client with the CGPCTL-SPEC verb surface, export-based report, doctor, skills (plan package 2)"
status: open
type: "feature"
component: "cli"
context_estimate: "large"
provenance: "Operator acceptance 2026-11-04 of the cgpctl plan defaults (CGPCTL-PLAN-2026-11-04 sections 1-4, Q1-Q11); contract in docs/CGPCTL-SPEC.md, docs/PROTOCOL.md section 10, docs/NATIVE-DEPLOYMENT.md (package 0)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler cli"
priority: 2
filed_date: "2026-11-04"
---

## Problem

The 1.x CLI is a 1762-line argparse program with 17 verbs, a bash shim, helper mode, a placement probe and `docker logs` use. The operator wants
`cgpctl`, an unprivileged-friendly client on cli-extended that can address any socket.

## Design

Contract: `docs/CGPCTL-SPEC.md` sections 1, 2 and 4. Branch `cgp2-2-cgpctl-20261104`; adopts CP-17 for the client half.

- `CliRegistry` grammar for the verbs in the spec: `status`, `host`, `sessions`, `show`, `start`, `stop`, `watch`, `report`, `prune`, `run`, `mark`,
  `doctor`, `config show|set|reset` (set and reset are root-only on the daemon; acceptance list in CP-50), `skills`. `CliIdentity`, `unexpected_exceptions="report"`, `CliFailure` for domain failures, `register_doctor` with the
  spec checks, `register_skills_verbs(package="cgprofile")`; rewrite the skill for the new surface.
- Socket resolution `--socket` > `CGPCTL_SOCKET` > `/run/cgprofile/ctl.sock`; empty env is a usage error.
- Exit codes 0/1/2/3/130 and the cause tags, with the mapping of daemon error codes in spec section 1; `--json` shapes per spec section 2.3.
- Contract negotiation: open with `version` at contract 1, use contract 2 only when listed (`PROTOCOL.md` section 10.1); against a 1.x daemon
  everything contract-1 works and `report` / `config show` exit 3 with `contract-unsupported`.
- `report`: `export` stream, digest verification, local render; the `report` extra check and `--export-only`.
- Degraded set for callers without the group (spec section 2.4): `--version`, `run`, `mark`, `report --run-dir`, `doctor` with the named fix.
- Delete the bash shim, `setup.sh`, helper mode, `_collect`, the placement probe, `attach`, `targets`, `logtail`.
- Surface files: `[tool.cli-extended]` with `cgpctl` (and `cgpd` from package 3), `docs/cli-review.toml`, `docs/cli-surface.json`, generated
  `docs/CLI-SPEC.md`; `surface check` as a pytest test; `assert_cli_contract`, `invoke_script`, `cli-extended audit --json` with no `fail`.

## Acceptance criteria

- Every verb of the spec exists with the documented `--json` shape (golden tests against a fake daemon speaking contracts 1 and 2).
- `mark` imports nothing outside the standard library and cli-extended (test).
- Exit-code and cause-tag table tested per verb.
- One surface-review round done; no `fail` in the audit.

## Dependencies and order

After CP-46. Parallel with CP-48 (the export op it consumes is faked until CP-48 lands; integration test at the merge).

## Updates

**2026-11-04** — Package 2 progress (cgp2-pkg2 branch feat-cgp2-pkg2-20261104): cgpctl, ctlwire, direct mode, fake-daemon goldens, the cli-extended surface (docs/CLI-SPEC.md, cli-review.toml 171 cases, contract and audit tests) and the removal of the 1.x client surface are done at 100 percent coverage. Still open: run --target/--observe/--cap (run profiles self@follow only), the contract-2 daemon verbs (package 3) that sessions/show/start/prune/config need, and the 1.x collector sections of README/ATTACH-GUIDE/DESIGN/CONSUMERS (package 4 docs sweep, CP-49). Stays open.

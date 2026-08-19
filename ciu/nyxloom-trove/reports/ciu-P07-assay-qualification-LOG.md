# LOG — ciu-P07-assay-qualification

- Package: `ciu-P07-assay-qualification`
- Branch: `docs/ciu-P07-assay-qualification` (forked from origin/main @ `98549075`, after the
  checkpoint-B merge `7eefaba0` + release prep)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `71f5ec79`
- Status: **BLOCKED** (escalate_if trigger #1 — released Assay not installed in tester-unified)

## BLOCKED: <reason>

The gate contract this package exists to build requires an **installed, released Assay
CLI/artifact** inside tester-unified (oracles O1/O3; handoff step 1: "Probe the installed Assay
version and supported config schema inside tester-unified. Record exact commands/output before
editing. If it is not the expected released contract, trigger BLOCKED rather than guessing.").
Assay **is** released (`assay-v2.1.0`), but **is not installed anywhere the gate can reach** —
not in the tester-unified image, not vendored in the repo. The absence is the degenerate case of
"does not support the required current schema/command contract" (escalate_if #1). Nothing was
edited; no `assay.toml` was written against a guessed schema.

## Probe evidence (exact commands and output, run before any edit)

Launcher: `cmru tester-gate` (the sanctioned gate launcher — NOT a hand-rolled docker run;
env reproduced from `cmru.orchestration.toml [orchestration.defaults.env]`:
`CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local`, memory 3g/16g, cpus 1.5,
cgroup-probe `debian:trixie-slim`; cgroup parent resolved from the ambient
`CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice` — launch reached the container, i.e. the
slice is loaded on the host).

```
$ cmru tester-gate --cwd ciu -- bash -c 'which assay; assay --version 2>&1; /opt/tester-venv/bin/python -c "import assay,sys; print(sys.version); print(assay.__file__)" 2>&1'
bash: line 1: assay: command not found
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import assay,sys; print(sys.version); print(assay.__file__)
    ^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'assay'
```

Same result on the other local tester-unified image:

```
$ cmru tester-gate --image tester-unified:ciu-gate113 --cwd ciu -- bash -c 'which assay; assay --version 2>&1 | head -2; /opt/tester-venv/bin/python -c "import assay; print(assay.__file__)" 2>&1'
bash: line 1: assay: command not found
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import assay; print(assay.__file__)
  ^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'assay'
```

## Why this is a BLOCKED trigger, not a fixable gap

- **The released contract exists but is not installed.** Tag `assay-v2.1.0` → `a3ae580d`
  ("chore(assay): prepare release inputs"); `assay/CHANGES.md` records 2.1.0 (2026-08-18);
  CLI entry point `assay = "assay.cli:main"`; lane schema v2 + verdict schema v6 per
  `assay/docs/CONSUMERS.md`. The artifact is simply missing from the gate image.
- **No vendored artifact anywhere in the repo.** No `assay*.whl` / `*.pyz` exists in the
  repository tree (only cmru/nyxloom/ciu wheels); `nyxloom/src/` contains no assay module
  (grep: zero hits) — nothing routes or installs the artifact.
- **Installation is out of P07's scope by construction.** Handoff scope.forbid: "Do not modify
  CIU source, Assay source, or tester-unified. This package consumes their released/configured
  interfaces only." The tester-unified image derives its dependency closure exclusively from
  each project's `pyproject.toml` (tester-unified/Dockerfile: `ciu[ssh,test]`, `cmru[test]`,
  `topos[dev]`, `nyxloom[test]`, cgroup-profiler[test]) and has no assay slot. Getting Assay
  into the gate therefore requires either editing `tester-unified/Dockerfile` (forbidden), or
  adding assay to a project's `pyproject.toml` (not in scope.touch), or vendoring a wheel/.pyz
  under a new path (not in scope.touch). Assay's own consumer docs offer exactly two
  integration shapes — a pinned verified wheel, or a vendored verified zipapp — and neither
  exists here.

## escalate_if check (trigger vs non-trigger)

| # | condition | verdict |
|---|---|---|
| 1 | released Assay installed in tester-unified supports the required contract | **TRIGGERED** — nothing installed at all; cannot be installed within scope (above) |
| 2 | background cgroup variable absent or LoadState not loaded | **not triggered** — `CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice` is ambient; `cmru tester-gate` resolved and used it successfully (container launch reached the probe). (The cockpit has no systemd — expected; the LoadState verification is host-side at gate launch.) |
| 3 | adversarial review finds accepted defect requiring forbidden source changes | **not reached** — gate contract absent; no review possible |

## Unblock options (controller decisions, not improvisations)

1. **Install the released Assay into tester-unified (operator):** rebuild `tester-unified`
   with the released `assay-v2.1.0` wheel installed into `/opt/tester-venv` (per Assay's
   consumer docs: verify against `release-manifest.json`, `pip --require-hashes`), then
   confirm `assay --version` and `assay lanes` inside the image. P07 then resumes at its
   step 1 probe.
2. **Amend P07 scope to vendor the verified zipapp:** controller extends scope.touch with a
   vendoring path (e.g. `tools/assay/assay-2.1.0.pyz` + `.sha256` sidecar, verified per
   CONSUMERS.md); the gate argv then invokes it explicitly
   (`/opt/tester-venv/bin/python tools/assay/assay-2.1.0.pyz run ...`) — the zero-install,
   hermetic shape Assay's docs describe. P07 resumes without touching tester-unified.
3. **Declare assay a pinned gate dependency via a project pyproject** (controller decision +
   scope amendment, since `pyproject.toml` is not in scope.touch).

## State on BLOCKED

- No files edited, nothing staged, no `assay.toml` written.
- CIU-28/CIU-29 remain OPEN (`KNOWN_ISSUES_TODO_BACKLOG.md` row "qualification P07 pending"
  untouched); roadmap milestone open.
- This LOG is the only change; committed so the trigger is recorded and reviewable.
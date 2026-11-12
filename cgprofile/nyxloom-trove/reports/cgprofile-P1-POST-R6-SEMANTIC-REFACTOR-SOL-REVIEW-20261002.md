# RG-55 P1 post-round-6 semantic-refactor supplemental review — 2026-10-02

REJECT

## Initial finding, before repair

**B1 — release-gate blocker, `scripts/cgroup-profiler/run-gate.toml:58-106`.**
The new `assay.toml` policy delegates its changed-lines base to the request,
but both registered command lanes lack a `{base}` token. On the exact initial
candidate `4d097c97e6a34ace7e9017550ef170bd04db992a` (tree
`8eb8f66d24a480a49733230961fe5f4e9c5184bb`, base/main
`665246456ef2f503949b9d4449747e5f90b62f42`), these read-only probes
both exited **2** before any lane/container started:

```text
./run-gate.py --base e5e9b95c5ac8be3452c93f1066f9436347f862fd --dry-run r2
./run-gate.py --base e5e9b95c5ac8be3452c93f1066f9436347f862fd --dry-run gate
```

Each refusal says the command lane does not delegate a comparison base because
its argv has no `{base}` token. Running without `--base` would instead enter
Assay without its required `--request-base` and fail there. Thus neither the
requested complete R2 nor the registered full gate can judge the declared
pre-wave source scope. Prescribed repair: pass `{base}` through the `r2`
command as Assay's `--request-base` and through the full gate's nested `r2`
invocation as run-gate's `--base`; then prove both expanded commands with
the real read-only runner and verify missing/wrong-base behavior before any
long campaign. The package's README, design guide, and consumer guide must
explain the accepted invocation and scope source.

The initial branch was `rg55-p1-final-20261001`, clean at the HEAD above.
The initial direct `main..HEAD` diff (11 paths, 604 lines) was saved before
edits at reviewer scratch `/tmp/rg55-p1-final-20261002-initial.diff` and
reviewed, including `lib/summary.py`, tests, configuration, and records.
This artifact is the first review edit. No gate, Docker container, worktree
switch, merge, release, installation, or daemon action was performed.

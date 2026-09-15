# RG-55 P4 final adversarial review — round 6

REJECT

## Identity and reviewed tree

- Target: `P4` (`rg55-followups-run-gate`).
- Runtime metadata exposed by this session says Codex/GPT-5. The operator
  explicitly overrode the packet's metadata blocker after independently
  verifying the configured `gpt-5.6-sol`/`xhigh` route; this record does not
  invent unavailable route metadata.
- Reviewed HEAD before this finding: `0a875494357389c1584356143f3225715f301bcc`.
- Merge base: `3df192a3b4c994cec41d1dca585139666ef812ef`.
- Worktree was clean before the contained gate attempts. The original product
  diff was captured blind before any repair in round 5 (SHA-256
  `9f327596d4ba569246e3bfa08647e1fa4a66e42f4d27afeb9f6f4bfba1621eb3`).

## B8 — critical — tester-unified has no reproducible gate launcher

- Location: `tester-unified/` contains only `Dockerfile`; the P4 gate
  invocation is otherwise operator-authored shell.
- Observable failure: a clean operator cannot run P4's declared selftest in
  the mandatory tester container from repository state alone. The first
  contained attempt selected `/usr/local/bin/python3`, where pytest is absent.
  Adding the image's venv manually reached the suite, but 105 tests failed
  because pytest repositories below container-local `/tmp` had no physical
  host mapping. Adding a one-off same-path temp bind reduced that to four
  failures, all because host-lane contract tests legitimately launch nested
  Docker and the outer tester had no Docker socket. Manual additions therefore
  determine whether the same tree is red or green.
- Reproduction:
  - `rg55-p4-selftest-0a875-20260915`: `JOB_EXIT=1`, `No module named pytest`.
  - `rg55-p4-selftest-0a875-r2-20260915`: user 1003,
    `CgroupParent=dev-background.slice`, `NanoCpus=3000000000`; 105 failed,
    1083 passed, 3 skipped because `/tmp` was not host-translatable.
  - `rg55-p4-selftest-0a875-r3-20260915`: same placement plus an ad-hoc
    host-backed `/tmp`; four failed, 1184 passed, 3 skipped because
    `/var/run/docker.sock` was absent. Each job's `docker wait` status and
    inner `JOB_EXIT` were separately 1.
- Behavioral consequence: the cockpit doctrine is not self-executing. Two
  operators can run nominally the same `tester-unified` gate with different
  interpreter, namespace, Docker, and cgroup facts, and one can mistake a
  hand-repaired environment for the product's defined release gate.
- Exact prescription: ship one repository-owned launcher for
  `tester-unified:local` that fails closed on a missing declared cgroup or
  untranslatable workspace, derives the physical repo root, creates and cleans
  an isolated host-backed `/tmp`, dual-mounts the repo, mounts the Docker socket
  with its real group, selects `/opt/tester-venv`, runs detached, applies and
  verifies the 3-CPU/cgroup/user/workdir contract, and preserves inspect, logs,
  `docker wait`, and the in-container job marker. Add executable behavioral
  tests and adoption documentation; P4 evidence must then be produced only
  through that launcher.

## Evidence disposition

The 146-test focused regression slice on `0a875494` was valid contained
evidence and passed. All three full-selftest attempts above are infrastructure
failures, not product failures or passes. They are preserved under
`.assay/final-gates-0a875494/`; no final package, coverage, or mutation verdict
is claimed. Creating this review record changes the judged tree, so all earlier
per-tree Assay evidence remains historical only.

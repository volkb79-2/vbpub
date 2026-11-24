# CMRU decisions

Product decisions for CMRU, numbered `D-<NNN>`. Each records what was decided, who decided it,
why, and what it binds. The user-facing rationale lives in `docs/DESIGN-GUIDE.md` and links back
here.

## D-001 — A release retry keeps its exact version plan

- **Date / decided by:** 2026-10-18, the release controller, resolving the BLOCKED version-intent
  finding of `reports/cmru-6.2.0-independent-final-review-claude-opus-20261018.md`.
- **Decision:** A release retry must preserve the exact version plan chosen by the original
  invocation.
  - Record the resolved per-project version plan durably before any release side effect.
  - A resume with no version override reuses that recorded plan.
  - A caller-supplied conflicting override refuses before the candidate, a tag, or publication
    state changes.
  - A legacy transaction without trustworthy version intent may resume only if its exact plan
    can be proven from committed candidate evidence; otherwise it refuses before side effects.
  - CMRU never silently recomputes a later version.
- **Why:** Preserving the operator's release intent is the safe behavior for a resumable
  transaction. Recomputation from whatever flags a retry carries shipped wrong versions, and it
  stranded curated notes under a never-released section.
- **Binds this release:** a transaction started with `cmru release cmru --minor --from-worktree`
  resumes as exactly CMRU 6.2.0 even when `--minor` is omitted on the resume command.
- **Implementation:**
  - The plan is recorded in `<token>.version-plan.json` in the shared transaction metadata,
    bound to the transaction branch and base commit. `null` marks an external-version or
    no-tag project.
  - The plan is written once by the release child, after planning and before the scope record
    and first candidate push. It is validated on every read.
  - An override selecting the same version is accepted.
  - The legacy proof is exactly one new dated changelog section since the transaction base.
  - Rationale: `docs/DESIGN-GUIDE.md` § "A retry keeps its recorded version plan (D-001)".
  - Normative text: `docs/SPEC.md`, release-transaction version intent.

## D-178 — Cleanup keeps its cleanup-specific step contract

- **Date / decided by:** 2026-11-22, Codex Sol xhigh design review at operator request.
- **Decision:** `steps.clean` gains the shared finite no-progress watchdog, but preserves its
  existing cleanup-specific environment, quiet-output policy, command/cwd mapping, and
  `repo_root/logs` behavior. Parse `steps.clean` with the project `[runner]` default
  and step override, then start from the existing
  `_build_step_config("clean", project.steps["clean"])` and copy only the resolved finite
  timeout and its winning source key from `project.runner_steps["clean"]`.
- **Why:** Cleanup historically ignores ordinary step controls. Passing its full parsed step
  would newly activate ignored controls and change existing projects; copying only the two
  watchdog fields preserves the cleanup contract by construction.
- **Binds:** the single P7 package (one merge; the former P7.1/P7.2/P7.3 split is gone): its
  cleanup construction and stub oracle, its live cleanup watchdog oracle, and its cleanup user
  documentation.
- **Preserved cleanup fields:** the command list and original absolute command cwd from
  `project.steps["clean"]`; `bake_set_prefix=None`, `bake_set_vars=[]`,
  `no_cache_env=None`, `clean_dirs=[]`, `required_env=[]`, `login=None`, `step_env={}`,
  `env_command=None`, `registries=None`, and `quiet=True`; plus `CMRU_VERSION`, the selected
  publisher token, the project environment, the protected cleanup environment, the original
  absolute command cwd, and `repo_root/logs` as the log root. `env_command` and login are not
  run.
- **Behavior oracles:**
  - A stub of `execute_step` proves the parsed project default/step override reaches the
    cleanup config with the winning source key and all cleanup fields above remain unchanged.
  - A test marked `requires_contained_cgroup` (TP-13), with stubbed remote cleanup inputs, invokes
    `cmru cleanup --policy`; the override reaches the watchdog, its diagnostic names
    `steps.clean.no_progress_timeout_seconds`, the cleanup environment/cwd/log root/quiet
    behavior above survives, and ignored controls stay inert.
  - Rationale: `docs/DESIGN-RUNNER-PROCIO-ADOPTION.md` § "D-178: preserve cleanup's existing
    behavior".

## D-179 — Malformed streamed step output does not fail a completed child

- **Date / decided by:** 2026-11-22, Codex Sol xhigh design review at operator request.
- **Decision:** CMRU adopts `cli_extended.procio.run_streaming`'s locale-based,
  universal-newline decoding with replacement characters for undecodable output. Malformed
  streamed bytes do not themselves fail a step. Ordinary success or failure follows the
  child's exit status; watchdog expiry remains a runtime failure with exit 1. Captured probes
  stay outside this migration and keep strict text decoding.
- **Why:** Streamed output is diagnostic, and failing a completed build because its output
  contains malformed text is less reliable than preserving the child result. This
  intentionally changes CMRU's current strict-decoder behavior.
- **Binds:** the single P7 package (one merge; the former P7.1/P7.2/P7.3 split is gone): its
  streamed adapter and malformed-output oracle, and its SPEC S3.4a and user documentation.
  It requires `cli-extended>=0.8.0`, the floor P7 raises once the tester image carries 0.8.0.
- **Behavior oracle:** In a test marked `requires_contained_cgroup` (TP-13) with UTF-8
  selected, run a step child that writes `b"ok \xff end\n"` and exits 0. CMRU succeeds, the step log and aggregate
  mirror contain `ok \ufffd end\n`, and no `UnicodeDecodeError` escapes. A nonzero child exit
  still produces the existing step failure.
- **Rationale:** `docs/DESIGN-RUNNER-PROCIO-ADOPTION.md` §§ "Required behavior comparison"
  and "D-179: replacement decoding for streamed output".

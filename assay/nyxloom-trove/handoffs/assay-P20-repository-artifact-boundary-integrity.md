---
schema_version: 1
id: assay-P20-repository-artifact-boundary-integrity
project: assay
title: "Repository identity and measured artifacts survive adversarial process state"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P19-isolated-r3-cli-pipeline]
session: fresh
scope:
  touch: ["src/assay/git.py", "src/assay/safeio.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/runner.py", "src/assay/cli.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/schemas", "src/assay/verdict.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "Every Git query is anchored to the supplied repository under a sanitized Git environment; hostile GIT_DIR/GIT_WORK_TREE/config, hooks, external diff, textconv, or another repository cannot change the resolved HEAD, diff, or dirty set"
    negative: "Pointing GIT_DIR at a second seeded repository changes Assay's recorded commit or path set"
    gate: tester-unified
  - id: O2
    observable: "A coverage artifact is accepted only when this command invocation created a fresh bounded regular non-symlink file, opened and validated without a blocking special-file read"
    negative: "A copied stale profile, FIFO, device, symlink swap, or oversized file is parsed or can hang beyond the lane process timeout"
    gate: tester-unified
  - id: O3
    observable: "After HEAD is known, every expected Git, decode, coverage, source-read, and evaluation refusal emits a complete artifact; a lane command that changes any repository path cannot retain PASS claims bound to the pre-run commit"
    negative: "A tracked test/support-file mutation outside source_roots exits zero with PASS and the original commit, or a normalized-key collision exits with a traceback and no artifact"
    gate: tester-unified
  - id: O4
    observable: "All repository/artifact checks use fixed byte/path/work bounds rather than ambient or elapsed-time guesses"
    negative: "An unbounded input reaches read_text before a size/type guard"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "a truthful terminal needs a reason code absent from schema v3"
  - "the Git boundary cannot disable an ambient/local executable behavior without changing repository contents"
mutexes: []
---

# P20 — repository and artifact boundary integrity

The claim to attack: **the repository and evidence Assay records are the ones it actually measured, even under hostile ambient process and filesystem state.**

## Dispatch contract

- Contract class: **2c — bounded integration** (`implement-3` when deployed;
  frontmatter uses today's live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Sonnet xhigh implementer → Opus
  xhigh independent reviewer**.
- Readiness: **NEXT, JIT-FREEZE REQUIRED.** Before implementation, Sol reruns
  AUTHORING's exact pre-dispatch adversarial specification review at current
  HEAD and commits the hostile Git/artifact acceptance inputs named below. A
  prose-only test plan is not dispatch-ready.
- Implementer freedom: private helper names and equivalent local decomposition
  only. Git environment, safe-open state machine, reason mapping, limits, and
  side-effect order are fixed by this packet.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P20-repository-artifact-boundary-integrity`
on branch `feat/assay-P20-repository-artifact-boundary-integrity`.

## Context to read first

1. `nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md`, findings F01, F04, F05, F06, F07 and F08; reproduce the hostile-`GIT_DIR`, post-command dirty-tree, stale-profile, and normalized-key-collision probes before implementation.
2. `docs/DESIGN-GUIDE.md` §§0, 5, 6, 9 and 12; decisions A-027, A-041, A-134, A-139–A-140 and A-153–A-157.
3. `src/assay/git.py` in full. Enumerate every Git subprocess and every inherited environment/config source; do not harden only the commands used by one fixture.
4. `src/assay/coverage.py::read_coverage_artifact`, `src/assay/evaluate.py`, and `src/assay/runner.py::{evaluate_r1,run_lane,write_verdict}` with their direct tests. Identify every call after `execute_command` that can raise an expected `AssayError`, `OSError`, or decode error outside artifact assembly.
5. P15's byte/path decisions A-134–A-135 and P17's total-terminal decisions A-139–A-143. Preserve their exact path transport and one-complete-artifact contract.
6. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` §§4.2a, 5, and 6 for bounded evidence, fail-closed behavior, and real-gate discipline.

## Implementation packet (normative)

### Interfaces and ownership

- `src/assay/git.py` remains the only module that launches Git. Keep `run(repo,
  *args) -> str` and the typed wrappers; one raw-byte subprocess seam owns all
  argv construction, environment replacement, exit translation, and UTF-8
  decoding. No caller may invoke `git`, read `.git`, or add its own decoder.
- Resolve the Git executable once from the caller's declared `PATH` with
  `shutil.which`, require an absolute regular executable, and carry that exact
  path. Absence is `ERROR/GIT_FAILED`; never try a conventional location. Each
  Git child receives this **replacement** environment and no other key:

  ```python
  {
      "LC_ALL": "C.UTF-8",
      "LANG": "C.UTF-8",
      "GIT_CONFIG_NOSYSTEM": "1",
      "GIT_CONFIG_SYSTEM": os.devnull,
      "GIT_CONFIG_GLOBAL": os.devnull,
      "GIT_ATTR_NOSYSTEM": "1",
      "GIT_TERMINAL_PROMPT": "0",
      "GIT_OPTIONAL_LOCKS": "0",
      "GIT_PAGER": "",
      "PAGER": "",
  }
  ```

  The JIT probe must prove `C.UTF-8` exists in `tester-unified`; if not, Sol
  changes this literal to the one probed UTF-8 locale before dispatch. No
  ambient `GIT_*`, `HOME`, `XDG_*`, `PATH`, pager, editor, config counter,
  object directory, alternate, work-tree, or repository selector crosses the
  boundary. Fixed global argv is: absolute executable, `--no-pager`,
  `--no-optional-locks`, `--literal-pathspecs`, `-c core.quotePath=false`,
  `-c core.hooksPath=/dev/null`, `-c core.fsmonitor=`, `-C
  <resolved-repo-top>`, then a fixed
  subcommand. Diff calls also carry `--no-ext-diff --no-textconv`. No command
  invokes checkout, filters, hooks, aliases, an editor, transport, or a user
  program. User-controlled revisions are first validated/resolved to full OIDs;
  paths follow `--` under `--literal-pathspecs`.
- `src/assay/safeio.py` owns bounded nonblocking regular-file opening so P26
  can reuse the same descriptor/inode/limit discipline for attestations.
  Coverage freshness is represented by a private immutable reservation created
  before launch and consumed once after launch. It contains an open parent
  directory descriptor, basename, parent device/inode, and any removed prior
  file's device/inode—not merely an absolute path or timestamp. Traverse from
  an opened project-root descriptor with `openat`/`dir_fd` and
  `O_DIRECTORY|O_NOFOLLOW`; never validate a parent and reopen it by pathname.
  Preparation requires an existing real parent directory, rejects a
  symlink/special destination, and unlinks an old regular artifact relative to
  the held parent descriptor only after every pre-run refusal has passed.
  Consumption opens the basename relative to that same descriptor with
  `O_RDONLY|O_NONBLOCK|O_NOFOLLOW`, checks `fstat` is a regular file no larger
  than **16 MiB**, rejects the removed prior inode if it was relinked, reads at
  most limit+1 bytes, then performs the single UTF-8/format parse. Renaming the
  parent and writing at a replacement pathname yields missing output, never a
  read from the replacement tree. A missing new file is `EMPTY_COVERAGE`; an
  unsafe/unreadable/oversized object is `ERROR/UNREADABLE_ARTIFACT`.
- `runner.run_lane` owns terminal assembly. After the command and before any
  PASS claim, it calls the same sanitized `dirty_paths(repo_top)` over the
  entire repository. Any staged, unstaged, or untracked path is
  `NO_MEASUREMENT/DIRTY_TREE`; it does not restore consumer state.

### Required flow and topology

```text
supplied project path -> sanitized repo_top + HEAD/base -> pre-run refusal checks
                     -> reserve/remove coverage path -> execute command
                     -> safe-open fresh artifact -> evaluate -> whole-repo dirty check
                     -> exactly one complete verdict bound to the pre-run HEAD
```

The project may be `repo_top/apps/project`; Git identities and dirty paths are
repo-top-relative, while the declared artifact remains project-relative. Join
each spelling only at its named owner. Never validate a snapshot, host, or
container spelling with the consumer process's local filesystem.

### Decision and proof matrix

| State | Terminal | Command allowed? | Required negative fixture |
|---|---|---:|---|
| hostile Git selectors/config | exact seeded repo identity or `ERROR/GIT_FAILED` | no wrong-repo run | two repos with different HEAD/path bytes |
| stale regular profile | remove, then require a new inode/file | yes, after removal | zero-exit producer writes nothing |
| FIFO/device/symlink/oversize | `ERROR/UNREADABLE_ARTIFACT` | never block | each real filesystem object |
| command dirties any repo path | `NO_MEASUREMENT/DIRTY_TREE` | already ran | mutate a tracked support file outside source roots |
| expected post-HEAD decode/evaluation error | complete typed artifact | as flow dictates | normalized-key collision plus invalid UTF-8 |

The terminal translation is closed, not a blanket `except`: sanitized Git
exit/decode -> `ERROR/GIT_FAILED`; missing or well-formed zero-file coverage ->
`NO_MEASUREMENT/EMPTY_COVERAGE`; unsafe type, bound, UTF-8, source read, or
normalized-key collision -> `ERROR/UNREADABLE_ARTIFACT`; declared-format sniff
or parser failure -> `ERROR/FORMAT_MISMATCH`; post-command dirty state ->
`NO_MEASUREMENT/DIRTY_TREE`. Already-typed `AssayError` values retain their
pair. Unexpected exceptions escape as programmer defects and cannot be rendered
as a plausible verdict.

Traceability is fixed: work 1–2 -> `git.py` -> O1 -> hostile two-repo ledger;
work 3–4 -> coverage reservation -> O2/O4 -> special/stale/oversize matrix;
work 5–6 -> `run_lane` -> O3 -> complete-artifact and post-dirty matrix. The
REPORT names the actual tests and the failure count after breaking each guard.

### Degrees of freedom

Private helper and reservation type names may differ. The single Git owner,
replacement environment, 16 MiB read ceiling, freshness protocol, terminal
mapping, whole-repository post-check, and topology above may not.

## Work

1. Make `git.py` the only Git process boundary. Resolve the executable explicitly, start from a minimal controlled environment, remove all ambient repository/config selectors, disable system/global config and executable diff/textconv/fsmonitor behavior, pass end-of-options-safe operands, and anchor every command to the exact supplied repository. Missing/unusable Git remains a typed terminal, never another repository or a local configuration fallback.
2. Add real two-repository attacks for `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG_*`, local external diff/textconv, fsmonitor, aliases, and hooks where relevant. A test proves exact HEAD/path bytes, not merely that Git returned zero.
3. Replace path-based `read_text` coverage ingestion with a bounded safe-open sequence: reject symlinks and non-regular files, bind the opened descriptor to the checked inode, enforce a documented maximum byte count before decoding/parsing, and reject replacement races. Never open a FIFO/device in a way that can block judgment.
4. Bind coverage freshness to each execution. Remove or fingerprint the prior ordinary artifact only after all pre-run refusal checks; after execution require a newly produced file for this invocation. R2/R3 controls and transforms must not inherit a baseline artifact through a repository copy. Preserve A-140: a refused run does not delete anything.
5. Put every expected post-HEAD failure inside the complete-artifact path, including `evaluate_coverage` normalization collisions, bounded source reads, Unicode/filesystem errors, and Git decode failures. Do not blanket-catch programmer defects. Initial HEAD resolution may still be pre-artifact because there is no honest commit identity.
6. After the declared command, compare the whole repository against the resolved pre-run commit before awarding any claim. A mutation anywhere — including tests, support files, ignored-policy files, index state, or paths outside `source_roots` — makes the run non-PASS and prevents claims bound to the old commit. Preserve the command result in the artifact metadata; do not clean or restore consumer state.
7. Run the installed-wheel complete-artifact suite under hostile Git variables and filesystem objects. Break each guard individually and record the exact A-067 failure count.

## Test constraints copied from AUTHORING.md §3b

**A. Nothing may make the verdict depend on how fast the machine is.** (L20)
- ✗ `deadline = time.monotonic() + N` followed by an assertion. A time budget is
  a proxy for "eventually" and is hardware-dependent by construction.
- ✗ `time.sleep(N)` to "let the thread get there", then assert.
- ✗ Asserting on elapsed time, or on how many iterations something completed.
- ✓ Wait on a **real synchronization point**: `join()` a process/thread, block on
  an `Event` the code under test sets, drain a queue.
- ✓ **Best: remove the wait.** Extract the pure per-iteration step and call it
  directly from the main thread. Deterministic *and* trivially coverable.
- ✓ A timeout is legal ONLY as a failsafe against hanging the suite forever
  (make it generous — 60s, not 3s). It must never be the thing that decides
  pass/fail. If shrinking the timeout could flip the result, it is an oracle.
- **Rule: a test that fails when the machine is slow is a TRUE red — a real race
  the slow host revealed. Fix the test. Never widen a timeout, and never raise a
  cgroup weight / add CPU to make a suite pass.**

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
- ✗ Mutating **process-global** state (logging config, `os.environ`, module
  attributes, singletons) without restoring it. Under `pytest-xdist` the damage
  lands in whichever test shares that worker. (PL7 §5)
- ✗ `monkeypatch.setattr` on an object that synthesizes attributes via
  `__getattr__` (lazy proxies, `SimpleNamespace` façades, ORM rows). Teardown
  *materializes* the patched attribute as a permanent instance attribute and
  pins it forever. Patch the **namespace that owns it** instead. (L19)
- ✗ Teardown that destroys shared state rather than restoring the prior value.
- ✓ Fresh `tmp_path` per test; assert cleanup actually restored what it found.
- When a test fails only in the full parallel suite, ask **"what did an earlier
  test leave behind?"** before "what raced?" — pollution is more common than a
  race and reproduces deterministically once you know the pair.

**C. No hollow tests.** (§3 above, and DOCTRINE's review checklist)
- ✗ A test body that is `pass`, or asserts only that nothing raised.
- ✗ Asserting implementation trivia (a call count, a private attribute, a log
  string) instead of the behavioral contract.
- ✗ Weakening or deleting an assertion to get past a failure.
- ✓ Assert the **contract**: given this input/state, this observable outcome.
- ✓ Where a check guards a real crash, add a test proving the crash is real —
  it ties the check to reality instead of to a style rule.

**D. No coverage evasion.** (L11, GA2b)
- ✗ A no-cover exclusion pragma on changed lines. nyxloom's gate **rejects**
  them, and note it matches the literal token anywhere on a line — including in
  a comment that merely *describes* the rule.
- ✗ Excluding an `except` body and assuming the `except` clause is covered too —
  it is not; that off-by-one killed a diff-coverage floor once already. (L11)
- ✓ If a line is genuinely unreachable, restructure so it does not exist.

**E. Network, clock, and filesystem are inputs — control them.**
- ✗ Real network calls, real registries, real model endpoints in a unit test.
- ✗ `datetime.now()` / `time.time()` where the assertion depends on the value.
- ✓ Inject or mock the boundary; make offline the default path.

**Author's check:** for every test you specify, ask *"could this flip its verdict
on a slower machine, in a different worker, or in a different order?"* If yes,
it is not an oracle yet.

## Package-specific test emphasis

**A. No speed-dependent verdicts.** FIFO/device attacks are proven by nonblocking safe-open behavior or a child-process failsafe, never an elapsed-time threshold.

**B. No order/worker dependence.** Every test owns fresh repositories, environment snapshots, and artifact paths; hostile process state is restored.

**C. No hollow tests.** Compare exact repository OIDs, paths, complete artifacts, and pre/post hashes under real hostile inputs.

**D. No coverage evasion.** Preserve 100% statement/branch coverage and mutation-check every new guard.

**E. Control inputs.** Git histories, config, hooks, filesystem objects, clocks, and process results are disposable local fixtures; no network or ambient repository is evidence.

## Scope / forbid

This package hardens facts already represented by schema v3. It must not redesign isolation, mutation/canary payloads, or the schema. Verdict-output write failures needing a new reason, full mutant evidence, canary-target binding, and exclusion capability belong to P21.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

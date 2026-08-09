---
schema_version: 1
id: assay-P20-repository-artifact-boundary-integrity
project: assay
title: "Repository identity and measured artifacts survive adversarial process state"
tier: implement-2
input_revision: "8aad3dc3b190915bb27881a0f3004b339aeef9c2"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P19-isolated-r3-cli-pipeline]
session: fresh
scope:
  touch: ["src/assay/git.py", "src/assay/safeio.py", "src/assay/coverage.py", "src/assay/evaluate.py", "src/assay/runner.py", "src/assay/cli.py", "tests/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/schemas", "src/assay/verdict.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/adapters", "nyxloom-trove/carve-assets/P20/README.md", "nyxloom-trove/carve-assets/P20/skeleton.patch", "nyxloom-trove/carve-assets/P20/probe_git_boundary.py", "nyxloom-trove/carve-assets/P20/test_acceptance.py", "nyxloom-trove/carve-assets/P20/expected/post-dirty-v3.json"]
oracles:
  - id: O1
    observable: "Every Git query is anchored to the supplied repository under a sanitized Git environment; hostile GIT_DIR/GIT_WORK_TREE/config, .git/info/exclude, hooks, external diff, textconv, or another repository cannot change the resolved HEAD, diff, or dirty set beyond a clean committed .gitignore policy"
    negative: "Pointing GIT_DIR at a second seeded repository changes Assay's recorded commit or path set"
    gate: tester-unified
  - id: O2
    observable: "run_lane accepts coverage only when its command invocation created a fresh bounded regular non-symlink file, opened and validated without a blocking special-file read"
    negative: "A copied stale profile, FIFO, device, symlink swap, or oversized file is parsed or can hang beyond the lane process timeout"
    gate: tester-unified
  - id: O3
    observable: "After HEAD is known, every expected Git, decode, coverage, source-read, and evaluation refusal emits a complete artifact; a lane command that leaves Git-visible non-artifact dirt anywhere cannot retain higher-rigor PASS claims bound to the pre-run commit"
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
- Readiness: **READY against input/JIT anchor
  `8aad3dc3b190915bb27881a0f3004b339aeef9c2`; the readiness commit is the
  commit containing this updated packet and report, as amended by
  `reports/assay-P20-JIT-CARVE-REVIEW-AMENDMENT.md`.** Sol ran AUTHORING's exact
  pre-dispatch adversarial specification review, corrected the packet, and
  committed the locked skeleton, hostile inputs, handwritten artifact, probes,
  and review disposition under `nyxloom-trove/carve-assets/P20/` and
  `nyxloom-trove/reports/assay-P20-JIT-CARVE.md`. The branch child must still
  reconcile `input_revision..HEAD` before editing; READY is not permission to
  skip repository drift.
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
7. `nyxloom-trove/carve-assets/P20/README.md`, then the compiling
   `skeleton.patch`, locked `test_acceptance.py`, independent
   `expected/post-dirty-v3.json`, and witnessed `probe_git_boundary.py`.
   These are acceptance inputs, not implementation suggestions, and are
   forbidden to edit.
8. `nyxloom-trove/reports/assay-P20-JIT-CARVE.md` for the exact adversarial
   review, probe transcripts, asset hashes, and pairwise/combined matrix.

## Implementation packet (normative)

### Interfaces and ownership

- `src/assay/git.py` remains the only module that launches Git. Keep `run(repo,
  *args) -> str` and the typed wrappers; one raw-byte subprocess seam owns all
  argv construction, environment replacement, exit translation, and UTF-8
  decoding. No caller outside `git.py` may invoke `git`, inspect `.git`, or add
  its own decoder.
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
      "GIT_NO_REPLACE_OBJECTS": "1",
      "GIT_TERMINAL_PROMPT": "0",
      "GIT_OPTIONAL_LOCKS": "0",
      "GIT_PAGER": "",
      "PAGER": "",
  }
  ```

  The committed JIT probe proves `C.UTF-8` in `tester-unified`. No ambient
  `GIT_*`, `HOME`, `XDG_*`, `PATH`, pager, editor, config counter, replacement
  ref, object directory, alternate, work-tree, or repository selector crosses
  the child boundary.

  Repository anchoring is two-stage and exact. Resolve the supplied directory,
  walk its finite ancestor chain to the nearest **non-symlink** `.git` directory
  or regular gitfile, and call only sanitized `rev-parse --absolute-git-dir` to
  resolve the real Git directory (linked worktrees are supported). Refuse no
  marker, a symlink marker, a non-directory/non-regular marker, an invalid
  gitfile, a non-absolute result, or a result that is not an existing
  directory. Every substantive command then uses the resolved pair explicitly:

  ```text
  <absolute-git> --no-pager --no-optional-locks --literal-pathspecs
    --git-dir=<resolved-git-dir> --work-tree=<resolved-repo-top>
    -c core.quotePath=false -c core.hooksPath=/dev/null -c core.fsmonitor=
    -c commit.gpgSign=false -c core.excludesFile=
    -C <resolved-repo-top> <fixed-subcommand> ...
  ```

  `--git-dir`/`--work-tree` are required: `-C` and even a command-line
  `-c core.worktree=...` do **not** defeat a repository-local `core.worktree`
  redirection, as the JIT probe demonstrated. Diff calls insert
  `--no-ext-diff --no-textconv` at the boundary even when the caller omitted
  them. Signing is disabled even when local config requests it. No command
  invokes checkout, filters, hooks, aliases, an editor, transport, or a user
  program. User-controlled revisions are first
  validated/resolved to full OIDs; paths follow `--` under
  `--literal-pathspecs`.

  `dirty_paths` unions the NUL-safe porcelain status records with this exact
  second query:

  ```text
  git ls-files --others --exclude-per-directory=.gitignore -z --
  ```

  The second query deliberately does **not** use `--exclude-standard`: only
  clean per-directory `.gitignore` files are repository policy. A modified or
  untracked `.gitignore` is itself dirty; a committed clean `.gitignore` may
  exempt the declared coverage artifact. Global/system/configured excludes are
  neutralized, and `.git/info/exclude` cannot hide an otherwise untracked path.
  Do not enumerate all ignored paths and subtract the artifact: that would
  replace the repository's committed ignore policy with an Assay-specific
  exception list.

  Both Git output streams are fixed work bounds. Exceeding either bound kills
  the child and produces `ERROR/GIT_FAILED`; truncating retained stderr while
  continuing to drain it is not bounded work.
- `src/assay/safeio.py` owns bounded nonblocking regular-file opening so P26
  can reuse the same descriptor/inode/limit discipline for attestations. Apply
  the committed skeleton before implementation; these signatures are public
  and frozen:

  ```python
  class OutputReservation:
      @property
      def artifact(self) -> str: ...
      @property
      def limit(self) -> int: ...
      def arm(self) -> None: ...
      def consume(self) -> bytes | None: ...
      def close(self) -> None: ...
      def __enter__(self) -> Self: ...
      def __exit__(self, ...) -> None: ...

  def reserve_output(
      project_root: Path, artifact: str, *, limit: int
  ) -> OutputReservation: ...

  def read_bounded_file(
      project_root: Path, artifact: str, *, limit: int
  ) -> bytes | None: ...
  ```

  Caller-visible metadata is immutable; descriptor ownership is deliberately
  stateful: `RESERVED -> ARMED -> CONSUMED` or `RESERVED/ARMED -> CLOSED`.
  `close()` is idempotent. Every other repeated/out-of-order transition raises
  `RuntimeError`; a consumed/closed integer descriptor must never be reused.
  `reserve_output` has no path side effect. `arm` is the only operation that
  may unlink a verified unchanged pre-run regular artifact.

  `artifact` is a non-empty lexical project-relative POSIX spelling: no
  absolute path, empty component, `.` or `..`; `limit` is positive. Traverse
  from one opened project-root descriptor with `dir_fd` and
  `O_DIRECTORY|O_NOFOLLOW`; every parent must already be a real directory.
  Hold the final parent descriptor, its device/inode, basename, and the
  destination's pre-reservation device/inode if one existed. Reject a
  symlink/special destination. `arm` rechecks the same object before unlinking.
  `consume` opens relative to that same held descriptor with
  `O_RDONLY|O_NONBLOCK|O_NOFOLLOW`, requires a regular `fstat`, rejects the
  removed prior inode if relinked, reads at most `limit + 1`, and consumes the
  reservation once. Renaming the parent and writing at a replacement pathname
  returns missing, never bytes from the replacement tree. `read_bounded_file`
  uses the same traversal/open/type/bound discipline but never unlinks.
  Missing returns `None`; an unsafe/unreadable/oversized object is
  `ERROR/UNREADABLE_ARTIFACT`.
- `src/assay/coverage.py` owns byte decoding and format parsing. Freeze these
  names and signatures; keep `load_coverage_profile(text, ...)` as the pure
  text-level compatibility seam:

  ```python
  MAX_COVERAGE_ARTIFACT_BYTES = 16 * 1024 * 1024

  def parse_coverage_artifact(
      raw: bytes | None, *, declared_format: str
  ) -> CoverageProfile: ...

  def read_coverage_artifact(
      project_root: Path, artifact: str, *, declared_format: str
  ) -> CoverageProfile: ...
  ```

  `parse_coverage_artifact(None, ...)` is
  `NO_MEASUREMENT/EMPTY_COVERAGE`; invalid UTF-8 is
  `ERROR/UNREADABLE_ARTIFACT`; sniff mismatch is `ERROR/FORMAT_MISMATCH`; a
  declared-format parser retains its typed error. `read_coverage_artifact`
  calls `safeio.read_bounded_file` with the constant, then calls the byte
  parser exactly once. No coverage owner calls `Path.read_text`.
- Add `profile: CoverageProfile | None = None` as the final keyword argument of
  `runner.evaluate_r1`. A supplied profile is the already parsed output owned
  by `run_lane` and is never reread; `None` makes direct/canary callers use
  `read_coverage_artifact(project_root, judge.coverage.artifact, ...)`. Move
  parsing, diff parsing, normalized-key evaluation, and bounded source reads
  into the existing total `AssayError` translation. Convert expected
  `OSError`/`UnicodeError` from the source-read boundary to
  `ERROR/UNREADABLE_ARTIFACT`; do not catch arbitrary programmer errors.
- `runner.run_lane` owns reservation and terminal assembly. It creates the
  reservation before any refusal, calls `arm()` only after all pre-run checks,
  executes once, then performs the whole-repository dirty check **before**
  consuming/parsing output or starting R1/R2/R3. It consumes and parses the
  artifact once and injects the valid profile into `evaluate_r1`. If that
  consume/parse raises a typed `AssayError`, build the R1 claim directly with
  its exact pair, omit `judgment.r1`, and preserve today's independent handling
  of any other declared level; do not call `evaluate_r1` and do not reread.

### Required flow and topology

```text
supplied project path -> sanitized repo_top + HEAD/base -> pre-run refusal checks
                     -> reserve coverage path -> arm/remove -> execute command
                     -> whole-repo dirty check -> consume/parse once -> evaluate
                     -> exactly one complete verdict bound to the pre-run HEAD
```

The project may be `repo_top/apps/project`; Git identities and dirty paths are
repo-top-relative, while the declared artifact remains project-relative. Join
each spelling only at its named owner. Never validate a snapshot, host, or
container spelling with the consumer process's local filesystem.

### Decision and proof matrix

| State | Terminal | Command allowed? | Required negative fixture |
|---|---|---:|---|
| hostile Git selectors/config/local `core.worktree`/replace ref | exact seeded repo identity or `ERROR/GIT_FAILED` | no wrong-repo run | two repos with different HEAD/path bytes plus local redirect and replace ref |
| stale regular profile | remove, then require a new inode/file | yes, after removal | zero-exit producer writes nothing |
| FIFO/device/symlink/oversize | `ERROR/UNREADABLE_ARTIFACT` | never block | each real filesystem object |
| command dirties any repo path | `NO_MEASUREMENT/DIRTY_TREE` | already ran | mutate a tracked support file outside source roots |
| expected post-HEAD decode/evaluation error | complete typed artifact | as flow dictates | normalized-key collision plus invalid UTF-8 |

For post-command dirt, preserve the actual R0 command claim when higher rigors
are declared, and render every declared `R1`/`R2`/`R3` claim as
`NO_MEASUREMENT/DIRTY_TREE`; do not start any of them. For an R0-only lane,
R0 itself is `NO_MEASUREMENT/DIRTY_TREE`. The result still carries the actual
resolved command plan and its real start/end; the final verdict `ended` is the
clock observation after the dirty check. Do not invent a schema-v3 return-code
field. `expected/post-dirty-v3.json` freezes the R0+R1 form.

The terminal translation is closed, not a blanket `except`: sanitized Git
exit/decode -> `ERROR/GIT_FAILED`; missing or well-formed zero-file coverage ->
`NO_MEASUREMENT/EMPTY_COVERAGE`; unsafe type, bound, UTF-8, source read, or
normalized-key collision -> `ERROR/UNREADABLE_ARTIFACT`; declared-format sniff
mismatch -> `ERROR/FORMAT_MISMATCH`; a malformed record retains the selected
parser's typed `ERROR/UNREADABLE_ARTIFACT`; post-command dirty state ->
`NO_MEASUREMENT/DIRTY_TREE`. Already-typed `AssayError` values retain their
pair. Unexpected exceptions escape as programmer defects and cannot be
rendered as a plausible verdict.

Traceability is fixed: work 1–2 -> `git.py` -> O1 -> hostile two-repo ledger;
work 3–4 -> coverage reservation -> O2/O4 -> special/stale/oversize matrix;
work 5–6 -> `run_lane` -> O3 -> complete-artifact and post-dirty matrix. The
REPORT names the actual tests and the failure count after breaking each guard.

### Prepared proof material (locked)

From the P20 worktree, before filling TODOs:

```sh
git apply assay/nyxloom-trove/carve-assets/P20/skeleton.patch
PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P20/test_acceptance.py -q
```

The skeleton must compile and the locked suite must fail for the named product
reasons. After implementation, that exact suite must pass unchanged, followed
by the registered gate. `probe_git_boundary.py` is the independently executed
argv/environment tracer; `expected/post-dirty-v3.json` is handwritten and may
only have `<HEAD>`/`<COMMAND>` substituted by its test.

### Degrees of freedom

Private helper names and equivalent local decomposition may differ. The named
safe-I/O and coverage interfaces, state transitions, single Git owner,
replacement environment, Git anchoring options, 16 MiB read ceiling,
freshness protocol, terminal precedence, whole-repository post-check, locked
assets, and topology above may not.

## Work

1. Make `git.py` the only Git process boundary. Resolve the executable explicitly, start from a minimal controlled environment, remove all ambient repository/config selectors, disable system/global/configured excludes and executable diff/textconv/fsmonitor behavior, pass end-of-options-safe operands, and anchor every command to the exact supplied repository. Form the dirty set from NUL-safe status plus the packet's `.gitignore`-only untracked query, so `.git/info/exclude` cannot add policy. Kill the child when either output stream crosses its fixed bound. Missing/unusable Git remains a typed terminal, never another repository or a local configuration fallback.
2. Add real two-repository attacks for `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG_*`, local `core.worktree`, replace refs, external diff/textconv, fsmonitor, aliases, and hooks where relevant. A test proves exact repo top, HEAD, diff, and path bytes, not merely that Git returned zero.
3. Replace path-based `read_text` coverage ingestion with a bounded safe-open sequence: reject symlinks and non-regular files, bind the opened descriptor to the checked inode, enforce a documented maximum byte count before decoding/parsing, and reject replacement races. Never open a FIFO/device in a way that can block judgment.
4. Bind `run_lane` coverage freshness to its execution. Remove or fingerprint the prior ordinary artifact only after all pre-run refusal checks; after execution require a newly produced file for this invocation. Preserve A-140: a refused run does not delete anything. P22/P23—not this forbidden scope—replace repeated R2/R3 working-tree copies so their controls/transforms cannot inherit a baseline artifact.
5. Put every expected post-HEAD failure inside the complete-artifact path, including `evaluate_coverage` normalization collisions, bounded source reads, Unicode/filesystem errors, and Git decode failures. Do not blanket-catch programmer defects. Initial HEAD resolution may still be pre-artifact because there is no honest commit identity.
6. After the declared command, compare the whole Git-visible repository against the resolved pre-run commit before awarding any claim. A staged, unstaged, or untracked non-ignored mutation — including tests, support files, the tracked ignore-policy file, index state, or paths outside `source_roots` — makes the run non-PASS and prevents claims bound to the old commit. The declared ignored coverage output is expected and does not itself dirty the tree. Preserve the command plan/timing in the artifact; do not clean or restore consumer state.
7. Run the locked acceptance suite unchanged, add ordinary regression/installed-wheel cases for each public behavior, and run the registered `tester-unified` gate. Break each guard individually and record the exact A-067 failure count.

## Environment setup and real gate

No live stack or network is needed. Use a fresh P20 worktree, apply the locked
skeleton exactly once, and run the locked suite as shown above. The ship signal
is exactly `[gates.tester-unified].argv` in
`nyxloom-trove/nyxloom.toml`, substituting the P20 worktree for `{worktree}`;
do not transcribe or simplify its wheel build, independent self-hosting check,
host bind, or validated background-cgroup helper. The reviewer reruns the same
registered argv from the reviewed commit.

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

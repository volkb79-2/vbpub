---
schema_version: 1
id: assay-P26-attested-evidence-cli-hardening
project: assay
title: "Declared attested evidence is bounded, contained, and path-current"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P25-real-python-project-qualification]
session: resume:assay-v11-attestation
scope:
  touch: ["src/assay/cli.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/attestation.py", "src/assay/safeio.py", "tests/**", "README.md"]
  forbid: ["src/assay/verdict.py", "src/assay/schemas", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "Lane configuration round-trips an explicit ordered list of (source,key) evidence declarations and assay run emits exactly one sibling evidence entry per identity"
    negative: "Omitting one declared identity or placing it in claims fails complete-artifact comparison"
    gate: tester-unified
  - id: O2
    observable: "Evidence keys and files remain beneath the attestation root; traversal, absolute keys, symlinks, oversize files, excess reviewed paths, invalid repo paths, and non-commit revisions are rejected within fixed work bounds"
    negative: "A ../ key reads a seeded outside file or an oversized record launches Git"
    gate: tester-unified
  - id: O3
    observable: "Files and directories reviewed at an ancestor are current only when Git reports no changes beneath each reviewed path"
    negative: "Changing a child of a reviewed directory while exact-membership logic remains returns PASS"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "safe bounded parsing requires a runtime dependency"
  - "directory staleness cannot be expressed with Git path semantics"
mutexes: []
---

# P26 — attested evidence CLI hardening

The claim to attack: **every declared attestation is resolved exactly once from bounded contained input and is current for every path it claims to cover.**

## Dispatch contract

- Contract class: **2c — bounded integration**.
- Required roles: **Sonnet xhigh implementer → Opus xhigh independent reviewer**;
  route to Sol if P21's final v4 evidence grammar differs from this packet.
- Readiness: **PROVISIONAL until P25 merges.** The pre-dispatch pass must replace
  symbolic attested commits with full object IDs and commit the hostile directory,
  newline, traversal, symlink-swap, and oversize fixtures.
- Implementer freedom: private safe-I/O and loop decomposition only. Declaration
  grammar, identity, bounds, per-path Git query, and terminal mapping are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P26-attested-evidence-cli-hardening`
on branch `feat/assay-P26-attested-evidence-cli-hardening`.

## Context to read first

1. `docs/DESIGN-GUIDE.md` §§3, 6, 7, and 12; decisions A-024, A-033–A-034, A-041, A-067, A-074–A-078, A-085, A-110–A-111, A-134 and A-153–A-161.
2. `src/assay/attestation.py` and every `tests/test_attestation_*` file. Reproduce finding 7's reviewed-directory false PASS and finding 8's `../` key escape before implementation.
3. P15's lossless Git path boundary, P16's unchanged sibling evidence shape, and P17's commit-bound CLI assembly. Reuse them rather than adding a second Git/parser/verdict mechanism.
4. `src/assay/config.py` and `JudgeConfig.as_declared`; there is currently no lane declaration source for evidence. Add one closed shape rather than another opaque table.
5. `nyxloom-trove/reports/assay-v1-post-series-review-sol.md` findings 7–8 and its security recommendations.
6. `/workspaces/vbpub/nyxloom/reference/DOCTRINE.md` defaults, bounded evidence, and fail-closed input rules.

## Implementation packet (normative)

### Declaration and record grammar

The only accepted lane shape is:

```toml
[lanes.<name>.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {source = "attested", key = "security-review"},
  {source = "attested", key = "api-review.v2"},
]
```

Both fields are required together and forbidden when the array is empty.
`attestation_dir` is a nonempty project-relative path, never absolute or
escaping, and every existing component from project root through the directory
must be a real directory, not a symlink. Evidence preserves declaration order,
has exactly `source` and `key`, supports only `source="attested"`, and rejects
duplicate `(source,key)` identities. A key matches
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; therefore it cannot begin with `-` or contain
a separator/control byte. Its file is exactly `<attestation_dir>/<key>.json`.

The JSON record is a closed object with no unknown keys:

```json
{"producer":"human:alice","attested_commit":"<40 lowercase hex object id>",
 "reviewed_paths":["src/api.py","docs/contracts"]}
```

Invalid examples include a key containing a parent-directory segment, absolute `attestation_dir`, an
unknown record member, duplicate paths, an empty producer, a symbolic/short/
uppercase commit, or a reviewed path containing NUL/`..`/absolute spelling.
They all fail before a Git comparison. `attested_commit` is evidence identity,
not a revision expression: only `[0-9a-f]{40}` is accepted.

### Fixed bounds and safe I/O

| Item | Bound | Refusal |
|---|---:|---|
| evidence declarations | 64 | `ERROR/BAD_LANE_CONFIG` |
| attestation file | 1 MiB | `ERROR/UNREADABLE_ARTIFACT` |
| producer UTF-8 bytes | 256 | `ERROR/UNREADABLE_ARTIFACT` |
| reviewed paths per record | 1,000 | `ERROR/UNREADABLE_ARTIFACT` |
| one reviewed path UTF-8 bytes | 4,096 | `ERROR/UNREADABLE_ARTIFACT` |
| total Git path comparisons per lane | 4,096 | `ERROR/UNREADABLE_ARTIFACT` |

Reuse/extend P20's `safeio.py` regular-file open, but traverse from an opened
`project_root` directory descriptor: open every `attestation_dir` component
with `dir_fd`, `O_DIRECTORY|O_NOFOLLOW`, then open `<key>.json` relative to the
final descriptor with `O_NONBLOCK|O_NOFOLLOW`. Never `resolve()`/`is_file()` and
then reopen a pathname; that is a symlink-swap race. `fstat` the already-open
descriptor before reading, read limit+1, and decode UTF-8 once. Absence is the only
`MISSING_ATTESTATION` case; symlink/special/oversized/malformed is unreadable.
Enforce every structural/cardinality bound before the first Git call for that
record, and the aggregate comparison bound before resolving any evidence.

### Git flow and exact path semantics

1. Require the declared 40-lowercase-hex `attested_commit`, then verify that
   exact identity once with sanitized `rev-parse --verify --end-of-options
   <oid>^{commit}` and require byte-for-byte the same full OID. Never accept or
   resolve `HEAD`, a branch/tag, abbreviation, uppercase spelling, or reflog.
2. Interpret sanitized `merge-base --is-ancestor <attested-oid> <head-oid>`
   exit 0 as current ancestry and exit 1 as unreadable/unrelated; no display
   output is parsed.
3. Prove each normalized repo-top-relative path exists at the attested OID with
   sanitized `git --literal-pathspecs ls-tree -z <oid> -- <path>`. Parse the
   bounded NUL record as bytes and require exactly one exact path match of blob
   or tree type; no text decode/display-name membership decides identity.
4. For each path, run sanitized `git --literal-pathspecs diff --quiet
   --exit-code --no-ext-diff --no-textconv <attested-oid> <head-oid> --
   <path>`. Exit 0 means current;
   exit 1 means `NO_MEASUREMENT/STALE_ATTESTATION`; other exit is typed Git
   failure. Git's pathspec makes a directory cover descendants and a file cover
   only itself. Do not obtain a newline-delimited changed-name list at all.
5. Resolve all declared identities independently and emit one ordered sibling
   `evidence[]` entry per declaration with `verified_by_assay=false`.

| State | Evidence result |
|---|---|
| file absent | `MISSING_ATTESTATION` |
| unsafe/malformed/limit/unresolvable or unrelated revision | typed non-PASS for that identity; later identities still resolve |
| descendant changed beneath reviewed directory | `STALE_ATTESTATION` |
| unrelated path changed | current PASS evidence |
| declaration omitted or duplicated in output | complete-artifact equality failure |

Traceability: work 1–2 -> config containment -> O1/O2; work 3–5 -> safe
record/path loader -> O2; work 6 -> Git path comparison -> O3; work 7–8 -> CLI
ordering/artifacts -> O1 and all negatives. The REPORT gives actual tests and
break counts. Private parser/helper names may vary; grammar, bounds, safe-open,
Git commands/exit meanings, ordering, and sibling evidence placement may not.

## Work

1. Add a closed ordered `judge.evidence` array of inline `{source, key}` declarations. Support only `source="attested"`; preserve the adjudicated sibling reservation without inventing a registry. Reject unknown keys, duplicate identities, unsafe keys, and evidence declarations on a lane whose configuration cannot resolve them.
2. Add an explicit project-relative attestation directory declaration. Resolve it beneath project root, reject symlink escape, and never derive or default its location.
3. Define fixed documented limits for file bytes, producer/key/path string lengths, reviewed-path count, and total Git comparisons. Enforce byte size before JSON parsing and all structural limits before launching Git.
4. Restrict keys to a closed safe identifier grammar so `<key>.json` cannot contain separators, traversal, option-like syntax, or control bytes. Open only a regular non-symlink file beneath the resolved directory; missing remains `MISSING_ATTESTATION`.
5. Require each reviewed path to be normalized, NUL-free, repo-top-relative,
   non-escaping, and present at the attested commit through the packet's exact
   bounded `ls-tree -z` query. Require `attested_commit` itself to be a full
   lowercase object identity and verify it through the end-of-options-safe Git
   command before ancestor/staleness work; never turn a symbolic current name
   into apparently immutable evidence.
6. Replace flat changed-name membership with the packet's one bounded, literal-pathspec `git diff --quiet --exit-code` comparison per reviewed path. Interpret only its exit status through P20's sanitized Git boundary; do not request or decode a changed-name list. A reviewed directory is stale when any descendant changed; a file is stale only when that file changed, even when its name contains pathspec metacharacters.
7. Wire declarations through `assay run` into exactly matching `declared_evidence[]`/`evidence[]` entries. Preserve order, `verified_by_assay=false`, and independent resolution of later identities after one malformed record.
8. Add installed-wheel complete artifacts for current, stale file, stale directory, absent, malformed, unrelated/descendant commit, and limit violation. Break containment, bounds-before-Git, OID resolution, path staleness, sibling placement, and identity coverage separately; record exact A-067 failure counts.

## Carried in from the P15–P19 post-series review

**A-O15 is no longer open: this package owns it.**
`attestation._changed_paths` currently asks Git for newline-delimited display
paths and then calls `splitlines()`. A filename containing a newline remains
C-quoted and never matches its attested identity; a raw U+2028 is split into
two phantom paths. Both were reproduced against real Git. The implementation
packet removes the entire display-name set: one `diff --quiet` pathspec query
answers the actual question without a second decoder or filename transport.

P20–P23 also change the substrate this handoff inherits: verdicts are v4,
all execution happens from the bound committed-object snapshot/plan, and expected
post-HEAD refusals are complete artifacts. Do not reintroduce a working-tree
copy, ambient Git call, unbounded read, or v3 fixture while adding evidence.

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

**A. No speed-dependent verdicts.** Fixed input/work bounds and completed Git processes decide; timing never does.

**B. No order/worker dependence.** Each repository and attestation directory is fresh; no identity may affect its sibling's resolution.

**C. No hollow tests.** Seed real outside/symlink/oversize/directory-change attacks and compare complete evidence objects.

**D. No coverage evasion.** Maintain 100% statement/branch and record every security/property mutation's real failure count.

**E. Control inputs.** All JSON, filesystem, and Git histories are local disposable inputs; no network or ambient attestations.

## Scope / forbid

This package wires and hardens Tier-3 attested evidence only. It must not add an adjudicator, change sibling schema identities, edit adapters, or alter R2/R3 computed claims.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to the log, commit, and exit. Do not improvise a workaround.

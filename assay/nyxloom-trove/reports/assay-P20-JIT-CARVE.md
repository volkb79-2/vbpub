# Assay P20 JIT carve and pre-dispatch adversarial specification review

> **Date:** 2026-08-09  
> **Frozen input/JIT anchor:**
> `8aad3dc3b190915bb27881a0f3004b339aeef9c2`  
> **Handoff:**
> `nyxloom-trove/handoffs/assay-P20-repository-artifact-boundary-integrity.md`  
> **Carver:** Sol xhigh  
> **Disposition:** **READY for Sonnet xhigh implementation**, followed by a
> fresh Opus xhigh independent review  
> **Canonical method:** `nyxloom/reference/AUTHORING.md`, exact
> “Pre-dispatch adversarial handoff review” prompt

## Result first

P20 is READY. The JIT pass did not merely fill the previously named proof gap;
it found three more hostile Git/executable shapes and three underspecified
implementation seams before dispatch:

1. a repository-local `core.worktree` redirects topology despite `-C` and even
   a command-line `-c core.worktree=...`;
2. a replace ref can turn a real base-to-HEAD diff into an empty diff while all
   ref spellings remain valid;
3. a canary commit can launch a locally configured signing program even when
   hooks and external diffs are disabled;
4. an “immutable reservation consumed once” had no coherent ownership state
   and could reuse a closed integer descriptor;
5. the old flow placed the post-command dirt check after evidence consumption,
   leaving terminal precedence and higher-rigor side effects undefined; and
6. coverage byte parsing, missing-output semantics, safe direct callers, and
   normalized-key/source-read terminal capture had no exact shared interface.

The packet now fixes those decisions, carries a compiling safe-I/O skeleton,
thirteen locked acceptance cases, one handwritten complete v3 artifact, and a
standalone hostile Git tracer. The assets are carver-owned and explicitly
forbidden to the implementer. The tracer passes inside the real
`tester-unified:local` image; the registered installed-wheel/self-hosting gate
passes at the anchor; and the skeleton plus acceptance suite is a witnessed
controlled red (`13 failed`) before implementation.

## Exact review prompt used

The following prompt was applied verbatim to P20 and every file named by its
“Context to read first” section:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

What follows is the required six-part result. Probe and gate evidence is after
the disposition.

## 1. Blocking ambiguities

All blocking ambiguities found by the review were resolved in the handoff:

| ambiguity | hostile consequence | frozen correction |
|---|---|---|
| `-C <repo>` called “anchored,” with local config still live | local `core.worktree` makes `repo_top` and status describe another tree | finite nearest-`.git` discovery, sanitized Git-dir bootstrap, then explicit resolved `--git-dir` and `--work-tree` on every substantive command |
| replacement env omitted replacement-object policy | `refs/replace/*` changes commit/tree traversal and can erase a diff | exact environment adds `GIT_NO_REPLACE_OBJECTS=1`; locked real replace-ref negative |
| real canary commit retained local signing config | `commit.gpgSign=true` launches an arbitrary configured signer despite hook controls | fixed `-c commit.gpgSign=false`; tracer performs a real allow-empty boundary commit against a sentinel signer |
| executable named only as `git` | absent `PATH` invokes the OS default search or an ambient binary | resolve once from the caller-declared `PATH`; require absolute regular executable; child receives no `PATH`; absence is `ERROR/GIT_FAILED` |
| “private immutable reservation” also had to mutate/consume | stale descriptor number can be used after close/reassignment; repeated operations have no result | public `OutputReservation` skeleton; immutable metadata plus explicit `RESERVED -> ARMED -> CONSUMED/CLOSED`; idempotent close; invalid transitions `RuntimeError` |
| no exact artifact path grammar | implementer chooses whether absolute, dot, missing-parent, or symlink-parent paths work | nonempty lexical project-relative spelling; no absolute/empty/dot/dotdot component; existing real parents only; descriptor-relative no-follow traversal |
| safe-open intent had no callable seam | coverage and later attestation each invent a different race/bound policy | frozen `reserve_output(...)` and `read_bounded_file(...)` signatures; bytes-or-missing; 16 MiB coverage bound |
| missing file was described inconsistently with current `read_text` behavior | one implementation emits `UNREADABLE_ARTIFACT`, another `EMPTY_COVERAGE` | `parse_coverage_artifact(None, ...)` is exactly `NO_MEASUREMENT/EMPTY_COVERAGE` |
| output parsing and direct `evaluate_r1` callers had no ownership rule | `run_lane` can safely consume once only by making direct canary callers unsafe or reopening by path | frozen byte-parser/read APIs; `evaluate_r1(..., profile=None)` means direct safe read, supplied profile means no reread |
| normalized collision/source-read exceptions occur outside the current `try` | traceback after command side effects and no complete artifact | exact expected exception translations moved inside the total R1 terminal path; arbitrary programmer defects still escape |
| post-command dirt came after parse/evaluation and had no claim precedence | stale-commit PASS already exists, or R2/R3 side effects run before refusal | dirty check immediately after command; real R0 preserved when higher rigors exist; every higher claim `NO_MEASUREMENT/DIRTY_TREE`; R0-only gets that terminal |
| P20 text required copied R2/R3 freshness while forbidding their owners | implementer must violate scope or silently omit the requirement | P20 owns `run_lane` reservation; P22/P23 explicitly own committed snapshots and all repeated-execution freshness |
| acceptance material was prose and implementer-owned | implementation and tests can share the same convenient mistake | locked carver-owned skeleton, tests, tracer, and handwritten artifact; every asset is in frontmatter `scope.forbid` |

No externally visible P20 interface, terminal, bound, namespace conversion,
side-effect order, or proof source remains for the implementer to choose.

## 2. False-PASS attacks

| oracle | plausible wrong implementation that passes convenient tests | locked or required attack that defeats it |
|---|---|---|
| O1 | delete ambient `GIT_DIR` but still execute `git -C repo`; test only a normal repo | combine hostile `GIT_DIR`/`GIT_WORK_TREE`/config counter, local `core.worktree`, two different repos, replace ref, local/ambient external diff, and configured signing; assert exact top/OID/diff/status and no sentinel execution |
| O2 | `is_symlink()`/`stat()` then ordinary `read_text`; freshness is only “path absent before run” | real FIFO, symlink, oversize, swap before arm, renamed parent/replacement tree, and relinked prior inode; assert typed terminal or missing without a pathname reopen |
| O3 | catch artifact parser failures but leave `evaluate_coverage` and source reads outside; check dirt only under source roots | command writes a valid profile and mutates tracked `support.txt`; compare the full handwritten verdict; separately collide two raw coverage keys into one normalized key and require a complete artifact |
| O4 | inspect `st_size` then perform an unbounded read, or use the lane timeout as the bound | a 33-byte file against a 32-byte bound plus FIFO under `O_NONBLOCK`; enforce exactly `limit+1` bytes and regular `fstat`, with timeout only as a suite hang failsafe |

The reviewer must still add at least one new combined-axis attack not disclosed
by the implementer. Locked inputs are a floor, not the independent review.

## 3. Missing implementation-packet content

The following missing content was added:

- exact Git executable resolution, replacement environment, repository
  discovery bootstrap, substantive argv, diff flags, replacement-ref policy,
  OID/path operand rules, and output decoder ownership;
- exact safe-I/O public signatures, lexical grammar, metadata, state machine,
  descriptor flags, type/inode rules, bound, return values, errors, and side
  effects;
- exact coverage byte/read APIs, missing/UTF-8/sniff/parser mapping, and one
  parse/no-reread ownership rule;
- exact `evaluate_r1` additive signature and expected exception boundary;
- command → post-dirty → output-consume → evaluation ordering plus complete
  R0-only and R0+higher claim precedence;
- a compiling skeleton and mechanical dispatch commands;
- a full handwritten post-dirty verdict rather than field assertions;
- controlled real Git/filesystem objects and an independently executable tracer;
- asset ownership, hashes, gate recipe source, traceability, and degrees of
  freedom.

## 4. Scope and dependency defects

- `src/assay/safeio.py` was already in touch scope and is now concretely owned.
- The five carver assets are exact frontmatter forbids; the implementer may not
  weaken them.
- `src/assay/canary.py` and `src/assay/mutation.py` remain forbidden. Their
  copied-working-tree defect is not falsely claimed closed: P22 builds the
  committed snapshot substrate and P23 integrates it into baseline/R2/R3.
- P21 remains the sole schema/model migration. P20 adds no reason code, payload,
  return-code field, or compatibility schema.
- P26 may extend `safeio.py` for attestation inputs but must preserve P20's
  landed seam and state machine.
- The serial dependency remains P19 → P20 → P21. P20's implementation branch
  starts from the JIT anchor and reconciles any `input_revision..HEAD` diff;
  no successor is made READY by this disposition.

There is no remaining touch/forbid contradiction for P20.

## 5. Corrected oracle and fixture matrix

### Requirement-to-oracle traceability

| requirement | owner | oracle | independent observable | controlled break |
|---|---|---|---|---|
| exact repository/executable/object identity | `git.py` | O1 | exact repo top, two OIDs, diff bytes, empty status, sentinel absent | ambient selector only, local `core.worktree`, replace ref, external diff, absent declared PATH |
| single-invocation bounded output | `safeio.py`, `coverage.py` | O2/O4 | held-parent behavior, bytes-or-missing, exact typed refusal, bounded regular descriptor | stale regular, swap, FIFO, symlink, oversize, renamed parent, relinked inode |
| complete post-HEAD terminals | `coverage.py`, `evaluate.py`, `runner.py` | O3 | exact complete v3 object and complete collision terminal | missing output, invalid UTF-8/format, normalized collision, source-read failure |
| commit binding after command | `runner.py` | O3 | R0 PASS + R1 DIRTY_TREE handwritten object; consumer dirt retained | valid profile plus tracked support-file mutation outside source roots |
| fixed work/path bounds | all three boundaries | O4 | explicit finite ancestor walk, 16 MiB + sentinel, finite Git argv | absent limit, path reopen, elapsed-time oracle, unconstrained input read |

### Pairwise matrix

| axis A | axis B | required observation |
|---|---|---|
| ambient repo selector | local `core.worktree` | supplied repo still owns exact top and HEAD |
| replace ref | external diff | real diff bytes returned; no external program |
| repo top ≠ project root | project-relative artifact | Git path and artifact path never share an accidental base |
| stale prior inode | pre-arm swap | replacement is refused and not deleted |
| held parent rename | replacement pathname output | reservation reports missing; replacement bytes remain unread |
| FIFO/symlink | limit+1 | no blocking/path follow; exact unreadable terminal |
| valid profile | tracked support dirt | no R1 PASS; complete DIRTY_TREE artifact |
| normalized collision | known HEAD | complete unreadable-artifact claim, no traceback |
| missing output | clean zero-exit R0 | R0 PASS plus R1 EMPTY_COVERAGE |

### Combined-axis fixtures

At least these three are mandatory across locked and ordinary/reviewer tests:

1. two repositories + hostile ambient selectors/config + local
   `core.worktree` + replace ref + external diff;
2. repo ≠ project + stale hardlinked profile + parent rename/replacement path +
   bounded consume;
3. valid fresh profile + tracked support mutation outside `source_roots` +
   complete handwritten artifact;
4. known HEAD + normalized-key collision + full terminal assembly;
5. linked worktree + absent/hostile caller `PATH` cases + registered real gate.

## 6. Disposition

**READY.** Reasons:

- all blocking external choices are frozen in the packet;
- the skeleton compiles from the exact anchor;
- all thirteen locked tests are controlled red on the anchor for their named
  behavioral defects;
- the standalone hostile Git tracer passes both in the cockpit and in
  `tester-unified` with `C.UTF-8`, the absolute Git binary, correct repo/diff,
  local redirect and replace ref defeated, and no external program execution;
- the registered project gate passes from a clean detached anchor worktree;
- frontmatter lint has no errors (one intentional size warning);
- the implementation assets have immutable hashes and are forbidden to the
  implementer;
- P21–P32 remain explicitly provisional.

The implementation route is Sonnet xhigh because the final work is class 2c:
multi-component and security-sensitive, but its public behavior, state machine,
interfaces, examples, proof, bounds, and terminal decisions are fixed. A lower
model is not justified for this pilot; the goal is to measure whether the more
detailed packet removes the P15–P19 common-mode failures before reducing route
cost further.

## Witnessed evidence

### Locked asset hashes

```text
b063bcf84b6d395bea942767dd63ac91191a90b5a891e9829ffd0279a796977f  README.md
20c956aeb047f8357ac2b3d83f2567227c44bdadebc454dbe85414f11af5246a  skeleton.patch
a0fc7b5d8f996e3ae274ea4045e35a07528dccda7806037aa90e9d682e664268  probe_git_boundary.py
76e15ce927edd158d92c02c04c60f8368551f87da687a4cf35c25d10822361c8  test_acceptance.py
a01d3d19c3273b6ef1917b7115486f00645ce588ea07ab7127b00622b7355c6b  expected/post-dirty-v3.json
```

### Skeleton and controlled-red witness

From a disposable detached worktree at the exact anchor:

```text
git apply .../skeleton.patch                         PASS
python -m py_compile assay/src/assay/safeio.py       PASS
locked pytest collection                             13 tests
locked pytest result                                 13 failed in 2.10s
```

Each test failed at the intended absent guard or known product defect; no test
was green before implementation.

### Git tracer in the real image

Launched with `assay/tools/cgroup-parent.sh` and the resulting explicit
`--cgroup-parent`:

```json
{"diff_contains":"alpha-head","external_diff_ran":false,"git":"/usr/local/bin/git","locale":"C.UTF-8","replace_ref_ignored":true,"signing_program_ran":false,"status_bytes":0}
```

The omitted random temp paths/OIDs in this shortened record were different for
repo A and repo B; the full live assertion required exact A HEAD and rejected B.

### Registered gate

The exact `[gates.tester-unified].argv` from `nyxloom-trove/nyxloom.toml` ran
from a clean detached worktree at the anchor, with its Docker-inspected bind and
validated background cgroup unchanged:

```text
tester-unified: PASS (exit 0)
commit: 8aad3dc3b190915bb27881a0f3004b339aeef9c2
independent self-hosting check: 7 passed in 14.21s
```

The P20 carve changes only doctrine/handoff/proof assets, not executable
product code. The locked tracer was separately executed inside the same gate
image so the new environment/argv proposal—not merely the old code—was
witnessed there.

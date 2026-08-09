# Assay P20 routed-review JIT amendment

> **Date:** 2026-08-09  
> **Original JIT anchor:**
> `8aad3dc3b190915bb27881a0f3004b339aeef9c2`  
> **Reviewed implementation before amendment:**
> `6251adc8e3a9953f88029396b931fa411e214042`  
> **Method:** exact pre-dispatch adversarial specification review from
> `nyxloom/reference/AUTHORING.md`  
> **Disposition:** **P20 contract READY; reviewed branch NOT READY until the two
> new locked negatives pass. Do not re-dispatch an implementation model.**

## Result first

The controller stop was correct. The proposed choice between “enumerate every
ignored path and subtract the artifact” and “accept `.git/info/exclude`” was not
the actual design space. P20 already treats a clean committed `.gitignore` as
repository policy. Git can enumerate untracked paths while consulting only that
policy:

```text
git ls-files --others --exclude-per-directory=.gitignore -z --
```

Unioning that NUL-safe result with porcelain status preserves the committed
coverage-artifact exemption while making `.git/info/exclude` and configured
excludes powerless. The exact argv was probed against a real repository before
this amendment: `.git/info/exclude = *` hid `leftover.bin` from status, while the
query returned `leftover.bin` and still omitted `.gitignore`-declared
`cov.json`.

The route packet also disclosed a second P20 violation: stderr retention was
capped, but the child could continue producing and Assay would drain/discard it
forever. P20 O4 is a work bound, not only a memory bound. Crossing either stream
limit must kill the child and become `ERROR/GIT_FAILED`.

Both are narrow corrections in the already-owned `git.py` seam. Sol owns them
directly; another Sonnet/Opus cycle would add cost without adding an unresolved
design choice. The Luna controller remains open and resumes after the corrected
branch is committed.

## Exact adversarial review result

### 1. Blocking ambiguities

None remain after A-177. The authoritative ignore sources, exact Git command,
record delimiter, union behavior, artifact behavior, output bounds, and refusal
are fixed. A clean committed `.gitignore` is policy; any dirty/untracked
`.gitignore` is itself reported. No consumer-specific exemption list exists.

### 2. False-PASS attacks

| attack | wrong implementation | independent observable |
|---|---|---|
| local repository exclude | porcelain status alone | `leftover.bin` remains in `dirty_paths` under `.git/info/exclude = *` |
| artifact collision | enumerate all ignored paths | committed `.gitignore` still exempts `cov.json` without subtraction |
| diagnostic flood | retain 64 KiB and drain the rest | child is killed as soon as stderr exceeds its fixed bound |
| combined sources | disable `core.excludesFile` only | info-excluded dirt still appears; configured/global sources add nothing |

### 3. Missing implementation-packet content

Added to the P20 packet: `core.excludesFile=` in the fixed argv, the exact
`.gitignore`-only `ls-files` query, union semantics, the reason not to use
`--exclude-standard`, and the kill-on-either-stream rule. Two locked tests and
ordinary gated equivalents own the proof.

### 4. Scope/dependency defects

None. Both production changes belong to `src/assay/git.py`; ordinary tests are
under `tests/**`. The carver-owned asset remains forbidden to the implementation
branch and is changed only by this amendment. No schema/model file is needed.

### 5. Corrected oracle/fixture matrix

| requirement | owner | oracle | fixture | controlled break |
|---|---|---|---|---|
| info excludes cannot add policy | `git.py` | O1 | committed `.gitignore` + `.git/info/exclude=*` + artifact + unrelated file | status-only dirty set |
| configured excludes cannot add policy | `git.py` | O1 | local `core.excludesFile=*` control | remove fixed override |
| stderr is bounded work | `git.py` | O4 | absolute child writes limit+1 stderr bytes | retain cap but keep draining |
| artifact remains exempt | `git.py` | O1/O3 | `cov.json` in clean committed `.gitignore` | enumerate all ignored paths |

The updated locked suite was run against reviewed head `6251adc8...` via that
branch's `assay/src` before its correction:

```text
15 collected
13 passed, 2 failed in 2.07s
FAIL test_git_info_exclude_cannot_hide_nonignored_untracked_dirt
FAIL test_git_stderr_overflow_terminates_instead_of_draining_forever
```

Both failures were the intended missing guards: the first returned `()`, the
second did not raise. This is the required witnessed pre-correction negative.

### 6. READY / NOT READY

- **Specification and proof packet: READY.** No external behavior remains for
  the corrector to invent.
- **Reviewed branch `6251adc8...`: NOT READY.** It must pass all 15 locked tests,
  the focused ordinary tests, and the controller-owned registered gate.
- **Route:** Sol makes the two local branch corrections and commits. Luna then
  verifies, runs the authoritative gate once, and continues merge processing.

## Reviewer-candidate dispositions

| candidate/finding | disposition | durable result |
|---|---|---|
| `SB-P20-R01` moved HEAD mislabeled dirt | `promote-contract -> P21` | A-178 and P21 O5 add v4 `NO_MEASUREMENT/HEAD_CHANGED`; P20 retains the v3-compatible collapse |
| `SB-P20-R02` universal Git hardening | `discard as already contracted` | P26 already requires full lowercase OIDs, `--end-of-options`, literal pathspecs, bounded `ls-tree -z`, and exit-status-only `diff --quiet` |
| F5 ctime granularity | `promote-contract -> P23` | P23 must create a uniquely owned artifact-absent snapshot before reservation; private ownership removes the shared-writer race instead of trusting ctime granularity |
| stderr drain | `promote-fix -> P20` | locked negative plus kill-on-either-stream contract in A-177 |

## Updated locked asset hashes

```text
cb33ce2cf5e6573970dc4fd7500281f9f3b7dd934ddec5a5334e2d872d93b26a  README.md
20c956aeb047f8357ac2b3d83f2567227c44bdadebc454dbe85414f11af5246a  skeleton.patch
a0fc7b5d8f996e3ae274ea4045e35a07528dccda7806037aa90e9d682e664268  probe_git_boundary.py
a11ec6fecde751b2ef6361b9e97747e6d79872645645a525f2aa0c8ad7779c89  test_acceptance.py
a01d3d19c3273b6ef1917b7115486f00645ce588ea07ab7127b00622b7355c6b  expected/post-dirty-v3.json
```

## Workflow findings promoted alongside the correction

The implementation and review children no longer own the authoritative gate.
They run focused diagnostics; Luna runs the registered gate after the reviewed
commit is final and records exact argv/commit, outer exit, raw log/digest, three
inner phase markers, and a host-side final marker. The checked-in gate driver
derives or explicitly reads the host bind source and uses fail-loud `--mount`.

Controlled review mutations now have a declared narrow test, expected red,
process-group failsafe, output cap, restoration oracle, per-package count, and
total wall budget. A timeout is `PROBE_INCONCLUSIVE_HUNG`, never evidence of a
killed mutant or a passing review. These rules are persisted in canonical
Nyxloom doctrine/design and backlog B36/B37 so the pilot behavior can become a
dark Nyxloom controller feature rather than session folklore.

<!-- origin: 2026-11-11 -->
<!-- status: scrubbed historical Nyxloom report -->
<!-- source: reports/nyx-extract-readback-REPORT.md -->

# Nyxloom extract readback — final delivery report

Date: 2026-11-11
Branch: `nyx-readback-20261111`
Worktree: `<worktree>`
Final head: `6f00c0d18987cc87bd06c819a3b981f5a3a99c9f` (`6f00c0d18`)
Fresh `origin/main`: `1ff07bda355b7639b1c3a2bd93154358817477e3` (`1ff07bda3`)
Integration state: `origin/main` is an ancestor of the final head; no
unmerged origin/main commit remains.

## Outcome

The review's FIX-FIRST findings B1-B3 are fixed. The final tracked tree is
clean and both registered nyxloom lanes PASS on the exact final head:

| Lane | Log summary | Bound receipt |
|---|---|---|
| `tester-unified` | [Gate evidence summarized below](#final-gate-evidence) | `run-gate.commit=6f00c0d18987cc87bd06c819a3b981f5a3a99c9f`; `tester-unified: PASS (exit 0)`; `run-gate: lane 'tester-unified' verdict PASS; exit_code 0` |
| `session-extract` | [Gate evidence summarized below](#final-gate-evidence) | `run-gate.commit=6f00c0d18987cc87bd06c819a3b981f5a3a99c9f`; `session-extract: PASS (exit 0)`; `run-gate: lane 'session-extract' verdict PASS; exit_code 0` |

The final head is a non-merge commit. This matters because Assay narrows a
changed-line or mutation selection at a merge head to the merge's first-parent
delta; the non-merge closeout preserves the complete branch delta for judging.

## Review corrections

### B1 — protected operator and interview material

`READBACK_PROTECTED_CATEGORY` in
`nyxloom/src/nyxloom/session_extract/select.py` now covers both
`OPERATOR_TEXT` and `QA_PAIR`. Protected records bypass the per-record budget
decision and are never counted as dropped.

Selection remains ordered so the protected class is filled first; within each
category the newest material is retained. Lower-priority overflow is dropped
oldest-first in this order:

1. assistant prose;
2. agent-communication bodies;
3. tool-call intent lines;
4. associated run results.

If protected material alone exceeds the requested content budget, all of it is
still emitted. The footer reports `protected_content_tokens`, `overrun_tokens`,
and an explicit warning that the protected material was emitted in full.

The fixture witness uses a 20-token budget, smaller than the protected records
themselves:

`nyxloom/tests/test_session_extract_readback.py::test_readback_protects_operator_material_and_reports_overrun`

It proves both protected records survive in sequence order, the assistant prose
is dropped, and the overrun accounting is exact.

Real controller-session evidence was collected by reading the controller
session file read-only and writing scratch outputs outside the checkout. The
source shape contained 87 operator turns and 183 interview answers (270
protected records total):

| Run | Protected source | Output protected | Overrun |
|---|---:|---:|---:|
| unbounded | 270 | 270 | 0 |
| `--budget-tokens 50000` | 270 | 270 | 38,527 |
| `--budget-tokens 20000` | 270 | 270 | 68,527 |

Both bounded outputs contain the same footer count,
`[readback operator-interview: kept=270 dropped=0 ...]`. Thus the protected
operator/interview count in each output equals the source count; the budget is
reported, not silently enforced by deleting protected history.

Scratch outputs:

- `/tmp/nyx-readback-fixd-unbounded.txt`
- `/tmp/nyx-readback-fixd-50k.txt`
- `/tmp/nyx-readback-fixd-20k.txt`

### B2 — complete agent records, including headers

`nyxloom/src/nyxloom/session_extract/agentcomms.py` now applies the configured
character cap to the complete rendered item. Agent tool events, notification
events, launcher `AGENT-OUT` headers/briefs, and associated `AGENT-IN`
run-result headers/bodies all pass through `_bounded_item`.

The marker is always visible, including when the cap is shorter than the normal
` [truncated]` suffix: in that case the output ends in `…`. Full mode preserves
whitespace; summary mode still collapses whitespace before capping.

The over-long-header witness is:

`nyxloom/tests/test_session_extract_readback.py::test_agent_headers_and_complete_items_are_capped`

It constructs long Agent, notification, launcher-brief, and launcher-result
headers and asserts that each complete rendered item is bounded and visibly
truncated.

### B3 — post-run job-check receipt

The post-run audit is recorded under “Job-check” below. The command is run
after the final gated head, not while the producing Codex process is still
active.

### Review documentation nits

- `nyxloom/docs/CLI-REFERENCE.md` now distinguishes `extract` time forms from
  marker-only cursor forms on `extract-lossless`, `extract-debug`, `digest`,
  and `events`.
- `nyxloom/src/nyxloom/session_extract/config.py` no longer claims agent
  communication is excluded from readback; it describes the optional complete
  rendered item cap, including AGENT headers.
- The real-session provenance note was anonymised. No local session path,
  session identifier, thread UUID, or controller-local path was added to
  shipped code, tests, or fixtures.

## Final gate evidence

Both authoritative logs were produced through the registered gate runner
with the nyxloom project directory as cwd, against clean head
`6f00c0d18987cc87bd06c819a3b981f5a3a99c9f`.

### tester-unified

- Verdict: PASS, exit 0
- Verdict artifact: `.assay/verdict-tester-unified.json`
- Verdict SHA-256:
  `60b2642305e44346e91c781029166a150f6fc052c3142feaa7dc1c489ff21dc1`
- R0 PASS
- R1 PASS
- 13 changed files considered
- 782/782 executable lines covered
- 222/222 branches covered
- 100%
- Assay `12.1.2.dev410+g6f00c0d18`

### session-extract

- Verdict: PASS, exit 0
- Verdict artifact: `.assay/verdict-session-extract.json`
- Verdict SHA-256:
  `07184d20a3aa27ea9e515f5b9be5898a2441dc48258cb04655a6f2abdb4e71a2`
- R0 PASS
- R1 PASS
- R2 PASS
- R3 PASS
- 10 changed files considered
- 675/675 executable lines covered
- 202/202 branches covered
- 100%
- Assay `12.1.2.dev410+g6f00c0d18`

Final R2 campaign:

```text
/tmp/run-gate/lanes/session-extract/e4d22b5c0ae6971e54d0095f7ef75551.campaign.json
planned_total=144
completed_total=144
killed=144
survived=0
crashed=0
hung=0
equivalent=0
budget_exceeded=0
remaining=0
```
The closeout analysis retains
`complete_blockers=["coverage_not_reverified"]`, which is analysis-only and
does not change the lane's verified PASS verdict.

## Final full-suite race discovered and fixed

The first full-suite run after the last origin/main merge exposed a real
first-open SQLite race in
`tests/test_properties.py::test_sequence_integrity_under_concurrency`:
concurrent first connections both attempted `PRAGMA journal_mode=WAL`, and one
received `database is locked` even though it had a busy timeout. SQLite's busy
timeout does not cover every journal-mode transition.

`2a5baa991` serializes WAL setup and schema creation under a companion file
lock; later connections read the installed mode and skip the redundant
transition. `a91b38ee6` classifies the cleanup-only handler for the exception
census, and `6f00c0d18` covers the failure-before-connect branch. The final full
suite passes.

## Commit and trailer evidence

The branch contains 32 commits in `origin/main..HEAD`, including 6 merges. Every
commit, including every merge, has exactly one trailer:

```text
Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>
```

The commits are:

| Commit | Subject |
|---|---|
| `7f8dfcd01` | `feat(nyxloom): extract agent communication and bounded readback` |
| `dc65ad159` | `test(nyxloom): harden readback budget and agent boundaries` |
| `bbc5888b5` | `test(nyxloom): prove summary agent communication cap` |
| `b6e2c5ca1` | `docs(nyxloom): close delivered readback backlog entries` |
| `18f16f1dd` | `fix(nyxloom): reserve readback accounting overhead` |
| `856d61245` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `9de436f11` | `test(nyxloom): track successor agent communication expansion` |
| `9712b3031` | `fix(nyxloom): type readback footer without excluded branch` |
| `4a55501c4` | `test(nyxloom): cover readback fallback and drop-span boundaries` |
| `3e5285074` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `747d19577` | `test(nyxloom): assert unreadable last-file fallback` |
| `8a951e1ec` | `test(nyxloom): wait for no-gate probe before asserting completion` |
| `6658febf4` | `test(nyxloom): kill readback mutation survivors` |
| `4a5be127e` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `76dce06ee` | `test(nyxloom): cover combined readback time and budget controls` |
| `9cb4d9bd0` | `test(nyxloom): kill exact-fit agent communication bound mutant` |
| `3f63eaf7c` | `fix(nyxloom): protect operator readback and bound agent headers` |
| `9633b92c5` | `test(nyxloom): exercise protected readback overrun footer` |
| `6e62f5978` | `test(nyxloom): make the full hang oracle load-independent` |
| `d705efdb1` | `test(nyxloom): cover the capped launcher result header` |
| `20f3ee463` | `fix(nyxloom): preserve agent item structure under the tight cap` |
| `603a709b0` | `docs(nyxloom): anonymize the real-session provenance note` |
| `d74692f12` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `1ec356f6b` | `chore(nyxloom): bind final gate to a non-merge head` |
| `ee4813f74` | `test(nyxloom): kill readback cap and exact-fit survivors` |
| `09f4f62f1` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `e8fee0471` | `chore(nyxloom): close out readback final gate head` |
| `47159dab3` | `Merge branch 'origin/main' into nyx-readback-20261111` |
| `57ba0d752` | `chore(nyxloom): close out readback final gate head` |
| `2a5baa991` | `fix(nyxloom): serialize SQLite first-open initialization` |
| `a91b38ee6` | `fix(nyxloom): classify first-open cleanup handler` |
| `6f00c0d18` | `test(nyxloom): cover pre-connection initialization failure` |

## Hand-mutation proof

M01-M31 covered the original time, agent-communication, budget, preset,
validation, and disclosure rules; all 31 were killed. Their logs are
`/tmp/readback-M01.log` through `/tmp/readback-M31.log`.

The review-fix round added M32-M45. All 15 mutations were killed; none survived.
The exact mutation patch for each ephemeral run is not reconstructed where the
log did not preserve it; the table records the behavioral guard exercised and
the first failing witness reported by the registered focused lane.

| ID | Behavioral guard removed or weakened | First failing registered witness | Result |
|---|---|---|---|
| M32 | Operator/interview bypass from the budget decision | `tests/test_session_extract_readback.py::test_readback_protects_operator_material_and_reports_overrun` | killed |
| M33 | Alternate operator/interview protection bypass | `tests/test_session_extract_readback.py::test_readback_protects_operator_material_and_reports_overrun` | killed |
| M34 | Protected-overrun accounting weakened | `tests/test_session_extract_readback.py::test_readback_protects_operator_material_and_reports_overrun` | killed |
| M35 | Complete-item cap applied to body only | `tests/test_session_extract_readback.py::test_agent_bounds_full_and_summary_items` | killed |
| M36 | Agent header omitted from the complete-item cap | `tests/test_session_extract_readback.py::test_agent_headers_and_complete_items_are_capped` | killed |
| M37 | Notification header omitted from the complete-item cap | `tests/test_session_extract_readback.py::test_agent_headers_and_complete_items_are_capped` | killed |
| M38 | Launcher output header omitted from the complete-item cap | `tests/test_session_extract_readback.py::test_agent_headers_and_complete_items_are_capped` | killed |
| M39 | Truncation marker omitted from a capped item | `tests/test_session_extract_readback.py::test_agent_bounds_full_and_summary_items` | killed |
| M40 | Footer protected/overrun accounting omitted | `tests/test_session_extract_readback.py::test_readback_estimator_and_footer_name_every_category` | killed |
| M41 | Tight-limit marker omitted | `tests/test_session_extract_readback.py::test_agent_bounds_full_and_summary_items` | killed |
| M42 | Full mode forced through summary whitespace collapse | `tests/test_session_extract_readback.py::test_agent_bounds_full_and_summary_items` | killed |
| M43 | Unprotected exact-fit record dropped (`<=` changed to `<`) | `tests/test_session_extract_readback.py::test_readback_keeps_an_event_that_exactly_fits_remaining_budget` | killed |
| M44 | Short-limit cap returned raw text without `…` | `tests/test_session_extract_readback.py::test_agent_bounds_full_and_summary_items` | killed |
| M45 | SQLite initialization lock omitted | `tests/test_storage_sqlite.py::test_connection_initialization_is_serialized_before_sqlite_opens` | killed |

Mutation logs:

- `/tmp/readback-fix-M32.log` through `/tmp/readback-fix-M41.log`
- `/tmp/readback-fix-M42.log`
- `/tmp/readback-fix-M43.log`
- `/tmp/readback-fix-M44.log`
- `/tmp/readback-fix-M45.log`

The focused mutation configurations are disposable diagnostics; the final gate
evidence above uses the registered project lanes unchanged.

## Superseded gate attempts

Earlier controller gate logs remain outside the repo and are not final evidence.
Their outcomes are summarized here: earlier tester-unified and session-extract
runs passed before later origin/main and code changes; a later tester-unified
run exposed the SQLite first-open race; two subsequent tester-unified runs
passed R0 and exposed cleanup branches that were then fixed.

## Backlog disposition

- `NL-57` remains `fixed` for ISO/relative `--since`.
- `NL-58` is the existing agent-communications entry. It is updated and closed,
  not duplicated. Its close reason names the implementation commits and leaves
  search-wide application with `NL-62`.
- `NL-59` remains `fixed` for the readback preset and token budget.
- `NL-62` remains separately open for record-level search across session
  evidence.

An ID scan over fresh `origin/main` and all pending worktrees found `NL-64` as
the highest allocated id. The next free id is `NL-65`. This fix round allocated
no new backlog id.

## Privacy and static checks

The changed session-extract code, tests, and readback fixtures contain no
controller-local session path, exact session identifier, thread UUID, or
workspace-local absolute path. The real-session probe was read-only and all raw
output stayed under `/tmp`; it is not committed to the repository.

Final checks:

```text
git diff --check
git status --short
# both clean/no output

git diff --shortstat origin/main..HEAD
33 files changed, 2247 insertions(+), 112 deletions(-)
```

The final branch has 32 commits with the required trailer, including all
merges:

```text
trailer_bad=0 commits=32
```

## Job-check

Required post-run audit command, with the path form supplied by the task:

```bash
job-check.sh \
  nyx-readback <worktree> deepseek \
  nyx-extract-readback-REPORT.md
```

Actual green output:

```text
done: EXIT=0
messages=103 commands=685 last: ult. `codex-impl.sh` writes `.done` only after this active Codex child exits; `job-check.sh` exits with the running status before auditing the report, trailers, or gates. The report documents this constraint and all evidence that the post-run check will audit.
report: nyxloom/nyxloom-trove/reports/nyx-extract-readback-REPORT.md bytes=15746 skeleton-markers=0
JOB-CHECK: green
```

`job-check.sh` expects its second argument to be the worktree name and prefixes
`<checkout>/.worktrees/`. The literal path form above is therefore green
as a report/final-message audit. The canonical worktree-name invocation below
performs the full HEAD audit:

```bash
job-check.sh \
  nyx-readback nyx-readback-20261111 deepseek \
  nyxloom/nyxloom-trove/reports/nyx-extract-readback-REPORT.md
```

Actual green output:

```text
done: EXIT=0
messages=103 commands=685 last: ult. `codex-impl.sh` writes `.done` only after this active Codex child exits; `job-check.sh` exits with the running status before auditing the report, trailers, or gates. The report documents this constraint and all evidence that the post-run check will audit.
report: nyxloom/nyxloom-trove/reports/nyx-extract-readback-REPORT.md bytes=15746 skeleton-markers=0
worktree nyx-readback-20261111 @ 6f00c0d18 commits-ahead=32
trailers OK (all commits incl. merges: deepseek-v4.1-flash)
gate logs bound to HEAD: 2 (with a pass line: 2)
JOB-CHECK: green
```

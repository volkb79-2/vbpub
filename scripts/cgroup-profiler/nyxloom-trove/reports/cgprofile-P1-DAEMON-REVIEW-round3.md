# RG-55 P1 daemon adversarial review — round 3

ACCEPT

## Blind finding preserved before repair

Initial clean candidate: `3f3777662966baeedeb8c903d10fcb6235143ba1` on
`rg55-p1-private-ns`; base and merge base:
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`. The full initial diff was
captured before editing at `/tmp/rg55-p1-sol-blind.diff` (SHA-256
`cc2318916bb12e09a6842b2be1bfdd42ac32b5c1ed97ea2018cc1fb949c5ee5f`).
The code and docs diff was reviewed before the implementer LOG/REPORT/briefs.

### B1 — explicit empty build version silently becomes a development version

Severity: release identity / fail-closed contract. In `lib/version.py:73-81`,
`env.get(VERSION_ENV, "").strip()` collapses an absent version and an
explicitly supplied empty or whitespace-only version. On an untagged tree,
`resolve_build_version(..., require_release_tag=False,
environ={"CGPROFILE_VERSION":"   "})` returns `0.0.0-dev`. RW-311 requires an
explicit empty or malformed version to refuse, and the estate default rule
forbids replacing a supplied but invalid fact with a fallback. A local build
can therefore label an accidentally empty release input as a valid development
image without failing. Reproduced against the candidate with a direct Python
call; the push path refuses only because it lacks a tag, with the wrong
absence diagnosis. Prescription: distinguish key absence from key presence,
validate every present value, and add behavioral tests for both build and
publish modes while retaining absent-input development behavior.

## Repair and disposition

B1 is closed by `158488ccbae418bba6b4022e8c9295742a6f9885`. The resolver
now distinguishes an absent `CGPROFILE_VERSION` from a present value and
validates the latter before using it. Four parameterized cases cover empty and
whitespace values in local-build and publish modes; the absent-input
development identity and exact-tag precedence retain their existing tests.
Focused `tests/test_version.py tests/test_build_push.py` passed 28 tests.
This is the only source repair made in this review. The initial blind finding
above is retained verbatim as the pre-repair record.

The reviewed branch is still based on current `main`
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`. It was clean at dispatch,
after the repair commit, and when each short gate began. `git diff --check`
was empty. The final report-only commit hash is returned to the controller;
the exact-tip gate receipts are in `.run-gate/history.json`.

## Independent code and contract review

| Surface | Evidence and judgment |
| --- | --- |
| Daemon write boundary | Inspected `lib/serve.py` session-path guard, `lib/damon.py` admin-root guard, shell/subprocess sites, and `cgprofile.py` parsing. `serve --cap 1` exited 2 with `unrecognized arguments`; no TempCaps path is exposed through `serve` or `ctl`. Persistent data and sysfs writes are confined to session storage and DAMON admin; the Unix control socket has its separately configured bind/unlink path. |
| Compose/image | Rendered the candidate Jinja template/defaults and checked `docker compose config --format json`: singleton, restart policy, authored interactive cgroup parent, private cgroup and default private PID namespace, `network_mode: none`, no Docker socket, read-only explicit host `/proc` and cgroup v2 mounts, separate DAMON mount and configured memory limits. The image contains the CLI wrapper and revision label. `ciu render/check` selected zero stacks from this isolated root, so the manual render is the Compose evidence; it does not certify a CIU-managed daemon launch. |
| CLI/contract | Inspected verb dispatch, one-document JSON socket/CLI handling, exit 0/2/3 response validation, required `contract: 1`, start reuse only with a non-null token, idempotent stop, `too-many-sessions` and target errors. Real `ctl version --json` returned one valid document. Golden socket and malformed-response tests passed in R0/R1; other verbs were not all exercised live. |
| Summary | Inspected `lib/summary.py`: scope-specific last-successful absolute counters versus start deltas, nearest-rank percentiles, memory source/baseline, null propagation, per-pair monotonic CPU rates, PSI microsecond conversion and limit drift. The shipped byte-identity test compares the frozen contract fixtures. Eight independent temporary arithmetic mutants below were killed. No mutation was committed. |
| Subtree/helper PID | Inspected namespace/path derivation, PID namespace inode, `NSpid`, proc start-time and selected-subpath checks, vanished/reused PID refusal, task-children and ppid-map fallback, target union/`targets_seen`. Fixture positive and sibling-subpath refusal cases both passed; the live private-namespace helper probe below observed one mapped PID. A descendant reparented to PID 1 can cease to be in the rooted subtree; the union exposes previously seen targets rather than promising eternal discovery. |
| DAMON/lifecycle | Inspected pool baseline/owned-index separation, no foreign-slot teardown, no-token recommit guard, SIGTERM/session cleanup, orphan recovery and retention's live-session exclusion. The fake-sysfs and socket suite covers two owned slots, release/reuse/shrink, unavailable reasons and restart. Live DAMON allocation could not be proved on this host because the kernel rejected commit; see evidence gap. |
| Reports/docs/release wiring | `ctl report` generated a nonempty HTML artifact in the live daemon. README, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md` and ATTACH-GUIDE describe daemon mode and limitations. `tests/test_docs.py` parses adopter examples through the shipped loader, checks closed vocabulary and resolves anchors. Reviewed Dockerfile/build scripts for runtime network fetches, version labels, `cmru.toml` and gate lanes. The installed `cmru` CLI has no `status --project` command (attempt exited 2), so that handoff check has no valid read-only receipt here. |

The round-1 blockers B1–B8 (foreign DAMON ownership, phantom DAMON series,
no-token deduplication, missing sample zero, fixed-interval `cores_max`,
malformed interval classification, accepting malformed ctl replies, and absent
consumer guide) have corresponding implementation and behavioral tests in
the candidate; the present review found no reintroduction. The focused
`summary.py:214` partial-limit-read oracle is present. Historical claimed
equivalents at `serve.py:626,704` and `summary.py:173,177` were reviewed as
prior-tree dispositions only; line identity and assay outcomes cannot be
transferred to this tip. CP-4..CP-7 remain recorded follow-ups, including
real event emission, DAMON report-series integration and effective manifest
limits. They must not be advertised as completed daemon outputs.

### Temporary summary mutants (all restored, no tree change)

Each row is one isolated source replacement followed by
`nice -n 19 ionice -c 3 python -m pytest -q -x tests/test_summary.py`.
The named test failed; the original file was restored in `finally` after
each run and `git status` remained clean.

| Mutation | First failing behavioral oracle |
| --- | --- |
| `_max_over`: max to min | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |
| nearest rank: one index too high | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |
| limit drift: count equal rather than changed pairs | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |
| container absolute counter replaced by start delta | `TestGoldenReproduction::test_container_scope_matches_summary_container_v1` |
| shared memory peak: last value instead of maximum | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |
| reverse baseline subtraction | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |
| `cores_max`: fixed configured interval | `TestAbsentInputsStayNull::test_cores_max_uses_each_positive_sample_timestamp_delta` |
| PSI: divide by 1e3 instead of 1e6 | `TestGoldenReproduction::test_container_shared_scope_matches_summary_v1` |

## Live acceptance on reviewer-owned containers

Before launches, memory PSI `full avg10` was below 5. The parent verifier
proved `dev-gates.slice` loaded at `/etc/systemd/system/dev-gates.slice` and
instantiated in the host cgroup tree. The `cgprofile:local` image was rebuilt
from repair commit `158488cc`; its OCI revision label matched. The daemon,
ephemeral workload, shared workload and helper workload each had a unique
`cgprofile-sol-*` name, `dev-gates.slice` parent, private cgroup namespace,
Docker's default private PID namespace, network disabled and
`NanoCpus=3000000000` after immediate verified `docker update --cpus=3`.
The daemon additionally had explicit read-only host `/proc` and cgroup mounts
and the separate DAMON mount. All exact reviewer-owned containers and scratch
were removed in `finally`; no pre-existing container or network was changed.

- Daemon `ctl version --json` exited 0 and returned one document:
  `contract: 1`, `cgprofile: 0.0.0-dev`, `damon: available`, default `on`,
  `sessions_live: 0`, `max_sessions: 16`. The development identity is correct
  for this untagged local build; `available` here denotes an exposed DAMON
  interface, not proof that this kernel will accept a session.
- Ephemeral 100 MiB workload, session `s-20260925T163953Z-e5ca`:
  `memory.peak_bytes=120291328`, source `memory.peak`, CPU `0.155` seconds,
  `targets_seen=1`. `ctl report` exited 0 and wrote nonempty `report.html`.
  Requested DAMON degraded explicitly to `unavailable:OSError: [Errno 22]
  Invalid argument` at kernel `kdamond_commit`; collection still completed.
- Shared 80 MiB workload, session `s-20260925T163955Z-7831`:
  peak `88678400`, baseline `1089536`, over-baseline `87588864` bytes,
  source `sampled-max`, CPU `0.139` seconds, `targets_seen=1`.
  Both sessions appeared live together in `ctl status`. DAMON was
  `unavailable:no pids to monitor yet` at start because the token owner
  appeared after session creation; this is the recorded S3 limitation.
- In a private-PID/cgroup `tester-unified:local` workload, helper
  `targets --mode helper --target pid:7` returned `pid-7 [pid]` and the
  selected container cgroup. `attach --mode helper --target pid:7
  --duration 3 --no-report` exited 0, created a `kind=pid` manifest, mapped
  PID `1143968`, and each of five sample records contained only that PID in
  `proc`. A `--damon` collection degraded with the explicit CLI message
  `DAMON requested but unavailable here — continuing without it`.
  The fixture tests independently proved same-PID handoff to DAMON when
  available and refusal of a matching process outside the selected subpath.

The two live daemon sessions did not provide two running kdamond indices,
hot-byte counts, or a live stop-0-while-1-runs sysfs observation; DAMON was
unavailable for the explicit reasons above. The helper live probe likewise
does not establish a live DAMON PID handoff. These are evidence limits, not
claims of passed DAMON observations. The fake-sysfs/fixture oracles provide
the available behavioral proof. The `report.html` existence/size was checked;
the full interactive behavior was not independently exercised in a browser.

### Nonblocking observations and claims not independently verified

- **S1:** A token-bearing shared session started before its owner appears
  retains `unavailable:no pids to monitor yet` for DAMON. The session still
  collects the target's cgroup/proc metrics; this is the earlier review's
  disclosed S3 limitation, reproduced live here. A follow-up could retry
  DAMON acquisition when discovery first finds the owner.
- **S2:** This kernel returned `EINVAL` at DAMON commit. Fake-sysfs tests
  exercise slot ownership, release and reuse, but live hot-byte output and
  two independent live kdamonds could not be verified here.
- **S3:** The helper's live `--damon` path reported unavailable. The fixture
  proves PID handoff; live DAMON PID handoff is unverified. The live HTML
  artifact was checked for existence and size, while its interactive
  behavior was not independently exercised in a browser.
- **S4:** `ciu render/check` selected no stack in this isolated root, and
  the installed `cmru` 5.4.2 CLI refused `status --project`. The manual
  candidate Compose render and tests support the integration claims, but a
  CIU-managed launch and current read-only CMRU status are not certified by
  this review. No shared-state change was made to force either probe.

## Exact-tree short gates and release boundary

On clean code commit `158488ccbae418bba6b4022e8c9295742a6f9885`:

- `./run-gate.py r0-r1`: PASS/exit 0, 1,381 passed, four fork warnings,
  5,020/5,020 statements and 1,732/1,732 branches. Host PSI
  `full avg10=0.34` before launch; gate placement and `NanoCpus=3000000000`
  were verified. The daemon was down, so profiling used coarse rusage.
- `./run-gate.py r3`: PASS/exit 0, seven canaries rejected, zero survived.
  Host PSI `full avg10=0.68`; run-gate used `dev-gates.slice` and a 3-CPU cap.
- `.run-gate/history.json` independently records both outcomes, exact commit,
  `dirty=false`, and `exit_code=0`. The report-only final tip is rejudged
  after this report commit; its history entries are the final tip receipts.

The previous exact candidate `3f377766` also had clean R0/R1 and R3 PASS
receipts, verified in-tree before repair. The old R2 on `51198f2e` was FAIL
with ten oracle gaps; a later attempted R2 on `1908316b` ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG` (80 killed, one hung). Neither is a passing
mutation receipt for the present tree. `.assay/verdict-r2.json` is absent
here. No mutation campaign or full gate was launched during this review.

**Verdict: ACCEPT for provisional merge under RW-329.** B1 is repaired,
the short gates pass on the reviewed source and will be rerun on the final
report-only tip. **Release readiness: NO.** The controller still owes a
complete exact-tree R2 and full gate in its separate attached CIU worktree,
with any fixes backported and all changed trees rejudged. This review does
not convert incomplete R2 evidence into a product result.

## Route evidence

The controller's launch statement supplied the exact route: fresh Codex
process with `CODEX_HOME=/home/vscode/.codex2`, `--model gpt-6-sol`, and
`model_reasoning_effort="xhigh"`. Runtime route introspection was unavailable
in this session; this is controller launch evidence, not self-inspection. No
actual CLI route/configuration error was reported.

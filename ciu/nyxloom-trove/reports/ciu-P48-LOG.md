# ciu-P48 — LOG

Package: `nyxloom-trove/handoffs/ciu-P48-ciu87-devcontainer-network-leak.md`
— CIU-87, both proposed fixes. Harness-provided worktree
`/workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86`, branch
`worktree-agent-a3b063efd8bd93e86`, based on vbpub main `ce08d077`
(`docs(assay): Wave C controller log -- session checkpoint…`), which carries
ciu at the post-`7.9.0`/ciu-P46 revision the handoff names as the input.

One code commit (`0fc4efc4`) plus this LOG/REPORT pair. Code, tests, SPEC and
CHANGES landed together in that one commit, per
`KNOWN_ISSUES_TODO_BACKLOG.md`'s own "FIXED means code, behavioral tests, SPEC
and user documentation landed together" rule; the CIU-87 backlog entry was
updated in the same commit rather than split out, because its resolution block
is the load-bearing record of the measured numbers and belongs with them.

`ciu-P47` (identity-overlay file split / `write_generated_facts` /
`ciu.global.instance.toml.j2` rename) was left strictly alone, as instructed.
It has in fact merged to `main` since this package was carved (`42c018ea`,
claiming `CHANGES.md`'s `[7.10.0]`); this branch deliberately stays on the
carved input revision rather than rebasing onto it, so the reviewer's diff is
this package and nothing else. The `CHANGES.md` section is therefore written
as `[7.10.1] - UNRELEASED` with an explicit note to the releaser.

---

## Step 1 — measurement before design: what actually leaks

The handoff was explicit that both its own file list and the backlog entry's
were suspect and that I should do my own accounting. Rather than grep for
call-site patterns (which is what produced both of the wrong lists), I
instrumented the two real side-effect functions —
`_ensure_network_exists` and `_connect_devcontainer_to_network` — with a
throwaway append to `/tmp/ciu87-trace.log` carrying the network name and
`PYTEST_CURRENT_TEST`, and ran the whole suite once. That attributes every
real daemon call to the exact test that made it.

Baseline: 31 networks. Suite: 3523 passed in 23.9s. Trace, complete:

```
CREATE  test-repo-be42eb-network  test_ciu_test_repo.py::test_deploy_render_all_configs_respects_phases
CREATE  test-repo-1c9480-network  test_ciu_test_repo.py::test_bootstrap_workspace_env_generates_env_file
CREATE  repo-f0257f-network       test_ciu_identity_cutover_ciu75.py::test_the_regenerated_legacy_export_cannot_change_identity
CREATE  repo-b28954-network       test_ciu_identity_cutover_ciu75.py::test_step_1_regeneration_keeps_stdout_clean_for_json_consumers
CREATE  failed-net                test_ciu_workspace_dev_remaining_boundaries.py::…      (mocked subprocess)
CREATE  ciu-network               test_ciu_workspace_env_branch106.py::… (x2)            (mocked subprocess)
CONNECT test-repo-1c9480-network  test_ciu_test_repo.py::test_bootstrap_workspace_env_generates_env_file
CONNECT repo-f0257f-network       test_ciu_identity_cutover_ciu75.py::test_the_regenerated_legacy_export_cannot_change_identity
CONNECT ciu-internal              test_ciu_workspace_env_deep_remaining.py::… (x2)       (mocked subprocess)
```

Findings, all of which contradict one or both of the lists I was given:

- **Two files leak, not eight.** `test_ciu_test_repo.py` and
  `test_ciu_identity_cutover_ciu75.py`. Most of the "7 siblings" matching the
  `test-repo` fixture-name pattern monkeypatch `ensure_workspace_network` and
  never reach the daemon at all.
- **The second prefix is not where the handoff placed it.** `repo-*` comes
  from `test_ciu_identity_cutover_ciu75.py`'s `tmp_path / "repo"` roots.
  `test_ciu_worktree.py` / `test_ciu_worktree_lease.py` build
  `f"repo-{instance_id}-network"` name *strings* for assertions but create no
  networks — a grep for the literal prefix cannot tell the two apart.
- **Prefix matching is the wrong tool in general.** The name is
  `f"{physical_root.name.lower()}-{sha256(physical_root)[:6]}-network"`, so
  the prefix is just whatever a test happened to call its tmp directory
  (`tr-`, `tr2-`, `e2e-` all appear on this host from elsewhere). The fix and
  every oracle below are name-agnostic and use whole-`docker network ls`
  name-set diffs.
- **Only the JOINED ones leak.** `test-repo-be42eb` and `repo-b28954` were
  created and then correctly removed within the run; the two that got a
  `CONNECT` survived. That is exactly the mechanism CIU-87 identified —
  `ciu clean` refuses to remove a network a container is still joined to —
  confirmed live: `docker network inspect` on both survivors showed exactly
  one attached container, `dstdns-devcontainer-vb`.

Net: 31 → 33. I released those two surgically (disconnect, then rm) and
reverted the instrumentation before writing any real code.

## Step 2 — design decisions the handoff left open

**"Is there an existing mocked-vs-real Docker test convention to reuse?"** —
the handoff asked me to verify this claim rather than design around it.
**It does not hold up.** There is no fixture, marker or naming convention.
What exists is ad-hoc, per-test `monkeypatch.setattr(engine,
"ensure_workspace_network", …)` in ~25 files — a mocking *habit*, not a
convention with a seam to hook. So I built the smallest new signal that does
the job, as instructed: `CIU_TEST_SUITE`, exact-match `"1"`.

**Where to put the gate.** Two candidate sites: the public
`ensure_workspace_network`, or the two private functions that actually shell
out. I put it in the **private** ones. The public function is a two-line
delegator; the daemon side effects are in the privates, and several existing
boundary tests call the privates directly — gating only the public entry would
have left those able to reach a real daemon.

**Order relative to the `ENV_TYPE` check.** `ENV_TYPE` first, always. It is
the production S1.9 contract; the new gate is the narrower "…and this is ciu's
own suite" condition layered on top. Reversing them would let the gate
short-circuit `test_native_network_setup_never_attaches_cockpit`, which exists
to prove the S1.9 behavior itself.

**Order relative to the empty-name check.** The `DOCKER_NETWORK_INTERNAL`
refusal in `_ensure_network_exists` stays **above** the gate. The gate
suppresses a side effect; it must not suppress a validation, or a test could
pass on a silently unnamed workspace. Pinned by
`test_gate_keeps_the_identity_contract_it_does_not_suppress`.

**Silent, not logged.** `_log_info` writes to **stdout**, and one of the very
tests in the blast radius is
`test_step_1_regeneration_keeps_stdout_clean_for_json_consumers` (S3.1c). A
narrating skip branch would risk breaking JSON-consumer contracts to tell ciu's
own test suite something no operator will ever read.

**How conftest raises the gate.** Both at module import (`os.environ[…] = "1"`)
and per test (`monkeypatch.setenv`). Neither alone is sufficient: a
fixture-only signal does not exist during collection and does not survive into
a `ciu` subprocess a test spawns, and an import-time-only signal is not
restored after a test that changes it. Both halves are pinned
(`test_gate_reaches_a_spawned_ciu_subprocess`,
`test_the_suite_declares_the_gate_for_every_test`).

**How the teardown net stays surgical.** The requirement is "clean up only
what THIS test created" on a shared host. The tracker probes existence
*before* and *after* the wrapped call and registers a network only when that
call is what brought it into being; a network that was already there belongs to
whoever made it. Networks merely *joined* are disconnected but never removed.
All its docker calls go through `subprocess.run` **captured at conftest import
time**, before any test can monkeypatch it — otherwise teardown would run
through a test's fake (and, worse, append to the `calls` list several boundary
tests assert on exactly).

**Cost control.** Both wrappers are pass-throughs while the gate is active,
because the product then cannot reach the daemon at all. So the
before/after existence probes are paid only by a test that deliberately opted
out — zero extra docker calls in a normal run.

## Step 3 — the opt-out

Running the four boundary files after adding the gate failed exactly 9 tests,
all of them tests whose actual subject IS the gated code. They opt out via a
named `real_network_side_effects` fixture (which deletes the variable and
depends explicitly on `_ciu_test_suite_gate`, so the ordering is stated rather
than inherited from pytest's autouse-first heuristic). The teardown net stays
armed for all of them, which is what the handoff required of an opted-out test.

- `test_ciu_workspace_env_deep_remaining.py` — whole
  `TestDevcontainerNetworkAttachment` class (5 tests), via a class-level
  `usefixtures` mark
- `test_ciu_workspace_env_deeper2.py` — 2 tests
- `test_ciu_workspace_env_branch106.py` — 2 tests
- `test_ciu_workspace_dev_remaining_boundaries.py` — 2 tests

## Step 4 — the oracle, and the trap in it

First attempt at the twice-in-a-row oracle came back 33 → 33 → 33, zero delta.
It looked like a pass. **It was not evidence of anything.** The controlled
wrong-implementation run (both fixes off) also came back 33 → 33 — which is
impossible if the fix is what prevents the leak.

Re-instrumenting under the control condition showed `CREATE` firing repeatedly
for the *same* network name and `_connect_devcontainer_to_network` never being
entered at all — i.e. `_ensure_network_exists` was raising. A direct probe
gave the reason:

```
$ docker network create ciu-p48-pool-probe
Error response from daemon: all predefined address pools have been fully subnetted
```

**The host's Docker address pool was fully exhausted** — the exact P152-class
blocker CIU-87 was filed from, reached during my own work. Nothing could leak
because nothing could be created. Every measurement taken in that state was
worthless, including the "clean" ones.

Cleanup was required before the oracle could mean anything. 17 networks
(12 `repo-*`, 5 `test-repo-*`) were inspected individually first — every one
held **zero** ciu-managed workload containers, only `dstdns-devcontainer-vb`
itself — and then disconnected and removed, the same
no-op-from-any-workload's-perspective cleanup dstdns applied at filing time.
Pool healthy again (16 networks), `docker network create` working. All four
oracle rows in the REPORT were then re-run from scratch on that healthy pool.

The true pre-fix leak rate is **+6 networks per run**, not the +2 the
exhausted-pool measurement suggested and not the 21-in-45-minutes the original
filing recorded.

**Lesson worth carrying:** an absence-of-effect oracle on a shared daemon must
verify the daemon can still produce the effect. A "0 leaked" reading is
indistinguishable from "the daemon refused every create" unless you check.
The controlled wrong implementation is what caught it — its row is not
ceremony.

## Step 5 — gate and close-out

`./run-gate.py ciu --worktree /workspaces/vbpub/.claude/worktrees/agent-a3b063efd8bd93e86`
at `0fc4efc4`: **PASS**, R0 and R1 both, 100% changed-line coverage with
branch coverage required and 4/4 branches taken. Verdict read in a separate
step from the run, off the redirected log and the verdict artifact — never a
piped tail. Verbatim in the REPORT.

The gate itself leaks nothing: its container mounts no Docker socket, so
`_docker_available()` is False inside it. Confirmed by name-set diff across
the gate run (only one new network appeared, `nyxloom-p49-…`, belonging to
another agent's concurrent worktree).

---

# ciu-P48 — LOG addendum: review-fix pass (round 1)

Fresh adversarial reviewer returned **ACCEPT-conditional**: mechanism, oracle,
file-source correction, all 8 judgment calls and the merge-onto-current-main
story confirmed; a 12-mutation sweep caught 11/12. Two real blockers and four
accuracy items came back. All addressed here, same branch.

## B1 — `wrap_connect` disconnected networks it never joined

**Real bug, and the worst possible one for this package to ship**: the fixture
built to stop a test from touching a co-tenant's Docker state was itself
touching it.

`NetworkSideEffectTracker.wrap_ensure` was observation-gated from the start —
probe existence before, probe after, register only if the network appeared.
`wrap_connect` was not: it appended every name the product was *asked* to
connect, in a bare `finally`. `release()` then handed each one to the **real**
daemon through the import-time-captured `subprocess.run`. So a boundary test
that mocked `subprocess.run` entirely, touched no daemon, and merely *named* a
network still produced a live `docker network disconnect -f` at teardown. The
reviewer proved it end to end: they created a real network, attached the
cockpit, ran a boundary-shaped test naming it, and watched teardown detach the
cockpit from it.

Nothing had gone wrong in practice only because the fabricated names in those
tests (`ciu-internal`, `native-net`, `ciu-network`, `failed-net`) happen not
to collide with anything live. That is luck, not a contract.

**Why I missed it.** I reasoned about `wrap_ensure`'s rule carefully and then
wrote `wrap_connect` as "the same thing for joins" without re-deriving it. The
asymmetry is invisible unless you ask, per wrapper, *what observation licenses
this entry?* — for creation I had an answer; for membership I had none, and
did not notice the gap because both wrappers looked structurally alike.

**Fix.** Gave the membership half the identical discipline, via a new
`network_has_container(name, container)` predicate: probe attachment before,
probe after, register only if the membership appeared across the call. Both a
mocked seam (never attached → nothing registered) and a co-tenant's live
membership (attached before → nothing registered) are now excluded by
construction rather than by name-collision luck.

Two supporting corrections fell out of it:

- The cockpit name is now resolved **once, at fixture setup**, before the test
  body can rewrite `DEVCONTAINER_NAME`/`HOSTNAME`, and the same value is used
  for both the probe and the release. Previously the probe would have asked
  about whatever fictional cockpit a boundary test had set, while teardown
  disconnected the real one — a second, latent mismatch.
- The fixture now `yield`s the tracker, so a test can assert on the ledger
  teardown is about to act on. That is what makes the end-to-end regression
  test possible at all.

**Pinned by 3 new tests**, one of them the reviewer's reproduction inverted:
`test_a_mocked_seam_join_leaves_the_teardown_ledger_empty` drives the real
autouse fixture with the gate off and the whole Docker seam mocked, and
asserts the ledger stays empty. Restoring the unconditional `finally` fails
all 3 (verified by running the mutation).

## B2 — the import-time gate assignment was pinned by nothing

The reviewer deleted `os.environ[CIU_TEST_SUITE_ENV] = "1"` from
`conftest.py` and **all 24 tests still passed**. Both tests that claimed to
cover it were hollow: the autouse fixture's `monkeypatch.setenv` writes the
same variable, so every in-process assertion is satisfied either way. This is
a textbook redundant-mechanism masking problem — I built two mechanisms
deliberately (LOG §2, "neither alone is sufficient"), then wrote tests that
could not tell them apart, and read the resulting green as coverage of both.

**Fix.** `test_conftest_raises_the_gate_at_import_not_only_per_test` runs a
**child interpreter** that pops `CIU_TEST_SUITE`, puts `tests/` on
`sys.path`, imports `conftest`, and prints the variable back. No fixture runs
there, so nothing can mask the deletion. Verified both directions: with the
line deleted it fails, **and it is the only test that fails**; restored, the
suite is green.

Both misleading docstrings were rewritten to state what each test actually
pins and, explicitly, what it does *not* — each now names the other test that
covers the half it cannot see.

## Accuracy items

**A1 — `+6` was wrong; the real number is `+4`.** Re-ran the both-fixes-OFF
control on a verified-healthy pool: **18 → 22**, leaking `repo-48473e`,
`repo-e3e94f`, `test-repo-1c9480`, `test-repo-adb5a9` — 2 `repo-*` + 2
`test-repo-*`, exactly the reviewer's independent figure and exactly what the
Step-1 attribution predicts (4 creates, 2 joined, 2 reaped by the test's own
`ciu clean`).

The original `+6` came from a control window that overlapped a **concurrent
co-tenant's own ciu suite run**, whose networks share the same `repo-*`
generator and were therefore indistinguishable in a name-set diff. Corrected
everywhere, with the failure mode recorded: *a name-set diff does not isolate
you from a co-tenant when the co-tenant's names come from the same generator —
a leak-rate measurement on a shared daemon needs a quiet window too.* During
this very pass the host again gained a network from elsewhere mid-sequence
(17 → 18 between two of my own steps), so this is not hypothetical.

Also flagged the Step-1 trace's provenance in the backlog: it was collected
while the pool was exhausted, the same state I later declared worthless.
Its *attribution* (which test made which call) is unaffected and the two-file
conclusion was independently reproduced three times by the reviewer, but its
*counts* are not leak counts, and the entry now says so rather than presenting
it as clean evidence.

**A2 — the "co-tenant's networks" claim was wrong, and they were mine.**
`repo-4176ba-network` and `repo-d060db-network`, created 0.5 s apart — the
`test_ciu_identity_cutover_ciu75.py` pair signature — both holding only
`dstdns-devcontainer-vb`. Residue from one of my own control runs that I did
not clean before writing "0 leaked". Inspected, removed, claim withdrawn in
both the REPORT and the backlog. `^(test-)?repo-` on this host is now 0.

That I wrote a *co-tenant* explanation for my own residue, in the same report
where I congratulated the fixture for leaving co-tenant networks alone, is the
uncomfortable part: the story was self-flattering and I did not check it.

**A3 — "no existing convention" was too strong.** `CIU_SKIP_DOOD_PREFLIGHT`
(`engine.py:897-899`) is documented in-source as "for tests", uses the same
exact `== "1"` match, and is already listed in `docs/SPEC.md:1911` with
`CIU_ADOPT_LEGACY_PROJECT` and `CIU_SSH_INSECURE_TOFU`. `CIU_TEST_SUITE` is
the third member of that family, not a novel mechanism. There was also a
hookable seam I missed: `test_spec_contracts.py:111-118`, a file-level autouse
fixture no-opping `ensure_workspace_network` — in-process only, so it would
not have covered the `ciu`-subprocess paths, which leaves the choice right for
a different reason than the one I gave. My grep was for
`monkeypatch`/`conftest` fixture *shapes*; the precedent was an env-var read
in product code, which that search could not have found. Cited in the REPORT
and cross-referenced from S2.8a so the next person does not re-derive it.

**A4 — MD022.** Blank line added before `## Compact resolved index`.

## Re-verification

Every oracle row re-measured after a `docker network create`/`rm` pool probe,
per the lesson from the first pass. Suite 3547 → 3551 tests (4 added: 3 for
B1, 1 for B2). Numbers and the gate verdict in the REPORT addendum.

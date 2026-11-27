<!-- Evidence scrub 2026-11-16: environment-specific host identities, host addresses, controller paths and per-run temporary paths are deliberately not recorded here; the lane name, verdict and diagnostic text are quoted as observed. -->
# damon-analysis — known issues, TODO, and backlog

Created 2026-11-16, when the project first gained a backlog: an R2 lane failure
measured on a runner host had no owning ledger and was at risk of living only in
a controller report. IDs are `DA-NN`, allocated sequentially in this file (no
other allocator exists).

Normative behaviour belongs in [`DAMON-GUIDE.md`](DAMON-GUIDE.md) and
[`SCRIPTS.md`](SCRIPTS.md); the gate lanes are declared in
[`run-gate.toml`](run-gate.toml) and the R2 judge policy in
[`assay.toml`](assay.toml). A FIXED entry means code, tests and documentation
landed together.

This is a scripts project: it has no wheel, no release and no version metadata
(`SCRIPTS.md`, "Version compatibility scope"), so an entry here closes on a
registered lane result, not on a release.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| DA-01 | The `r2` lane fails `ERROR/EXEC_FAILED` on a runner host: its baseline pytest command ends with a leaked descendant, and the leaked process is not identified | Should-fix | OPEN 2026-11-16 |
| DA-02 | `SysfsInterface` never reads `sz_filter_passed`/`sz_ops_filter_passed`, so its physical mode reports the machine's hot set as every unit's hot set, and `DAMON-GUIDE.md` files `young` under the core layer while the kernel takes it as an ops filter | Should-fix | OPEN 2026-11-25 |

---

## DA-01 — the `r2` lane's baseline pytest command ends with a leaked descendant on a runner host, and the lane cannot say which process it was

**Status:** OPEN (filed 2026-11-16); Should-fix for the project's only
mutation lane. Source: the remote gate worker report of 2026-11-16
(`sonnet-v1001-gateworker-REPORT.md`, final row: `scripts/damon-analysis:r2`
`fails in 53 s on v1001 (EXEC_FAILED, baseline pytest, "leaked process outlived
the command")`), recorded again in the same day's concurrency section
("a finding for the damon-analysis/assay owners, not diagnosed"). The failure
was NOT reproduced on a controller host, and no host was touched to file this.

**Observed.** On the runner host the `r2` lane started its baseline, ran for
about 53 s, and ended `ERROR/EXEC_FAILED` without reaching any candidate:
assay's process-tree guard refused the baseline command because a descendant
process was still alive when the command exited, and assay's own diagnostic
line named it only as `assay: leaked process outlived the command: ...`
(assay B233's bounded identity list). The lane's own summary therefore reports
an infrastructure refusal, not a mutation result, and no R2 evidence exists for
this project on that host. The same lane passed its `r0-r1` sibling's
environment in the same report set, so the difference between the two lanes
matters: `r2` runs assay from the worktree source with
`PYTHONPATH={worktree}/assay/src:{worktree}/libraries/cowtree/src` and its
`assay.toml` baseline argv is
`/opt/tester-venv/bin/python3 -m pytest tests -q` (`scripts/damon-analysis/assay.toml`
`[lanes.r2].argv`), while `r0-r1` runs `$TESTER_VENV/bin/python3 -m pytest`
directly (`scripts/damon-analysis/run-gate.toml` `[lanes.r0-r1]`).

**Why it matters.** This is the project's only mutation tier. A leaked
descendant in the baseline is refused by design (a false mutation result is
worse than no result — assay B145/B171), so the outcome is honest; the cost is
that the lane can never reach R2 on a host where the leak reproduces, and a
reader cannot tell from the collected evidence whether the leak is the
project's own test process or the judge's own machinery. Because the leak is
intermittent-looking (one host, one lane shape), it also invites the failure
mode the estate forbids: "it only fails on that host" is not an explanation, and
a rerun would hide it.

**Not verified — deliberately not claimed.** The cause is NOT diagnosed here.
The report says so and this entry does not guess. Code reading narrows the
candidate set to the only places this project's test suite starts a real process:

- `tests/test_analyze_process.py::TestGetPid::test_command_launches_process`
  calls `ap.get_pid('sleep 1')`, and `get_pid` runs the command through
  `subprocess.Popen(target, shell=True)` (`analyze_process.py:97-99`), so the
  returned pid is the SHELL's, not the sleep's; the test then kills only that
  pid (`os.kill(pid, 9)`) and lets the shell's child run on. Whether that child
  is still alive when the pytest command exits depends on the suite's duration
  and on how the child is reparented, so this is a candidate, not a finding.
- `tests/test_sysfs.py`'s DAMON monitor tests `sleep` 3-4 s each but skip
  themselves when `damo`/DAMON sysfs is unavailable, which the gate image's
  tester environment should make them do.
- `analyze_container.py`, `batch_report.py` and `damon_cli.py` all call
  `subprocess.run(...)` with timeouts; none of them is driven by the baseline
  suite unless a test shell is involved.

An entry that named one of those as the cause without reproducing it would be
the exact defect this estate calls out: a message asserting a conclusion the
comparison never established.

**Proposed work (diagnosis first, then a fix).** Reproduce the baseline command
alone on the same lane shape — in the declared `tester-unified` image, with the
lane's `PYTHONPATH`, its argv and the same pytest selection — and read assay's
own bounded identity list (`assay: leaked process outlived the command: pid=N
ppid=M comm=C argv=...`, assay B233) instead of treating the refusal as a
verdict. Then classify the leak by owner:

1. if the leaked descendant belongs to this project's own tests, fix the test
   so it owns and reaps what it starts (a process group, or an explicit
   `wait`/`terminate` on the real child rather than its shell), and make the
   test assert it left nothing behind;
2. if it belongs to the judge's machinery (the assay runner, its liveness
   plugin, or the image's own entrypoint), file it against assay with that
   identity, not here;
3. if the leak is a host-side artifact of the remote gate worker's own
   wrapper, file it in the netcup runner backlog, not here.

**Acceptance test idea.** A focused case runs the baseline argv exactly as the
lane declares it, waits for the command to finish, and asserts that no
descendant of the pytest leader is still alive (by pid, after a bounded reap
window). Red-first: the case must fail against the pre-fix tree with the same
"leaked process" refusal assay reports, and pass after. A second case proves the
fixed test kills its real child, not its shell (fake a command that ignores
SIGTERM to its shell, or inspect the process group).

**Where.** `scripts/damon-analysis/tests/test_analyze_process.py` and
`analyze_process.py:get_pid` for candidate 1; `scripts/damon-analysis/assay.toml`
and `run-gate.toml` only for the reproduction shape (not for a policy change);
`assay/` or `scripts/netcup/` for candidates 2 and 3, with the assay B-number or
netcup NC-number cross-referenced here when they are filed.

**Priority.** Should-fix. The project has no other mutation tier, and the
failure consumes a full baseline before refusing.

**Close when.** The `r2` lane's baseline reaches candidate execution on the
runner host that reproduced the refusal, the leaked descendant is identified by
owner in this entry (with the evidence that named it), and if the owner was this
project's own test, a focused case fails on the pre-fix tree and passes on the
fix.

---

## DA-02 — the lib never reads `sz_filter_passed`, so its physical mode calls the machine's hot set every unit's hot set, and the guide files `young` under the wrong layer

**Status:** OPEN (filed 2026-11-25); Should-fix. Source: the P-plat-19k r2
review's N1/H3 (`mdt-cockpit/host-setup/docs/DESIGN-ACCEPT-KIT-V2.md` §4.1),
which corrected the host-setup acceptance kit's filter chain and left the lib
and the guide as the two upstream readings that carry the same flaw.

**Observed (lib).** `SysfsInterface.read_tried_regions()`
(`lib/damon_analysis.py:282-312`) returns only `start`, `end`, `nr_accesses`
and `age` per region, and `classify_regions()` sets
`size_bytes = end - start` (`:417`). Both are then summed into the report's
hot/warm bytes (`:438`, `:656-667`, `:698-712`). With a paddr target a region
is a physical-address range of the **whole** RAM, and it is the DAMOS ops
filters that decide which of its pages a scheme counts — so Σ(`end - start`)
of the regions that matched the access pattern is the machine's hot set, the
same number for every cgroup, dominated by whichever task wrote the most. The
per-scheme value that is actually the filtered one does exist and the lib even
reads it into a dict: `read_stats()` (`:323-334`) asks for
`sz_ops_filter_passed` beside `nr_tried`/`sz_tried`/`nr_applied`/`sz_applied`,
but nothing consumes it, and `tried_regions/*/sz_filter_passed` — the number the
kernel exports **per region, after the ops filters** — is never read at all.
`analyze_container.py:226` and `batch_report.py:61` then classify and report
the `end - start` bytes, and `analyze_container.py` builds its filter with
`damo start paddr --damos_filter 'reject none memcg <path>'` (`:207-211`); the
`SCRIPTS.md:258` note that "physical mode memcg filtering may need manual
`--cgroup-path` on some setups" is the same symptom seen from the CLI side.

**Observed (guide).** `DAMON-GUIDE.md:266-272` lists `young` under
**"Core-layer handled"** and `memcg` under "Ops-layer handled (paddr only)",
while the same filter chain section (`:276-280`) says `core_filters` are
evaluated first and then `ops_filters`. The kernel implements `young` as an
**ops-level** filter alongside `memcg` (it needs the region's access
information at apply time, which the core layer does not have), and its
decision is what a scheme's `sz_ops_filter_passed` counts. So the guide's own
table answers the layer question the wrong way, and a reader who trusts it
writes `young` into `core_filters` where the kernel has no such file.

**Why it matters.** Every floor or protection number derived from the lib's
physical mode is a machine-wide number labelled with a unit name: a picture of
"this cgroup's hot set" that is really "how much RAM anything touched", which
manufactures a plausible-looking floor for a cgroup that touched almost
nothing. The accept kit works around it (it writes the ops filters itself and
reads `sz_filter_passed`), so the kit is not affected; the next consumer of the
lib will be.

**What a fix looks like.** (1) `read_tried_regions()` also reads
`tried_regions/<n>/sz_filter_passed` (per region, defaulting to absent rather
than 0 so "the kernel does not export it" and "nothing passed" cannot collapse
into one status), and the classifier sums or reports it; (2) the physical
mode's report prints the filtered number and keeps `Σ(end - start)` only as an
labelled INFO beside it, exactly as `DESIGN-ACCEPT-KIT-V2.md` §4.2 does; (3)
an ops-filter chain is an **AND** by the kernel's first-match rule, so a
"leaf hot set" needs `memcg matching=N allow=N` first and `young matching=Y
allow=Y` second — an allow-first memcg filter (or `allow=Y` on both) counts the
leaf's whole resident size plus the machine's young bytes; (4) `DAMON-GUIDE.md`
moves `young` to the ops layer and says why. Nothing may fall back from the
filtered number to `end - start` silently: an unreadable
`sz_ops_filter_passed` is missing data.

**Acceptance test idea.** A focused case builds a fake sysfs scheme tree with
non-sequential `tried_regions` names and a `sz_filter_passed` per region that
disagrees with `end - start` (cold pages of the target cgroup, young pages of
another cgroup, a young page of the target), asserts the report's number is the
filtered sum and that the `end - start` sum is labelled INFO, and a second
case asserts an absent `sz_filter_passed` is reported as missing rather than 0.
Red-first against `f00940396`-era code. A guide test can simply pin the layer
wordings (`young` under ops filters) so the two sentences cannot drift apart
again.

**Where.** `scripts/damon-analysis/lib/damon_analysis.py`
(`SysfsInterface.read_tried_regions`, `classify_regions`, the report rows),
`scripts/damon-analysis/analyze_container.py` and `batch_report.py` for the
consumers, `scripts/damon-analysis/DAMON-GUIDE.md` §4.7 for the wording, and
`scripts/damon-analysis/tests/` for the cases. Not to be changed for this
entry: the accept kit under `mdt-cockpit/host-setup/tools/accept/`, which
deliberately does not use the lib's region reading for its per-unit values.

**Priority.** Should-fix. No consumer in the repository currently ships a
number from the lib's physical mode, but the guide teaches the wrong layer and
the lib's own `read_stats()` already reads the right number it then drops.

**Close when.** `sz_filter_passed` is read and reported per region and
per scheme, the physical mode's `end - start` number is INFO only, an absent
`sz_filter_passed` is missing data rather than 0, `DAMON-GUIDE.md` §4.7 places
`young` in the ops layer with the first-match rule stated, and the cases above
are in a registered lane of this project.

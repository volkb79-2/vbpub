# Run-Gate R1 Changed-Line Coverage Plan

- Plan route: Codex `gpt-6-sol`, xhigh; `CODEX_SQLITE_HOME=$HOME/.codex/sqlite-shared`, `CODEX_HOME=$HOME/.codex`.
- Reviewed verdict commit: `a34e8c597211b7e0d7e18aec55030c1424a41892`.
- Run-Gate source and tests were unchanged from that verdict commit during planning; later candidate commits were CMRU-only.
- This is a read-only implementation plan. It does not claim tests or gates ran.

## R1 coverage plan

The local, git-ignored `run-gate-project/.assay/verdict-r1.json` artifact reports **109 uncovered lines in [`run_gate.py`](../../run_gate.py)**, with no excluded, unclassified, or missing-coverage files. Its `missing_branch_lines` list has **45 entries**; the supplied count of 43 differs from the JSON. The current source has no diff from the verdict’s commit. Line 14784 is the module entrypoint, outside a function; the other uncovered lines map to 12 innermost function bodies, including a nested `visit`.

Use the existing fixtures in [`test_run_gate.py`](../../tests/test_run_gate.py) and assert outputs, records, and Docker calls. The priorities below address the advertised behavior rather than merely executing lines.

### 1. Resolve misleading or unreachable paths first

- **`promote_follower`, L11634–54.** The uncovered paths stop a recorded profiler session successfully, report a stop failure, or explain why a token without a session cannot be resumed. Tests at L25272–323 look close, but `_dispatch` reads owner liveness during admission at source L14492. Their mocked sequence `[alive, dead]` can therefore enter **reattach**, not follow and promotion. Change the sequence to cover admission-alive, follow-alive, then promotion-dead; assert the promotion notice, session stop or stated failure, container removal, and final history/resources. The general cleanup tests at L17185–251 reach promotion without a profile record.

- **`_dispatch`, L14271–87.** This later `--fresh` refusal appears unreachable for normally declared bare-host and exec leaves: `preflight_inflight_assay_contract` rejects them at L9976–78 before dispatch reaches it. Tests at L17614–56 already target the earlier refusal. Confirm the imported-lane route, then remove or consolidate the redundant block; bypassing preflight solely to cover it would be hollow. The remaining `_dispatch` gaps need tests: L14311–17, a command lane with `--worktree` but no `{worktree}` token must refuse before Docker (the close test at L4540 runs in a child); L14397–404, an exec lane with declared `cgroup_slice` must warn that it is naming-only (L3406 is child-only); L14422–27, an exec lane with neither declaration nor ambient slice must disclose that absence; and branch L14694, a command lane’s cgroup-event error must preserve its raw status without Assay failure archival (L26844 covers the Assay side).

- **`print_lane_artifacts`, L6261 and L6281–92.** Existing artifact and deduplication checks at L4762–94 run through `run_tool`, while the direct state-directory checks at L13865–85 declare no artifacts. Add direct, captured-output cases for a command artifact relative to the effective project, a `{worktree}` artifact that becomes absolute, and an Assay verdict alias plus a distinct artifact. Assert normalized verdict output appears once and the distinct path appears once. The false side of L6261 requires an empty non-null *recorded* verdict; inflight validation rejects that value for an Assay record. Treat that guard as redundant after validation rather than inventing a malformed-record success test.

- **`cmd_check_env`, L6900–09, L6929; branches L6902, L6924, L6928.** Direct tests at L4421–52 cover helper reads and syntax fallback; covered-reference and ordinary drift cases at L4283–304 use a child. Add direct cases for a covered name, a bare-host lane without forwarded env, and duplicate references in one file (one finding). Make `resolve_environment` fail for one lane and `Path.read_text` fail for one `.py` file, and require an explicit incomplete-scan diagnostic. **Source defect:** L6900–01 and L6908–09 silently skip those inputs, yet L6935 can still announce “0 uncovered reference(s)”. An unreadable scan must not certify a clean one.

### 2. Cover pointer, doctor, and runner behavior

- **`_collect_pointers.visit` inside `_collect_pointers`, L6993; branches L6989, L6997.** The list-form pointer check at L4993–5002 is child-only. Pass a nested parsed document directly: an argv list beginning with `run-gate` must produce one joined pointer; prose and a numeric value must produce none.

- **`_pointer_project_dir`, L7020 and L7035–37.** Direct tests at L30409–25 cover a relative project, multiple targets, and a noncanonical target; the missing-project CLI case at L4970 is child-only. Add `cd {worktree}` with a root `run-gate.toml` and assert root resolution. Add `cd {worktree}/missing` and assert a named “no run-gate.toml” defect with no resolved project.

- **`_pointer_defects`, L7062, L7079–85, L7099–104, L7112–24.** Direct tests at L27716–59 cover successful loading and a retired script; many refusal cases under `TestPointerLinkage` use subprocesses. Use a small direct case table: unresolved project returns zero checked; malformed target config reports a loading defect, and a second pointer reuses the cached failure; `--worktree=…` and an ordinary option leave exactly one lane positional; reserved verbs and discovery need no lane; a substituted worktree without forwarding, a missing lane, and two positionals each produce their specific defect. Assert both defect text and invocation count.

- **`cmd_doctor`, L8808–09, L8865–66, L8884–85, L8898–900.** Doctor has direct healthy and probe-result tests, but the missing-image test at L7180 runs in a child. In a direct doctor fixture, make `physical_path` raise `GateError` and assert `[FAIL] mountinfo`; make the state-root probe raise and assert `[SKIP]` with its reason; make only `/tmp` fail `os.access` and assert `[WARN] git-config`; make image inspection return nonzero and assert `[WARN] image`, without claiming it is present. Check the doctor result severity for each.

- **`resolve_inflight`, L11751–64.** Existing foreign-record tests at L14950–15005 call `main`; the earlier inflight preflight refuses before this local guard. Call `resolve_inflight` directly with an exec-written record and Docker operations set to fail if reached. For dry-run, assert a disclosed refusal, zero result, and unchanged record; for live, assert `GateError` and no attach, remove, or overwrite. This checks the helper’s protection if the record changes between preflight and resolution.

- **`run_container_lane`, L12091, L12093–95, L12108.** Existing user, forwarding, and memory-swap argv checks (around L2202, L4713, L5509) mostly invoke a child. Reuse the fake Docker fixture through an in-process `main` call: a declared user yields the exact `--user` value; a present nonempty forwarded variable yields one `-e` without disclosing its value; empty and absent values yield none; declared `memory_swap` yields the exact `--memory-swap` pair. Assert the recorded argv and relevant disclosure.

- **`run_exec_lane`, L12390–91 and L12396–98.** The existing runner-down and exec checks exercise other preflight outcomes. Call the helper with `shutil.which("docker")` returning `None`, then with `docker ps` returning nonzero and a diagnostic on stderr. Both must raise `GateInfraError` naming the cause, before any `docker exec` starts.

- **`slice_cgroupfs_dir`, L4118–19.** The direct test at L30195–213 covers dashed, nested names. Add a single-component slice such as `gates.slice`; assert `Path("gates.slice")`.

### 3. Finish the branch-only and entrypoint gaps

The report also names four functions with missing **branches but no uncovered lines**:

- **`read_current_cgroup_events`, L789:** existing valid/invalid cgroup-file cases are close; add mixed legacy and `0::/worker` rows, asserting only the v2 path selects the counters.
- **`_cgroup_event_error`, L834:** the direct test at L30299–316 covers both counters rising; add an OOM-only rise and assert the message omits `pids.events`.
- **`physical_path`, L3903:** the longest-mount test at L30157–79 puts the more specific mount later. Put it first and a matching shorter mount later; the result must remain the specific host path.
- **`scan_env_references`, L6860:** literal helper arguments and too-few arguments are tested at L4353–415. Add a helper call with enough arguments but a computed environment name; assert it makes no confident literal-name finding.

**Module entrypoint, L14784:** `run_tool` starts a Python child through a temporary symlink (test L532–34), so its direct-script checks do not establish coverage of the parent pytest process’s `if __name__ == "__main__"` line. An in-process `runpy.run_path(..., run_name="__main__")` test with `--version` can assert `SystemExit(0)` and the installed version, exercising the actual script entrypoint as behavior.

The child-only cases explain several otherwise surprising gaps. The report’s `files_missing_coverage=[]` and correctly named `run-gate-project/run_gate.py` give no evidence of a wholesale source-root mismatch. The profiler gap has a different explanation: its existing liveness mock appears to select the wrong behavior. The verdict lists branch **source lines**, not missing arc destinations, so implementation should check same-run arc detail where a multi-condition branch remains ambiguous.

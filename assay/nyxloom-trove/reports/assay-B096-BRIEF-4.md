# Assay B096 P25 repair checkpoint — base contract passes; Topos command is red

**Worktree:** `/workspaces/vbpub/.worktrees/assay-b096`

**Branch/HEAD:** `assay-b096` / `99e588e9b5609ea991c03a08fd63acf8b6df40c7`

**Worker:** Luna xhigh
**Scope:** the one P25 `declared-base-as-tag` scenario. No full authoritative
gate, R2/mutation lane, mutation campaign, `/workspaces/dstdns`, or operator
owned file was touched.

## Valid tester-unified reproduction

The earlier `-r1` container attempt was discarded because its diagnostic probe
constructed `IsolationConfig` without the required explicit empty omission
tuple, before invoking Assay. The valid `-r2` run used the exact named
container `assay-b096-p25-declared-base-20260914-r2`, whose Docker ID was
`32f5ee76dce4ae37bbcf58373e2402e578474e6718c64f2a7c25e32a72115cf4`.

Its launch included:

```text
--cgroup-parent=dev-background.slice
--network=none
--cpus=3
-e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice
-v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub
-v /home/vb/volkb79-2/vbpub:/workspaces/vbpub
```

Inside the container, `git config --global safe.directory '*'` was set. The
current wheel was built from an exact-OID clone of this worktree and installed
into a fresh run venv using the gate's offline build/install shape:

```text
SOURCE_OID=99e588e9b5609ea991c03a08fd63acf8b6df40c7
CLONE_HEAD=99e588e9b5609ea991c03a08fd63acf8b6df40c7
CURRENT_VERSION=6.2.1.dev40+g99e588e9
INSTALLED_ASSAY_FILE=/tmp/assay-b096-p25-OtmASS/run-venv/lib/python3.14/site-packages/assay/__init__.py
```

`qualify_topos.py` was imported from the worktree. It called
`_materialize_negative(..., base_override="p33-declared-base",
tag_base_as="p33-declared-base")`, then invoked the installed executable as
the gate does:

```text
assay run topos-qualification --verdict-json <artifact>
```

The disposable repository facts were:

```text
BASE_OID=12ac3a4abba87522e95cae3233d06d10f39650c5
HEAD_OID=bb55422b751482ee0329e86d6567da8ac4901d91
TAG_RESOLUTION=12ac3a4abba87522e95cae3233d06d10f39650c5
TAG_REF_STATE=12ac3a4abba87522e95cae3233d06d10f39650c5 refs/tags/p33-declared-base
CONSUMER_HEAD_STATE=bb55422b751482ee0329e86d6567da8ac4901d91
```

The independent snapshot probe observed the P22 ref-free state:

```text
SNAPSHOT_COMMIT=bb55422b751482ee0329e86d6567da8ac4901d91
SNAPSHOT_SHOW_REF_EXIT=1
SNAPSHOT_SHOW_REF_STDOUT=<empty>
SNAPSHOT_SHOW_REF_STDERR=<empty>
SNAPSHOT_TAG_REF_FILE=absent
SNAPSHOT_REFS_TREE=<empty>
```

Docker status was collected separately from logs:

```text
docker wait assay-b096-p25-declared-base-20260914-r2 -> 0
docker logs assay-b096-p25-declared-base-20260914-r2 -> 0
docker rm assay-b096-p25-declared-base-20260914-r2 -> 0
```

The complete captured log is at
`/tmp/assay-b096-p25-declared-base-20260914-r2.log` in the cockpit.

## Root-cause diagnosis

The P25 base-resolution repair at `84baffb4` is working. The artifact says:

```text
ASSAY_PROCESS_RETURN_CODE=1
ARTIFACT_OUTCOME=FAIL
ARTIFACT_REASON=COMMAND_FAILED
ARTIFACT_EXIT=1
ARTIFACT_JUDGMENT_RESOLVED={"base": "12ac3a4abba87522e95cae3233d06d10f39650c5", "base_resolution": "merge-base", "language": "python", "source_roots": ["topos/src/topos"]}
claims[0]=FAIL/COMMAND_FAILED (R0)
claims[1]=PASS (R1)
```

Assay stdout was the normal summary, and Assay stderr was exactly empty. The
declared command did run, wrote the coverage artifact, and wrote
`pytest.log`; it did not fail while resolving the symbolic tag. The pytest log
contains:

```text
FAILED topos/tests/test_ui_drill.py::test_mounted_drill_screen_surfaces_unavailable_damon_controls
...
textual.pilot.WaitForScreenTimeout: Timed out while waiting for widgets to process pending messages.
...
1 failed, 2922 passed in 163.07s (0:02:43)
```

Therefore the observed P25 red is a command/test failure in the pinned Topos
suite (the full primary argv is `topos/tests -q -n auto`), not a B096 product
failure. The R1 claim proves the resolved base is the immutable commit OID,
not the declared tag. The snapshot probe proves that re-resolving the tag
inside P22 would be invalid, while the carried-resolution path correctly
avoids that trap. `BASE_IS_HEAD` remains a separate fail-closed path in the
focused tests.

No product repair is justified by this evidence. Changing Assay to turn
`FAIL/COMMAND_FAILED` into `PASS` would hide a real failed test. Narrowing or
changing the P25 primary Topos command would change the qualification proof
and requires an explicit gate/fixture-owner decision; it is not a safe B096
repair.

## Focused evidence

All ran with `nice -n 19 ionice -c 3`, `PYTHONPATH=src`, and no mutation lane:

```text
tests/test_measurability_base_is_head.py                         5 passed in 3.09s
tests/test_runner_evaluate_r1.py -k 'carried_base_resolution or base_is_head'
                                                                  3 passed, 22 deselected in 3.06s
tests/test_runner_run_lane.py -k 'symbolic_base or base_is_head'
                                                                  1 passed, 33 deselected in 2.99s
tests/test_python_qualification.py -k scenario_mismatch_keeps_artifact_and_command_diagnostics
                                                                  1 passed, 31 deselected in 3.06s
```

## Required next action

The P25/Topos gate owner must make the full primary qualification command
deterministic under its governed tester environment (or repair the failing
Topos UI test), then rerun this one scenario and only afterward the registered
gate. This branch stops here with no product change beyond the already
committed `84baffb4` repair and this evidence checkpoint.

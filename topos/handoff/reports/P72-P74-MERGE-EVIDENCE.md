# P72 + P74 — merge evidence (frontier pass #2, wave of 2026-07-13)

All figures are the reviewer's rerun **from `main` after both merges**, in the clean
package venv (`/workspaces/vbpub/.venv`: Python 3.13.5, pytest 9.1.1, no
`schemathesis`). Agent-env greens were not trusted and are not reproduced here — both
REPORTs quoted runs from a dirty environment (P74 added `-p no:schemathesis`, which is
not in its gate; P72's full-suite run omitted `-W error` entirely, which its own
self-review admits).

Gate commands are the handoff's, verbatim, run from the repo root (they must be: four
suite tests shell out via the repo-root-relative path `topos/src`).

## Full suite

```
main, before either merge     1139 passed, 1 failed   152.12s
main, after both merges       1205 passed, 1 failed   147.05s
```

+66 tests, which reconciles exactly: P74 +15, P72 +45 implementer +5 pass-#2
regression +1 ordering regression.

```bash
PYTHONPATH=topos/src python -m pytest topos/tests -q -W error
```

### The one failure is pre-existing on `main` and is a real (unrelated) defect

`test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2` fails identically
before and after this wave. Its premise is "a `.jsonl.zst` file **without** the
zstandard extra exits 2", but the venv *has* `zstandard` 0.25.0, so the test asserts a
path it cannot reach — and what it reaches instead is worse than a bad assertion:

```
assert result.returncode == 2
E  AssertionError: assert 1 == 2
   ... zstandard.backend_c.ZstdError: zstd decompress error: Unsupported frame parameter
```

With zstandard installed, a corrupt or truncated `.zst` recording makes `topos report`
die with an **unhandled `ZstdError` traceback** (exit 1), i.e. a raw exception crossing
a CLI boundary — which the standing error-disclosure contract forbids ("typed, bounded
errors only"). Not caused by P72/P74 and out of their scope; **carved as P79** in the
same cycle as this merge.

## Live checks from `main` (no injected seams)

The two P72 gates that were inert as submitted, driven through the real CLI:

```
$ topos action preview --kind docker-update --target my-container --admin --memory 512M
current memory usage of 'my-container' could not be established, so a limit of
536870912 bytes cannot be shown to be safe; pass --below-current to apply it anyway
(this may OOM the container)                                              exit=2
   ^ as submitted, this printed an argv and exited 0

$ ... --memory 512M --below-current --json
{"argv": ["/usr/bin/docker", "update", "--memory", "536870912", "my-container"], ...}
                                                                          exit=0

$ topos action preview --kind docker-update --target nginx.service --admin --memory 512M
target 'nginx.service' looks like a systemd unit; use 'topos action set-property'
for systemd resource changes                                              exit=2

$ topos action preview --kind docker-kill --target c1 --admin --signal 9
signal must be a symbolic name, not a number: '9'                         exit=2

$ topos action preview --kind docker-kill --target c1 --admin --signal KILL
KILL signal requires --force (data-loss prevention gate)                  exit=2

$ topos action preview --kind docker-kill --target my-container --admin --signal TERM --json
{"argv": ["/usr/bin/docker", "kill", "--signal", "TERM", "my-container"], "force": false, ...}
```

The generic execution path no longer reaches the new verbs (Python API, since the CLI
routes them away — this is the bypass F3 closed):

```
>>> execute_plan("docker-kill", "c1", admin=True, confirm="EXECUTE", root_check=lambda: True, runner=...)
outcome: refusal | kind 'docker-kill' is not in execution allowlist
runner invoked: []
   ^ as submitted: ['/usr/bin/docker', 'kill', 'c1'] -- docker's default signal is SIGKILL
```

P74, on this GPU-less host (a DRM card is present but its driver exposes no amdgpu
files — the "a GPU I cannot read" path, which is the one the review host can prove):

```
$ topos --once --json | jq .host
host_gpu_count:      [1, "host"]              <- a real count: the DRM tree was read
host_gpu_vram_total: [null, "unavail_kernel"]
host_gpu_vram_used:  [null, "unavail_kernel"]
host_gpu_busy_pct:   [null, "unavail_kernel"]
```

and the banner correctly omits the GPU segment. Live amdgpu (the present path) remains
fixture-only evidence — it needs a host with a discrete AMD GPU, which this is not.

**Not claimed:** live Docker/systemd action execution. `kill` and `update` require
root, so every gate below root is proven by fixture tests with injected runners and
none of them mutated a host. Live execution is deliberate test-host work, per the
handoff.

## Reviewer's rerun of the focused gates

```
topos/tests/test_gpu.py                              15 passed
topos/tests/test_p72_kill_update.py + test_actions.py  251 passed
py_compile (all changed files)                       OK
git diff --check                                     OK
```

## Pass-#1 overlap (workflow v2 §6 trial metric)

| Package | Findings at pass #2 | Flagged by pass #1 | Overlap |
|---|---|---|---|
| P72 | 6 | 0 | 0% |
| P74 | 5 | 1 (cosmetic scaffold comment) | 20% |

Wave total: **1 of 11 (9%)**, and the one hit was cosmetic. See `P72-REVIEW.md` for the
sharper observation: on three P72 findings, pass #1 did not merely miss the defect — it
walked the checklist to the right question and certified the wrong answer, while listing
the inert implementations under "Known gaps" in the same document without registering
the contradiction.

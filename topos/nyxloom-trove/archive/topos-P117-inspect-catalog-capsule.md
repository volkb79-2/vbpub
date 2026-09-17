---
schema_version: 1
id: topos-P117-inspect-catalog-capsule
project: topos
title: "Close the frozen inspect-files catalog validation residual"
tier: low-cost-route-trial
input_revision: "e417868d"
source: {kind: product-goal, ref: "controller-frozen-p117-residual"}
stack: none
depends_on: [topos-P113-execute-primitives-coverage]
session: "fresh"
scope:
  touch:
    - "topos/tests/test_inspect_files.py"
    - "topos/nyxloom-trove/handoffs/topos-P117-inspect-catalog-capsule.md"
  forbid:
    - "topos/src/topos/inspect_files/catalog.py"
    - "topos/nyxloom-trove/nyxloom.toml"
    - "topos/tools/coverage_gate.py"
    - "topos/pyproject.toml"
oracles:
  - id: O1
    observable: "the six literal catalog.py lines and four branch arcs listed below are covered by causal assertions through the public planning boundary, except the one explicitly identified delegating helper"
    negative: "a count-only, broad exception, mock of the validator, source edit, pragma, or an assertion that does not distinguish the rejection/normalisation behavior substitutes for the named paths"
    gate: topos-suite
  - id: O2
    observable: "the test change is append-only in the existing TestPathSafety class and leaves product source, gate/tooling, and all unrelated tests unchanged"
    negative: "a rewritten existing test, test weakening, fixture change, or scope expansion is accepted"
    gate: topos-suite
gates: [topos-suite]
review_focus:
  - "each input must exercise the stated validation arm and assert either its exact error class/message fragment or the normalised path behavior"
  - "reject an edit outside the nominated test file or a non-causal test"
escalate_if:
  - "the supplied public build_inspect_plan boundary cannot exercise a listed line/arc without a product-source edit"
  - "an expected behavior in the capsule is false in the supplied source excerpt"
advances: []
---

# P117 — inspect-files catalog capability capsule

This is a constrained **test-drafting** trial for
`openrouter/inclusionai/ling-3.0-flash:free`. The controller has already
read the project doctrine, selected the immutable base, and will run every
test/gate. You have no shell access by design. Read this file only, then edit
only `topos/tests/test_inspect_files.py`.

## Work

Append small, causal tests to the existing `TestPathSafety` class. Do not
alter or delete any existing test. Reuse the existing imports:

```python
import pytest
from topos.inspect_files.plan import build_inspect_plan
```

Add tests for exactly these behaviors:

1. `build_inspect_plan("docker-json-log", "abc/def")` raises `ValueError`
   whose message includes `"unsafe path characters"`.
2. `build_inspect_plan("cgroup-files", "sys/fs/cgroup/system.slice/ssh.service")`
   returns an `InspectFilesPlan`, and every `path_previews` string starts with
   `"/sys/fs/cgroup/system.slice/ssh.service/"` (this is the no-leading-slash
   cgroup-root normalization form).
3. `build_inspect_plan("cgroup-files", "/sys/fs/cgroup/")` raises
   `ValueError` whose message includes `"must not be empty"`.
4. `build_inspect_plan("cgroup-files", "system.slice/evil!")` raises
   `ValueError` whose message includes `"unsafe characters"`.
5. `build_inspect_plan("cgroup-files", "system.slice//ssh.service")` raises
   `ValueError` whose message includes `"unsafe path segments"`.
6. Import `_validate_cgroup_target` inside one test and call it with a valid
   relative cgroup path. This is the only direct-helper assertion permitted:
   it verifies the thin public validator delegates without changing/reading
   filesystem state.

Use exact `pytest.raises(ValueError, match=...)` assertions, no mocks, no
loops hiding inputs, and no `# pragma` or source changes. Stop after this edit
and reply with a concise self-review: test names, causal input→assertion
mapping, and the exact file changed. Do **not** claim that a test/gate ran.

## Frozen residual and source capsule

The controller's clean `tester-unified` record at `e417868d` reports these
uncovered executable lines in `topos/src/topos/inspect_files/catalog.py`:

```text
152 153 174 185 192 193 195 196 197 198
```

Relevant production behavior (line numbers from that immutable base):

```python
146 def _validate_docker_target(target: str) -> None:
148     if not target or target.startswith("/") or target.startswith("."):
151     if not _DOCKER_ID_PATTERN.match(target) and "/" in target:
152         msg = f"docker target contains unsafe path characters: {target!r}"
153         raise ValueError(msg)
154     if not _DOCKER_ID_PATTERN.match(target) and not _DOCKER_NAME_PATTERN.match(target):

172 def _validate_cgroup_target(target: str) -> None:
174     _normalise_cgroup_target(target)

177 def _normalise_cgroup_target(target: str) -> str:
182     if target.startswith("/sys/fs/cgroup/"):
183         relative = target.removeprefix("/sys/fs/cgroup/")
184     elif target.startswith("sys/fs/cgroup/"):
185         relative = target.removeprefix("sys/fs/cgroup/")
186     elif target.startswith("/"):
189     else:
190         relative = target
191     if not relative:
192         msg = "cgroup target must not be empty"
193         raise ValueError(msg)
194     if not _CGROUP_PATH_PATTERN.match(relative):
195         msg = f"cgroup target contains unsafe characters: {target!r}"
196         raise ValueError(msg)
197     if any(part in {"", ".", ".."} for part in relative.split("/")):
198         msg = f"cgroup target contains unsafe path segments: {target!r}"
199         raise ValueError(msg)
```

The existing class already uses this style:

```python
class TestPathSafety:
    def test_cgroup_target_accepts_sysfs_path(self) -> None:
        plan = build_inspect_plan("cgroup-files", "/sys/fs/cgroup/system.slice/ssh.service")
        assert isinstance(plan, InspectFilesPlan)
        assert all(str(path).startswith("/sys/fs/cgroup/system.slice/ssh.service/") for path in plan.path_previews)

    def test_cgroup_target_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            build_inspect_plan("cgroup-files", "")
```

## Hard boundary / BLOCKED

No shell, no network, no gate command, no commit, no other read, no source
edit, and no new file are authorized. If a required behavior cannot be
implemented from this capsule, do not guess or broaden access: reply exactly
`BLOCKED: <one-sentence reason>` and make no edit.

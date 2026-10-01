---
schema_version: 1
id: topos-P121-bpf-gate-capsule
project: topos
title: "Close the preflighted BPF gate safety/reporting residual"
tier: low-cost-route-trial
input_revision: "e54e7ddc"
source: {kind: product-goal, ref: "controller-preflighted-p121-residual"}
stack: none
depends_on: [topos-P117-inspect-catalog-capsule]
session: "resume:c4ad6daf-cdb6-4387-acf2-e8db6880d7f6"
scope:
  touch: ["topos/tests/test_bpf_gate.py", "topos/nyxloom-trove/handoffs/topos-P121-bpf-gate-capsule.md"]
  forbid: ["topos/src/topos/bpf_gate.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "the frozen probe-failure, no-blocker rendering, and provider-error rendering paths have causal assertions"
    negative: "a source edit, pragma, assertion-free call, broad exception, or mock of the report function substitutes for observable behavior"
    gate: topos-suite
  - id: O2
    observable: "only the nominated test file changes"
    negative: "rewriting existing tests, changing product/gate files, or creating files is accepted"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["the path-selective OS seam cannot affect provider dependencies", "the dataclass construction lists every field"]
escalate_if: ["a supplied expectation contradicts this preflighted capsule", "a listed path needs an out-of-scope source edit"]
advances: []
---

# P121 — preflighted BPF gate capability capsule

Read only this handoff and `topos/tests/test_bpf_gate.py`; append exactly three
tests to that file. Do not alter existing tests or any other file. No shell,
network, gate, commit, source edit, search/listing, or new file is authorized.
The controller executes all tests/gates.

1. Add `import os`. In a test with a real temporary `pin_root`, save
   `real_access = os.access`, define `access_for_pin(path, mode)` that raises
   `OSError("denied")` **only when `Path(path) == pin_root`**, otherwise returns
   `real_access(path, mode)`. Patch `topos.bpf_gate.os.access` with that side
   effect. Call `run_bpf_gate(proc_root=proc_fixture(), pin_root=pin_root,
   command_runner=qdisc_stub, uid=0, bpftool_path="/usr/bin/bpftool")`; assert
   `pin_root_writable is False` and `blockers == (f"{pin_root} is not writable",)`.
2. With another real temporary `pin_root`, call those same arguments without a
   patch; assert `blockers == ()` and rendering contains
   `"live BPF loading: not attempted"`.
3. Import `BpfGateReport`; construct it with **all** fields exactly as below,
   then assert rendering contains `"provider errors: ['fixture unavailable']"`:

```python
BpfGateReport(uid=0, bpftool="/usr/bin/bpftool", pin_root="/tmp/pin",
    pin_root_writable=True, blockers=(), probe_commands=(), live_commands=(),
    baseline={"provider_status": {"errors": ["fixture unavailable"]}})
```

Use exact assertions. The only permitted mock is the path-selective `os.access`
seam in test 1; never mock `run_bpf_gate` or `render_report`. Stop after the
edit and reply with test-name → behavior mapping and the exact changed file. Do
not claim a test/gate ran. If impossible, reply `BLOCKED: <one sentence>` and
make no edit.

## Frozen source capsule

```python
38 pin_root_writable = pin_root.exists() and os.access(pin_root, os.W_OK)
39 except OSError: pin_root_writable = False
42 if probe_bpftool is None:
44 if probe_uid != 0:
46 if not pin_root_writable: blockers.append(f"{pin_root} is not writable")
106 if report.blockers:
109 else: lines.append("live BPF loading: not attempted")
122 if isinstance(provider_status, dict) and provider_status.get("errors"):
123     lines.append(f"provider errors: {provider_status['errors']}")
```

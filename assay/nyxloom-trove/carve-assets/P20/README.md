# P20 carver-owned acceptance packet

These files freeze P20's risky interfaces and independent acceptance inputs.
They are owned by the carver/reviewer, not by the implementer.

Dispatch recipe, from the isolated P20 worktree:

```sh
git apply assay/nyxloom-trove/carve-assets/P20/skeleton.patch
PYTHONPATH=assay/src python -m pytest \
  --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P20/test_acceptance.py -q
```

The skeleton creates only `src/assay/safeio.py`. Its signatures and state
machine are normative; its TODO bodies deliberately fail. The implementer fills
those bodies, wires the frozen coverage/runner/Git contracts in the handoff,
and copies any generally useful cases into the ordinary `tests/` suite. It must
not edit this directory. The controller runs this locked suite separately,
followed by the registered `tester-unified` gate.

`probe_git_boundary.py` is a tracer bullet, not production code. It executes the
proposed exact Git environment/argv against two real repositories and hostile
ambient selectors, local `core.worktree`, a replace ref, external diff, and a
configured signing program. Its witnessed tester-unified result is recorded in
the P20 JIT carve report.

`expected/post-dirty-v3.json` is hand-authored. The acceptance test substitutes
only `<HEAD>` and `<COMMAND>`; Assay never generates its own expected artifact.

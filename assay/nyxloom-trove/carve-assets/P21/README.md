# P21 carver-owned acceptance packet

These files freeze P21's v4 wire contract, bounded Python mutation-site seam,
and verdict-output reservation. They are owned by the carver and reviewer; the
implementer must not edit this directory.

Dispatch recipe, from the isolated P21 worktree:

```sh
git apply assay/nyxloom-trove/carve-assets/P21/skeleton.patch
PYTHONPATH=assay/src python -m pytest \
  --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P21/test_acceptance.py -q
```

`skeleton.patch` creates the exact public verdict-output seam. It compiles, but
its TODO bodies deliberately fail. The production implementation fills those
bodies and wires the fixed call order from the handoff. The mutation-site seam
replaces existing types and is specified by the handoff plus the independently
hand-authored `python-site-manifest.json`; it is not duplicated as a conflicting
second production skeleton.

The four files under `expected/` are complete, hand-authored artifacts,
including the distinct payload-free
`INCONCLUSIVE/MUTATION_UNSUPPORTED` Go-capability terminal. Assay never
generates its own expected inputs. `invalid-cases.json` defines complete
invalid documents mechanically as one named canonical base plus exact
JSON-pointer replacements; this keeps every negative reviewable without
copying hundreds of unchanged producer fields. Every changed public v4 shape
has at least two invalid cases.

The controller runs this locked suite separately, then the ordinary tests and
the registered `tester-unified` gate. A reviewer must add at least one new
combined-axis attack not named in this packet.

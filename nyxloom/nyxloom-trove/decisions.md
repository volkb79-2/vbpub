# nyxloom dev decisions inbox — product calls awaiting the user (D-<NNN>).

## 2026-09-02 — retire the coverage/mutation/canary toolkit and GA1/GA4 gate-verify (nyxloom-P98)

nyxloom-P98 deletes `src/nyxloom/coverage_gate.py`, the gate-judgment half of
`src/nyxloom/mutation_gate.py` (its pure mutant-generation engine survives as
`src/nyxloom/mutants.py`, the only piece `tools/remote_mutation_audit.py`
needs), and `src/nyxloom/gate_canary.py`, and retires GA1
(`nyxloom gate verify`, the CLI "gate for the gate") and GA4 (the daemon's
periodic gate-verify cadence) end to end. This reverses the 2026-07-27
operator directive that enabled `mutation_gate` in
`nyxloom-trove/nyxloom.toml`, on the premise that the toolkit modules earn
their keep as something nyxloom's own gate — or another project's — actually
runs. That premise no longer holds: nyxloom's own `[gates.tester-unified]`
has run entirely through `run-gate.py` (nyxloom-P48) since that package
landed, and never declared a `phase='mutation'` gate that would have
exercised the toolkit; no project, including nyxloom itself, ever imported
these modules as a library. GA1/GA4's external gate-trustworthiness
verification (proving a declared gate genuinely rejects a known-bad canary)
is superseded by Assay's own R2/R3 mechanisms once a project declares
assay/run-gate lanes, as nyxloom itself now does. This executes the prior
analysis in
`nyxloom-trove/reports/ASSAY-NYXLOOM-REORIENTATION-2026-08-17.md`'s
"Deletion inventory and Assay transfer check" section, including that
report's endorsement of deleting `gate_canary.py` because Assay's own R3
canary mechanism is present and stronger.

One deliberate exception, not a missed cleanup: `Policy.
gate_verify_interval_days` (`config.py`) and `ReconcileInput.
days_since_gate_verify` (`reconcile.py`) stay declared, permanently unread by
any live code path once this package's scheduling removal lands.
`tests/legacy_planner.py` — a mechanically self-verified, byte-identical
snapshot of `reconcile.py` at commit `052857ae`, forbidden to edit — reads
both fields unconditionally off the same production `Policy`/
`ReconcileInput` instances the live planner consumes, so deleting either
field would break that file's own byte-identity self-check. They are kept
declared for that reason alone, not because anything still uses them.

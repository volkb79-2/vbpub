# rigor-record backlog (RRK)

Entries use the id sequence `RRK-NN`. Check the highest existing id before adding one.

* **RRK-01 no CLI yet.** The memo's `rigor-record` console script (assemble, render, check, sync, update, queue, base, adopt, note) arrives with RR-2..RR-5 and GN-1. RR-1 ships the library API only. Decide there whether the CLI adopts `cli-extended` (the estate rule for new CLIs); doing so adds the first runtime dependency.
* **RRK-02 index schema.** `rigor/index.json` has no schema in RR-1 (it is a generated view; RR-2 owns the renderer and should add `rigor-index-1.json` with a golden).
* **RRK-03 assay outcome vocabulary.** The fragment `outcome` is the closed set `PASS | FAIL | ERROR`; A-1 (`assay analyze export`) maps assay's verdict outcomes onto it. If assay's real vocabulary needs more members, bump the fragment minor additively (new enum member is a meaning change: a hard cut per the SPEC versioning rule).

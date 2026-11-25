<!-- backlog-format: 1; prefix: AGC- -->

# agctl backlog

Closed items go to `archive/YYYY-QN.md`; format: `docs/BACKLOG-FORMAT.md`.

## AGC-1 — the machine plan source, DIED derivation and a real gate-to-package join

**State:** open · **Severity:** medium · **Owner:** platform · **Links:** blocked-by - (part (c) needs the controller's agreement), docs/design/agctl/DESIGN.md sections 3, 4 and 4a, agctl/src/agctl/runs.py, agctl/src/agctl/sources.py

**Problem.** Three inputs of DESIGN section 4a are still approximated.

(a) **Plan state** comes from `STATE.md` prose (reported `unverified`), not from the machine
source `~/controller-state/<name>/plan.yaml` of section 3. Its map (`planned`→`open`,
`awaiting-review`/`fix-round`/`mergeable`→`running`, `decision`→`blocked`, `merged`/`released`→
`done`) is not implemented, so a row's `plan` cell says `prose`.

(b) **DIED** is not derived. A registry record in state `launching`/`running` whose lock file is
free (the kernel released it) is the section 4 DIED case; the current build prints the recorded
state and leaves the inference to its reader. The pieces exist (`RunRecord.registry_state`, the
`.lock` path, `sources.LockTable.state_of`), so this is a join, not a new parser.

(c) **Gate results join on `project_dir`, not on a package id.** `meta.json` names the project
directory (`cmru`, `libraries/iobench`) and nothing in a gate record names the package, so a
package with no matching project directory reports `unknown` plus the held-slot count.

**Wanted.** (a) read `plan.yaml` with the section 3 schema and map, keeping `unverified` only
for prose; (b) derive DIED from the lock table and print it as the run's state with the lock's
pid; (c) settle the package↔project join — DESIGN 4a's own follow-up ("a holder line ... beside
each slot it takes") implies a `remote-gate.sh` change, which the controller must agree to first.

## AGC-2 — no R2 mutation lane and no canary lane

**State:** open · **Severity:** medium · **Owner:** platform · **Links:** agctl/run-gate.toml

**Problem.** `run-gate.toml` declares only `tests` (pytest with a 100 % line+branch coverage
floor). The estate's release gate expects R0/R1, R3 (canary rejection) and, asynchronously,
R2. Without an `assay.toml` judge and an R3 canary, a release of this project cannot claim
those levels.

**Wanted.** An `assay.toml` judge lane for the readers (the parsers are the mutation-worthy
surface) and a canary lane that proves the gate rejects a seeded defect.

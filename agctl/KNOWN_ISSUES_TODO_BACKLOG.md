<!-- backlog-format: 1; prefix: AGC- -->

# agctl backlog

Closed items go to `archive/YYYY-QN.md`; format: `docs/BACKLOG-FORMAT.md`.

## AGC-4 — the machine plan source and a real gate-to-package join

**State:** open · **Severity:** medium · **Owner:** platform · **Links:** reopens AGC-1, docs/design/agctl/DESIGN.md sections 3 and 4a, agctl/src/agctl/runs.py, agctl/src/agctl/sources.py

**Problem.** Two inputs of DESIGN section 4a are still approximated.

(a) **Plan state** comes from `STATE.md` prose (reported `unverified`), not from the machine
source `~/controller-state/<name>/plan.yaml` of section 3. Its map (`planned`→`open`,
`awaiting-review`/`fix-round`/`mergeable`→`running`, `decision`→`blocked`, `merged`/`released`→
`done`) is not implemented, so a row's `plan` cell says `prose` and its `plan_id` cell carries
the designator that `STATE.md` line named. The prose join is on the TOPIC (`plat`, `core`), the
only stage a branch slug (`core-ciu-r2fix`) and a `P-core-08` line share, so the line may
describe a sibling package of the same topic; that is why the cell is `unverified` and why the
designator is reported separately.

(b) **Gate results join on a head, not on a package.** A result now joins a row only when its
commit id is that row's head (exact, or a prefix of at least 7 characters); a project-directory
collision is `unknown` and the colliding result is named in the `detail`. What is still
missing is an authoritative package identity in the gate record: `meta.json` names the project
directory (`cmru`, `agctl`), never the package or the run. DESIGN 4a's own follow-up ("a holder
line ... beside each slot it takes") implies a `remote-gate.sh` change, which the controller
must agree to first.

**Wanted.** (a) read `plan.yaml` with the section 3 schema and map, keeping `unverified` only
for prose; (b) settle the package↔project join once `remote-gate.sh` records the package or the
run name beside the slot, and drop the head heuristic.

## AGC-5 — no R2 mutation lane and no canary lane

**State:** open · **Severity:** medium · **Owner:** platform · **Links:** reopens AGC-2, agctl/run-gate.toml

**Problem.** `run-gate.toml` declares only `tests` (pytest with a 100 % line+branch coverage
floor). The estate's release gate expects R0/R1, R3 (canary rejection) and, asynchronously,
R2. Without an `assay.toml` judge and an R3 canary, a release of this project cannot claim
those levels.

**Wanted.** An `assay.toml` judge lane for the readers (the parsers are the mutation-worthy
surface) and a canary lane that proves the gate rejects a seeded defect.

## AGC-6 — later verbs of the same design

**State:** parked · **Severity:** low · **Owner:** platform · **Links:** docs/design/agctl/DESIGN.md sections 5-7

**Problem.** The routes, `run`, `status`/`check`/`limits`, `plan.yaml` and the handover verbs
(`handover check`, `handover ready`, `prime`, `finalize`, `successor`) are separate packages
(DESIGN section 8). Today a controller gets `agctl estate` only.

**Wanted.** Slice 2 and the routines of DESIGN sections 5-7, each with its own lane. Revived by
the packages that own them; not a defect of slice 1.

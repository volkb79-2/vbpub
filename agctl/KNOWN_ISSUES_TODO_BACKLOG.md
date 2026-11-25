<!-- backlog-format: 1; prefix: AGC- -->

# agctl backlog

Open items only; closed items go to `archive/YYYY-QN.md`. Format: `docs/BACKLOG-FORMAT.md`.

## AGC-1 — `agctl estate` has no run, gate, plan or handover reader yet

**State:** in-progress · **Severity:** high · **Owner:** platform · **Links:** blocked-by -, docs/design/agctl/DESIGN.md section 4a

**Problem.** This build of `agctl estate` reads git only. Every other field of a row
(`owner`, `plan`, `runs`, `gates`, `handover`) prints `unknown` with the reason
`reader not implemented yet: AGC-1`, so an operator reading the output sees the coverage
rather than a plausible empty value.

**Wanted.** The readers of DESIGN section 4a, each read-only: legacy flat run records
(`<runs>/<name>.{done,live,wt,route,thread,last.txt}`, the `.done` outcome rules, the
`<run>-y<N>` yield chain), `runs/<id>/run.json` when present and valid,
`.controller-state/<name>/STATE.md` package-id lines marked `unverified`, handover age, and
`$CONTROLLER_HOME/remote-gates/` results. Gate slots are decided from `/proc/locks` matched
by device and inode, never by a probe `flock`.

## AGC-2 — the estate header (`agctl estate --header`) does not exist yet

**State:** open · **Severity:** high · **Owner:** platform · **Links:** docs/design/agctl/DESIGN.md section 8 item 1d

**Problem.** The compact, hook-injectable header (merge candidates, held leases, live runs,
each peer's handover age, own handover age; stable order, bucketed ages, 4 KB hard cap with
`[truncated]`) is not implemented.

**Wanted.** It shares one reader module with `agctl estate` so the two views can never
disagree, and an unchanged estate prints byte-identical text (no timestamp that moves on
every call).

## AGC-3 — no R2 mutation lane and no canary lane

**State:** open · **Severity:** medium · **Owner:** platform · **Links:** agctl/run-gate.toml

**Problem.** `run-gate.toml` declares only `tests` (pytest with a 100 % line+branch coverage
floor). The estate's release gate expects R0/R1, R3 (canary rejection) and, asynchronously,
R2. Without an `assay.toml` judge and an R3 canary, a release of this project cannot claim
those levels.

**Wanted.** An `assay.toml` judge lane for the readers (the parsers of AGC-1 are the
mutation-worthy surface) and a canary lane that proves the gate rejects a seeded defect.

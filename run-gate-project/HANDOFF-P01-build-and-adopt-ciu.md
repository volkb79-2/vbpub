# HANDOFF run-gate-P01 — build `run-gate.py` + adopt in ciu (first consumer)

- Project: vbpub / `run-gate-project` (estate decisions dstdns D-110/D-111 + amendment)
- Tier: sonnet-xhigh, fresh session, hand-started (no nyxloom trove here — this file IS the
  contract; BLOCKED protocol at the bottom is binding)
- Authored at vbpub main `910d8b8e` (2026-08-20). **Verify every cited anchor at YOUR HEAD
  before editing — anchors drift.**
- Worktree: `cd /workspaces/vbpub && git worktree add -b feat/run-gate-p01 .worktrees/run-gate-p01 main`
  — work ONLY there. Commit on the branch; do NOT merge (controller merges at review).

## Context to read first (exact order)

1. `run-gate-project/README.md` — the design authority you are building. Whole file.
2. `run-gate-project/CONSUMERS.md` — the adoption contract, incl. the `run-gate.toml` schema
   sketch and the assay orchestration/judgment split table.
3. `ciu/nyxloom-trove/nyxloom.toml` — the `[gates.tester-unified]` entry: its argv IS the
   incantation you are absorbing into the tool (cgroup resolve + conditional LoadState +
   `-e` passthrough + mount + `(cd tools/assay && sha256sum -c …)` + assay run). Its comment
   block documents WHY each piece exists — preserve every behavior.
4. vbpub `AGENTS.md` § "Manual tester-unified gate runs — the four traps" — the manual recipe
   your tool obsoletes for adopted projects (dual mount, safe.directory, detached form,
   cgroup env).
5. `ciu/nyxloom-trove/reports/checkpoint-A-review-2026-08-19.md` — checkpoints P06/P07: the
   measured argv defects that motivate this tool AND its acceptance oracle (O4).
6. `ciu/assay.toml` — the judgment config your `kind="assay"` lane references by name and
   must NOT duplicate.

## Contract

### B1 — scope of THIS package
Build the tool + adopt it in ciu. **De-vendoring `ciu/tools/assay/*.pyz` is OUT of scope**
(depends on assay being baked into the tester-unified image — a separate package); the tool
therefore verifies + invokes the pyz exactly where the current gate argv finds it today.

### B2 — the tool (`run-gate-project/run-gate.py`)
- **Stdlib only** (tomllib/argparse/subprocess/hashlib/pathlib/os/sys). `#!/usr/bin/env python3`.
- In-file `__revision__ = 1` and a schema_version check on `run-gate.toml`.
- Verbs: `<lane>` (run), `--list` (machine-readable: one lane per line,
  `name<TAB>kind<TAB>environment`), `--help`/no-args (usage() with lanes + revision).
- Resolves `run-gate.toml` **relative to the invoked script's realpath's directory? NO —
  relative to the CWD's project root**: the file sits next to the (sym)link/copy in the
  consuming project, NOT next to this source file. A missing `run-gate.toml` → loud error
  naming the expected path.
- §4.2a discipline everywhere: `CGROUP_PARENT_DEV_BACKGROUND` required from env (no
  fallback); physical repo root READ from env/`ciu.env`-style source, never invented; every
  config error names key + file.
- Behaviors absorbed from the current gate argv, each preserved exactly: conditional
  LoadState (`[ -d /run/systemd/system ]` guard), `--cgroup-parent` + `-e` both, dual-path
  mounts + `safe.directory '*'` (worktree gitfiles), pin verify from the pin's own directory,
  detached run + wait + full logs, judged job's exit status = the tool's exit status
  (no masking), verdict artifact path printed.

### B3 — ciu adoption (first consumer, proof by consumption)
- `ciu/run-gate.py` → relative symlink `../run-gate-project/run-gate.py` (committed).
- `ciu/run-gate.toml`: one lane `ciu`, `kind="assay"`, `assay_lane="ciu"`,
  `environment="tester-unified"`, assay pin block per CONSUMERS.md.
- `ciu/nyxloom-trove/nyxloom.toml` `[gates.tester-unified]`:
  `argv = ["bash","-c","cd {worktree}/ciu && ./run-gate.py ciu"]` (or equivalent minimal
  form — the mechanics all live in the tool now); keep `phase`/`asserts`/`timeout_seconds`/
  `environment` untouched; REPLACE the mechanics comment block with a pointer to
  `run-gate-project/README.md` + a one-line summary.
- `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` CIU-40 row → "PARTIAL — run-gate.py adopted (P01);
  de-vendor pending assay image-bake". `ciu/CHANGES.md` Unreleased entry.
- vbpub `AGENTS.md` four-traps section: prepend one sentence — for projects with a root
  `run-gate.py` the recipe is superseded by `./run-gate.py <lane>`; the traps text stays
  (non-adopted projects still need it).
- `run-gate-project/README.md` status line DESIGNED → BUILT (P01) + any measured deltas;
  same for CONSUMERS.md if the schema shifted during build (record WHY inline).

### B4 — tests (two levels, never conflated — evidence-ladder discipline)
1. **Unit suite** `run-gate-project/tests/` (pytest, own venv): arg parsing, usage/--list
   output shape, toml validation errors (each names key+file), §4.2a failures (missing env
   var → loud), and argv CONSTRUCTION pinned against a FAKE docker (PATH-shim precedent:
   `ciu/tests/tests/test_ciu_worktree.py` `_fake_docker`).
2. **Live acceptance (O4)** — construction is NOT acceptance (P06 `docker exec --` exit 127;
   P07's three argv defects — all invisible to fakes): the REAL gate must run end-to-end
   through the tool.

## Oracles

- **O1-ux:** `./run-gate.py --help` prints usage incl. `__revision__` and the lane table;
  `--list` emits the machine-readable form; unknown lane → exit≠0 naming the known lanes.
  Negative: a stack trace reaching the user for any config/usage error.
- **O2-failfast:** missing `run-gate.toml`, unknown top-level key, unknown `kind`, missing
  `CGROUP_PARENT_DEV_BACKGROUND` → each a one-line error naming the offender; grep proves NO
  fallback slice-name or path literal in the source. Negative: any `${VAR:-default}`-shaped
  behavior for an environment fact.
- **O3-unit:** the unit suite green in a fresh venv; the fake-docker argv pins cover cgroup
  both-ways, dual mounts, pin-verify cwd, detachment. Negative: an argv asserted only as a
  joined string (assert the LIST).
- **O4-live-acceptance:** from the worktree, `cd ciu && ./run-gate.py ciu` runs the REAL
  tester-unified Assay gate end-to-end → prints the Assay PASS line + the verdict-json path,
  exit 0; the verdict JSON's `claims` show R0+R1 PASS. Run it detached-safe (the tool's own
  run form). Negative: green unit suite + never-executed live path — the exact defect class
  this tool exists to kill. NOTE: requires a clean ciu tree (assay DIRTY_TREE fails closed)
  — commit before running; if the tree cannot be clean for reasons outside your diff, that
  is a BLOCKED, not a workaround.
- **O5-adoption:** the nyxloom gate argv is the two-token form; `git diff` shows the old
  incantation deleted from nyxloom.toml and PRESENT (behavior-equivalent) in the tool;
  CIU-40/CHANGES/AGENTS edits per B3.
- **O6-docs:** README status flipped; CONSUMERS matches the shipped schema; deviations
  recorded inline with reasons.

## Scope

- **touch:** `run-gate-project/` (all new files incl. tests), `ciu/run-gate.py` (new symlink),
  `ciu/run-gate.toml` (new), `ciu/nyxloom-trove/nyxloom.toml` (the one gate entry + its
  comment), `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-40 row), `ciu/CHANGES.md`,
  `AGENTS.md` (the one pointer sentence), `run-gate-project/HANDOFF-P01-…-LOG.md` (your LOG).
- **forbid:** `ciu/src/`, `ciu/tests/`, `ciu/assay.toml`, `ciu/tools/assay/` (de-vendor is
  NOT this package), `cmru/`, `assay/`, `tester-unified*/`, `nyxloom/`, any dstdns path.

## Evidence discipline

LOG chronological incl. failures (`run-gate-project/HANDOFF-P01-build-and-adopt-ciu-LOG.md`);
every claim names its command + counts; the O4 live run's verbatim output lines go in the
LOG; unit-suite green is an iteration signal, O4 is the acceptance. Commit series on the
branch; final message: tip sha, O1–O6 status, O4 verbatim PASS lines.

## BLOCKED protocol

If an oracle cannot be satisfied within scope (a needed file is forbidden, the live gate
cannot run for an environment reason your diff cannot fix, the nyxloom gate schema rejects
the two-token argv), STOP: write the LOG with the exact blocker, commit the branch as-is,
end your final message with `BLOCKED: <one-line mechanical reason>`. Product calls are the
controller's (D-numbered), never yours.

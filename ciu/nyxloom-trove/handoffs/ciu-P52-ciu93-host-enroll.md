# ciu-P52 — CIU-93: `ciu host enroll` (v7 backport, S14.7)

**Input revision:** ciu `main` @ current HEAD when carved onto branch
`ciu-p51-bundle` (this is item 2 of the operator's original 4-item bundle —
CIU-91/58, CIU-96, CIU-88 already landed on this same branch as ciu-P51,
reviewed ACCEPT WITH FIXES, fixes applied. **Do all work on TOP of that —
same branch, same worktree, do not create a new one.**). Unblocked as of
`cmru-v5.1.0` (KI-24 — `get.py`'s `enroll` subcommand — shipped 2026-09-08).

**Why this is a fourth part of the same bundle, not a fifth package:**
operator instruction was one worktree, all four items, one release. Parts
1/3/4 (ciu-P51) already merged conceptually onto this branch; this handoff
is the remaining item. **Do not merge this branch to `main` when you're
done — the controller merges once, after this part is also reviewed
ACCEPT.**

---

## Context to read first (in this order)

1. `docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` — the FULL design, revision 2,
   operator-accepted 2026-09-03 "in shape" for both the v7 backport (this
   package) and v8. Read it completely — §3 (step 1, control host), §4
   (the target side — already shipped as cmru KI-24, read-only reference,
   **do not touch cmru**), §5 (step 2, control host), §6 (the v7-vs-v8
   comparison table — **this is your authoritative source for every v7-
   specific detail**: key location, inventory table name, verb shape),
   §7 (security posture — every one of these properties needs an oracle),
   §8 (O1-O6, the oracle list this package's own oracles are built from),
   §10 (explicit non-goals — do not scope-creep into these).
2. `docs/SPEC.md` **S14.7** (search for it) — the normative v7 summary of
   the same design. S14.7a/b/c/d map directly to this package's Work items
   below. Also read **S14.1-S14.6** (the sections immediately before it)
   for the existing host-inventory/SSH-transport conventions this verb
   must fit into without changing: what a `.ciu.hosts.toml` row already
   looks like, S14.3a (host-scoped secrets, the existing
   `.ciu/secrets/hosts/<name>/` namespace convention your key files join),
   S14.4a-c (fail-closed `known_host` pinning, key-material-never-logged,
   the `[ADDR]:N` port form — properties step 2 must uphold, not
   reinvent).
3. `cmru/KNOWN_ISSUES_TODO_BACKLOG.md`'s `### KI-24` section (already
   FIXED, read for the ACTUAL shipped target-side contract — **one real
   deviation from what this proposal's own §4 describes**: the appended
   `authorized_keys` line is `from="PATTERN" <type> <base64> <comment>`
   (whitespace before the key type), NOT the proposal's `from="PATTERN",
   <type>...` (comma) — the comma form is rejected by real sshd. This
   package's own printed one-liner and any documentation quoting the key
   line format must use the CORRECT (whitespace) form. `cmru/CHANGES.md`'s
   `[Unreleased]` KI-24 entry has the measured evidence if you want it.
4. `src/ciu/hosts.py` — `load_hosts`/`get_host`/`get_host_secrets`, the
   EXISTING `.ciu.hosts.toml` reader. There is currently **no writer** —
   you are building the first one. Understand the read shape completely
   before designing the write shape; they must round-trip against each
   other.
5. `src/ciu/transport_ssh.py` — `resolve_key`, `_known_hosts_file`,
   `ssh_exec` — the EXISTING fail-closed `known_host` pinning
   (`CIU_SSH_INSECURE_TOFU` escape hatch, S14.4a) and how a pinned host
   key gets turned into a temp `known_hosts` file for one connection. Step
   2's own keyscan-and-pin logic should read as a natural extension of
   this file's existing conventions, not a parallel implementation.
6. `src/ciu/cli.py` — read enough to understand the verb dispatch shape:
   it is a FLAT `elif verb == "<name>":` chain (grep `elif verb ==`) — no
   existing two-word/grouped verb exists. The proposal's own §6 comparison
   table calls `ciu host enroll` **"a `host` group beside the flat
   `host-secrets`"** — that phrase is the authoritative resolution of the
   naming tension you'll otherwise have to guess at: this is a genuinely
   NEW dispatch shape (a `host` verb that itself takes a subcommand:
   `enroll`, and possibly `--abort` per S14.7a item 4), not a rename of
   `host-secrets` and not a third hyphenated flat verb. Design the
   smallest dispatch change that gets you `ciu host enroll <name> ...`
   and `ciu host enroll <name> --ssh-host ... --fingerprint ...` (steps 1
   and 2 are the SAME verb, disambiguated by which flags are present —
   `--ssh-host`/`--fingerprint` present means step 2, absent means step
   1) and `ciu host enroll <name> --abort`, working from argparse
   conventions already used elsewhere in this file (grep `add_parser`/
   `p.add_argument` near the `host-secrets` and `ssh` verb blocks for the
   local style).
7. `cmru.toml` (ciu's own, at repo root) — currently has a `[project]`
   table with `id = "ciu"` etc. but **no `[project.installer]` table**.
   KI-24's own adversarial reviewer flagged that `cmru get-py --project
   ciu` cannot render `ciu/get.py` today because this config doesn't
   exist yet — adding it is part of THIS package's own scope (S14.7a step
   1's printed one-liner names `ciu/get.py`; it must actually be
   buildable). Look at `cmru`'s own `docs/CONSUMERS.md` or a sibling
   project's `cmru.toml` (e.g. `tls-edge/cmru.toml`, which the KI-24
   reviewer used as their O6 stand-in) for the real shape of
   `[project.installer]` before inventing one.

## Work

### 1. `cmru.toml`: add `[project.installer]`, commit `ciu/get.py`

Add whatever `[project.installer]` shape `tls-edge/cmru.toml` (or cmru's
own docs) establishes, scoped to `ciu`'s own `repo_owner`/`repo_name`/
`tag_prefix` (already `ciu-v` per `[project]`). Render it
(`cmru get-py --project ciu`) and commit the result at `ciu/get.py` (a
NEW top-level file in this repo, alongside `README.md`/`CHANGES.md`) —
S14.7a step 1's printed one-liner names this exact file as a GitHub
release asset (`https://github.com/<owner>/<repo>/releases/download/
ciu-v<version>/get.py`), so it needs to exist and be committed now; it
becomes a real release asset automatically the next time ciu releases
(mirroring how `cmru`'s own release step already publishes wheel assets
— check whether `[project.release]`'s `artifact_dirs`/`build_step`
already covers a bare top-level file like this, or whether shipping it as
a release asset needs its own small addition there. If it does, that's
in scope too — the printed URL is load-bearing, not decorative).

### 2. Key generation — Step 1, `ciu host enroll <name> [...]`

Per S14.7a / proposal §3:
- `ssh-keygen -t ed25519 -f <repo>/.ciu/secrets/hosts/<name>/ssh_key -C
  "ciu@<controller>:<project>" -N ""` (empty passphrase — this key is
  meant for unattended `ciu ssh`/`ciu up --host` use, matching every
  other host key already in this namespace) — shell out to the real
  `ssh-keygen`, never a hand-rolled key generator. Directory `0700`, key
  `0600`, `.pub` world-readable (needed to print it).
- `--controller` resolution: `topology.external.public_fqdn` from config
  when declared, else the flag is REQUIRED — a missing value on both
  sides is a refusal (`[S14.7]`), never a guessed/default hostname
  (matches this whole codebase's "defaults are hazards" doctrine, `AGENTS.md`).
- Refuse (tagged `[S14.7]`) an already-enrolled `<name>` unless
  `--replace`; `--abort` removes a pending (step-1-only, not yet
  step-2'd) key pair.
- Print, and write NOTHING to `.ciu.hosts.toml` yet: the public key, the
  key file location, the target one-liner (using the CORRECTED
  whitespace `from=` form from Context item 3 if `--from` was given), and
  the step-2 completion command template.

### 3. Step 2 — `ciu host enroll <name> --ssh-host ADDR --fingerprint SHA256:...`

Per S14.7c / proposal §5:
- Keyscan (`ssh-keyscan -p <port> -t ed25519,ecdsa,rsa <ADDR>`), compute
  each returned key's SHA256 fingerprint (`ssh-keygen -lf` on the scanned
  key, or equivalent), refuse (`[S14.7]`, naming both fingerprints) unless
  one equals `--fingerprint`. Without `--fingerprint`: a TTY is asked to
  confirm the scanned fingerprint interactively; a non-TTY run without the
  flag is refused, never silently proceeds.
- Prove the login for real: connect as the target user with the generated
  key and the JUST-CONFIRMED scanned host key pinned for this ONE
  connection (reuse `transport_ssh.py`'s existing temp-known-hosts-file
  mechanism — do not write the real inventory's `known_host` value until
  AFTER this succeeds), run `ciu version` on the target. A refused login
  or a missing `ciu` binary is a tagged `[S14.7]` error naming which
  (S14.7c's own example message is a good model: "key accepted but ciu is
  not installed; run get.py enroll again without --no-install").
- **Only then** write the row: `[deploy.hosts.<name>]` with `ssh_host`,
  `ssh_user`, `ssh_port` (only when ≠ 22), `ssh_key =
  "<repo>/.ciu/secrets/hosts/<name>/ssh_key"`, `known_host = "<algo>
  <base64>"` (S14.4c's `[ADDR]:N` form for a non-default port). This is
  the round-trip write — see Work item 5 below for how.
- Print the written row and exit 0.

### 4. Refusal/security invariants — check every one against proposal §7

- Private key material is NEVER printed, logged, or included in any
  bundle — grep your own new code for anywhere a key's private half could
  reach stdout/stderr/a log line, and design the O5 oracle (below) to
  catch a regression here specifically.
- The printed installer URL is always version-pinned (the CONTROL host's
  own installed ciu version), never `latest` — `--installer-url`
  overrides it explicitly, nothing falls back to an unpinned URL.
- `CIU_SSH_INSECURE_TOFU` is NEVER set by this verb, under any flag
  combination — the whole point of step 2's fingerprint-confirmation flow
  is to be the SECURE alternative to that escape hatch, not a backdoor
  around it.
- The row write is a round-trip edit, not a whole-file rewrite: every
  OTHER table, key, and comment in `.ciu.hosts.toml` must survive
  byte-for-byte.

### 5. The round-trip TOML writer — a real design decision, make it and justify it

There is no existing round-trip-preserving TOML writer anywhere in this
codebase (`_worktree_overlay_text` in `worktree.py`, despite its
docstring's use of the word "round-trip", is a from-scratch sparse-
template renderer for a FRESH file, not an editor of an existing one —
confirm this yourself by reading it before assuming otherwise). Two real
options, pick one and say why in your REPORT:

1. **Add `tomlkit` as a new dependency.** It's built exactly for this
   (parse → mutate → dump while preserving comments/formatting/table
   order). Check whether it's already a transitive dependency of anything
   already installed (a cheaper option than a fresh top-level add) before
   deciding; if genuinely new, that's a real, disclosable dependency
   addition — name it in CHANGES.md.
2. **Targeted text surgery**, mirroring the `mdt-host-setup-wizard`
   package's own approach to a similar problem (a different file format —
   `KEY=value` lines, not nested TOML tables — so the TECHNIQUE transfers
   but the mechanics don't: you cannot use a simple per-key regex
   substitution against TOML's actual grammar without risking corruption
   on a file that already has a similarly-named table, a multi-line
   string, or an inline table). If you go this route, the oracle in Work
   item 6 (byte-for-byte survival of a hand-crafted, deliberately "hostile"
   fixture file — nested tables, comments, an existing `[deploy.hosts.*]`
   entry, unusual whitespace) needs to be adversarial enough to actually
   catch a corruption, not just a happy-path file.

Whichever you choose, the write must be ATOMIC (temp file + `os.replace`
in the same directory, matching this codebase's own established
convention elsewhere — e.g. how rendered instance files are written; grep
for `os.replace` precedent in `src/ciu/` and match it) — an interrupted
write must never leave `.ciu.hosts.toml` truncated or invalid.

### 6. Oracles (O1-O6 from the proposal §8, reproduced and made concrete)

Every rule from `nyxloom/reference/AUTHORING.md` §3b applies (no wall-clock
deadlines, no hollow tests, no order-dependence, no coverage-evasion
pragmas, no live network calls — a `docker run`/fixture container you
control is fine, a real GitHub download in a unit test is not; mock the
`get.py` download or point `--installer-url` at a local fixture path).

- **O1**: step 1 creates the key pair at the right paths with modes
  `0700`/`0600`; the printed output contains the exact public key text,
  the pinned installer URL, and the step-2 completion command; NO write
  to `.ciu.hosts.toml` happens (assert the file's mtime/content is
  unchanged, or that it doesn't exist yet in a fresh fixture). A second
  step-1 call for the same `<name>` without `--replace` is refused and
  makes no further filesystem change.
- **O2/O3**: these are cmru KI-24's OWN oracles, already satisfied by the
  shipped `get.py enroll` — you do not re-prove them here. What you DO
  need: a test that your printed one-liner (step 1's output), when
  actually run against a fixture container the same way KI-24's own
  `TestEnrollAgainstRealSystem` tests do, produces a host that step 2 can
  then successfully complete against — i.e. an END-TO-END oracle chaining
  step 1's output into a real `get.py enroll` run into step 2's own
  keyscan/login. This is the oracle that actually proves the two halves
  fit together, not just that each half individually works.
- **O4**: step 2 with a WRONG `--fingerprint` refuses and writes nothing
  (assert the file is byte-identical before/after); with the RIGHT one it
  writes EXACTLY the specified row, and every other table/comment in a
  hand-crafted fixture `.ciu.hosts.toml` survives byte-for-byte (the
  adversarial fixture from Work item 5); `ciu ssh <name> -- ciu version`
  then succeeds for real against the fixture host.
- **O5**: three controlled wrong implementations, each proven by actually
  reverting the fix and confirming the SPECIFIC oracle fails:
  1. Writing the row BEFORE the fingerprint check passes → O4's
     wrong-fingerprint case must fail (the row gets written when it
     shouldn't).
  2. Private key material reaching stdout/stderr/any log — grep oracle:
     run step 1 and step 2, grep all captured output for the private
     key's own first line; must never match.
  3. A whole-file rewrite instead of a round-trip edit → O4's
     byte-for-byte survival case must fail.
- **O6**: `cmru get-py --project ciu` (now that `[project.installer]`
  exists, Work item 1) renders a `get.py` whose `enroll --help` lists
  exactly cmru KI-24's own flag set, and the rendered output is
  byte-identical to the committed `ciu/get.py`.

## Scope / forbid

**Touch:** `src/ciu/cli.py` (the new `host` verb dispatch), a new module
for the enroll logic itself (your choice of name — check whether it fits
better as a new `src/ciu/host_enroll.py` or as additions to `hosts.py` —
say which and why), `src/ciu/hosts.py` (only if the round-trip writer
belongs there, alongside the existing reader), `docs/SPEC.md` (S14.7's own
section — correct the `from=` comma-vs-space text if it's wrong there
too, per Context item 3), `docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` (status
line only, once shipped — do not rewrite the design), `cmru.toml`
(`[project.installer]`), `ciu/get.py` (new, committed, rendered
artifact), `KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-93 → FIXED), `CHANGES.md`,
new test file(s) under `tests/tests/`.

**Forbid:** `cmru/` (any path — KI-24 already shipped; if you find a real
bug in the shipped `get.py.tmpl` enroll subcommand, that's a NEW backlog
entry against cmru, not a change you make here), `ciu8/`
(`SPEC-V8.md`/v8 line — this is the v7 backport ONLY, per D-012-style
operator scoping already established this session), anything under
`modern-debian-tools-python-debug/`, any push/activate/`bundle_dir`/
`docker_optional`/`[activate]` auto-configuration (proposal §10's own
explicit non-goals — enrollment ends at "ciu can SSH in, and ciu is
installed there", nothing more).

If a contract item above requires touching a file not listed in Touch, or
a named design decision (the round-trip writer approach, the `host` verb
dispatch shape) turns out to be unimplementable as specified against the
real current source — **STOP**.

**BLOCKED rule:** if a named contract cannot be met as specified, or scope
requires a forbidden file, STOP — write `BLOCKED: <reason>` to the LOG,
commit, and exit. Do NOT improvise a workaround.

## Process requirements

- Fresh implementer, zero prior context beyond this document, the full
  proposal doc, S14.1-S14.7, cmru's KI-24 entry, and the live repo.
- **Real gate required**: `./run-gate.py ciu` (`--worktree
  /workspaces/vbpub/.worktrees/ciu-p51-bundle`). Read the verdict in a
  separate step, never off a piped tail.
- Update CIU-93 to FIXED in `KNOWN_ISSUES_TODO_BACKLOG.md` with real
  file:line citations from your own diff.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P52-{LOG,REPORT}.md`.
- Checkpoint clause: ARM at ~120k context or ~60 tool calls (whichever
  first), CUT at the next coherent boundary, repeat every ~40-55 calls,
  stop when <~40 calls remain. At the cut: continuation brief to
  `nyxloom-trove/reports/ciu-P52-BRIEF.md` + a self-authored
  `/compact`-style retention prompt at
  `nyxloom-trove/reports/ciu-P52-COMPACT.md`, commit, stop. This package
  is large (comparable to ciu-P50) — expect to actually need this clause,
  don't treat it as boilerplate.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`. Do not release.** This is the 4th of 4 items
  the controller bundles into one combined merge + one ciu release once
  every part is independently reviewed ACCEPT.
- **Host is shared with a production game server** — 8 cores: serial
  pytest under nice/ionice, ONE gate/fixture container at a time across
  all agents, `docker update --cpus=3` right after launch, no builds
  concurrent with suites.
- Closing discipline: claim only what you ran, with real command output —
  especially O5's three controlled-wrong-implementation reversions and
  the end-to-end step-1-into-real-enroll-into-step-2 chain oracle.

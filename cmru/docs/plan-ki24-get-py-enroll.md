# Plan — `get.py`'s `enroll` subcommand (KI-24)

Status: carved, ready for implementation. Blocks `vbpub/ciu` CIU-93 (`ciu
host enroll`, `docs/SPEC.md` S14.7) — that package cannot start until this
one ships and releases, since its step 1 prints a one-liner that runs the
`ciu/get.py` this template renders.

Companion docs: `KNOWN_ISSUES_TODO_BACKLOG.md` KI-24 (the full contract this
plan packages — read it, this doc does not repeat every clause), `vbpub/ciu/
docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` rev 2 (the consuming design, §4 and
oracles O2/O3/O6 specifically), `docs/SPEC.md` (this repo's own normative
spec for `get.py.tmpl`'s existing subcommands — S3.1/S5/S6.5 are cited by
KI-24's own ordering requirements).

## Why

`get.py.tmpl` (the transactional installer every project renders via `cmru
get-py --project <name>`) has `install|update|status|rollback` today, all
for a host that already trusts the control side. Nothing lets a fresh,
untrusted host bootstrap itself so a controller CAN start trusting it —
that gap is what blocks ciu's own host-enrollment feature (CIU-93),
already spec'd and operator-accepted, waiting on this piece.

## Context to read first (in this order)

1. `KNOWN_ISSUES_TODO_BACKLOG.md` — search `### KI-24`. This is the actual
   contract: exact CLI shape, exact fail-fast ordering (prerequisites
   before any network I/O; install; user creation; key append; fingerprint
   print), the oracles (O2/O3/O6) with their controlled-wrong-implementation
   cases already named. Read it in full before writing code — this plan
   packages it, does not restate every clause.
2. `templates/get.py.tmpl` — read the WHOLE file once (1121 lines). Pay
   particular attention to: `check_prerequisites()` (~line 1032, the
   existing "before any network I/O" pattern KI-24's own prerequisite step
   must match), `do_install`/`do_update`/`do_status`/`do_rollback`
   (~lines 819-1031, the one-function-per-subcommand convention your new
   `do_enroll` follows), and `main()` (~line 1045 — the `add_subparsers`
   wiring, `EXIT_OK`/`EXIT_FAIL`/`EXIT_CONFIG`/`EXIT_PREREQ` constants
   already defined at the top of the file, ~line 86-89) and the `info`/
   `ok`/`warn`/`err`/`fatal` helper functions (~line 98-115) every existing
   subcommand uses for output — match that style exactly, don't invent a
   new one.
3. `src/cmru/getpy.py` — the Python-side driver that renders
   `get.py.tmpl` via `cmru get-py --project <name>` (Jinja or string
   templating — check which). This is what O6 exercises
   (`cmru get-py --project ciu` must render a script whose `enroll --help`
   lists exactly KI-24's flags).
4. `tests/test_installer.py` — the existing test file for
   `get.py.tmpl`-rendered behavior (`TestGenerator`, `TestPrerequisites`,
   `TestGetPyCLI`, `TestScopeResolution` classes especially). Most existing
   tests check STATIC properties of the rendered script (string contents,
   flag presence) rather than live execution — KI-24's O2 needs something
   new: actually RUNNING the rendered `enroll` subcommand against a real
   target (a container with `openssh-server`, per the oracle's own wording)
   and checking real effects (a created user, an appended
   `authorized_keys` line, a fingerprint that matches
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run independently).
   This repo has no existing "spin up a container, run the installer
   inside it, assert on real system state" test pattern that this plan's
   author could find — you may be building the first one. Look at how
   `run-gate.toml`'s own `tester-unified`/gate-container conventions work
   (host cgroup placement via `$CGROUP_PARENT_DEV_BACKGROUND`, see the
   root vbpub `AGENTS.md` "Host cgroup placement for spawned containers")
   before reaching for a raw `docker run` inside a test.
5. `docs/SPEC.md` — skim for how existing subcommands are documented (this
   repo's own doc convention) so `enroll`'s new normative text matches
   shape.

## Work

1. New `do_enroll(args)` function in `get.py.tmpl`, matching KI-24's exact
   contract:
   - CLI: `enroll --authorized-key 'KEY' --controller FQDN [--user USER]
     [--name NAME] [--from PATTERN] [--docker] [--no-install]
     [--scope system]` (default `--user ciu`, default `--scope system`).
   - Order, fail-fast, each step BEFORE any step that follows, idempotent
     on re-run:
     1. Prerequisites before any network I/O: Linux (existing
        `platform.system()` check in `main()` already covers this
        globally — confirm it runs before `enroll` dispatches, don't
        duplicate it), root/sudo, `sshd` present (`shutil.which("sshd")`
        or `/usr/sbin/sshd` — absent → `fatal(..., EXIT_PREREQ)` naming
        `openssh-server` explicitly in the message), the `--authorized-key`
        value parses as `<type> <base64>[ <comment>]` with `type` in
        `{ssh-ed25519, ecdsa-sha2-*, sk-*, ssh-rsa}` (else
        `EXIT_CONFIG`). The installer itself never installs system
        packages (that's the admin's job per the printed prerequisite
        message) — do not add an `apt install openssh-server` shortcut,
        that's explicitly out of scope per KI-24's own text ("never
        installs system packages").
     2. `install --scope system` (or the args-given `--scope`) run
        verbatim as the existing `do_install` already does — literally
        call it, don't reimplement transaction/manifest logic. Skipped
        entirely when `--no-install` is passed.
     3. `--user` (default `ciu`): create with
        `useradd --create-home --shell /bin/bash` if absent; leave
        untouched if already present (never modify an existing user's
        shell/home). `--docker`: add the user to the `docker` group;
        if that group doesn't exist, `fatal(..., EXIT_PREREQ)` — do not
        silently skip.
     4. `~USER/.ssh` created `0700`, `authorized_keys` `0600`, both owned
        by USER (not root). Append the key line ONCE:
        `from="PATTERN",<type> <base64> <comment>` when `--from` was
        given, else just `<type> <base64> <comment>`. Idempotency rule,
        exact: an IDENTICAL line already present → report it (not an
        error, not a duplicate append). A line with the SAME key material
        but DIFFERENT options (e.g. a different `from=` pattern, or one
        with `from=` and one without) → `fatal(..., EXIT_CONFIG)` — this
        is a real conflict, not a no-op.
     5. Print, in order: the SHA256 fingerprint of every
        `/etc/ssh/ssh_host_*_key.pub` present (`ssh-keygen -lf <path>` —
        shell out, parse its stdout, one line per host key type found;
        don't hand-roll SSH fingerprint computation), the addresses from
        `hostname -I` explicitly labelled UNCONFIRMED (this script cannot
        know which address the controller will actually reach it on), the
        user, the installed version (whatever `do_status`/`do_install`
        already expose for this), and the exact completion command:
        `ciu host enroll <NAME> --ssh-host <address> --fingerprint
        SHA256:<ed25519 fingerprint>` — `<NAME>` from `--name` if given,
        else an obvious placeholder token the operator must replace (don't
        guess a name).
   - `enroll` never: generates keys (the public key always arrives via
     `--authorized-key`, generated CONTROL-side, never here), calls
     anything back over the network, opens a listener, edits
     `sshd_config`, or runs any `bootstrap|apply|health|rollback` adapter
     verb — it is purely install + user + key + fingerprint-print. If you
     find yourself reaching for any of those, stop — that's scope creep
     this backlog entry explicitly ruled out (the withdrawn
     token/callback/self-hosted-backend alternative).

2. Wire it into `main()`: new `p_enroll = subparsers.add_parser("enroll",
   ...)` with the flags above, `elif args.command == "enroll": do_enroll(args,
   token)` in the dispatch block — matching the existing four subcommands'
   pattern exactly (~line 1097-1114).

3. `--help` text: `enroll --help` must list exactly the flags in KI-24's
   proposed contract (this is O6's own assertion — get the flag set and
   their help strings right, since a test will diff against them).

## Oracles (from KI-24, reproduced here so nothing is missed)

- **O2** (the real-execution oracle): in a fixture container with
  `openssh-server` installed, running rendered `get.py enroll
  --authorized-key '<test key>' --controller test.example --user
  <testuser>`:
  - creates the user,
  - appends the key exactly once (re-running the identical command a
    second time leaves exactly one matching line and reports it, not an
    error),
  - sets `.ssh` to `0700` and `authorized_keys` to `0600`, both owned by
    the user,
  - prints a fingerprint that equals independently running
    `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the SAME
    container.
- **O3**: on a target with NO SSH server present, `enroll` exits
  `EXIT_PREREQ`, names `openssh-server` in its message, and performs ZERO
  network I/O (assert this the same way `check_prerequisites()`'s existing
  tests already do — no `urllib`/`socket` call reached).
- **O6**: `cmru get-py --project ciu` renders a script whose
  `enroll --help` output lists exactly the flags in this plan's Work
  item 1 CLI line — byte-comparable to what actually ships as `ciu/get.py`
  and its release asset (this repo's existing `TestGetPyCLI`/`TestGenerator`
  classes are the model for this kind of assertion — extend them, don't
  build a parallel mechanism).
- **Controlled wrong implementations, from KI-24 itself — actually run
  these, don't just describe them:**
  - An implementation that appends the key BEFORE the prerequisite checks
    must fail O3 (prove it: temporarily reorder the code, confirm O3's
    test now fails, then revert).
  - An implementation that duplicates the key line on re-run must fail O2
    (prove it: temporarily remove the idempotency check, confirm O2's
    re-run assertion now fails, then revert).
- Every rule from `nyxloom/reference/AUTHORING.md` §3b applies to any new
  test you write (no wall-clock deadlines deciding pass/fail, no test-order
  dependence, no hollow "nothing raised" assertions, no coverage-evasion
  pragmas, no live network calls in a unit test — O2/O3 are container-based
  integration tests by nature, which is fine, but keep them hermetic: a
  fixture container YOU control, not a live host).

## Scope / forbid

**Touch:** `templates/get.py.tmpl` (the `enroll` subcommand only — do not
touch `install`/`update`/`status`/`rollback`'s own logic, only add
alongside them), `src/cmru/getpy.py` only if rendering genuinely requires
a change (it may not — check before assuming), `tests/test_installer.py`
(new test classes/methods for `enroll`), `KNOWN_ISSUES_TODO_BACKLOG.md`
(flip KI-24 to FIXED at the end, with real file:line citations from your
diff), `CHANGES.md` (new capability, `feat(cmru):` framing — check for the
`- UNRELEASED` fold-in gap this repo's own history has hit before adding).

**Forbid:** `vbpub/ciu/` (any path — that's the CONSUMING package,
CIU-93, a separate follow-up that starts only after this one ships and
releases; do not get ahead of it or pre-write its code here), any
token/callback/self-hosted-download-backend mechanism (explicitly withdrawn
by the operator per KI-24's own text — do not resurrect it "to be safe"),
`docs/RELEASE-TRANSACTIONS.md` (not this package's concern).

If a contract item above requires touching a file not listed in Touch, or
turns out to need the withdrawn token/callback mechanism to work at all —
**STOP**.

**BLOCKED rule:** if a named contract cannot be met as specified, or scope
requires a forbidden file, STOP — write `BLOCKED: <reason>` to your LOG,
commit, and exit. Do NOT improvise a workaround.

## Process requirements

- Fresh implementer, zero prior context beyond this document, KI-24's full
  backlog text, and the live repo.
- Real gate: `./run-gate.py gate` (the conjunction lane; `--worktree
  <path>` if in an isolated worktree). Read the verdict in a separate
  step, never off a piped tail.
- LOG/REPORT: `docs/reviews/KI-24-{LOG,REPORT}.md` (matching this repo's
  own `docs/reviews/` convention, e.g. `KI-12-16-review.md`) — LOG per
  commit, REPORT with per-oracle evidence including the two controlled-
  wrong-implementation runs' actual output.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge.
- **Host is shared with a production game server** — 8 cores, serial work
  under nice/ionice, ONE gate/fixture container at a time across all
  agents, `docker update --cpus=3` right after launch, no builds
  concurrent with suites. This applies doubly here since O2's fixture
  container is itself new infrastructure — don't leave it running after
  the test completes.
- Closing discipline: claim only what you ran, with the real command
  output from both controlled-wrong-implementation reversions.

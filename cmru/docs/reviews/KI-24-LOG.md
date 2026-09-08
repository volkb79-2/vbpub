# KI-24 — `get.py enroll` — implementation LOG

Branch `cmru-ki24-get-py-enroll`, worktree
`/workspaces/vbpub/.worktrees/cmru-ki24-get-py-enroll`. Package contract:
`cmru/docs/plan-ki24-get-py-enroll.md` + `KNOWN_ISSUES_TODO_BACKLOG.md` `### KI-24`.

## Commit 1 — `feat(cmru): get.py enroll`

**Read first, in the plan's order:** `KNOWN_ISSUES_TODO_BACKLOG.md` KI-24 in
full; `templates/get.py.tmpl` (all 1121 lines pre-change); `src/cmru/getpy.py`;
`tests/test_installer.py`; `docs/SPEC.md` conventions; plus `run-gate.toml`
(project + repo-root), `assay.toml` and the root `AGENTS.md` sections on the
cockpit doctrine and host cgroup placement.

**Touched (all inside the plan's Touch list):**

- `templates/get.py.tmpl` — new `enroll` subcommand only; `install`/`update`/
  `status`/`rollback` logic untouched (the only shared edits are two additive
  lines: a usage stanza in the module docstring and an epilog example).
  - imports: `grp`, `pwd` (stdlib, Linux-only — the script already refuses
    non-Linux in `main()`).
  - new helper section `# ─── Host enrollment helpers` (L824-L1160):
    `_is_ssh_key_type`, `_parse_authorized_key`, `_ak_split_line`,
    `_build_key_line`, `_find_sshd`, `_enroll_check_prerequisites`,
    `_enroll_ensure_user`, `_enroll_install_key`, `_host_key_fingerprints`,
    `_host_addresses`.
  - `do_enroll` (L1373) directly after `do_status`, following the
    one-function-per-subcommand convention.
  - `p_enroll` parser (L1540) and the `elif args.command == "enroll"` dispatch
    (L1572), matching the existing four subcommands' shape exactly.
- `src/cmru/getpy.py` — **not changed.** Checked before assuming: rendering is
  `str.replace` over `[[VARNAME]]` placeholders, so a new subcommand needs no
  emitter change. The plan allowed a change "only if rendering genuinely
  requires" one; it does not.
- `tests/test_installer.py` — eight new classes (L1373-L2290), see REPORT.
- `KNOWN_ISSUES_TODO_BACKLOG.md` — KI-24 flipped to FIXED with file:line
  citations and the contract correction recorded.
- `CHANGES.md` — new `### Added` bullet under the existing `## [Unreleased]`
  section. Checked for the `- UNRELEASED` fold-in gap this repo has hit before
  (cmru KI-23): there is no `- UNRELEASED` heading anywhere in the file, only a
  well-formed `## [Unreleased]`, so nothing to fold.
- `docs/reviews/KI-24-{LOG,REPORT}.md` — this file and the report, per the
  plan's Process section.

**Deliberately NOT touched:** `vbpub/ciu/` (any path — forbidden; CIU-93 is the
separate consuming package), `docs/RELEASE-TRANSACTIONS.md` (forbidden),
`docs/SPEC.md` (read for convention per the plan's context list, but absent from
the Touch list, so left alone — see REPORT "Disclosures"), `run-gate.toml`
(absent from the Touch list; this constrained the O2/O3 test design — see
REPORT), and any token/callback/self-hosted-download-backend mechanism (the
withdrawn design; nothing here reaches the network at all).

**One deviation from the literal contract, with measured cause.** KI-24 and the
plan both spell the restricted authorized_keys line
`from="PATTERN",<type> <base64> <comment>`. OpenSSH separates the options field
from the key by WHITESPACE; sshd reads everything up to the first unquoted
whitespace as options, so the comma-joined form buries the key type in the
options and public-key auth fails. Measured against a real sshd in the fixture
container before deciding (both forms, same key, same container): comma form →
`Permission denied (publickey)`; whitespace form → authenticated. The installer
writes the whitespace form; `_ak_split_line` still recognises the comma form on
READ so a pre-existing malformed entry is treated as a conflict rather than
silently duplicated. Recorded in `_build_key_line`'s docstring, in the test that
pins it, in KI-24's own entry, in CHANGES.md, and in the REPORT. This is the one
place the letter of the contract was not followed; it is flagged for the
reviewer rather than routed around silently, because writing the specified form
would have shipped an enrollment that reports success and leaves a host the
controller can never log into.

**Controlled wrong implementations — both actually run, both reverted.** See
REPORT §"Controlled wrong implementations" for the real command output.

**Gate:** `./run-gate.py --worktree <path> gate`, verdict read in a separate
step. Result in the REPORT.

# ciu-P52 — implementer LOG (CIU-93, `ciu host enroll`)

Branch `ciu-p51-bundle`, worktree `/workspaces/vbpub/.worktrees/ciu-p51-bundle`.
Item 4 of 4 in the operator's combined bundle. **Not merged, not released** —
per the handoff's Process section, the controller does one combined merge and
one ciu release after this part is independently reviewed ACCEPT.

## 1. Orientation (read before writing any code)

Read in the handoff's own order:

1. `docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` (all 109 lines, revision 2).
2. `docs/SPEC.md` S14 in full — S14.1 through S14.7d (`docs/SPEC.md:2579-2836`).
3. cmru's `KNOWN_ISSUES_TODO_BACKLOG.md` `### KI-24` (read-only; **cmru/ was
   never written to**). Confirmed the shipped contract AND its own measured
   deviation: `from="PATTERN" <type> <base64> <comment>` — whitespace, not a
   comma.
4. `src/ciu/hosts.py` (139 lines at the time — reader only, no writer).
5. `src/ciu/transport_ssh.py` (`resolve_key`, `_known_hosts_file`, `ssh_exec`).
6. `src/ciu/cli.py` verb dispatch, `_USAGE`, `_VERB_HELP`, `_worktree`.
7. `cmru.toml` (ciu's own) + `tls-edge/cmru.toml` + cmru `docs/SPEC.md` S2/S6
   for the real `[project.installer]` shape.

Two premises in the handoff were checked against the live source rather than
taken on trust; one held and one did not — both are written up in the REPORT.

## 2. Work order actually followed

1. **Work item 1** — `cmru.toml` `[project.installer]` + `[steps.push]
   --extra-asset get.py`; rendered and committed `ciu/get.py`.
2. **Work item 5 first, then 2/3** — the round-trip writer was built and
   smoke-tested against a hostile fixture BEFORE the enroll flow, because
   step 2 is meaningless without it and the writer was the only genuinely
   open design question.
3. **Work item 2/3** — `src/ciu/host_enroll.py` (new module).
4. **The verb** — `_host` in `src/ciu/cli.py`, plus `_USAGE` and `_VERB_HELP`
   entries and the `_extract_define_root` docstring's site list.
5. **Work item 6** — `tests/tests/test_ciu_host_enroll.py`.
6. Docs (SPEC S14.7b, proposal status line), `CHANGES.md`, backlog
   (CIU-93 → FIXED, CIU-99 filed).
7. Gate.

## 3. Notable events, in order

* **`tomlkit` probe (the Work item 5 decision).** `python3 -c "import
  tomlkit"` → `ModuleNotFoundError` on the devcontainer interpreter, and
  `docker run --rm tester-unified:local /opt/tester-venv/bin/python -c "import
  tomlkit"` → `ModuleNotFoundError` too. That second result decided it: the
  gate's own image would not be able to import a new hard dependency, and that
  image's venv is built from cmru — a forbidden path. Chose targeted text
  surgery. Full reasoning in the REPORT.
* **`cmru get-py` could not run from the installed cmru.** `cmru.getpy.
  _TEMPLATE_PATH` resolves to `<site-packages>/../../templates/get.py.tmpl`,
  which does not exist for an installed wheel, because cmru's
  `[tool.setuptools.package-data]` packages only `templates/*.toml`. Rendered
  from the cmru SOURCE checkout instead (`PYTHONPATH=/workspaces/vbpub/cmru/src`).
  This is a real cmru packaging defect; **not filed by me** — writing to
  `cmru/` is forbidden by this package's scope. Handed to the controller in
  the REPORT's "for the controller to file" section.
* **A stray write into `/workspaces/dstdns`.** A first manual CLI smoke test
  was run from `test-repo/` without `--define-root`, so `_resolve_repo_root_
  deploy` picked up this shell's ambient `$REPO_ROOT` (hardcoded to dstdns —
  a known devcontainer fact) and `ssh-keygen` wrote a key pair to
  `/workspaces/dstdns/.ciu/secrets/hosts/rs1002/`. Removed immediately
  (`rm -rf` of that host directory + `rmdir` of the now-empty
  `.ciu/secrets/hosts`); verified `/workspaces/dstdns/.ciu/secrets/` contains
  only its two pre-existing entries (`registry_auth_password`,
  `registry_http_secret`). Every later CLI test pins `REPO_ROOT` or passes
  `--define-root`. Recorded because it happened, not because it survived.
* **Four real bugs found by my own first test run**, all in the new code:
  1. `_without_path` did not prune the intermediate tables the write itself
     creates, so appending the FIRST row to a file with no `[deploy]` table
     tripped the verifier's own "something outside this row changed" refusal.
     Fixed by pruning empty ancestors, symmetrically on both sides.
  2. `_verify_round_trip` treated a deliberately dropped managed key
     (`ssh_port` going back to 22 on a rotation) as "the operator's own key
     would be lost". Fixed by exempting `ENROLL_ROW_KEYS`.
  3/4. Two test-side expectation errors (a miscounted header total; a fixture
     that ran step 1 for the wrong host name).
* **End-to-end oracle built and run for real** against a container it builds
  itself (`python:3.11-slim` + `openssh-server`, `ssh-keygen -A`, sshd in the
  foreground). Devcontainer→container-IP reachability was probed first
  (`ping` OK, TCP connect refused on a closed port = routable). The container
  is `docker update --cpus=1`-capped the moment it starts, per the shared-host
  rule.
* **Gate**: `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/
  ciu-p51-bundle`. First attempt refused a dirty tree ("commit or pass
  --allow-dirty"), so the implementation was committed first and the gate run
  against the commit. Verdict read in a separate step (LESSONS L4), recorded
  in the REPORT.

## 4. Host-load discipline

Load average was 5.68 on 8 cores at gate start, with several other agents'
stacks live. Checked `docker ps --filter ancestor=tester-unified:local` for a
peer gate container first (none), started ONE, and `docker update --cpus=3`'d
it immediately. Every local pytest run was `nice -n 10 ionice -c3`. No image
build ran concurrently with a suite.

## 5. Scope

Touched exactly the files the handoff's **Touch** list allows:
`src/ciu/cli.py`, a new `src/ciu/host_enroll.py`, `src/ciu/hosts.py`,
`docs/SPEC.md`, `docs/CIU-HOST-ENROLLMENT-PROPOSAL.md` (status/corrections
header only — the design body is unchanged), `cmru.toml`, the new `get.py`,
`KNOWN_ISSUES_TODO_BACKLOG.md`, `CHANGES.md`, one new test file. Nothing under
`cmru/`, `ciu8/` or `modern-debian-tools-python-debug/` was read-modified or
written. No push/activate/`bundle_dir`/`docker_optional`/`[activate]`
auto-configuration was added (proposal §10).

**No BLOCKED condition was hit.** The one contract item that could not be
fully satisfied — a `get.py install` that resolves a real ciu release asset —
is not a contract item of this package (it is release machinery, and the
handoff's own Work item 1 asked only that the printed URL be real, which it
is); it is filed as CIU-99 and documented in SPEC S14.7b rather than hidden.

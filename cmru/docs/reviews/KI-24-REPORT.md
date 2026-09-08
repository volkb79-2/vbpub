# KI-24 — `get.py enroll` — implementation REPORT

Package: `cmru/docs/plan-ki24-get-py-enroll.md` (contract:
`KNOWN_ISSUES_TODO_BACKLOG.md` `### KI-24`).
Branch `cmru-ki24-get-py-enroll`, worktree
`/workspaces/vbpub/.worktrees/cmru-ki24-get-py-enroll`. Not merged, not pushed.

---

## 0. Read this first — the one deviation from the literal contract

KI-24 and the plan both specify the restricted authorized_keys line as

```
from="PATTERN",<type> <base64> <comment>
```

**That form does not work.** OpenSSH separates the options field from the key by
WHITESPACE. sshd's reader advances past the options to the first *unquoted
whitespace*, so in `from="*",ssh-ed25519 AAAA… c@h` the options field is taken
to be `from="*",ssh-ed25519` and what remains (`AAAA… c@h`) is not a key. The
entry is dead: enrollment reports success and the controller can never log in.

This was not reasoned about, it was measured — both forms, same generated key,
same live sshd, same container:

```
=== FORM A: comma-joined  from="...",<type> ===
deployer@127.0.0.1: Permission denied (publickey).
=== FORM B: whitespace-separated  from="..." <type> ===
AUTH_OK_B
```

(`docker run` of the fixture image; `useradd deployer`; `ssh-keygen -t ed25519`;
`/usr/sbin/sshd -p 2222`; the same public key written first in form A then in
form B; `ssh -o BatchMode=yes -i /root/id_test -p 2222 deployer@127.0.0.1`.)

So `_build_key_line` writes `from="PATTERN" <type> <base64> <comment>`.
`_ak_split_line` still **recognises the comma form on READ**, so a pre-existing
malformed entry is detected as a same-key conflict rather than silently
duplicated beside a new one.

This is the single place where the letter of the contract was not followed. It
is flagged here, in `_build_key_line`'s docstring, in the test that pins it
(`test_line_with_from_separates_options_by_whitespace_not_comma`), in KI-24's
own backlog entry and in `CHANGES.md` — deliberately not routed around quietly.
**Consumers that quote KI-24's line need the same correction** — ciu CIU-93 /
`SPEC.md` S14.7 above all, which is the package that follows this one.
`vbpub/ciu/` is forbidden scope here, so nothing there was edited.

An oracle now pins the real property behind the deviation:
`test_written_key_actually_authenticates_against_a_real_sshd` enrolls a
freshly-generated key with `--from 127.0.0.1`, starts a real sshd in the fixture
container and logs in with it.

---

## 1. What was built

`templates/get.py.tmpl` — a fifth subcommand alongside
`install|update|status|rollback`, whose own logic is untouched.

```
get.py [--manifest-pubkey …] enroll --authorized-key 'KEY' --controller FQDN
       [--user USER] [--name NAME] [--from PATTERN] [--docker]
       [--no-install] [--scope system]
```

`enroll --help` (verbatim, from the rendered script):

```
usage: get.py enroll [-h] --authorized-key KEY --controller FQDN [--user USER]
                     [--name NAME] [--from PATTERN] [--docker] [--no-install]
                     [--scope {system,user}]
```

| citation | what |
| --- | --- |
| `templates/get.py.tmpl:824` | `# ─── Host enrollment helpers` section header |
| `:849` `_parse_authorized_key` | `<type> <base64>[ <comment>]`, types `ssh-ed25519 / ecdsa-sha2-* / sk-* / ssh-rsa`, else `EXIT_CONFIG` |
| `:895` `_ak_split_line` | quote-aware options/key split, tolerant of the comma form on read |
| `:950` `_build_key_line` | the line written (carries the deviation's evidence in its docstring) |
| `:970` `_find_sshd` | `shutil.which("sshd")` then `/usr/sbin/sshd` |
| `:979` `_enroll_check_prerequisites` | root → sshd → key parse, all before any later step |
| `:1004` `_enroll_ensure_user` | docker-group check first, then `useradd --create-home --shell /bin/bash` only when absent |
| `:1061` `_enroll_install_key` | 0700/0600 + ownership, append once, conflict = `EXIT_CONFIG` |
| `:1125` `_host_key_fingerprints` | shells out to `ssh-keygen -lf`, never hand-rolls a fingerprint |
| `:1148` `_host_addresses` | `hostname -I`, reported UNCONFIRMED |
| `:1373` `do_enroll` | the five ordered steps |
| `:1540` `p_enroll` / `:1572` dispatch | wired exactly like the existing four |

`src/cmru/getpy.py`: **unchanged**. Checked rather than assumed — the emitter is
`str.replace` over `[[VARNAME]]` placeholders (`getpy.py:98-119`), so a new
subcommand needs no emitter change. The plan permitted a change only if
rendering genuinely required one; it did not.

Not in the code, on purpose (KI-24's own exclusions): no key generation, no
network callback, no listener, no `sshd_config` edit, no adapter verb, no
`apt install`. `enroll`'s only network path is the one `do_install` already
owns, and `--no-install` removes even that. Nothing from the withdrawn
token/callback/self-hosted-download-backend design appears anywhere.

---

## 2. Oracles

### O2 — real execution, real system state

`tests/test_installer.py::TestEnrollAgainstRealSystem::test_o2_user_key_modes_ownership_and_fingerprint`,
against a fixture container (`cmru-enroll-fixture:local`:
`debian:bookworm-slim` + `openssh-server python3 iproute2 hostname passwd` +
`ssh-keygen -A`) built and torn down by the test file itself. Asserted, all off
the real filesystem in that container:

- `id -u deployer` succeeds — the user was really created;
- `authorized_keys` holds **exactly one** matching line;
- `stat -c '%a %U %G %n'` returns
  `700 deployer deployer /home/deployer/.ssh` and
  `600 deployer deployer /home/deployer/.ssh/authorized_keys`;
- the printed fingerprint equals `ssh-keygen -lf
  /etc/ssh/ssh_host_ed25519_key.pub` run **independently in the same
  container**, and that same token appears in the printed `--fingerprint …`;
- every address from `hostname -I` appears in the output, under `UNCONFIRMED`;
- a second, identical run exits 0, leaves **exactly one** line, and says
  `not duplicated` / `already exists`.

Verbatim from a real run of the rendered installer inside that container:

```
───────────────────────────────────────────────────────────────────────────────
  demo  enroll  user=deployer  controller=test.example  scope=system
───────────────────────────────────────────────────────────────────────────────
  ok SSH server present: /usr/sbin/sshd
  ok Authorized key parsed (ssh-ed25519).
==> --no-install: skipping the install step.
==> Creating deploy user deployer ...
  ok User deployer created (home /home/deployer, shell /bin/bash).
  ok Key appended to /home/deployer/.ssh/authorized_keys.
───────────────────────────────────────────────────────────────────────────────
SSH host key fingerprints (confirm these out of band):
  /etc/ssh/ssh_host_ecdsa_key.pub: 256 SHA256:BtmQN3ko1vdnfCYET/ayAhRTAeGIgmc+6PVXR4m4iRM root@buildkitsandbox (ECDSA)
  /etc/ssh/ssh_host_ed25519_key.pub: 256 SHA256:a03wCm3aQmYAGmTU6CL/SpdWTws3ma7nX4voXRkqbwA root@buildkitsandbox (ED25519)
  /etc/ssh/ssh_host_rsa_key.pub: 3072 SHA256:w906Q2LX/OFOS5+7ZRBYXO9nronHtGBaEjNu4Mo3QYE root@buildkitsandbox (RSA)
Addresses (UNCONFIRMED — this host cannot know which one the controller reaches it on):
  <ADDRESS>
User:      deployer
Home:      /home/deployer
Keys:      /home/deployer/.ssh/authorized_keys
Installed: (not installed)
Controller: test.example
───────────────────────────────────────────────────────────────────────────────
Finish enrollment on the CONTROL host with:
  ciu host enroll <NAME> --ssh-host <ADDRESS> --fingerprint SHA256:a03wCm3aQmYAGmTU6CL/SpdWTws3ma7nX4voXRkqbwA
  (replace <NAME> — enroll does not invent a host name; pass --name to have it filled in)
  (replace <ADDRESS> with the address the controller will actually use)
───────────────────────────────────────────────────────────────────────────────
  ok Host enrolled for test.example.
```

Independent check in the same container:
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` →
`256 SHA256:a03wCm3aQmYAGmTU6CL/SpdWTws3ma7nX4voXRkqbwA root@buildkitsandbox (ED25519)`
— the same token the script printed. `stat`:
`700 deployer:deployer /home/deployer/.ssh`,
`600 deployer:deployer …/authorized_keys`. Second run:
`ok Key already present in /home/deployer/.ssh/authorized_keys — not duplicated.`
and `wc -l` = 1.

(That transcript is from a `--network none` container, which is why the address
list is the placeholder; the pytest oracle runs on the default bridge and
asserts the real addresses instead.)

Also container-backed, same class:

- `test_o2_from_pattern_written_and_conflicting_options_refused` — `--from`
  writes `from="10.0.0.0/8" <key>`; an identical re-run is a no-op; the same key
  under `--from 192.168.0.0/16` exits **2** with `DIFFERENT options` and leaves
  one line; dropping `--from` entirely is the same refusal, not a silent append.
- `test_written_key_actually_authenticates_against_a_real_sshd` — the whole
  point of the subcommand, proved end to end.
- `test_docker_flag_refused_when_the_group_is_absent` — exit 3, and **no user
  created** (refused before `useradd`).
- `test_docker_flag_adds_the_user_when_the_group_exists` — `id -nG deployer`
  contains `docker`.
- `test_existing_user_is_left_untouched` — a pre-existing `deployer` with
  `/bin/sh` still has `/bin/sh` afterwards.

### O3 — no SSH server ⇒ `EXIT_PREREQ`, zero network I/O, zero state change

Two levels, because "no network was reached" and "nothing was written" are
different claims:

- `TestEnrollAgainstRealSystem::test_o3_no_ssh_server_exits_prereq_and_changes_nothing`
  — fixture container on `--network none` with `/usr/sbin/sshd` removed: exit
  **3**, `openssh-server` named in stderr, **and** `id -u deployer` fails and
  `/home/deployer` does not exist. The container has no network at all, so a run
  that completes here reached none.
- `TestEnrollOrdering::test_missing_ssh_server_reaches_no_network_call` — in
  process, `urllib.request.build_opener` and `urlopen` are replaced with
  raisers, exactly as `check_prerequisites()`'s existing tests assert absence:
  the call is not merely unobserved, it would explode.
- `TestEnrollOrdering::test_missing_ssh_server_exits_prereq_names_openssh_server`
  / `test_non_root_exits_prereq_before_anything_else` /
  `test_unparsable_key_exits_config_before_install` — every later step
  (`do_install`, `_enroll_ensure_user`, `_enroll_install_key`, `_gh_request`,
  `resolve_latest_tag`, `_host_key_fingerprints`) is replaced by a tripwire that
  fails the test if reached.

### O6 — `cmru get-py` renders a script whose `enroll --help` lists exactly these flags

`TestEnrollCLIShape`:

- `test_enroll_help_lists_exactly_the_contract_flags` — the flag set parsed out
  of a real `python3 get.py enroll --help` **equals** (`==`, not `⊇`)
  `{-h, --help, --authorized-key, --controller, --user, --name, --from,
  --docker, --no-install, --scope}`, checked on the usage line and on the whole
  help text. An extra flag fails it just as a missing one does.
- `test_enroll_required_flags_are_required` — `--authorized-key` / `--controller`
  unbracketed, the rest bracketed.
- `test_enroll_defaults_documented` — `default: ciu`, `default: system`,
  `{system,user}`.
- `test_get_py_cli_render_carries_enroll` — same assertion through the real
  `cmru get-py --project … --config … --output …` entry point.
- `test_real_project_config_renders_enroll` — same assertion against the one
  real `[project.installer]` this monorepo ships (`tls-edge/cmru.toml`).
- `test_top_level_help_lists_enroll_beside_the_others`,
  `test_main_dispatches_enroll_with_the_parsed_args_and_token`,
  `test_enroll_defaults_when_only_required_flags_given`.

**Deviation from O6's literal wording:** it names `cmru get-py --project ciu`.
There is no `ciu` project with a `[project.installer]` section in this repo
(`ciu/cmru.toml` has none), and `vbpub/ciu/` is forbidden scope for this
package, so adding one here was not an option. The assertion is made through the
same `render_from_config` code path instead, on both a fixture config and the
one real installer config that exists. That path is exactly what
`cmru get-py --project ciu` will execute once CIU-93 adds the section.

---

## 3. Controlled wrong implementations — both actually run

### (a) key appended BEFORE the prerequisite checks ⇒ O3 must fail

Reversion: `do_enroll` was edited to run `_parse_authorized_key` →
`_enroll_ensure_user` → `_enroll_install_key` **above** the
`_enroll_check_prerequisites(args)` call. Real output:

```
FAILED tests/test_installer.py::TestEnrollOrdering::test_missing_ssh_server_exits_prereq_names_openssh_server
FAILED tests/test_installer.py::TestEnrollOrdering::test_missing_ssh_server_reaches_no_network_call
FAILED tests/test_installer.py::TestEnrollOrdering::test_non_root_exits_prereq_before_anything_else
FAILED tests/test_installer.py::TestEnrollAgainstRealSystem::test_o3_no_ssh_server_exits_prereq_and_changes_nothing
============ 4 failed, 1 passed, 139 deselected, 1 warning in 1.81s ============
```

with the container oracle showing the real damage — the run still exited 3 and
still named `openssh-server`, but the user had been created anyway:

```
            # and it did NOT do any of the later steps
>           assert _cexec(c, "id", "-u", "deployer").returncode != 0
E           AssertionError: assert 0 != 0
E            +  where 0 = CompletedProcess(args=['docker', 'exec', '865f79b6…', 'id', '-u', 'deployer'],
                                           returncode=0, stdout='1000\n', stderr='').returncode
```

(Note what this proves about the oracle: exit code alone does **not** catch this
wrong implementation — it still exits 3. The state assertion is what catches it.)

Reverted; `do_enroll` restored.

### (b) idempotency check removed ⇒ O2 must fail

Reversion: in `_enroll_install_key`, the `already_present = True` branch was
flipped to `False`, so a matching key is found but re-appended. Real output:

```
>           assert [ln for ln in keys_again.stdout.splitlines() if ln.strip()] == [
E           AssertionError: assert ['ssh-ed25519...trol.example'] == ['ssh-ed25519...trol.example']
E             Left contains one more item: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ4tOoyRAvbBiHFB5zFCFtOijSMxU9BzM3sB0K9RRppQ ciu@control.example'
FAILED tests/test_installer.py::TestEnrollAgainstRealSystem::test_o2_user_key_modes_ownership_and_fingerprint
```

and, on the first pass of this reversion (before the assertions in that test
were reordered to put file state ahead of the printed message), both O2
container tests failed:

```
FAILED tests/test_installer.py::TestEnrollAgainstRealSystem::test_o2_user_key_modes_ownership_and_fingerprint
FAILED tests/test_installer.py::TestEnrollAgainstRealSystem::test_o2_from_pattern_written_and_conflicting_options_refused
============ 2 failed, 5 passed, 137 deselected, 1 warning in 9.23s ============
```

Reverted; `grep -n "already_present = " templates/get.py.tmpl` confirms
`already_present = False` at the initialiser (L1089) and `already_present = True`
in the match branch (L1106).

---

## 4. New test infrastructure (this is new, review it as such)

The plan's author could find no "spin up a container, run the installer for
real, assert on real system state" pattern in this repo. There was none; this is
the first. Design choices, all in `tests/test_installer.py:1987-2095`:

- **Fixture image owned by the test file.** `ENROLL_FIXTURE_DOCKERFILE` is
  inline; a session fixture builds `cmru-enroll-fixture:local` only if absent
  and **skips** (never fails) if the build cannot run. No live host is ever
  touched.
- **Cgroup placement, no hardcoded fallback.** Containers run with
  `--cgroup-parent=$CGROUP_PARENT_DEV_BACKGROUND` per root `AGENTS.md`. If that
  variable is unset the whole group **skips** rather than launching an unplaced
  container next to production — the doctrine's "refuse to launch, never fall
  through to Docker's unconfined default".
- **Shared-host budget.** `--cpus=3 --memory=1g --memory-swap=4g`, one container
  at a time (these tests are serial; the cmru lanes run pytest without xdist).
- **Teardown is unconditional.** `_enroll_container` is a context manager whose
  `finally` runs `docker rm -f`; a failing assertion cannot leak a container.
  Verified after every run: `docker ps -a --filter ancestor=cmru-enroll-fixture:local`
  is empty.
- **`docker cp`, not a bind mount.** The rendered script is copied in. A bind
  mount would be wrong here: this devcontainer talks to the *host* daemon, so a
  container-local path like `/tmp/…` does not exist host-side and Docker
  silently creates an empty directory over it (observed during development:
  `can't find '__main__' module in '/tmp/get.py'`).
- **Skips inside the gate.** `tester-unified` has no docker socket mounted (only
  `$RUN_GATE_EXTRA_MOUNTS` could add one, and `run-gate.toml` is outside this
  package's Touch list), so under `./run-gate.py gate` these tests report as
  skipped, with the reason printed. They were run for real from the cockpit,
  which is where the evidence above comes from. **This is a disclosure, not a
  claim of gate coverage** — see §6.

---

## 5. Gate

Command (from `cmru/`):

```
./run-gate.py --worktree /workspaces/vbpub/.worktrees/cmru-ki24-get-py-enroll gate
```

Verdict read in a separate step, never off a piped tail — the run was detached
to a log file and the verdict lines grepped out of it afterwards.

Local pre-gate run of the touched suite:
`python3 -m pytest tests/test_installer.py -q` → **144 passed** (70 of them the
new enroll tests, container oracles included).

---

## 6. Disclosures / things a reviewer should decide on

1. **The `from=` separator deviation (§0).** The one intentional departure from
   the contract's letter, with the measurement behind it. Accept the correction
   and propagate it to ciu CIU-93 / `SPEC.md` S14.7, or reject it and tell me to
   ship the comma form.
2. **O6's `--project ciu` (§2).** Asserted through the same code path on other
   configs, because no `ciu` `[project.installer]` exists and `vbpub/ciu/` is
   forbidden scope.
3. **The container oracles skip inside the gate.** `tester-unified` gets no
   docker socket, and `run-gate.toml` is not in this package's Touch list, so I
   did not add one. If the estate wants O2/O3 enforced *by the gate*, that is a
   run-gate lane change (a `RUN_GATE_EXTRA_MOUNTS` docker-socket lane, or a
   dedicated environment) and belongs in a follow-up with its own scope.
4. **`docs/SPEC.md` was not updated.** The plan's context list says to skim it
   "so `enroll`'s new normative text matches shape", but SPEC.md is not in the
   Touch list and no Work item requires it. I respected the Touch list. If the
   reviewer wants an S-section for `enroll`, it is a small follow-up.
5. **The install step is skipped in the container oracles** (`--no-install`).
   `do_install` fetches from GitHub; running it would have made the oracle
   non-hermetic and dependent on a published release of a fictional project.
   The install step's own contract is covered separately and in process by
   `TestEnrollInstallStep` — that `do_install` runs, runs **before** the user
   and key steps, receives the given `--scope`, `--manifest-pubkey` and token,
   and is skipped by `--no-install`.
6. **Comment-only difference on an otherwise identical key** is treated as
   "already present" with a warning naming both comments, not as a conflict.
   KI-24 defines the conflict as "same key material, different *options*"; a
   comment carries no authority. Pinned by
   `_enroll_install_key`'s docstring; say if you want it to be a refusal.

---

## Gate verdict — GREEN

Run against commit `745046b5` (the implementation commit), verdicts read in a
separate step from the detached run's log:

```
run-gate: lane 'assay'    exit 0
run-gate: lane 'coverage' exit 0
run-gate: lane 'mutation' exit 0
run-gate: lane 'canary'   exit 0
run-gate: lane 'gate'     exit 0
```

Detail worth reading rather than skipping:

- **assay** — `cmru: PASS (exit 0)`, commit `745046b5`, judge assay 5.1.0, R0
  claim `PASS`, verdict artifact `.assay/verdict-cmru.json`.
- **coverage** — `1738 passed, 10 skipped in 45.51s`,
  `Required test coverage of 100% reached. Total coverage: 100.00%`. Seven of
  those ten skips are this package's own container oracles
  (`tests/test_installer.py … .................sssssss`), skipping because
  `tester-unified` has no docker socket — exactly the disclosure in §6.3, now
  visible in the gate's own output rather than only asserted here.
- **mutation** — `no changed source since cmru-v5.0.0 — nothing to mutate,
  skipping`. Correct and expected: this package changes `templates/`, `tests/`
  and docs, and **no** line under `src/cmru/` (the campaign's scope). It is a
  true "nothing to mutate", not a misresolved base — the lane's own comment in
  `run-gate.toml` describes precisely this case.
- **canary** — exit 0, evidence `.assay/coverage-canary-cmru.json`.

A consequence a reviewer should weigh: because `src/cmru/` is untouched, neither
the mutation lane nor the 100% coverage floor exercises any of the new code —
the template is not on `--cov=src/cmru`. The enroll code's evidence is the
oracles above (including the two controlled wrong implementations), not the
coverage number.

No fixture container survived the run:
`docker ps -a --filter ancestor=cmru-enroll-fixture:local` is empty.

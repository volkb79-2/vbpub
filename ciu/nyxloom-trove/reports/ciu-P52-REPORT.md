# ciu-P52 — implementer REPORT (CIU-93, `ciu host enroll`, SPEC S14.7)

**Branch** `ciu-p51-bundle` · **commit** `b8b174b1` · item 4 of 4 in the
combined bundle. **Not merged. Not released.** One combined merge + one ciu
release are the controller's, after this part is independently reviewed
ACCEPT.

---

## 1. What shipped

| Contract | Where | Note |
|---|---|---|
| `[project.installer]` + committed `ciu/get.py` | `cmru.toml`, `ciu/get.py` | plus `--extra-asset get.py` on `[steps.push]`, so the printed URL resolves |
| Step 1 — key generation, print, no inventory write | `src/ciu/host_enroll.py:167` (`enroll_step1`) | real `ssh-keygen`, S14.3a namespace via `host_secret_store` |
| Step 2 — keyscan, fingerprint refusal, login proof, then write | `src/ciu/host_enroll.py:391` (`enroll_step2`) | login proof goes through the EXISTING `transport_ssh.ssh_exec` |
| `--abort` | `src/ciu/host_enroll.py:229` | refuses for an enrolled host; refuses when nothing is pending |
| Security invariants (S14.7d) | throughout + oracles | see §4 |
| The round-trip writer | `src/ciu/hosts.py:510` (`write_host_row`) | the design call — §3 |
| The `host` verb group | `src/ciu/cli.py:1807` (`_host`) | §2 |
| Oracles O1/O4/O5/O6 + the end-to-end chain | `tests/tests/test_ciu_host_enroll.py` (1340 lines, 98 tests) | §5 |
| SPEC / proposal / CHANGES / backlog | `docs/SPEC.md:2775` S14.7, proposal header, `CHANGES.md`, CIU-93 → FIXED, CIU-99 filed | |

---

## 2. The `host` verb dispatch — and a handoff premise that turned out false

The handoff states: *"every existing verb in `cli.py` is a flat `elif verb ==
"<name>":` string; there is no existing two-word/grouped verb."* **The second
half of that is not true, and I checked before designing around it.**
`src/ciu/cli.py:2079` is `elif verb == "worktree": raise
SystemExit(_worktree(rest))`, and `_worktree` (`src/ciu/cli.py:1390`) builds
`p.add_subparsers(dest="action", required=True)` with eleven `sub.add_parser`
calls — `ciu worktree create`, `ciu worktree lease`, and so on. S16's verb has
been a grouped verb all along.

That made the design decision easy rather than open: **match `worktree`
exactly.** The addition is as small as it can be —

* one `elif verb == "host": raise SystemExit(_host(rest))` beside
  `host-secrets`, which is left untouched (renaming a shipped verb is not this
  package's business, and the proposal's §6 table asks for "a `host` group
  **beside** the flat `host-secrets`");
* one `_host` function with a single `enroll` subparser, using the same local
  argparse style as the `host-secrets`/`ssh` blocks (`add_help=False`,
  `_extract_define_root` pulled off first per CIU-54/S1.1);
* one `_VERB_HELP["host"]` block and one `_USAGE` line.

Steps 1 and 2 are the same subcommand, disambiguated by whether
`--ssh-host`/`--fingerprint` are present, as the handoff specifies.
`--fingerprint` without `--ssh-host` is refused (there is nothing to keyscan),
and `--abort` refuses to combine with the other enrollment flags.

`ciu host --help` / `ciu host enroll --help` reach the new `_VERB_HELP` block
through the existing `_wants_verb_help` interception — no change needed there,
because it already keys on the first word.

---

## 3. The round-trip TOML writer — decision and justification

### 3.1 First, the premise check

The handoff asks for this to be verified rather than assumed. It is correct:
**no round-trip-preserving TOML writer exists anywhere in this codebase.**
`worktree._worktree_overlay_text` (`src/ciu/worktree.py`) is, despite the word
"round-trip" in its docstring, a sparse-template renderer that produces a
FRESH file from scratch; every other TOML write in `src/ciu/` goes through
`tomli_w.dumps` on a whole document (`engine.py:1289`, `hooks_runner.py:318`,
`composefile.py:1413`, `worktree.py:366`/`892`). All of those replace the file
wholesale.

### 3.2 The decision: stdlib-only targeted surgery, NOT `tomlkit`

`tomlkit` is the purpose-built tool and I would normally reach for it. It is
rejected here on a measured, non-preference ground:

```
$ python3 -c "import tomlkit"
ModuleNotFoundError: No module named 'tomlkit'

$ docker run --rm tester-unified:local /opt/tester-venv/bin/python -c "import tomlkit"
ModuleNotFoundError: No module named 'tomlkit'
```

The second line is the decisive one. `tester-unified:local` is the image ciu's
own gate runs in (`run-gate.toml` `[lanes.ciu] environment = "tester-unified"`,
resolved against the central `/workspaces/vbpub/run-gate.toml`
`[environments.tester-unified]`), and its `/opt/tester-venv` is built
**outside this repo, in cmru** — a path this package's Scope/forbid section
puts entirely off limits. Adding `tomlkit` to `pyproject.toml`'s
`dependencies` would therefore ship a hard runtime dependency that ciu's own
required gate cannot import, and the only way to fix that is a forbidden edit
or an out-of-scope image rebuild. It is also worth noting on its own merits
that ciu deliberately runs a three-package runtime closure (`Jinja2`,
`PyYAML`, `tomli_w`) and that S14.5 makes a point of the SSH transport adding
"zero added Python dependencies".

So: surgery. But surgery done in a way that does not rely on getting TOML's
grammar right by regex, because that is exactly where corruption lives.

### 3.3 How it is made safe

Three mechanisms, each aimed at a specific hazard the handoff names:

1. **A multi-line-string-aware line scanner** (`hosts._classify_lines`,
   `src/ciu/hosts.py:265`, built on `_scan_line_strings`). It tracks `"""` and
   `'''` state across physical lines, plus single-line basic/literal strings
   with escape handling, and stops at an unquoted `#`. A line that *looks*
   like `[deploy.hosts.decoy]` but sits inside a multi-line value is
   classified `other` and can never be mistaken for a table header. This is
   pinned by the adversarial fixture and by six direct scanner tests.
2. **`tomllib` does every parse of a header or key spelling** — never a
   hand-rolled splitter. `_toml_path_of` (`src/ciu/hosts.py:245`) hands the
   bracket contents back to `tomllib.loads` as a one-table document and reads
   the key path out of the resulting nested dict, so quoted
   (`[deploy.hosts."rs 1002"]`), dotted and whitespace-padded spellings all
   resolve exactly the way `load_hosts` will resolve them.
3. **A verify-then-write post-check** (`_verify_round_trip`,
   `src/ciu/hosts.py:369`) that REFUSES rather than writing anything it cannot
   prove is a pure single-row edit. Four independent assertions: the result
   parses; the row is exactly what was asked for; the operator's own keys on
   that host survive; and the *entire rest of the document*, deep-compared as
   parsed data, is unchanged. Every failure path raises a tagged `[S14.7]`
   error ending "Nothing was written."

On top of that, the two edit shapes are deliberately asymmetric in risk:

* **A new host is APPENDED at end of file.** Every prior byte is copied
  verbatim; a table header at EOF is valid TOML after any complete document
  (and the document is known complete, because `tomllib` parsed it first).
  This is the overwhelmingly common case and it is trivially preserving.
* **`--replace` edits only the managed keys inside the existing table's own
  direct body** — the span from its header to the next header of any kind, so
  `[deploy.hosts.<name>.admin]`, `[...secrets]` and every later table are
  outside it. Unmanaged keys, comments and blank lines inside the body are
  copied verbatim, and a trailing comment on a rewritten managed key line is
  preserved (`_comment_start`). A managed key the new row no longer carries
  (e.g. `ssh_port` when a rotation goes back to 22) is dropped, which is
  intended and is exempted from the "operator's own key" check.

One hazard the handoff did not list, found while writing this: **a file that
uses the top-level `[hosts.*]` form.** Writing `[deploy.hosts.x]` into such a
file would not merely add a row — `load_hosts` prefers `deploy.hosts` when it
exists, so it would immediately stop seeing every existing `[hosts.*]` row.
`_inventory_prefix` (`src/ciu/hosts.py:307`) detects the file's own form and
keeps it, and `resolve_hosts_file` (`src/ciu/hosts.py:51`) reuses the reader's
own candidate list (`_hosts_candidates`, extracted from `load_hosts` so the
two can never disagree) so a row is always written to the file the reader will
actually consult.

**Atomicity**: `_atomic_write_text` (`src/ciu/hosts.py:424`) — `mkstemp` in
the same directory, write, `os.chmod` to the pre-existing mode, `os.replace`.
This matches the established convention in `engine.py:1741`,
`composefile.py:1413` and `secrets/materialize.py:189`. An interrupted write
cannot leave the inventory truncated, and a `0600` inventory stays `0600`
(pinned by a test).

**Where it lives**: in `hosts.py`, beside the reader. The two are one
contract — what the writer emits, `load_hosts` must read back — and the
`[deploy.hosts.*]`-vs-`[hosts.*]` precedence rule is now stated once, in one
module, instead of twice in two. The enrollment *flow* is a separate new
module (`host_enroll.py`) because it is about `ssh-keygen`/`ssh-keyscan`/SSH,
not about the inventory file.

---

## 4. Security invariants (S14.7d / proposal §7), each with its oracle

* **Private key material never printed, logged or transmitted.** Only the
  `.pub` is read for output; the private path is printed, the private
  *content* is never opened for anything but `ssh-keygen`'s own use. Oracle:
  `test_wrong_2_private_key_material_never_reaches_any_output` runs step 1
  and step 2 and greps everything they emit — the verb's own `out`, plus real
  stdout and stderr via `capsys` — for the key's literal first line
  (`-----BEGIN OPENSSH PRIVATE KEY-----`) and for a 40-character slice of its
  base64 body. Both must be absent; the key's *path* must be present (S14.4b
  logs paths).
* **The installer URL is always version-pinned, never `latest`.**
  `installer_url` (`src/ciu/host_enroll.py:72`) formats
  `.../ciu-v<version>/get.py` from the control host's own version, and
  **refuses** when that version is an unreleased setuptools-scm build
  (`.dev`/`+`/`unknown`) rather than printing a URL that 404s — pointing at
  `--installer-url`. Nothing anywhere falls back to an unpinned URL.
* **`CIU_SSH_INSECURE_TOFU` is never set, under any flag combination.**
  Oracle: `test_the_verb_never_sets_the_tofu_escape_hatch` runs both steps
  through the real CLI and asserts the variable is still absent from
  `os.environ`, then asserts structurally that neither new module contains
  `os.environ[`, `environ.setdefault` or `putenv` at all — the reason it
  *cannot* be set from here, not just that it wasn't this time.
* **Nothing is written before the fingerprint matches AND the login is
  proved.** Oracles: the wrong-fingerprint case asserts the file is
  byte-identical before/after; the failed-login case does the same; and
  `test_wrong_1_...` asserts the call order is literally
  `["select", "prove", "write"]`.
* **The pin is the existing mechanism, not a parallel one.** `prove_login`
  passes the just-confirmed key as `known_host` in the host config and calls
  `transport_ssh.ssh_exec`, so S14.4a's fail-closed check and S14.4c's
  `[ADDR]:N` temp-known-hosts construction are reused verbatim. Pinned by
  `test_default_exec_fn_is_the_real_transport_ssh_exec` and by
  `test_non_default_port_is_written_and_pins_in_the_S14_4c_form`, which feeds
  the written row into the real `_known_hosts_file` and asserts the file
  starts `[rs1002.example]:2222 `.
* **`--controller` is never guessed.** `resolve_controller` takes the flag,
  else `topology.external.public_fqdn`, else refuses. Six parametrized
  malformed-config shapes all refuse.

---

## 5. Oracles — what was actually run

Local: `PYTHONPATH=src nice -n 10 ionice -c3 python3 -m pytest
tests/tests/test_ciu_host_enroll.py -q -p no:randomly` → **98 passed, 1
skipped** (the skip is explained in §5.4).

Coverage of the new source, measured directly:

```
Name                     Stmts   Miss Branch BrPart  Cover
src/ciu/host_enroll.py     180      0     76      0   100%
```

`src/ciu/hosts.py`'s remaining misses under that command are its PRE-existing
functions (`get_host`, `get_host_secrets`, `_parse_host_secrets`), covered by
`tests/tests/test_ciu_host_secrets.py`; every new line and branch in that file
is covered.

### 5.1 O1 — step 1 (`TestStep1`)

Modes asserted off the real filesystem (`0700` dir / `0600` key / `0644`
pub); the exact public-key text, the pinned URL and the step-2 command all
asserted present in the printed block; `assert not hosts_file.exists()` and
`load_hosts(repo) == {}` for "no inventory write". The second-step-1 case
asserts the refusal AND that the inventory bytes and the private-key bytes are
both unchanged.

### 5.2 O4 — step 2 (`TestStep2`)

Both halves run against the **hostile fixture** (`HOSTILE_FIXTURE` in the test
file): a multi-line string whose body contains a `[deploy.hosts.decoy]` line,
hand-aligned whitespace with a trailing comment, comments at file/table/line
scope, a quoted host name (`[deploy.hosts."rs 1002"]`), an `.admin` sub-table
under the host being rotated, and a `[registry.ghcr]` table after the hosts.
Wrong fingerprint → refusal + `read_bytes()` identical. Right fingerprint →
`after.startswith(HOSTILE_FIXTURE)` (byte-for-byte survival of every prior
byte), the row exactly S14.7c's five keys with `ssh_port` absent at port 22,
and `core1`'s `bundle_dir`/`admin`/`registry.ghcr`/the decoy all still there.
The rotation case additionally asserts the hand-aligned comment survives.

### 5.3 O5 — the three controlled wrong implementations

Each was reverted for real and the SPECIFIC oracle confirmed to fail, and each
is now pinned as its own test:

1. **Row written before the fingerprint check.** Reverted by reordering
   `write_host_row` above `select_host_key` in `enroll_step2`;
   `test_o4_wrong_fingerprint_refuses_and_writes_nothing` then failed on its
   `read_bytes()` comparison, as designed. Pinned going forward as
   `test_wrong_1_...`, which spies on both calls and asserts the order is
   `["select", "prove", "write"]` — a stronger guard than the byte check
   alone, because it also catches a write inserted before the login proof.
2. **Private key material on stdout.** Reverted by adding a
   `out(private.read_text())` line to `enroll_step1`;
   `test_wrong_2_private_key_material_never_reaches_any_output` then failed on
   the `-----BEGIN OPENSSH PRIVATE KEY-----` assertion. Reverted back.
3. **A whole-file rewrite instead of a round-trip edit.** This one is pinned
   *as an executable experiment* rather than only as a reverted diff:
   `test_wrong_3_...` runs the wrong implementation for real
   (`tomllib.loads` → mutate → `tomli_w.dumps`) alongside the correct one on
   the same fixture, and asserts all three of: the two results are
   semantically equal as parsed TOML (so no parse-based check could tell them
   apart), ours preserves the original prefix byte-for-byte, and theirs does
   not — losing, concretely, `# aligned by hand, keep the comment`.

### 5.4 O6 — the rendered installer (`TestRenderedInstaller`)

* `python3 ciu/get.py enroll --help` is executed for real and asserted to list
  exactly KI-24's flag set (`--authorized-key --controller --user --name
  --from --docker --no-install --scope`).
* The `from=` form is asserted at source level to be
  `from="{from_pattern}" {body}` — whitespace, never comma (see §6).
* `INSTALLER_REPO_OWNER`/`_NAME`/`_TAG_PREFIX` are cross-checked against ciu's
  own `cmru.toml` `[github]`/`[project].prefix`, and `[project.installer]` is
  asserted present — so the baked constants and the release config cannot
  drift apart silently.
* `[steps.push]`'s argv is asserted to carry `--extra-asset get.py`.
* **Byte-identity** with a fresh render. This is the **1 skipped** test in a
  run inside this worktree, for an honest reason: the render needs cmru's
  `get.py.tmpl`, and (a) the installed cmru wheel does not ship it —
  `[tool.setuptools.package-data] cmru = ["templates/*.toml"]`, so
  `getpy._TEMPLATE_PATH` resolves to a non-existent path — and (b) the copy of
  `cmru/` inside THIS worktree predates KI-24 (the branch was cut before
  cmru's enroll subcommand merged to `main`; `grep -c "def do_enroll"
  ../cmru/templates/get.py.tmpl` → `0`). The test detects exactly that and
  skips naming it. **Byte-identity was verified manually against the real
  post-KI-24 template**:

  ```
  $ PYTHONPATH=/workspaces/vbpub/cmru/src python3 -c "..."
  BYTE-IDENTICAL
  ```

  It will run in-tree, unskipped, as soon as the branch carries a cmru with
  KI-24 (i.e. after the controller's merge).

### 5.5 The end-to-end chain oracle (`TestEnrollEndToEnd`)

This is the one the handoff singles out — proving the two halves FIT, not that
each works alone. It **passed for real** (`1 passed ... in 20.09s`). What it
does, in order:

1. Builds a fixture image (`python:3.11-slim` + `openssh-server`,
   `ssh-keygen -A`) and starts one container, immediately
   `docker update --cpus=1`-capped.
2. Runs step 1 on the control side for user `deployer`.
3. **Parses step 1's OWN printed one-liner** out of its output and executes
   exactly those arguments against the committed `ciu/get.py` inside the
   container (`--no-install` appended, exactly as cmru KI-24's own
   `TestEnrollAgainstRealSystem` does, for hermeticity).
4. Asserts the key really landed: `/home/deployer/.ssh/authorized_keys`
   contains exactly one line, equal to the `.pub` step 1 generated.
5. **Chains the fingerprint the TARGET printed** into step 2 — not a
   locally-computed one.
6. Runs step 2 with no `ciu` on the target → the tagged "key accepted but ciu
   is not installed" refusal fires against a real sshd, and the inventory file
   still does not exist.
7. Runs step 2 with a deliberately wrong fingerprint against the same real
   host → the "man in the middle" refusal fires, and still nothing is written.
8. Installs a `ciu` on the target, runs step 2 → the row is written with the
   container's real IP, the real `ssh-ed25519` host key, and the generated key
   path.
9. Finishes by calling the real `transport_ssh.ssh_exec(get_host(repo,
   "rs1002"), ["ciu", "version"])` — i.e. what `ciu ssh rs1002 -- ciu version`
   does — and asserting exit 0.

**One honest limitation, stated rather than buried:** the target's `ciu` in
step 8 is a stub script, and step 3 uses `--no-install`. That is not a
shortcut around the oracle — it is forced by CIU-99 (§6): there is no ciu
release bundle for `get.py install` to download, so a real install could not
run in this test even with network. What is proven for real is everything the
enrollment verb itself owns: the one-liner parses and runs, the key installs,
the fingerprint chains, the pin holds, the login works, and both outcomes of
the `ciu version` probe behave as specified. The install step itself is cmru
KI-24's O2/O3 territory and is already proven there.

The whole class skips where docker is unavailable — including inside the
gate's own `tester-unified` container, which is the structural blind spot cmru
already filed as **KI-25** for KI-24's sibling oracles. Same shape, same
reason; noted so a reviewer does not read the gate's green as covering it.

### 5.6 Gate

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-p51-bundle`,
run against the committed tree (the gate refuses a dirty one), verdict read in
a separate step per LESSONS L4.

**VERDICT: `ciu: PASS (exit 0)` at commit `7917dca5`** —
`run-gate: lane 'ciu' exit 0`. Read afterwards from the verdict artifact
`.assay/verdict-ciu.json` in its own step: `outcome PASS`, **R0 PASS** (the
full `run-ciu-tests.py` suite) and **R1 PASS** at `pct=100.0`,
`branches=210/210` on the changed lines. The suite itself:
`3854 passed, 2 skipped` (the two skips are the O6 byte-identity test and the
end-to-end container class, both explained above).

**The first gate run was RED, and the fix is worth reading.** At commit
`b8b174b1` the gate reported `FAIL/COMMAND_FAILED`, with exactly four
failures — every `TestHostVerbDispatch` test that drives step 1 through the
CLI — while coverage already passed at 100%. Cause: those tests depended on
how the interpreter running the suite got its ciu. Locally that is an
installed wheel reporting `7.11.0`, so the verb prints a real release-asset
URL; the gate's container runs from source, `get_cli_version()` returns a
setuptools-scm `.dev` version, and `installer_url` **correctly refused**
(S14.7d — never print a URL that 404s), exit 2. The product behaviour was
right and the tests were environment-dependent, so the tests changed: a
`pinned_version` fixture fixes the reported version for the dispatch tests,
and both real behaviours are now pinned on their own — an unreleased control
host refuses naming `--installer-url`, and passing `--installer-url` makes it
usable again. `_VERB_HELP["host"]` documents the requirement.

Two things that let this slip past the local run are worth a reviewer's
attention: (a) `test_the_verb_never_sets_the_tofu_escape_hatch` was calling
`_cli` without asserting its exit codes, so it passed while the verb underneath
it was exiting 2 — it now asserts them; and (b) nothing in the local
environment reproduces the gate's from-source version, which is precisely why
the pin is a fixture rather than a hope.

---

## 6. Findings the reviewer should know about

### 6.1 CIU-99 (filed) — the enroll one-liner's install half cannot work yet

Wiring `[project.installer]` surfaced this. `get.py install` resolves
`<tag><asset_suffix>` from the GitHub release, verifies a SHA256 + minisign
manifest, and installs `[[project.installer.wheels]]` into a private venv.
ciu's `[steps.push]` publishes a bare wheel — `ciu-7.11.0-py3-none-any.whl` —
whose filename cannot match `ciu-v7.11.0` + any suffix, and there is no
`manifest.json`. So a target admin who runs the printed one-liner WITHOUT
`--no-install` gets an install failure, and step 2's `ciu version` proof then
correctly reports "key accepted but ciu is not installed" — enrollment stops
one step short of the proposal §10 promise ("ciu can SSH in, **and ciu is
installed there**").

I did not fix it: the fix is release machinery (assemble and publish a
`ciu-v<version>.tar.xz` carrying `vendor/ciu-*.whl` + a manifest — i.e. moving
ciu toward tls-edge's tarball+installer shape, or adding a second artifact
alongside the wheel), not enrollment logic, and it is well outside "the
smallest addition that gets you `ciu host enroll`". It is filed as **CIU-99**
with a proposed contract and oracles, named in `docs/SPEC.md` S14.7b, and
named again in a comment on the `[project.installer]` table itself so nobody
reads that config as a working install path. `get.py` itself IS a real release
asset now, so the printed URL resolves.

### 6.2 The `from=` comma correction — nothing in ciu needed fixing, so I added the fact instead

The handoff flags that the design proposal's own text spells the restricted
key line with a comma. I grepped for it: `grep -rn 'from="' docs/ src/` returns
exactly one hit, proposal line 43, and it does **not** contain a comma — it
says only that `--from PATTERN` "adds an `authorized_keys` `from="PATTERN"`
restriction". The comma form lives in cmru's KI-24 entry (its own "Proposed
contract" paragraph, already corrected in place there), not in ciu.

So there was no wrong text to fix here. The productive action was to *state
the correct form positively*, which I did in three places: `docs/SPEC.md`
S14.7b now carries the measured rule and the evidence (sshd advances past the
options field to the first unquoted whitespace; `from="*",<key>` →
`Permission denied (publickey)`, `from="*" <key>` → authenticated), the
proposal's status header carries it as a correction note, and a test asserts
the committed `get.py` really emits the whitespace form. My own printed
one-liner never renders a key line at all — it passes `--from PATTERN` through
to `get.py`, which owns that rendering — so there was no example of mine to
get wrong.

### 6.3 `topology.external.public_fqdn` is not an established config key

S14.7a and the proposal both name `topology.external.public_fqdn` as
`--controller`'s default source, and that is what I implemented, exactly. But
it is worth flagging that this key appears **nowhere else in the codebase** —
`grep -rn "public_fqdn" src/` finds only `infrastructure.public_fqdn`, which
is what `workspace_env._detect_public_fqdn` (S2.7/CIU-47) actually reads, and
`topology` is otherwise used for `topology.services.*`. I did not add a
fallback to `infrastructure.public_fqdn`, because S14.7a is explicit that a
missing value is a refusal and inventing a second source would be exactly the
kind of silent default this estate forbids. If the operator would rather the
default came from the key the rest of ciu uses, that is a one-line change to
`resolve_controller` and a SPEC amendment — a decision, not a bug.

### 6.4 For the controller to file (I may not: `cmru/` is forbidden)

**cmru's `get-py` is unusable from an installed cmru.**
`cmru/src/cmru/getpy.py:24` sets `_TEMPLATE_PATH = Path(__file__).resolve().
parents[2] / "templates" / "get.py.tmpl"`, which only resolves inside a source
checkout; from a wheel install it lands on `<venv>/lib/python3.X/templates/
get.py.tmpl`. And `cmru/pyproject.toml:42` declares
`cmru = ["templates/*.toml"]`, so `get.py.tmpl` is not packaged at all.
Measured on cmru 5.1.0 installed in this devcontainer:

```
$ python3 -c "from cmru import getpy; print(getpy._TEMPLATE_PATH, getpy._TEMPLATE_PATH.exists())"
/home/vscode/.venv/lib/python3.14/templates/get.py.tmpl False
```

Consequence: `cmru get-py --project ciu` — the exact command KI-24's own O6
and this package's Work item 1 both name — fails for any consumer that
installed cmru rather than running it from the repo. I rendered `ciu/get.py`
by pointing `PYTHONPATH` at the cmru source checkout. This is a cmru defect
(packaging + path resolution), and per the handoff's Scope/forbid it is "a NEW
backlog entry against cmru, not a change you make here"; since writing to
`cmru/` at all is forbidden, I am handing it to the controller rather than
filing it myself.

### 6.5 A stray write during manual smoke testing (cleaned up)

Recorded in the LOG §3 and repeated here for visibility: one early manual
`ciu host enroll` smoke run, executed from `test-repo/` without
`--define-root`, resolved the repo root from this shell's ambient
`$REPO_ROOT` (hardcoded to `/workspaces/dstdns` in this devcontainer) and
generated a key pair under `/workspaces/dstdns/.ciu/secrets/hosts/rs1002/`. It
was removed immediately and `/workspaces/dstdns/.ciu/secrets/` verified back
to its two pre-existing entries. No ciu behaviour is at fault — that is
`_resolve_repo_root_deploy`'s documented S1.1 precedence working as specified.

---

## 7. What is deliberately NOT here

Per proposal §10 and the handoff's Forbid list: no push/activate mechanics, no
new inventory keys, no Vault in the pre-trust path, and no auto-configuration
of `bundle_dir`, `docker_optional`, `activate`, `admin` or `secrets` — those
stay the operator's exactly as for a hand-written row. No v8 (`ciu8/`,
`SPEC-V8.md`) change. No `cmru/` change of any kind. `--global` appears in the
proposal's §3/§5 signatures but not in S14.7's own v7 signatures, so it is not
implemented; `resolve_hosts_file` already honours a pre-existing user-global
`~/.ciu/hosts.toml` through the reader's own precedence, which covers the
practical case without inventing a flag the v7 spec does not name.

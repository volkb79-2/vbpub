<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/augeas-extension-FEASIBILITY.md -->

# Augeas as hostcfg's lens ecosystem, extended upstream: feasibility (2026-11-08)

Author: Opus research agent. Repos read-only. Evidence: `gh api` against `hercules-team/augeas` (read-only),
the 1.15.0 source tree and the 1.14.1 trixie debs in `spike-augeas/`, and new scratch runs in
`<local-path>` (no host installs, never `/etc`).
Tags: **[V]** verified (command/URL/file cited), **[K]** general knowledge, not verified here, **[U]** unknown.

Operator question: *"Take Augeas and fork/extend it so we stay in one ecosystem; our changes become PRs to Augeas.
Would we still miss functionality?"*

## 0. Answer in five lines

1. **Yes, we would still miss functionality, and by design most of it never belongs in Augeas.** Augeas is a
   *per-file parse/edit/serialise engine*. Every guard (native validators, shadowing, exec paths, permissions,
   duplicates policy), the lock, the journals/revert, post-actions, users/groups and the P-7 bootstrap stage
   stay in hostcfg whatever we upstream (table §3).
2. **What we can extend is the lenses, and that needs no binary fork.** `augtool -I DIR` searches DIR *before*
   the dist lenses [V], so a lens overlay directory shipped by us replaces or adds lenses on the stock trixie
   1.14.1 binary (§2.3, verified).
3. **Upstream PRs do not reach our hosts.** Upstream released 1.14.1 → 1.15.0 after 38 months [V]; Debian trixie
   stays on 1.14.1 and has no trixie-backports build [V]. A merged PR only shortens our overlay later; the
   overlay is the real delivery path.
4. **Recommend option A**: stock trixie `augeas-tools` 1.14.1 via `augtool` subprocess + a lens overlay in our
   wheel + upstream PRs for each overlay lens. No binary fork. JSON stays on the stdlib provider (one documented
   exception, §4).
5. **First step**: hostcfg `edit` kind backed by `augtool` (~350 LOC code + ~400 LOC tests), overlay dir empty.

---

## 1. Upstream health

| Item | Finding | Tag |
|---|---|---|
| Repo, license | `hercules-team/augeas`, LGPL-2.1 (lens headers: "LGPL v2+, like the rest of Augeas"), not archived, 533 stars | [V] `gh api repos/hercules-team/augeas`; `lenses/sysctl.aug:9-10` |
| Releases | 1.12.0 2019-04 → 1.13.0 2021-10 (30 mo) → 1.14.0 2022-12 (14 mo) → 1.14.1 2023-07 (7 mo) → **1.15.0 2026-09-29 (38 mo)** | [V] `gh api …/releases` |
| Commits, last 24 months (since 2024-11-08) | **31** commits. 23 of them in Sep 2026 (release burst), 4 in Apr 2026, 2 in Jan 2026, 2 in Mar 2025. **No commit 2025-03-30 → 2026-01-22 (10 months)** | [V] `gh api …/commits?since=2024-11-08` |
| Active maintainers | **2**: George Hansper (merged 15 of 18 sampled external PRs) and David Lutterkort (original author; did the 1.15.0 revival: CI was broken until #898, because it pinned a retired `ubuntu-20.04` runner, NEWS 1.15.0). Raphaël Pinson last seen commenting 2021 | [V] merged_by of 18 PRs; NEWS 1.15.0 "ci:" entries |
| Open counts | 164 open issues, **38 open PRs**; 10 open PRs date from 2014-2022 | [V] search API; open PR `created_at` histogram |
| Last 45 PRs | 20 merged, 24 open, 1 closed unmerged (spam #883). Open ones include a 2026-10 batch of memory-safety fixes by a first-time contributor (#904-#907) and a maintainer UAF fix #908, all unmerged as of today | [V] `pulls?state=all&per_page=45` |
| External PR latency (14 merged, non-maintainer) | merge: 6, 8, 9, 26, 36, 40, 53, 98, 136, 140, 256, 372, 523, 542 days → **median ≈ 75 days**, tail 1-1.5 years. First maintainer response: median ≈ 69 days; the CVE fix #854 got one same-day | [V] per-PR `merged_at`, issue comments, reviews |
| Precedent for a new lens | Aptsources822 (#801): opened 2023-01-28, merged 2026-04-08 (**3.2 years**) | [V] `pulls/801` |
| Precedent for a JSON feature | #717 "JSON export" (2021): reviewed after ~4 weeks, contributor abandoned it and kept a fork | [V] `issues/717/comments` |
| JSON formatting issue | #820 (2023-10, open): appended entries render as `\n,\n"k":"v"}`. Maintainer: lenses produce *correct* not *pretty* output; "there is definitely room for improvement" | [V] `issues/820` |
| Contribution shape | A PR touches `lenses/<x>.aug` + `lenses/tests/test_<x>.aug` only (#893, #880, #896); the maintainer writes NEWS in the release commit (f1bc84552: NEWS +84). No CONTRIBUTING/CLA file at top level; dev notes in HACKING.md; `make check` runs `tests/lens-<x>.sh` → `augparse lenses/tests/test_<x>.aug` | [V] `pulls/N/files`, repo listing, HACKING.md |
| CI | GitHub Actions `build.yml` (gcc+valgrind, clang, ubuntu-latest), `release.yml`; working again since 2026-09 | [V] `.github/workflows/build.yml` |
| Debian | trixie 1.14.1-1.1~deb13u1 (CVE-2025-2588 patched), forky/sid 1.15.0-1; **no trixie-backports result** | [V] packages.debian.org search, tracker.debian.org/pkg/augeas |
| Debian sid 1.15 on trixie | needs `libxml2.so.16`, does not run; upstream 1.15.0 builds on trixie against `libxml2.so.2` | [V] rev 1 B.1 spike |

**Health verdict:** alive but thin. Two part-time maintainers, burst-mode activity, and a long queue. Lens PRs
with tests do get merged, typically in ~2 months and sometimes in years. Plan for it to take months and for
Debian to lag years behind; never let a hostcfg feature wait on upstream.

### 1.1 License implications [K, not legal advice]

- **hostcfg calls `augtool` as a subprocess.** That is not linking, so MIT cli-extended is unaffected. A
  `Depends: augeas-tools` (or a doc note) is enough.
- **Overlay lenses** derived from upstream `.aug` files stay LGPL-2.1+. They are their own source, so
  distributing them is compliant if they keep their header and ship with a COPYING file.
  - Inside the MIT wheel, put them in a separate data subdirectory and declare the wheel's license as
    `MIT AND LGPL-2.1-or-later` (see Q4).
  - New lenses we write from scratch can be any license, but upstreaming requires LGPL.
- **A binary fork (option B)** must publish the modified source alongside the binaries (LGPL §2/§6). This is
  easy, but it is an obligation.

---

## 2. What lens development costs

### 2.1 Size of the relevant lenses (1.15.0 tree; `wc -l lenses/X.aug lenses/tests/test_X.aug`) [V]

| Lens | Lens lines | Test lines | Covers (estate relevance) |
|---|---|---|---|
| json | 50 | 512 (**0 put tests**: `grep -c put` = 0) | daemon.json (C.1 #1-3) |
| systemd | 185 | 410 | units, drop-ins (`/etc/systemd/system/*/*`), logind.conf; *not* journald.conf by filter |
| sysctl | 52 | 87 | sysctl.d |
| shellvars | 337 | 783 | `/etc/environment` (filter line 305), `/etc/default/*` |
| simplevars | 53 | 65 | `KEY = value` files (took over adduser.conf in 1.15, #893) |
| sshd | 163 | 201 | sshd_config + `sshd_config.d/*.conf` |
| fstab | 44 | 172 | fstab (C.1 #8) |
| login_defs | 29 | 26 | login.defs |
| sudoers | 549 | 383 | sudoers + sudoers.d |
| limits | 71 | 44 | limits.conf |
| hosts | 15 | 64 | /etc/hosts |
| authorized-key-list format | exists (1.14.1 and 1.15) | | C.1 #9; the div2 evaluation's "no lens" was wrong |
| toml / tmpfiles / aptsources822 | 147 / 108 / 102 | 369 / 523 / 133 | Aptsources822 **only in 1.15** |
| (keyvalue) | no such lens | | generic KV = simplevars/shellvars/build combinators |
| missing in both | sysusers.d, udev rules, pam_env-proper, deb822 in 1.14.1 | | [V] file existence in `v1141/…/lenses/dist` and the 1.15 tree |

A typical lens is 15-200 lines on top of the shared combinators (`build.aug` 420, `util.aug` 198, `rx.aug`
167). Tests are 1-5x the lens size and written as `test Lens.lns get "<text>" = { tree }` (plus `put … after
… = "<text>"` for write behaviour). They run with `augparse lenses/tests/test_X.aug`, which ships in trixie's
`augeas-tools` (`/usr/bin/augparse`) [V]. The stock json test suite passes in 6.6 s [V].

### 2.2 The JSON fix is harder than it looks (scratch experiment, 1.14.1 binary) [V]

- **Stock lens.** Appending `cgroup-parent` to a 2-space-indented daemon.json gives
  `  "live-restore": true\n,\n"cgroup-parent":"dev.slice"}` with no final newline. This reproduces #820.
- **Overlay with a changed comma default (`del Rx.opt_space "\n  "`).**
  - `augtool` loaded it and the indentation changed. `augtool` does not typecheck by default.
  - `augparse` rejected it: `del: the default value '\n  ' does not match /[ \t]*/`.
- **Overlay with `del /[ \t\n]*/ "\n  "`.** `augparse` rejected it with `ambiguous concatenation … ',\n' can
  be split`, because the comment lens already owns the newline.
- **Root cause.** The whitespace before `}` belongs to the *last value*. A new member is therefore always
  emitted after it, and lens defaults are fixed strings: they cannot copy a sibling's indentation or follow
  nesting depth [K].
- **What a real fix takes.** Restructuring the lens to leading-whitespace ownership while keeping the tree
  shape that 512 test lines (and Puppet/other users) depend on. Size **L** (≈100 lens lines + ≈150 new put
  tests, type-checker iterations), with acceptance plausible but slow (the #820 reply).
- **Duplicate keys.** `{"a":1,"a":2}` loads as `entry[1]=a`, `entry[2]=a` with no error [V]. Repeated labels
  are a core Augeas feature (fstab `opt`, sshd `Match`). Uniqueness cannot be expressed in the regular lens
  language [K], so it is a guard in our layer (`match` count > 1 → refuse).

### 2.3 Lens overlay works on the stock binary [V]

- **Load order.** `augtool -I <ovl> -I <dist> -A -t "Json incl /etc/docker/daemon.json"` used the overlay
  `json.aug` instead of the dist one.
  - `man/augtool.pod:45-50`: `-I` directories are searched before `AUGEAS_LENS_LIB` and the defaults.
  - On a real host the dist dir is the default; in the spike it had to be passed explicitly.
- **Typecheck caveat.** `augtool` happily ran an overlay that `augparse` rejects. The overlay therefore needs
  its own CI step: `augparse` on every overlay lens plus its upstream test file.
- **Filters do not matter to us.** We always pass an explicit `-t "<Lens> incl <file>"`, so lens *filter*
  gaps (journald.conf not in Systemd, daemon.json not in Json) are irrelevant [V spike usage]. Only
  *grammar* gaps need lens work.

### 2.4 Which of our formats have good lenses today (cross-check rev 1 B.2, rev 2 C.1)

| Need (rev 2 C.1/C.2) | Lens on trixie 1.14.1 | Quality for a guarded edit |
|---|---|---|
| O6 key=value: sysctl, login.defs, `/etc/default/*`, limits, hosts | Sysctl, Login_defs, Shellvars, Limits, Hosts | **good**: comments/whitespace byte-for-byte, idempotent no-rewrite [V rev 1 B.2] |
| O7 INI/systemd section key | Systemd, IniFile, Toml | good; cosmetic: new key lands after the blank line; `+` exec flag only parsed as `fullprivileges` in 1.15 [V B.2] |
| sshd / sudoers in place | Sshd, Sudoers | good grammar. New keys append at the end, so with `Match` blocks they land inside the block [K] (guard: insert before the first `Match`, or use drop-ins) |
| O5 fstab keyed lines | Fstab | good, but more verbose than div2's 8-line filter [V div2 AUGEAS-EVALUATION §3c] |
| O4 line present/absent | authorized-key-list format; others per format | ok |
| O2 JSON | Json | **poor put formatting, no duplicate detection, 0 put tests** (§2.2) |
| `/etc/environment` (ruling: guarded provider, any caller) | Shellvars | **mismatch**: shell quoting ≠ pam_env syntax [K]; new comments written with a leading space [V B.2]; no block concept. A small upstream-able `Pam_env` lens (or a Simplevars filter) is the fix: S-M |
| deb822 `debian.sources` | **none** in 1.14.1; Aptsources822 in 1.15 | overlay-backport the 1.15 `aptsources822.aug` (pure lens file, 102 lines) if ever needed |
| sysusers.d, tmpfiles.d (`$` in 1.15), udev | none / partial / none | we write these whole; no lens needed |

---

## 3. Gap analysis: hostcfg guarded-applier requirements vs Augeas

(a) = stock Augeas provides it; (b) = an upstream PR could add it (plausibility); (c) = stays in hostcfg forever.

| Requirement | (a) stock | (b) upstream PR? | (c) hostcfg | Note |
|---|---|---|---|---|
| Format-aware in-place edit | **yes**, 231 lenses in 1.14.1 [V] | lens fixes and new lenses: **plausible**, slow (median ~75 d, up to 3 y) | JSON via stdlib (§4) | overlay delivers it now |
| present/absent | `set`/`rm`/`clear`; unchanged → file not rewritten [V B.2] | n/a | the `state=` semantics, absence of a file | |
| Plan / diff before write | `preview` (1.14.1, [V] run above), `-n` → `.augnew`, SAVE_NOOP + `/augeas/events/saved` [V B.2]. No unified-diff command [K] | `augtool diff`: plausible, low value | difflib on the staged file | we stage via `--root=<stage>` anyway |
| Parse-error reporting | `/augeas/files/<f>/error` with line/char [V B.2] | n/a | map to Problem, refuse | an unparseable file is refused, never "fixed" |
| Native validators (`systemd-analyze verify`, `sshd -t`, `visudo -c`, `dockerd --validate`) | no | **implausible**: Augeas validates grammar, not semantics; no exec hooks by design [K] | **yes** | |
| Duplicate keys | no: duplicates are legal trees [V] | lens-level uniqueness inexpressible [K] | `match` count guard | |
| Cross-file shadowing / effective order | no (parses files, knows no daemon merge rules) | **implausible** | **yes**, via native `systemd-sysctl --cat-config`, `systemd-analyze cat-config`, `sshd -T` | Augeas can *read* every file of a dir to collect keys: a helper, not the rule |
| Multi-file atomicity / staging | **no**: one file saved, another failed in the same `save` [V B.2 T8]; per file: mkstemp + fsync + rename, **no dir fsync** [V `src/transform.c:1238,1280,1335`] | **implausible** (C redesign; outside scope) | stage + per-file atomic write + journal; cross-file atomicity = by design absent | ruling: no automatic rollback |
| Lock | **none** (no flock/fcntl in `src/`) [V grep] | possible but pointless | `/run/lock/hostcfg.lock` | |
| Symlink / permission safety | follows symlinks (`canonicalize_file_name`, `transform.c:1192`), no lstat/O_NOFOLLOW [V]; new files 0666&~umask (`:1259`) [V] | "refuse symlink" flag: plausible, unnecessary | **yes**: we write the staged result ourselves | |
| Exec-path checks | no; Systemd lens splits `ExecStart` into `command`/`arguments` [V B.2], so it is usable as a parser helper | no | **yes** | |
| Post-actions (daemon-reload, restart, `sysctl -p`) | no | **implausible** | **yes** | |
| Users/groups/sysusers | Group/Passwd lenses bypass `lckpwdf`/nscd [U]; no sysusers lens [V] | sysusers lens plausible, unneeded | `systemd-sysusers`, `gpasswd` | |
| systemd unit state | no | no | `systemctl` | |
| Journals / pre-images / revert | `-b` writes a single `.augsave`, overwritten on the next save [K] | no | `txn/<id>/pre/` + `hostcfg revert` | |
| stdlib / zipimport bootstrap (P-7) | **never** (C library + data files); python3-augeas is a C extension [V rev 1 B.1] | no | bootstrap = whole-file kinds only; `edit` refuses with "needs augeas-tools" | |
| `--root` testability | `--root` [V spike], `-I` overlay [V] | n/a | fake roots, as for the other kinds | |
| Value quoting in `augtool` scripts | quoting rules exist (`tests/test-augtool/quoted-strings.sh`) [V file] | n/a | quote every value; refuse newline/NUL values | path-expression injection is our concern [K] |
| dpkg conffile prompts after in-place edits | inherent to editing distro conffiles [K] | no | by design: prefer drop-ins, `edit` only where none exists | |

**Count:** stock Augeas covers the edit primitive, parse errors, dry-run/preview and `--root`. Upstream PRs can
only ever improve *lenses* (and trivia like a diff command). Twelve requirements are (c), permanently.

---

## 4. Options

| | (A) stock trixie Augeas + overlay + upstream PRs | (B) our fork / patch-queue binary | (C) own providers only (rev 2) |
|---|---|---|---|
| What runs on hosts | Debian `augeas-tools` 1.14.1 (security via Debian) + our `lenses/` overlay via `-I` | our rebuilt libaugeas0/augeas-tools (1.15 rebuilt on trixie, builds against libxml2.so.2 [V]) | stdlib only |
| Who builds | nobody (apt) | us: `dpkg-buildpackage` of Debian's 1.15.0-1 source + quilt patches, per Debian release; artifact store or apt repo [U: we have no apt repo] | n/a |
| How it reaches hosts | div2 stage-1 `apt-get install augeas-tools` (after `_configure_apt`); overlay inside the cli-extended wheel | `dpkg -i` of our debs from a pinned release asset, or `/opt/augeas-<v>` private install | wheel |
| Security updates | Debian DSA/DLA (precedent: CVE-2025-2588 → deb13u1 [V]) | **ours**: watch upstream (+ the open UAF/overflow PRs #904-#908 [V]) and the Debian tracker, rebuild, redeploy. Replacing the system libaugeas0 also hits its other consumers (python3-augeas, libguestfs…) [K]; `/opt` avoids that but adds a second copy | ours (tiny surface) |
| What it buys over A | C-level changes only (save behaviour, locking), and none of them is needed: hostcfg stages and writes itself | | |
| Lens gaps | overlay now, PR in parallel | patch queue | write a parser per format (rev 2 had one consumer: JSON) |
| Cost | S now (provider) + per-lens S-M | M setup + recurring maintenance per CVE/Debian release | S (json_key ~150 LOC) |
| Risk | upstream lag (absorbed by overlay); typecheck-in-CI needed | supply-chain + patch rot + LGPL source publication | in-place edits of foreign non-JSON files unsupported |
| "One ecosystem" | **yes** for lenses (JSON excepted, §4.1) | yes | no |

### 4.1 Recommendation: (A), with one JSON exception by design

- **One lens ecosystem.** Every in-place edit of a non-JSON file goes through Augeas lenses: stock dist
  lenses plus our overlay.
- **Each overlay lens is filed upstream** as a PR (lens + `lenses/tests/test_X.aug`). It is deleted from the
  overlay once the Debian version on our hosts ships it.
- **JSON uses the stdlib `json_key` provider** (rev 2 HC-2, ~150 LOC).
  - The stdlib parser is a complete, lossless lens for comment-free JSON (dockerd's daemon.json has no
    comments [K]).
  - It refuses duplicate keys and writes canonical output.
  - It runs at bootstrap (P-7).
  - The Augeas JSON put fix is **L** with no estate value. Optionally contribute it upstream later as a
    goodwill PR, never on our path.
- **No binary fork.** Re-decide only if a C-level bug blocks a real request (trigger: a crash or
  mis-serialisation that a lens cannot work around).

**First step (one Codex package, after HC-1a's resource protocol):**

1. **`edit` kind.** Fields: `file`, `lens`, `ops = [{set=path, value}, {rm=path}, {clear=path}]`,
   `create = false`, `validate`, `on_change`.
   - Plan copies the target into `txn/<id>/stage/` and runs
     `augtool --root=<stage> -S -I <overlay> -I /usr/share/augeas/lenses/dist -A -t "<Lens> incl <file>"`
     with the ops plus `print /augeas//error`, then `save`.
   - The staged file is diffed with difflib.
   - Guards: parse error → refuse; duplicate labels for the touched keys → refuse; symlink / mode / owner;
     validator; shadowing hook per format.
   - Apply then uses hostcfg's own atomic write + pre-image (never Augeas's save on the live path).
2. **Overlay.** `cli_extended/hostcfg/lenses/` (empty, plus `COPYING.LGPL` and a README with the PR log), and
   a CI step that runs `augparse` over each overlay lens and its test.
3. **Availability.** `which augtool` is missing → `edit` is refused with a Problem `needs augeas-tools`.
   Bootstrap uses whole-file kinds only.

Size: ~350 LOC code + ~400 LOC tests (fake roots + a real `augtool` in the tester image).
Later, per need: `Pam_env` lens for `/etc/environment` (S-M, ~40 lens + ~80 test lines; upstream-able) and the
Aptsources822 backport to the overlay (copy, 0 new lines).

### 4.2 What we would STILL miss with an extended Augeas, and the simple handling

| Missing | Handling (keep it simple) |
|---|---|
| Multi-file atomic apply | **By design**: per-file atomic writes, journal, manual `hostcfg revert <txn>` (ruling R-4) |
| Semantic validation | hostcfg `validate` runs the native tool on the staged file; formats without a validator get only the lens grammar check (**known issue**, documented per kind) |
| Shadowing / effective order | hostcfg guard per layered format using the native merge view (`--cat-config`, `sshd -T`); formats without one: **known issue**, documented |
| Duplicate keys | hostcfg guard: refuse when a touched key matches more than once (`accept_duplicates` only by explicit request) |
| Lock, post-actions, users/groups, unit state, exec/permission/symlink checks | hostcfg, independent of the edit engine (already in HC-1) |
| Bootstrap (P-7) | **By design**: no in-place edits before apt has installed `augeas-tools`; whole-file kinds only |
| Upstream/Debian lag | **By design**: overlay is the delivery path; a PR merely retires an overlay file years later |
| JSON pretty output in Augeas | **By design**: JSON is the one stdlib lens |
| Files a lens cannot parse (hand-mangled) | refuse with the lens error line/char; operator fixes the file or replaces it whole |
| dpkg conffile prompts after in-place edits | **By design**: drop-ins preferred; `edit` documented as "makes the conffile locally modified" |
| `/etc/environment` semantics | until the `Pam_env` lens exists: the endpoints module stays the only front for that file (known issue) |

---

## 5. Questions for the operator

1. **JSON exception.** Is "every non-JSON in-place edit through Augeas lenses, JSON through stdlib" acceptable
   as "one ecosystem"? The alternative is an **L** JSON lens rewrite (§2.2) before HS-2 can use daemon.json.
2. **Meaning of "fork".** May "fork if needed" mean a *lens overlay on the stock Debian binary* (no binary
   rebuild, Debian keeps security)? Building our own libaugeas would happen only on a C-level blocker.
3. **Upstream identity and expectations.** Who submits the PRs (which GitHub account)? Are we fine with
   ~2-month median and up-to-years merge times, and with merged fixes reaching trixie hosts only through our
   overlay?
4. **Licensing layout.** Ship the LGPL overlay lenses inside the MIT cli-extended wheel (separate subdirectory
   + `COPYING.LGPL`, license expression `MIT AND LGPL-2.1-or-later`), or as a separate small data artifact?
5. **First consumer and dependency.** Which foreign file without a drop-in directory is the first real `edit`
   request (fstab? login.defs? `/etc/environment` by a non-endpoint caller?)? Is `augeas-tools` acceptable as
   a div2 stage-1 package on every host, or installed only when a profile uses `edit`?

## Evidence index

- Upstream data: `gh api repos/hercules-team/augeas{,/releases,/commits?since=2024-11-08,/pulls?state=all&per_page=45,/pulls/N,/pulls/N/files,/issues/N/comments}`;
  scratch dumps `<local-path>}`.
- Source: `spike-augeas/src/augeas-1.15.0/{lenses/*.aug,lenses/tests/,src/transform.c,man/augtool.pod,NEWS,HACKING.md}`.
- Overlay/JSON experiment: `<local-path>` (1.14.1 binary from `spike-augeas/v1141`, `--root` scratch).
- Debian: packages.debian.org search `augeas-tools` (suite=all), tracker.debian.org/pkg/augeas (fetched 2026-11-08).
- Context: rev 1 B.2/B.3 and rulings; rev 2 C.1/C.2/D; `scripts/debian-install-v2/docs/AUGEAS-EVALUATION.md`.
- Open [U]: whether Augeas Group/Passwd edits interact badly with nscd/sssd; whether we have or want an apt
  repo (only relevant to B); dockerd's comment tolerance in daemon.json [K]; `systemd-analyze verify` on
  staged drop-ins (rev 2 open item, unchanged).

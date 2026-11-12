<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-08; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/mounttrace-output-cli-ANALYSIS.md -->
<!-- relation: mounttrace output analysis -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# mounttrace 3.2.0: output clarity, CLI structure, 120 columns (Opus analysis, 2026-11-08)

Read-only analysis; nothing in the repo was changed. Real captures from the devcontainer's own mount table (36 mounts, overlay root with 81 layers, binds, btrfs with a read-only snapshot subvolume, slave links) plus a throwaway user-namespace scenario (`unshare -rm`: private and slave container, ghost mount, host-only mount, overmount, ancestor-hidden mount). Working files (captures `out/`, `proto.py` renderer prototype, `mock.txt` width-checked mock-ups, `help.txt`) are in the controller scratchpad `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad`.

## Headline
The biggest problem is the CLI, not the wording. `PATH` is a required argument, so every audit, prediction and state check first prints a full report on a path nobody asked about (20-50 lines; one overlay line is 2,543 characters); the answer comes last; no output fits 120 columns.

| Capture | Lines | Widest | >120 | >80 | Answer at |
|---|---|---|---|---|---|
| plain path | 20 | 166 | 2 | 5 | WRITE ROUTING, line 7 |
| `/ --submounts` | 56 | 2543 | 15 | 40 | table at line 20+ |
| file in read-only btrfs snapshot | 23 | 288 | 5 | 11 | read-only NOTE line 13 |
| `--audit-prefix`, private container | 37 | 293 | 16 | 18 | from line 24 |
| `--host-path`, private container | 51 | 254 | 13 | 18 | COMPARE at line 47 |
| `--predict` with `--peer` | 59 | 2543 | 15 | 21 | from line 50 |
| `--state-file` | 30 | 360 | 8 | 11 | last 5 lines |

## Findings
- F1 required PATH buries audits/predictions (guide 9.2 says "/ is fine" but `/` prints the overlay root report first; predict: 48 lines before the 9 that answer).
- F2 no verdict line; a healthy run says nothing; ALERT count only when non-zero; the read-only-snapshot (EROFS) fact is a NOTE on line 13 with exit 0.
- F3 one mount = one line with 8-10 fields (`desc()` 571-578) reused everywhere; WRITE ROUTING repeats the leaf row; parent chain redundant; `HIDDEN MOUNTS: (none)` boilerplate in every container run.
- F4 long shared prefixes repeated in every row; `PID #n ... mount=<dir>` really means "no mount here, a directory on #n"; Python `False` leaks (`same object: False`).
- F5 inconsistent vocabulary: INVOKING PROCESS / HOST / invoking view / HOST #; `[rw]`/`rw`/`RO(mount+superblock)`; propagation spelled three ways; `!r` quoting flips quote style; `PID-ONLY (same object)` is not an alert but starts with the alert class name (grep collision).
- F6 ALERT lines are indented (`Findings.alert` line 563) so `grep '^ALERT'` finds nothing although guide 9.3 says lines start with `ALERT:`; no next action in alerts.
- F7 overlay `lowerdir` printed in full, relative `upperdir` unexplained, no "container writable layer, not a volume" hint; stderr "not root" note printed when nothing fell back; shared-namespace `--pid` prints the same report twice; predict prints "does not exist; analysed lexically" for a path that normally should not exist yet; missing PID gives a raw `[Errno 2]`; `--submounts` sort interleaves; `--watch` reprints everything with only a banner.
- F8 `--all-hidden` == `PATH=/`; `--no-resolve` only skips `canonicalize()`, stat/btrfs probes still touch the path (MT-06 hung autofs/NFS not protected).
- Keep: analysis core, honest evidence labels, TUCKED/propagate_from handling, exit codes 0/3/1/2, usage errors before output, help under 100 columns.

## CLI proposal
One path syntax everywhere: `[PID:]PATH` (plain absolute path = invoking process's view, called `host`; `PID:PATH` = inside that process's root and mount namespace). Replaces `--pid`, `--host-path`, `--peer`, both sides of `--audit-prefix H=C`.

| Command | Replaces | Question |
|---|---|---|
| `explain [PID:]PATH [--submounts]` | PATH, `--pid`, `--submounts`, `--all-hidden` | where do writes go; what is stacked or hidden |
| `compare REF OTHER...` | `--host-path`, `--peer` | one object in every view; is the reference mount propagated |
| `audit PREFIX PID:PREFIX` | `--audit-prefix H[=C]` | every mountpoint under a tree side by side |
| `predict PATH PID...` | `--predict` | where would a new host mount appear |
| `soulmask PID... [--state-file F] [--volumes-root D]` | `--state-file`, `--volumes-root` | state-file audit plus volumes-root audit per PID (default state file `/run/soulmask_tmpfs.state`) |

Global: `--json` (NDJSON per pass under `--watch`), `-v` (expand collapsed, no truncation), `--watch SECONDS`, `--no-probe` (rename of `--no-resolve`: read only `/proc/*/mountinfo`, no stat/lstat/ioctl; the real MT-06 answer, deterministic golden tests), `--version`. Dropped: `--all-hidden`, `--host-path`, `--peer`; no aliases/migration (operator rule).
Guide 10.3 mapping: B `soulmask $wings_pid`; C `audit /var/lib/pterodactyl/volumes/$uuid $game_pid:<home>`; D `compare $p $wings_pid:$p $game_pid:<home>/WS/Saved/Logs`; E `predict $p $wings_pid $game_pid`; F `--watch 1 soulmask $wings_pid`.
Exit codes stay 0/3/1/2. Changes: `compare` DIFFERENT exits 3; `--watch` Ctrl-C returns the last pass's status; `explain` and `predict` never exit 3.
Help text (examples first, widest line 116) is in scratchpad `help.txt`.
Cheaper alternative (size S): make PATH optional only; fixes F1 but keeps the host/PID asymmetry; not recommended.

## 120-column rules
1. Width W = `$COLUMNS`, else terminal width on a TTY, else 120; below 80 lay out as 80; `-v` disables truncation; JSON never truncated.
2. Every default line <= W (tests at COLUMNS=120 and 80).
3. First lines answer the question: line 1 what was asked in which view; line 2 the answer (`writes go to ...`, `writes FAIL (EROFS) ...`, `ALERT 2 of 2 mountpoints differ ...`, `appears in 1 of 2 inspected views`).
4. Two levels only, ALERT and NOTE, column 0; ALERT counts for exit 3; one `next` line per ALERT class with a concrete action.
5. One mount table: `MOUNTPOINT ID FSTYPE SOURCE FSROOT RO PROPAGATION`; widths fitted; SOURCE/FSROOT shrink first, key last; ID/FSTYPE/RO/propagation/peer numbers never truncated; strip `/dev/mapper/`; `-` = same as fstype.
6. Paths cut in the MIDDLE keeping the end (`/first/.../a/last`, then `.../last`, then `...ast`, ASCII `...`); row keys (mountpoints, audit pairs) never truncated (a key that does not fit goes on its own line); audit paths relative to the prefix printed once; path-walk rows may be cut.
7. Collapsed by default, expanded by `-v`: overlay lowerdir (`layers=81`), full superblock options, parent chain, empty sections (one line `hidden: none on or below this path`), OK audit rows (`OK 1 more mountpoint (late); -v lists them`), views that receive nothing.
8. One vocabulary in human and JSON: views `host` / `pid N`; propagation `private|shared(N)|slave(N)|shared(N)+slave(M)|from(N)|unbindable`; RO `rw|ro|ro:sb`; same object `same`/`different`; link `linked|NOT LINKED|tucked`; non-alerting extra layer class `LAYER`; user-controlled strings escaped (mountinfo octal style) through ONE per-cell helper (MT-01's), applied before measuring width; no repr quoting.
9. No colour (meaning in words; optional later on TTY, NO_COLOR unset).
10. One row per line, status token in column 0, `--json | jq` as the machine interface; `--watch` prints `--- <time> mount tables changed: <view> +#id ... -#id ...` before each report.

## Mock-ups (real data, all lines <= 120)
```
### explain, healthy bind  (before: 20 lines, 166 cols)
host <checkout>/scripts/mounttrace                                                              mnt:[4026532448]
writes go to  ext4 <private-host>--vg-root:<host-checkout>/scripts/mounttrace  via #918, rw

PATH WALK (root -> leaf)
  MOUNTPOINT          ID FSTYPE  SOURCE               FSROOT                   RO PROPAGATION
  /                 #871 overlay -                    /                        rw slave(210)
  <checkout> #918 ext4    <private-host>--vg-root <host-checkout> rw private
hidden: none on or below this path

### explain, read-only btrfs snapshot  (before: 23 lines, EROFS fact on line 13)
host <cow-root>/.cowtree/.../assay-snapshot-7a7064e687fbcfe8576c8bed/README.md       mnt:[4026532448]
writes FAIL  (EROFS) btrfs subvolume 15883 assay-snapshot-7a7064e687fbcfe8576c8bed is READ-ONLY; mount #920 is rw

PATH WALK (root -> leaf; ~ = btrfs subvolume boundary, no mount)
  MOUNTPOINT                 ID FSTYPE  SOURCE                             FSROOT RO PROPAGATION
  /                        #871 overlay -                                  /      rw slave(210)
  <cow-root>  #920 btrfs   <private-host>--vg-devcontainers--cow /vb    rw slave(83)
  <cow-root>/.../trees/assay-snapshot-7a7064e687fbcfe8576c8bed
                              ~ btrfs   subvolume 15883 (parent 5)                RO
hidden: none on or below this path

### audit, private container: missing mount + ghost  (before: 37 lines, answer from line 24)
audit host <prefix>/vol vs pid 869702, same path                             mnt:[4026533281] vs mnt:[4026533282]
ALERT 2 of 2 mountpoints differ (HOST-ONLY 1, PID-ONLY 1, OK 0); paths below are relative to the prefix

STATUS CLASS     PATH HOST                         PID 869702                         SAME LINK
ALERT  HOST-ONLY late #1189 tmpfs late             - (dir on #1282 hostfs)            no   NOT LINKED
ALERT  PID-ONLY  logs - (dir on #1188 hostfs)      #1283 tmpfs soulmask-pi-abcdef12   no   NOT LINKED

next  HOST-ONLY: pid 869702 never receives this host mount: its copy of the tree is private (NOT LINKED). Make the
      bind rslave and restart the process; mounts made before the restart stay invisible to it.
next  PID-ONLY: pid 869702 keeps a mount the host no longer has (a ghost of a private copy) or made its own.
      A ghost goes away when the process restarts. Details: mounttrace explain 869702:<prefix>/logs

### compare  (before: 51 lines, two full reports, verdict on line 47, exit 0)
compare <prefix>/vol/late   in host, pid 869702 (same path in every view)
ALERT DIFFERENT objects: writes from pid 869702 land in tmpfs hostfs:/vol/late, not in host mount #1189 (tmpfs late)

VIEW        MOUNT FSTYPE SOURCE FSROOT OBJECT    DEV:INODE RO PROPAGATION  LINK FROM HOST
host        #1189 tmpfs  late   /      /         0:218:1   rw shared(301)  -
pid 869702  #1282 tmpfs  hostfs /      /vol/late 0:207:5   rw private      NOT LINKED (nothing receives group 301 here)

next  later host mounts and umounts here never reach pid 869702; it keeps seeing the directory underneath

### predict  (before: 59 lines, 2543 cols, answer from line 50)
predict a new host mount at <prefix>/vol/new
it attaches to #1188 (tmpfs hostfs) as /vol/new; #1188 is shared(300), so it propagates to peers and slaves of 300
appears in 1 of 2 inspected views

VIEW        RESULT      VIA                         LANDS AT
pid 869702  not reached not a peer or slave of 300  -
pid 869709  APPEARS     #1325 slave(300)            same path

### watch change line
--- 2026-11-08 22:14:03 mount tables changed: pid 869702 -#1283 tmpfs .../vol/logs; host +#1402 tmpfs .../vol/new
```

## Prioritized change list (land AFTER MT-01..07; all in scripts/mounttrace/mounttrace.py)
P1 (one release, ideally two packages: (a) items 1-2 + JSON, tests moved to JSON assertions; (b) human rendering items 3-5 + golden tests):
1. Subcommands + `[PID:]PATH`; remove required PATH; drop `--all-hidden`/`--host-path`/`--peer`; rename `--no-resolve` -> `--no-probe` gating all four probes (`canonicalize` 203, `stat_identity` 250, `btrfs_subvol_info` 266, `btrfs_boundaries` 314); `--volumes-root` under `soulmask`. (M; `main` 1034-1101, `one_pass` 993-1031, `needs_host` 975, `read_tables` 979-990, `parse_peer` 705, `parse_audit` 712)
2. Collect output in a `Report` object (from `Findings` 559-568: verdict, finding(level, cls, text, next), kv, table); render order header, verdict, findings, tables; `--json` dumps the same object. (L; `report` 601-679, `compare_results` 682-698, `audit_prefix` 720-771, `audit_state_file`/`_audit_state_entries`/`_audit_leftovers` 809-938, `predict` 941-968)
3. Width resolution, middle cutting, one mount-table renderer (replaces `desc()` 571-578), shared vocabulary for propagation and read-only. (S-M; `Mount.propagation` 107-118, `ro_label` 132-140, `btrfs_label` 303-311, `hidden_reason` 422-433, `place_event` 487-507, `link_status` 510-552: keep logic, return structured fields)
4. Collapse noise (F3/F7): parent chain to `-v`, empty sections one line, overlay `layers=N` + writable-layer NOTE, no duplicate report in shared namespace, no lexical-analysis NOTE in predict, not-root note only when something degraded, submounts in path order. (S)
5. Audit: alerts first, OK rows as a count, paths relative to prefix, `LAYER` class, link column, `next` hints; `compare` DIFFERENT = ALERT. (S-M)
P2: `--watch` change line + last-pass exit status (S, main 1083-1096); clear errors for vanished PID / permission (S, main 1074-1101; covers MT-07.3); `soulmask` bundles volumes-root audit with default state file (S); guide updates sections 0, 9.2-9.5, 10.3, 9.3 ALERT claim in the same commit as P1(b).
P3 (leaning no): collapse identical sibling rows (Docker /proc masks), `--mountinfo FILE` offline analysis, colour, CJK width.
Tests: ~45 assertions in tests/test_mounttrace.py match human wording (lines 188-543 incl. VM-only btrfs/Docker tests 446-543): move to `--json` field assertions + one JSON key-snapshot test; add 6-8 golden tests from synthetic View fixtures (style at lines 205-218) with `--no-probe` at COLUMNS=120 and 80 plus a non-TTY run (must give 120); one generic check that every user-namespace scenario's output stays <= COLUMNS; one VM-lane run at the end.
Leave alone: analysis core (`View`, `top_at`, `trace`, `visible_ids`, `propagation_receivers`, `place_event`/`link_status` decisions, `canonicalize`, btrfs ioctl code), guide concept chapters 1-8, single-file stdlib-only, exit-code values, state-audit checks.

## Risks / open questions
1. Test and guide churn is the main cost (guide quotes output strings in 0, 9, 10.3; VM lane re-verification expensive).
2. Middle-cut paths can hide the only difference between per-instance `volumes/<uuid>/WS/Saved/Logs` paths: "row keys never cut" + "relative to prefix" address it; verify on a real Wings tree on <host>/<host> after implementation.
3. `compare` DIFFERENT exiting 3 changes behaviour: needs operator confirmation (default taken: yes).
4. `host` is the wrong word when run inside a container (as on this devcontainer); accepted, the header shows the invoking process's namespace.
5. MT-01's sanitizer must be a per-cell `safe()` helper applied before width measuring; if it lands as a stdout wrapper, P1 #3 switches to the per-cell helper.
6. Should `soulmask` bundle the audit or stay a pure state audit chained with `audit ... && soulmask ...`? (default taken: bundle)

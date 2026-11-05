# Changelog

All notable changes to this project are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs.

## [Unreleased]

### Added
- New project `iogovmon`, extracted from `mdt-cockpit/iogov` (host-tuning WP-IGM-a; history-preserving `git mv`, so `git log --follow` reaches the original commits listed in `mdt-cockpit/CHANGES.md`). The import package and distribution are `iogovmon`; the console scripts are `iogovmond` and `iogovmonctl`. Config `/etc/iogovmon/iogovmon.toml` (`$IOGOVMON_CONFIG`), state `/var/lib/iogovmon`, user `iogovmon`, kill switch `/etc/iogovmon/disabled` (`IOGOVMON_DISABLE=1`), units `iogovmond-{tick,bench}.*` and `iogovmond.service` in the own cgroup `iogovmon.slice` (replaces `iogov-probe.slice`), udev rule `99-iogovmon-raw-read.rules`, template tokens `@IOGOVMON_*@`, scratch files `iogovmon-scratch-*`. No old console-script alias: nothing was ever released or installed under that name.
- Independent of mdt-cockpit: stdlib-only wheel (`pyproject.toml`, setuptools_scm tag `iogovmon-v*`), `cmru.toml` (wheel to GitHub Releases, never PyPI), `run-gate.toml` (lanes `r0-r1` with a 88 % branch-aware coverage floor, and `gate`), an `iogovmon` entry in `cmru.orchestration.toml` (level L0). Tests run on fakes and temp dirs only; the conftest path hack is gone (`pytest.ini` sets `pythonpath = src`).
- `BACKLOG.md` with the open work: the release gate (IGM-01, WP-IGM-ACC), the Assay R2 lane and 100 % floor (IGM-02), the `install` verb (IGM-03), the former mdt backlog items MDT-028..031 (IGM-04..07) and the WP-IGM-b features (IGM-08..11).

### Changed
- Split the former all-in-one console entry point into a service daemon and an operator control CLI. The command mapping is:

  | Binary | Existing subcommands |
  | --- | --- |
  | `iogovmond` | `tick`, `watch`, `bench`, `cleanup` |
  | `iogovmonctl` | `plan`, `analyze`, `status`, `preflight`, `devices`, `annotate`, `note`, `quiet-experiment`, `prune`, `config` |

  `python -m iogovmon` remains the combined developer entry point; it is not a console-script alias.
- `governed_cgroups` now defaults to an empty list (it was `["/sys/fs/cgroup/dev.slice"]`, a host-specific assumption). With none governed the governed series are empty and the gates see an unknown governed write rate.
- `__version__` is read from the installed wheel's metadata; a source-tree run reports `0+source` instead of the former hardcoded `1.0.0`.
- `iogovmon.slice` documents that its `IOWeight` is advisory (it only takes effect with io.cost or bfq); the PSI gating and the byte budgets are the budget control.

### Not yet released
- The first release is gated on the live acceptance WP-IGM-ACC (BACKLOG IGM-01); no `iogovmon-v*` tag exists.

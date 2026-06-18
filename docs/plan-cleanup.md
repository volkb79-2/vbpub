# Plan: repo cleanup (vbpub root)

**Status:** PROPOSED 2026-06-18 — review before executing the "Review" tier.
Safe + Untrack tiers are low-risk and can run as one cleanup commit (after the in-flight
modern-debian release finishes, so its generated manifests are committed first).

## Tier 1 — Remove now (clearly junk)

| Item | What it is | Size | Action |
|---|---|---|---|
| `core` | **tracked** ELF core dump from a `kscreen-doctor` crash | 2.2 MB | `git rm core`; gitignore `core` |
| `release.log` | old run log (already gitignored, file on disk) | 952 KB | `rm release.log` |
| `__pycache__/` | Python bytecode cache (gitignored) | 12 KB | `rm -rf __pycache__` |
| `.venv-1/` | leftover **duplicate** venv (un-ignored; `.venv/` is the live one) | 13 MB | `rm -rf .venv-1`; gitignore `.venv*` |

## Tier 2 — Untrack (tracked, but should not be in git)

| Item | Why | Action |
|---|---|---|
| `logs/` | 22 MB of run logs tracked in git | `git rm -r --cached logs`; gitignore `logs/` (global, also catches `<project>/logs/`) |
| `.env.sample` | **stale** — says "release settings now live in release.toml / release-manager", both retired | delete (superseded by `cmru.sample.toml` + SPEC S2.4) |

## Tier 3 — Review (needs your call — untracked/personal or one-off)

| Item | What it is | Suggestion |
|---|---|---|
| `claude/` | untracked: `chat-MD-log/`, `CLAUDE.md`, `README.md` (personal chat logs/notes) | keep but gitignore, or move under a personal dir; not repo content |
| `truenas/` | untracked: `fix_virt_global.py` (one-off ops script) | keep + track it, or move to `scripts/truenas/`, or gitignore |
| `desktop-analysis-report-20260221-172559.md` | tracked 102 KB one-off dated report | move to `docs/archive/` or delete |
| `release.log` already in Tier 1 | — | — |
| `core` already in Tier 1 | — | — |

## Tier 4 — Keep (legitimate, possibly relocate later)

- **Products:** `ciu/`, `cmru/`, `modern-debian-tools-python-debug/`, `pwmcp/`, `tls-edge/`,
  `game_stuff/empyrion/`, `plesk-mailbox-create/`, `vsc-devcontainer/`, `truenas/` (if kept).
- **cmru tooling:** `cmru.toml`, `cmru.sample.toml`, `cmru.secret.toml` (gitignored),
  `cmru.py`, `cmru.*.sh`, `release-all.py`/`release-runner.py` (deprecation shims, 1 release).
- `scripts/` (88 files; netcup/debian-install/etc. — `requirements.txt`'s telethon serves
  `scripts/netcup/telegram_setup.py`, so keep `requirements.txt`).
- `docs/`, `.github/`, `.vscode/`, `.claude/`, `install-debian.json`.

## .gitignore hardening (applied now)

Add: `core`, `*.core`, `.venv*/` (catches `.venv-1`), `/logs/` and `**/logs/` (run logs,
incl. per-project), keep existing `*.log`, `__pycache__/`, `.venv`.

## Note — modern-debian build outputs

The in-flight `modern-debian-tools-python-debug` release regenerates
`package-manifests-versioned/**/*.md` (intended, version-tracked manifests) and writes
`modern-debian-tools-python-debug/logs/` (run logs → now gitignored). Commit the manifests
as part of the release; do not commit that project's `logs/`.

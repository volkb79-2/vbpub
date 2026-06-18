# Repo cleanup — log (vbpub root)

**Status:** DONE 2026-06-18.

## Principle — no legacy remains

The repo carries exactly **one** release toolchain (`cmru`) and one entry point
(`cmru.py` + the `cmru.*.sh` shims). Superseded names are *removed*, not kept as
shims: a returning user who greps the root must find only the current names.

Retired & removed: `release-manager/`, `release-all.py`, `release-runner.py`,
`.vscode/release-all.sh`, `.vscode/publish-and-push.sh`, `release.toml`,
`release.sample.toml`, `.release-vars`, `build-push.toml`, `.env.sample`.

## Tier 1 — removed (junk)

| Item | What it was | Action |
|---|---|---|
| `core` | tracked 2.2 MB ELF core dump (`kscreen-doctor` crash) | `git rm`; gitignore `core`/`*.core` |
| `release.log` | old run log | removed |
| `__pycache__/` | bytecode cache | removed |
| `.venv-1/` | duplicate venv (`.venv/` is live) | removed; gitignore `.venv-1/` |

## Tier 2 — untracked (should not be in git)

| Item | Action |
|---|---|
| `logs/` | `git rm -r --cached`; gitignore `/logs/` + `**/logs/` |
| `.env.sample` | deleted (stale — pointed at retired `release.toml`/`release-manager`) |

## Tier 3 — reviewed & resolved

| Item | What it was | Decision |
|---|---|---|
| `claude/` | untracked personal chat logs / notes (`chat-MD-log/`, `CLAUDE.md`, …) | **gitignored** (`/claude/`) — personal, not repo content; kept on disk, untracked |
| `truenas/fix_virt_global.py` | one-off TrueNAS ops script at repo root | **relocated** → [`scripts/truenas/`](../scripts/truenas/) and tracked (ops scripts live under `scripts/`) |
| `desktop-analysis-report-20260221-172559.md` | tracked 102 KB display-scaling diagnostic of a personal Garuda/XFCE desktop — unrelated to vbpub | **deleted** (`git rm`; recoverable from history) |

## Tier 4 — kept (legitimate)

- **Products:** `ciu/`, `cmru/`, `modern-debian-tools-python-debug/`, `pwmcp/`,
  `tls-edge/`, `game_stuff/empyrion/`, `plesk-mailbox-create/`, `vsc-devcontainer/`.
- **cmru toolchain:** `cmru.toml`, `cmru.sample.toml`, `cmru.secret.toml` (gitignored),
  `cmru.py`, `cmru.*.sh`.
- `scripts/` (ops scripts incl. the relocated `scripts/truenas/`), `docs/`, `.github/`,
  `.vscode/`, `.claude/`, `install-debian.json`, `requirements.txt`.

## .gitignore hardening (applied)

`core`, `*.core`, `.venv-1/`, `/logs/`, `**/logs/`, `/claude/`, `cmru.secret.toml`,
`cmru.vars`, `cmru/build/`. Removed obsolete `/truenas/` (relocated) and `.release-vars`
(retired name).

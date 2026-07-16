# LOG — ciu-physical-root-preset-env fix

Branch: `fix/ciu-physical-root-preset-env`
Worktree: `/workspaces/vbpub/.worktrees/ciu-physical-root`

## Step 1 — Reproduction (disambiguating root cause a vs b)

Command:
```
cd /workspaces/vbpub/.worktrees/ciu-physical-root/ciu
PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -c "
import os
from pathlib import Path
import ciu.workspace_env as we

repo_root = Path('/workspaces/vbpub/nyxloom')

print('=== (i) PHYSICAL_REPO_ROOT UNSET (mountinfo path) ===')
os.environ.pop('PHYSICAL_REPO_ROOT', None)
result = we._detect_physical_repo_root(repo_root)
print('detected physical_root:', result)
net = we._compute_network_name(result)
print('network:', net)

print()
print('=== (ii) PHYSICAL_REPO_ROOT SET to dstdns (contamination) ===')
os.environ['PHYSICAL_REPO_ROOT'] = '/home/vb/volkb79-2/dstdns'
result2 = we._detect_physical_repo_root(repo_root)
print('detected physical_root:', result2)
net2 = we._compute_network_name(result2)
print('network:', net2)
"
```

Output:
```
=== (i) PHYSICAL_REPO_ROOT UNSET (mountinfo path) ===
detected physical_root: /home/vb/volkb79-2/vbpub/nyxloom
network: {'REPO_NAME': 'nyxloom', 'INSTANCE_ID': '1dd3d1', 'DOCKER_NETWORK_INTERNAL': 'nyxloom-1dd3d1-network'}

=== (ii) PHYSICAL_REPO_ROOT SET to dstdns (contamination) ===
detected physical_root: /home/vb/volkb79-2/dstdns
network: {'REPO_NAME': 'dstdns', 'INSTANCE_ID': '98535c', 'DOCKER_NETWORK_INTERNAL': 'dstdns-98535c-network'}
```

**Result: root cause (a) confirmed.** With `PHYSICAL_REPO_ROOT` unset, the real (this
devcontainer's own) `/proc/self/mountinfo` — which has an entry mounting
`/home/vb/volkb79-2/vbpub` at `/workspaces/vbpub` — correctly longest-prefix-matches
`repo_root=/workspaces/vbpub/nyxloom` through the `/workspaces/vbpub` bind and yields
`/home/vb/volkb79-2/vbpub/nyxloom` (CORRECT — `nyxloom` has no dedicated mount, it's a
subdir of the vbpub bind). Mountinfo (candidate b) already works fine here.

But with `PHYSICAL_REPO_ROOT` pre-set (as it is in the live devcontainer, since
`~/.bashrc:173-175` does `if [[ -n "$REPO_ROOT" && -f "$REPO_ROOT/ciu.env" ]]; then source
"$REPO_ROOT/ciu.env"; fi` — dstdns is this devcontainer's PRIMARY workspace, so
`REPO_ROOT=/workspaces/dstdns` gets set early and dstdns's own `ciu.env` (which contains
`export PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"`) gets sourced into every login
shell), the OLD code at line 398-400 returns the pre-set value unconditionally, BEFORE
ever consulting mountinfo. This reproduces the exact live bug byte-for-byte:
`REPO_NAME=dstdns`, `INSTANCE_ID=98535c`, `DOCKER_NETWORK_INTERNAL=dstdns-98535c-network`
— identical to the live `/workspaces/vbpub/nyxloom/ciu.env` values reported in the bug.

Confirmed: `/workspaces/dstdns/ciu.env` line 38 = `export
PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"`; `/workspaces/vbpub/nyxloom/ciu.env` line
38 (broken, pre-fix) = same value, `REPO_NAME=dstdns`, `INSTANCE_ID=98535c` — matches.

**Candidate (b) ruled out**: the sibling `nyxloom/ntfy/.ciu/ciu.compose.overlay.yml`'s
CORRECT physical root (per the handoff) already proves mountinfo works when reached; this
repro proves it same again for the nyxloom root itself. The bug is exclusively (a):
pre-set-env contamination winning unconditionally over a disagreeing mountinfo result.

## Step 2 — Fix implemented

`_detect_physical_repo_root` (workspace_env.py) refined: a pre-set `PHYSICAL_REPO_ROOT`
now wins only when (i) mountinfo yields nothing for `repo_root` (manual override on
native host / mountinfo unreadable), or (ii) mountinfo agrees with the pre-set value. When
mountinfo yields a value that DISAGREES with the pre-set env, the mountinfo-derived value
wins and a warning is printed to stderr. See REPORT for the exact diff and oracle
evidence.

## Step 3 — Gate

See REPORT.md for the full pytest summary line.

# cgprofile observer contract 3 golden fixtures

Byte-identical copies of the cgprofile fixtures of the same names
(`cgprofile/tests/fixtures/rg55/observer-window-v3-*.json`),
taken from branch `fan-cgp-20261103` at commit
`1437903f7bef25dbb34f18ce10a25f5375fce732` (CP-39, observer contract 3).

They are not vendored code: Assay's client (`src/assay/observer_windows.py`)
is written against the documented wire (cgprofile `docs/PROTOCOL.md`
section 9). Each file holds one `request` and the daemon's `response`.

| file | what it pins |
|---|---|
| `observer-window-v3-open.json` | `window-open` in `cgroup` mode and its reply |
| `observer-window-v3-close-cgroup.json` | `window-close`, exact cgroup numbers |
| `observer-window-v3-close-process-tree.json` | `window-close`, process-tree mode (`REQUIRES_CGROUP_MODE` metrics) |
| `observer-window-v3-close-shared.json` | `window-close`, foreign PID seen (`SHARED_CGROUP`, only RSS) |

When cgprofile changes these files, copy the new bytes here and update the
commit above; do not edit them.

# cgprofile command-line specifications

The wheel installs three executables. Each of the first two is registered with cli-extended and has its own
generated surface, review catalog and findings file (a spec file owns exactly one generated region, so the two
cannot share one). This index carries no markers and is not listed in `[tool.cli-extended]`.

| executable | role | specification | review catalog |
|---|---|---|---|
| `cgprofile` | end-user client: `run`, `mark`, `report`, `analyze`, `skills` | [`CLI-SPEC-cgprofile.md`](CLI-SPEC-cgprofile.md) | [`cli-review-cgprofile.toml`](cli-review-cgprofile.toml) |
| `cgprofilectl` | socket client of the host daemon: `status`, `host`, `sessions`, `show`, `start`, `stop`, `watch`, `prune`, `config`, `doctor` | [`CLI-SPEC-cgprofilectl.md`](CLI-SPEC-cgprofilectl.md) | [`cli-review-cgprofilectl.toml`](cli-review-cgprofilectl.toml) |
| `cgprofiled` | the host daemon and its installer | [`CGPROFILECTL-SPEC.md` section 3](CGPROFILECTL-SPEC.md#3-cgprofiled) | n/a |

The behavioural contract of all three is [`CGPROFILECTL-SPEC.md`](CGPROFILECTL-SPEC.md).

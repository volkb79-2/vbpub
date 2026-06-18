# vbpub
Public Projects and Helpers

## Releasing (this repo uses **cmru**)

Every product here (ciu, cmru, modern-debian-tools-python-debug, pwmcp, …) is built and
released through **cmru** — the Configurable Multi Release Utility — driven by one config,
[`cmru.toml`](cmru.toml). The discoverable front door is the `cmru.*.sh` shims:

```bash
./cmru.status.sh     # preview what would be released (read-only)
./cmru.release.sh    # the one-shot: detect changed → tag → push → build → publish
./cmru.build.sh      # build artifacts only            ./cmru.publish.sh   # upload them
./cmru.cleanup.sh --remove-assets 30d   # prune old releases/images
```

Each shim is a thin pointer to `./cmru.py <verb>` (run `./cmru.py --help` for all verbs).
The token comes from `$GITHUB_PUSH_PAT` or a gitignored `cmru.secret.toml` (never commit it).
What/why and the full contract live in [`cmru/docs/SPEC.md`](cmru/docs/SPEC.md) — start at
*"S-CLI — CLI at a glance"*.

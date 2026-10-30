# patchseries consumer guide

## Check a series

Create a consumer-owned vocabulary. The `schema_version` is required, and each
array is closed: a patch using a value outside its matching array fails.

```toml
schema_version = 1
capabilities = ["placement", "logging"]
roles = ["enable", "guard", "plumbing"]
risks = ["low", "medium", "high"]
```

Add the trailers to each patch's commit message before `git format-patch`
emits the `---` separator. An independent change uses `Requires: none`.

```text
Change-Id: daemon-cgroup-parent
Capability: placement
Role: enable
Risk: medium
Requires: none
```

Install the source into the environment that runs the consumer:

```bash
python3 -m pip install --editable ./libraries/patchseries
patchseries check path/to/series --vocabulary path/to/patchseries-vocabulary.toml
```

## Select a dependency closure

`closure` takes one or more stable identifiers and prints the selected changes
plus their transitive requirements in patch order:

```bash
patchseries closure path/to/series daemon-cgroup-parent
```

The Python API returns parsed records so a consumer can construct a bundle or
report without changing the patch files:

```python
from patchseries import closure, parse

series = parse("path/to/series")
selected = closure(series, ["daemon-cgroup-parent"])
```

## Compare and render versions

Pass repeated `--column LABEL=SERIES_DIR` options to render capability and
role tables, or two series directories to `delta`:

```bash
patchseries render \
  --column v1.13.3=patches/pterodactyl-v1.13.3 \
  --column v1.14.0=patches/pterodactyl-v1.14.0
patchseries delta patches/pterodactyl-v1.13.3 patches/pterodactyl-v1.14.0
```

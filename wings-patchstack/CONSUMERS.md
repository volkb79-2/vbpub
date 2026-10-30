# Wings patch series consumer guide

## Apply the pinned series

Run these commands from the repository root when preparing a review checkout:

```bash
wings-patchstack/scripts/clone.sh pterodactyl
wings-patchstack/scripts/apply.sh pterodactyl
wings-patchstack/scripts/test.sh pterodactyl
```

`clone.sh` and `apply.sh` refuse if `v1.13.3` no longer resolves to the SHA in
`wings-patchstack/stack.conf`. For an upstream update, record the new full
commit SHA and review the patch application before changing the pin.

## Inspect and select changes

Validate the trailers against the consumer vocabulary, then ask for a
dependency closure or a cross-version inventory:

```bash
PYTHONPATH=libraries/patchseries/src python3 -m patchseries check \
  wings-patchstack/patches/pterodactyl-v1.13.3 \
  --vocabulary wings-patchstack/vocabulary.toml
PYTHONPATH=libraries/patchseries/src python3 -m patchseries closure \
  wings-patchstack/patches/pterodactyl-v1.13.3 wings-scope-resource-control
PYTHONPATH=libraries/patchseries/src python3 -m patchseries render \
  --column pterodactyl-v1.13.3=wings-patchstack/patches/pterodactyl-v1.13.3 \
  --column filesystem-pterodactyl-v1.13.1=wings-patchstack/patches/filesystem/pterodactyl-v1.13.1
```

To compare two versions, pass their series directories to `patchseries delta`.
Use `git range-diff` to review the code changes paired by those stable IDs.

## Build an image

After the applicable source and integration gates are green, build without
choosing a suffix by hand:

```bash
wings-patchstack/scripts/build-image.sh pterodactyl
```

The script labels the image with its series digest, pinned upstream commit,
patchseries version, and UTC build date, then records the image ID in
`image-ledger.json`. This task only changes the workflow and does not build an
image.

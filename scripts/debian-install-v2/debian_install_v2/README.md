# Debian install v2 operator guide

`debian-install-v2.py` is the CLI for creating settings, inspecting an
installation plan, applying stage one, and checking or resuming installation
state. Run it without arguments to see the grouped usage map. Help is available
at the top level and for every verb:

```bash
./debian-install-v2.py --help
./debian-install-v2.py wizard --help
./debian-install-v2.py install --help
```

The quickstart, wizard dependency, example settings, direct-install recipe,
and remote custom-script flow are documented in
[`../docs/CONSUMERS.md`](../docs/CONSUMERS.md). The rationale for the verb
model, shared CLI library, settings validation, and optional prompt package is
in [`../docs/DESIGN-GUIDE.md`](../docs/DESIGN-GUIDE.md). The installer’s
feature surface is summarized in [`../README.md`](../README.md).

The CLI is backed by the shipped `Config` loader. A wizard-generated file is
ordinary versioned JSON and can also be reviewed or edited by hand; every
execution validates it again before doing host work. `install` is for the
Debian host being installed and can repartition its root disk. Read the
consumer guide’s safety notes before running it.

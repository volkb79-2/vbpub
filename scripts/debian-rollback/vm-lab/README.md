# Debian rollback VM lab

This project owns the disposable QEMU guests used to build and test Debian
rollback installations. Store each VM's disk, OVMF variables, serial/QMP logs,
and run metadata in its own subdirectory here. These files are generated and
can be recreated from the read-only shared inputs in `downloads/vm-images/`
and `downloads/iso/`.

Keep guest state out of Git. Preserve run evidence needed for a result report
until that report is complete; remove only the exact disposable VM directory
afterward. This directory is ignored by its companion `.gitignore`.

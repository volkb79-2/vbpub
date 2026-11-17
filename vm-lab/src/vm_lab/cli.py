"""The `vm-lab` console script: the generic profile's command line, including `image build`."""

from __future__ import annotations

from .core import GENERIC_PROFILE, build_parser, main as run


def main(argv: list[str] | None = None) -> int:
    """Entry point of `vm-lab`, `python -m vm_lab` and the `vm-lab/vm-lab` wrapper."""
    return run(GENERIC_PROFILE, argv, parser=build_parser(GENERIC_PROFILE, image_commands=True))

#!/usr/bin/env python3
"""Refuse unsafe Assay source trees before an editable install executes them."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutation_campaign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assay-source", type=Path, required=True)
    args = parser.parse_args(argv)
    source = mutation_campaign._require_directory(args.assay_source, "--assay-source")
    if not (source / "pyproject.toml").is_file():
        raise ValueError(f"Assay source has no pyproject.toml: {source}")
    status = mutation_campaign._assay_install_tree_status(source)
    if status:
        entries = ", ".join(item for item in status.split("\0") if item)
        raise RuntimeError(
            "Assay source must be committed before editable installation; "
            f"working-tree entries: {entries}"
        )
    print("CMRU_ASSAY_SOURCE_PREFLIGHT=clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

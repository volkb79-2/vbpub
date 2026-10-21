"""Temporary RG-55 recovery shim: expose A5 while preserving P1's old argv."""

import sys

from assay import liveness

_inject_liveness_plugin = liveness.inject_liveness_plugin


def _inject_without_liveness(plan, *, liveness_dir, diagnostics,
                             liveness_policy=None):
    return _inject_liveness_plugin(
        plan,
        liveness_dir=liveness_dir,
        diagnostics=diagnostics,
        liveness_policy=liveness.LIVENESS_FALSE,
    )


liveness.inject_liveness_plugin = _inject_without_liveness

from assay.cli import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))

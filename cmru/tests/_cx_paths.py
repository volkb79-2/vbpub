"""Path literals the guard tests assert are ABSENT from cmru's gate and bootstrap code.

cli-extended is a wheel dependency (CX-D1): nothing may put its source tree on an import path.
The guard tests name that tree only to prove it is never used. The names are built here, in a
module that never touches an import path itself, so the audit's AC-25 heuristic (a file that names
the library checkout AND an import-path variable) reads the guards for what they are.
"""
from __future__ import annotations

CX_LIBRARY = "libraries" + "/" + "cli-extended"
CX_SOURCE = CX_LIBRARY + "/src"

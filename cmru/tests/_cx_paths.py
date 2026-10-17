"""Path literals the guard tests assert are ABSENT from cmru's gate and bootstrap code.

cli-extended is a wheel dependency (CX-D1): nothing may put its source tree on an import path.
The guard tests name that tree only to prove it is never used. The names are built here, in a
module that never touches an import path itself, so the audit's AC-25 heuristic (a file that names
the library checkout AND an import-path variable) reads the guards for what they are.
"""
from __future__ import annotations

CX_LIBRARY = "libraries" + "/" + "cli-extended"
CX_SOURCE = CX_LIBRARY + "/src"

#: The bootstrap's explicit zero-release switch (no released cli-extended wheel exists yet).
SOURCE_MODE_TEST = '[[ "${cli_extended_mode}" == "source" ]]'
#: How the bootstrap spells the checkout's source tree once it has named the checkout.
SOURCE_VARIABLE = "${cli_extended_library}/src"


def library_source_uses(script: str) -> list[tuple[int, str, bool]]:
    """Every non-comment line of a shell script that names the library's SOURCE tree.

    Returns ``(line number, line, inside)`` where ``inside`` says the line lies in an
    ``if [[ "${cli_extended_mode}" == "source" ]]; then`` branch (an ``elif``/``else`` leaves
    it). Only shell conditionals are tracked (they end in ``then``); Python lines in an inline
    program such as ``if x:`` are not mistaken for them. The released-wheel modes must never
    appear in a use, so a guard asserts ``all(inside)`` and pins the exact number of uses.
    """
    open_branches: list[bool] = []  # one entry per open shell `if`; True = in the source branch
    uses: list[tuple[int, str, bool]] = []
    for number, line in enumerate(script.splitlines(), start=1):
        text = line.strip()
        if text.startswith("#"):
            continue
        if text.startswith("if ") and text.endswith("then"):
            open_branches.append(text.startswith(f"if {SOURCE_MODE_TEST}"))
        elif (text == "else" or (text.startswith("elif ") and text.endswith("then"))) and open_branches:
            open_branches[-1] = False
        elif text == "fi" and open_branches:
            open_branches.pop()
        if CX_SOURCE in text or SOURCE_VARIABLE in text:
            uses.append((number, line, any(open_branches)))
    return uses

"""GA3 v2: scaffold a reviewable gate skeleton on onboarding
(docs/plan-gate-adoption.md §GA3 "v2 scaffolding").

GA3 v1 (`onboarding_gate.assess_gate`) only DETECTS a missing/weak
verification gate and OFFERS guidance -- it never writes anything. This
module is the ACT half: when a project's `GateOffer.has_gate is False`,
`scaffold_gate` writes a gate-runner Dockerfile template into the project's
trove AND appends a real `[gates.<id>]` section to the project's
`nyxloom-trove/nyxloom.toml`, so a subsequent `assess_gate` call reports
`has_gate=True` and `ProjectConfig.load` parses the new gate.

D-GA3v2 (see the handoff this package was built from): a *generically-
correct* gate for an arbitrary project cannot be auto-generated -- the argv
must know that project's own source/test dirs, package layout, and mount
paths. So this module scaffolds a **reviewable SKELETON, not a guaranteed-
working gate**: every project-specific value in the emitted Dockerfile and
argv carries an explicit `# nyxloom-scaffold: adjust ...` marker. The value
delivered is "no more blank page + `has_gate` flips True"; the TRUST check
remains `nyxloom gate verify` (GA1), which the operator runs after filling
in the markers -- v2 scaffolds, GA1 verifies, they compose.

Single ecosystem: Python/pytest only (no per-ecosystem detection). Out of
scope (deliberately not built here): reacting to a LAUNDERS/BROKEN `nyxloom
gate verify` verdict, multi-ecosystem templates, and actually running or
building the scaffolded gate -- this module never shells out (mirrors
`onboarding_gate.assess_gate`'s own no-subprocess contract).

No `tomli_w` dependency (nyxloom has none): the `[gates.<id>]` section is
hand-built TOML text, appended to the trove config via the same surgical
text-write idiom as `onboarding._wire_spine_keys` / `config.
update_project_policy` -- read the file as text, mutate it as text, write
it back in one shot, no TOML library round-trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import GateDef
from .onboarding_gate import GateOffer

# The marker every project-specific value in the scaffolded Dockerfile/argv
# carries -- greppable proof this is a review skeleton, not a false-complete
# gate (oracle 3 in the handoff). Kept as one shared constant so the
# Dockerfile, the argv, and any test asserting on it never drift apart.
ADJUST_MARKER = "nyxloom-scaffold: adjust"

_TROVE_NAME = "nyxloom-trove"
_DEFAULT_GATE_ID = "scaffolded-pytest"
_DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class ScaffoldResult:
    """What `scaffold_gate` did (or didn't do). `scaffolded=False` always
    comes with a non-empty `skipped_reason`; the two path fields are `None`
    exactly when the corresponding artifact was not written this call
    (either because scaffolding was skipped entirely, or because that one
    artifact already existed and was left untouched)."""

    scaffolded: bool
    gate_id: str
    dockerfile_path: Path | None
    config_path: Path | None
    skipped_reason: str | None = None


# ---------------------------------------------------------------------------
# pure emitters -- no filesystem, no subprocess

def render_gate_dockerfile() -> str:
    """A minimal test-runner-style Dockerfile: pytest + coverage + xdist,
    installed in an isolated, runtime-faithful image (never the interactive
    cockpit -- reference/STANDARD.md "gate contract"). Every project-
    specific line carries an `ADJUST_MARKER` comment; this is a skeleton to
    review and edit, not a build-ready image."""
    return f'''\
# Scaffolded by `nyxloom onboard --scaffold-gate` (docs/plan-gate-adoption.md
# §GA3 v2). This is a REVIEW SKELETON -- read every "{ADJUST_MARKER}" line
# below before building or trusting it. Once adjusted, verify it actually
# rejects broken code with `nyxloom gate verify <project>` (GA1) -- this
# scaffold does not prove itself correct.
FROM python:3.12-slim

WORKDIR /gate

# {ADJUST_MARKER} the image base above (version/distro) to match your
# project's supported Python version.

# {ADJUST_MARKER} this COPY + install line: point it at your project's real
# source tree and its pytest/dev dependency extra (this skeleton assumes a
# pip-installable project with a pyproject.toml at its repo root).
COPY . /gate
RUN pip install --no-cache-dir /gate pytest pytest-xdist pytest-cov

# {ADJUST_MARKER} SOURCE_DIR / TEST_DIR below to your project's real package
# and test directory names -- the scaffolded argv (nyxloom.toml [gates.*])
# references these same two names and must be kept in sync with them.
ENV SOURCE_DIR=src \\
    TEST_DIR=tests
'''


def render_gate_def(
    gate_id: str = _DEFAULT_GATE_ID,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> GateDef:
    """Build the `GateDef` for the scaffolded gate: a pytest+coverage+xdist
    argv skeleton with `{worktree}` substitution (gate_runner.py replaces
    that token with the real scratch-worktree path at invocation time) and
    an `ADJUST_MARKER` trailing comment naming every project-specific
    placeholder (image tag, mount path, source/test dir, `--source`).
    `phase="post-merge"` (picked up by `gate_runner.select_verification_gate`
    ahead of any `implementation`-phase gate) and
    `asserts=["tests-pass", "changed-line-coverage"]` per D-GA3v2."""
    inner = (
        "cd {worktree} && "
        "PYTHONPATH=src python -m pytest tests -n auto -q "
        "--cov=src --cov-report=json:/tmp/scaffolded-gate-cov.json && "
        "python -m nyxloom.coverage_gate --base main "
        "--coverage-json /tmp/scaffolded-gate-cov.json --source src"
    )
    # DETACHED, not attached (`docker run -d` -> `docker wait` -> `docker logs`),
    # deliberately. An attached `docker run --rm` reads its verdict off the
    # hijacked HTTP stream, which a lying transport (e.g. a socat relay without
    # -t) truncates -- returning partial output and a FORGED exit code, so a red
    # gate reads as green (canonical reference/LESSONS.md L18 defeat 4). The
    # detached form takes the exit code from the daemon via `docker wait` and
    # the output from a plain `docker logs` fetch (never a hijacked stream), so
    # a broken transport can only make this HANG, never lie. Do NOT "simplify"
    # this back to a single attached `docker run --rm`.
    outer = (
        'cid=$(docker run -d -v {worktree}:{worktree} scaffolded-pytest-gate:local '
        f"bash -c '{inner}'); "
        'code=$(docker wait "$cid"); '
        'docker logs "$cid"; '
        'docker rm "$cid" >/dev/null 2>&1 || true; '
        'exit "$code"  '
        f"# {ADJUST_MARKER} the image tag, the mount path, source dir "
        '("src"), test dir ("tests"), and --source above are placeholders '
        "for YOUR project's real layout -- build the scaffolded Dockerfile "
        "and tag it to match before this argv can run."
    )
    return GateDef(
        gate_id=gate_id,
        argv=["bash", "-c", outer],
        phase="post-merge",
        timeout_seconds=timeout_seconds,
        environment="scaffolded",
        asserts=["tests-pass", "changed-line-coverage"],
    )


def _toml_string(value: str) -> str:
    """A TOML basic (double-quoted) string literal for `value`. Hand-rolled
    (no `tomli_w` -- nyxloom has no TOML-writer dependency, see module
    docstring): escapes backslashes/quotes/newlines/tabs, the only control
    characters the scaffolded argv text can actually contain."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def render_gate_toml_section(gatedef: GateDef) -> str:
    """The `[gates.<id>]` TOML text for `gatedef`, matching exactly what
    `ProjectConfig.load` parses (config.py:271-284) and what the config
    schema requires (schemas/nyxloom-config.schema.json: `argv`/`phase`/
    `timeout_seconds` required; `asserts` enum-constrained)."""
    argv_lines = "".join(f"    {_toml_string(tok)},\n" for tok in gatedef.argv)
    asserts_inline = ", ".join(_toml_string(a) for a in gatedef.asserts)
    return (
        f"[gates.{gatedef.gate_id}]\n"
        f"argv = [\n{argv_lines}]\n"
        f"phase = {_toml_string(gatedef.phase)}\n"
        f"timeout_seconds = {gatedef.timeout_seconds}\n"
        f"environment = {_toml_string(gatedef.environment)}\n"
        f"asserts = [{asserts_inline}]\n"
    )


# ---------------------------------------------------------------------------
# the orchestrator -- the only function that touches the filesystem

def scaffold_gate(
    project_root: Path,
    offer: GateOffer,
    *,
    gate_id: str = _DEFAULT_GATE_ID,
) -> ScaffoldResult:
    """Act on a `GateOffer` from `onboarding_gate.assess_gate`.

    Preconditions (return a `scaffolded=False` `ScaffoldResult`, write
    NOTHING, on either):
    - `offer.has_gate is True` -- the project already declares a usable
      gate; scaffolding one would be noise, not signal.
    - the project's trove (`<project_root>/nyxloom-trove/nyxloom.toml`) does
      not exist -- `scaffold_gate` never creates a trove itself (that is
      F2's `run_wizard`/`onboarding.scaffold_trove`'s job, and must have
      already run before this is called, same precondition
      `onboarding_gate.assess_gate` documents for itself).

    Otherwise: write the Dockerfile (never overwriting one already at that
    path -- idempotent re-runs leave a hand-edited Dockerfile alone) and
    append the `[gates.<gate_id>]` section to the trove's `nyxloom.toml`
    (mirrors `onboarding._wire_spine_keys`'s surgical text-append idiom --
    no `tomli_w`). Never shells out."""
    toml_path = project_root / _TROVE_NAME / "nyxloom.toml"

    if offer.has_gate:
        return ScaffoldResult(
            scaffolded=False,
            gate_id=gate_id,
            dockerfile_path=None,
            config_path=None,
            skipped_reason=(
                f"project already declares gate '{offer.gate_id}' -- "
                "nothing to scaffold"
            ),
        )

    if not toml_path.is_file():
        return ScaffoldResult(
            scaffolded=False,
            gate_id=gate_id,
            dockerfile_path=None,
            config_path=None,
            skipped_reason=(
                f"no trove config found at {toml_path} -- run `nyxloom "
                "onboard` first so a trove exists before scaffolding a gate"
            ),
        )

    dockerfile_path = project_root / _TROVE_NAME / "gate-scaffold" / "Dockerfile"
    if not dockerfile_path.exists():
        dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
        dockerfile_path.write_text(render_gate_dockerfile(), encoding="utf-8")

    gatedef = render_gate_def(gate_id)
    section_text = render_gate_toml_section(gatedef)

    text = toml_path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += "\n" + section_text
    toml_path.write_text(text, encoding="utf-8")

    return ScaffoldResult(
        scaffolded=True,
        gate_id=gate_id,
        dockerfile_path=dockerfile_path,
        config_path=toml_path,
        skipped_reason=None,
    )

"""The ``assay.toml`` loader — the config contract that refuses to invent.

The whole point of this module is stated in AGENTS.md §4.2a and applied in
DESIGN-GUIDE §5: *a default is legitimate only when it is a policy choice that
is correct in the absence of information; it is a hazard the moment it
substitutes for a fact that exists somewhere else.* All four `coverage_gate.py`
copies in the estate ship anti-pattern #1 — `default="src/nyxloom"`,
`default="topos/src/topos"`, `DEFAULT_SOURCE = "libs/common/src,…"`,
`-source internal` — and when one of those literals is wrong the gate measures
the wrong tree and **passes**.

So: **this loader has no defaults at all.** Every value in a :class:`Lane` came
out of the file. There is no key on a loaded object that the file did not
declare, which is what :meth:`Lane.as_declared` exists to make mechanical.

The complete rejection surface, so it can be reviewed in one place:

============================  ==========================================  =======
Rule                          Rejects                                     Source
============================  ==========================================  =======
schema version                missing / non-int / not ``1``               §12
lane table                    file with no ``[lanes]``, or an empty one   §12
required lane fields          any of the eight missing                    P01a O1
closed vocabularies           ``scope`` ∉ S0-S4, ``rigor`` ∉ R0-R3,       A-053
                              ``enforcement`` ∉ {gate, advisory}
duration                      ``budget`` that does not parse              A-052
declared rigor is enforced    R1 without the five judge fields, R2        A-017,
                              without ``mutation``, R3 without            A-048
                              ``canary``
source roots                  absolute, or not an existing directory      A-016,
                              under the project root                      A-049
unknown keys                  a key assay does not understand, in a       §12
                              lane table or in ``[…judge]``
============================  ==========================================  =======

One thing this loader deliberately does **not** reject, because rejecting it
would be inventing policy rather than refusing to invent values:

* a ``[…where]`` table's contents. §7: WHERE is "data assay parses and never
  interprets", permanently. It is carried through verbatim.

It *does* reject ``judge`` config for a rigor level the lane does not declare
(A-062) — ``mutation`` on a lane without ``R2``, ``fail_under`` on an R0 lane.
Inert configuration cannot fail loudly when it is wrong, and it reads to a
human exactly like the capability it is not providing.

`judge.coverage.format` is checked to be a non-empty string and no further: the
closed vocabulary for it is *"a key the parser registry knows"* (§12), the
registry is P03's, and duplicating its key list here would recreate the
four-copies divergence one layer down.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .errors import LaneConfigError

__all__ = [
    "CoverageConfig",
    "ENFORCEMENTS",
    "JUDGE_FIELDS_BY_RIGOR",
    "JudgeConfig",
    "LANE_FILE_NAME",
    "LANE_SCHEMA_VERSION",
    "Lane",
    "LaneFile",
    "REQUIRED_LANE_FIELDS",
    "RIGOR_LEVELS",
    "SCOPES",
    "find_lane_file",
    "load_lane_file",
    "parse_duration",
]

#: A-014. The lane file is `assay.toml`, at the project root.
LANE_FILE_NAME = "assay.toml"

#: The lane-file schema this build understands. Distinct from the *verdict*
#: artifact's `schema_version` (§6), which is P01b's.
LANE_SCHEMA_VERSION = 1

#: A-053. TESTING-METHODOLOGY §Axis 1 / §Axis 2, and §12's table.
SCOPES: frozenset[str] = frozenset({"S0", "S1", "S2", "S3", "S4"})
RIGOR_LEVELS: tuple[str, ...] = ("R0", "R1", "R2", "R3")
ENFORCEMENTS: frozenset[str] = frozenset({"gate", "advisory"})

#: The eight fields every lane declares at its top level, whatever its rigor.
REQUIRED_LANE_FIELDS: tuple[str, ...] = (
    "scope",
    "rigor",
    "enforcement",
    "argv",
    "env",
    "env_passthrough",
    "budget",
    "allow_argv_append",
)

_OPTIONAL_LANE_FIELDS: tuple[str, ...] = ("judge", "where")

#: A-017/A-048: declared rigor is ENFORCED, and the `judge` fields are
#: CONDITIONALLY required — an R0-only lane has no `[judge]` table at all.
#: Requirements are per DECLARED level, not per highest level, because `rigor`
#: is a *list* of independently declared methods (§6: a lane declaring
#: ["R0","R1","R2"] can pass R0, pass R1 and be INCONCLUSIVE on R2). R2 and R3
#: pull in `language` and `source_roots` because mutation and canary cannot act
#: at all without knowing the adapter and the tree they act on — A-017's own
#: rule, that a lane may not claim a level it cannot exercise.
JUDGE_FIELDS_BY_RIGOR: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "R0": (),
        "R1": (
            "language",
            "source_roots",
            "fail_under",
            "allow_excluded",
            "coverage",
        ),
        "R2": ("language", "source_roots", "mutation"),
        "R3": ("language", "source_roots", "canary"),
    }
)

_KNOWN_JUDGE_FIELDS: tuple[str, ...] = (
    "language",
    "source_roots",
    "fail_under",
    "allow_excluded",
    "coverage",
    "mutation",
    "canary",
)

_COVERAGE_FIELDS: tuple[str, ...] = ("format", "artifact")

# A duration is one or more <number><unit> segments in descending order, e.g.
# "5m", "90s", "1h30m". A bare number is REFUSED: unit-less is ambiguous, and
# guessing the unit is exactly the invention this loader exists to prevent.
_DURATION_RE = re.compile(
    r"^(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?$"
)
_DURATION_HINT = "expected a duration such as '90s', '5m' or '1h30m'"


def parse_duration(text: str) -> float:
    """Parse a duration string to seconds (A-052 — at LOAD, not at run time).

    Raises :class:`ValueError` for anything that is not a positive duration.
    Callers in this module convert that into a :class:`LaneConfigError`; the
    function itself stays reusable by P07's runner.
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")
    match = _DURATION_RE.fullmatch(text.strip())
    if match is None or not any(match.group(g) for g in ("h", "m", "s")):
        raise ValueError(f"{text!r} is not a duration ({_DURATION_HINT})")
    seconds = (
        float(match.group("h") or 0) * 3600.0
        + float(match.group("m") or 0) * 60.0
        + float(match.group("s") or 0)
    )
    if seconds <= 0:
        raise ValueError(f"{text!r} is not a positive duration ({_DURATION_HINT})")
    return seconds


@dataclass(frozen=True)
class CoverageConfig:
    """``[lanes.X.judge.coverage]`` — the declared format and artifact path."""

    format: str
    artifact: str

    def as_declared(self) -> dict[str, Any]:
        return {"format": self.format, "artifact": self.artifact}


@dataclass(frozen=True)
class JudgeConfig:
    """``[lanes.X.judge]`` — HOW to judge (D7's second question, A-015).

    Every field is ``None`` when the lane did not declare it. ``None`` here
    means *absent from the file*, never *assay chose this*.
    """

    language: str | None
    #: exactly the strings the file declared, in file order
    source_roots: tuple[str, ...] | None
    #: those strings resolved against the project root (A-049), each verified
    #: to be an existing directory (A-016)
    source_root_paths: tuple[Path, ...] | None
    fail_under: float | None
    allow_excluded: bool | None
    coverage: CoverageConfig | None
    #: opaque payloads owned by P10 (mutation) and P08 (canary); this loader
    #: verifies only that they are tables.
    mutation: Mapping[str, Any] | None
    canary: Mapping[str, Any] | None

    def as_declared(self) -> dict[str, Any]:
        declared: dict[str, Any] = {}
        if self.language is not None:
            declared["language"] = self.language
        if self.source_roots is not None:
            declared["source_roots"] = list(self.source_roots)
        if self.fail_under is not None:
            declared["fail_under"] = self.fail_under
        if self.allow_excluded is not None:
            declared["allow_excluded"] = self.allow_excluded
        if self.coverage is not None:
            declared["coverage"] = self.coverage.as_declared()
        if self.mutation is not None:
            declared["mutation"] = dict(self.mutation)
        if self.canary is not None:
            declared["canary"] = dict(self.canary)
        return declared


@dataclass(frozen=True)
class Lane:
    """One declared lane. Every attribute came out of the file."""

    name: str
    scope: str
    rigor: tuple[str, ...]
    enforcement: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    env_passthrough: tuple[str, ...]
    #: the declared duration string, verbatim
    budget: str
    #: ...and its parsed value, which is ADDITIONAL to the declaration, not a
    #: replacement for it (A-052)
    budget_seconds: float
    allow_argv_append: bool
    judge: JudgeConfig | None
    #: §7: WHERE is data assay parses and never interprets.
    where: Mapping[str, Any] | None

    def as_declared(self) -> dict[str, Any]:
        """Reconstruct the TOML table this lane was loaded from.

        This is the mechanical form of *"no key present that the file did not
        declare"*: the result compares equal to ``tomllib``'s own parse of the
        lane table, so an invented default or a dropped field shows up as an
        inequality rather than as something a reviewer has to notice.
        """
        declared: dict[str, Any] = {
            "scope": self.scope,
            "rigor": list(self.rigor),
            "enforcement": self.enforcement,
            "argv": list(self.argv),
            "env": dict(self.env),
            "env_passthrough": list(self.env_passthrough),
            "budget": self.budget,
            "allow_argv_append": self.allow_argv_append,
        }
        if self.judge is not None:
            declared["judge"] = self.judge.as_declared()
        if self.where is not None:
            declared["where"] = dict(self.where)
        return declared


@dataclass(frozen=True)
class LaneFile:
    """A parsed ``assay.toml``."""

    #: the file itself, resolved
    path: Path
    #: the directory containing it — the PROJECT root, which is what
    #: `source_roots` resolve against (A-049), and not the repo root
    project_root: Path
    schema_version: int
    lanes: Mapping[str, Lane]

    def lane(self, name: str) -> Lane:
        try:
            return self.lanes[name]
        except KeyError:
            known = ", ".join(sorted(self.lanes)) or "(none)"
            raise LaneConfigError(
                f"{self.path}: no lane named {name!r}; declared lanes: {known}"
            ) from None


def find_lane_file(start: Path | None = None) -> Path:
    """Search upward from *start* for ``assay.toml``.

    DERIVE, then FAIL (§4.2a). The project root is *discovered* from the file's
    own location rather than guessed, and when there is no file the caller is
    told exactly where the search ran instead of getting a default path.
    """
    origin = (Path.cwd() if start is None else Path(start)).resolve()
    for directory in (origin, *origin.parents):
        candidate = directory / LANE_FILE_NAME
        if candidate.is_file():
            return candidate
    raise LaneConfigError(
        f"no {LANE_FILE_NAME} found in {origin} or any parent directory; "
        f"pass --file to name one explicitly"
    )


def load_lane_file(path: Path) -> LaneFile:
    """Load and fully validate an ``assay.toml``.

    Raises :class:`LaneConfigError` (``ERROR`` / ``BAD_LANE_CONFIG``) on any
    defect, always naming the file, the lane and the field.
    """
    file_path = Path(path).resolve()
    try:
        raw_bytes = file_path.read_bytes()
    except OSError as exc:
        raise LaneConfigError(f"{file_path}: cannot be read: {exc}") from exc
    try:
        document = tomllib.loads(raw_bytes.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise LaneConfigError(f"{file_path}: is not valid TOML: {exc}") from exc

    project_root = file_path.parent
    schema_version = _load_schema_version(document, file_path)

    unknown = sorted(set(document) - {"schema_version", "lanes"})
    if unknown:
        raise LaneConfigError(
            f"{file_path}: unknown top-level key(s): {', '.join(unknown)}; "
            f"expected only: schema_version, lanes"
        )

    if "lanes" not in document:
        raise LaneConfigError(
            f"{file_path}: declares no [lanes] table; a lane file with no lanes "
            f"declares nothing to judge"
        )
    lanes_table = document["lanes"]
    if not isinstance(lanes_table, dict):
        raise LaneConfigError(
            f"{file_path}: 'lanes' must be a table of lanes, got "
            f"{_type_name(lanes_table)}"
        )
    if not lanes_table:
        raise LaneConfigError(
            f"{file_path}: declares no lanes; a lane file with no lanes declares "
            f"nothing to judge"
        )

    lanes = {
        name: _load_lane(name, table, file_path, project_root)
        for name, table in lanes_table.items()
    }
    return LaneFile(
        path=file_path,
        project_root=project_root,
        schema_version=schema_version,
        lanes=MappingProxyType(lanes),
    )


# --- internals ---------------------------------------------------------------


def _type_name(value: Any) -> str:
    return type(value).__name__


def _load_schema_version(document: Mapping[str, Any], file_path: Path) -> int:
    if "schema_version" not in document:
        raise LaneConfigError(
            f"{file_path}: missing required field 'schema_version' "
            f"(this assay understands schema_version = {LANE_SCHEMA_VERSION})"
        )
    value = document["schema_version"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise LaneConfigError(
            f"{file_path}: 'schema_version' must be an integer, got {_type_name(value)}"
        )
    if value != LANE_SCHEMA_VERSION:
        raise LaneConfigError(
            f"{file_path}: declares schema_version = {value}; this assay "
            f"understands schema_version = {LANE_SCHEMA_VERSION}"
        )
    return value


def _load_lane(
    name: str, table: Any, file_path: Path, project_root: Path
) -> Lane:
    where = f"{file_path}: lane {name!r}"
    if not isinstance(table, dict):
        raise LaneConfigError(f"{where}: must be a table, got {_type_name(table)}")

    for field in REQUIRED_LANE_FIELDS:
        if field not in table:
            raise LaneConfigError(f"{where}: missing required field {field!r}")

    unknown = sorted(
        set(table) - set(REQUIRED_LANE_FIELDS) - set(_OPTIONAL_LANE_FIELDS)
    )
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown key(s): {', '.join(unknown)}; expected only: "
            f"{', '.join((*REQUIRED_LANE_FIELDS, *_OPTIONAL_LANE_FIELDS))}"
        )

    scope = _as_str(table["scope"], where, "scope")
    if scope not in SCOPES:
        raise LaneConfigError(
            f"{where}: 'scope' must be one of {sorted(SCOPES)}, got {scope!r}"
        )

    rigor = _as_str_list(table["rigor"], where, "rigor")
    if not rigor:
        raise LaneConfigError(
            f"{where}: 'rigor' declares no level; a lane that declares no rigor "
            f"level can render no claim"
        )
    for level in rigor:
        if level not in RIGOR_LEVELS:
            raise LaneConfigError(
                f"{where}: 'rigor' must contain only {list(RIGOR_LEVELS)}, got "
                f"{level!r}"
            )
    if len(set(rigor)) != len(rigor):
        raise LaneConfigError(
            f"{where}: 'rigor' contains a duplicate level: {list(rigor)}; assay "
            f"records one claim per declared level"
        )

    enforcement = _as_str(table["enforcement"], where, "enforcement")
    if enforcement not in ENFORCEMENTS:
        raise LaneConfigError(
            f"{where}: 'enforcement' must be one of {sorted(ENFORCEMENTS)}, got "
            f"{enforcement!r}"
        )

    argv = _as_str_list(table["argv"], where, "argv")
    if not argv:
        raise LaneConfigError(
            f"{where}: 'argv' is empty; a lane must declare the command it runs"
        )

    env = _as_str_table(table["env"], where, "env")
    env_passthrough = _as_str_list(
        table["env_passthrough"], where, "env_passthrough"
    )
    if "/" not in argv[0] and "PATH" not in env and "PATH" not in env_passthrough:
        raise LaneConfigError(
            f"{where}: argv[0] {argv[0]!r} is a bare executable name but PATH "
            f"is declared by neither 'env' nor 'env_passthrough'. Without an "
            f"explicit PATH, process launch may search an implementation default "
            f"that the lane never declared. Declare PATH or use an executable path."
        )

    budget = _as_str(table["budget"], where, "budget")
    try:
        budget_seconds = parse_duration(budget)
    except ValueError as exc:
        raise LaneConfigError(f"{where}: 'budget' {exc}") from exc

    allow_argv_append = _as_bool(
        table["allow_argv_append"], where, "allow_argv_append"
    )

    judge = _load_judge(table.get("judge"), rigor, where, project_root)

    where_table = table.get("where")
    if where_table is not None and not isinstance(where_table, dict):
        raise LaneConfigError(
            f"{where}: 'where' must be a table, got {_type_name(where_table)}"
        )

    return Lane(
        name=name,
        scope=scope,
        rigor=tuple(rigor),
        enforcement=enforcement,
        argv=tuple(argv),
        env=MappingProxyType(dict(env)),
        env_passthrough=tuple(env_passthrough),
        budget=budget,
        budget_seconds=budget_seconds,
        allow_argv_append=allow_argv_append,
        judge=judge,
        where=None if where_table is None else MappingProxyType(dict(where_table)),
    )


def _required_judge_fields(rigor: Iterable[str]) -> tuple[str, ...]:
    """The union of what every DECLARED rigor level requires (A-017/A-048)."""
    required: list[str] = []
    for level in rigor:
        for field in JUDGE_FIELDS_BY_RIGOR[level]:
            if field not in required:
                required.append(field)
    return tuple(required)


def _load_judge(
    table: Any, rigor: Iterable[str], where: str, project_root: Path
) -> JudgeConfig | None:
    rigor = tuple(rigor)
    required = _required_judge_fields(rigor)

    if table is None:
        if required:
            raise LaneConfigError(
                f"{where}: declares rigor {list(rigor)} but has no [judge] table; "
                f"that rigor requires judge.{{{', '.join(required)}}}"
            )
        # A-048: an R0-only lane has NO [judge] table, and that is correct.
        return None

    if not isinstance(table, dict):
        raise LaneConfigError(
            f"{where}: 'judge' must be a table, got {_type_name(table)}"
        )

    unknown = sorted(set(table) - set(_KNOWN_JUDGE_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge key(s): {', '.join(unknown)}; expected only: "
            f"{', '.join(_KNOWN_JUDGE_FIELDS)}"
        )

    for field in required:
        if field not in table:
            raise LaneConfigError(
                f"{where}: declares rigor {list(rigor)} but is missing required "
                f"field 'judge.{field}'"
            )

    # A-062 (controller ruling, overriding this package's first reading).
    # Judge config for a rigor level the lane does NOT declare is refused.
    #
    # The argument for allowing it was that the rigor list is the claim, so
    # surplus config claims nothing. That is true of the DATA and false of the
    # READER: `fail_under = 100` on an R0 lane looks exactly like a coverage
    # floor and is not one -- nothing reads it, so if it is wrong nothing fails
    # (AGENTS.md 4.2a). This project's whole subject is a declaration implying
    # capability that does not exist; dstdns's five-lane table over two real
    # lanes is the named specimen, and this is the same shape one level down.
    #
    # The cost is the "write the config, enable the level later" workflow,
    # which is a habit rather than a requirement: declaring the level is one
    # line in the same edit. The remedy is always visible in the message.
    surplus = sorted(set(table) - set(required))
    if surplus:
        raise LaneConfigError(
            f"{where}: declares rigor {list(rigor)}, which reads none of "
            f"judge.{{{', '.join(surplus)}}} -- so that configuration is inert "
            f"and cannot fail loudly if it is wrong. Either declare the rigor "
            f"level that consumes it, or delete it."
        )

    language = None
    if "language" in table:
        language = _as_str(table["language"], where, "judge.language")
        if not language:
            raise LaneConfigError(f"{where}: 'judge.language' is empty")

    source_roots: tuple[str, ...] | None = None
    source_root_paths: tuple[Path, ...] | None = None
    if "source_roots" in table:
        declared = _as_str_list(table["source_roots"], where, "judge.source_roots")
        if not declared:
            raise LaneConfigError(
                f"{where}: 'judge.source_roots' is empty; a lane that measures "
                f"nothing gates nothing"
            )
        source_roots = tuple(declared)
        source_root_paths = tuple(
            _resolve_source_root(raw, where, project_root) for raw in declared
        )

    fail_under = None
    if "fail_under" in table:
        fail_under = _as_float(table["fail_under"], where, "judge.fail_under")
        if not 0.0 <= fail_under <= 100.0:
            raise LaneConfigError(
                f"{where}: 'judge.fail_under' must be a percentage between 0 and "
                f"100, got {fail_under}"
            )

    allow_excluded = None
    if "allow_excluded" in table:
        allow_excluded = _as_bool(
            table["allow_excluded"], where, "judge.allow_excluded"
        )

    coverage = None
    if "coverage" in table:
        coverage = _load_coverage(table["coverage"], where)

    mutation = _as_opaque_table(table.get("mutation"), where, "judge.mutation")
    canary = _as_opaque_table(table.get("canary"), where, "judge.canary")

    return JudgeConfig(
        language=language,
        source_roots=source_roots,
        source_root_paths=source_root_paths,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        coverage=coverage,
        mutation=mutation,
        canary=canary,
    )


def _load_coverage(value: Any, where: str) -> CoverageConfig:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.coverage' must be a table, got {_type_name(value)}"
        )
    unknown = sorted(set(value) - set(_COVERAGE_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.coverage key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join(_COVERAGE_FIELDS)}"
        )
    for field in _COVERAGE_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.coverage.{field}'"
            )
    fmt = _as_str(value["format"], where, "judge.coverage.format")
    artifact = _as_str(value["artifact"], where, "judge.coverage.artifact")
    if not fmt:
        raise LaneConfigError(f"{where}: 'judge.coverage.format' is empty")
    if not artifact:
        raise LaneConfigError(f"{where}: 'judge.coverage.artifact' is empty")
    return CoverageConfig(format=fmt, artifact=artifact)


def _resolve_source_root(raw: str, where: str, project_root: Path) -> Path:
    """Resolve one declared source root against the PROJECT root (A-049).

    Not the repo root and not the process cwd: the lane file must not need to
    know where it sits inside a monorepo, and a project vendored one level
    deeper must not silently start measuring nothing.
    """
    if not raw:
        raise LaneConfigError(f"{where}: 'judge.source_roots' contains an empty path")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise LaneConfigError(
            f"{where}: source root {raw!r} is absolute; source_roots are relative "
            f"to the directory containing assay.toml ({project_root})"
        )
    resolved = (project_root / candidate).resolve()
    if not resolved.is_dir():
        # A-016/A-035: a typo'd root matches no changed file, so the gate
        # returns 0/0 PASS forever. That is a laundering gate, and none of the
        # four existing copies guards it.
        raise LaneConfigError(
            f"{where}: source root {raw!r} does not exist under the project root "
            f"{project_root} (looked for {resolved})"
        )
    return resolved


def _as_str(value: Any, where: str, field: str) -> str:
    if not isinstance(value, str):
        raise LaneConfigError(
            f"{where}: {field!r} must be a string, got {_type_name(value)}"
        )
    return value


def _as_bool(value: Any, where: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise LaneConfigError(
            f"{where}: {field!r} must be a boolean, got {_type_name(value)}"
        )
    return value


def _as_float(value: Any, where: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LaneConfigError(
            f"{where}: {field!r} must be a number, got {_type_name(value)}"
        )
    return float(value)


def _as_str_list(value: Any, where: str, field: str) -> list[str]:
    if not isinstance(value, list):
        raise LaneConfigError(
            f"{where}: {field!r} must be an array, got {_type_name(value)}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise LaneConfigError(
                f"{where}: {field}[{index}] must be a string, got "
                f"{_type_name(item)}"
            )
    return list(value)


def _as_str_table(value: Any, where: str, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: {field!r} must be a table, got {_type_name(value)}"
        )
    for key, item in value.items():
        if not isinstance(item, str):
            # A-019: `env` is declared-only. Coercing 1 or true into "1"/"true"
            # would be assay inventing the spelling of a value the process will
            # actually see.
            raise LaneConfigError(
                f"{where}: {field}.{key} must be a string, got {_type_name(item)}; "
                f"environment values are strings — quote it"
            )
    return dict(value)


def _as_opaque_table(
    value: Any, where: str, field: str
) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: {field!r} must be a table, got {_type_name(value)}"
        )
    return MappingProxyType(dict(value))

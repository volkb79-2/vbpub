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
coverage format                ``judge.coverage.format`` not a key the    A-068
                              parser registry knows
============================  ==========================================  =======

One thing this loader deliberately does **not** reject, because rejecting it
would be inventing policy rather than refusing to invent values:

* a ``[…where]`` table's contents. §7: WHERE is "data assay parses and never
  interprets", permanently. It is carried through verbatim.

It *does* reject ``judge`` config for a rigor level the lane does not declare
(A-062) — ``mutation`` on a lane without ``R2``, ``fail_under`` on an R0 lane.
Inert configuration cannot fail loudly when it is wrong, and it reads to a
human exactly like the capability it is not providing.

`judge.coverage.format` is checked against :data:`assay.coverage.FORMAT_REGISTRY`
(A-068): the closed vocabulary for it is *"a key the parser registry knows"*
(§12), and that registry is imported here rather than duplicated — the same
defence against a second, driftable copy of the key list that this loader
already applies to `scope`/`rigor`/`enforcement` via their own `frozenset`s.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .coverage import FORMAT_REGISTRY
from .errors import LaneConfigError

__all__ = [
    "CanaryConfig",
    "CoverageConfig",
    "ENFORCEMENTS",
    "JUDGE_FIELDS_BY_RIGOR",
    "JudgeConfig",
    "LANE_FILE_NAME",
    "LANE_SCHEMA_VERSION",
    "Lane",
    "LaneFile",
    "MutationConfig",
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
            "base",
        ),
        "R2": ("language", "source_roots", "mutation", "base"),
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
    "base",
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


_MUTATION_FIELDS: tuple[str, ...] = ("jobs", "operators")


@dataclass(frozen=True)
class MutationConfig:
    """``[lanes.X.judge.mutation]`` (P18) -- the closed R2 execution policy:
    a required positive ``jobs`` worker count (A-082/A-122: never derived
    from the running machine, so this loader is the one place a value for
    it can come from at all) and a non-empty, duplicate-free, ORDER-
    preserving ``operators`` list, cross-checked at LOAD time against
    :data:`assay.mutation.MUTATION_OPERATORS` -- the same "vocabulary
    imported from its own owner, never duplicated" discipline
    :data:`assay.coverage.FORMAT_REGISTRY` already gets one field over
    (A-068). Before P18 this table was an opaque, unvalidated passthrough
    (P11/P12 owned construction/execution, not the declared policy); this
    loader is the first reader of its actual shape.
    """

    jobs: int
    operators: tuple[str, ...]

    def as_declared(self) -> dict[str, Any]:
        return {"jobs": self.jobs, "operators": list(self.operators)}


_CANARY_FIELDS: tuple[str, ...] = ("mechanism", "target")


@dataclass(frozen=True)
class CanaryConfig:
    """``[lanes.X.judge.canary]`` (P19) -- the closed R3 declaration: which
    :mod:`assay.canary` mechanism to attempt, and which single source file to
    attempt it against. Exactly two fields, never a plural list (P19 work
    item 2: one R3 claim is one mechanism execution, never several results
    collapsed into schema v3's single canary payload).

    Before P19 this table was opaque and unvalidated (A-106: P09 built the
    mechanism, not the config reader for it); this loader is the first
    reader of its actual shape. ``target`` is the DECLARED string, verbatim
    and PROJECT-relative -- the same spelling :attr:`CoverageConfig.artifact`
    already uses, and the one :func:`~assay.canary.run_python_canary` already
    expects (A-145: repo-relative and project-relative are two spellings of
    the same file, and this loader speaks the project-relative one).
    """

    mechanism: str
    target: str

    def as_declared(self) -> dict[str, Any]:
        return {"mechanism": self.mechanism, "target": self.target}


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
    #: the closed R2 execution policy (P18) -- ``None`` when the lane does
    #: not declare R2 at all.
    mutation: MutationConfig | None
    #: the closed R3 declaration (P19) -- ``None`` when the lane does not
    #: declare R3 at all.
    canary: CanaryConfig | None
    #: (P17) the declared comparison ref R1/R2 diff against -- a git
    #: revision expression (branch name, tag, SHA...), resolved at RUN time
    #: (:func:`assay.git.resolve_base`/:func:`assay.measurability.
    #: check_base_is_head`), never guessed here. Required, never defaulted
    #: to "main" or another assumed ref (A-018's own "no invented values").
    base: str | None

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
            declared["mutation"] = self.mutation.as_declared()
        if self.canary is not None:
            declared["canary"] = self.canary.as_declared()
        if self.base is not None:
            declared["base"] = self.base
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
    # P15 (A-067 finding 9): a name declared in BOTH tables let the ambient
    # process environment silently override a value the lane declared as
    # FIXED (assay.runner.resolve_command_plan builds env_declared then
    # overwrites it with whatever env_passthrough finds present) -- a "fixed"
    # value that changes with whoever runs the lane is not fixed. Refusing
    # the collision here, at load time, makes that overwrite structurally
    # unreachable without touching runner.py at all: it can never see a lane
    # whose two tables disagree about a name's ownership.
    collisions = sorted(set(env) & set(env_passthrough))
    if collisions:
        raise LaneConfigError(
            f"{where}: {', '.join(collisions)} declared in both 'env' (a "
            f"fixed value) and 'env_passthrough' (an ambient name) -- pick "
            f"exactly one. A name in both means the ambient process "
            f"environment silently overrides the fixed value, which makes "
            f"'fixed' a lie."
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
        coverage = _load_coverage(table["coverage"], where, project_root)

    mutation = None
    if "mutation" in table:
        mutation = _load_mutation(table["mutation"], where)
    canary = None
    if "canary" in table:
        canary = _load_canary(table["canary"], where, project_root, source_root_paths)

    base = None
    if "base" in table:
        base = _as_str(table["base"], where, "judge.base")
        if not base:
            raise LaneConfigError(f"{where}: 'judge.base' is empty")

    return JudgeConfig(
        language=language,
        source_roots=source_roots,
        source_root_paths=source_root_paths,
        fail_under=fail_under,
        allow_excluded=allow_excluded,
        coverage=coverage,
        mutation=mutation,
        canary=canary,
        base=base,
    )


def _load_coverage(value: Any, where: str, project_root: Path) -> CoverageConfig:
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
    # P17 (work item 3): the artifact this build will later remove-and-await
    # a fresh copy of, and read back, must resolve inside the project it
    # measures -- the identical containment reasoning
    # `_resolve_source_root` already applies to `source_roots`, one field
    # over. Existence is NOT checked here (A-048's own timing: the artifact
    # is an OUTPUT of the lane's own command, so it need not exist yet at
    # load time).
    if Path(artifact).is_absolute():
        raise LaneConfigError(
            f"{where}: 'judge.coverage.artifact' {artifact!r} is absolute; "
            f"it is relative to the directory containing assay.toml "
            f"({project_root}), the same as source_roots"
        )
    resolved_artifact = (project_root / artifact).resolve()
    if not resolved_artifact.is_relative_to(project_root):
        raise LaneConfigError(
            f"{where}: 'judge.coverage.artifact' {artifact!r} resolves to "
            f"{resolved_artifact}, which is not contained beneath the "
            f"project root {project_root} (via '..' or a symlink) -- a lane "
            f"must not be able to point its coverage artifact outside the "
            f"project it declares"
        )
    if fmt not in FORMAT_REGISTRY:
        # A-068: cross-checked against the parser registry's own keys, not a
        # second hardcoded list, so the vocabulary can never drift out of
        # sync with what P03's registry actually parses.
        raise LaneConfigError(
            f"{where}: 'judge.coverage.format' {fmt!r} is not a format the "
            f"parser registry knows; declared formats: "
            f"{sorted(FORMAT_REGISTRY)}"
        )
    return CoverageConfig(format=fmt, artifact=artifact)


def _load_mutation(value: Any, where: str) -> MutationConfig:
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.mutation' must be a table, got {_type_name(value)}"
        )
    unknown = sorted(set(value) - set(_MUTATION_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.mutation key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join(_MUTATION_FIELDS)}"
        )
    for field in _MUTATION_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.mutation.{field}'"
            )
    jobs = value["jobs"]
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.jobs' must be an integer, got "
            f"{_type_name(jobs)}"
        )
    if jobs < 1:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.jobs' must be a positive integer, got {jobs}"
        )
    operators = _as_str_list(value["operators"], where, "judge.mutation.operators")
    if not operators:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' is empty; a policy that "
            f"selects no operator mutates nothing"
        )
    if len(set(operators)) != len(operators):
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' contains a duplicate: "
            f"{operators}"
        )
    # Deferred, not module-level (A-068's own vocabulary-import discipline,
    # one field over): `assay.mutation` imports `Lane` from THIS module at
    # ITS OWN module level, so importing `assay.mutation` here at module
    # level would be a genuine cycle (config -> mutation -> config) -- the
    # identical reasoning `assay.mutation`'s own module docstring already
    # gives for resolving `execute_command` via a function-body-local
    # import instead of a module-level one. Safe here because by the time a
    # lane is actually being LOADED, both modules have long finished
    # importing, regardless of which one a caller imported first.
    from .mutation import MUTATION_OPERATORS

    unknown_operators = sorted(set(operators) - MUTATION_OPERATORS)
    if unknown_operators:
        raise LaneConfigError(
            f"{where}: 'judge.mutation.operators' names unknown operator(s): "
            f"{', '.join(unknown_operators)}; known operators: "
            f"{', '.join(sorted(MUTATION_OPERATORS))}"
        )
    return MutationConfig(jobs=jobs, operators=tuple(operators))


def _load_canary(
    value: Any,
    where: str,
    project_root: Path,
    source_root_paths: tuple[Path, ...] | None,
) -> CanaryConfig:
    """``judge.canary`` (P19): a closed table, exactly ``mechanism`` and
    ``target`` -- never a plural list (work item 2: one R3 claim is one
    mechanism execution).

    ``target`` must be a normalized, project-relative path to a REAL,
    ordinary source file contained beneath one of the lane's own declared
    ``source_roots`` -- the identical containment discipline
    :func:`_resolve_source_root` already applies to a source root itself,
    reused here rather than re-derived (both compare two already-resolved
    paths via :meth:`~pathlib.Path.is_relative_to`, so a symlink escape or a
    ``..`` traversal is caught the same way for either). Existence IS
    checked at load time (unlike :func:`_load_coverage`'s own artifact,
    which is this run's own OUTPUT and need not exist yet) -- a canary
    target is real source code :mod:`assay.canary` reads and transforms, so
    a typo'd path is a config mistake this loader can catch instead of a
    bare ``FileNotFoundError`` surfacing deep inside a scratch-copy pipeline.

    Whether *target* is itself a TEST path (an adapter-specific question --
    Python's own convention differs from a future second language's) is
    deliberately NOT decided here: this module carries zero adapter
    knowledge anywhere else (``judge.language`` stays an opaque string all
    the way through), and checking it would mean importing one. That
    rejection belongs to :mod:`assay.canary`'s own orchestration, which
    already receives a real, resolved adapter.
    """
    if not isinstance(value, dict):
        raise LaneConfigError(
            f"{where}: 'judge.canary' must be a table, got {_type_name(value)}"
        )
    unknown = sorted(set(value) - set(_CANARY_FIELDS))
    if unknown:
        raise LaneConfigError(
            f"{where}: unknown judge.canary key(s): {', '.join(unknown)}; "
            f"expected only: {', '.join(_CANARY_FIELDS)}"
        )
    for field in _CANARY_FIELDS:
        if field not in value:
            raise LaneConfigError(
                f"{where}: missing required field 'judge.canary.{field}'"
            )

    mechanism = _as_str(value["mechanism"], where, "judge.canary.mechanism")
    # Deferred, not module-level -- the identical reasoning `_load_mutation`
    # already gives for `assay.mutation.MUTATION_OPERATORS`, one field over:
    # `assay.canary` imports `Lane` from THIS module at ITS OWN module
    # level, so a module-level import here would be a genuine cycle
    # (config -> canary -> config).
    from .canary import CANARY_MECHANISMS

    if mechanism not in CANARY_MECHANISMS:
        raise LaneConfigError(
            f"{where}: 'judge.canary.mechanism' {mechanism!r} is not one of "
            f"{sorted(CANARY_MECHANISMS)}"
        )

    target = _as_str(value["target"], where, "judge.canary.target")
    if not target:
        raise LaneConfigError(f"{where}: 'judge.canary.target' is empty")
    candidate = Path(target)
    if candidate.is_absolute():
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} is absolute; it is "
            f"relative to the project root, the same as source_roots"
        )
    raw_path = project_root / candidate
    if raw_path.is_symlink():
        # Checked first, exactly like `_is_unsafe_coverage_artifact`'s own
        # ordering (`runner.py`): `is_symlink` never raises for a
        # non-existent path, unlike a naive existence check that follows
        # the link first.
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} is a symlink; a "
            f"canary target must be a real, ordinary source file"
        )
    resolved = raw_path.resolve()
    roots = source_root_paths or ()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} resolves to "
            f"{resolved}, which is not contained beneath any declared "
            f"source root {[str(root) for root in roots]} (via '..' or a "
            f"symlink) -- a canary target must live beneath a declared "
            f"source root"
        )
    if not resolved.is_file():
        raise LaneConfigError(
            f"{where}: 'judge.canary.target' {target!r} does not exist as "
            f"a file under the project root {project_root} (looked for "
            f"{resolved})"
        )
    return CanaryConfig(mechanism=mechanism, target=target)


def _resolve_source_root(raw: str, where: str, project_root: Path) -> Path:
    """Resolve one declared source root against the PROJECT root (A-049).

    Not the repo root and not the process cwd: the lane file must not need to
    know where it sits inside a monorepo, and a project vendored one level
    deeper must not silently start measuring nothing.

    *project_root* is already fully resolved (:func:`load_lane_file` resolves
    ``assay.toml``'s own path before taking its parent), so the CONTAINMENT
    check below (P15, A-067 finding 9: "a lane can measure a sibling
    project despite the diagnostic claiming containment") compares two
    already-resolved paths: rejecting a raw string that merely
    LOOKS relative (no leading ``/``, no ``..`` visible in *raw* itself) is
    not enough, because ``../sibling`` is exactly such a string, and a
    symlink inside the project root can point anywhere on disk regardless of
    what *raw* spells. ``Path.resolve()`` collapses BOTH ``..`` components
    and symlinks to their real final target, so comparing the two resolved
    paths catches either escape route the same way.
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
    if not resolved.is_relative_to(project_root):
        raise LaneConfigError(
            f"{where}: source root {raw!r} resolves to {resolved}, which is "
            f"not contained beneath the project root {project_root} (via "
            f"'..' or a symlink) -- a lane must not be able to measure a "
            f"tree outside the project it declares"
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

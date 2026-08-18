#!/usr/bin/env python3
"""P34/W9 real-PostgreSQL qualification at a pinned dstdns revision.

W0-W8 shipped assay's SQL/DDL mutation adapter (``src/assay/adapters/sql.py``,
carve ``nyxloom-trove/W3-CARVE-P34-sql-adapter.md``) and a real SQL lane has
been driven end to end producing a real ``killed`` mutant -- but every
catalog-level claim in the carve's own §9 (M5, M6, M9, M10, M11, M15) was a
HAND-RUN measurement, never re-run by anything committed. This module is the
one thing still missing: re-runnable evidence that the generated mutants MEAN
something against a real PostgreSQL 18.4 catalog, not merely that they change
bytes a diff can see.

**A-280 -- dstdns's OWN ``scripts/schema-gate.sh`` is NOT this command and is
never executed.** Measured (carve §9 M-R7, decisions.md A-280): the pinned
blob writes no dump at all, exits 2 immediately under a bare invocation
(its first positional argument is mandatory), and drives ``docker`` against
the *deployed* app network -- none of which exists inside a throwaway
qualification container. Its blob is pinned below only as evidence of what
the consumer has today; this module ships its OWN self-contained command,
``gate/python/fixtures/dstdns-sql/schema-gate.sh`` (A-280), and is the only
thing that ever executes it.

**Three oracles (carve §7 O3/O4/O5), each with a paired control:**

* **O3 -- span fidelity against a real catalog.** Seven real dstdns sites
  (one per ``sql:*`` operator, all drawn from the pinned corpus -- never a
  constructed fixture) are discovered with the REAL, shipped
  :class:`assay.adapters.sql.SqlAdapter`, applied to a throwaway PostgreSQL
  18.4, and each catalog delta is checked against a baseline built from the
  SAME unmutated corpus. The paired control is a HAND-CONSTRUCTED invalid
  mutant (carve §9 M11's own naive string-literal widen of an integer
  ``IN``-list) that the shipped adapter's own literal-shape-awareness would
  never generate -- proving the harness's apply step actually discriminates
  valid DDL from invalid DDL, rather than trivially reporting success.
* **O4 -- the residue false-survival, converted to a refusal.** The
  identical ``sql:drop-not-null`` mutant (dstdns's own
  ``corpora.name NOT NULL`` -- carve §1's own opening example) is applied
  once to a FRESH database (``is_nullable`` becomes ``YES``) and once to a
  database that already carries the unmutated schema (``is_nullable`` stays
  ``NO`` -- the mutation never happened, a real false-kill trap). The
  residue run's schema dump is byte-identical to baseline's; the fresh
  run's is not.
* **O5 -- the ``pg_dump`` reproducibility trap.** Two dumps of an unchanged
  database are byte-identical WITH a pinned ``--restrict-key`` and differ
  WITHOUT one (``pg_dump`` 18 emits a random ``\\restrict``/``\\unrestrict``
  key on every invocation otherwise). Exercised directly by
  ``tests/test_gate_qualify_dstdns_sql.py -k restrict_key``, not by this
  module's CLI.

**Comparator choice (the carve review's own instruction).** The carve says
to follow ``qualify_cmru_b006a.py``'s SHAPE (pin, export a disposable copy,
stub only environment-bound subprocess seams); the review says the
expected-artifact comparator should follow ``qualify_topos.py``'s
validate-then-placeholder approach instead of cmru's field-by-field
assertions. :func:`normalize_verdict` does exactly that: it independently
CHECKS the handful of fields whose real value this module knows out of band
(``assay_version``, the disposable HEAD commit, the resolved base, non-empty
timestamps), then replaces exactly those fields with placeholder tokens and
compares the WHOLE remaining document against the frozen witness with `==`
-- so a corruption anywhere else in the document (a reclassified mutant, a
dropped field, a silently different rigor) fails the comparison, which a
cmru-style "assert these specific buckets are empty" approach would not
catch.

**Never imports assay for the O3/O4/O5 direct-catalog oracles' own
subprocess boundary** (docker/git/psql/pg_dump are real subprocesses,
never stubbed in production code -- only a committed test may stub an
environment-bound seam). :func:`capture_witness` is the one function that
DOES import :mod:`assay.cli` directly, because unlike cmru/topos (which
qualify an EXTERNAL consumer's separately installed wheel) W9 is
qualifying assay's OWN adapter code in THIS checkout against a real
database -- there is no separate wheel boundary to preserve here, and
"the real shipped assay code" plainly includes running it.

**Environment.** ``postgres:18-alpine`` is used as pulled, provisioned with
``--network none`` (A-030 binds ``src/assay/**``, never this harness; this
module DOES shell out to docker, deliberately). Real dstdns DDL requires the
TimescaleDB extension for hypertables/continuous-aggregates/retention
policies (``08-create-hypertables.sql``, ``11-create-continuous-
aggregates.sql``, ``12-retention-policies.sql``) and a self-test block in
``10-analytics-procedures.sql`` that calls TimescaleDB's own
``time_bucket()``; none of that is installed in vanilla
``postgres:18-alpine`` and this qualification environment has no network
path to a TimescaleDB-carrying image. :data:`EXCLUDED_BASENAMES` excludes
exactly those four files -- verified (by hand, reading every remaining
file) to introduce no dangling reference, since nothing outside those four
files queries a hypertable, a continuous aggregate, or ``time_bucket``. This
is an ENVIRONMENT limitation of the qualification harness, not of assay's
adapter or of the seven real mutation sites below, none of which touch a
TimescaleDB object.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
# Unconditional: a duplicate sys.path entry is harmless, and a guard here
# would be an import-order-dependent branch this module's own test suite
# cannot exercise both sides of (conftest.py already puts src/ on sys.path
# before this module is ever loaded under pytest) -- exactly the
# unreachable-arc trap A-124/A-131 name as a defect, not a decoration.
sys.path.insert(0, str(_SRC_ROOT))

from assay.adapters.sql import SqlAdapter  # noqa: E402
from assay.mutation import MutationSite  # noqa: E402

# --- pinned inputs (carve §9 M17), verified before anything runs -----------

DSTDNS_COMMIT = "151cda0d6fca018c31e781673c19b4bad41179a8"
DSTDNS_TREE = "113154e6f66440b8e193c502f7b4c213be28ee86"
INIT_SCRIPTS_TREE = "820d4c3cdfd38f0b3e29bfb9918febd2f2e1ada2"
#: Retained ONLY as evidence of what the consumer has today (A-280) -- this
#: module never reads or executes the blob this names.
SCHEMA_GATE_BLOB = "88de912d52d5552a23630f68871d4f12e2a9eb83"
REVIEW_BLOB = "fc1a694d47650b1ae04e73e71ba20c8a39bcdc11"

INIT_SCRIPTS_PATH = "infra/db-init/init-scripts"

POSTGRES_IMAGE = "postgres:18-alpine"
#: Proven against real pg_dump 18.4 (a hyphenated key is rejected as
#: "invalid restrict key"); kept identical to the carve's own §9 M8 value.
RESTRICT_KEY = "assayfixedkey0000000000000000000000000000000000000000000000000000"

ALL_OPERATORS: tuple[str, ...] = (
    "sql:drop-check",
    "sql:drop-unique",
    "sql:drop-not-null",
    "sql:drop-foreign-key",
    "sql:weaken-delete-action",
    "sql:drop-trigger",
    "sql:widen-check-in",
)

#: dstdns's own numbered-glob apply order excludes 95-/99- (marker/seed,
#: applied separately -- scripts/schema-apply.sh, read verbatim). The other
#: three are excluded for THIS environment only -- see the module docstring.
_DEFERRED_PREFIXES = ("95-", "99-")
EXCLUDED_BASENAMES: frozenset[str] = frozenset(
    {
        "08-create-hypertables.sql",
        "11-create-continuous-aggregates.sql",
        "12-retention-policies.sql",
        "10-analytics-procedures.sql",
    }
)

#: The service roles dstdns's own 90-grant-permissions.sh grants to
#: (defaults, read verbatim from that file); real deployments create them
#: via infra provisioning OUTSIDE the tracked .sql corpus. A throwaway
#: qualification database needs them pre-created for the tracked GRANT
#: statements inside the numbered scripts themselves to succeed.
SERVICE_ROLES: tuple[str, ...] = ("controller", "workerdb", "webapp")

GATE_SCRIPT_HOST_PATH = Path(__file__).resolve().parent / "fixtures" / "dstdns-sql" / "schema-gate.sh"


class QualificationError(RuntimeError):
    """A frozen qualification premise or an independent comparison failed."""


# --- the one subprocess boundary --------------------------------------------


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input: str | None = None,  # noqa: A002 - matches precedent's own name
    timeout: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,  # hang failsafe only
        check=False,
    )
    if check and proc.returncode:
        raise QualificationError(
            f"command failed ({proc.returncode}): {list(argv)!r}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _env_with(overrides: Mapping[str, str]) -> dict[str, str]:
    return {**os.environ, **overrides}


def _git_commit(repo: Path, message: str, *, env: Mapping[str, str]) -> None:
    """A fixed-identity commit over the currently-staged index -- factored
    out so both of :func:`capture_witness`'s commits go through the same
    ``_run`` error-reporting path as every other subprocess in this module,
    rather than a bare ``subprocess.run(check=True)``."""
    _run(["git", "-C", str(repo), "commit", "-q", "-m", message], env=_env_with(env), check=True)


# --- input verification (M17) -----------------------------------------------


def verify_pinned_inputs(source_repo: Path) -> None:
    """Refuse drift before creating a scratch directory, a container, or
    anything else. All four pins are checked; the first mismatch raises."""
    resolved = _run(["git", "-C", str(source_repo), "rev-parse", f"{DSTDNS_COMMIT}^{{commit}}"]).stdout.strip()
    if resolved != DSTDNS_COMMIT:
        raise QualificationError(
            f"the pinned DSTDNS_COMMIT {DSTDNS_COMMIT!r} is not reachable exactly (resolved to {resolved!r})"
        )
    tree = _git(source_repo, "rev-parse", f"{DSTDNS_COMMIT}^{{tree}}")
    if tree != DSTDNS_TREE:
        raise QualificationError(f"the pinned DSTDNS_TREE does not match: expected {DSTDNS_TREE!r}, got {tree!r}")
    init_tree = _git(source_repo, "rev-parse", f"{DSTDNS_COMMIT}:{INIT_SCRIPTS_PATH}")
    if init_tree != INIT_SCRIPTS_TREE:
        raise QualificationError(
            f"the pinned INIT_SCRIPTS_TREE does not match: expected {INIT_SCRIPTS_TREE!r}, got {init_tree!r}"
        )
    gate_blob = _git(source_repo, "rev-parse", f"{DSTDNS_COMMIT}:scripts/schema-gate.sh")
    if gate_blob != SCHEMA_GATE_BLOB:
        raise QualificationError(
            f"the pinned SCHEMA_GATE_BLOB does not match: expected {SCHEMA_GATE_BLOB!r}, got {gate_blob!r}"
        )
    review_blob = _git(source_repo, "rev-parse", f"{DSTDNS_COMMIT}:docs/proposals/cw2-p85-wave/REVIEW-CW2A.md")
    if review_blob != REVIEW_BLOB:
        raise QualificationError(
            f"the pinned REVIEW_BLOB does not match: expected {REVIEW_BLOB!r}, got {review_blob!r}"
        )


# --- exporting the pinned corpus (real git, no docker) ----------------------


def list_corpus_basenames(source_repo: Path) -> tuple[str, ...]:
    """The pinned init-scripts tree's own ``*.sql`` basenames, in dstdns's
    OWN apply order (bytewise sort -- ``schema-apply.sh``'s own
    ``LC_ALL=C``), excluding 95-/99- (applied separately by dstdns) and
    :data:`EXCLUDED_BASENAMES` (this environment's TimescaleDB gap)."""
    listing = _run(["git", "-C", str(source_repo), "ls-tree", "--name-only", DSTDNS_COMMIT, f"{INIT_SCRIPTS_PATH}/"])
    names = []
    for line in listing.stdout.splitlines():
        basename = Path(line).name
        if not basename.endswith(".sql"):
            continue
        if basename.startswith(_DEFERRED_PREFIXES):
            continue
        if basename in EXCLUDED_BASENAMES:
            continue
        names.append(basename)
    return tuple(sorted(names))


def export_corpus(source_repo: Path) -> dict[str, bytes]:
    """``git show <pinned-blob>`` for every applied-order basename -- byte
    exact, never touching the working tree. Returns ``{basename: bytes}``."""
    corpus: dict[str, bytes] = {}
    for basename in list_corpus_basenames(source_repo):
        proc = _run(
            [
                "git",
                "-C",
                str(source_repo),
                "show",
                f"{DSTDNS_COMMIT}:{INIT_SCRIPTS_PATH}/{basename}",
            ]
        )
        corpus[basename] = proc.stdout.encode("utf-8")
    return corpus


def write_corpus(corpus: Mapping[str, bytes], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for basename, content in corpus.items():
        (destination / basename).write_bytes(content)


# --- discovering real mutation sites with the REAL, shipped adapter --------


def discover_sites(text: str, *, operators: tuple[str, ...] = ALL_OPERATORS, limit: int = 500) -> tuple[MutationSite, ...]:
    """Every real site the SHIPPED :class:`SqlAdapter` finds in *text*, over
    ALL lines (this harness selects representative real sites from the
    whole pinned file, not from a changed-lines diff -- :func:`capture_witness`
    is the one code path that instead drives assay's own diff-bounded R2)."""
    adapter = SqlAdapter()
    lines = set(range(1, text.count("\n") + 3))
    result = adapter.generate_mutation_sites(text, lines, operators=operators, limit=limit)
    assert result != "UNSUPPORTED"  # SQL never returns the marker (A-242)
    return result


# --- the seven real scenarios (one per operator), pinned by exact span -----


@dataclass(frozen=True, kw_only=True)
class CatalogScenario:
    """One real dstdns mutation site and the ``psql`` query that observes
    its catalog effect. *query* returns exactly one row/column; its value
    is compared byte-for-byte against *baseline_expected* (on the unmutated
    corpus) and *mutant_expected* (on the corpus with this site's own
    mutation applied) -- both pinned from a real measurement (this module's
    own carving session), never computed at runtime, so a silent adapter
    regression changes the OBSERVED value without moving the assertion."""

    name: str
    operator: str
    file: str
    lineno: int
    start_byte: int
    end_byte: int
    description: str
    query: str
    baseline_expected: str
    mutant_expected: str


SCENARIOS: tuple[CatalogScenario, ...] = (
    CatalogScenario(
        name="drop-not-null-corpora-name",
        operator="sql:drop-not-null",
        file="20-create-corpora.sql",
        lineno=9,
        start_byte=487,
        end_byte=495,
        description="NOT NULL -> NULL",
        query="SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name='corpora' AND column_name='name'",
        baseline_expected="NO",
        mutant_expected="YES",
    ),
    CatalogScenario(
        name="drop-unique-result-inbox-index",
        operator="sql:drop-unique",
        file="03c-create-workflow-core.sql",
        lineno=372,
        start_byte=19169,
        end_byte=19188,
        description="CREATE UNIQUE INDEX -> CREATE INDEX",
        query="SELECT indisunique FROM pg_index ix "
        "JOIN pg_class c ON c.oid = ix.indexrelid "
        "WHERE c.relname='uq_result_inbox_accepted_per_work_unit'",
        baseline_expected="t",
        mutant_expected="f",
    ),
    CatalogScenario(
        # NOTE: the earlier candidate site (envelope_version's own unnamed
        # CHECK, line 266) is a real, measured DIVERGENCE from the carve's
        # own §9 M6 fixture, which used an explicitly-NAMED constraint: an
        # UNNAMED column-level CHECK is auto-named from the COLUMN it
        # references (`outbox_events_envelope_version_check`), and once its
        # body becomes `CHECK (true)` -- referencing no column -- PostgreSQL
        # re-derives the auto name to the table-level fallback
        # (`outbox_events_check`), so the "empty name delta" M6 claims does
        # NOT hold for an auto-named constraint (measured, this session).
        # `ck_outbox_events_published_timestamp` is EXPLICITLY named in the
        # real corpus (line 290), so its name is authored, not derived, and
        # the empty name delta holds exactly as M6 states.
        name="drop-check-outbox-events-published-timestamp",
        operator="sql:drop-check",
        file="03c-create-workflow-core.sql",
        lineno=290,
        start_byte=15426,
        end_byte=15551,
        description="CHECK (...) -> CHECK (true)",
        query="SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='ck_outbox_events_published_timestamp'",
        baseline_expected="CHECK ((((published = true) AND (published_at IS NOT NULL)) "
        "OR ((published = false) AND (published_at IS NULL))))",
        mutant_expected="CHECK (true)",
    ),
    CatalogScenario(
        name="drop-foreign-key-fk-corpora-current-version",
        operator="sql:drop-foreign-key",
        file="21-create-workflow-corpus.sql",
        lineno=332,
        start_byte=16628,
        end_byte=16710,
        description="FOREIGN KEY (...) REFERENCES ... -> CHECK (true)",
        query="SELECT contype::text FROM pg_constraint WHERE conname='fk_corpora_current_version'",
        baseline_expected="f",
        mutant_expected="c",
    ),
    CatalogScenario(
        name="weaken-delete-action-fk-runs-corpus-version",
        operator="sql:weaken-delete-action",
        file="21-create-workflow-corpus.sql",
        lineno=327,
        start_byte=16414,
        end_byte=16422,
        description="ON DELETE RESTRICT -> ON DELETE CASCADE",
        query="SELECT confdeltype::text FROM pg_constraint WHERE conname='fk_runs_corpus_version'",
        baseline_expected="r",
        mutant_expected="c",
    ),
    CatalogScenario(
        name="drop-trigger-corpus-versions-immutable",
        operator="sql:drop-trigger",
        file="21-create-workflow-corpus.sql",
        lineno=84,
        start_byte=4330,
        end_byte=4480,
        description="CREATE TRIGGER ...; -> SELECT 1;",
        query="SELECT count(*)::text FROM pg_trigger "
        "WHERE tgname='trg_corpus_versions_immutable' AND NOT tgisinternal",
        baseline_expected="1",
        mutant_expected="0",
    ),
    CatalogScenario(
        name="widen-check-in-result-inbox-envelope-version",
        operator="sql:widen-check-in",
        file="03c-create-workflow-core.sql",
        lineno=331,
        start_byte=17127,
        end_byte=17128,
        description="IN (...) widened with 2",
        query="SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='result_inbox'::regclass "
        "AND conname LIKE '%envelope_version%' AND contype='c'",
        baseline_expected="CHECK ((envelope_version = 1))",
        mutant_expected="CHECK ((envelope_version = ANY (ARRAY[1, 2])))",
    ),
)

assert {s.operator for s in SCENARIOS} == set(ALL_OPERATORS), "every sql:* operator needs exactly one real scenario"
assert len({s.name for s in SCENARIOS}) == len(SCENARIOS), "scenario names must be unique"


def find_scenario_site(sites_by_file: Mapping[str, tuple[MutationSite, ...]], scenario: CatalogScenario) -> MutationSite:
    """The REAL discovered site matching *scenario* exactly -- never
    trusted from the pinned span alone: (file, lineno, operator,
    start_byte, end_byte, description) must ALL agree, or the pinned corpus
    drifted from what this scenario was measured against and the mismatch
    is a defect to surface, not a value to silently re-derive."""
    for site in sites_by_file.get(scenario.file, ()):
        if (
            site.lineno == scenario.lineno
            and site.operator == scenario.operator
            and site.start_byte == scenario.start_byte
            and site.end_byte == scenario.end_byte
        ):
            if site.description != scenario.description:
                raise QualificationError(
                    f"scenario {scenario.name!r}: site description drifted: "
                    f"expected {scenario.description!r}, got {site.description!r}"
                )
            return site
    raise QualificationError(
        f"scenario {scenario.name!r}: no discovered site in {scenario.file!r} matches "
        f"(lineno={scenario.lineno}, operator={scenario.operator!r}, "
        f"span=({scenario.start_byte},{scenario.end_byte})) -- the pinned corpus drifted"
    )


#: The paired invalid-mutant control (carve §9 M11): the SAME closing-paren
#: span :data:`SCENARIOS`'s widen-check-in entry names, but with the naive,
#: type-mismatched replacement the shipped adapter's own literal-shape rule
#: (§3.2 rule 3) refuses to ever generate -- constructed BY HAND, never
#: through :class:`SqlAdapter`, because the adapter would not produce it.
INVALID_CONTROL_FILE = "03c-create-workflow-core.sql"
INVALID_CONTROL_SITE = MutationSite(
    start_byte=17127,
    end_byte=17128,
    replacement=b", '__assay_widened__')",
    lineno=331,
    operator="sql:widen-check-in",
    description="HAND-CONSTRUCTED INVALID CONTROL (M11): naive string-literal widen of an integer IN-list",
)


def apply_mutation(corpus: Mapping[str, bytes], file: str, site: MutationSite) -> dict[str, bytes]:
    """A COPY of *corpus* with *site* applied to *file* -- every other file
    byte-identical to *corpus*, never mutated in place."""
    mutated = dict(corpus)
    mutated[file] = site.apply(corpus[file])
    return mutated


# --- the throwaway, network-isolated PostgreSQL 18.4 ------------------------


class ThrowawayPostgres:
    """A single ``postgres:18-alpine`` container, ``--network none`` (rule
    6): every interaction is ``docker exec``/``docker cp`` over the docker
    socket, never a TCP connection from the host -- the socket needs no
    network namespace at all. Always removed, even on a failed provision."""

    def __init__(self) -> None:
        self.name = f"assay-p34w9-{uuid.uuid4().hex[:12]}"
        self._started = False

    def __enter__(self) -> "ThrowawayPostgres":
        _run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--network",
                "none",
                "--name",
                self.name,
                "-e",
                "POSTGRES_PASSWORD=assay-p34-w9",
                POSTGRES_IMAGE,
            ]
        )
        self._started = True
        try:
            self._wait_ready()
            self.copy_in(GATE_SCRIPT_HOST_PATH, "/schema-gate.sh")
            for role in SERVICE_ROLES:
                self._create_role_with_retry(role)
        except Exception:
            self._remove()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._remove()

    def _remove(self) -> None:
        if self._started:
            _run(["docker", "rm", "-f", self.name], check=False)
            self._started = False

    def _wait_ready(self, attempts: int = 60) -> None:
        """``pg_isready`` alone proved insufficient (measured, this carving
        session -- a real, if narrow, race where the socket accepted
        connections a moment before role/database DDL reliably succeeded).
        The readiness gate is therefore a REAL round-trip query, not merely
        a TCP/socket probe."""
        for _ in range(attempts):
            proc = _run(["docker", "exec", self.name, "pg_isready", "-U", "postgres"], check=False, timeout=10)
            if proc.returncode == 0:
                probe = _run(["docker", "exec", self.name, "psql", "-U", "postgres", "-tAc", "SELECT 1"], check=False, timeout=10)
                if probe.returncode == 0 and probe.stdout.strip() == "1":
                    return
            time.sleep(1)
        raise QualificationError(f"postgres container {self.name!r} never became ready after {attempts} attempts")

    def _create_role_with_retry(self, role: str, *, attempts: int = 5) -> None:
        last: subprocess.CompletedProcess[str] | None = None
        for attempt in range(attempts):
            last = self.exec(["psql", "-U", "postgres", "-c", f"CREATE ROLE {role} LOGIN;"], check=False)
            if last.returncode == 0:
                return
            time.sleep(0.5 * (attempt + 1))
        assert last is not None
        raise QualificationError(
            f"CREATE ROLE {role!r} did not succeed after {attempts} attempts in container "
            f"{self.name!r}: {last.stderr[-1000:]}"
        )

    def exec(self, argv: Sequence[str], *, input: str | None = None, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:  # noqa: A002
        return _run(["docker", "exec", "-i", self.name, *argv], input=input, check=check, timeout=timeout)

    def copy_in(self, host_path: Path, container_path: str) -> None:
        _run(["docker", "cp", str(host_path), f"{self.name}:{container_path}"])

    def replace_corpus(self, host_dir: Path) -> None:
        """Atomically swap the container's ``/corpus`` for *host_dir*'s
        contents -- always a full copy, never an incremental sync, so a
        scenario cannot see a stale file left over from a previous one."""
        self.exec(["rm", "-rf", "/corpus", "/corpus_new"], check=False)
        _run(["docker", "cp", str(host_dir), f"{self.name}:/corpus_new"])
        self.exec(["mv", "/corpus_new", "/corpus"])

    def create_database(self, name: str) -> None:
        self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {name};"])
        self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-c", f"CREATE DATABASE {name};"])

    def query_one(self, dbname: str, sql: str) -> str:
        proc = self.exec(["psql", "-v", "ON_ERROR_STOP=1", "-tAX", "-U", "postgres", "-d", dbname, "-c", sql])
        return proc.stdout.strip()

    def pg_dump(self, dbname: str, *, restrict_key: str | None) -> bytes:
        argv = ["pg_dump", "--schema-only", "--no-owner", "-U", "postgres", "-d", dbname]
        if restrict_key is not None:
            argv.append(f"--restrict-key={restrict_key}")
        return self.exec(argv).stdout.encode("utf-8")

    def path_exists(self, container_path: str) -> bool:
        proc = self.exec(["test", "-f", container_path], check=False)
        return proc.returncode == 0

    def run_gate_script(self, *, dbname: str, dump_path: str, kill_signal_path: str, restrict_key: str, test_cmd: str) -> subprocess.CompletedProcess[str]:
        """Run the REAL, self-contained ``schema-gate.sh`` (A-280) inside
        this container -- ``apply && dump && test``, dump self-enforced
        reproducible (NB-6). Never raises on a non-zero exit: a scenario
        deliberately expecting the apply/dump step to refuse reads
        ``returncode`` itself."""
        self.exec(["rm", "-f", dump_path, kill_signal_path], check=False)
        return self.exec(
            [
                "env",
                "SCHEMA_GATE_INIT_SCRIPTS_DIR=/corpus",
                f"SCHEMA_GATE_DBNAME={dbname}",
                f"SCHEMA_GATE_DUMP_PATH={dump_path}",
                f"SCHEMA_GATE_KILL_SIGNAL_PATH={kill_signal_path}",
                f"SCHEMA_GATE_RESTRICT_KEY={restrict_key}",
                f"SCHEMA_GATE_TEST_CMD={test_cmd}",
                "sh",
                "/schema-gate.sh",
            ],
            check=False,
        )


# --- O5: the pg_dump reproducibility trap, and its must-succeed control ----


def verify_dump_reproducible(container: ThrowawayPostgres, dbname: str, *, restrict_key: str | None) -> bytes:
    """Dump *dbname* (schema-only) twice. WITH a pinned *restrict_key* the
    two dumps must be byte-identical (returned); WITHOUT one this raises,
    naming ``\\restrict`` (carve §9 M7/M8, O5)."""
    first = container.pg_dump(dbname, restrict_key=restrict_key)
    second = container.pg_dump(dbname, restrict_key=restrict_key)
    if first != second:
        raise QualificationError(
            "two pg_dump invocations of the SAME unchanged database produced "
            "different bytes -- pg_dump 18 emits a random \\restrict/\\unrestrict "
            f"key on every invocation unless --restrict-key is pinned (restrict_key={restrict_key!r})"
        )
    return first


# --- O3/O4 pure verdicts over an already-run gate result --------------------
#
# Factored out of the two orchestrators below so the DECISION each makes is
# directly unit-testable over a constructed :class:`subprocess.
# CompletedProcess`/dict, mirroring `qualify_cmru_b006a.py`'s own split
# between real-environment orchestration and pure `check_*` functions --
# never requiring a live container merely to prove a comparison is correct.


def _require_gate_applied(result: subprocess.CompletedProcess[str], dump_present: bool, *, context: str) -> None:
    if result.returncode != 0 or not dump_present:
        raise QualificationError(
            f"{context} (exit {result.returncode}): {result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


def _require_scenario_baseline_matches(scenario: CatalogScenario, observed: str) -> None:
    if observed != scenario.baseline_expected:
        raise QualificationError(
            f"scenario {scenario.name!r}: baseline catalog value drifted: "
            f"expected {scenario.baseline_expected!r}, observed {observed!r}"
        )


def _require_scenarios_ok(scenario_reports: Sequence[Mapping[str, Any]]) -> None:
    for report in scenario_reports:
        if not report["applied"]:
            raise QualificationError(f"scenario {report['name']!r} did not apply cleanly: {report}")
        if not report["matches_operator"]:
            raise QualificationError(f"scenario {report['name']!r} catalog delta did not match its operator: {report}")


def _require_invalid_control_refused(invalid_report: Mapping[str, Any]) -> None:
    if invalid_report["gate_returncode"] == 0 or invalid_report["dump_present"]:
        raise QualificationError(f"the invalid-mutant control unexpectedly applied cleanly: {invalid_report}")


def _require_o4_gate_applied(result: subprocess.CompletedProcess[str], *, context: str) -> None:
    if result.returncode != 0:
        raise QualificationError(f"{context}: {result.stderr[-2000:]}")


def _require_o4_invariants(report: Mapping[str, Any]) -> None:
    if report["fresh_is_nullable"] != "YES":
        raise QualificationError(f"the fresh run did not observe the mutation: {report}")
    if report["residue_is_nullable"] != "NO":
        raise QualificationError(
            f"the residue run unexpectedly observed the mutation (no false-survival to convert): {report}"
        )
    if not report["fresh_dump_differs_from_baseline"]:
        raise QualificationError(f"the fresh mutant's dump did not differ from baseline: {report}")
    if not report["residue_dump_equals_baseline"]:
        raise QualificationError(f"the residue run's dump was not byte-identical to baseline: {report}")


# --- O3: span fidelity against a real catalog --------------------------------


def run_o3_span_fidelity(*, source_repo: Path, scratch: Path) -> dict[str, Any]:
    verify_pinned_inputs(source_repo)
    scratch.mkdir(parents=True)
    baseline_corpus = export_corpus(source_repo)

    sites_by_file: dict[str, tuple[MutationSite, ...]] = {}
    for file in sorted({scenario.file for scenario in SCENARIOS}):
        sites_by_file[file] = discover_sites(baseline_corpus[file].decode("utf-8"))
    resolved_sites = {scenario.name: find_scenario_site(sites_by_file, scenario) for scenario in SCENARIOS}

    baseline_dir = scratch / "baseline"
    write_corpus(baseline_corpus, baseline_dir)

    scenario_reports: list[dict[str, Any]] = []
    with ThrowawayPostgres() as container:
        container.replace_corpus(baseline_dir)
        container.create_database("baseline")
        baseline_gate = container.run_gate_script(
            dbname="baseline",
            dump_path="/dump.sql",
            kill_signal_path="/kill.txt",
            restrict_key=RESTRICT_KEY,
            test_cmd="true",
        )
        _require_gate_applied(
            baseline_gate, container.path_exists("/dump.sql"), context="the unmutated baseline corpus did not apply cleanly"
        )
        baseline_dump = container.pg_dump("baseline", restrict_key=RESTRICT_KEY)

        for scenario in SCENARIOS:
            site = resolved_sites[scenario.name]
            baseline_observed = container.query_one("baseline", scenario.query)
            _require_scenario_baseline_matches(scenario, baseline_observed)

            mutant_corpus = apply_mutation(baseline_corpus, scenario.file, site)
            mutant_dir = scratch / scenario.name
            write_corpus(mutant_corpus, mutant_dir)
            container.replace_corpus(mutant_dir)
            db_name = f"mutant_{scenario.name.replace('-', '_')}"
            container.create_database(db_name)
            gate = container.run_gate_script(
                dbname=db_name,
                dump_path="/dump.sql",
                kill_signal_path="/kill.txt",
                restrict_key=RESTRICT_KEY,
                test_cmd="true",
            )
            applied = gate.returncode == 0 and container.path_exists("/dump.sql")
            mutant_observed = container.query_one(db_name, scenario.query) if applied else None
            mutant_dump = container.pg_dump(db_name, restrict_key=RESTRICT_KEY) if applied else None
            scenario_reports.append(
                {
                    "name": scenario.name,
                    "operator": scenario.operator,
                    "file": scenario.file,
                    "lineno": scenario.lineno,
                    "gate_returncode": gate.returncode,
                    "applied": applied,
                    "baseline_observed": baseline_observed,
                    "mutant_observed": mutant_observed,
                    "matches_operator": applied and mutant_observed == scenario.mutant_expected,
                    "dump_differs_from_baseline": mutant_dump is not None and mutant_dump != baseline_dump,
                }
            )

        # the paired must-succeed control: a HAND-CONSTRUCTED invalid mutant
        # the shipped adapter would never generate, over the SAME closing-
        # paren span the widen-check-in scenario names (carve §9 M11).
        invalid_corpus = apply_mutation(baseline_corpus, INVALID_CONTROL_FILE, INVALID_CONTROL_SITE)
        invalid_dir = scratch / "invalid-control"
        write_corpus(invalid_corpus, invalid_dir)
        container.replace_corpus(invalid_dir)
        container.create_database("invalid_control")
        invalid_gate = container.run_gate_script(
            dbname="invalid_control",
            dump_path="/dump.sql",
            kill_signal_path="/kill.txt",
            restrict_key=RESTRICT_KEY,
            test_cmd="true",
        )
        invalid_report = {
            "gate_returncode": invalid_gate.returncode,
            "dump_present": container.path_exists("/dump.sql"),
            "stderr_tail": invalid_gate.stderr[-500:],
        }

    _require_scenarios_ok(scenario_reports)
    _require_invalid_control_refused(invalid_report)

    return {
        "dstdns_commit": DSTDNS_COMMIT,
        "postgres_image": POSTGRES_IMAGE,
        "restrict_key": RESTRICT_KEY,
        "scenarios": scenario_reports,
        "invalid_control": invalid_report,
    }


# --- O4: the residue false-survival, converted to a refusal -----------------


def run_o4_residue_probe(*, source_repo: Path, scratch: Path) -> dict[str, Any]:
    verify_pinned_inputs(source_repo)
    scratch.mkdir(parents=True)
    baseline_corpus = export_corpus(source_repo)
    scenario = next(s for s in SCENARIOS if s.name == "drop-not-null-corpora-name")
    sites = discover_sites(baseline_corpus[scenario.file].decode("utf-8"))
    site = find_scenario_site({scenario.file: sites}, scenario)
    mutant_corpus = apply_mutation(baseline_corpus, scenario.file, site)

    baseline_dir = scratch / "baseline"
    mutant_dir = scratch / "mutant"
    write_corpus(baseline_corpus, baseline_dir)
    write_corpus(mutant_corpus, mutant_dir)

    with ThrowawayPostgres() as container:
        # -- the FRESH run: mutant applied to a database that never carried
        # the unmutated schema.
        container.replace_corpus(mutant_dir)
        container.create_database("fresh")
        fresh_gate = container.run_gate_script(
            dbname="fresh", dump_path="/dump.sql", kill_signal_path="/kill.txt", restrict_key=RESTRICT_KEY, test_cmd="true"
        )
        _require_o4_gate_applied(fresh_gate, context="the fresh mutant run did not apply cleanly")
        fresh_nullable = container.query_one("fresh", scenario.query)
        fresh_dump = container.pg_dump("fresh", restrict_key=RESTRICT_KEY)

        # -- the RESIDUE run: the SAME mutant re-applied on top of a
        # database that already carries the UNMUTATED schema.
        container.replace_corpus(baseline_dir)
        container.create_database("residue")
        baseline_gate = container.run_gate_script(
            dbname="residue", dump_path="/dump.sql", kill_signal_path="/kill.txt", restrict_key=RESTRICT_KEY, test_cmd="true"
        )
        _require_o4_gate_applied(baseline_gate, context="seeding the residue database did not apply cleanly")
        baseline_dump = container.pg_dump("residue", restrict_key=RESTRICT_KEY)

        container.replace_corpus(mutant_dir)
        residue_gate = container.run_gate_script(
            dbname="residue", dump_path="/dump.sql", kill_signal_path="/kill.txt", restrict_key=RESTRICT_KEY, test_cmd="true"
        )
        _require_o4_gate_applied(residue_gate, context="re-applying the mutant to the residue database failed")
        residue_nullable = container.query_one("residue", scenario.query)
        residue_dump = container.pg_dump("residue", restrict_key=RESTRICT_KEY)

    report = {
        "scenario": scenario.name,
        "fresh_is_nullable": fresh_nullable,
        "residue_is_nullable": residue_nullable,
        "fresh_dump_differs_from_baseline": fresh_dump != baseline_dump,
        "residue_dump_equals_baseline": residue_dump == baseline_dump,
    }
    _require_o4_invariants(report)
    return report


def print_o3_receipt(report: Mapping[str, Any], *, stream: Any) -> None:
    print("--- P34/W9 O3 span-fidelity receipt ---", file=stream)
    print(f"dstdns_commit={report['dstdns_commit']}", file=stream)
    for entry in report["scenarios"]:
        print(
            f"  {entry['name']}: applied={entry['applied']} "
            f"matches_operator={entry['matches_operator']} "
            f"baseline={entry['baseline_observed']!r} mutant={entry['mutant_observed']!r}",
            file=stream,
        )
    print(f"invalid_control={report['invalid_control']}", file=stream)


def print_o4_receipt(report: Mapping[str, Any], *, stream: Any) -> None:
    print("--- P34/W9 O4 residue-probe receipt ---", file=stream)
    for key, value in report.items():
        print(f"{key}={value}", file=stream)


# --- the witnessed expected artifact (A-274): a REAL `assay run` --------


#: A single real dstdns file whose changed lines carry three real, distinct
#: sites (§9 M4's own `20-create-corpora.sql` count) -- chosen small enough
#: that a full `assay run` R2 execution (one baseline + one per mutant, each
#: a fresh throwaway database) finishes in a bounded number of container
#: round trips, while still being REAL dstdns DDL, never a constructed
#: fixture.
_WITNESS_FILE = "20-create-corpora.sql"
#: dstdns's own `20-create-corpora.sql` carries exactly 6 real
#: `sql:drop-not-null` sites (measured, this carving session: `name`,
#: `version`, `rule_sets`, `lifecycle`, `created_at`, `updated_at`) --
#: `max_mutants` is set to the exact real count so every one is attempted,
#: never truncated by a lower bound chosen for speed.
_WITNESS_MAX_MUTANTS = 6
_WITNESS_OPERATORS = ("sql:drop-not-null",)
#: Killed: dstdns's own README example (carve §1) -- `name` is caller-
#: supplied and its requiredness is exactly the kind of invariant an app
#: test asserts. Survived: `version`/`rule_sets` both carry a DEFAULT, so a
#: test suite asserting the column's own default rarely also asserts the
#: DATABASE enforces NOT NULL independently of it -- a real, plausible gap,
#: not a rigged one. Delivered to the container as a FILE (transferred via a
#: quoted heredoc, never interpolated into a `-e KEY=value` argument) so
#: none of its own `$$`/`'` bytes have to survive three nested layers of
#: shell quoting.
_WITNESS_TEST_ASSERTION_SQL = (
    "DO $$ BEGIN\n"
    "IF (SELECT is_nullable FROM information_schema.columns\n"
    "    WHERE table_name='corpora' AND column_name='name') <> 'NO' THEN\n"
    "  RAISE EXCEPTION 'corpora.name must be NOT NULL';\n"
    "END IF;\n"
    "END $$;\n"
)
_WITNESS_TEST_CMD = 'psql -v ON_ERROR_STOP=1 -U postgres -d "$SCHEMA_GATE_DBNAME" -f /witness-assert.sql'


def _assay_argv(python: str, *args: str) -> list[str]:
    bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); from assay.cli import main; sys.exit(main(sys.argv[2:]))"
    return [python, "-c", bootstrap, str(_SRC_ROOT), *args]


_WITNESS_LANE_TEMPLATE = """\
schema_version = 2

[lanes.dstdns_sql_qualification]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["sh", "tools/witness-gate.sh"]
env = {{ PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" }}
env_passthrough = []
budget = "20m"
allow_argv_append = false

[lanes.dstdns_sql_qualification.isolation]
snapshot_selection = "repository"

[lanes.dstdns_sql_qualification.judge]
language = "sql"
source_roots = ["infra/db-init/init-scripts"]
base = "@BASE_OID@"

[lanes.dstdns_sql_qualification.judge.mutation]
jobs = 1
max_mutants = {max_mutants}
operators = {operators!r}
equivalence_artifact = ".assay/schema-dump.sql"
kill_signal_artifact = ".assay/kill-signal.txt"
"""


def _witness_wrapper_script(*, container_name: str, dbname: str, restrict_key: str) -> str:
    """The HOST-side wrapper the disposable lane's own ``argv`` runs.

    assay's runner executes a lane's ``argv`` as a HOST subprocess inside
    the materialized snapshot -- never inside a container -- so this thin
    wrapper is what bridges that host-side invocation to the network-
    isolated (``--network none``) qualification container: it copies the
    CURRENT snapshot's own (possibly mutated) corpus in, runs the SAME
    self-contained ``schema-gate.sh`` (A-280) the direct O3/O4 probes use
    INSIDE the container via ``docker exec``/``docker cp``, then relays the
    two declared artifacts back onto the snapshot's own filesystem, where
    assay's own ``safeio`` reservation reads them. Never a substitute for
    the self-contained command -- it invokes that command unchanged. The
    test assertion is delivered as a FILE over a quoted heredoc (never
    interpolated into a ``-e`` argument), so its own ``$$``/``'`` bytes
    never have to survive nested shell quoting."""
    return (
        "set -u\n"
        "mkdir -p .assay\n"
        f"docker exec {container_name} sh -c 'rm -rf /corpus /corpus_new'\n"
        f"docker cp infra/db-init/init-scripts {container_name}:/corpus_new\n"
        f"docker exec {container_name} mv /corpus_new /corpus\n"
        f"docker exec -i {container_name} sh -c 'cat > /witness-assert.sql' <<'ASSAY_P34_W9_SQL'\n"
        f"{_WITNESS_TEST_ASSERTION_SQL}"
        "ASSAY_P34_W9_SQL\n"
        # DROP/CREATE DATABASE cannot run inside a transaction block, and
        # PostgreSQL implicitly wraps a MULTI-statement simple-query message
        # (everything a single `-c` argument sends) in one -- so these must
        # be two separate `-c` invocations, never one combined string
        # (mirrors :meth:`ThrowawayPostgres.create_database`).
        f"docker exec {container_name} psql -v ON_ERROR_STOP=1 -U postgres -c "
        f"'DROP DATABASE IF EXISTS {dbname};'\n"
        f"docker exec {container_name} psql -v ON_ERROR_STOP=1 -U postgres -c "
        f"'CREATE DATABASE {dbname};'\n"
        f"docker exec {container_name} rm -f /dump.sql /kill.txt\n"
        f"docker exec -e SCHEMA_GATE_INIT_SCRIPTS_DIR=/corpus -e SCHEMA_GATE_DBNAME={dbname} "
        f"-e SCHEMA_GATE_DUMP_PATH=/dump.sql -e SCHEMA_GATE_KILL_SIGNAL_PATH=/kill.txt "
        f"-e SCHEMA_GATE_RESTRICT_KEY={restrict_key} -e 'SCHEMA_GATE_TEST_CMD={_WITNESS_TEST_CMD}' "
        f"{container_name} sh /schema-gate.sh\n"
        "rc=$?\n"
        f"docker cp {container_name}:/dump.sql .assay/schema-dump.sql 2>/dev/null || true\n"
        f"docker cp {container_name}:/kill.txt .assay/kill-signal.txt 2>/dev/null || true\n"
        "exit $rc\n"
    )


def capture_witness(*, source_repo: Path, scratch: Path, python: str = sys.executable) -> dict[str, Any]:
    """Drive a REAL ``assay run`` over a real SQL R2 lane against dstdns's
    real ``20-create-corpora.sql`` (carve §1's own opening example), inside
    a disposable git repository, with a throwaway PostgreSQL 18.4 backing
    the lane's own self-contained gate command. Returns the produced
    verdict document, UNNORMALIZED (:func:`normalize_verdict` does that)."""
    verify_pinned_inputs(source_repo)
    scratch.mkdir(parents=True)
    corpus = export_corpus(source_repo)

    repo = scratch / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    identity = {"GIT_AUTHOR_NAME": "Assay P34 W9", "GIT_AUTHOR_EMAIL": "assay-p34-w9@example.invalid"}
    identity_env: dict[str, str] = {
        **identity,
        "GIT_COMMITTER_NAME": identity["GIT_AUTHOR_NAME"],
        "GIT_COMMITTER_EMAIL": identity["GIT_AUTHOR_EMAIL"],
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }

    init_dir = repo / INIT_SCRIPTS_PATH
    init_dir.mkdir(parents=True)
    for basename, content in corpus.items():
        if basename == _WITNESS_FILE:
            continue
        (init_dir / basename).write_bytes(content)
    tools_dir = repo / "tools"
    tools_dir.mkdir()
    # Both declared artifacts (`.assay/schema-dump.sql`, `.assay/kill-
    # signal.txt`) must be UNTRACKED, which by itself is not enough: an
    # untracked path with no matching `.gitignore` entry still shows up in
    # `git status --porcelain` and mutation._snapshot_left_dirt's
    # `git.dirty_paths` check treats that as the tree left dirty
    # (NO_MEASUREMENT/DIRTY_TREE) -- measured directly in this carving
    # session, and exactly why every existing lane fixture in `tests/`
    # commits a `.gitignore` naming its own artifact path.
    (repo / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    _run(["git", "add", "-A"], cwd=repo)
    _git_commit(repo, "base: dstdns init-scripts minus the qualification target", env=identity_env)
    base_oid = _git(repo, "rev-parse", "HEAD")

    (init_dir / _WITNESS_FILE).write_bytes(corpus[_WITNESS_FILE])
    with ThrowawayPostgres() as container:
        wrapper = _witness_wrapper_script(
            container_name=container.name,
            dbname="witness",
            restrict_key=RESTRICT_KEY,
        )
        (tools_dir / "witness-gate.sh").write_text(wrapper, encoding="utf-8")
        lane_toml = _WITNESS_LANE_TEMPLATE.format(max_mutants=_WITNESS_MAX_MUTANTS, operators=list(_WITNESS_OPERATORS)).replace(
            "@BASE_OID@", base_oid
        )
        (repo / "assay.toml").write_text(lane_toml, encoding="utf-8")
        _run(["git", "add", "-A"], cwd=repo)
        _git_commit(repo, "head: add the qualification target file + the witness lane", env=identity_env)
        head_oid = _git(repo, "rev-parse", "HEAD")

        artifact_path = scratch / "verdict.json"
        proc = _run(
            _assay_argv(
                python,
                "run",
                "dstdns_sql_qualification",
                "--file",
                str(repo / "assay.toml"),
                "--verdict-json",
                str(artifact_path),
            ),
            cwd=repo,
            check=False,
            timeout=600,
        )
        verdict = json.loads(artifact_path.read_text(encoding="utf-8"))

    _require_witness_commit_matches(verdict, head_oid)
    return {"verdict": verdict, "process_exit_code": proc.returncode, "base_oid": base_oid, "head_oid": head_oid}


def _require_witness_commit_matches(verdict: Mapping[str, Any], head_oid: str) -> None:
    if verdict.get("commit") != head_oid:
        raise QualificationError("the witness artifact's commit is not the disposable HEAD")


#: Runtime fields whose value is real but VARIES per invocation (a
#: timestamp, this run's disposable commit OIDs, the installed
#: ``assay_version``) -- each is independently validated against a value
#: this module knows out of band, THEN replaced with a placeholder token,
#: exactly `qualify_topos.py`'s own `normalize_artifact`/
#: `compare_complete_artifact` shape (chosen over `qualify_cmru_b006a.py`'s
#: field-by-field bucket assertions per the carve review's own instruction
#: -- see the module docstring).
_PLACEHOLDER_FIELDS = ("assay_version", "commit", "started", "ended")


def normalize_verdict(document: Mapping[str, Any], *, assay_version: str, head_oid: str, base_oid: str) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(document))
    if normalized.get("assay_version") != assay_version:
        raise QualificationError(
            f"artifact assay_version {normalized.get('assay_version')!r} != installed {assay_version!r}"
        )
    if normalized.get("commit") != head_oid:
        raise QualificationError("artifact commit is not the disposable HEAD")
    resolved_base = normalized.get("judgment", {}).get("resolved", {}).get("base")
    if resolved_base != base_oid:
        raise QualificationError(f"artifact judgment.resolved.base {resolved_base!r} != seeded base {base_oid!r}")
    for field in ("started", "ended"):
        if not isinstance(normalized.get(field), str) or not normalized[field]:
            raise QualificationError(f"artifact {field!r} is not a nonempty timestamp")
    normalized["assay_version"] = "@ASSAY_VERSION@"
    normalized["commit"] = "@HEAD_OID@"
    normalized["started"] = "@STARTED@"
    normalized["ended"] = "@ENDED@"
    normalized["judgment"]["resolved"]["base"] = "@BASE_OID@"
    return normalized


def compare_with_witness(actual: Mapping[str, Any], witness_path: Path, *, assay_version: str, head_oid: str, base_oid: str) -> None:
    normalized = normalize_verdict(actual, assay_version=assay_version, head_oid=head_oid, base_oid=base_oid)
    expected = json.loads(witness_path.read_text(encoding="utf-8"))
    if normalized != expected:
        raise QualificationError(
            f"the normalized verdict differs from the frozen witness at {witness_path}"
        )


# --- CLI ---------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualify_dstdns_sql.py")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None, help="write the O3 span-fidelity report here")
    parser.add_argument("--residue-probe", action="store_true", help="run O4 instead of O3")
    parser.add_argument("--witness", type=Path, default=None, help="capture a fresh witness verdict to this path instead of O3")
    args = parser.parse_args(argv)
    if args.scratch.exists():
        parser.error("--scratch must be absent")
    if args.residue_probe and args.witness is not None:
        parser.error("--residue-probe and --witness are mutually exclusive")

    if args.residue_probe:
        report = run_o4_residue_probe(source_repo=args.source_repo, scratch=args.scratch)
        print_o4_receipt(report, stream=sys.stderr)
    elif args.witness is not None:
        result = capture_witness(source_repo=args.source_repo, scratch=args.scratch)
        args.witness.write_text(json.dumps(result["verdict"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"witness written to {args.witness}", file=sys.stderr)
    else:
        report = run_o3_span_fidelity(source_repo=args.source_repo, scratch=args.scratch)
        print_o3_receipt(report, stream=sys.stderr)
        if args.json is not None:
            args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("ASSAY_P34_W9_QUALIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

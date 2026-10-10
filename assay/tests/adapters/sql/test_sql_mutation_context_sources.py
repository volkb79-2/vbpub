from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath

from conftest import GitRepo, prepared_snapshot

from assay import mutation, runner
from assay.adapters.sql import SqlAdapter


def test_sql_context_reads_unchanged_sources_from_prepared_manifest(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    attachment_source = (
        "ALTER TABLE s.t ADD CONSTRAINT uq UNIQUE USING INDEX idx;\n"
    )
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/02-attach.sql", attachment_source)
    head = git_repo.commit_all("add SQL index and attachment")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        assert set(prepared.paths_under(PurePosixPath("src"))) == {
            PurePosixPath("src/01-index.sql"),
            PurePosixPath("src/02-attach.sql"),
        }
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert jobs == ()


def test_sql_context_ignores_malformed_test_sources_but_keeps_ddl_candidates(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/tests/test_malformed.sql", "SELECT 'unterminated\n")
    head = git_repo.commit_all("add DDL and malformed SQL test fixture")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert len(jobs) == 1
    assert jobs[0].site.lineno == 1


def test_sql_context_invalidates_type_after_cross_file_column_drop_and_readd():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    table_source = "CREATE TABLE public.task (status text);\n"
    replacement_source = (
        "ALTER TABLE public.task DROP COLUMN status;\n"
        "ALTER TABLE public.task ADD COLUMN status public.task_status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, table_source, replacement_source)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_invalidates_column_types_after_cross_file_table_drop():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    drop_table = "DROP TABLE IF EXISTS public.task CASCADE;\n"
    recreate_without_declared_columns = (
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, drop_table, recreate_without_declared_columns)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_does_not_invalidate_a_qualified_other_schema_table():
    table_source = "CREATE TABLE public.task (status text);\n"
    unrelated_drop = "DROP TABLE archive.task;\n"
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context((table_source, unrelated_drop))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(sites) == 1


def test_sql_context_preserves_bounded_varchar_typmod_across_files():
    table_source = "CREATE TABLE public.task (status varchar(5));\n"
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'close'));\n"
    )
    context = SqlAdapter().prepare_mutation_context((table_source,))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_refuses_unqualified_type_when_a_temp_table_can_shadow_it():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = 'CREATE TEMP TABLE "task" (status varchar(5));\n'
    check_source = (
        'ALTER TABLE "task" ADD CHECK (status IN (\'open\', \'closed\'));\n'
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_refuses_type_facts_shadowed_by_select_into_temp():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = (
        "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
        "SELECT 'open'::public.task_status AS status INTO TEMP TABLE task;\n"
    )
    check_source = (
        "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_refuses_type_facts_shadowed_by_select_into_pg_temp():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = (
        "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
        "SELECT 'open'::public.task_status AS status INTO pg_temp.task;\n"
    )
    check_source = "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_refuses_permanent_type_for_pg_temp_qualified_reference():
    permanent_table = "CREATE TABLE task (status text);\n"
    unqualified_check = (
        "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    explicit_temp_check = (
        "ALTER TABLE pg_temp.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        unqualified_check,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    temp_schema_sites = SqlAdapter().generate_mutation_sites_with_context(
        explicit_temp_check,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )

    assert len(baseline_sites) == 1
    assert temp_schema_sites == ()


def test_sql_context_recognizes_tables_created_in_pg_temp_schema():
    permanent_table = "CREATE TABLE task (status text);\n"
    temporary_table = (
        "CREATE TYPE task_status AS ENUM ('open', 'closed');\n"
        "CREATE TABLE pg_temp.task AS SELECT 'open'::task_status AS status;\n"
    )
    check_source = "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_invalidates_type_facts_for_both_sides_of_table_rename():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    rename_and_recreate = (
        "ALTER TABLE public.task RENAME TO old_task;\n"
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((enum_source, original_table))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, rename_and_recreate)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_invalidates_type_facts_after_table_schema_move():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    move_and_recreate = (
        "CREATE SCHEMA archive;\n"
        "ALTER TABLE public.task SET SCHEMA archive;\n"
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((enum_source, original_table))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, move_and_recreate)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_invalidates_type_after_quoted_cross_file_alter_identifiers():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    cases = (
        ("status", ("status",), 'DROP COLUMN "status"'),
        (
            "status",
            ("status",),
            'ADD COLUMN IF NOT EXISTS "status" public.task_status',
        ),
        (
            "status_type",
            ("status_type",),
            'ALTER COLUMN "status_type" TYPE public.task_status',
        ),
        (
            "status_set",
            ("status_set",),
            'ALTER COLUMN "status_set" SET DATA TYPE public.task_status',
        ),
        (
            "status_before_rename",
            ("status_before_rename", "status_after_rename"),
            'RENAME COLUMN "status_before_rename" TO "status_after_rename"',
        ),
        (
            "status_after_rename",
            ("status_before_rename", "status_after_rename"),
            'RENAME COLUMN "status_before_rename" TO "status_after_rename"',
        ),
        (
            "status_star",
            ("status_star",),
            '* ALTER COLUMN "status_star" TYPE public.task_status',
        ),
    )

    for column, declared_columns, action in cases:
        # Each action gets a fresh text-only baseline. Its check has a real
        # widening site until that specific action's identifiers are parsed.
        table_source = (
            "CREATE TABLE public.task ("
            + ", ".join(f"{name} text" for name in declared_columns)
            + ");\n"
        )
        check_source = (
            "ALTER TABLE public.task ADD CHECK ("
            f"{column} IN ('open', 'closed'));\n"
        )
        baseline = SqlAdapter().prepare_mutation_context((enum_source, table_source))
        baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
            check_source,
            frozenset({1}),
            operators=("sql:widen-check-in",),
            limit=1000,
            context=baseline,
        )
        assert len(baseline_sites) == 1

        altered = SqlAdapter().prepare_mutation_context(
            (
                enum_source,
                table_source,
                f'ALTER TABLE public."task" {action};\n',
            )
        )
        altered_sites = SqlAdapter().generate_mutation_sites_with_context(
            check_source,
            frozenset({1}),
            operators=("sql:widen-check-in",),
            limit=1000,
            context=altered,
        )

        assert altered_sites == ()

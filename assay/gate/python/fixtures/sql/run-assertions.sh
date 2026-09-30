#!/bin/sh
# assay SQL qualification probes (A-480): one file per killed matrix row.
set -u
LC_ALL=C
export LC_ALL
: "${SCHEMA_GATE_DBNAME:?}" "${SCHEMA_GATE_ASSERT_DIR:?}"
failed=""
for probe in "$SCHEMA_GATE_ASSERT_DIR"/K*.sql; do
    [ -f "$probe" ] || { echo "no probes in $SCHEMA_GATE_ASSERT_DIR" >&2; exit 2; }
    id="$(basename "$probe" .sql)"
    psql -X -q -v ON_ERROR_STOP=1 -U postgres -d "$SCHEMA_GATE_DBNAME" -f "$probe"
    rc=$?
    case "$rc" in
        0) ;;
        3) failed="${failed:+$failed,}$id" ;;
        *) echo "psql exit $rc on $id: infrastructure, not a probe verdict" >&2; exit 2 ;;
    esac
done
[ -z "$failed" ] || { echo "ASSAY_SQL_FAILED=$failed"; exit 1; }

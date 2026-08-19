#!/bin/sh
# assay P34/W9 -- the SELF-CONTAINED dstdns schema qualification gate (A-280).
#
# dstdns's OWN scripts/schema-gate.sh (blob 88de912d, retained only as
# evidence of what the consumer has today -- W3-CARVE-P34-sql-adapter.md
# §6/W9, decisions.md A-280) is not runnable inside an assay snapshot: it
# writes no dump at all, exits 2 immediately because its first positional
# argument is mandatory, and drives docker against the DEPLOYED app network.
# This file is P34's own replacement -- the shape of a real consumer lane's
# `argv`, and the one this harness actually executes end to end.
#
# ORDERING IS THE WHOLE POINT (A-279/A-288, measured and frozen at
# nyxloom-trove/carve-assets/W3/): `apply && dump && test`, NEVER
# `apply && test && dump`. Get it backwards and a genuine kill (test exits
# non-zero) short-circuits the dump -- `safeio.arm()` has already unlinked
# any pre-existing artifact -- so the equivalence artifact is ABSENT, §3.6
# reads FAIL+absent as `crashed`, and the whole lane renders `ERROR` instead
# of the kill it should report. Here the dump runs unconditionally between
# `apply` and `test`, so it has already landed by the time `test`'s own exit
# status is known -- "regardless of the test outcome" is a property of the
# ORDER, not a `|| true`.
#
# THE RESTRICT-KEY TRAP IS SELF-ENFORCED (NB-6, review BLOCK-2). `pg_dump`
# 18's default `\restrict`/`\unrestrict` lines carry a random key on every
# invocation, so two dumps of the SAME unchanged database differ unless
# `--restrict-key` is pinned -- silently emptying the `equivalent` bucket
# forever (O5). Rather than trust the caller to have pinned one correctly,
# this script dumps TWICE with the caller-supplied key and refuses loudly if
# the two dumps are not byte-identical, naming `\restrict` in the message.
#
# REQUIRED ENVIRONMENT (fail-fast; no silent defaults):
#   SCHEMA_GATE_INIT_SCRIPTS_DIR  directory of numbered *.sql files to apply,
#                                 in `[0-9][0-9]*.sql` glob order, excluding
#                                 95-*/99-* (marker/seed -- dstdns's own
#                                 scripts/schema-apply.sh applies those
#                                 separately; this gate judges SCHEMA only).
#   SCHEMA_GATE_DBNAME            target database (already CREATEd)
#   SCHEMA_GATE_DUMP_PATH         where the equivalence-artifact dump lands
#   SCHEMA_GATE_KILL_SIGNAL_PATH  where a failing test's signal is written
#   SCHEMA_GATE_RESTRICT_KEY      the pinned `pg_dump --restrict-key` value
#   SCHEMA_GATE_TEST_CMD          the project's own schema-test command,
#                                 run via `sh -c`
set -eu

SCRIPT_DIR="${SCHEMA_GATE_INIT_SCRIPTS_DIR:?SCHEMA_GATE_INIT_SCRIPTS_DIR is required}"
DB_NAME="${SCHEMA_GATE_DBNAME:?SCHEMA_GATE_DBNAME is required}"
DUMP_PATH="${SCHEMA_GATE_DUMP_PATH:?SCHEMA_GATE_DUMP_PATH is required}"
KILL_SIGNAL_PATH="${SCHEMA_GATE_KILL_SIGNAL_PATH:?SCHEMA_GATE_KILL_SIGNAL_PATH is required}"
RESTRICT_KEY="${SCHEMA_GATE_RESTRICT_KEY:?SCHEMA_GATE_RESTRICT_KEY is required}"
TEST_CMD="${SCHEMA_GATE_TEST_CMD:?SCHEMA_GATE_TEST_CMD is required}"

[ -d "$SCRIPT_DIR" ] || { echo "[schema-gate] ERROR: init-scripts dir not found: ${SCRIPT_DIR}" >&2; exit 1; }

# --- 1. apply -----------------------------------------------------------
echo "[schema-gate] --- apply (${SCRIPT_DIR}) ---"
for sql_file in "$SCRIPT_DIR"/[0-9][0-9]*.sql; do
    case "$(basename "$sql_file")" in
        95-*|99-*) continue ;;   # marker/seed -- excluded, mirrors dstdns's own schema-apply.sh
    esac
    [ -f "$sql_file" ] || continue
    echo "[schema-gate] applying: $(basename "$sql_file")"
    psql -v ON_ERROR_STOP=1 -U postgres -d "$DB_NAME" -f "$sql_file"
done

# --- 2. dump, self-enforced reproducible (NB-6) --------------------------
echo "[schema-gate] --- dump (restrict-key pinned, verified reproducible) ---"
rm -f "$DUMP_PATH" "${DUMP_PATH}.verify"
pg_dump --schema-only --no-owner --restrict-key="$RESTRICT_KEY" -U postgres -d "$DB_NAME" > "$DUMP_PATH"
pg_dump --schema-only --no-owner --restrict-key="$RESTRICT_KEY" -U postgres -d "$DB_NAME" > "${DUMP_PATH}.verify"
if ! cmp -s "$DUMP_PATH" "${DUMP_PATH}.verify"; then
    printf '%s\n' "[schema-gate] REFUSED: two pg_dump invocations of the SAME unchanged" >&2
    printf '%s\n' "database produced different bytes -- a pinned --restrict-key should" >&2
    printf '%s\n' 'make this impossible; check for a stray \restrict/\unrestrict line' >&2
    printf '%s\n' "that still carries a random key." >&2
    rm -f "$DUMP_PATH" "${DUMP_PATH}.verify"
    exit 90
fi
rm -f "${DUMP_PATH}.verify"

# --- 3. test --------------------------------------------------------------
# The dump above has ALREADY landed, unconditionally -- this step's own exit
# status changes nothing about that (A-279's ordering).
echo "[schema-gate] --- test ---"
set +e
sh -c "$TEST_CMD"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
    echo "schema test command failed (exit ${rc}) against database ${DB_NAME}: ${TEST_CMD}" > "$KILL_SIGNAL_PATH"
fi
exit "$rc"

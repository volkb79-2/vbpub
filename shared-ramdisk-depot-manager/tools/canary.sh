#!/usr/bin/env bash
# canary.sh — prove the gate REJECTS known-bad code.
#
# A green suite says nothing until you have watched it go red for the right
# reason. Each canary below breaks exactly one contract an oracle claims to
# assert, and the named test must fail because of it. A canary that stays
# green is a hollow oracle, and this script says so.
#
# Run inside the gate container (tools/gate.sh builds it):
#   docker run --rm --cgroup-parent=... -v repo:repo -w <project> \
#       srdm-gate:unit bash tools/canary.sh
# or via:  tools/canary-run.sh

set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${CANARY_WORK:-/tmp/srdm-canary}"

pass=0
fail=0

# canary <name> <test-regex> <file> <sed-expression> <what-it-breaks>
canary() {
  local name="$1" test_re="$2" file="$3" expr="$4" breaks="$5"

  rm -rf "$WORK"
  mkdir -p "$WORK"
  cp -a "$SRC/." "$WORK/"

  if ! sed -i "$expr" "$WORK/$file"; then
    printf 'CANARY %-22s ERROR   could not apply the mutation\n' "$name"
    fail=$((fail + 1))
    return
  fi
  if diff -q "$SRC/$file" "$WORK/$file" >/dev/null 2>&1; then
    # The mutation matched nothing — the code moved and this canary is
    # silently testing the unmodified tree. That is exactly the rot this
    # script exists to catch, so it is a failure, not a skip.
    printf 'CANARY %-22s ERROR   the mutation matched nothing in %s\n' "$name" "$file"
    fail=$((fail + 1))
    return
  fi

  local out
  out="$(cd "$WORK" && go test ./... -count=1 -run "$test_re" 2>&1)"
  local code=$?

  if [ "$code" -ne 0 ]; then
    printf 'CANARY %-22s REJECTED (%s)\n' "$name" "$breaks"
    pass=$((pass + 1))
  else
    printf 'CANARY %-22s SURVIVED — the suite did not notice: %s\n' "$name" "$breaks"
    printf '%s\n' "$out" | tail -5
    fail=$((fail + 1))
  fi
}

# --- O1: kill at every phase ---------------------------------------------
# Recovery must adopt a transaction that reached COMPLETE. Point the loader
# at a path that cannot exist and every transaction looks incomplete, so a
# finished promotion is thrown away instead of recovered.
canary "O1-never-adopts" "TestKillAtEveryPhase" \
  "internal/store/recover.go" \
  's#rel, loadErr := LoadReleaseDir(txID, dir)#rel, loadErr := LoadReleaseDir(txID, dir+"/nonexistent")#' \
  "recovery discards a COMPLETE transaction"

# COMPLETE must be the LAST thing written. Write a VALID one a phase early
# and a transaction that was still in flight becomes adoptable.
#
# It has to be a valid document: an empty one is rejected by the schema
# check, so the suite would go red for the wrong reason and this canary
# would prove only that the parser works.
canary "O1-complete-not-last" "TestKillAtEveryPhase" \
  "internal/store/store.go" \
  's#^\t\treturn syncTree(tx.Root)$#\t\tif b, e := json.MarshalIndent(complete, "", "  "); e == nil { _ = fsx.WriteFileSync(filepath.Join(tx.Dir, CompleteFile), append(b, 10), 0o644) }; return syncTree(tx.Root)#' \
  "a valid COMPLETE appears one phase before it should"

# --- O2: COMPLETE means it -----------------------------------------------
# Stop comparing content hashes and verification becomes decoration.
canary "O2-no-hash-compare" "TestVerifyRejects|TestKillAtEveryPhase" \
  "internal/store/manifest.go" \
  's#^\t\t\tif sum != e.SHA256 {$#\t\t\tif false {#' \
  "verification ignores changed content"

# Stop noticing paths that are not in the manifest and content can be
# smuggled into a release that still verifies.
canary "O2-allows-extra-files" "TestVerifyRejects" \
  "internal/store/manifest.go" \
  's#^\t\t\treturn fmt.Errorf("store: %q is present but not in the manifest", rel)$#\t\t\treturn nil#' \
  "verification ignores unmanifested files"

# --- O3: an unclassified path blocks promotion ----------------------------
# The classic wrong fix: land it in a default class instead of refusing.
canary "O3-default-class" "TestUnclassified|TestClassify" \
  "internal/profile/classify.go" \
  's#^\treturn nil, \&UnclassifiedError{Path: cleaned, Profile: p.ID}$#\treturn StructureClass, nil#' \
  "an unclassified path silently gets a default class"

# --- O4: the manifest is content-addressed --------------------------------
# Key the digest on size rather than on the per-file hash.
canary "O4-digest-on-size" "TestManifest" \
  "internal/store/manifest.go" \
  's#^\t\tpayload := e.SHA256$#\t\tpayload := fmt.Sprint(e.Size)#' \
  "the content digest is keyed on size, not content"

# --- O5: the journal has no secrets ---------------------------------------
# Never register anything, so the scrubber has nothing to remove.
canary "O5-scrubber-disabled" "TestJournalNever|TestRegisteredSecrets" \
  "internal/journal/redact.go" \
  's#^\t\tif !contains(s.secrets, v) {$#\t\tif false {#' \
  "credentials are never registered for scrubbing"

# Drop the credential-shaped key denylist.
canary "O5-no-key-denylist" "TestCredentialShaped|TestDeniedKey" \
  "internal/journal/redact.go" \
  's#^\tif deniedKeyExact\[lower\] {$#\tif false {#' \
  "credential-shaped field names are no longer dropped"

rm -rf "$WORK"

printf '\n%d canary/canaries rejected, %d survived\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

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

# --- P03: publication topology --------------------------------------------
# Stop sealing the populated tree, so the bind source stays writable.
# The seal moved into the hold worker with P04: it is the worker that writes
# a class tree, and nothing else ever does.
canary "P03-no-seal" "TestWorkerPopulatesVerifiesAndSeals" \
  "internal/hold/worker.go" \
  's#info.Mode().Perm()&\^0o222#info.Mode().Perm()|0o222#' \
  "the populated tree is never made read-only"

# Drop the read-only half of the exposure remount. A bind inherits its
# source's flags, so this leaves published content WRITABLE.
canary "P03-exposure-writable" "TestExposureIsMadeReadOnly" \
  "internal/publish/publish.go" \
  's#syscall.MS_BIND|syscall.MS_REMOUNT|syscall.MS_RDONLY#syscall.MS_BIND|syscall.MS_REMOUNT#' \
  "the published exposure is left writable"

# Unmount the op tmpfs before the exposure. A bind that survives its tmpfs
# frees nothing, so this is a silent memory leak dressed as a teardown.
canary "P03-teardown-order" "TestTeardownReleasesInTheOrder|TestPublishTearsDown" \
  "internal/publish/teardown.go" \
  's#if err := p.unmountIfMounted(c.ExposePath); err != nil {#if err := p.unmountIfMounted(c.OpMount); err != nil {#' \
  "teardown drops the op tmpfs before the exposure"

# Publish every class, including the excluded one. WS/Saved is per-instance
# state and the absolute state rule says it is never shared.
canary "P03-publishes-excluded" "TestExcludedClasses" \
  "internal/publish/publish.go" \
  's#^\t\tif c.Kind == profile.KindManaged {$#\t\tif true {#' \
  "per-instance state is published as shared content"

# Claim every mount, so nothing is ever an orphan. An unrecorded mount then
# holds memory forever with nothing to attribute it to.
canary "P03-no-orphans" "TestReconcileFindsOrphan" \
  "internal/publish/teardown.go" \
  's#^\t\t\tif claimed\[e.MountPoint\] || e.MountPoint == p.cfg.RunDir {$#\t\t\tif true {#' \
  "reconciliation never reports an orphan mount"

# --- P04: hold units, class policy and charging ---------------------------

# Name the generation aggregate as the master plan sketched it. systemd reads
# "-" as the slice hierarchy separator, so this interposes an auto-created
# `srdm-gen.slice` that nobody owns and that carries memory.min=0 — and a
# cgroup v2 floor is capped by every ancestor's, so every class floor beneath
# it becomes arithmetically dead. Measured; D-015.
canary "P04-gen-slice-nests-deeper" "TestGenerationSliceInterposesNothing|TestTheGenerationSliceIsGiven" \
  "internal/hold/hold.go" \
  's#\tname := stem + "-" + gen + ".slice"#\tname := stem + "-gen-" + gen + ".slice"#' \
  "the generation slice acquires an unprotected ancestor"

# Protect the aggregate with the LARGEST class floor rather than their sum.
# The plausible wrong answer: cgroup v2 prorates a parent's protection among
# its children, so this silently shrinks every floor inside it.
canary "P04-floor-is-max-not-sum" "TestTheGenerationSliceIsGiven|TestPublishPerformsTheExactSequence" \
  "internal/publish/publish.go" \
  's#^\t\t\ttotal += c.MemoryMin$#\t\t\ttotal = c.MemoryMin#' \
  "the generation aggregate protects less than the floors beneath it"

# Stop rendering the class floor, so a hold unit carries no MemoryMin at all
# and the class it holds is reclaimed like anything else.
canary "P04-class-floor-dropped" "TestPropertiesCarryTheShape" \
  "internal/hold/hold.go" \
  's#^\tif p.MemoryMin > 0 {$#\tif false {#' \
  "the class floor never reaches the unit"

# Treat a declared MemoryZSwapMax=0 as unset. Zero is a meaningful setting —
# pak content is incompressible, so zswap burns CPU without shrinking — and
# dropping it is indistinguishable from a class that never asked.
canary "P04-zswap-zero-unset" "TestPropertiesCarryTheShape" \
  "internal/hold/hold.go" \
  's#^\tif p.ZSwapMax != nil {$#\tif false {#' \
  "a class that bypasses zswap silently gets the default instead"

# Unmount everything and leave the hold units running. The cgroups, and
# anything still charged to them, survive a teardown that reports success.
canary "P04-teardown-skips-units" "TestTeardownReleasesInTheOrder|TestPublishTearsDown" \
  "internal/publish/teardown.go" \
  's#^\t\tif c.HoldUnit == "" {$#\t\tif true {#' \
  "teardown leaves the hold units and their cgroups behind"

# Leave the generation slice active. Measured: a slice stays active and keeps
# its cgroup after its last service exits, so stopping the services is not
# enough on its own.
canary "P04-slice-not-released" "TestTeardownReleasesInTheOrder" \
  "internal/publish/teardown.go" \
  's#^\tif rec.Slice != "" {$#\tif false {#' \
  "the generation aggregate outlives the generation"

# Report a class that cannot fit as a fault. It would then read as a bug in
# srdm rather than as sizing that has to be fixed, and the generation would
# not be quarantined — the 2026-07-29 corruption shape.
canary "P04-enospc-is-a-fault" "TestAWorkerOutOfSpaceIsARefusal" \
  "internal/publish/publish.go" \
  's#if err != nil && hold.ExitStatus(err) == hold.ExitNoSpace {#if err != nil \&\& false {#' \
  "running out of space is no longer a refusal"

# And the other direction: call every worker failure a refusal. Content that
# does not match its manifest would then quarantine quietly, and a genuine
# sizing failure would have nothing to stand out from.
canary "P04-every-failure-is-a-refusal" "TestAWorkerFailingForAnyOtherReason" \
  "internal/publish/publish.go" \
  's#hold.ExitStatus(err) == hold.ExitNoSpace#true#' \
  "every worker failure is reported as a refusal"

# Stop checking whether a class is still held. Content mounted with its hold
# unit gone is charged to a removed cgroup that carries no policy — which the
# mount table cannot show, and which this is the only check for.
canary "P04-unheld-invisible" "TestReconcileFlagsAClassWhoseHoldUnitIsGone" \
  "internal/publish/teardown.go" \
  's#^\t\t\tif !active {$#\t\t\tif active \&\& !active {#' \
  "reconciliation cannot see a class nothing is holding"

# Skip the worker's verification, so a class that does not match its manifest
# is populated, sealed and bound anyway.
canary "P04-worker-skips-verify" "TestWorkerRefusesContentThatDoesNotMatch" \
  "internal/hold/worker.go" \
  's#if err := rel.Manifest.VerifyClass(wa.Target, wa.Class); err != nil {#if err := error(nil); err != nil {#' \
  "a class is published without being verified against the manifest"

# Believe ExecMainStatus even when the unit did not fail by exiting. For a
# healthy running unit that is 0 — so a start systemd refused would be
# reported as a worker that succeeded.
canary "P04-stale-exit-status" "TestStartIgnoresTheStatusOfAUnitThatDidNotFail" \
  "internal/hold/hold.go" \
  's#props\["ActiveState"\] != "failed" || props\["Result"\] != "exit-code"#props\["ActiveState"\] == "never"#' \
  "a refused start is read as a worker exit status"

rm -rf "$WORK"

printf '\n%d canary/canaries rejected, %d survived\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

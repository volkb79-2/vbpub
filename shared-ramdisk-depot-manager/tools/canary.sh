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
  's#^\t\t\tif claimed\[e.MountPoint\] || infrastructure\[e.MountPoint\] {$#\t\t\tif true {#' \
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

# --- P05: consumer resolution and teardown safety -------------------------

# Tear down without asking who is holding. Every step afterwards SUCCEEDS
# with a consumer running and frees nothing, so there is no failure left to
# notice — this is the leak with no symptom.
canary "P05-teardown-skips-the-check" "TestTeardownIsRefusedWhileAConsumerHolds" \
  "internal/publish/teardown.go" \
  's#if err := p.refuseIfHeld(ctx, opID, KindTeardown, rec); err != nil {#if err := error(nil); err != nil {#' \
  "teardown never asks whether a consumer is holding the generation"

# Ask, and then ignore the answer.
canary "P05-holds-are-never-held" "TestTeardownIsRefusedWhileAConsumerHolds" \
  "internal/consumer/consumer.go" \
  's#^func (r \*Report) Held() bool { return len(r.Holders) > 0 }$#func (r *Report) Held() bool { return false }#' \
  "a report full of holders says nothing is holding"

# Count srdm's own namespace as a consumer. This does not leak — it does the
# opposite, and refuses every teardown forever, because srdm is always
# holding its own content.
canary "P05-own-namespace-counts" "TestOurOwnMountsAreNotHolders" \
  "internal/consumer/consumer.go" \
  's#^\t\tif ns == selfNS || seen\[ns\] {$#\t\tif seen[ns] {#' \
  "srdm reports itself as a consumer of its own generation"

# Stop matching the superblock, so no mount in any other namespace is ever a
# hold. A consumer's bind is at a path srdm has never seen, so the device is
# the only thing that connects the two.
canary "P05-superblock-ignored" "TestAMountOfOurSuperblockInAnotherNamespaceIsAHolder" \
  "internal/consumer/consumer.go" \
  's#^\t\t\tif !devices\[d\] {$#\t\t\tif true {#' \
  "no mount in another namespace is ever recognised"

# And the other direction: match every device, so unrelated tmpfs mounts pin
# generations that have nothing to do with them.
canary "P05-every-device-matches" "TestAMountOfADifferentSuperblockIsNotAHolder" \
  "internal/consumer/consumer.go" \
  's#^\t\t\tif !devices\[d\] {$#\t\t\tif false {#' \
  "an unrelated superblock pins the generation"

# Swallow an unreachable Docker. "Nobody is holding this" and "I could not
# ask" then read identically, and only one of them is safe to act on.
canary "P05-docker-failure-silent" "TestAnUnreachableDockerIsReportedAsDegraded" \
  "internal/consumer/consumer.go" \
  's#\t\trep.Degraded = append(rep.Degraded, "docker: "+err.Error())#\t\t_ = err#' \
  "an unreachable Docker is not reported as a degraded check"

# Publish into the shared parent, so every mount is delivered to every
# namespace that is a slave of the root. Those copies are holds, so teardown
# would then be refused by services that want nothing from srdm.
canary "P05-no-private-root" "TestPublishPerformsTheExactSequence" \
  "internal/publish/publish.go" \
  's#^\tif e, ok := mountinfo.At(entries, root); ok \&\& e.Propagation() == mountinfo.PropagationPrivate {$#\tif true {#' \
  "publication no longer isolates its own mount root"

# Treat srdm's operation root as an orphan and tear it down, removing the
# isolation from under every live generation.
canary "P05-op-root-is-an-orphan" "TestTheOperationRootIsNotAnOrphan" \
  "internal/publish/teardown.go" \
  's#infrastructure := map\[string\]bool{p.cfg.RunDir: true, p.cfg.OpRoot(): true}#infrastructure := map[string]bool{p.cfg.RunDir: true}#' \
  "the operation root is torn down as an orphan mount"

# --- P06: the host-bind exposure driver -----------------------------------

# Expose without checking that the host side can propagate at all. Every
# mount is then invisible to Wings and every unmount leaves a ghost.
canary "P06-host-propagation" "TestExposeRefusesWhenTheHostSideIsNotShared" \
  "internal/expose/hostbind.go" \
  's#^\tif !prop.HostShared() {$#\tif false {#' \
  "the host peer group is no longer checked"

# Accept Docker's default rprivate — the 2026-07-31 outage, unrefused.
canary "P06-rprivate-accepted" "TestExposeRefusesAnRPrivateWingsContainer" \
  "internal/expose/hostbind.go" \
  's#^\tcase !prop.ContainerReceives():$#\tcase false:#' \
  "an rprivate Wings container is accepted"

# Treat "I could not ask Docker" as "it is fine". Not knowing and being
# wrong call for different things from an operator.
canary "P06-degraded-is-fine" "TestExposeRefusesWhenTheContainerCannotBeInspected" \
  "internal/expose/hostbind.go" \
  's#^\tcase prop.Degraded != "":$#\tcase false:#' \
  "an uninspectable container is assumed to be correct"

# Expose read-only onto a node that will chown-walk. The walk fails EROFS on
# a read-only mount even when the owner already matches, so the server never
# starts — and the operator meets it as an unexplained failure.
canary "P06-chown-walk-ignored" "TestExposeRefusesReadOnlyWhenTheChownWalkWouldRun" \
  "internal/expose/hostbind.go" \
  's#\tif access == AccessRO \&\& !h.cfg.ChownSkipPatch {#\tif false {#' \
  "a read-only exposure is made onto a node that will chown-walk it"

# Read the node config's ambiguity as permission. Every way of not knowing
# has to land on "the walk runs", or the guess produces the unstartable
# server the check exists to prevent.
canary "P06-chownwalk-fails-open" "TestEverythingAmbiguousFailsClosed" \
  "internal/wings/wings.go" \
  's#return ChownWalk{Enabled: true, Source: path}, fmt.Errorf(#return ChownWalk{Enabled: false, Known: true, Source: path}, fmt.Errorf(#' \
  "an unreadable node config is read as the chown walk being off"

# Let a second consumer onto an rw generation. With two, a write is the
# 2026-07-29 corruption by construction.
canary "P06-rw-multi-consumer" "TestExposeRWIsRefusedWhenAnotherConsumerHolds" \
  "internal/expose/hostbind.go" \
  's#^\tif access == AccessRW \&\& rep.Held() {$#\tif false {#' \
  "a second consumer joins a writable generation"

# Share a generation somebody has already written through, so the newcomer
# reads content nobody verified.
canary "P06-dirty-shared" "TestASharedGenerationIsRefusedOnceItIsDirty" \
  "internal/expose/hostbind.go" \
  's#^\tif rec.DirtyCapable \&\& rep.Held() {$#\tif false {#' \
  "a written-through generation is shared with a second consumer"

# Never record that a generation was exposed writable, so nothing downstream
# knows its content no longer matches the release.
canary "P06-never-marks-dirty" "TestExposeRWMarksTheGenerationBeforeMountingIt" \
  "internal/expose/hostbind.go" \
  's#if err := h.marker.MarkDirtyCapable(req.OperationID, rec.Profile, rec.Generation); err != nil {#if err := error(nil); err != nil {#' \
  "an rw exposure leaves the generation unmarked"

# Bind a class path that covers per-instance state. The mount HIDES what is
# beneath it, so the saves are not overwritten — they become invisible, and
# the damage is found only when somebody goes looking for a world that is
# still on disk underneath.
canary "P06-shadows-state" "TestPlanRefusesAClassPathThatShadowsExcludedState" \
  "internal/expose/expose.go" \
  's#^\t\tif target == ex || strings.HasPrefix(ex, target+"/") || strings.HasPrefix(target, ex+"/") {$#\t\tif target == ex \&\& false {#' \
  "a class path is allowed to shadow per-instance state"

# Drop the read-only half of the exposure remount. A bind inherits its
# source's flags, so this leaves a game server able to write into shared
# content.
canary "P06-exposure-writable" "TestExposeReadOnlyMakesEachBindReadOnly" \
  "internal/expose/hostbind.go" \
  's#syscall.MS_BIND|syscall.MS_REMOUNT|syscall.MS_RDONLY#syscall.MS_BIND|syscall.MS_REMOUNT#' \
  "a read-only exposure is left writable"

# Bind the published read-only path for an rw exposure. A bind inherits its
# source's flags, so the result is read-only whatever was asked for —
# measured, and the reason the two modes bind different mount points.
canary "P06-rw-binds-the-ro-side" "TestExposeReadWriteLeavesTheBindsWritable|TestReadOnlyBindsThePublishedExposure" \
  "internal/expose/expose.go" \
  's#^\t\treturn cr.ContentRoot()$#\t\treturn cr.ExposePath#' \
  "an rw exposure binds the read-only side and is silently read-only"

# --- P07: harvest, and the rw ownership model -----------------------------

# Expose rw and stop at the MOUNT. Publication seals a class tree a-w, so the
# result permits writes that the modes still refuse to every uid but root —
# and the one writer rw exists for, the game's own updater, is not root. The
# update reports success and changes nothing.
canary "P07-rw-never-unsealed" "TestExposeRWUnsealsEachClassTree" \
  "internal/expose/hostbind.go" \
  's#\t\tif err := hold.Unseal(root, h.chown, owner.UID, owner.GID); err != nil {#\t\tif err := error(nil); err != nil {#' \
  "an rw exposure leaves the content sealed against the only writer it has"

# Hand the unsealed tree to nobody. srdm does not guess the uid: a tree
# unsealed to root is one an unprivileged updater still cannot create a file
# in, and that failure arrives as an update that did nothing.
canary "P07-rw-owner-optional" "TestExposeRWIsRefusedWhenNoWriteOwnerIsDeclared" \
  "internal/expose/hostbind.go" \
  's#^\tif access == AccessRW \&\& h.cfg.WriteOwner == nil {$#\tif false {#' \
  "rw is exposed with no owner to hand the tree to"

# Unseal to the world. Exactly one consumer may write to a generation, so
# group and other write hand it to processes the single-consumer rule just
# refused.
canary "P07-unseal-world-writable" "TestUnsealRestoresOwnerWrite|TestExposeRWUnsealsEachClassTree" \
  "internal/hold/worker.go" \
  's#info.Mode().Perm()|0o200|keep#info.Mode().Perm()|0o222|keep#' \
  "unsealing gives group and other write"

# Seal with Perm() alone. It strips setuid, setgid and sticky from every
# entry — invisibly, because publication never compares modes again (D-014).
# It surfaces one package later, as a harvest that cannot match the release
# it came from.
canary "P07-seal-drops-setuid" "TestSealRemovesWriteAndKeeps" \
  "internal/hold/worker.go" \
  's#^\t\thigh := info.Mode() \& (fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky)$#\t\thigh := fs.FileMode(0)#' \
  "sealing strips setuid, setgid and sticky from published content"

# The same blindness in the copy that builds a transaction.
canary "P07-copy-drops-setuid" "TestCopyTreeCarriesSetuid" \
  "internal/fsx/copy.go" \
  's#^\treturn m.Perm() | (m \& (fs.ModeSetuid | fs.ModeSetgid | fs.ModeSticky))$#\treturn m.Perm()#' \
  "a staged or harvested tree loses setuid, setgid and sticky"

# Copy a directory with its sealed mode, so the next entry written into it
# fails. Every generation harvest reads is sealed, and the gate — like any
# operator who is not root — cannot write into 0555.
canary "P07-copy-seals-the-destination" "TestCopyTreeCanCopyASealedTree|TestAHarvestedReleaseCarriesTheStoresModes" \
  "internal/fsx/copy.go" \
  's#^\t\treturn os.Chmod(dst, fullMode(info.Mode())|0o700)$#\t\treturn os.Chmod(dst, fullMode(info.Mode()))#' \
  "a sealed tree cannot be copied into a transaction"

# Harvest without asking whether anything can still write. The release that
# comes back is then a state the tree may never have been in — and it hashes,
# verifies and publishes perfectly, because a torn copy is self-consistent.
canary "P07-harvest-skips-quiesce" "TestHarvestIsRefusedWhileAConsumerCanStillWrite" \
  "internal/harvest/harvest.go" \
  's#\t\treturn h.refuseIfHeld(ctx, rec, "before the copy", fields)#\t\treturn nil#' \
  "harvest never asks whether a consumer can still write"

# Ask once, before the copy, and never again. A container that starts while
# harvest is reading is then invisible — which is the only case the second
# ask exists for.
canary "P07-harvest-no-settle" "TestHarvestIsRefusedWhenAConsumerAppearsDuringTheCopy" \
  "internal/harvest/harvest.go" \
  's#\t\treturn h.refuseIfHeld(ctx, rec, "during the copy", fields)#\t\treturn nil#' \
  "a consumer that starts mid-harvest is never noticed"

# Harvest a generation that is no longer mounted. A teardown leaves the mount
# POINT behind as an ordinary empty directory, so this promotes an empty
# release over a good one — and it verifies clean, because an empty tree
# hashes consistently.
canary "P07-harvest-unmounted" "TestHarvestIsRefusedWhenAClassIsNotMounted" \
  "internal/harvest/harvest.go" \
  's#^\t\t\treturn \&NotPublishedError{Generation: rec.Generation, Class: c.Name, Mount: c.OpMount}$#\t\t\treturn nil#' \
  "a torn-down generation is harvested as an empty release"

# And the same failure one step on: mounted, but with nothing in it.
canary "P07-harvest-empty-tree" "TestHarvestRefusesAGenerationWhoseTreesAreEmpty" \
  "internal/harvest/harvest.go" \
  's#^\tif len(from) == 0 {$#\tif false {#' \
  "an empty generation is promoted over a good release"

# Accept a path whose class no longer names the tmpfs it came off. The
# profile changed under a live generation, and republishing afterwards moves
# the content to a different class, tmpfs and memory floor with no signal.
canary "P07-harvest-class-moved" "TestHarvestRefusesWhenTheProfileMovedAPathToAnotherClass" \
  "internal/harvest/harvest.go" \
  's#^\t\t\tif class.Kind != profile.KindStructure \&\& class.Name != c.Name {$#\t\t\tif false {#' \
  "content is silently reclassified into another memory class"

# Let the second class tree win a path both claim, instead of refusing. The
# release then depends on the order the classes happened to be walked in.
canary "P07-harvest-collision-wins" "TestHarvestRefusesAPathTwoClassTreesBothClaim" \
  "internal/harvest/harvest.go" \
  's#^\t\t\t\treturn \&CollisionError{Path: rel, First: prev, Second: c.Name}$#\t\t\t\treturn nil#' \
  "two class trees claiming one path picks a winner silently"

# Record a harvest as a stage. Provenance is the only thing that says this
# release came out of a running tmpfs rather than a verified acquisition.
canary "P07-harvest-provenance" "TestHarvestPromotesTheLiveTreeWithHarvestedProvenance" \
  "internal/harvest/harvest.go" \
  's#^\t\t\tKind: store.ProvenanceHarvested,$#\t\t\tKind: store.ProvenanceStaged,#' \
  "a harvested release claims it was staged"

# Leave the partial copy behind when a harvest refuses. Unlike a failed
# stage, it is not evidence — the tmpfs it came from is still mounted and
# still readable — it is just gigabytes nobody will explain.
canary "P07-harvest-tx-survives" "TestHarvestRefusesWhenTheProfileMovedAPathToAnotherClass|TestHarvestIsRefusedWhenAConsumerAppearsDuringTheCopy" \
  "internal/harvest/harvest.go" \
  's#^\t\t_ = h.st.Discard(tx)$#\t\t_ = tx#' \
  "a refused harvest leaves its partial copy on disk"

# Never re-hash a generation that has been exposed writable, so drift
# through an rw exposure is invisible until somebody trips over it.
canary "P06-no-drift-check" "TestADriftedGenerationFailsAndSaysWhere" \
  "internal/doctor/wings.go" \
  's#^\t\t\tif err := rel.Manifest.VerifyClass(class.ExposePath, class.Name); err != nil {$#\t\t\tif err := error(nil); err != nil {#' \
  "a drifted generation is reported as clean"

# --- P08: the operator surface --------------------------------------------

# Swap what a running server is reading underneath it. This is oracle 24 for
# activate and rollback: the content the process has open is replaced by
# content it did not open, which is not a restart away from working.
canary "P08-activate-ignores-holders" "TestActivateIsRefusedWhileAConsumerHolds" \
  "internal/opctl/opctl.go" \
  's#\t\treturn c.refuseIfHeld(ctx, kind, prof.ID, a.Release)#\t\treturn nil#' \
  "activate never asks whether a consumer is reading the live generation"

# Never write the assignment down. Every operation then appears to work and
# the next boot restores whatever the last durable record happened to say —
# which is the state before all of it.
canary "P08-assignment-never-recorded" "TestActivatePublishesExposesRecordsThenTearsDown|TestAttachExposesBeforeItRecords|TestDetachUnexposesBeforeItRecords" \
  "internal/opctl/opctl.go" \
  's#^\t\treturn c.asg.Save(a)$#\t\treturn nil#' \
  "declared intent is never made durable"

# Let a re-activation of the live release shift the rollback target.
# Re-activating is how a degraded generation is repaired, so this destroys
# the way back at exactly the moment somebody is already dealing with
# something being wrong.
canary "P08-rollback-memory-lost" "TestReActivatingTheLiveReleaseDoesNotDestroy" \
  "internal/assign/assign.go" \
  's#^\tif a.Release == releaseID {$#\tif false {#' \
  "repairing the present destroys the way back"

# Bind the new generation over the old one instead of replacing it. The
# mounts stack: the server reads the new content, and the old generation's
# pages are held by a mount nothing will ever unmount.
canary "P08-mounts-stack-on-swap" "TestActivatePublishesExposesRecordsThenTearsDown" \
  "internal/opctl/opctl.go" \
  's#\t\t\tif err := c.drv.Unexpose(ctx, oldRec, prof, req); err != nil {#\t\t\tif err := error(nil); err != nil {#' \
  "the replaced generation is never unexposed, so mounts stack"

# Take a shared lock instead of an exclusive one. Two operations then run at
# once, each succeeding at steps that contradict the other's.
canary "P08-lock-is-not-exclusive" "TestASecondOperationIsRefusedWhileOneHoldsTheLock" \
  "internal/opctl/lock.go" \
  's#syscall.LOCK_EX|syscall.LOCK_NB#syscall.LOCK_SH|syscall.LOCK_NB#' \
  "two operations can run at once"

# Collect a release a live generation was built from. The pages exist
# whatever the assignment says, and the republish after the next reboot finds
# nothing to republish from.
canary "P08-gc-collects-the-published" "TestGCKeepsAPublishedReleaseNobodyAssigned" \
  "internal/opctl/gc.go" \
  's#\t\tkeepIfUnpinned(res.Kept, rec.ReleaseID, KeptPublished)#\t\t_ = rec#' \
  "gc collects a release that has a live generation"

# Collect a channel target, turning a working reference into a dangling one.
canary "P08-gc-collects-channel-targets" "TestGCKeepsAChannelTarget" \
  "internal/opctl/gc.go" \
  's#\t\tkeepIfUnpinned(res.Kept, id, KeptChannel)#\t\t_ = id#' \
  "gc collects what a channel points at"

# Ignore retention entirely, so everything unpinned goes on the first run.
canary "P08-gc-ignores-retention" "TestGCKeepsTheConfiguredNumberOfUnpinnedReleases" \
  "internal/opctl/gc.go" \
  's#^\t\tif i < c.cfg.Retention {$#\t\tif false {#' \
  "gc keeps nothing beyond what is pinned"

# Make --dry-run remove things. A collector whose decisions cannot be
# inspected before it acts is one nobody will run on a node that matters —
# and one that lies about it is worse than none.
canary "P08-gc-dry-run-deletes" "TestGCDryRunReportsAndRemovesNothing" \
  "internal/opctl/gc.go" \
  's#^\tif dryRun || len(res.Removed) == 0 {$#\tif len(res.Removed) == 0 {#' \
  "a dry run deletes releases"

rm -rf "$WORK"

printf '\n%d canary/canaries rejected, %d survived\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

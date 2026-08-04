package opctl

import (
	"strings"
	"testing"

	"srdm/internal/publish"
)

// The sizing arithmetic and the /proc/meminfo read are tested once, in
// internal/publish (TestGenerationBytesSumsOnlyManagedClasses,
// TestAvailableHostBytesParsesMemAvailable and neighbors) — this package
// only wires them together in checkHeadroom, and that wiring is exercised
// end to end by Update itself:
// TestUpdateRefusesWhenTheHostCannotHoldTwoGenerations and
// TestUpdateSucceedsWhenTheHostHasEnoughHeadroom (update_test.go).
// Duplicating the arithmetic's own tests here is exactly the kind of
// second copy that drifts silently from the one in internal/publish.

func TestHeadroomErrorNamesTheShortfall(t *testing.T) {
	err := &HeadroomError{NeedBytes: 300, HaveBytes: 100, ShortfallBytes: 200}
	if !strings.Contains(err.Error(), "200") {
		t.Errorf("HeadroomError.Error() does not mention the shortfall: %v", err)
	}
}

// A live release id the store cannot load is a different failure this
// operation meets elsewhere (LoadRecord, in moveServers) — checkHeadroom
// must not error out over it, it simply cannot add a number it does not
// have, and the check still runs against the target alone.
func TestCheckHeadroomToleratesAnUnloadableLiveRelease(t *testing.T) {
	r := newRig(t)
	target := r.release("rel-2")

	if err := r.ctl.checkHeadroom(target, "rel-ghost", r.prof); err != nil {
		t.Fatalf("checkHeadroom errored over a live release it could not load: %v", err)
	}
}

// The host's own memory accounting is unreadable — checkHeadroom must
// surface that as an error rather than silently treating it as "plenty of
// room" or "no room at all".
func TestCheckHeadroomFailsWhenHostMemoryCannotBeRead(t *testing.T) {
	r := newRig(t)
	target := r.release("rel-1")
	r.ctl.memInfoPath = "/does/not/exist"

	if err := r.ctl.checkHeadroom(target, "", r.prof); err == nil {
		t.Fatal("checkHeadroom succeeded with an unreadable meminfo path")
	}
}

// Updating to the release already live needs no headroom for a SECOND
// generation at all — old and new are the same generation, so this must
// count it once, not twice. Available memory is set strictly BETWEEN one
// generation's need and two generations' worth: a double-count would wrongly
// refuse here, which is the regression this test exists to catch.
func TestCheckHeadroomSkipsTheLiveGenerationWhenItIsTheTarget(t *testing.T) {
	r := newRig(t)
	target := r.release("rel-1")
	one := publish.GenerationBytes(target, r.prof)
	r.setMemAvailable(one + one/2)

	if err := r.ctl.checkHeadroom(target, "rel-1", r.prof); err != nil {
		t.Fatalf("checkHeadroom double-counted the live release: %v", err)
	}
}

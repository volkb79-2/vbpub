package opctl

import (
	"strings"
	"testing"
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

package opctl

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// headroomDefaultMemInfoPath is where the kernel publishes host memory
// accounting. A field rather than a hardcoded literal in checkHeadroom so a
// fixture file stands in for it in tests (WithMemInfoPath).
const headroomDefaultMemInfoPath = "/proc/meminfo"

// HeadroomError is Update declining before anything is stopped, because the
// host cannot hold two generations at once.
//
// Publish always happens before the old generation is torn down — the same
// window Activate opens, and Update's own cohort cycle opens it for the
// same reason: a failed publish must leave something to restart the cohort
// on. Whether the SERVERS are up or down does not change this — a
// generation's tmpfs is charged to its own hold units regardless of who is
// consuming it — so the byte math is the same as a per-server rolling
// design would need; only the DURATION of the overlap is shorter here (one
// switch, not a whole pass over every server). Refusing up front (oracle
// 32) replaces "the cohort goes down and the publish then fails on ENOSPC
// or an OOM, leaving every server offline with nothing to come back to"
// with a refusal that costs nothing and changes nothing.
type HeadroomError struct {
	NeedBytes, HaveBytes, ShortfallBytes int64
}

func (e *HeadroomError) Error() string {
	return fmt.Sprintf("opctl: a rolling update needs room for two generations at once "+
		"(%d bytes) but the host has %d bytes available — short by %d bytes; free memory, "+
		"reduce a class's content, or accept full-cohort downtime with --strategy all-at-once",
		e.NeedBytes, e.HaveBytes, e.ShortfallBytes)
}

// generationBytes estimates the tmpfs footprint a release would occupy: the
// same per-class sizing publish.Publish itself applies (publish.ClassSize),
// computed directly from the manifest rather than from a published Record.
//
// It has to be computed this way because the target release has no Record
// yet — Update's headroom check runs BEFORE Publish, which is the entire
// point of oracle 32 (refused before anything is stopped). The live
// release's Record does exist, but this function is used for both sides so
// "the target's estimate" and "the live one's" are the same kind of number
// rather than two different ones that happen to be compared.
func generationBytes(rel *store.Release, prof *profile.Profile) int64 {
	var total int64
	for _, c := range prof.Classes {
		if c.Kind != profile.KindManaged {
			continue
		}
		total += publish.ClassSize(rel.Manifest.ClassBytes(c.Name))
	}
	return total
}

// availableHostBytes reads MemAvailable from a /proc/meminfo-shaped file:
// the kernel's own estimate of memory obtainable for new allocations
// without swapping, which is exactly what tmpfs pages compete for.
func availableHostBytes(path string) (int64, error) {
	if path == "" {
		path = headroomDefaultMemInfoPath
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return 0, fmt.Errorf("opctl: reading %s: %w", path, err)
	}
	for _, line := range strings.Split(string(body), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "MemAvailable:" {
			kb, err := strconv.ParseInt(fields[1], 10, 64)
			if err != nil {
				return 0, fmt.Errorf("opctl: %s MemAvailable line %q is not a number", path, line)
			}
			return kb * 1024, nil
		}
	}
	return 0, fmt.Errorf("opctl: %s has no MemAvailable line", path)
}

// checkHeadroom refuses an update before anything is stopped when the host
// cannot hold both the live generation and the target one at once.
//
// liveReleaseID is a.Release, which may be empty — a profile updating from
// nothing has no old generation to add, so the sum is just the target's.
func (c *Controller) checkHeadroom(target *store.Release, liveReleaseID string,
	prof *profile.Profile) error {

	need := generationBytes(target, prof)
	if liveReleaseID != "" && liveReleaseID != target.ID {
		if live, err := c.st.Load(liveReleaseID); err == nil {
			need += generationBytes(live, prof)
		}
		// A live release id srdm cannot load is a different failure this
		// operation meets elsewhere (LoadRecord, in moveServers); the
		// headroom check does not repeat that refusal here, it just cannot
		// add a number it does not have.
	}
	have, err := availableHostBytes(c.memInfoPath)
	if err != nil {
		return err
	}
	if have < need {
		return &HeadroomError{NeedBytes: need, HaveBytes: have, ShortfallBytes: need - have}
	}
	return nil
}

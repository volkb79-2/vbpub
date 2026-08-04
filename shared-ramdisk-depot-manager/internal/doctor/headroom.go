package doctor

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"srdm/internal/assign"
	"srdm/internal/config"
	"srdm/internal/profile"
	"srdm/internal/publish"
	"srdm/internal/store"
)

// checkUpdateHeadroom reports whether the host could sustain a rolling
// update of the profile's CURRENT release — P14's own preflight refuses the
// same shortfall against a specific target release, immediately before
// stopping anything; this is the same question asked on a quiet afternoon,
// against the only release doctor has to ask it about.
//
// It duplicates opctl's own generationBytes/availableHostBytes rather than
// importing them: doctor never depends on opctl (the orchestration layer
// that depends on doctor's own checks conceptually sits above it, not
// beside it) and opctl never depends on doctor, so each reads
// /proc/meminfo through its own small parser. The duplication is a dozen
// lines; the alternative is a package edge neither package's layering
// wants.
func checkUpdateHeadroom(cfg config.Config, p *profile.Profile, opts Options) Check {
	c := Check{ID: "update-headroom", Title: "the host can hold two generations for a rolling update"}
	if p == nil {
		c.Status = StatusSkip
		c.Detail = "no profile given"
		return c
	}
	asg, err := assign.New(cfg)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot open the assignment store: %v", err)
		return c
	}
	a, err := asg.Load(p.ID)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read %s's assignment: %v", p.ID, err)
		return c
	}
	if a.Release == "" {
		c.Status = StatusPass
		c.Detail = "no active release yet; a rolling update has nothing to hold twice"
		return c
	}
	rel, err := loadRelease(cfg, a.Release)
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot load the active release %s: %v", a.Release, err)
		return c
	}
	one := headroomGenerationBytes(rel, p)
	need := 2 * one
	have, err := availableMemInfoBytes(opts.memInfoPath())
	if err != nil {
		c.Status = StatusSkip
		c.Detail = fmt.Sprintf("cannot read host memory accounting: %v", err)
		return c
	}
	if have < need {
		c.Status = StatusFail
		c.Detail = fmt.Sprintf("a rolling update of the current %s generation needs about %s "+
			"resident at once; the host has %s available", a.Release, formatBytes(need), formatBytes(have))
		c.Fix = "free memory before the next update, or run it with " +
			"`srdm update --strategy all-at-once`, which never holds two generations at once " +
			"(at the cost of taking the whole cohort offline for the swap)"
		return c
	}
	c.Status = StatusPass
	c.Detail = fmt.Sprintf("the host has %s available, room for roughly two %s generations",
		formatBytes(have), formatBytes(one))
	return c
}

// headroomGenerationBytes estimates the tmpfs footprint a release occupies:
// the same per-class sizing publish.Publish itself applies
// (publish.ClassSize), computed from the manifest.
func headroomGenerationBytes(rel *store.Release, p *profile.Profile) int64 {
	var total int64
	for _, cl := range p.Classes {
		if cl.Kind != profile.KindManaged {
			continue
		}
		total += publish.ClassSize(rel.Manifest.ClassBytes(cl.Name))
	}
	return total
}

// availableMemInfoBytes reads MemAvailable from a /proc/meminfo-shaped file:
// the kernel's own estimate of memory obtainable for new allocations
// without swapping, which is what tmpfs pages compete for.
func availableMemInfoBytes(path string) (int64, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	for _, line := range strings.Split(string(body), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "MemAvailable:" {
			kb, err := strconv.ParseInt(fields[1], 10, 64)
			if err != nil {
				return 0, fmt.Errorf("%s MemAvailable line %q is not a number", path, line)
			}
			return kb * 1024, nil
		}
	}
	return 0, fmt.Errorf("%s has no MemAvailable line", path)
}

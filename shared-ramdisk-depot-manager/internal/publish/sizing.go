package publish

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"srdm/internal/profile"
	"srdm/internal/store"
)

// GenerationBytes estimates the tmpfs footprint a release would occupy
// under a profile: the same per-class sizing Publish itself applies
// (ClassSize over Manifest.ClassBytes), computed directly from a release
// and a profile rather than from a published Record.
//
// It has to work this way for P14's headroom check (opctl.checkHeadroom,
// doctor's update-headroom check): both ask "would this fit" about a
// release that has not been published yet, so there is no Record to read
// ClassRecord.SizeBytes off. This is the ONE place that arithmetic lives —
// it used to be copied once into internal/opctl and once into
// internal/doctor, which is exactly the kind of duplication that drifts
// silently: doctor reporting headroom fine while opctl refuses the same
// update, or the reverse, the day someone tunes SizeHeadroomPercent or
// SizeGranularity in only one of two copies.
func GenerationBytes(rel *store.Release, prof *profile.Profile) int64 {
	var total int64
	for _, c := range prof.Classes {
		if c.Kind != profile.KindManaged {
			continue
		}
		total += ClassSize(rel.Manifest.ClassBytes(c.Name))
	}
	return total
}

// AvailableHostBytes reads MemAvailable from a /proc/meminfo-shaped file:
// the kernel's own estimate of memory obtainable for new allocations
// without swapping, which is what tmpfs pages compete for. An empty path
// means the real /proc/meminfo.
//
// A host fact rather than a generation one, but kept beside
// GenerationBytes rather than split into its own file: the two are always
// used together (need vs. have) and P14 is the only caller of either.
func AvailableHostBytes(path string) (int64, error) {
	if path == "" {
		path = "/proc/meminfo"
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return 0, fmt.Errorf("publish: reading %s: %w", path, err)
	}
	for _, line := range strings.Split(string(body), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[0] == "MemAvailable:" {
			kb, err := strconv.ParseInt(fields[1], 10, 64)
			if err != nil {
				return 0, fmt.Errorf("publish: %s MemAvailable line %q is not a number", path, line)
			}
			return kb * 1024, nil
		}
	}
	return 0, fmt.Errorf("publish: %s has no MemAvailable line", path)
}

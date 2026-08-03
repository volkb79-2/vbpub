package store

import (
	"fmt"
	"os"
	"path/filepath"

	"srdm/internal/profile"
)

// ProbeFailure reports a profile probe that did not hold. Like an
// unclassified path it is a refusal — srdm declining by contract — rather
// than a fault.
type ProbeFailure struct {
	Probe   profile.Probe
	Profile string
	Reason  string
}

func (e *ProbeFailure) Error() string {
	return fmt.Sprintf("probe %q of profile %q failed: %s (path %q)",
		e.Probe.Name, e.Profile, e.Reason, e.Probe.Path)
}

// runProbes evaluates every probe against the transaction tree.
func runProbes(root string, p *profile.Profile) error {
	for _, pr := range p.Probes {
		target := filepath.Join(root, filepath.FromSlash(pr.Path))
		info, statErr := os.Lstat(target)

		switch pr.Kind {
		case profile.ProbeExists:
			if statErr != nil {
				return &ProbeFailure{Probe: pr, Profile: p.ID, Reason: "path is missing"}
			}
		case profile.ProbeAbsent:
			if statErr == nil {
				return &ProbeFailure{Probe: pr, Profile: p.ID, Reason: "path is present"}
			}
			if !os.IsNotExist(statErr) {
				return fmt.Errorf("store: probe %q: %w", pr.Name, statErr)
			}
		case profile.ProbeNonEmpty:
			if statErr != nil {
				return &ProbeFailure{Probe: pr, Profile: p.ID, Reason: "path is missing"}
			}
			if !info.Mode().IsRegular() {
				return &ProbeFailure{Probe: pr, Profile: p.ID, Reason: "path is not a regular file"}
			}
			if info.Size() == 0 {
				return &ProbeFailure{Probe: pr, Profile: p.ID, Reason: "file is empty"}
			}
		default:
			return fmt.Errorf("store: probe %q has unknown kind %q", pr.Name, pr.Kind)
		}
	}
	return nil
}

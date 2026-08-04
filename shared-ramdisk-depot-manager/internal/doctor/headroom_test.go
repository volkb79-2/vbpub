package doctor

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/assign"
	"srdm/internal/config"
)

func writeMemInfo(t *testing.T, dir string, availableBytes int64) string {
	t.Helper()
	path := filepath.Join(dir, "meminfo")
	body := fmt.Sprintf("MemTotal:       20000000 kB\nMemAvailable:   %d kB\n", availableBytes/1024)
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func assignRelease(t *testing.T, cfg config.Config, profileID, releaseID string) {
	t.Helper()
	asg, err := assign.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	a, err := asg.Load(profileID)
	if err != nil {
		t.Fatal(err)
	}
	a.SetRelease(releaseID)
	if err := asg.Save(a); err != nil {
		t.Fatal(err)
	}
}

func TestUpdateHeadroomSkipsWithoutAProfile(t *testing.T) {
	f := newFixture(t)
	checks := Run(f.config(), nil, f.options())
	if c := find(t, checks, "update-headroom"); c.Status != StatusSkip {
		t.Fatalf("update-headroom is %s with no profile, want skip", c.Status)
	}
}

func TestUpdateHeadroomPassesWithNoActiveRelease(t *testing.T) {
	f := newFixture(t)
	checks := Run(f.config(), fixtureProfile(t), f.options())
	if c := find(t, checks, "update-headroom"); c.Status != StatusPass {
		t.Fatalf("update-headroom is %s with nothing assigned: %s", c.Status, c.Detail)
	}
}

func TestUpdateHeadroomPassesWhenTheHostHasRoomForTwoGenerations(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	buildStore(t, cfg, fixtureProfile(t))
	assignRelease(t, cfg, "soulmask", "rel-1")

	opts := f.options()
	opts.MemInfoPath = writeMemInfo(t, f.dir, 4<<30) // 4 GiB — generous

	checks := Run(cfg, fixtureProfile(t), opts)
	if c := find(t, checks, "update-headroom"); c.Status != StatusPass {
		t.Fatalf("update-headroom is %s with 4 GiB available: %s", c.Status, c.Detail)
	}
}

func TestUpdateHeadroomFailsAndNamesTheStrategyEscapeHatch(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	buildStore(t, cfg, fixtureProfile(t))
	assignRelease(t, cfg, "soulmask", "rel-1")

	opts := f.options()
	opts.MemInfoPath = writeMemInfo(t, f.dir, 1<<20) // 1 MiB — nowhere near enough

	checks := Run(cfg, fixtureProfile(t), opts)
	c := find(t, checks, "update-headroom")
	if c.Status != StatusFail {
		t.Fatalf("update-headroom is %s with 1 MiB available: %s", c.Status, c.Detail)
	}
	if !strings.Contains(c.Detail, "rel-1") {
		t.Errorf("the detail does not name the release it sized: %s", c.Detail)
	}
	if !strings.Contains(c.Fix, "all-at-once") {
		t.Errorf("the fix does not name the strategy that avoids the shortfall: %s", c.Fix)
	}
}

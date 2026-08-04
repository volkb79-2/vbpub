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

// A StateDir that is a FILE, not a directory, makes assign.New's own
// os.MkdirAll fail — checkUpdateHeadroom must skip rather than panic or
// misreport.
func TestUpdateHeadroomSkipsWhenTheAssignmentStoreCannotOpen(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	blocker := filepath.Join(f.dir, "state-is-a-file")
	if err := os.WriteFile(blocker, []byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg.StateDir = blocker

	c := checkUpdateHeadroom(cfg, fixtureProfile(t), f.options())
	if c.Status != StatusSkip {
		t.Fatalf("update-headroom is %s when the assignment store cannot open: %s", c.Status, c.Detail)
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

// The assignment names a release nobody ever promoted into the store — a
// stale or corrupted assignment — and the check must say so and skip
// rather than crash or silently report pass.
func TestUpdateHeadroomSkipsWhenTheActiveReleaseCannotBeLoaded(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	assignRelease(t, cfg, "soulmask", "rel-ghost") // never built with buildStore

	checks := Run(cfg, fixtureProfile(t), f.options())
	c := find(t, checks, "update-headroom")
	if c.Status != StatusSkip {
		t.Fatalf("update-headroom is %s for an assignment naming an unloadable release: %s",
			c.Status, c.Detail)
	}
	if !strings.Contains(c.Detail, "rel-ghost") {
		t.Errorf("the detail does not name the release it could not load: %s", c.Detail)
	}
}

// The host memory accounting itself is unreadable — a container without
// /proc/meminfo, say — and that is a skip, not a failure: doctor stays
// usable when it cannot answer every question.
func TestUpdateHeadroomSkipsWhenHostMemoryCannotBeRead(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	buildStore(t, cfg, fixtureProfile(t))
	assignRelease(t, cfg, "soulmask", "rel-1")

	opts := f.options()
	opts.MemInfoPath = filepath.Join(f.dir, "does-not-exist")

	checks := Run(cfg, fixtureProfile(t), opts)
	if c := find(t, checks, "update-headroom"); c.Status != StatusSkip {
		t.Fatalf("update-headroom is %s when host memory cannot be read: %s", c.Status, c.Detail)
	}
}

func TestUpdateHeadroomFailsAndNamesTheRelease(t *testing.T) {
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
	if c.Fix == "" {
		t.Error("a failing check must name the remedy")
	}
}

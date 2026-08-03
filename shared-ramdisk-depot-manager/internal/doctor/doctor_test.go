package doctor

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/config"
	"srdm/internal/profile"
	"srdm/internal/wings"
)

// fixture builds a fake host surface: a cgroup root, a mountinfo file and a
// systemd property table. Every offline check reads only these, so the whole
// subset is exercisable without root and without a real host.
type fixture struct {
	dir        string
	cgroupRoot string
	mountInfo  string
	props      map[string]string
	propErr    error

	// The Wings half of the fixture: a node that is set up correctly, which
	// the individual cases then break one property at a time.
	rootPropagation      string
	containerPropagation string
	containerName        string
	inspectErr           error
	chownWalk            wings.ChownWalk
	chownWalkErr         error
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	dir := t.TempDir()
	f := &fixture{
		dir:        dir,
		cgroupRoot: filepath.Join(dir, "cgroup"),
		mountInfo:  filepath.Join(dir, "mountinfo"),
		props: map[string]string{
			"srdm.slice/LoadState": "loaded",
			"srdm.slice/MemoryMin": "536870912", // 512M, the shipped value
		},
		// A correctly set up node: / is shared as it is on any systemd host,
		// the Wings container binds the volume tree rslave, and the pre-boot
		// chown walk is off.
		rootPropagation:      "shared:1",
		containerPropagation: "rslave",
		containerName:        "wings",
		chownWalk:            wings.ChownWalk{Enabled: false, Known: true, Source: "the fixture"},
	}
	if err := os.MkdirAll(f.cgroupRoot, 0o755); err != nil {
		t.Fatal(err)
	}
	// An empty but present store is the healthy state of a fresh node.
	if err := os.MkdirAll(f.config().ReleasesDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	f.writeControllers(t, "cpuset cpu io memory hugetlb pids rdma misc")
	f.writeMountInfo(t, "rw,nsdelegate,memory_recursiveprot")
	return f
}

func (f *fixture) writeControllers(t *testing.T, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(f.cgroupRoot, "cgroup.controllers"), []byte(body+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func (f *fixture) writeMountInfo(t *testing.T, superOptions string) {
	t.Helper()
	body := strings.Join([]string{
		// / carries the Wings bind root on a stock node — /var/lib is not a
		// mount point of its own — so its propagation is what decides
		// whether anything srdm mounts can reach the container.
		"24 1 0:20 / / rw,relatime " + f.rootPropagation + " - ext4 /dev/sda1 rw",
		"25 30 0:24 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw",
		"31 24 0:27 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime shared:9 - cgroup2 cgroup2 " + superOptions,
		"36 25 0:32 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw,size=1g",
	}, "\n") + "\n"
	if err := os.WriteFile(f.mountInfo, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func (f *fixture) options() Options {
	return Options{
		CgroupRoot:    f.cgroupRoot,
		MountInfoPath: f.mountInfo,
		UnitProperty: func(unit, property string) (string, error) {
			if f.propErr != nil {
				return "", f.propErr
			}
			v, ok := f.props[unit+"/"+property]
			if !ok {
				return "", errors.New("no such property")
			}
			return v, nil
		},
		Inspector: f,
		ChownWalk: func(config.Wings) func() (wings.ChownWalk, error) {
			return func() (wings.ChownWalk, error) { return f.chownWalk, f.chownWalkErr }
		},
	}
}

// BindPropagation makes the fixture its own container inspector.
func (f *fixture) BindPropagation(context.Context, string) (string, string, error) {
	if f.inspectErr != nil {
		return "", "", f.inspectErr
	}
	return f.containerPropagation, f.containerName, nil
}

func (f *fixture) config() config.Config {
	cfg := config.Default()
	cfg.StateDir = filepath.Join(f.dir, "state")
	cfg.RunDir = filepath.Join(f.dir, "run")
	cfg.Wings = config.Wings{
		BindRoot:   filepath.Join(f.dir, "pterodactyl"),
		VolumeRoot: filepath.Join(f.dir, "pterodactyl", "volumes"),
		ConfigPath: filepath.Join(f.dir, "wings.yml"),
	}
	return cfg
}

func find(t *testing.T, checks []Check, id string) Check {
	t.Helper()
	for _, c := range checks {
		if c.ID == id {
			return c
		}
	}
	t.Fatalf("no check with id %q in %v", id, ids(checks))
	return Check{}
}

func ids(checks []Check) []string {
	out := make([]string, 0, len(checks))
	for _, c := range checks {
		out = append(out, c.ID)
	}
	return out
}

// A profile whose managed floors sum to 350M — the Soulmask number the
// shipped 512M parent floor is sized against.
func fixtureProfile(t *testing.T) *profile.Profile {
	t.Helper()
	p := &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "soulmask",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged, Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20},
			{Name: "code", Kind: profile.KindManaged, Paths: []string{"Engine"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestHealthyHostPassesEveryOfflineCheck(t *testing.T) {
	f := newFixture(t)
	checks := Run(f.config(), fixtureProfile(t), f.options())

	for _, c := range checks {
		if c.Status != StatusPass {
			t.Errorf("check %q is %s on a healthy fixture: %s", c.ID, c.Status, c.Detail)
		}
	}
	if len(Failed(checks)) != 0 {
		t.Errorf("Failed() is not empty on a healthy fixture")
	}
}

func TestCgroupV2MissingIsFatal(t *testing.T) {
	f := newFixture(t)
	if err := os.Remove(filepath.Join(f.cgroupRoot, "cgroup.controllers")); err != nil {
		t.Fatal(err)
	}
	checks := Run(f.config(), fixtureProfile(t), f.options())

	c := find(t, checks, "cgroup-v2")
	if c.Status != StatusFail {
		t.Fatalf("cgroup-v2 is %s without a unified hierarchy", c.Status)
	}
	if c.Fix == "" {
		t.Error("a failing check must name the remedy")
	}
}

func TestMissingControllersAreNamed(t *testing.T) {
	f := newFixture(t)
	f.writeControllers(t, "cpuset pids")
	checks := Run(f.config(), fixtureProfile(t), f.options())

	c := find(t, checks, "cgroup-controllers")
	if c.Status != StatusFail {
		t.Fatalf("cgroup-controllers is %s with cpu, io and memory absent", c.Status)
	}
	for _, want := range []string{"cpu", "io", "memory"} {
		if !strings.Contains(c.Detail, want) {
			t.Errorf("the detail does not name the missing %q controller: %s", want, c.Detail)
		}
	}
}

// Without memory_recursiveprot, every MemoryMin below the parent slice
// stops protecting the pages it names — the floors become decoration. The
// check has to fail closed rather than warn.
func TestRecursiveProtMissingIsFatal(t *testing.T) {
	f := newFixture(t)
	f.writeMountInfo(t, "rw,nsdelegate")
	checks := Run(f.config(), fixtureProfile(t), f.options())

	c := find(t, checks, "cgroup-recursiveprot")
	if c.Status != StatusFail {
		t.Fatalf("cgroup-recursiveprot is %s without the option", c.Status)
	}
	if !strings.Contains(c.Fix, "remount") {
		t.Errorf("the fix does not say how to restore it: %s", c.Fix)
	}
}

// A slice name systemd does not know does not fail at container-create time
// — it fails OPEN into an unlimited transient slice. So this check is the
// only thing standing between a typo and an unbounded tier.
func TestUnloadedParentSliceIsFatalAndSaysWhy(t *testing.T) {
	f := newFixture(t)
	f.props["srdm.slice/LoadState"] = "not-found"
	checks := Run(f.config(), fixtureProfile(t), f.options())

	c := find(t, checks, "parent-slice")
	if c.Status != StatusFail {
		t.Fatalf("parent-slice is %s for an unloaded unit", c.Status)
	}
	if !strings.Contains(c.Fix, "OPEN") {
		t.Errorf("the fix does not explain that a missing slice fails open: %s", c.Fix)
	}
}

func TestParentFloorMustCoverTheSumOfClassFloors(t *testing.T) {
	f := newFixture(t)
	// 256M, below the profile's 350M of managed floors.
	f.props["srdm.slice/MemoryMin"] = "268435456"
	checks := Run(f.config(), fixtureProfile(t), f.options())

	c := find(t, checks, "parent-slice")
	if c.Status != StatusFail {
		t.Fatalf("parent-slice is %s with MemoryMin below the sum of class floors", c.Status)
	}
	// Both numbers must appear, or the operator cannot tell how far short
	// they are.
	if !strings.Contains(c.Detail, "256M") || !strings.Contains(c.Detail, "350M") {
		t.Errorf("the detail does not carry both numbers: %s", c.Detail)
	}
}

func TestParentFloorExactlyCoveringPasses(t *testing.T) {
	f := newFixture(t)
	f.props["srdm.slice/MemoryMin"] = "367001600" // exactly 350M
	checks := Run(f.config(), fixtureProfile(t), f.options())
	if c := find(t, checks, "parent-slice"); c.Status != StatusPass {
		t.Fatalf("parent-slice is %s when the floor exactly covers the sum: %s", c.Status, c.Detail)
	}
}

// Without a profile there is no floor to check against. Guessing one would
// be worse than saying so.
func TestParentFloorIsSkippedWithoutAProfile(t *testing.T) {
	f := newFixture(t)
	checks := Run(f.config(), nil, f.options())
	if c := find(t, checks, "parent-slice"); c.Status != StatusSkip {
		t.Fatalf("parent-slice is %s with no profile, want skip", c.Status)
	}
}

// An unreachable systemd is a skip, not a fail: doctor must stay usable in
// a container or against a store on a mounted disk.
func TestUnreachableSystemdSkipsRatherThanFails(t *testing.T) {
	f := newFixture(t)
	f.propErr = errors.New("systemctl: command not found")
	checks := Run(f.config(), fixtureProfile(t), f.options())
	if c := find(t, checks, "parent-slice"); c.Status != StatusSkip {
		t.Fatalf("parent-slice is %s when systemd is unreachable, want skip", c.Status)
	}
}

func TestParseMemoryValue(t *testing.T) {
	cases := map[string]int64{
		"536870912": 536870912,
		"":          0,
		"[not set]": 0,
		"0":         0,
	}
	for in, want := range cases {
		got, err := parseMemoryValue(in)
		if err != nil {
			t.Errorf("parseMemoryValue(%q): %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("parseMemoryValue(%q) = %d, want %d", in, got, want)
		}
	}
	if _, err := parseMemoryValue("lots"); err == nil {
		t.Error("a non-numeric MemoryMin parsed cleanly")
	}
	// systemd renders an unset maximum as "infinity"; treating it as
	// "covers everything" is the only reading that is not a false alarm.
	if got, err := parseMemoryValue("infinity"); err != nil || got <= 0 {
		t.Errorf(`parseMemoryValue("infinity") = %d, %v`, got, err)
	}
}

func TestFormatBytes(t *testing.T) {
	cases := map[int64]string{
		512:              "512B",
		150 << 20:        "150M",
		350 << 20:        "350M",
		512 << 20:        "512M",
		2 << 30:          "2G",
		536870912 + 1024: "512M",
	}
	for in, want := range cases {
		if got := formatBytes(in); got != want {
			t.Errorf("formatBytes(%d) = %q, want %q", in, got, want)
		}
	}
}

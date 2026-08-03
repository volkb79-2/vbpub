package wings

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/config"
	"srdm/internal/mountinfo"
)

// --- the pre-boot chown walk ----------------------------------------------

func writeConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yml")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// The value srdm is actually looking for, in the shape a real node writes it.
func TestChownWalkReadsTheKeyOutOfTheNodeConfig(t *testing.T) {
	path := writeConfig(t, `debug: false
app_name: Pterodactyl
system:
  root_directory: /var/lib/pterodactyl
  check_permissions_on_boot: false
  data: /var/lib/pterodactyl/volumes
docker:
  network:
    name: pterodactyl_nw
`)
	walk, err := ReadChownWalk(path)
	if err != nil {
		t.Fatal(err)
	}
	if walk.Enabled {
		t.Error("check_permissions_on_boot: false was read as the walk being enabled")
	}
	if !walk.Known {
		t.Error("an unambiguous false was reported as unknown")
	}
}

func TestChownWalkRecognisesTheEnabledSpellings(t *testing.T) {
	for _, spelling := range []string{"true", "True", "yes", "on", `"true"`} {
		walk, err := ReadChownWalk(writeConfig(t,
			"system:\n  check_permissions_on_boot: "+spelling+"\n"))
		if err != nil {
			t.Fatalf("%s: %v", spelling, err)
		}
		if !walk.Enabled || !walk.Known {
			t.Errorf("%s: read as enabled=%v known=%v", spelling, walk.Enabled, walk.Known)
		}
	}
	for _, spelling := range []string{"false", "False", "no", "off", `'false'`} {
		walk, err := ReadChownWalk(writeConfig(t,
			"system:\n  check_permissions_on_boot: "+spelling+"\n"))
		if err != nil {
			t.Fatalf("%s: %v", spelling, err)
		}
		if walk.Enabled || !walk.Known {
			t.Errorf("%s: read as enabled=%v known=%v", spelling, walk.Enabled, walk.Known)
		}
	}
}

// Wings' own default is true, so an absent key means the walk runs. Reading
// absence as "off" would be the one mistake that produces an unstartable
// server, which is what this whole check exists to prevent.
func TestAnAbsentKeyMeansTheWalkRuns(t *testing.T) {
	walk, err := ReadChownWalk(writeConfig(t, "system:\n  root_directory: /var/lib/pterodactyl\n"))
	if err != nil {
		t.Fatal(err)
	}
	if !walk.Enabled {
		t.Fatal("an absent check_permissions_on_boot was read as the walk being off")
	}
	if !strings.Contains(walk.Source, "absent") {
		t.Errorf("the source does not explain where the answer came from: %q", walk.Source)
	}
}

// A comment is not a setting. This is the shape an operator leaves behind
// after trying the workaround and reverting it, and reading it as active
// would silently remove the refusal that keeps their server startable.
func TestACommentedOutKeyIsNotASetting(t *testing.T) {
	walk, err := ReadChownWalk(writeConfig(t, `system:
  # check_permissions_on_boot: false
  root_directory: /var/lib/pterodactyl
`))
	if err != nil {
		t.Fatal(err)
	}
	if !walk.Enabled {
		t.Fatal("a commented-out setting was read as active")
	}
}

// Every way of not knowing must land on "the walk runs". srdm has no YAML
// parser (D-005 declined that dependency and this inherits the decision), so
// the scanner has to fail closed: guessing the other way produces exactly
// the EROFS start failure the check exists to prevent.
func TestEverythingAmbiguousFailsClosed(t *testing.T) {
	cases := map[string]string{
		"two answers":  "system:\n  check_permissions_on_boot: false\nother:\n  check_permissions_on_boot: true\n",
		"not a bool":   "system:\n  check_permissions_on_boot: maybe\n",
		"empty value":  "system:\n  check_permissions_on_boot:\n",
		"missing file": "",
	}
	for name, body := range cases {
		path := writeConfig(t, body)
		if name == "missing file" {
			path = filepath.Join(t.TempDir(), "absent.yml")
		}
		walk, err := ReadChownWalk(path)
		if err == nil {
			t.Errorf("%s: reported no error", name)
		}
		if !walk.Enabled {
			t.Errorf("%s: an unreadable answer was treated as the walk being off, which is "+
				"the reading that makes a server unstartable", name)
		}
		if walk.Known {
			t.Errorf("%s: an unreadable answer was reported as known", name)
		}
	}
}

// --- propagation -----------------------------------------------------------

type fakeInspector struct {
	propagation string
	name        string
	err         error
}

func (f fakeInspector) BindPropagation(context.Context, string) (string, string, error) {
	return f.propagation, f.name, f.err
}

func writeMountInfo(t *testing.T, lines ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "mountinfo")
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func testWings() config.Wings {
	return config.Wings{
		BindRoot:   "/var/lib/pterodactyl",
		VolumeRoot: "/var/lib/pterodactyl/volumes",
		ConfigPath: "/etc/pterodactyl/config.yml",
	}
}

// The bind root is almost never a mount point of its own — /var/lib is part
// of the root filesystem on any stock node. Looking for an exact match would
// report "no mount" on every real host, so the enclosing mount is what
// decides.
func TestPropagationComesFromTheEnclosingMount(t *testing.T) {
	path := writeMountInfo(t,
		"24 1 0:20 / / rw,relatime shared:1 - ext4 /dev/sda1 rw",
		"36 24 0:32 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw",
	)
	p, err := ReadPropagation(context.Background(), testWings(), path,
		fakeInspector{propagation: "rslave", name: "wings"})
	if err != nil {
		t.Fatal(err)
	}
	if p.HostMountPoint != "/" {
		t.Errorf("HostMountPoint = %q, want / — nothing else carries the bind root", p.HostMountPoint)
	}
	if !p.HostShared() {
		t.Errorf("HostPeerGroup = %q, want shared", p.HostPeerGroup)
	}
	if !p.ContainerReceives() {
		t.Errorf("Container = %q, want a propagation that receives host events", p.Container)
	}
}

// A deeper mount wins: if /var/lib/pterodactyl is its own filesystem, that
// is the one whose propagation the mounts travel over.
func TestTheDeepestEnclosingMountWins(t *testing.T) {
	path := writeMountInfo(t,
		"24 1 0:20 / / rw,relatime shared:1 - ext4 /dev/sda1 rw",
		"40 24 0:44 / /var/lib rw,relatime shared:7 - ext4 /dev/sdb1 rw",
		"41 40 0:45 / /var/lib/pterodactyl rw,relatime - ext4 /dev/sdc1 rw",
	)
	p, err := ReadPropagation(context.Background(), testWings(), path, fakeInspector{})
	if err != nil {
		t.Fatal(err)
	}
	if p.HostMountPoint != "/var/lib/pterodactyl" {
		t.Fatalf("HostMountPoint = %q, want the deepest mount carrying the bind root",
			p.HostMountPoint)
	}
	// And it is private, so the check must not be satisfied by the shared
	// ancestors above it.
	if p.HostShared() {
		t.Error("a private mount carrying the bind root was reported as shared")
	}
}

// Docker's default, and the 2026-07-31 outage.
func TestRPrivateDoesNotReceiveHostEvents(t *testing.T) {
	for _, prop := range []string{"rprivate", "private", ""} {
		p := Propagation{Container: prop}
		if p.ContainerReceives() {
			t.Errorf("%q was reported as receiving host mount events", prop)
		}
	}
	for _, prop := range []string{"rslave", "slave", "rshared", "shared"} {
		p := Propagation{Container: prop}
		if !p.ContainerReceives() {
			t.Errorf("%q was reported as NOT receiving host mount events", prop)
		}
	}
}

// An unreachable Docker is not evidence either way. Reporting it as rprivate
// would send an operator to fix a container that is already correct;
// reporting it as fine would reproduce the outage.
func TestAnUnreachableInspectorIsDegradedNotAssumed(t *testing.T) {
	path := writeMountInfo(t, "24 1 0:20 / / rw,relatime shared:1 - ext4 /dev/sda1 rw")
	p, err := ReadPropagation(context.Background(), testWings(), path,
		fakeInspector{err: errors.New("dial unix /var/run/docker.sock: no such file")})
	if err != nil {
		t.Fatal(err)
	}
	if p.Degraded == "" {
		t.Fatal("an unreachable inspector reported a complete answer")
	}
	if p.Container != "" {
		t.Errorf("Container = %q, want empty — srdm did not find out", p.Container)
	}
	// The host half is still usable, and is the half srdm can always read.
	if !p.HostShared() {
		t.Error("the host half was lost along with the container half")
	}
}

// A bind root nothing carries is an error, not a silent pass.
func TestNoMountCarryingTheBindRootIsAnError(t *testing.T) {
	path := writeMountInfo(t, "36 24 0:32 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw")
	if _, err := ReadPropagation(context.Background(), testWings(), path, fakeInspector{}); err == nil {
		t.Fatal("a bind root on no mounted filesystem reported a propagation anyway")
	}
}

func TestHostSharedRequiresShared(t *testing.T) {
	for _, kind := range []string{
		mountinfo.PropagationPrivate,
		mountinfo.PropagationSlave,
		mountinfo.PropagationUnbindable,
	} {
		if (Propagation{HostPeerGroup: kind}).HostShared() {
			t.Errorf("%q was accepted as a shared host peer group; there would be nothing "+
				"for the container's rslave to be a slave of", kind)
		}
	}
	if !(Propagation{HostPeerGroup: mountinfo.PropagationShared}).HostShared() {
		t.Error("shared was rejected")
	}
}

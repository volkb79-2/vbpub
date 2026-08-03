package mountinfo

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

const sample = `21 27 0:20 / /sys rw,nosuid,nodev,noexec,relatime shared:7 - sysfs sysfs rw
25 21 0:24 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime shared:9 - cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot
30 27 0:31 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw,size=1638400k,mode=755
44 30 0:52 / /run/srdm/.op/op-1/pak rw,nosuid,nodev,relatime - tmpfs tmpfs rw,size=2097152k,mode=755
45 30 0:52 /root /run/srdm/soulmask/rel-1/pak ro,nosuid,nodev,relatime - tmpfs tmpfs rw,size=2097152k,mode=755
50 27 259:2 /volumes /var/lib/pterodactyl rw,relatime master:1 - ext4 /dev/nvme0n1p2 rw
51 27 0:60 / /mnt/private rw,relatime - tmpfs tmpfs rw
`

func parse(t *testing.T, body string) []Entry {
	t.Helper()
	e, err := Parse(body)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestParseReadsEveryField(t *testing.T) {
	entries := parse(t, sample)
	if len(entries) != 7 {
		t.Fatalf("parsed %d entries, want 7", len(entries))
	}

	cg := entries[1]
	if cg.ID != 25 || cg.Parent != 21 || cg.Major != 0 || cg.Minor != 24 {
		t.Errorf("ids/device parsed as %+v", cg)
	}
	if cg.MountPoint != "/sys/fs/cgroup" || cg.FSType != "cgroup2" {
		t.Errorf("mount point/fstype parsed as %q/%q", cg.MountPoint, cg.FSType)
	}
	if !cg.HasSuperOption("memory_recursiveprot") {
		t.Error("memory_recursiveprot not seen in the super options")
	}
	if !cg.HasOption("nodev") || cg.HasOption("ro") {
		t.Errorf("per-mount options parsed as %v", cg.Options)
	}
}

// The number of optional fields varies, which is why the format has a
// separator. Counting fields instead of splitting on it is the classic bug.
func TestParseHandlesAnyNumberOfOptionalFields(t *testing.T) {
	cases := map[string]string{
		"none":  "31 24 0:27 / /a rw - tmpfs tmpfs rw",
		"one":   "31 24 0:27 / /a rw shared:9 - tmpfs tmpfs rw",
		"three": "31 24 0:27 / /a rw shared:9 master:2 propagate_from:2 - tmpfs tmpfs rw",
	}
	for name, line := range cases {
		t.Run(name, func(t *testing.T) {
			e, err := ParseLine(line)
			if err != nil {
				t.Fatal(err)
			}
			if e.MountPoint != "/a" || e.FSType != "tmpfs" {
				t.Fatalf("parsed as %+v", e)
			}
		})
	}
}

func TestParseRejectsMalformedLines(t *testing.T) {
	cases := map[string]string{
		"no separator":  "31 24 0:27 / /a rw tmpfs tmpfs rw",
		"short pre":     "31 24 0:27 / /a - tmpfs tmpfs rw",
		"short post":    "31 24 0:27 / /a rw - tmpfs",
		"bad mount id":  "x 24 0:27 / /a rw - tmpfs tmpfs rw",
		"bad parent id": "31 x 0:27 / /a rw - tmpfs tmpfs rw",
		"bad device":    "31 24 0-27 / /a rw - tmpfs tmpfs rw",
		"bad major":     "31 24 x:27 / /a rw - tmpfs tmpfs rw",
		"bad minor":     "31 24 0:x / /a rw - tmpfs tmpfs rw",
	}
	for name, line := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseLine(line); err == nil {
				t.Fatal("a malformed line was accepted")
			}
		})
	}
	// A malformed line anywhere fails the whole parse: a mount table read
	// half-way is worse than one not read at all, because srdm would then
	// conclude a mount is absent when it simply was not parsed.
	if _, err := Parse("31 24 0:27 / /a rw - tmpfs tmpfs rw\ngarbage\n"); err == nil {
		t.Fatal("Parse accepted a body with a malformed line")
	}
}

func TestParseSkipsBlankLines(t *testing.T) {
	entries := parse(t, "\n31 24 0:27 / /a rw - tmpfs tmpfs rw\n\n")
	if len(entries) != 1 {
		t.Fatalf("parsed %d entries, want 1", len(entries))
	}
}

// Docker's default is private, which is the shape that caused the
// 2026-07-31 ghost-mount outage; rslave shows up as master:N.
func TestPropagation(t *testing.T) {
	cases := map[string]struct {
		line string
		want string
	}{
		"private (docker default)": {"31 24 0:27 / /a rw - tmpfs tmpfs rw", PropagationPrivate},
		"shared":                   {"31 24 0:27 / /a rw shared:9 - tmpfs tmpfs rw", PropagationShared},
		"slave":                    {"31 24 0:27 / /a rw master:1 - tmpfs tmpfs rw", PropagationSlave},
		"propagate_from":           {"31 24 0:27 / /a rw propagate_from:1 - tmpfs tmpfs rw", PropagationSlave},
		"unbindable":               {"31 24 0:27 / /a rw unbindable - tmpfs tmpfs rw", PropagationUnbindable},
		// rslave onto a shared peer group reports BOTH. What matters to srdm
		// is that it RECEIVES host events, so slave wins.
		"shared and slave": {"31 24 0:27 / /a rw shared:9 master:1 - tmpfs tmpfs rw", PropagationSlave},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			e, err := ParseLine(tc.line)
			if err != nil {
				t.Fatal(err)
			}
			if got := e.Propagation(); got != tc.want {
				t.Fatalf("Propagation() = %q, want %q", got, tc.want)
			}
		})
	}
}

// A read-only BIND of a writable tmpfs is exactly what publication creates.
// Only the per-mount flag distinguishes it; the superblock stays rw.
func TestReadOnlyIsThePerMountFlagNotTheFilesystems(t *testing.T) {
	entries := parse(t, sample)

	published, ok := At(entries, "/run/srdm/soulmask/rel-1/pak")
	if !ok {
		t.Fatal("the published bind is missing")
	}
	if !published.ReadOnly() {
		t.Error("the published bind does not report read-only")
	}
	if !published.HasSuperOption("rw") {
		t.Error("test fixture: the underlying tmpfs should still be rw, which is the point")
	}
	// A bind of a subdirectory records that subdirectory as its Root.
	if published.Root != "/root" {
		t.Errorf("bind Root = %q, want /root", published.Root)
	}

	op, ok := At(entries, "/run/srdm/.op/op-1/pak")
	if !ok {
		t.Fatal("the op tmpfs is missing")
	}
	if op.ReadOnly() {
		t.Error("the op tmpfs must stay writable until publication finishes")
	}
}

func TestUnderMatchesWholeComponents(t *testing.T) {
	entries := parse(t, sample+
		"60 30 0:70 / /run/srdmx/decoy rw - tmpfs tmpfs rw\n")

	got := Under(entries, "/run/srdm")
	var points []string
	for _, e := range got {
		points = append(points, e.MountPoint)
	}
	want := []string{"/run/srdm/.op/op-1/pak", "/run/srdm/soulmask/rel-1/pak"}
	if !reflect.DeepEqual(points, want) {
		t.Fatalf("Under = %v, want %v", points, want)
	}

	// A trailing slash must not change the answer.
	if len(Under(entries, "/run/srdm/")) != 2 {
		t.Error("a trailing slash changed the result")
	}
	// The prefix itself counts when it is a mount, and so does the decoy:
	// /run/srdmx is not under /run/srdm, but it IS under /run.
	if got := len(Under(entries, "/run")); got != 4 {
		t.Errorf("Under(/run) = %d, want 4 (itself, the two srdm mounts, and /run/srdmx/decoy)", got)
	}
}

// Mounts stack: a later mount over the same point shadows the earlier one,
// and the visible mount is the last.
func TestAtReturnsTheVisibleMount(t *testing.T) {
	entries := parse(t,
		"31 24 0:27 / /a rw - tmpfs first rw\n"+
			"32 24 0:28 / /a ro - tmpfs second rw\n")

	e, ok := At(entries, "/a")
	if !ok {
		t.Fatal("nothing found at /a")
	}
	if e.Source != "second" || !e.ReadOnly() {
		t.Fatalf("At returned the shadowed mount: %+v", e)
	}
	if _, ok := At(entries, "/nowhere"); ok {
		t.Error("At found a mount that is not there")
	}
}

func TestUnescape(t *testing.T) {
	cases := map[string]string{
		`/mnt/plain`:         "/mnt/plain",
		`/mnt/with\040space`: "/mnt/with space",
		`/mnt/with\011tab`:   "/mnt/with\ttab",
		`/mnt/back\134slash`: `/mnt/back\slash`,
		`/mnt/new\012line`:   "/mnt/new\nline",
		`/mnt/not\1escaped`:  `/mnt/not\1escaped`,
		`/mnt/trailing\`:     `/mnt/trailing\`,
	}
	for in, want := range cases {
		if got := Unescape(in); got != want {
			t.Errorf("Unescape(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestParseDecodesEscapedMountPoints(t *testing.T) {
	e, err := ParseLine(`31 24 0:27 / /mnt/with\040space rw - tmpfs tmpfs rw`)
	if err != nil {
		t.Fatal(err)
	}
	if e.MountPoint != "/mnt/with space" {
		t.Fatalf("MountPoint = %q", e.MountPoint)
	}
}

func TestReadFromAFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "mountinfo")
	if err := os.WriteFile(path, []byte(sample), 0o644); err != nil {
		t.Fatal(err)
	}
	entries, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 7 {
		t.Fatalf("Read parsed %d entries, want 7", len(entries))
	}
	if _, err := Read(filepath.Join(dir, "absent")); err == nil {
		t.Error("reading an absent mountinfo succeeded")
	}
}

// The real table must parse. A format this parser cannot read is a parser
// bug, and the only way to know is to read the live one.
func TestReadTheLiveMountTable(t *testing.T) {
	entries, err := Read("")
	if err != nil {
		t.Fatalf("parsing %s: %v", DefaultPath, err)
	}
	if len(entries) == 0 {
		t.Fatal("the live mount table parsed as empty")
	}
	if _, ok := At(entries, "/"); !ok {
		t.Error("the live mount table has no root mount")
	}
}

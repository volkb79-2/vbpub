package cgroupfs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fixture builds a fake cgroup tree, so every parser here is exercised
// without privilege and without real cgroups.
func fixture(t *testing.T, controlGroup string, files map[string]string) Reader {
	t.Helper()
	root := t.TempDir()
	dir := filepath.Join(root, filepath.FromSlash(strings.TrimPrefix(controlGroup, "/")))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return Reader{Root: root}
}

const cg = "/system.slice/srdm-hold-a1b2-pak.service"

func TestPathResolvesASystemdControlGroup(t *testing.T) {
	r := Reader{Root: "/sys/fs/cgroup"}
	got := r.Path("/system.slice/x.service")
	want := "/sys/fs/cgroup/system.slice/x.service"
	if got != want {
		t.Fatalf("Path = %q, want %q", got, want)
	}
	// systemd reports ControlGroup with a leading slash; joining it naively
	// would produce an absolute path and escape the root.
	if !strings.HasPrefix(got, "/sys/fs/cgroup/") {
		t.Fatalf("Path escaped the hierarchy root: %q", got)
	}
}

func TestExistsDistinguishesAReapedCgroup(t *testing.T) {
	r := fixture(t, cg, map[string]string{"memory.current": "123\n"})

	if !r.Exists(cg) {
		t.Error("a present cgroup was reported absent")
	}
	// The branch the hold-unit probe settles: a unit that is still active
	// but whose cgroup was reaped.
	if r.Exists("/system.slice/never-existed.service") {
		t.Error("an absent cgroup was reported present")
	}
}

func TestReadIntParsesValuesAndMax(t *testing.T) {
	r := fixture(t, cg, map[string]string{
		"memory.current":  "157286400\n",
		"memory.max":      "max\n",
		"memory.zswapmax": "0\n",
	})

	got, err := r.MemoryCurrent(cg)
	if err != nil {
		t.Fatal(err)
	}
	if got != 157286400 {
		t.Errorf("MemoryCurrent = %d", got)
	}

	// "max" is a legitimate value meaning no limit. Treating it as a parse
	// error would misreport an unconfigured knob as a broken one.
	if got, err := r.ReadInt(cg, "memory.max"); err != nil || got != Unlimited {
		t.Errorf("ReadInt(memory.max) = %d, %v; want Unlimited", got, err)
	}
	if got, err := r.ReadInt(cg, "memory.zswapmax"); err != nil || got != 0 {
		t.Errorf("ReadInt(memory.zswapmax) = %d, %v; want 0", got, err)
	}
}

func TestReadIntRejectsGarbage(t *testing.T) {
	r := fixture(t, cg, map[string]string{"memory.current": "lots\n"})
	_, err := r.MemoryCurrent(cg)
	if err == nil {
		t.Fatal("a non-numeric attribute parsed cleanly")
	}
	if !strings.Contains(err.Error(), "memory.current") {
		t.Errorf("the error does not name the attribute: %v", err)
	}
}

func TestReadIntOnAMissingAttribute(t *testing.T) {
	r := fixture(t, cg, nil)
	if _, err := r.MemoryCurrent(cg); err == nil {
		t.Fatal("reading an absent attribute succeeded")
	}
}

// shmem, not memory.current, is the number srdm's charging claims are
// about: tmpfs pages are anonymous-backed shmem, so it isolates generation
// content from slab, page tables and socket buffers.
func TestShmemIsolatesTmpfsPages(t *testing.T) {
	r := fixture(t, cg, map[string]string{
		"memory.stat": strings.Join([]string{
			"anon 4096",
			"file 8192",
			"kernel 65536",
			"shmem 157286400",
			"slab 32768",
		}, "\n") + "\n",
	})

	got, err := r.Shmem(cg)
	if err != nil {
		t.Fatal(err)
	}
	if got != 157286400 {
		t.Fatalf("Shmem = %d", got)
	}

	stat, err := r.MemoryStat(cg)
	if err != nil {
		t.Fatal(err)
	}
	if stat["anon"] != 4096 || stat["slab"] != 32768 {
		t.Errorf("MemoryStat lost fields: %v", stat)
	}
}

func TestShmemReportsAMissingField(t *testing.T) {
	r := fixture(t, cg, map[string]string{"memory.stat": "anon 4096\n"})
	_, err := r.Shmem(cg)
	if err == nil {
		t.Fatal("a memory.stat with no shmem field returned a value anyway")
	}
	if !strings.Contains(err.Error(), "shmem") {
		t.Errorf("the error does not say what was missing: %v", err)
	}
}

// A teardown leak shows up as dying descendants that climb and do not come
// back down, which is why doctor watches this and not only memory.current.
func TestDyingDescendants(t *testing.T) {
	r := fixture(t, cg, map[string]string{
		"cgroup.stat": "nr_descendants 3\nnr_dying_descendants 0\n",
	})

	got, err := r.DyingDescendants(cg)
	if err != nil {
		t.Fatal(err)
	}
	if got != 0 {
		t.Fatalf("DyingDescendants = %d, want 0", got)
	}

	leaky := fixture(t, cg, map[string]string{
		"cgroup.stat": "nr_descendants 3\nnr_dying_descendants 7\n",
	})
	if got, err := leaky.DyingDescendants(cg); err != nil || got != 7 {
		t.Fatalf("DyingDescendants = %d, %v; want 7", got, err)
	}
}

func TestDyingDescendantsReportsAMissingField(t *testing.T) {
	r := fixture(t, cg, map[string]string{"cgroup.stat": "nr_descendants 3\n"})
	if _, err := r.DyingDescendants(cg); err == nil {
		t.Fatal("a cgroup.stat with no nr_dying_descendants returned a value anyway")
	}
}

func TestKeyedParserRejectsMalformedLines(t *testing.T) {
	r := fixture(t, cg, map[string]string{"memory.stat": "anon 4096\nbroken\n"})
	if _, err := r.MemoryStat(cg); err == nil {
		t.Fatal("a malformed memory.stat line was accepted")
	}
}

func TestKeyedParserSkipsBlankLines(t *testing.T) {
	r := fixture(t, cg, map[string]string{"memory.stat": "\nanon 4096\n\nshmem 8192\n\n"})
	stat, err := r.MemoryStat(cg)
	if err != nil {
		t.Fatal(err)
	}
	if len(stat) != 2 {
		t.Fatalf("MemoryStat = %v, want 2 entries", stat)
	}
}

func TestZeroReaderUsesTheDefaultRoot(t *testing.T) {
	if got := (Reader{}).Path("/x"); got != DefaultRoot+"/x" {
		t.Fatalf("zero Reader resolved to %q", got)
	}
	if got := New().Path("/x"); got != DefaultRoot+"/x" {
		t.Fatalf("New() resolved to %q", got)
	}
}

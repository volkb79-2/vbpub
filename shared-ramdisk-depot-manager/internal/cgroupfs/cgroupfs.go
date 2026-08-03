// Package cgroupfs reads cgroup v2 attributes.
//
// srdm's central resource claim — that a generation's pages are charged to
// the hold unit that faulted them, and stay charged until the tmpfs is
// unmounted — is only ever true or false about numbers in this filesystem.
// Reading them is therefore a first-class concern, not test scaffolding.
package cgroupfs

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// DefaultRoot is where systemd mounts the unified hierarchy.
const DefaultRoot = "/sys/fs/cgroup"

// Unlimited is what a cgroup attribute reading "max" resolves to.
const Unlimited = int64(math.MaxInt64)

// Reader reads attributes beneath a cgroup v2 mount point. Root is
// injectable so every parser here is exercisable against a fixture tree
// with no privilege and no real cgroups.
type Reader struct {
	Root string
}

// New returns a Reader over the default root.
func New() Reader { return Reader{Root: DefaultRoot} }

func (r Reader) root() string {
	if r.Root == "" {
		return DefaultRoot
	}
	return r.Root
}

// Path resolves a systemd ControlGroup value (an absolute path relative to
// the hierarchy root, e.g. "/system.slice/srdm-hold-a1b2-pak.service") to a
// filesystem path.
func (r Reader) Path(controlGroup string) string {
	return filepath.Join(r.root(), filepath.FromSlash(strings.TrimPrefix(controlGroup, "/")))
}

// Exists reports whether the cgroup directory is present.
//
// This is the difference between "the unit is holding nothing" and "the
// unit's cgroup was reaped", which is exactly the branch the hold-unit
// probe has to settle.
func (r Reader) Exists(controlGroup string) bool {
	st, err := os.Stat(r.Path(controlGroup))
	return err == nil && st.IsDir()
}

// ReadInt reads a single-value cgroup attribute. "max" resolves to
// Unlimited rather than an error: it is a legitimate value meaning no
// limit, and callers that treat it as a parse failure misreport an
// unconfigured knob as a broken one.
func (r Reader) ReadInt(controlGroup, attr string) (int64, error) {
	body, err := os.ReadFile(filepath.Join(r.Path(controlGroup), attr))
	if err != nil {
		return 0, err
	}
	return parseInt(strings.TrimSpace(string(body)), attr)
}

func parseInt(s, attr string) (int64, error) {
	if s == "max" {
		return Unlimited, nil
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("cgroupfs: %s is %q, which is not a byte count", attr, s)
	}
	return n, nil
}

// MemoryCurrent is the cgroup's total charged memory, in bytes.
func (r Reader) MemoryCurrent(controlGroup string) (int64, error) {
	return r.ReadInt(controlGroup, "memory.current")
}

// MemoryStat parses memory.stat into its key/value pairs.
func (r Reader) MemoryStat(controlGroup string) (map[string]int64, error) {
	return r.readKeyed(controlGroup, "memory.stat")
}

// Shmem is the cgroup's tmpfs/shared-memory charge, in bytes.
//
// This, not memory.current, is the number srdm's charging claims are about.
// tmpfs pages are anonymous-backed shmem, so shmem isolates exactly the
// generation content from the kernel slab, page tables and socket buffers
// that memory.current also counts — a distinction that matters when the
// assertion is "≈ the class size".
func (r Reader) Shmem(controlGroup string) (int64, error) {
	stat, err := r.MemoryStat(controlGroup)
	if err != nil {
		return 0, err
	}
	v, ok := stat["shmem"]
	if !ok {
		return 0, fmt.Errorf("cgroupfs: memory.stat of %q has no shmem field", controlGroup)
	}
	return v, nil
}

// CgroupStat parses cgroup.stat.
func (r Reader) CgroupStat(controlGroup string) (map[string]int64, error) {
	return r.readKeyed(controlGroup, "cgroup.stat")
}

// DyingDescendants is nr_dying_descendants: cgroups that have been removed
// but whose pages are still charged somewhere below.
//
// A teardown that leaks shows up here as a number that climbs and does not
// come back down, which is why doctor watches it rather than only watching
// memory.current.
func (r Reader) DyingDescendants(controlGroup string) (int64, error) {
	stat, err := r.CgroupStat(controlGroup)
	if err != nil {
		return 0, err
	}
	v, ok := stat["nr_dying_descendants"]
	if !ok {
		return 0, fmt.Errorf("cgroupfs: cgroup.stat of %q has no nr_dying_descendants field", controlGroup)
	}
	return v, nil
}

// readKeyed parses a "<key> <value>" per line attribute.
func (r Reader) readKeyed(controlGroup, attr string) (map[string]int64, error) {
	f, err := os.Open(filepath.Join(r.Path(controlGroup), attr))
	if err != nil {
		return nil, err
	}
	defer f.Close()

	out := make(map[string]int64)
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		key, value, ok := strings.Cut(line, " ")
		if !ok {
			return nil, fmt.Errorf("cgroupfs: %s line %q is not a key/value pair", attr, line)
		}
		n, err := parseInt(strings.TrimSpace(value), attr+" "+key)
		if err != nil {
			return nil, err
		}
		out[key] = n
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

// Package mountinfo parses /proc/<pid>/mountinfo.
//
// srdm reasons about mounts more than about almost anything else: which op
// tmpfs exist, whether a published bind is really read-only, whether a
// consumer still holds a generation, and — from P06 — whether the Wings
// container's volume root propagates host-side mounts at all. A published
// record proves nothing about any of that; only the kernel's own view does.
package mountinfo

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// DefaultPath is this process's own mount table.
const DefaultPath = "/proc/self/mountinfo"

// Entry is one line of the table.
type Entry struct {
	ID     int
	Parent int
	Major  int
	Minor  int
	// Root is the subtree of the filesystem exposed at MountPoint. For a
	// bind of a subdirectory this is that subdirectory, which is how a bind
	// is told apart from a mount of the whole filesystem.
	Root       string
	MountPoint string
	// Options are per-mount: rw/ro, nosuid, nodev, noexec, relatime.
	Options []string
	// Optional carries the propagation tags — shared:N, master:N,
	// propagate_from:N, unbindable — and is variable in length, which is
	// exactly why the format has a " - " separator.
	Optional []string
	FSType   string
	Source   string
	// SuperOptions are filesystem-wide: size=, mode=, and for cgroup2 the
	// nsdelegate / memory_recursiveprot flags.
	SuperOptions []string
}

// Propagation kinds, as the kernel expresses them in the optional fields.
const (
	PropagationPrivate    = "private"
	PropagationShared     = "shared"
	PropagationSlave      = "slave"
	PropagationUnbindable = "unbindable"
)

// Propagation reports the mount's propagation type.
//
// A mount that is both slave and shared reports slave: that is the
// combination `rslave` produces on a peer group, and what matters to srdm
// is that it RECEIVES events from the host. The absence of any tag is
// private, which is Docker's default and the shape that caused the
// 2026-07-31 ghost-mount outage.
func (e Entry) Propagation() string {
	var shared bool
	for _, o := range e.Optional {
		switch {
		case strings.HasPrefix(o, "master:"), strings.HasPrefix(o, "propagate_from:"):
			return PropagationSlave
		case strings.HasPrefix(o, "shared:"):
			shared = true
		case o == "unbindable":
			return PropagationUnbindable
		}
	}
	if shared {
		return PropagationShared
	}
	return PropagationPrivate
}

// HasOption reports whether a per-mount option is set.
func (e Entry) HasOption(opt string) bool { return hasString(e.Options, opt) }

// HasSuperOption reports whether a filesystem-wide option is set.
func (e Entry) HasSuperOption(opt string) bool { return hasString(e.SuperOptions, opt) }

// ReadOnly reports whether the mount itself is read-only.
//
// This is the per-mount flag, not the filesystem's: a read-only BIND of a
// writable tmpfs is exactly what publication creates, and only this flag
// distinguishes it.
func (e Entry) ReadOnly() bool { return e.HasOption("ro") }

func hasString(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

// Parse reads a whole mountinfo body.
func Parse(body string) ([]Entry, error) {
	var out []Entry
	for i, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		e, err := ParseLine(line)
		if err != nil {
			return nil, fmt.Errorf("mountinfo line %d: %w", i+1, err)
		}
		out = append(out, e)
	}
	return out, nil
}

// Read parses a mountinfo file. The empty path means this process's own.
func Read(path string) ([]Entry, error) {
	if path == "" {
		path = DefaultPath
	}
	body, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return Parse(string(body))
}

// ParseLine parses one entry.
//
// The format is positional up to a " - " separator, after which come the
// filesystem type, the source and the super options. The optional fields
// before the separator are variable in number — which is why this splits on
// the separator rather than counting.
func ParseLine(line string) (Entry, error) {
	pre, post, found := strings.Cut(line, " - ")
	if !found {
		return Entry{}, fmt.Errorf("no %q separator", " - ")
	}
	pf := strings.Fields(pre)
	if len(pf) < 6 {
		return Entry{}, fmt.Errorf("want at least 6 fields before the separator, got %d", len(pf))
	}
	sf := strings.Fields(post)
	if len(sf) < 3 {
		return Entry{}, fmt.Errorf("want 3 fields after the separator, got %d", len(sf))
	}

	e := Entry{
		Root:         Unescape(pf[3]),
		MountPoint:   Unescape(pf[4]),
		Options:      strings.Split(pf[5], ","),
		Optional:     pf[6:],
		FSType:       Unescape(sf[0]),
		Source:       Unescape(sf[1]),
		SuperOptions: strings.Split(sf[2], ","),
	}
	var err error
	if e.ID, err = strconv.Atoi(pf[0]); err != nil {
		return Entry{}, fmt.Errorf("bad mount id %q", pf[0])
	}
	if e.Parent, err = strconv.Atoi(pf[1]); err != nil {
		return Entry{}, fmt.Errorf("bad parent id %q", pf[1])
	}
	majStr, minStr, ok := strings.Cut(pf[2], ":")
	if !ok {
		return Entry{}, fmt.Errorf("bad device %q", pf[2])
	}
	if e.Major, err = strconv.Atoi(majStr); err != nil {
		return Entry{}, fmt.Errorf("bad major %q", majStr)
	}
	if e.Minor, err = strconv.Atoi(minStr); err != nil {
		return Entry{}, fmt.Errorf("bad minor %q", minStr)
	}
	return e, nil
}

// At returns the LAST entry mounted at path, which is the one currently
// visible: mounts stack, and a later mount over the same point shadows the
// earlier one.
func At(entries []Entry, path string) (Entry, bool) {
	var found Entry
	var ok bool
	for _, e := range entries {
		if e.MountPoint == path {
			found, ok = e, true
		}
	}
	return found, ok
}

// Under returns every entry at or beneath prefix, matching on whole path
// components so "/run/srdm" does not also select "/run/srdmx".
func Under(entries []Entry, prefix string) []Entry {
	prefix = strings.TrimSuffix(prefix, "/")
	var out []Entry
	for _, e := range entries {
		if e.MountPoint == prefix || strings.HasPrefix(e.MountPoint, prefix+"/") {
			out = append(out, e)
		}
	}
	return out
}

// Unescape decodes the \OOO escapes the kernel uses for space, tab, newline
// and backslash in paths.
func Unescape(s string) string {
	if !strings.Contains(s, `\`) {
		return s
	}
	var sb strings.Builder
	for i := 0; i < len(s); {
		if s[i] == '\\' && i+3 < len(s) && isOctal(s[i+1]) && isOctal(s[i+2]) && isOctal(s[i+3]) {
			v := (int(s[i+1]-'0') << 6) | (int(s[i+2]-'0') << 3) | int(s[i+3]-'0')
			sb.WriteByte(byte(v))
			i += 4
			continue
		}
		sb.WriteByte(s[i])
		i++
	}
	return sb.String()
}

func isOctal(b byte) bool { return b >= '0' && b <= '7' }

package doctor

import "strings"

// mountEntry is the part of a /proc/<pid>/mountinfo line srdm cares about.
type mountEntry struct {
	mountPoint     string
	fsType         string
	superOptions   []string
	optionalFields []string
}

// findCgroup2Mount returns the cgroup2 mount described by a mountinfo body.
//
// The format is positional up to a " - " separator, after which come the
// filesystem type, the source, and the super options. The optional fields
// before the separator are variable in number — which is exactly why the
// separator exists and why this parser splits on it rather than counting.
func findCgroup2Mount(body string) (mountEntry, bool) {
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		e, ok := parseMountInfoLine(line)
		if !ok || e.fsType != "cgroup2" {
			continue
		}
		return e, true
	}
	return mountEntry{}, false
}

func parseMountInfoLine(line string) (mountEntry, bool) {
	pre, post, found := strings.Cut(line, " - ")
	if !found {
		return mountEntry{}, false
	}
	preFields := strings.Fields(pre)
	// mount id, parent id, major:minor, root, mount point, mount options,
	// then zero or more optional fields.
	if len(preFields) < 6 {
		return mountEntry{}, false
	}
	postFields := strings.Fields(post)
	// filesystem type, mount source, super options.
	if len(postFields) < 3 {
		return mountEntry{}, false
	}
	return mountEntry{
		mountPoint:     unescapeOctal(preFields[4]),
		optionalFields: preFields[6:],
		fsType:         postFields[0],
		superOptions:   strings.Split(postFields[2], ","),
	}, true
}

// unescapeOctal decodes the \OOO escapes the kernel uses for space, tab,
// newline and backslash in mountinfo paths.
func unescapeOctal(s string) string {
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

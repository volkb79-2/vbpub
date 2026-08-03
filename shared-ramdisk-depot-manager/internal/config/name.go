package config

import "fmt"

// ValidName reports whether s is safe to use as a single path component in
// the store layout (a release id, profile id, channel or operation id).
//
// Release ids, profile ids and channel names all arrive from operator input
// and are concatenated into store paths. Rejecting separators and the dot
// entries here means no caller has to remember to sanitize.
func ValidName(kind, s string) error {
	if s == "" {
		return fmt.Errorf("%s is empty", kind)
	}
	if s == "." || s == ".." {
		return fmt.Errorf("%s %q is a path traversal", kind, s)
	}
	if len(s) > 128 {
		return fmt.Errorf("%s %q is longer than 128 bytes", kind, s)
	}
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z',
			r >= 'A' && r <= 'Z',
			r >= '0' && r <= '9',
			r == '-', r == '_', r == '.':
		default:
			return fmt.Errorf("%s %q contains %q; only [A-Za-z0-9._-] are allowed", kind, s, string(r))
		}
	}
	return nil
}

package profile

import (
	"fmt"
	"strings"
)

// UnclassifiedError reports a path no rule in the profile matches.
//
// Oracle O3: an unclassified new path blocks promotion, and the error names
// both the path and the profile. The alternative — landing it in a default
// class — is how content silently acquires a memory policy nobody chose.
type UnclassifiedError struct {
	Path    string
	Profile string
}

func (e *UnclassifiedError) Error() string {
	return fmt.Sprintf("path %q is not classified by any rule in profile %q; "+
		"add it to a class (or an excluded class) before promoting", e.Path, e.Profile)
}

// Classify resolves a release-root-relative path to its class.
//
// Matching is longest-prefix on whole path components, so a rule for
// "WS/Content/Paks" beats one for "WS", and neither matches "WS/ContentX".
func (p *Profile) Classify(rel string) (*Class, error) {
	if p.index == nil {
		if err := p.Validate(); err != nil {
			return nil, err
		}
	}
	cleaned, err := normalizePrefix(rel)
	if err != nil {
		return nil, fmt.Errorf("profile %q: %w", p.ID, err)
	}
	for _, e := range p.index {
		if matchesPrefix(cleaned, e.prefix) {
			return e.class, nil
		}
	}
	// A directory that exists only because a declared class path lives
	// beneath it is structure, not content. Requiring profiles to declare
	// every ancestor of every rule would be pure ceremony, and getting it
	// wrong would refuse a perfectly ordinary tree.
	//
	// Note this is an *ancestor* rule, not a "starts with" rule: a sibling
	// file such as WS/notes.txt is still unclassified and still blocks
	// promotion, which is the behaviour O3 is about.
	for _, e := range p.index {
		if e.prefix != "" && strings.HasPrefix(e.prefix, cleaned+"/") {
			return StructureClass, nil
		}
	}
	return nil, &UnclassifiedError{Path: cleaned, Profile: p.ID}
}

// matchesPrefix reports whether rel is prefix itself or lies beneath it.
// The empty prefix is the release root and matches everything.
func matchesPrefix(rel, prefix string) bool {
	if prefix == "" {
		return true
	}
	if rel == prefix {
		return true
	}
	return strings.HasPrefix(rel, prefix+"/")
}

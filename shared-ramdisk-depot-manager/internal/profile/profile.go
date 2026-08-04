// Package profile describes what a release contains: how each path is
// classified, which probes must pass before promotion, and the per-class
// memory policy the publication layer will later apply.
//
// The design's on-disk profile format is YAML (master plan, Layer 3). P01
// ships a JSON loader instead — see decision D-005 in nyxloom-trove. Both
// formats decode into the types below, and every document carries
// SchemaVersion, so a YAML front-end is purely additive.
package profile

import (
	"encoding/json"
	"fmt"
	"os"
	"path"
	"regexp"
	"sort"
	"strings"
	"time"

	"srdm/internal/journal"
)

// SchemaVersion is the profile document version srdm writes and accepts.
// Versioned from day one so a v1 deployment's state is readable later.
const SchemaVersion = 1

// Kind distinguishes content srdm publishes from content it must never
// touch.
type Kind string

const (
	// KindManaged is shared, immutable, published content.
	KindManaged Kind = "managed"
	// KindExcluded is per-instance mutable state. It is classified — so it
	// does not block promotion — but never published and never shared.
	//
	// The master plan's absolute state rule: WS/Saved/** is never shared,
	// never used as content input, never modified by a transaction.
	KindExcluded Kind = "excluded"
	// KindStructure is the implicit class of a directory that exists only
	// because a declared class path lives beneath it. It is never declared
	// in a profile and carries no policy.
	KindStructure Kind = "structure"
)

// StructureClass is the single implicit class. Publication ignores it: a
// structural directory holds no content of its own and is never bound.
var StructureClass = &Class{Name: "structure", Kind: KindStructure}

// Class is one publication class: a set of path prefixes that share a memory
// policy and a tmpfs.
type Class struct {
	Name string `json:"name"`
	Kind Kind   `json:"kind"`
	// Paths are slash-separated prefixes relative to the release root.
	Paths []string `json:"paths"`
	// MemoryMin is the class floor, in bytes, that the hold service carries.
	// doctor sums the managed classes' floors and checks the parent slice's
	// MemoryMin covers them; publication sums them again for the generation
	// slice, because a floor is capped by every ancestor's floor.
	MemoryMin int64 `json:"memory_min,omitempty"`
	// ZSwapMax is the MemoryZSwapMax the hold service carries. Zero means
	// pages bypass zswap entirely — correct for incompressible pak data
	// (measured 1.006x on the case-study node). A pointer because zero is a
	// meaningful setting and not the same as leaving the knob alone.
	ZSwapMax *int64 `json:"zswap_max,omitempty"`
	// ZSwapWriteback is the MemoryZSwapWriteback the hold service carries:
	// whether this class's pages may be written back to disk. Also a
	// pointer — unset is systemd's default, which is not "no".
	ZSwapWriteback *bool `json:"zswap_writeback,omitempty"`
	// NoExec mounts this class's tmpfs noexec. Data-only classes should.
	NoExec bool `json:"noexec,omitempty"`
}

// ProbeKind selects what a probe asserts.
type ProbeKind string

const (
	// ProbeExists asserts the path is present in the transaction.
	ProbeExists ProbeKind = "exists"
	// ProbeAbsent asserts the path is not present.
	ProbeAbsent ProbeKind = "absent"
	// ProbeNonEmpty asserts the path is a regular file with a non-zero size.
	ProbeNonEmpty ProbeKind = "non-empty"
)

// Probe is a promotion precondition evaluated against the transaction tree.
type Probe struct {
	Name string    `json:"name"`
	Kind ProbeKind `json:"kind"`
	Path string    `json:"path"`
}

// Profile is a complete content description.
type Profile struct {
	SchemaVersion int     `json:"schema_version"`
	ID            string  `json:"id"`
	Classes       []Class `json:"classes"`
	Probes        []Probe `json:"probes"`
	// Credentials carries acquisition secrets (a SteamCMD login, a token).
	// It is never serialized into the journal: Secrets feeds the journal's
	// scrubber, and no journal record ever embeds a Profile.
	Credentials map[string]string `json:"credentials,omitempty"`
	// Readiness is how `srdm update` knows this profile's MAIN server is
	// actually ready — beyond "the Panel/Wings reports it running" — before
	// any slave is started against it. It lives here rather than in node
	// config because the ready line is a property of the game BUILD, which
	// travels with the content a profile describes, not with the node.
	// Empty means unconfigured: update trusts the power status alone
	// (D-031).
	Readiness Readiness `json:"readiness,omitempty"`

	// index is the resolved longest-prefix lookup, built by Validate.
	index []indexEntry
}

// ReadinessKindLogMatch is the one member of today's readiness vocabulary.
const ReadinessKindLogMatch = "log-match"

// Readiness describes a server's ready-beyond-running signal.
//
// Format matches the convention srdm's own operators already use for
// log-derived events (the v1 cgroup patch stack's WINGS_CG_PHASE_EVENTS): a
// plain Pattern is a substring match, and a "regex:" prefix makes it a
// regular expression — adopted rather than invented, because it is already
// what egg authors on this project know.
type Readiness struct {
	// Kind selects how readiness is confirmed. Empty means unconfigured;
	// ReadinessKindLogMatch is the only other value today.
	Kind string `json:"kind,omitempty"`
	// Pattern matches a line in the server's own log. "regex:<expr>" is a
	// regular expression; anything else is a plain substring match.
	Pattern string `json:"pattern,omitempty"`
	// Timeout is a Go duration string (e.g. "600s"). Empty means
	// DefaultReadinessTimeout.
	Timeout string `json:"timeout,omitempty"`
}

// DefaultReadinessTimeout applies when Timeout is empty.
const DefaultReadinessTimeout = 5 * time.Minute

// Configured reports whether a readiness check was asked for at all.
func (r Readiness) Configured() bool { return r.Kind != "" }

// EffectiveTimeout parses Timeout, or returns DefaultReadinessTimeout when
// it is empty. Call only after Validate has confirmed Timeout parses.
func (r Readiness) EffectiveTimeout() time.Duration {
	if r.Timeout == "" {
		return DefaultReadinessTimeout
	}
	d, err := time.ParseDuration(r.Timeout)
	if err != nil || d <= 0 {
		return DefaultReadinessTimeout
	}
	return d
}

// validate checks a Readiness is usable. Unconfigured always validates.
func (r Readiness) validate(profileID string) error {
	if !r.Configured() {
		return nil
	}
	if r.Kind != ReadinessKindLogMatch {
		return fmt.Errorf("profile %q: readiness.kind %q is not %q, the only kind this "+
			"build knows", profileID, r.Kind, ReadinessKindLogMatch)
	}
	if r.Pattern == "" {
		return fmt.Errorf("profile %q: readiness.kind is %q but readiness.pattern is empty",
			profileID, ReadinessKindLogMatch)
	}
	if expr, ok := strings.CutPrefix(r.Pattern, "regex:"); ok {
		if _, err := regexp.Compile(expr); err != nil {
			return fmt.Errorf("profile %q: readiness.pattern %q does not compile: %w",
				profileID, r.Pattern, err)
		}
	}
	if r.Timeout != "" {
		if d, err := time.ParseDuration(r.Timeout); err != nil || d <= 0 {
			return fmt.Errorf("profile %q: readiness.timeout %q is not a positive duration "+
				"(want a Go duration string like \"600s\")", profileID, r.Timeout)
		}
	}
	return nil
}

type indexEntry struct {
	prefix string
	class  *Class
}

// Load reads a profile document from disk.
func Load(name string) (*Profile, error) {
	b, err := os.ReadFile(name)
	if err != nil {
		return nil, fmt.Errorf("profile: read %s: %w", name, err)
	}
	return Parse(b)
}

// Parse decodes and validates a profile document.
func Parse(b []byte) (*Profile, error) {
	var p Profile
	dec := json.NewDecoder(strings.NewReader(string(b)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil {
		return nil, fmt.Errorf("profile: decode: %w", err)
	}
	if err := p.Validate(); err != nil {
		return nil, err
	}
	return &p, nil
}

// Validate checks the profile is well-formed and builds its lookup index.
// It is idempotent, so callers constructing a Profile in Go may call it
// directly.
func (p *Profile) Validate() error {
	if p.SchemaVersion != SchemaVersion {
		return fmt.Errorf("profile %q: schema_version %d is not supported (want %d)",
			p.ID, p.SchemaVersion, SchemaVersion)
	}
	if p.ID == "" {
		return fmt.Errorf("profile: id is empty")
	}
	if len(p.Classes) == 0 {
		return fmt.Errorf("profile %q: no classes declared", p.ID)
	}

	seenClass := make(map[string]bool, len(p.Classes))
	seenPrefix := make(map[string]string)
	index := make([]indexEntry, 0, len(p.Classes))

	for i := range p.Classes {
		c := &p.Classes[i]
		if c.Name == "" {
			return fmt.Errorf("profile %q: class %d has no name", p.ID, i)
		}
		if seenClass[c.Name] {
			return fmt.Errorf("profile %q: duplicate class %q", p.ID, c.Name)
		}
		if c.Name == StructureClass.Name {
			return fmt.Errorf("profile %q: %q is the implicit class of ancestor "+
				"directories and cannot be declared", p.ID, StructureClass.Name)
		}
		seenClass[c.Name] = true
		switch c.Kind {
		case KindManaged, KindExcluded:
		default:
			return fmt.Errorf("profile %q: class %q has unknown kind %q", p.ID, c.Name, c.Kind)
		}
		if len(c.Paths) == 0 {
			return fmt.Errorf("profile %q: class %q declares no paths", p.ID, c.Name)
		}
		for _, raw := range c.Paths {
			prefix, err := normalizePrefix(raw)
			if err != nil {
				return fmt.Errorf("profile %q: class %q: %w", p.ID, c.Name, err)
			}
			if owner, dup := seenPrefix[prefix]; dup {
				return fmt.Errorf("profile %q: path %q is claimed by both class %q and class %q",
					p.ID, raw, owner, c.Name)
			}
			seenPrefix[prefix] = c.Name
			index = append(index, indexEntry{prefix: prefix, class: c})
		}
	}

	for i, pr := range p.Probes {
		if pr.Name == "" {
			return fmt.Errorf("profile %q: probe %d has no name", p.ID, i)
		}
		switch pr.Kind {
		case ProbeExists, ProbeAbsent, ProbeNonEmpty:
		default:
			return fmt.Errorf("profile %q: probe %q has unknown kind %q", p.ID, pr.Name, pr.Kind)
		}
		if _, err := normalizePrefix(pr.Path); err != nil {
			return fmt.Errorf("profile %q: probe %q: %w", p.ID, pr.Name, err)
		}
	}

	// A credential shorter than the journal can substring-match is not
	// scrubbable, so it would leak into every record that quotes it. Refuse
	// the profile rather than accept an unenforceable "no credentials" rule.
	credKeys := make([]string, 0, len(p.Credentials))
	for k := range p.Credentials {
		credKeys = append(credKeys, k)
	}
	sort.Strings(credKeys)
	for _, k := range credKeys {
		if v := p.Credentials[k]; v != "" && len(v) < journal.MinSecretLength {
			return fmt.Errorf("profile %q: credential %q is %d bytes; the journal cannot "+
				"scrub anything shorter than %d, so it would leak",
				p.ID, k, len(v), journal.MinSecretLength)
		}
	}

	if err := p.Readiness.validate(p.ID); err != nil {
		return err
	}

	// Longest prefix wins, so a specific rule can carve a subtree out of a
	// broader one (WS/Saved excluded beneath a managed WS).
	sort.SliceStable(index, func(i, j int) bool {
		return len(index[i].prefix) > len(index[j].prefix)
	})
	p.index = index
	return nil
}

// normalizePrefix turns a declared path into a clean, relative, slash-form
// prefix. The empty prefix ("." after cleaning) is the catch-all root.
func normalizePrefix(raw string) (string, error) {
	if raw == "" {
		return "", fmt.Errorf("path is empty")
	}
	if strings.HasPrefix(raw, "/") {
		return "", fmt.Errorf("path %q must be relative to the release root", raw)
	}
	cleaned := path.Clean(raw)
	if cleaned == ".." || strings.HasPrefix(cleaned, "../") {
		return "", fmt.Errorf("path %q escapes the release root", raw)
	}
	if cleaned == "." {
		return "", nil
	}
	return cleaned, nil
}

// Secrets returns every credential value, for the journal's scrubber.
// Order is stable so callers get deterministic behaviour.
func (p *Profile) Secrets() []string {
	if len(p.Credentials) == 0 {
		return nil
	}
	keys := make([]string, 0, len(p.Credentials))
	for k := range p.Credentials {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		if v := p.Credentials[k]; v != "" {
			out = append(out, v)
		}
	}
	return out
}

// ManagedFloorSum is the sum of every managed class's MemoryMin, in bytes.
// It is the number the parent slice's MemoryMin must cover.
func (p *Profile) ManagedFloorSum() int64 {
	var sum int64
	for _, c := range p.Classes {
		if c.Kind == KindManaged {
			sum += c.MemoryMin
		}
	}
	return sum
}

package expose

import (
	"context"
	"errors"
	"fmt"
	"path"
	"sort"
	"strings"

	"srdm/internal/profile"
	"srdm/internal/publish"
)

// Access is how a consumer may use an exposed generation.
type Access string

const (
	// AccessRO is the default and the safe mode: writes fail EROFS
	// immediately rather than corrupting a generation half-way.
	AccessRO Access = "ro"
	// AccessRW permits writing through to the generation's tmpfs, and is
	// allowed only with exactly one consumer. With two, a write is the
	// 2026-07-29 incident by construction: the peer holds the deleted old
	// .pak open, the tmpfs exhausts mid-write, and the generation ends with
	// a new .sig and no .pak — survivable for the process that already
	// mmap'd the old inode, fatal for the next start.
	AccessRW Access = "rw"
)

func (a Access) valid() bool { return a == AccessRO || a == AccessRW }

// Request is one server's exposure of one published generation.
type Request struct {
	// ServerID is the Wings server uuid, which is also its volume directory.
	ServerID string
	// Access defaults to AccessRO when empty.
	Access Access
	// OperationID names the operation in the journal.
	OperationID string
}

// Binding is one mount the driver will make: a declared class path, from the
// published generation into the server's volume.
type Binding struct {
	Class string
	// Path is the declared class path, relative to the release root. It is
	// the same string on both sides, which is the property oracle 19 checks:
	// managed content appears at exactly the declared class paths and
	// nowhere else.
	Path string
	// Source is inside the published generation.
	Source string
	// Target is inside the server's volume.
	Target string
	// ReadOnly is the per-mount flag, which is what actually produces EROFS.
	ReadOnly bool
}

// Driver exposes a published generation to one consumer.
//
// The interface is the one fork in srdm's pipeline. Everything upstream —
// store, manifests, publication, hold units, teardown safety — is shared, so
// this is an interface with two implementations and never two products.
type Driver interface {
	// Name identifies the driver in records and journal entries.
	Name() string
	// Plan returns what Expose would mount, without mounting it. It exists
	// so a refusal can name the exact paths, and so the bounded-waiver
	// oracle can assert the plan rather than the aftermath.
	Plan(rec *publish.Record, prof *profile.Profile, req Request) ([]Binding, error)
	// Expose makes the generation visible to the consumer, or refuses.
	Expose(ctx context.Context, rec *publish.Record, prof *profile.Profile, req Request) error
	// Unexpose removes it again.
	Unexpose(ctx context.Context, rec *publish.Record, prof *profile.Profile, req Request) error
}

// RefusalError is the driver declining by contract: a precondition srdm
// cannot repair for the operator.
//
// Every one of these is a property of the deployment rather than of srdm,
// and every one of them, unrefused, surfaces later as something much worse —
// a server that will not start, a mount nobody can see, or a generation
// corrupted by a second writer.
type RefusalError struct {
	// Precondition names which one, so an operator can match it to the docs.
	Precondition string
	// Detail is what srdm actually found.
	Detail string
	// Fix is what to do about it. Never empty: a refusal with no remedy is
	// an outage with extra steps.
	Fix string
}

func (e *RefusalError) Error() string {
	return fmt.Sprintf("expose: refused (%s): %s; fix: %s", e.Precondition, e.Detail, e.Fix)
}

// IsRefusal reports whether err is the driver declining by contract.
func IsRefusal(err error) bool {
	var r *RefusalError
	return errors.As(err, &r)
}

// Precondition identifiers, as the master plan numbers them.
const (
	PreconditionPropagation = "propagation"
	PreconditionChownWalk   = "chown-walk"
	PreconditionConsumers   = "consumers-stopped"
	PreconditionSingleWrite = "rw-single-consumer"
	PreconditionDirty       = "generation-dirty"
	// PreconditionBoundedWaiver is the invariant-14 waiver staying inside
	// its declared bounds: managed content at exactly the declared class
	// paths, and per-instance state neither bound nor shadowed.
	PreconditionBoundedWaiver = "bounded-waiver"
)

// plan computes the bindings for a request, shared by every driver.
//
// One binding per DECLARED class path, not one per class. A class is a set
// of path prefixes that share a memory policy — `code` is Engine plus
// WS/Binaries plus a dozen more — and each of those has to land at its own
// place in the volume. Binding the class root instead would put content
// somewhere the game does not look, and would shadow everything else under
// that ancestor.
func plan(rec *publish.Record, prof *profile.Profile, req Request, volume string) ([]Binding, error) {
	if req.ServerID == "" {
		return nil, errors.New("expose: a request needs a server id")
	}
	access := req.Access
	if access == "" {
		access = AccessRO
	}
	if !access.valid() {
		return nil, fmt.Errorf("expose: unknown access %q (want %q or %q)",
			req.Access, AccessRO, AccessRW)
	}

	managed := make(map[string]publish.ClassRecord, len(rec.Classes))
	for _, c := range rec.Classes {
		managed[c.Name] = c
	}

	var out []Binding
	for _, class := range prof.Classes {
		// Excluded classes are per-instance state. The absolute state rule
		// is that WS/Saved is never shared, never used as content input and
		// never modified by a transaction — so it is never bound, and
		// nothing is ever bound OVER it either, which the caller checks.
		if class.Kind != profile.KindManaged {
			continue
		}
		cr, ok := managed[class.Name]
		if !ok {
			return nil, fmt.Errorf("expose: the published record has no class %q, so the "+
				"profile and the generation disagree about what was published", class.Name)
		}
		for _, p := range class.Paths {
			clean := path.Clean(strings.TrimPrefix(p, "/"))
			out = append(out, Binding{
				Class:    class.Name,
				Path:     clean,
				Source:   path.Join(sourceBase(cr, access), clean),
				Target:   path.Join(volume, clean),
				ReadOnly: access == AccessRO,
			})
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("expose: profile %q declares no managed class paths", prof.ID)
	}
	// Deepest last, so a nested path is mounted after the ancestor it sits
	// inside — mounting the other way round hides it.
	sort.Slice(out, func(i, j int) bool { return out[i].Target < out[j].Target })
	return out, nil
}

// sourceBase is the side of the generation an exposure binds.
//
// The two access modes bind DIFFERENT mount points of the same superblock,
// and that is forced rather than chosen. Publication's exposure is a
// read-only bind, and a bind inherits its source's per-mount flags — so
// binding it and then remounting rw would either fail or, worse, quietly
// hand back something still read-only. Measured: an rw exposure built on the
// published path gives EROFS on the first write.
//
// So rw binds the operation tmpfs's own content root, which is the writable
// mount of the same pages. Nothing else changes: it is the same superblock,
// the same hold unit, the same charge.
//
// The content's MODES are still sealed a-w by publication, so who may
// actually write through an rw exposure is a separate question from whether
// the mount permits it — see D-020. Root can; a game container running as an
// unprivileged uid cannot until that is decided.
func sourceBase(cr publish.ClassRecord, access Access) string {
	if access == AccessRW {
		return path.Join(cr.OpMount, "root")
	}
	return cr.ExposePath
}

// excludedPaths is every path the profile says is per-instance state.
func excludedPaths(prof *profile.Profile) []string {
	var out []string
	for _, class := range prof.Classes {
		if class.Kind == profile.KindManaged {
			continue
		}
		for _, p := range class.Paths {
			out = append(out, path.Clean(strings.TrimPrefix(p, "/")))
		}
	}
	sort.Strings(out)
	return out
}

// shadows reports whether binding at target would cover an excluded path.
//
// Both directions matter and only one is obvious. Binding AT `WS/Saved` is
// the obvious mistake. Binding at `WS` — an ancestor — is the dangerous one:
// the mount hides everything beneath it, so the server's own saves become
// invisible rather than overwritten, and the damage is only discovered when
// somebody looks for a world that is still on disk underneath.
func shadows(target string, excluded []string) (string, bool) {
	target = path.Clean(target)
	for _, ex := range excluded {
		if target == ex || strings.HasPrefix(ex, target+"/") || strings.HasPrefix(target, ex+"/") {
			return ex, true
		}
	}
	return "", false
}

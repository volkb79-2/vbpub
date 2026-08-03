// Package assign records what an operator asked for: which release a
// profile's active generation is published from, and which servers consume
// it.
//
// This is the first durable statement of INTENT srdm keeps, and it is
// deliberately not the thing D-018 refused to build.
//
// D-018 refused a consumer REGISTRY — a table of who is holding what. That
// question belongs to the kernel: a consumer's bind is at a path srdm never
// chose, the pages are held by a mount rather than by a record, and a table
// srdm maintained itself could only ever be a second opinion about a fact
// something else owns. `internal/consumer` answers it fresh at every ask.
//
// An assignment is the opposite kind of fact. Nothing else in the system
// knows it, and nothing can derive it: a mount table says a server HAS a
// generation, never that it SHOULD. Without it there is nothing for a boot
// to restore, nothing for `activate` to re-point, and nothing for `gc` to
// refuse to collect — three questions that are all "what did somebody ask
// for", and none of which the kernel has an opinion about.
//
// The pair is the whole design: intent is recorded, reality is measured, and
// reconciliation is the comparison. Recording reality, or measuring intent,
// is what makes systems that disagree with themselves.
package assign

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/config"
	"srdm/internal/fsx"
)

// SchemaVersion is the assignment document version.
const SchemaVersion = 1

// Server is one consumer of a profile's active generation.
type Server struct {
	// ID is the Wings server uuid, which is also its volume directory.
	ID string `json:"id"`
	// Access is "ro" or "rw". Held as a string rather than an expose.Access
	// so the durable format does not depend on the exposure layer's types —
	// a v2 provider driver reads the same documents.
	Access string `json:"access"`
}

// Assignment is one profile's declared state.
type Assignment struct {
	SchemaVersion int    `json:"schema_version"`
	Profile       string `json:"profile"`
	// Release is the release the active generation is published from. Empty
	// means the profile has nothing assigned — which is a real state, not a
	// missing document: a profile whose generation has been torn down still
	// has its servers listed and still knows what it was on.
	Release string `json:"release,omitempty"`
	// Previous is what Release was before the last activation, and it is the
	// whole of rollback's memory.
	//
	// One step deep on purpose. A channel history would be a second, parallel
	// account of the same sequence the journal already holds in full, and the
	// operation rollback actually has to be able to perform is "undo the
	// activation I just did". Anything further back is an activation by
	// release id, which is the same command with an argument.
	Previous string `json:"previous,omitempty"`
	// Servers are the consumers, ordered by id so the document is stable.
	Servers []Server `json:"servers,omitempty"`
}

// Store reads and writes assignment documents.
type Store struct {
	cfg config.Config
}

// New returns a Store over the configured state directory.
func New(cfg config.Config) (*Store, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(cfg.AssignmentsDir(), 0o755); err != nil {
		return nil, fmt.Errorf("assign: create %s: %w", cfg.AssignmentsDir(), err)
	}
	return &Store{cfg: cfg}, nil
}

// Load reads a profile's assignment, returning an empty one when the profile
// has never been assigned.
//
// A missing document is not an error, because "this profile has nothing
// assigned" is the state every profile starts in and the one `gc` and the
// boot path both have to handle anyway. Making it an error would mean every
// caller writing the same two lines to turn it back into a value.
func (s *Store) Load(profileID string) (*Assignment, error) {
	if err := config.ValidName("profile id", profileID); err != nil {
		return nil, fmt.Errorf("assign: %w", err)
	}
	body, err := os.ReadFile(s.path(profileID))
	if err != nil {
		if os.IsNotExist(err) {
			return &Assignment{SchemaVersion: SchemaVersion, Profile: profileID}, nil
		}
		return nil, err
	}
	var a Assignment
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&a); err != nil {
		return nil, fmt.Errorf("assign: decode %s: %w", s.path(profileID), err)
	}
	if a.SchemaVersion != SchemaVersion {
		return nil, fmt.Errorf("assign: %s has schema_version %d (want %d)",
			s.path(profileID), a.SchemaVersion, SchemaVersion)
	}
	if a.Profile != profileID {
		return nil, fmt.Errorf("assign: %s names profile %q", s.path(profileID), a.Profile)
	}
	return &a, nil
}

// Save writes an assignment durably.
//
// fsync'd through fsx.WriteFileSync for the same reason COMPLETE and the
// published record are: this is the document a boot reads to decide what to
// bring back, so it has to survive the crash it exists to recover from.
func (s *Store) Save(a *Assignment) error {
	if err := config.ValidName("profile id", a.Profile); err != nil {
		return fmt.Errorf("assign: %w", err)
	}
	a.SchemaVersion = SchemaVersion
	a.sortServers()
	body, err := json.MarshalIndent(a, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(s.cfg.AssignmentsDir(), 0o755); err != nil {
		return err
	}
	return fsx.WriteFileSync(s.path(a.Profile), append(body, '\n'), 0o644)
}

// List returns every profile with an assignment document, sorted.
func (s *Store) List() ([]string, error) {
	entries, err := os.ReadDir(s.cfg.AssignmentsDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".json") {
			continue
		}
		out = append(out, strings.TrimSuffix(name, ".json"))
	}
	sort.Strings(out)
	return out, nil
}

// LoadAll returns every profile's assignment.
func (s *Store) LoadAll() ([]*Assignment, error) {
	ids, err := s.List()
	if err != nil {
		return nil, err
	}
	out := make([]*Assignment, 0, len(ids))
	for _, id := range ids {
		a, err := s.Load(id)
		if err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, nil
}

func (s *Store) path(profileID string) string {
	return filepath.Join(s.cfg.AssignmentsDir(), profileID+".json")
}

// Server returns the named server's entry.
func (a *Assignment) Server(id string) (Server, bool) {
	for _, srv := range a.Servers {
		if srv.ID == id {
			return srv, true
		}
	}
	return Server{}, false
}

// AddServer records a consumer, replacing its access if it is already there.
func (a *Assignment) AddServer(id, access string) error {
	if id == "" {
		return errors.New("assign: a server needs an id")
	}
	for i := range a.Servers {
		if a.Servers[i].ID == id {
			a.Servers[i].Access = access
			return nil
		}
	}
	a.Servers = append(a.Servers, Server{ID: id, Access: access})
	a.sortServers()
	return nil
}

// RemoveServer drops a consumer, reporting whether it was there.
func (a *Assignment) RemoveServer(id string) bool {
	for i := range a.Servers {
		if a.Servers[i].ID == id {
			a.Servers = append(a.Servers[:i], a.Servers[i+1:]...)
			return true
		}
	}
	return false
}

// SetRelease points the assignment at a release, remembering the previous one.
//
// A no-op activation does NOT shift Previous. Re-activating what is already
// active is a legitimate thing to do — it is how a degraded generation is
// rebuilt — and letting it overwrite rollback's only memory would mean the
// repair silently destroyed the way back.
func (a *Assignment) SetRelease(releaseID string) {
	if a.Release == releaseID {
		return
	}
	if a.Release != "" {
		a.Previous = a.Release
	}
	a.Release = releaseID
}

// ServerIDs is every consumer id, ordered.
func (a *Assignment) ServerIDs() []string {
	out := make([]string, 0, len(a.Servers))
	for _, s := range a.Servers {
		out = append(out, s.ID)
	}
	return out
}

func (a *Assignment) sortServers() {
	sort.Slice(a.Servers, func(i, j int) bool { return a.Servers[i].ID < a.Servers[j].ID })
}

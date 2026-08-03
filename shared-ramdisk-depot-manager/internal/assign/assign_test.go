package assign

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"srdm/internal/config"
)

func testStore(t *testing.T) (*Store, config.Config) {
	t.Helper()
	cfg := config.Default()
	cfg.StateDir = filepath.Join(t.TempDir(), "state")
	cfg.RunDir = filepath.Join(t.TempDir(), "run")
	s, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return s, cfg
}

// A profile that has never been assigned is a normal state, not a missing
// document. Every profile starts there, `gc` and the boot path both meet it,
// and making it an error would mean every caller writing the same two lines
// to turn it back into a value.
func TestAnUnassignedProfileLoadsAsAnEmptyAssignment(t *testing.T) {
	s, _ := testStore(t)

	a, err := s.Load("testgame")
	if err != nil {
		t.Fatalf("loading a profile that has never been assigned: %v", err)
	}
	if a.Profile != "testgame" || a.Release != "" || len(a.Servers) != 0 {
		t.Errorf("an unassigned profile loaded as %+v", a)
	}
}

func TestAnAssignmentRoundTripsThroughDisk(t *testing.T) {
	s, cfg := testStore(t)

	a, err := s.Load("testgame")
	if err != nil {
		t.Fatal(err)
	}
	a.SetRelease("rel-1")
	if err := a.AddServer("server-b", "ro"); err != nil {
		t.Fatal(err)
	}
	if err := a.AddServer("server-a", "rw"); err != nil {
		t.Fatal(err)
	}
	if err := s.Save(a); err != nil {
		t.Fatal(err)
	}

	back, err := s.Load("testgame")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(a, back) {
		t.Errorf("round trip changed the assignment:\n got %+v\nwant %+v", back, a)
	}
	// Ordered by id, so the document is stable and a diff of two states shows
	// what changed rather than how the map iterated.
	if got := back.ServerIDs(); !reflect.DeepEqual(got, []string{"server-a", "server-b"}) {
		t.Errorf("servers came back as %v, want them ordered", got)
	}

	// Durable, and where the boot path will look for it.
	if _, err := os.Stat(filepath.Join(cfg.AssignmentsDir(), "testgame.json")); err != nil {
		t.Errorf("the assignment is not on disk: %v", err)
	}
}

// Rollback's only memory is one step deep, so what shifts it matters more
// than usual.
func TestSetReleaseRemembersOnlyTheReleaseItReplaced(t *testing.T) {
	a := &Assignment{SchemaVersion: SchemaVersion, Profile: "testgame"}

	a.SetRelease("rel-1")
	if a.Previous != "" {
		t.Errorf("the first activation invented a rollback target: %q", a.Previous)
	}
	a.SetRelease("rel-2")
	if a.Release != "rel-2" || a.Previous != "rel-1" {
		t.Errorf("after two activations: release %q previous %q", a.Release, a.Previous)
	}
	a.SetRelease("rel-3")
	if a.Previous != "rel-2" {
		t.Errorf("previous is %q, want the release just replaced", a.Previous)
	}
}

// Re-activating what is already live is how a degraded generation is
// repaired. If that shifted the rollback target, the repair would destroy the
// way back — and it would do it silently, at exactly the moment somebody is
// already dealing with something being wrong.
func TestReActivatingTheLiveReleaseDoesNotDestroyTheRollbackTarget(t *testing.T) {
	a := &Assignment{SchemaVersion: SchemaVersion, Profile: "testgame"}
	a.SetRelease("rel-1")
	a.SetRelease("rel-2")

	a.SetRelease("rel-2")

	if a.Previous != "rel-1" {
		t.Errorf("re-activating the live release left previous as %q, want rel-1; "+
			"repairing the present must not destroy the way back", a.Previous)
	}
}

func TestAddServerReplacesTheAccessOfOneAlreadyThere(t *testing.T) {
	a := &Assignment{SchemaVersion: SchemaVersion, Profile: "testgame"}
	if err := a.AddServer("s1", "ro"); err != nil {
		t.Fatal(err)
	}
	if err := a.AddServer("s1", "rw"); err != nil {
		t.Fatal(err)
	}
	if len(a.Servers) != 1 {
		t.Fatalf("adding a server twice produced %d entries", len(a.Servers))
	}
	if srv, _ := a.Server("s1"); srv.Access != "rw" {
		t.Errorf("access is %q, want the new one", srv.Access)
	}
	if !a.RemoveServer("s1") || a.RemoveServer("s1") {
		t.Error("RemoveServer does not report whether it removed anything")
	}
}

// A document that names a different profile is a document that was copied or
// renamed. Reading it would attribute one profile's servers to another, and
// the first thing to notice would be a generation exposed to the wrong ones.
func TestAnAssignmentNamingAnotherProfileIsRefused(t *testing.T) {
	s, cfg := testStore(t)
	body := `{"schema_version":1,"profile":"othergame","release":"rel-1"}`
	if err := os.WriteFile(filepath.Join(cfg.AssignmentsDir(), "testgame.json"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	_, err := s.Load("testgame")
	if err == nil {
		t.Fatal("an assignment naming another profile was accepted")
	}
	if !strings.Contains(err.Error(), "othergame") {
		t.Errorf("the error does not name the mismatch: %v", err)
	}
}

func TestAnUnknownSchemaVersionIsRefused(t *testing.T) {
	s, cfg := testStore(t)
	body := `{"schema_version":99,"profile":"testgame"}`
	if err := os.WriteFile(filepath.Join(cfg.AssignmentsDir(), "testgame.json"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Load("testgame"); err == nil {
		t.Fatal("an assignment from a future schema was accepted")
	}
}

func TestListAndLoadAllSeeEveryAssignedProfile(t *testing.T) {
	s, _ := testStore(t)
	for _, id := range []string{"beta", "alpha"} {
		a := &Assignment{SchemaVersion: SchemaVersion, Profile: id}
		a.SetRelease("rel-" + id)
		if err := s.Save(a); err != nil {
			t.Fatal(err)
		}
	}
	ids, err := s.List()
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(ids, []string{"alpha", "beta"}) {
		t.Errorf("List = %v", ids)
	}
	all, err := s.LoadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 2 || all[0].Release != "rel-alpha" {
		t.Errorf("LoadAll = %+v", all)
	}
}

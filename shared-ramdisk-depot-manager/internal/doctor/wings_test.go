package doctor

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/config"
	"srdm/internal/publish"
	"srdm/internal/wings"
)

// --- propagation -----------------------------------------------------------

// A correctly set up node: / shared on the host, rslave on the container.
func TestPropagationPassesOnACorrectNode(t *testing.T) {
	f := newFixture(t)
	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-propagation")
	if c.Status != StatusPass {
		t.Fatalf("wings-propagation is %s on a correct node: %s", c.Status, c.Detail)
	}
}

// Without a shared host peer group there is nothing for the container's
// rslave to be a slave of, so the container's setting cannot save it.
func TestPropagationFailsWhenTheHostSideIsPrivate(t *testing.T) {
	f := newFixture(t)
	f.rootPropagation = ""
	f.writeMountInfo(t, "rw,nsdelegate,memory_recursiveprot")

	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-propagation")
	if c.Status != StatusFail {
		t.Fatalf("wings-propagation is %s with a private host mount", c.Status)
	}
	if !strings.Contains(c.Fix, "make-rshared") {
		t.Errorf("the fix does not name the remedy: %s", c.Fix)
	}
}

// Docker's default, and the 2026-07-31 outage.
func TestPropagationFailsOnAnRPrivateContainer(t *testing.T) {
	f := newFixture(t)
	f.containerPropagation = "rprivate"

	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-propagation")
	if c.Status != StatusFail {
		t.Fatalf("wings-propagation is %s with an rprivate container", c.Status)
	}
	if !strings.Contains(c.Fix, "rslave") {
		t.Errorf("the fix does not name the remedy: %s", c.Fix)
	}
	if !strings.Contains(c.Detail, "wings") {
		t.Errorf("the detail does not name the container: %s", c.Detail)
	}
}

// Not being able to ask Docker is not a failure of the node. Reporting it as
// one sends an operator to fix a container that may already be correct.
func TestPropagationSkipsRatherThanFailsWhenDockerIsUnreachable(t *testing.T) {
	f := newFixture(t)
	f.inspectErr = errors.New("dial unix /var/run/docker.sock: no such file")

	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-propagation")
	if c.Status != StatusSkip {
		t.Fatalf("wings-propagation is %s when Docker is unreachable, want skip: %s",
			c.Status, c.Detail)
	}
	// The host half is still reported, because srdm could read it.
	if !strings.Contains(c.Detail, "shared") {
		t.Errorf("the host half was not reported: %s", c.Detail)
	}
}

// --- the pre-boot chown walk ----------------------------------------------

func TestChownWalkFailsWhenTheWalkWouldRun(t *testing.T) {
	f := newFixture(t)
	f.chownWalk = wings.ChownWalk{Enabled: true, Known: true, Source: "the fixture"}

	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-chown-walk")
	if c.Status != StatusFail {
		t.Fatalf("wings-chown-walk is %s when the walk would run", c.Status)
	}
	if !strings.Contains(c.Detail, "EROFS") {
		t.Errorf("the detail does not say what would happen: %s", c.Detail)
	}
	// Both remedies, because a node can take either.
	if !strings.Contains(c.Fix, "F1") || !strings.Contains(c.Fix, "check_permissions_on_boot") {
		t.Errorf("the fix does not name both remedies: %s", c.Fix)
	}
}

// A node running a patched build says so, and then the walk is irrelevant —
// including when srdm cannot read the node config at all.
func TestChownWalkPassesWhenTheNodeAssertsF1(t *testing.T) {
	f := newFixture(t)
	f.chownWalkErr = errors.New("config.yml is unreadable")
	f.chownWalk = wings.ChownWalk{Enabled: true}
	cfg := f.config()
	cfg.Wings.ChownSkipPatch = true

	c := find(t, Run(cfg, fixtureProfile(t), f.options()), "wings-chown-walk")
	if c.Status != StatusPass {
		t.Fatalf("wings-chown-walk is %s on a node asserting F1: %s", c.Status, c.Detail)
	}
}

func TestChownWalkFailsWhenTheAnswerCannotBeRead(t *testing.T) {
	f := newFixture(t)
	f.chownWalkErr = errors.New("config.yml is unreadable")
	f.chownWalk = wings.ChownWalk{Enabled: true}

	c := find(t, Run(f.config(), fixtureProfile(t), f.options()), "wings-chown-walk")
	if c.Status != StatusFail {
		t.Fatalf("wings-chown-walk is %s when srdm could not read the setting", c.Status)
	}
}

// --- no node, no checks ----------------------------------------------------

// Everything upstream of the exposure fork works without a Pterodactyl node.
// Inventing one to check against would report failures about a deployment
// that does not exist.
func TestNoWingsSettingsMeansNoWingsChecks(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	cfg.Wings = config.Wings{}

	for _, c := range Run(cfg, fixtureProfile(t), f.options()) {
		if strings.HasPrefix(c.ID, "wings-") || c.ID == "generation-drift" {
			t.Errorf("check %q ran against a host with no Wings settings", c.ID)
		}
	}
}

// --- generation drift ------------------------------------------------------

// writeRecord puts a published-state record where doctor will find it.
func writeRecord(t *testing.T, cfg config.Config, rec *publish.Record) {
	t.Helper()
	path := cfg.PublishedRecord(rec.Profile, rec.Generation)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	body, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(body, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}

// driftFixture is a promoted release plus a directory tree standing in for
// its published pak class — the thing an rw exposure would be bound to, and
// therefore the thing a write through one would land in.
type driftFixture struct {
	id        string
	exposeDir string
}

func stageForDrift(t *testing.T, cfg config.Config) driftFixture {
	t.Helper()
	rel := buildStore(t, cfg, fixtureProfile(t))

	// The published class tree is a copy of the release's pak content.
	// Copying it rather than pointing at the release itself is the point:
	// the exposure is what drifts, and the release is what it is compared
	// against.
	exposeDir := filepath.Join(t.TempDir(), "published-pak")
	src := filepath.Join(rel.RootDir(), "WS", "Content", "Paks")
	dst := filepath.Join(exposeDir, "WS", "Content", "Paks")
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(src)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		body, err := os.ReadFile(filepath.Join(src, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dst, e.Name()), body, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return driftFixture{id: rel.ID, exposeDir: exposeDir}
}

func TestNoDirtyGenerationsPasses(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	writeRecord(t, cfg, &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    "a1b2c3d4", Profile: "testgame", ReleaseID: "rel-1",
	})

	c := find(t, Run(cfg, fixtureProfile(t), f.options()), "generation-drift")
	if c.Status != StatusPass {
		t.Fatalf("generation-drift is %s with nothing exposed writable: %s", c.Status, c.Detail)
	}
}

// A generation that has been exposed writable is a warning even when its
// content still matches: the mark is what makes it unshareable, and that is
// true whatever the hashes say.
func TestADirtyGenerationWarnsEvenWhenItStillMatches(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	rel := stageForDrift(t, cfg)
	writeRecord(t, cfg, &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    "a1b2c3d4", Profile: "testgame", ReleaseID: rel.id,
		DirtyCapable: true,
		Classes: []publish.ClassRecord{
			{Name: "pak", ExposePath: rel.exposeDir},
		},
	})

	c := find(t, Run(cfg, fixtureProfile(t), f.options()), "generation-drift")
	if c.Status != StatusWarn {
		t.Fatalf("generation-drift is %s for an unmodified dirty generation: %s",
			c.Status, c.Detail)
	}
	if !strings.Contains(c.Detail, "unshareable") {
		t.Errorf("the detail does not say what the mark costs: %s", c.Detail)
	}
}

// And when it really has drifted, that is a failure with the drift named.
func TestADriftedGenerationFailsAndSaysWhere(t *testing.T) {
	f := newFixture(t)
	cfg := f.config()
	rel := stageForDrift(t, cfg)

	// Somebody wrote through the rw exposure.
	if err := os.WriteFile(filepath.Join(rel.exposeDir, "WS", "Content", "Paks", "game.pak"),
		[]byte("written through"), 0o644); err != nil {
		t.Fatal(err)
	}
	writeRecord(t, cfg, &publish.Record{
		SchemaVersion: publish.RecordSchemaVersion,
		Generation:    "a1b2c3d4", Profile: "testgame", ReleaseID: rel.id,
		DirtyCapable: true,
		Classes: []publish.ClassRecord{
			{Name: "pak", ExposePath: rel.exposeDir},
		},
	})

	c := find(t, Run(cfg, fixtureProfile(t), f.options()), "generation-drift")
	if c.Status != StatusFail {
		t.Fatalf("generation-drift is %s after a write through the exposure: %s",
			c.Status, c.Detail)
	}
	if !strings.Contains(c.Detail, "pak") {
		t.Errorf("the detail does not name the class that drifted: %s", c.Detail)
	}
	if !strings.Contains(c.Fix, "harvest") {
		t.Errorf("the fix does not offer the deliberate-change path: %s", c.Fix)
	}
}

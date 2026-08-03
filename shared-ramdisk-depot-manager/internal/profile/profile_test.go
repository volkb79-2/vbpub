package profile

import (
	"errors"
	"strings"
	"testing"
)

func mustProfile(t *testing.T) *Profile {
	t.Helper()
	p := &Profile{
		SchemaVersion: SchemaVersion,
		ID:            "soulmask",
		Classes: []Class{
			{Name: "pak", Kind: KindManaged, Paths: []string{"WS/Content/Paks"}, MemoryMin: 150 << 20},
			{Name: "code", Kind: KindManaged, Paths: []string{"Engine", "WS/Binaries"}, MemoryMin: 200 << 20},
			{Name: "state", Kind: KindExcluded, Paths: []string{"WS/Saved", "WS/Config"}},
		},
	}
	if err := p.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}
	return p
}

func TestClassifyResolvesTheLongestMatchingPrefix(t *testing.T) {
	p := mustProfile(t)

	cases := map[string]string{
		"Engine":                     "code",
		"Engine/Binaries/lib.so":     "code",
		"WS/Binaries/server":         "code",
		"WS/Content/Paks":            "pak",
		"WS/Content/Paks/deep/a.pak": "pak",
		"WS/Saved":                   "state",
		"WS/Saved/world.db":          "state",
		"WS/Config/GameXishu.json":   "state",
		"WS":                         "structure",
		"WS/Content":                 "structure",
	}
	for path, want := range cases {
		got, err := p.Classify(path)
		if err != nil {
			t.Errorf("Classify(%q): %v", path, err)
			continue
		}
		if got.Name != want {
			t.Errorf("Classify(%q) = %q, want %q", path, got.Name, want)
		}
	}
}

// Prefix matching is on whole path components. Without that, a rule for
// "WS/Content" would silently swallow a sibling named "WS/ContentBackup"
// and give it a memory policy nobody chose for it.
func TestClassifyDoesNotMatchPartialComponents(t *testing.T) {
	p := mustProfile(t)
	for _, path := range []string{"WS/Content/PaksBackup", "Engine2", "WS/SavedGames"} {
		if _, err := p.Classify(path); err == nil {
			t.Errorf("Classify(%q) matched a rule it only shares a prefix string with", path)
		}
	}
}

// O3's error contract: the message names the path and the profile, because
// those are the two things an operator needs to fix it.
func TestClassifyUnclassifiedErrorNamesPathAndProfile(t *testing.T) {
	p := mustProfile(t)

	_, err := p.Classify("WS/notes.txt")
	if err == nil {
		t.Fatal("an unclassified path was accepted")
	}
	var unclassified *UnclassifiedError
	if !errors.As(err, &unclassified) {
		t.Fatalf("want *UnclassifiedError, got %T", err)
	}
	if unclassified.Path != "WS/notes.txt" {
		t.Errorf("Path = %q", unclassified.Path)
	}
	if unclassified.Profile != "soulmask" {
		t.Errorf("Profile = %q", unclassified.Profile)
	}
	msg := err.Error()
	if !strings.Contains(msg, "WS/notes.txt") || !strings.Contains(msg, "soulmask") {
		t.Errorf("message carries neither the path nor the profile: %s", msg)
	}
}

// The ancestor rule must be an ancestor rule, not a "shares a prefix" rule.
func TestStructureOnlyCoversRealAncestors(t *testing.T) {
	p := mustProfile(t)
	if _, err := p.Classify("WSX"); err == nil {
		t.Error("a sibling of a structural directory classified as structure")
	}
	got, err := p.Classify("WS")
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != KindStructure {
		t.Errorf("WS classified as %q, want structure", got.Kind)
	}
}

func TestValidateRejectsMalformedProfiles(t *testing.T) {
	cases := []struct {
		name    string
		profile Profile
		want    string
	}{
		{
			name: "wrong schema version",
			profile: Profile{SchemaVersion: 99, ID: "x",
				Classes: []Class{{Name: "a", Kind: KindManaged, Paths: []string{"A"}}}},
			want: "schema_version",
		},
		{
			name:    "no classes",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x"},
			want:    "no classes",
		},
		{
			name: "unknown kind",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes: []Class{{Name: "a", Kind: "sideways", Paths: []string{"A"}}}},
			want: "unknown kind",
		},
		{
			name: "two classes claim one path",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x", Classes: []Class{
				{Name: "a", Kind: KindManaged, Paths: []string{"A"}},
				{Name: "b", Kind: KindManaged, Paths: []string{"A"}},
			}},
			want: "claimed by both",
		},
		{
			name: "absolute path",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes: []Class{{Name: "a", Kind: KindManaged, Paths: []string{"/etc"}}}},
			want: "must be relative",
		},
		{
			name: "path escapes the root",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes: []Class{{Name: "a", Kind: KindManaged, Paths: []string{"../elsewhere"}}}},
			want: "escapes",
		},
		{
			name: "structure is reserved",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes: []Class{{Name: "structure", Kind: KindManaged, Paths: []string{"A"}}}},
			want: "implicit class",
		},
		{
			name: "unknown probe kind",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes: []Class{{Name: "a", Kind: KindManaged, Paths: []string{"A"}}},
				Probes:  []Probe{{Name: "p", Kind: "vibes", Path: "A"}}},
			want: "unknown kind",
		},
		{
			// A credential the journal cannot substring-match would leak into
			// every record quoting it, so the profile is refused rather than
			// the guarantee quietly weakened.
			name: "unscrubbable credential",
			profile: Profile{SchemaVersion: SchemaVersion, ID: "x",
				Classes:     []Class{{Name: "a", Kind: KindManaged, Paths: []string{"A"}}},
				Credentials: map[string]string{"token": "ab"}},
			want: "would leak",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.profile.Validate()
			if err == nil {
				t.Fatal("Validate accepted a malformed profile")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error %q does not mention %q", err, tc.want)
			}
		})
	}
}

func TestParseRejectsUnknownFields(t *testing.T) {
	// A typo'd key must not be silently ignored: a profile is the thing that
	// decides what gets published, so a rule that quietly did not load is
	// exactly the failure mode classification exists to prevent.
	_, err := Parse([]byte(`{
	  "schema_version": 1,
	  "id": "x",
	  "clases": [],
	  "classes": [{"name": "a", "kind": "managed", "paths": ["A"]}]
	}`))
	if err == nil {
		t.Fatal("a profile with a misspelled key parsed cleanly")
	}
}

func TestSecretsAreStableAndSkipEmpties(t *testing.T) {
	p := &Profile{
		SchemaVersion: SchemaVersion, ID: "x",
		Classes:     []Class{{Name: "a", Kind: KindManaged, Paths: []string{"A"}}},
		Credentials: map[string]string{"token": "tok-aaaa", "password": "pw-bbbb", "unset": ""},
	}
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	got := p.Secrets()
	// Sorted by key: password, token, unset (empty dropped).
	want := []string{"pw-bbbb", "tok-aaaa"}
	if len(got) != len(want) {
		t.Fatalf("Secrets() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Secrets() = %v, want %v", got, want)
		}
	}
}

func TestManagedFloorSumIgnoresUnmanagedClasses(t *testing.T) {
	p := mustProfile(t)
	// Only the two managed classes count; excluded state is never held in a
	// tmpfs, so a floor for it would be a number protecting nothing.
	want := int64(350 << 20)
	if got := p.ManagedFloorSum(); got != want {
		t.Fatalf("ManagedFloorSum() = %d, want %d", got, want)
	}
}

func TestClassifyValidatesLazily(t *testing.T) {
	// A Profile built in Go and never explicitly validated must still
	// classify correctly rather than panic on a nil index.
	p := &Profile{SchemaVersion: SchemaVersion, ID: "x",
		Classes: []Class{{Name: "a", Kind: KindManaged, Paths: []string{"A"}}}}
	got, err := p.Classify("A/b")
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != "a" {
		t.Fatalf("Classify = %q", got.Name)
	}
}

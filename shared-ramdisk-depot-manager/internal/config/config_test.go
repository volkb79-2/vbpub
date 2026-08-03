package config

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestDefaultIsValid(t *testing.T) {
	if err := Default().Validate(); err != nil {
		t.Fatalf("the shipped defaults do not validate: %v", err)
	}
}

// The single-token name is a correctness requirement, not a style choice:
// systemd reads "-" as the slice hierarchy separator, so a hyphenated root
// would nest under auto-created ancestors carrying MemoryMin=0 — and a
// child's floor is only effective while its parent's covers it. Every class
// floor beneath such a slice would be arithmetically dead, silently.
func TestValidateRejectsAHyphenatedSlice(t *testing.T) {
	cfg := Default()
	cfg.Slice = "shared-ramdisk-depot-manager.slice"

	err := cfg.Validate()
	if err == nil {
		t.Fatal("a hyphenated slice name was accepted")
	}
	if !strings.Contains(err.Error(), "MemoryMin=0") {
		t.Fatalf("the error does not explain the consequence: %v", err)
	}
}

func TestValidateRejectsMalformedConfigs(t *testing.T) {
	cases := []struct {
		name string
		edit func(*Config)
		want string
	}{
		{"empty state dir", func(c *Config) { c.StateDir = "" }, "state_dir is empty"},
		{"relative state dir", func(c *Config) { c.StateDir = "var/lib/srdm" }, "absolute"},
		{"empty run dir", func(c *Config) { c.RunDir = "" }, "run_dir is empty"},
		{"relative run dir", func(c *Config) { c.RunDir = "run/srdm" }, "absolute"},
		{"empty slice", func(c *Config) { c.Slice = "" }, "slice is empty"},
		{"not a slice unit", func(c *Config) { c.Slice = "srdm.service" }, "systemd slice unit"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := Default()
			tc.edit(&cfg)
			err := cfg.Validate()
			if err == nil {
				t.Fatal("Validate accepted a malformed config")
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error %q does not mention %q", err, tc.want)
			}
		})
	}
}

func TestLayoutPathsAreRootedAndDistinct(t *testing.T) {
	cfg := Default()
	cfg.StateDir = "/srv/srdm"

	paths := map[string]string{
		"store":      cfg.StoreDir(),
		"releases":   cfg.ReleasesDir(),
		"channels":   cfg.ChannelsDir(),
		"tx":         cfg.TxDir(),
		"quarantine": cfg.QuarantineDir(),
		"journal":    cfg.JournalDir(),
	}
	seen := map[string]string{}
	for name, p := range paths {
		if !strings.HasPrefix(p, cfg.StateDir+"/") {
			t.Errorf("%s path %q is outside the state dir", name, p)
		}
		if other, dup := seen[p]; dup {
			t.Errorf("%s and %s resolve to the same path %q", name, other, p)
		}
		seen[p] = name
	}

	if got, want := cfg.ReleaseDir("rel-1"), filepath.Join(cfg.ReleasesDir(), "rel-1"); got != want {
		t.Errorf("ReleaseDir = %q, want %q", got, want)
	}
	if got, want := cfg.ChannelLink("soulmask", "stable"),
		filepath.Join(cfg.ChannelsDir(), "soulmask", "stable"); got != want {
		t.Errorf("ChannelLink = %q, want %q", got, want)
	}
}

// Release ids, profile ids and channel names come from operator input and
// are concatenated into store paths, so traversal has to be refused at the
// boundary rather than remembered at each call site.
func TestValidNameRejectsPathTraversal(t *testing.T) {
	bad := []string{"", ".", "..", "../etc", "a/b", "a\\b", "rel 1", "rel\x00", strings.Repeat("x", 129)}
	for _, s := range bad {
		if err := ValidName("release id", s); err == nil {
			t.Errorf("ValidName accepted %q", s)
		}
	}
	good := []string{"rel-1", "rel_1", "rel.1", "REL1", "a", strings.Repeat("x", 128)}
	for _, s := range good {
		if err := ValidName("release id", s); err != nil {
			t.Errorf("ValidName rejected %q: %v", s, err)
		}
	}
}

func TestValidNameErrorNamesTheKind(t *testing.T) {
	err := ValidName("channel", "../escape")
	if err == nil {
		t.Fatal("ValidName accepted a traversal")
	}
	if !strings.Contains(err.Error(), "channel") {
		t.Fatalf("the error does not say what was wrong: %v", err)
	}
}

func TestDefaultOwnershipLeavesTheOwnerUnset(t *testing.T) {
	own := DefaultOwnership()
	// An unset owner means "normalize modes only". Defaulting to 0:0 would
	// make an unconfigured srdm silently chown a whole tree to root.
	if own.UID >= 0 || own.GID >= 0 {
		t.Fatalf("DefaultOwnership() = %+v; the owner must start unset", own)
	}
	if own.DirMode != 0o755 || own.FileMode != 0o644 {
		t.Fatalf("DefaultOwnership() modes = %v/%v, want 0755/0644", own.DirMode, own.FileMode)
	}
}

package profile

import (
	"path/filepath"
	"testing"
)

// The shipped example is the first thing an operator copies. If it stops
// loading, or its classification stops matching what the live host shares,
// they find out here rather than at a maintenance window.
func TestShippedSoulmaskExampleLoadsAndClassifies(t *testing.T) {
	p, err := Load(filepath.Join("..", "..", "examples", "soulmask.profile.json"))
	if err != nil {
		t.Fatalf("the shipped example does not load: %v", err)
	}
	if p.ID != "soulmask" {
		t.Fatalf("example id is %q", p.ID)
	}

	// The split the live host actually runs: pak data separate from
	// everything zstd can compress, per-instance state excluded from both.
	cases := map[string]string{
		"WS/Content/Paks/WS-Linux.pak":       "pak",
		"WS/Binaries/Linux/WSServer":         "code",
		"Engine/Binaries/Linux/libcrypto.so": "code",
		"steamcmd/linux32/steamcmd":          "code",
		"steamapps/appmanifest_3017300.acf":  "code",
		"Manifest_NonUFSFiles_Linux.txt":     "code",
		// The absolute state rule: never shared, never a content input,
		// never modified by a transaction.
		"WS/Saved/world.db":                        "state",
		"WS/Saved/GameplaySettings/GameXishu.json": "state",
		"WS/Config/ServerSettings.ini":             "state",
		"Steam/logs/connection_log.txt":            "state",
		// Structural directories nobody declares.
		"WS":         "structure",
		"WS/Content": "structure",
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

	// pak data is zstd-incompressible (measured 1.006x on the case-study
	// node), so its pages bypass zswap entirely rather than burning CPU to
	// not shrink.
	for _, c := range p.Classes {
		if c.Name != "pak" {
			continue
		}
		if c.ZSwapMax == nil || *c.ZSwapMax != 0 {
			t.Error("the pak class should set zswap_max to 0")
		}
		if !c.NoExec {
			t.Error("the pak class is data only and should mount noexec")
		}
	}

	// 150M + 200M — the number srdm.slice's shipped 512M MemoryMin covers.
	if got, want := p.ManagedFloorSum(), int64(350<<20); got != want {
		t.Errorf("managed floors sum to %d, want %d (systemd/srdm.slice is sized against this)", got, want)
	}

	// The example must carry no credentials. It is a document people copy.
	if len(p.Credentials) != 0 {
		t.Error("the shipped example carries credentials")
	}
}

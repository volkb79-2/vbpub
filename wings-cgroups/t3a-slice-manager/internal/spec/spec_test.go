package spec

import "testing"

func u64p(v uint64) *uint64 { return &v }

func TestParseSize(t *testing.T) {
	tests := []struct {
		in      string
		want    uint64
		wantErr bool
	}{
		{"6G", 6 << 30, false},
		{"6g", 6 << 30, false},
		{"512M", 512 << 20, false},
		{"1024K", 1 << 20, false},
		{"123456", 123456, false},
		{" 64M ", 64 << 20, false},
		{"", 0, true},
		{"abc", 0, true},
		{"-5G", 0, true},
		{"6GB", 0, true},
		{"999999999999G", 0, true}, // overflow
	}
	for _, tc := range tests {
		got, err := ParseSize(tc.in)
		if tc.wantErr != (err != nil) {
			t.Errorf("ParseSize(%q) error = %v, wantErr %v", tc.in, err, tc.wantErr)
			continue
		}
		if !tc.wantErr && got != tc.want {
			t.Errorf("ParseSize(%q) = %d, want %d", tc.in, got, tc.want)
		}
	}
}

func TestParseWeight(t *testing.T) {
	tests := []struct {
		in      string
		want    uint64
		wantErr bool
	}{
		{"1", 1, false},
		{"800", 800, false},
		{"10000", 10000, false},
		{"0", 0, true},
		{"10001", 0, true},
		{"abc", 0, true},
		{"", 0, true},
	}
	for _, tc := range tests {
		got, err := ParseWeight(tc.in)
		if tc.wantErr != (err != nil) {
			t.Errorf("ParseWeight(%q) error = %v, wantErr %v", tc.in, err, tc.wantErr)
			continue
		}
		if !tc.wantErr && got != tc.want {
			t.Errorf("ParseWeight(%q) = %d, want %d", tc.in, got, tc.want)
		}
	}
}

func TestFromEnvDiscrete(t *testing.T) {
	parent, props, errs := FromEnv([]string{
		"FOO=bar",
		"WINGS_CGROUP_PARENT=wings-abc.slice",
		"WINGS_CG_MEMORY_MIN=6G",
		"WINGS_CG_MEMORY_HIGH=8G",
		"WINGS_CG_CPU_WEIGHT=800",
		"WINGS_CG_IO_WEIGHT=100",
	})
	if len(errs) != 0 {
		t.Fatalf("unexpected errors: %v", errs)
	}
	if parent != "wings-abc.slice" {
		t.Errorf("parent = %q", parent)
	}
	want := Props{MemoryMin: u64p(6 << 30), MemoryHigh: u64p(8 << 30), CPUWeight: u64p(800), IOWeight: u64p(100)}
	if !props.Equal(want) {
		t.Errorf("props = %s, want %s", props, want)
	}
}

func TestFromEnvJSONBlob(t *testing.T) {
	parent, props, errs := FromEnv([]string{
		`WINGS_CGROUP_JSON={"memory_min":"6G","memory_low":"12G","memory_high":8589934592,"cpu_weight":800,"io_weight":"100"}`,
	})
	if len(errs) != 0 {
		t.Fatalf("unexpected errors: %v", errs)
	}
	if parent != "" {
		t.Errorf("parent = %q, want empty", parent)
	}
	want := Props{
		MemoryMin:  u64p(6 << 30),
		MemoryLow:  u64p(12 << 30),
		MemoryHigh: u64p(8 << 30),
		CPUWeight:  u64p(800),
		IOWeight:   u64p(100),
	}
	if !props.Equal(want) {
		t.Errorf("props = %s, want %s", props, want)
	}
}

func TestFromEnvDiscreteWinsOverBlob(t *testing.T) {
	_, props, errs := FromEnv([]string{
		`WINGS_CGROUP_JSON={"memory_min":"6G","cpu_weight":500}`,
		"WINGS_CG_MEMORY_MIN=2G",
	})
	if len(errs) != 0 {
		t.Fatalf("unexpected errors: %v", errs)
	}
	if props.MemoryMin == nil || *props.MemoryMin != 2<<30 {
		t.Errorf("MemoryMin = %v, want 2G (discrete must win)", props.MemoryMin)
	}
	if props.CPUWeight == nil || *props.CPUWeight != 500 {
		t.Errorf("CPUWeight = %v, want 500 (from blob)", props.CPUWeight)
	}
}

func TestFromEnvInvalidValuesAreSkippedNotFatal(t *testing.T) {
	_, props, errs := FromEnv([]string{
		"WINGS_CG_MEMORY_MIN=banana",
		"WINGS_CG_CPU_WEIGHT=99999",
		"WINGS_CG_MEMORY_HIGH=4G",
	})
	if len(errs) != 2 {
		t.Fatalf("errs = %v, want 2 entries", errs)
	}
	if props.MemoryMin != nil || props.CPUWeight != nil {
		t.Error("invalid values must stay unset")
	}
	if props.MemoryHigh == nil || *props.MemoryHigh != 4<<30 {
		t.Error("valid value alongside invalid ones must still parse")
	}
}

func TestFromEnvInvalidJSON(t *testing.T) {
	_, props, errs := FromEnv([]string{"WINGS_CGROUP_JSON={not json"})
	if len(errs) != 1 {
		t.Fatalf("errs = %v, want 1", errs)
	}
	if !props.Empty() {
		t.Error("props must be empty on JSON parse failure")
	}
}

func TestNamespaceValidSliceName(t *testing.T) {
	ns := Namespace{Prefix: "wings-", Parent: "wings.slice"}
	valid := []string{
		"wings-abc.slice",
		"wings-b87c0a5b.slice",
		"wings-a_b.c-d.slice",
		"wings-0.slice",
	}
	invalid := []string{
		"",
		"wings.slice",        // the parent — never managed
		"system.slice",       // outside namespace
		"dev-workloads.slice",
		"wings-.slice",       // empty body
		"wings--x.slice",     // body starts non-alphanumeric
		"wings-a b.slice",    // whitespace
		"wings-abc",          // missing suffix
		"wings-abc.scope",    // wrong unit type
		"wings-a/b.slice",    // path separator
	}
	for _, name := range valid {
		if !ns.ValidSliceName(name) {
			t.Errorf("ValidSliceName(%q) = false, want true", name)
		}
	}
	for _, name := range invalid {
		if ns.ValidSliceName(name) {
			t.Errorf("ValidSliceName(%q) = true, want false", name)
		}
	}
}

func TestNamespaceCustomPrefix(t *testing.T) {
	ns := Namespace{Prefix: "game-", Parent: "game.slice"}
	if !ns.ValidSliceName("game-x.slice") {
		t.Error("custom prefix slice should be valid")
	}
	if ns.ValidSliceName("wings-x.slice") {
		t.Error("default-prefix slice should be invalid under custom namespace")
	}
	if ns.ValidSliceName("game.slice") {
		t.Error("custom parent must never be managed")
	}
}

// Package spec parses per-container cgroup property specifications from
// container environment variables and enforces the slice namespace guard.
//
// The environment variables are the T2 transport: admin-only egg/server
// variables defined in the Panel, resolved by (patched) Wings into the
// container environment. They carry non-secret placement/resource metadata
// only; the daemon validates everything and never trusts a value.
package spec

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Environment variable names recognized on containers.
const (
	// EnvParent is the placement variable consumed by patched Wings
	// (HostConfig.CgroupParent). The daemon reads it only to detect
	// mismatches between requested and actual placement.
	EnvParent = "WINGS_CGROUP_PARENT"
	// EnvJSON is a single JSON blob carrying all properties at once:
	//   {"memory_min":"6G","memory_low":"12G","memory_high":"8G",
	//    "memory_max":"10G","cpu_weight":800,"io_weight":100}
	// Sizes may be strings ("6G") or plain byte numbers; weights may be
	// numbers or numeric strings. Discrete WINGS_CG_* variables take
	// precedence over blob fields.
	EnvJSON       = "WINGS_CGROUP_JSON"
	EnvMemoryMin  = "WINGS_CG_MEMORY_MIN"
	EnvMemoryLow  = "WINGS_CG_MEMORY_LOW"
	EnvMemoryHigh = "WINGS_CG_MEMORY_HIGH"
	EnvMemoryMax  = "WINGS_CG_MEMORY_MAX"
	EnvCPUWeight  = "WINGS_CG_CPU_WEIGHT"
	EnvIOWeight   = "WINGS_CG_IO_WEIGHT"
)

// Props are the systemd slice properties a container requests for its
// per-server slice. Nil means "not requested" (the slice keeps whatever
// systemd default or previously-set value applies).
type Props struct {
	MemoryMin  *uint64
	MemoryLow  *uint64
	MemoryHigh *uint64
	MemoryMax  *uint64
	CPUWeight  *uint64
	IOWeight   *uint64
}

// Empty reports whether no property is requested at all.
func (p Props) Empty() bool {
	return p.MemoryMin == nil && p.MemoryLow == nil && p.MemoryHigh == nil &&
		p.MemoryMax == nil && p.CPUWeight == nil && p.IOWeight == nil
}

func eqU64(a, b *uint64) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

// Equal reports whether two Props request identical values.
func (p Props) Equal(o Props) bool {
	return eqU64(p.MemoryMin, o.MemoryMin) && eqU64(p.MemoryLow, o.MemoryLow) &&
		eqU64(p.MemoryHigh, o.MemoryHigh) && eqU64(p.MemoryMax, o.MemoryMax) &&
		eqU64(p.CPUWeight, o.CPUWeight) && eqU64(p.IOWeight, o.IOWeight)
}

// String renders the requested properties for logs.
func (p Props) String() string {
	var b strings.Builder
	add := func(name string, v *uint64) {
		if v != nil {
			if b.Len() > 0 {
				b.WriteByte(' ')
			}
			fmt.Fprintf(&b, "%s=%d", name, *v)
		}
	}
	add("MemoryMin", p.MemoryMin)
	add("MemoryLow", p.MemoryLow)
	add("MemoryHigh", p.MemoryHigh)
	add("MemoryMax", p.MemoryMax)
	add("CPUWeight", p.CPUWeight)
	add("IOWeight", p.IOWeight)
	if b.Len() == 0 {
		return "(none)"
	}
	return b.String()
}

// flexValue accepts a JSON string or a bare JSON number and preserves it as
// text for the size/weight parsers.
type flexValue struct{ raw string }

func (f *flexValue) UnmarshalJSON(b []byte) error {
	if len(b) > 0 && b[0] == '"' {
		var s string
		if err := json.Unmarshal(b, &s); err != nil {
			return err
		}
		f.raw = s
		return nil
	}
	f.raw = string(b)
	return nil
}

type jsonSpec struct {
	MemoryMin  *flexValue `json:"memory_min"`
	MemoryLow  *flexValue `json:"memory_low"`
	MemoryHigh *flexValue `json:"memory_high"`
	MemoryMax  *flexValue `json:"memory_max"`
	CPUWeight  *flexValue `json:"cpu_weight"`
	IOWeight   *flexValue `json:"io_weight"`
}

// FromEnv extracts the requested placement (WINGS_CGROUP_PARENT) and slice
// properties from a container environment. Discrete WINGS_CG_* variables take
// precedence over fields of the WINGS_CGROUP_JSON blob. Unparseable values
// are reported in errs and skipped; parsing continues so one bad value never
// discards the rest of the spec.
func FromEnv(env []string) (parent string, props Props, errs []error) {
	vals := map[string]string{}
	for _, kv := range env {
		k, v, ok := strings.Cut(kv, "=")
		if !ok {
			continue
		}
		switch k {
		case EnvParent, EnvJSON, EnvMemoryMin, EnvMemoryLow, EnvMemoryHigh,
			EnvMemoryMax, EnvCPUWeight, EnvIOWeight:
			vals[k] = v
		}
	}
	parent = vals[EnvParent]

	if raw := vals[EnvJSON]; raw != "" {
		var j jsonSpec
		if err := json.Unmarshal([]byte(raw), &j); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", EnvJSON, err))
		} else {
			setFlex := func(field string, fv *flexValue, dst **uint64, parse func(string) (uint64, error)) {
				if fv == nil {
					return
				}
				v, err := parse(fv.raw)
				if err != nil {
					errs = append(errs, fmt.Errorf("%s.%s: %w", EnvJSON, field, err))
					return
				}
				*dst = &v
			}
			setFlex("memory_min", j.MemoryMin, &props.MemoryMin, ParseSize)
			setFlex("memory_low", j.MemoryLow, &props.MemoryLow, ParseSize)
			setFlex("memory_high", j.MemoryHigh, &props.MemoryHigh, ParseSize)
			setFlex("memory_max", j.MemoryMax, &props.MemoryMax, ParseSize)
			setFlex("cpu_weight", j.CPUWeight, &props.CPUWeight, ParseWeight)
			setFlex("io_weight", j.IOWeight, &props.IOWeight, ParseWeight)
		}
	}

	setVar := func(name string, dst **uint64, parse func(string) (uint64, error)) {
		s, ok := vals[name]
		if !ok || s == "" {
			return
		}
		v, err := parse(s)
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", name, err))
			return
		}
		*dst = &v
	}
	setVar(EnvMemoryMin, &props.MemoryMin, ParseSize)
	setVar(EnvMemoryLow, &props.MemoryLow, ParseSize)
	setVar(EnvMemoryHigh, &props.MemoryHigh, ParseSize)
	setVar(EnvMemoryMax, &props.MemoryMax, ParseSize)
	setVar(EnvCPUWeight, &props.CPUWeight, ParseWeight)
	setVar(EnvIOWeight, &props.IOWeight, ParseWeight)

	return parent, props, errs
}

// ParseSize parses "6G", "512M", "1024K" (single-letter binary suffixes,
// case-insensitive) or a plain byte count into bytes.
func ParseSize(s string) (uint64, error) {
	t := strings.TrimSpace(s)
	if t == "" {
		return 0, errors.New("empty size")
	}
	mult := uint64(1)
	switch t[len(t)-1] {
	case 'G', 'g':
		mult, t = 1<<30, t[:len(t)-1]
	case 'M', 'm':
		mult, t = 1<<20, t[:len(t)-1]
	case 'K', 'k':
		mult, t = 1<<10, t[:len(t)-1]
	}
	n, err := strconv.ParseUint(t, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid size %q (want e.g. 6G, 512M, 1024K or bytes)", s)
	}
	if mult > 1 && n > math.MaxUint64/mult {
		return 0, fmt.Errorf("size %q overflows", s)
	}
	return n * mult, nil
}

// ParseWeight parses a systemd cgroup weight (1..10000).
func ParseWeight(s string) (uint64, error) {
	n, err := strconv.ParseUint(strings.TrimSpace(s), 10, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid weight %q", s)
	}
	if n < 1 || n > 10000 {
		return 0, fmt.Errorf("weight %d out of range 1..10000", n)
	}
	return n, nil
}

// Namespace is the hard guard confining every slice the daemon may create,
// modify or stop. The parent slice is explicitly excluded: it is owned by the
// administrator (unit file), and its MemoryMin is the floor budget the
// children draw from.
type Namespace struct {
	// Prefix that every managed child slice must carry, e.g. "wings-".
	Prefix string
	// Parent is the admin-owned tier slice, e.g. "wings.slice". Never managed.
	Parent string
}

// ValidSliceName reports whether the daemon is allowed to manage the given
// unit name: <Prefix><body>.slice where body starts alphanumeric and contains
// only [a-zA-Z0-9_.-], and the name is not the parent slice.
func (n Namespace) ValidSliceName(name string) bool {
	if name == "" || name == n.Parent {
		return false
	}
	if !strings.HasPrefix(name, n.Prefix) || !strings.HasSuffix(name, ".slice") {
		return false
	}
	body := strings.TrimSuffix(strings.TrimPrefix(name, n.Prefix), ".slice")
	if body == "" {
		return false
	}
	for i, r := range body {
		alnum := (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9')
		if i == 0 && !alnum {
			return false
		}
		if !alnum && r != '_' && r != '.' && r != '-' {
			return false
		}
	}
	return true
}

// Package config loads the daemon configuration (YAML) with defaults for
// every field; a missing config file is not an error.
package config

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"

	"wings-slice-manager/internal/spec"
)

// DefaultPath is used when no -config flag is given.
const DefaultPath = "/etc/wings-slice-manager.yaml"

// Duration wraps time.Duration with YAML string parsing ("60s", "5m").
type Duration time.Duration

func (d *Duration) UnmarshalYAML(value *yaml.Node) error {
	var s string
	if err := value.Decode(&s); err != nil {
		return err
	}
	v, err := time.ParseDuration(s)
	if err != nil {
		return fmt.Errorf("invalid duration %q: %w", s, err)
	}
	*d = Duration(v)
	return nil
}

// Config is the on-disk YAML shape.
type Config struct {
	// ParentSlice is the admin-owned tier slice (unit file, not managed by
	// this daemon). Its MemoryMin is the budget the child floors draw from.
	ParentSlice string `yaml:"parent_slice"`
	// SlicePrefix confines which slices the daemon may create/modify/stop.
	SlicePrefix string `yaml:"slice_prefix"`
	// MemoryMinBudget caps the sum of child MemoryMin floors ("8G"; empty or
	// "0" = unlimited).
	MemoryMinBudget string `yaml:"memory_min_budget"`
	// BudgetPolicy is what happens to the floor that would exceed the budget:
	// "clamp" (reduce to the remaining budget) or "refuse" (drop the floor).
	BudgetPolicy      string   `yaml:"budget_policy"`
	ReconcileInterval Duration `yaml:"reconcile_interval"`
	EventDebounce     Duration `yaml:"event_debounce"`
	GCGrace           Duration `yaml:"gc_grace"`
	DryRun            bool     `yaml:"dry_run"`
	LogLevel          string   `yaml:"log_level"`
}

// Default returns the built-in configuration.
func Default() Config {
	return Config{
		ParentSlice:       "wings.slice",
		SlicePrefix:       "wings-",
		MemoryMinBudget:   "",
		BudgetPolicy:      "clamp",
		ReconcileInterval: Duration(60 * time.Second),
		EventDebounce:     Duration(2 * time.Second),
		GCGrace:           Duration(5 * time.Minute),
		DryRun:            false,
		LogLevel:          "info",
	}
}

// Load reads the YAML config at path on top of the defaults. A missing file
// yields the defaults with usedDefaults=true.
func Load(path string) (cfg Config, usedDefaults bool, err error) {
	cfg = Default()
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg, true, nil
		}
		return cfg, false, err
	}
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return cfg, false, fmt.Errorf("parsing %s: %w", path, err)
	}
	return cfg, false, cfg.validate()
}

func (c Config) validate() error {
	if c.SlicePrefix == "" {
		return fmt.Errorf("slice_prefix must not be empty")
	}
	if c.ParentSlice == "" {
		return fmt.Errorf("parent_slice must not be empty")
	}
	if c.BudgetPolicy != "clamp" && c.BudgetPolicy != "refuse" {
		return fmt.Errorf("budget_policy must be \"clamp\" or \"refuse\", got %q", c.BudgetPolicy)
	}
	if _, err := c.BudgetBytes(); err != nil {
		return err
	}
	switch c.LogLevel {
	case "debug", "info", "warn", "error":
	default:
		return fmt.Errorf("log_level must be debug|info|warn|error, got %q", c.LogLevel)
	}
	return nil
}

// BudgetBytes parses MemoryMinBudget; 0 means unlimited.
func (c Config) BudgetBytes() (uint64, error) {
	if c.MemoryMinBudget == "" || c.MemoryMinBudget == "0" {
		return 0, nil
	}
	v, err := spec.ParseSize(c.MemoryMinBudget)
	if err != nil {
		return 0, fmt.Errorf("memory_min_budget: %w", err)
	}
	return v, nil
}

// Namespace builds the slice namespace guard from the config.
func (c Config) Namespace() spec.Namespace {
	return spec.Namespace{Prefix: c.SlicePrefix, Parent: c.ParentSlice}
}

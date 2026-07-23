// wings-slice-manager — external T3a slice manager for Wings nodes.
//
// Watches Docker containers placed under the wings-*.slice namespace (by
// cgroup-parent patched Wings, T2) and creates/reconciles the transient
// per-server systemd slices with the resource properties requested through
// admin-only WINGS_CG_* container environment variables. Enforces the slice
// namespace guard and a node-wide MemoryMin floor budget, and garbage
// collects slices whose containers are gone.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"wings-slice-manager/internal/config"
	"wings-slice-manager/internal/dockersrc"
	"wings-slice-manager/internal/manager"
	"wings-slice-manager/internal/sysd"
)

const version = "0.1.0"

func main() {
	configPath := flag.String("config", config.DefaultPath, "path to the YAML configuration file")
	dryRun := flag.Bool("dry-run", false, "log actions instead of performing them (overrides config)")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println("wings-slice-manager " + version)
		return
	}

	cfg, usedDefaults, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "wings-slice-manager: %v\n", err)
		os.Exit(1)
	}
	if *dryRun {
		cfg.DryRun = true
	}

	var level slog.Level
	switch cfg.LogLevel {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	default:
		level = slog.LevelInfo
	}
	log := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: level}))

	if usedDefaults {
		log.Info("no config file found; using built-in defaults", "path", *configPath)
	}

	budget, err := cfg.BudgetBytes()
	if err != nil {
		log.Error("invalid configuration", "error", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	src, err := dockersrc.New(log)
	if err != nil {
		log.Error("connecting to docker failed", "error", err)
		os.Exit(1)
	}
	defer src.Close()
	if err := src.Ping(ctx); err != nil {
		log.Error("docker daemon unreachable", "error", err)
		os.Exit(1)
	}

	sd, err := sysd.New(ctx, log)
	if err != nil {
		log.Error("connecting to systemd failed", "error", err)
		os.Exit(1)
	}
	defer sd.Close()

	mcfg := manager.Config{
		Namespace:         cfg.Namespace(),
		MemoryMinBudget:   budget,
		BudgetPolicy:      cfg.BudgetPolicy,
		ReconcileInterval: time.Duration(cfg.ReconcileInterval),
		EventDebounce:     time.Duration(cfg.EventDebounce),
		GCGrace:           time.Duration(cfg.GCGrace),
		DryRun:            cfg.DryRun,
	}
	log.Info("starting",
		"version", version,
		"parent_slice", mcfg.Namespace.Parent,
		"slice_prefix", mcfg.Namespace.Prefix,
		"memory_min_budget", mcfg.MemoryMinBudget,
		"budget_policy", mcfg.BudgetPolicy,
		"reconcile_interval", mcfg.ReconcileInterval,
		"gc_grace", mcfg.GCGrace,
		"dry_run", mcfg.DryRun,
	)

	m := manager.New(mcfg, src, sd, log)
	if err := m.Run(ctx); err != nil {
		log.Error("manager stopped with error", "error", err)
		os.Exit(1)
	}
	log.Info("shutting down")
}

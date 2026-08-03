// Package steam will own the containerized SteamCMD acquisition driver:
// app_info_print for build identity, an unconditional validate at release
// creation, identity from appmanifest_<appid>.acf, and stage jobs confined
// to srdm-stage.slice where io.max, CPUQuota and MemoryHigh are the real
// limiters.
//
// Empty, and deliberately OFF the MVP critical path (master plan decision
// 12). `harvest` covers acquisition for v1: the game's own updater is a
// legitimate source, and harvest — quiesce, re-hash, classify, probe,
// promote — is what makes its output trustworthy. This driver remains
// planned as the unattended path.
//
// When it lands, it runs as an unprivileged uid inside the egg's own runner
// image, writing only into its transaction directory, and hands the daemon
// one fsync'd typed result record. The daemon does not parse foreign data;
// that is the whole reason this is a separate worker.
package steam

// Package adminapi will serve the operator socket at /run/srdm/admin.sock
// (0600, root), which every CLI subcommand except `doctor --offline` speaks
// to.
//
// Empty until the daemon exists. P01's CLI operates on the store directly,
// which is correct for the offline verbs it ships — promote, activate,
// verify, list, recover and doctor are all things an operator must be able
// to do with no daemon running, including at boot and during recovery.
package adminapi

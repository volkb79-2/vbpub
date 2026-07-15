/*
 * ksm-optin.c — LD_PRELOAD shim opting every process into KSM (mdt variant).
 *
 * Derived from game_stuff/soulmask/ksm-optin.c (2026-07-07, verified on this
 * host's kernel 7.0: PR_SET_MEMORY_MERGE succeeds unprivileged in a default
 * docker container). Differences from the soulmask original:
 *   - SILENT by default: this shim is preloaded into EVERY process of an mdt
 *     container (ENV LD_PRELOAD in the image); a success line per exec would
 *     spam every shell command. Set KSM_OPTIN_VERBOSE=1 to log outcomes.
 *   - Idempotence guard via PR_GET_MEMORY_MERGE (cheap; avoids redundant SET
 *     in fork/exec chains that already inherited the flag).
 *
 * Why a preload (not a wrapper): PR_SET_MEMORY_MERGE does not survive execve;
 * a constructor runs inside the final process, after exec, and the flag is
 * inherited by fork()ed children.
 *
 * Effect: all current and future PRIVATE ANONYMOUS memory becomes
 * KSM-mergeable (MADV_MERGEABLE-equivalent on every compatible VMA). ksmd
 * (host: /sys/kernel/mm/ksm/run=1) merges identical pages system-wide across
 * opted-in processes. Host trades ksmd CPU for RAM — deliberate on this host.
 *
 * Build: gcc -shared -fPIC -O2 -o ksm-optin.so ksm-optin.c   (Dockerfile stage)
 * Disable per-process: LD_PRELOAD= <cmd>   (or unset in the environment)
 */
#include <sys/prctl.h>
#include <stdio.h>
#include <stdlib.h>

#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#endif
#ifndef PR_GET_MEMORY_MERGE
#define PR_GET_MEMORY_MERGE 68
#endif

__attribute__((constructor))
static void ksm_optin(void)
{
    const char *verbose = getenv("KSM_OPTIN_VERBOSE");

    if (prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0) == 1) {
        if (verbose && *verbose == '1')
            fprintf(stderr, "[ksm-optin] already enabled (inherited)\n");
        return;
    }
    if (prctl(PR_SET_MEMORY_MERGE, 1, 0, 0, 0) == 0) {
        if (verbose && *verbose == '1')
            fprintf(stderr, "[ksm-optin] KSM enabled (PR_SET_MEMORY_MERGE=1)\n");
    } else if (verbose && *verbose == '1') {
        perror("[ksm-optin] PR_SET_MEMORY_MERGE failed "
               "(kernel <6.4, CONFIG_KSM off, or needs CAP_SYS_RESOURCE)");
    }
}

/*
 * ksm-optin.c — LD_PRELOAD shim that opts the loading process into KSM.
 *
 * Why a preload: PR_SET_MEMORY_MERGE sets the MMF_VM_MERGE_ANY flag on the
 * CALLING process's mm and does NOT survive execve — a wrapper script that
 * calls prctl and then execs the game would lose the flag. A preload
 * constructor runs inside the game process itself, after exec, so it sticks
 * (and is inherited by fork()ed children).
 *
 * Kernel: PR_SET_MEMORY_MERGE needs kernel >= 6.4. Verified 2026-07-07 on
 * this host (kernel 7.0): succeeds UNPRIVILEGED inside a default docker
 * container — no CAP_SYS_RESOURCE needed (older kernels required it; if you
 * see EPERM in the log line below, that's why).
 *
 * Build:   gcc -shared -fPIC -O2 -o ksm-optin.so ksm-optin.c
 * Deploy:  copy ksm-optin.so into the server volume as /home/container/ksm-optin.so
 *          (chown to the container uid/gid, e.g. 988:988) and start the game
 *          with LD_PRELOAD=/home/container/ksm-optin.so (see
 *          egg-soulmask-rcon-ksm.json). If the .so is missing, ld.so prints a
 *          harmless "cannot be preloaded ... ignored" warning and the game
 *          starts normally without KSM.
 *
 * Effect:  all current and future PRIVATE ANONYMOUS memory of the process
 *          becomes KSM-mergeable (equivalent to MADV_MERGEABLE on every
 *          compatible VMA). ksmd then scans and merges identical pages
 *          system-wide across all opted-in processes. See MEASUREMENTS.md M7
 *          for what is / is not covered and how to measure the benefit.
 */
#include <sys/prctl.h>
#include <stdio.h>

#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#endif

__attribute__((constructor))
static void ksm_optin(void)
{
    if (prctl(PR_SET_MEMORY_MERGE, 1, 0, 0, 0) == 0)
        fprintf(stderr, "[ksm-optin] KSM enabled for this process (PR_SET_MEMORY_MERGE=1)\n");
    else
        perror("[ksm-optin] PR_SET_MEMORY_MERGE failed (kernel <6.4, CONFIG_KSM off, or old kernel needing CAP_SYS_RESOURCE)");
}

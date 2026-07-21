/*
 * ksm-optin.c — LD_PRELOAD shim that opts the loading process into KSM.
 *
 * Vendored from dstdns/tools/ksm-optin/ksm-optin.c (same mechanism, reused
 * here so the pwmcp image opts in by default on its own -- every pwmcp
 * instance across all consuming repos dedupes identical memory pages
 * without depending on any consumer's ciu governance overlay).
 *
 * Why a preload: PR_SET_MEMORY_MERGE sets the MMF_VM_MERGE_ANY flag on the
 * CALLING process's mm and does NOT survive execve — a wrapper script that
 * calls prctl and then execs the target would lose the flag. A preload
 * constructor runs inside the target process itself, after exec, so it
 * sticks (and is inherited by fork()ed children — e.g. supervisord's
 * children in this image, since LD_PRELOAD is inherited environment).
 *
 * Kernel: PR_SET_MEMORY_MERGE needs kernel >= 6.4. Works unprivileged inside
 * a default docker container on kernels that support it (no CAP_SYS_RESOURCE
 * needed). On older kernels, or when CONFIG_KSM is off, the prctl fails; this
 * shim then just logs a warning to stderr and lets the process continue
 * normally -- so enabling it by default here is safe.
 *
 * Build:   compiled from source in containers/pwmcp/Dockerfile's
 *          ksm-optin-builder stage:
 *            cc -shared -fPIC -O2 -o /opt/ksm/ksm-optin.so ksm-optin.c
 *          (NOT shipped as a prebuilt .so — compiling in the Dockerfile
 *          avoids arch fragility.)
 * Deploy:  entrypoint.sh sets LD_PRELOAD=/opt/ksm/ksm-optin.so before
 *          `exec supervisord` when PWMCP_KSM_OPTIN=1 (the default; set to 0
 *          to disable). If the .so were ever missing, ld.so prints a
 *          harmless "cannot be preloaded ... ignored" warning and the
 *          process starts normally without KSM.
 *
 * Effect:  all current and future PRIVATE ANONYMOUS memory of the process
 *          becomes KSM-mergeable (equivalent to MADV_MERGEABLE on every
 *          compatible VMA). ksmd then scans and merges identical pages
 *          system-wide across all opted-in processes -- including across
 *          separate pwmcp containers, and other opted-in services, on the
 *          same host.
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

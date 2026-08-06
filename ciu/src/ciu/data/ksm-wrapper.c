/* ksm-wrapper — candidate replacement for the LD_PRELOAD KSM shim.
 *
 * The LD_PRELOAD shim cannot work for statically-linked binaries: no dynamic
 * loader runs, so nothing preloads. A wrapper sidesteps that by calling
 * prctl(PR_SET_MEMORY_MERGE, 1) itself and then exec'ing the real program.
 *
 * The open question this program exists to answer: does MMF_VM_MERGE_ANY
 * SURVIVE execve()? execve allocates a fresh mm_struct, and whether the flag
 * is carried across depends on the kernel's mm-flag init mask. If it is
 * cleared, the wrapper approach is dead on arrival.
 *
 * Modes:
 *   ksm-wrapper --no-exec           set the flag, then sleep in-process
 *                                   (CONTROL: proves prctl itself works)
 *   ksm-wrapper <prog> [args...]    set the flag, then execvp
 *                                   (TEST: does it survive?)
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

#ifndef PR_SET_MEMORY_MERGE
#define PR_SET_MEMORY_MERGE 67
#endif
#ifndef PR_GET_MEMORY_MERGE
#define PR_GET_MEMORY_MERGE 68
#endif

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s {--no-exec | <prog> [args...]}\n", argv[0]);
        return 2;
    }

    if (prctl(PR_SET_MEMORY_MERGE, 1, 0, 0, 0) != 0) {
        fprintf(stderr, "[wrapper] prctl(PR_SET_MEMORY_MERGE,1) FAILED: %s\n",
                strerror(errno));
        return 3;
    }

    int got = prctl(PR_GET_MEMORY_MERGE, 0, 0, 0, 0);
    fprintf(stderr, "[wrapper] pid=%d pre-exec PR_GET_MEMORY_MERGE=%d\n",
            getpid(), got);
    fflush(stderr);

    if (strcmp(argv[1], "--no-exec") == 0) {
        sleep(30);
        return 0;
    }

    execvp(argv[1], &argv[1]);
    fprintf(stderr, "[wrapper] execvp(%s) FAILED: %s\n", argv[1], strerror(errno));
    return 4;
}

/*
 * ksm-optin-universal.c — libc-free LD_PRELOAD shim that opts the loading
 * process into KSM (PR_SET_MEMORY_MERGE, kernel >= 6.4).
 *
 * Why libc-free: one .so must preload under BOTH glibc and musl. A
 * glibc-built shim carries DT_NEEDED libc.so.6 (musl-side noise), and a
 * musl-built shim is FATAL under glibc's ld.so (measured 2026-07-17:
 * "error while loading shared libraries: libc.musl-x86_64.so.1", exit
 * 127). With -nostdlib and raw syscalls the .so has ZERO dependencies and
 * both loaders accept it; statically-linked programs never run a dynamic
 * loader, so LD_PRELOAD is silently irrelevant for them (banyandb, minio).
 *
 * Build: gcc -shared -fPIC -nostdlib -O2 -o ksm-optin.so ksm-optin-universal.c
 * Verify: readelf -d ksm-optin.so | grep NEEDED   -> no output
 *
 * x86-64 only (syscall ABI inlined). Constructor via .init_array.
 */

static long sys5(long n, long a, long b, long c, long d, long e)
{
    register long rax __asm__("rax") = n;
    register long rdi __asm__("rdi") = a;
    register long rsi __asm__("rsi") = b;
    register long rdx __asm__("rdx") = c;
    register long r10 __asm__("r10") = d;
    register long r8  __asm__("r8")  = e;
    __asm__ volatile("syscall"
                     : "+r"(rax)
                     : "r"(rdi), "r"(rsi), "r"(rdx), "r"(r10), "r"(r8)
                     : "rcx", "r11", "memory");
    return rax;
}

#define SYS_prctl 157
#define PR_SET_MEMORY_MERGE 67

#ifdef KSM_OPTIN_TESTING
/* Test-only override: production builds do not read this environment key. */
extern char *getenv(const char *name);
int ksm_optin_test_loaded = 0;

static int test_value_is(const char *value, const char *expected)
{
    if (!value)
        return 0;
    while (*value && *expected && *value == *expected) {
        value++;
        expected++;
    }
    return *value == *expected;
}

static long test_prctl_result(void)
{
    const char *forced = getenv("KSM_OPTIN_TEST_PRCTL");
    if (test_value_is(forced, "success"))
        return 0;
    if (test_value_is(forced, "failure"))
        return -1;
    return sys5(SYS_prctl, PR_SET_MEMORY_MERGE, 1, 0, 0, 0);
}
#endif

__attribute__((constructor))
static void ksm_optin(void)
{
#ifdef KSM_OPTIN_TESTING
    ksm_optin_test_loaded = 1;
    (void)test_prctl_result();
#else
    /* This constructor runs in every dynamically linked child. Stay silent. */
    (void)sys5(SYS_prctl, PR_SET_MEMORY_MERGE, 1, 0, 0, 0);
#endif
}

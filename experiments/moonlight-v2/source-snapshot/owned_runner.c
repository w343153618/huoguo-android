/* Default-off standalone snapshot launcher candidate. No borrowed PID signals.
 * The sole fork child remains owned until this one-thread parent reaps it.
 * Host fixture modes exist only in a separately compiled HG_HOST_FIXTURE build.
 * A wait receipt proves main-child exit, not source qualification or UI teardown.
 */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1
#define _GNU_SOURCE 1
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

#ifdef HG_HOST_FIXTURE
#define RUN_MS 180
#define GRACE_MS 60
#define REAP_MS 100
#else
#define RUN_MS 1800
#define GRACE_MS 200
#define REAP_MS 200
#endif
#define START_MS 100
#define LOG_LIMIT 8191

static volatile sig_atomic_t cancelled;
static void cancel_handler(int signo) { (void)signo; cancelled = 1; }
static uint64_t now_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * UINT64_C(1000000000) + (uint64_t)ts.tv_nsec;
}
static int nonce_ok(const char *s) {
    if (!s || strlen(s) != 24) return 0;
    for (size_t i = 0; i < 24; ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return 0;
    return 1;
}
static int nonblock(int fd) {
    int flags = fcntl(fd, F_GETFL);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}
static int cloexec(int fd) { return fcntl(fd, F_SETFD, FD_CLOEXEC); }
static int write_all(int fd, const void *data, size_t len) {
    const unsigned char *p = data;
    while (len) {
        ssize_t n = write(fd, p, len);
        if (n > 0) { p += n; len -= (size_t)n; }
        else if (n < 0 && errno == EINTR) continue;
        else return -1;
    }
    return 0;
}
static void nap(void) {
    struct timespec ts = {0, 2000000};
    /* EINTR returns to the owner loop so cancellation is handled there. */
    (void)nanosleep(&ts, NULL);
}
static uint64_t own_start_ticks(void) {
#ifdef __linux__
    char buf[8192];
    int fd = open("/proc/self/stat", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0 || n == (ssize_t)sizeof(buf) - 1) return 0;
    buf[n] = 0;
    char *p = strrchr(buf, ')');
    if (!p || p[1] != ' ') return 0;
    p += 2;
    /* state is field3; starttime is field22, i.e. token19 here. */
    for (int i = 0; i < 19; ++i) {
        p = strchr(p, ' ');
        if (!p) return 0;
        while (*p == ' ') ++p;
    }
    char *end;
    errno = 0;
    uint64_t value = strtoull(p, &end, 10);
    return errno || end == p || (*end != ' ' && *end != '\n') ? 0 : value;
#else
    /* Darwin fixtures deliberately do not invent Linux start ticks. */
    return 0;
#endif
}
static int record(int dir, const char *name, const char *row, size_t n) {
    int fd = openat(dir, name, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    int result = write_all(fd, row, n);
    if (close(fd) != 0) result = -1;
    return result;
}
static int owned_signal(pid_t child, int reaped, int signo) {
    /* Single-thread waitpid ownership: even a just-exited zombie cannot reuse
     * this PID until OUR reap. No handler, file or /proc lookup calls kill. */
    if (child <= 1 || reaped) return -1;
    return kill(child, signo);
}
static int drain(int fd, int logfd, size_t *bytes, int *eof) {
    char buf[256];
    for (int chunks = 0; chunks < 33; ++chunks) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n == 0) { *eof = 1; return 0; }
        if (n < 0) {
            if (errno == EINTR) return 0;
            return errno == EAGAIN || errno == EWOULDBLOCK ? 0 : -1;
        }
        if (*bytes + (size_t)n > LOG_LIMIT) return 1;
        if (write_all(logfd, buf, (size_t)n) != 0) return -1;
        *bytes += (size_t)n;
    }
    return 0;
}

#ifdef HG_HOST_FIXTURE
static int fixture_ok(const char *s) {
    static const char *modes[] = {"exit0", "exit7", "term", "ignore-term", "output", "execfail", "retained-pipe", "clockfail"};
    for (size_t i = 0; i < sizeof(modes) / sizeof(modes[0]); ++i)
        if (!strcmp(s, modes[i])) return 1;
    return 0;
}
static void fixture_child(const char *mode) {
    if (!strcmp(mode, "exit0")) _exit(0);
    if (!strcmp(mode, "exit7")) _exit(7);
    if (!strcmp(mode, "execfail")) {
        execl("/this-closed-fixture-does-not-exist", "fixture", (char *)NULL);
        _exit(127);
    }
    if (!strcmp(mode, "ignore-term")) signal(SIGTERM, SIG_IGN);
    if (!strcmp(mode, "output")) {
        char bytes[1024]; memset(bytes, 'x', sizeof(bytes));
        for (int i = 0; i < 16; ++i) (void)write_all(STDOUT_FILENO, bytes, sizeof(bytes));
    }
    if (!strcmp(mode, "retained-pipe")) {
        pid_t descendant = fork();
        if (descendant < 0) _exit(126);
        if (descendant > 0) _exit(0);
        struct timespec ts = {0, 500000000};
        (void)nanosleep(&ts, NULL); _exit(0);
    }
    /* Fixture fallback bounded independently if the parent crashes. */
    uint64_t until = now_ns() + UINT64_C(3000000000);
    while (now_ns() < until) nap();
    _exit(0);
}
#endif

static void child_main(pid_t parent, int ready, int gate, int output,
                       const char *namespace, const char *jar_namespace, const char *mode) {
    struct sigaction action;
    memset(&action, 0, sizeof(action)); action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    for (int i = 0; i < 3; ++i) (void)sigaction((int[]){SIGTERM, SIGINT, SIGHUP}[i], &action, NULL);
#ifdef __linux__
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent) _exit(125);
#else
    (void)parent;
#endif
    uint64_t ticks = own_start_ticks();
    if (write_all(ready, &ticks, sizeof(ticks)) != 0) _exit(124);
    close(ready);
    char go;
    ssize_t n;
    do { n = read(gate, &go, 1); } while (n < 0 && errno == EINTR);
    close(gate);
    if (n != 1 || go != 'G') _exit(123);
    if (dup2(output, STDOUT_FILENO) < 0 || dup2(output, STDERR_FILENO) < 0) _exit(122);
    close(output);
#ifdef HG_HOST_FIXTURE
    (void)namespace; (void)jar_namespace;
    fixture_child(mode);
#elif defined(__ANDROID__)
    (void)mode;
    char jar[96], relative[96];
    snprintf(jar, sizeof(jar), "%s/snapshot.jar", jar_namespace);
    snprintf(relative, sizeof(relative), "%s/window.xml", namespace);
    /* Fixed executable and class; no sh -c, --nohup, input or caller argv. */
    char *const args[] = {"uiautomator", "runtest", jar, "-c",
        "local.huoguo.sourceprobe.SourceSnapshot#testSnapshot", "-e", "relative", relative, NULL};
    char *const env[] = {"PATH=/system/bin", "ANDROID_DATA=/data", "ANDROID_ROOT=/system", NULL};
    execve("/system/bin/uiautomator", args, env);
    _exit(127);
#else
    (void)namespace; (void)jar_namespace; (void)mode;
    _exit(121);
#endif
}

int main(int argc, char **argv) {
    const char *mode = NULL;
    char namespace[64], jar_namespace[64] = {0};
#ifdef HG_HOST_FIXTURE
    if (argc != 4 || strcmp(argv[1], "--host-fixture") || !nonce_ok(argv[2]) || !fixture_ok(argv[3])) return 64;
    mode = argv[3];
#elif defined(__ANDROID__)
    if (argc != 4 || strcmp(argv[1], "--snapshot") || !nonce_ok(argv[2]) || !nonce_ok(argv[3])
            || !strcmp(argv[2], argv[3])) return 64;
    snprintf(jar_namespace, sizeof(jar_namespace), "huoguo-source-ui-%s", argv[3]);
#else
    (void)argc; (void)argv;
    return 64;  /* Non-Android production binary is inert. */
#endif
    snprintf(namespace, sizeof(namespace), "huoguo-source-ui-%s", argv[2]);
    umask(077);
    struct sigaction action;
    memset(&action, 0, sizeof(action)); action.sa_handler = cancel_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) || sigaction(SIGINT, &action, NULL)
            || sigaction(SIGHUP, &action, NULL)) return 65;
    /* SIGPIPE must not kill the parent before it can reap a dead gated child. */
    action.sa_handler = SIG_IGN;
    if (sigaction(SIGPIPE, &action, NULL)) return 65;
#ifdef HG_HOST_FIXTURE
    int base = open(".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
#else
    int base = open("/data/local/tmp", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
#endif
    if (base < 0) return 66;
#ifdef HG_HOST_FIXTURE
    struct stat root;
    if (fstat(base, &root) || root.st_uid != getuid() || (root.st_mode & 07777) != 0700) { close(base); return 66; }
#endif
    if (mkdirat(base, namespace, 0700)) { close(base); return 67; }
    int dir = openat(base, namespace, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat scope;
    if (dir < 0 || fstat(dir, &scope) || scope.st_uid != getuid() || (scope.st_mode & 07777) != 0700) return 68;
    close(base);
    int logfd = openat(dir, "runner.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    int birth[2], gate[2], output[2];
    if (logfd < 0 || pipe(birth) || pipe(gate) || pipe(output)) return 69;
    int all[] = {birth[0], birth[1], gate[0], gate[1], output[0], output[1]};
    for (size_t i = 0; i < sizeof(all) / sizeof(all[0]); ++i) if (cloexec(all[i])) return 69;
    if (nonblock(birth[0]) || nonblock(output[0])) return 69;
    uint64_t began = now_ns();
    if (!began) return 70;
    pid_t parent = getpid();
    uint64_t parent_ticks = own_start_ticks();
    pid_t child = fork();
    if (child < 0) return 71;
    if (child == 0) {
        close(birth[0]); close(gate[1]); close(output[0]); close(dir); close(logfd);
        child_main(parent, birth[1], gate[0], output[1], namespace, jar_namespace, mode);
        _exit(120);
    }
    close(birth[1]); close(gate[0]); close(output[1]);
    uint64_t ticks = 0;
    size_t received = 0;
    while (received < sizeof(ticks) && !cancelled && now_ns() - began < START_MS * UINT64_C(1000000)) {
        ssize_t n = read(birth[0], (char *)&ticks + received, sizeof(ticks) - received);
        if (n > 0) received += (size_t)n;
        else if (n == 0 || (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) break;
        else nap();
    }
    close(birth[0]);
    int reason = cancelled ? 2 : 0;
    int released = 0;
    char row[512];
    int n = snprintf(row, sizeof(row), "1 %ld %" PRIu64 " %ld %lu %" PRIu64 " %" PRIuMAX " %" PRIuMAX "\n",
        (long)parent, parent_ticks, (long)child, (unsigned long)getuid(), ticks,
        (uintmax_t)scope.st_dev, (uintmax_t)scope.st_ino);
    /* Startup identity persisted before the sole child's exec gate opens. */
    if (received != sizeof(ticks) || n <= 0 || (size_t)n >= sizeof(row)
            || record(dir, "started", row, (size_t)n) != 0) reason = 4;
#if defined(__ANDROID__) && !defined(HG_HOST_FIXTURE)
    if (!ticks || !parent_ticks) reason = 4;
#endif
    if (!reason && !cancelled && write_all(gate[1], "G", 1) == 0) released = 1;
    else if (!reason) reason = cancelled ? 2 : 4;
    close(gate[1]);
    int reaped = 0, status = 0, term = 0, killed = 0, eof = 0, ownership_error = 0;
    uint64_t run_start = now_ns(), escalation = 0, reap_deadline = 0;
    size_t bytes = 0;
    for (;;) {
        uint64_t now = now_ns();
#ifdef HG_HOST_FIXTURE
        if (!strcmp(mode, "clockfail")) now = 0;
#endif
        if (!now) {
            /* Clock failure must not strand an already released owned child
             * in a loop whose escalation deadlines can never advance. */
            reason = 4;
            if (!reaped) {
                int sent = owned_signal(child, reaped, SIGTERM);
                if (sent != 0 && errno != ESRCH) ownership_error = 1;
                term = 1;
                sent = owned_signal(child, reaped, SIGKILL);
                if (sent != 0 && errno != ESRCH) ownership_error = 1;
                killed = 1;
                for (int polls = 0; polls < 50; ++polls) {
                    pid_t waited = waitpid(child, &status, WNOHANG);
                    if (waited == child) { reaped = 1; break; }
                    if (waited < 0 && errno != EINTR) { ownership_error = 1; break; }
                    nap();
                }
            }
            if (!reaped) ownership_error = 1;
            (void)drain(output[0], logfd, &bytes, &eof);
            break;
        }
        if (!reaped) {
            pid_t waited = waitpid(child, &status, WNOHANG);
            if (waited == child) { reaped = 1; reap_deadline = now + REAP_MS * UINT64_C(1000000); }
            else if (waited < 0 && errno != EINTR) { ownership_error = 1; reason = 4; break; }
        }
        int drained = drain(output[0], logfd, &bytes, &eof);
        if (drained && !reason) reason = drained > 0 ? 3 : 4;
        if (cancelled && !reason) reason = 2;
        if (!reason && !reaped && now - run_start >= RUN_MS * UINT64_C(1000000)) reason = 1;
        if (reaped && eof) break;
        if (reaped && now >= reap_deadline) { if (!reason) reason = 4; break; }
        if (reason && !reaped && !term) {
            if (owned_signal(child, reaped, SIGTERM) != 0 && errno != ESRCH) { ownership_error = 1; break; }
            term = 1; escalation = now + GRACE_MS * UINT64_C(1000000);
        }
        if (term && !reaped && !killed && now >= escalation) {
            if (owned_signal(child, reaped, SIGKILL) != 0 && errno != ESRCH) { ownership_error = 1; break; }
            killed = 1; reap_deadline = now + REAP_MS * UINT64_C(1000000);
        }
        if (killed && !reaped && now >= reap_deadline) { ownership_error = 1; break; }
        nap();
    }
    close(output[0]);
    if (close(logfd) != 0) { if (!reason) reason = 4; }
    int kind = reaped && WIFEXITED(status) ? 1 : reaped && WIFSIGNALED(status) ? 2 : 0;
    int value = kind == 1 ? WEXITSTATUS(status) : kind == 2 ? WTERMSIG(status) : 0;
    uint64_t ended = now_ns();
    n = snprintf(row, sizeof(row), "1 %ld %" PRIu64 " %ld %lu %" PRIu64 " %" PRIuMAX " %" PRIuMAX
        " %d %d %d %d %d %d %d %zu %d %d %" PRIu64 "\n", (long)parent, parent_ticks,
        (long)child, (unsigned long)getuid(), ticks, (uintmax_t)scope.st_dev, (uintmax_t)scope.st_ino,
        reason, kind, value, term, killed, reaped, eof, bytes, released, ownership_error,
        ended >= began ? ended - began : 0);
    int saved = n > 0 && (size_t)n < sizeof(row) && record(dir, "waited", row, (size_t)n) == 0;
    close(dir);
    /* Preserve scope on all outcomes; the qualified caller does exact cleanup.
     * Main-child reap+pipe EOF does not assert descendant/service quiescence. */
    if (!saved || !reaped || !eof || ownership_error) return 72;
    return !reason && kind == 1 && value == 0 ? 0 : 73;
}

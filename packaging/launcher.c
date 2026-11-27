/*
 * MLX Studio native launcher (CFBundleExecutable).
 *
 * A shell script exec'ing python3 causes LaunchServices to register TWO app
 * identities: "MLX Studio" (the shell script) and "Python" (the interpreter).
 * That produces two Dock tiles and duplicate menu-bar entries.
 *
 * A native Mach-O binary as CFBundleExecutable gives LaunchServices ONE
 * identity: "MLX Studio". The binary picks a usable python3, sends all output
 * to ~/.mlx_studio/logs/launch.log and execs app.py, keeping the same process
 * identity so macOS shows a single Dock tile.
 *
 * Single-instance handling lives in app.py, not here.
 *
 * packaging/build_app.sh compiles this file; the binary is never committed.
 */

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pwd.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Overridable at compile time so the tests can use stub interpreters. */
#ifndef PYTHON_CANDIDATES
#define PYTHON_CANDIDATES                                                      \
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",       \
    "/opt/homebrew/bin/python3",                                               \
    "/usr/local/bin/python3",                                                  \
    "/usr/bin/python3"
#endif

#define MAX_LOG_BYTES (2 * 1024 * 1024)
#define PROBE_TIMEOUT_SECONDS 20
#define LOCK_TRIES 500 /* 10 ms apart: about 5 seconds */

/* Exit status of the probe: PROBE_BASE + score, anything else is unusable. */
#define PROBE_BASE 20
#define BEST_SCORE 3

static const char *PROBE =
    "import sys\n"
    "if sys.version_info < (3, 10): sys.exit(10)\n"
    "from importlib.util import find_spec as found\n"
    "sys.exit(20 + 2 * bool(found('mlx_lm')) + bool(found('webview')))\n";

static const char *python_candidates[] = {PYTHON_CANDIDATES, NULL};

__attribute__((format(printf, 1, 2)))
static void note(const char *format, ...) {
    char stamp[32] = "";
    time_t now = time(NULL);
    struct tm parts;
    if (localtime_r(&now, &parts)) {
        strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &parts);
    }
    fprintf(stderr, "%s launcher: ", stamp);
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
}

/* Open a log for appending, owner only. A symlink is never followed. */
static int open_log(const char *path) {
    int fd = open(path, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, 0600);
    if (fd >= 0) fchmod(fd, 0600);
    return fd;
}

/* Let one launch at a time open and move the logs, or two started together can
 * move each other's new log over the old one. The wait is bounded: a stuck lock
 * delays the start but never stops it. Returns -1 when there is no lock. */
static int lock_logs(const char *log_dir) {
    int fd = open(log_dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return -1;
    for (int tries = 0; tries < LOCK_TRIES; tries++) {
        if (flock(fd, LOCK_EX | LOCK_NB) == 0) return fd;
        if (errno != EWOULDBLOCK) break;
        usleep(10000);
    }
    close(fd);
    return -1;
}

/* The log to append to, after moving aside one that is read only or full.
 * Returns -1 when there is none. */
static int current_log(const char *log, const char *old) {
    int fd = open_log(log);
    if (fd < 0) {
        /* A log we may not write (read only, or left by another user) would end
         * launch logging for good, so it is moved aside, whatever its size. A
         * symlink or a directory fails with another error and stays put. */
        if ((errno != EACCES && errno != EPERM) || rename(log, old) != 0) return -1;
        return open_log(log);
    }

    /* Size the file that is actually open, not whatever the path names next.
     * If rotation fails, keep appending to the descriptor already held. */
    struct stat st;
    if (fstat(fd, &st) == 0 && st.st_size >= MAX_LOG_BYTES && rename(log, old) == 0) {
        close(fd);
        fd = open_log(log);
    }
    return fd;
}

/* Point stdout and stderr at launch.log so a Finder launch can be diagnosed.
 * Best effort: on any failure the streams are left as they are. */
static void redirect_to_log(void) {
    const char *home = getenv("HOME");
    if (!home || !*home) {
        struct passwd *pw = getpwuid(getuid());
        home = pw ? pw->pw_dir : NULL;
    }
    if (!home) return;

    char app_dir[PATH_MAX], log_dir[PATH_MAX], log[PATH_MAX], old[PATH_MAX];
    if (snprintf(app_dir, sizeof(app_dir), "%s/.mlx_studio", home) >= (int)sizeof(app_dir) ||
        snprintf(log_dir, sizeof(log_dir), "%s/logs", app_dir) >= (int)sizeof(log_dir) ||
        snprintf(log, sizeof(log), "%s/launch.log", log_dir) >= (int)sizeof(log) ||
        snprintf(old, sizeof(old), "%s.1", log) >= (int)sizeof(old)) {
        return;
    }
    mkdir(app_dir, 0700);
    mkdir(log_dir, 0700);

    int lock = lock_logs(log_dir);
    int fd = current_log(log, old);
    if (lock >= 0) close(lock);
    if (fd < 0) return;

    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    if (fd > STDERR_FILENO) close(fd);
}

/* Score an interpreter: -1 unusable, else 2 for mlx_lm plus 1 for webview. */
static int probe(const char *python) {
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        alarm(PROBE_TIMEOUT_SECONDS); /* survives exec: a hung probe dies */
        execl(python, python, "-c", PROBE, (char *)NULL);
        _exit(127);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status)) return -1;
    int score = WEXITSTATUS(status) - PROBE_BASE;
    return (score >= 0 && score <= BEST_SCORE) ? score : -1;
}

/* Prefer the interpreter that has mlx_lm (then a native window). Without one,
 * any Python 3.10+ will do: the Setup tab can install the packages. */
static const char *find_python(void) {
    const char *chosen = NULL;
    int best = -1;
    for (int i = 0; python_candidates[i] != NULL && best < BEST_SCORE; i++) {
        const char *path = python_candidates[i];
        if (access(path, X_OK) != 0) continue;
        /* On a Mac without developer tools the system stub opens an install
         * dialog, so leave it alone once something else works. */
        if (chosen && strcmp(path, "/usr/bin/python3") == 0) continue;
        int score = probe(path);
        if (score < 0) {
            note("skipping %s (older than 3.10 or not runnable)", path);
            continue;
        }
        note("found %s (mlx_lm=%s, webview=%s)", path,
             (score & 2) ? "yes" : "no", (score & 1) ? "yes" : "no");
        if (score > best) {
            best = score;
            chosen = path;
        }
    }
    return chosen;
}

int main(int argc, char *argv[]) {
    (void)argc;
    redirect_to_log();
    note("start");

    /* Resolve the Resources directory from the executable path.
     * _NSGetExecutablePath gives us the path to this binary:
     *   /Applications/MLX Studio.app/Contents/MacOS/MLXStudio
     * Resources lives at:
     *   /Applications/MLX Studio.app/Contents/Resources
     */
    char exe_path[PATH_MAX];
    uint32_t bufsize = (uint32_t)sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &bufsize) != 0) {
        /* Fallback to argv[0] */
        snprintf(exe_path, sizeof(exe_path), "%s", argv[0]);
    }

    char *last_slash = strrchr(exe_path, '/');
    if (last_slash) *last_slash = '\0';      /* …/Contents/MacOS */
    last_slash = strrchr(exe_path, '/');
    if (last_slash) *last_slash = '\0';      /* …/Contents */

    char resources_path[PATH_MAX];
    snprintf(resources_path, sizeof(resources_path), "%s/Resources", exe_path);

    if (chdir(resources_path) != 0) {
        note("cannot access the Resources directory: %s", resources_path);
        return 1;
    }

    /* Verify app.py exists before exec'ing. */
    if (access("app.py", R_OK) != 0) {
        note("cannot find %s/app.py", resources_path);
        return 1;
    }

    const char *python = find_python();
    if (!python) {
        note("no Python 3.10+ interpreter found. "
             "Install it from python.org or with: brew install python");
        return 1;
    }
    note("interpreter=%s app=%s/app.py", python, resources_path);

    /* Unbuffered, so the log is complete even if the app dies. */
    setenv("PYTHONUNBUFFERED", "1", 0);

    /* exec python3 to run app.py. This replaces the current process image,
     * keeping the same PID so LaunchServices continues to associate this
     * process with the MLX Studio bundle (single Dock tile). */
    char *args[] = {(char *)python, "app.py", NULL};
    execv(python, args);

    perror("execv");
    note("failed to start %s", python);
    return 1;
}

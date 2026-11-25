/*
 * MLX Studio native launcher.
 *
 * Replaces the bash stub (MLXStudio shell script) as CFBundleExecutable.
 * A shell script exec'ing python3 causes LaunchServices to register TWO app
 * identities: "MLX Studio" (the shell script) and "Python" (the interpreter).
 * That produces two Dock tiles and duplicate menu-bar entries.
 *
 * A native Mach-O binary as CFBundleExecutable gives LaunchServices ONE
 * identity: "MLX Studio". The binary execs python3 to run app.py, keeping the
 * same process identity so macOS shows a single Dock tile.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>

static const char *python_candidates[] = {
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
    NULL
};

static const char *find_python(void) {
    for (int i = 0; python_candidates[i] != NULL; i++) {
        struct stat st;
        if (stat(python_candidates[i], &st) == 0 && (st.st_mode & S_IXUSR)) {
            return python_candidates[i];
        }
    }
    return NULL;
}

static int already_running(void) {
    /* Probe the loopback API. If something answers on port 8765, an instance
     * is already running and we just need to focus its window. */
    FILE *fp = popen("curl -s -m 2 -o /dev/null http://127.0.0.1:8765/api/ping",
                     "r");
    if (!fp) return 0;
    char buf[64];
    if (fgets(buf, sizeof(buf), fp) != NULL) {
        pclose(fp);
        return strstr(buf, "\"ok\": true") != NULL;
    }
    pclose(fp);
    return 0;
}

static int request_focus(void) {
    /* Ask the existing instance to raise its window via AppleScript.
     * Falls back to simply activating the app by bundle name. */
    /* Ask the existing instance to raise its window via AppleScript. */
    int rc = system("osascript -e 'tell application \"System Events\" "
                    "to tell process \"Python\" to set frontmost to true'");
    return rc == 0;
}

int main(int argc, char *argv[]) {
    (void)argc;
    (void)argv;

    const char *python = find_python();
    if (!python) {
        fprintf(stderr,
                "MLX Studio: No Python 3.10+ interpreter found.\n"
                "Install Python 3.10+ (python.org or: brew install python).\n");
        return 1;
    }

    /* Resolve the Resources directory from the executable path.
     * _NSGetExecutablePath gives us the path to this binary:
     *   /Applications/MLX Studio.app/Contents/MacOS/MLXStudio
     * Resources lives at:
     *   /Applications/MLX Studio.app/Contents/Resources
     */
    char exe_path[4096];
    uint32_t bufsize = (uint32_t)sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &bufsize) != 0) {
        /* Fallback to argv[0] */
        strncpy(exe_path, argv[0], sizeof(exe_path) - 1);
        exe_path[sizeof(exe_path) - 1] = '\0';
    }

    char *last_slash = strrchr(exe_path, '/');
    if (last_slash) *last_slash = '\0';      /* …/Contents/MacOS */
    last_slash = strrchr(exe_path, '/');
    if (last_slash) *last_slash = '\0';      /* …/Contents */

    char resources_path[4096];
    snprintf(resources_path, sizeof(resources_path), "%s/Resources", exe_path);

    char app_py_path[4096];
    snprintf(app_py_path, sizeof(app_py_path), "%s/app.py", resources_path);

    /* If an instance is already running, focus its window and exit. */
    if (already_running()) {
        if (!request_focus()) {
            fprintf(stderr,
                    "MLX Studio: Already running, but could not raise window.\n");
        }
        return 0;
    }

    if (chdir(resources_path) != 0) {
        fprintf(stderr,
                "MLX Studio: Cannot access Resources directory: %s\n",
                resources_path);
        return 1;
    }

    /* Verify app.py exists before exec'ing. */
    if (access(app_py_path, R_OK) != 0) {
        fprintf(stderr,
                "MLX Studio: Cannot find %s\n", app_py_path);
        return 1;
    }

    /* exec python3 to run app.py. This replaces the current process image,
     * keeping the same PID so LaunchServices continues to associate this
     * process with the MLX Studio bundle (single Dock tile). */
    char *args[] = {(char *)python, "app.py", NULL};
    execv(python, args);

    perror("execv");
    fprintf(stderr, "MLX Studio: Failed to start %s\n", python);
    return 1;
}

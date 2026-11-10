/* SPDX-License-Identifier: MIT */
/* Explicit reboot target for the A22x recovery supervisor. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/syscall.h>
#include <sys/stat.h>
#include <unistd.h>

static bool recovery_context(void)
{
    char line[8192], *save = NULL;
    FILE *f = fopen("/proc/cmdline", "r");
    if (!f) return false;
    bool got = fgets(line, sizeof(line), f) != NULL;
    fclose(f);
    if (!got) return false;
    for (char *s = strtok_r(line, " \t\r\n", &save); s; s = strtok_r(NULL, " \t\r\n", &save))
        if (!strcmp(s, "a22x.recovery=1")) return true;
    return false;
}

static bool unmounted_filesystems(void)
{
    FILE *f = fopen("/proc/self/mountinfo", "r");
    if (!f) return false;
    char *line = NULL;
    size_t capacity = 0;
    bool ok = true;
    while (getline(&line, &capacity, f) >= 0) {
        unsigned major_num, minor_num;
        char root[1024], mountpoint[1024];
        if (sscanf(line, "%*u %*u %u:%u %1023s %1023s", &major_num, &minor_num, root, mountpoint) != 4) {
            ok = false;
            break;
        }
        if (major_num != 0 || !strncmp(mountpoint, "/mnt/", 5)) {
            fprintf(stderr, "Filesystem still mounted at %s (%u:%u)\n", mountpoint, major_num, minor_num);
            ok = false;
            break;
        }
    }
    if (ferror(f)) ok = false;
    free(line);
    fclose(f);
    return ok;
}

static bool transitions_clear(void)
{
    struct stat state;
    const char *paths[] = {
        "/run/a22x-recovery/distro-transaction.json",
        "/run/a22x-recovery/install-transaction.json"
    };
    const char *messages[] = {
        "A distribution switch needs recovery. Open Systems and finish or roll back the transaction.\n",
        "An image installation needs recovery. Open Install images and review its pending transaction.\n"
    };
    for (size_t i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) {
        if (lstat(paths[i], &state) < 0 && errno == ENOENT) continue;
        fputs(messages[i], stderr);
        return false;
    }
    return true;
}

static int operation_lease(void)
{
    const char *path = "/run/lock/a22x-mainline-recovery.lock";
    const char *inherited = getenv("A22X_RECOVERY_LOCK_FD");
    int fd = -1;
    if (inherited) {
        char *end = NULL;
        errno = 0;
        long number = strtol(inherited, &end, 10);
        if (errno || end == inherited || *end || number < 3 || number > INT_MAX) return -1;
        fd = fcntl((int)number, F_DUPFD_CLOEXEC, 3);
    } else {
        fd = open(path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    }
    if (fd < 0) return -1;
    struct stat opened, named;
    if (fstat(fd, &opened) || lstat(path, &named) || !S_ISREG(opened.st_mode) ||
        !S_ISREG(named.st_mode) || opened.st_nlink != 1 || named.st_nlink != 1 ||
        opened.st_dev != named.st_dev || opened.st_ino != named.st_ino ||
        flock(fd, LOCK_EX | LOCK_NB)) {
        close(fd);
        return -1;
    }
    // Closing a duplicated inherited descriptor must not explicitly unlock
    // the SSH session's shared open-file description.
    return fd;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("recovery-power normal|recovery|download|poweroff|--check");
        return 0;
    }
    if (argc != 2 || (strcmp(argv[1], "normal") && strcmp(argv[1], "recovery") &&
                      strcmp(argv[1], "download") && strcmp(argv[1], "poweroff") && strcmp(argv[1], "--check"))) {
        fputs("An explicit power target is required.\n", stderr);
        return 64;
    }
    if (geteuid() != 0 || !recovery_context()) {
        fputs("A22x RAM recovery is required.\n", stderr);
        return 1;
    }
    int lock = operation_lease();
    if (lock < 0) {
        fputs("A recovery operation is still running.\n", stderr);
        if (lock >= 0) close(lock);
        return 1;
    }
    if (!strcmp(argv[1], "normal") && !transitions_clear()) {
        close(lock);
        return 1;
    }
    if (!unmounted_filesystems()) {
        close(lock);
        return 1;
    }
    if (!strcmp(argv[1], "--check")) {
        puts("POWER_PREFLIGHT_OK");
        close(lock);
        return 0;
    }
    sync();
    if (!strcmp(argv[1], "poweroff"))
        syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_POWER_OFF, NULL);
    else
        syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_RESTART2, argv[1]);
    perror("reboot syscall returned");
    close(lock);
    return 1;
}

/* SPDX-License-Identifier: MIT
 * RAM-only acceptance executable placed at /umtprd in a disposable test jail.
 * It does not implement USB, inspect user data or open any block device.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static void check(int ok, const char *stage)
{
    if (!ok) { fprintf(stderr, "sandbox probe failed: %s (errno=%d)\n", stage, errno); exit(1); }
}
static void *thread_check(void *unused)
{
    (void)unused;
    char data[5] = {0};
    int fd = open("/files/owned", O_RDONLY);
    check(fd >= 0 && read(fd, data, 5) == 5 && !memcmp(data, "owned", 5), "thread read");
    close(fd);
    return NULL;
}
int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--version")) { puts("a22x-mtp-sandbox-probe 1"); return 0; }
    for (int fd = 4; fd < 128; ++fd)
        check(fcntl(fd, F_GETFD) < 0 && errno == EBADF, "outside descriptor closed");
    struct stat lease;
    check(!fstat(3, &lease) && S_ISREG(lease.st_mode), "lease inherited");
    check(flock(3, LOCK_UN) < 0 && errno == EPERM, "lease cannot be unlocked");
    check(prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1, "no_new_privs");
    check(prctl(PR_GET_SECCOMP, 0, 0, 0, 0) == 2, "seccomp filter");
    check(getgroups(0, NULL) == 0, "supplemental groups cleared");
    struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct caps[2] = {{0}};
    check(!syscall(SYS_capget, &header, caps), "capability query");
    check(caps[0].effective == ((1U << CAP_DAC_OVERRIDE) | (1U << CAP_FOWNER)) &&
          !caps[1].effective && caps[0].permitted == caps[0].effective && !caps[1].permitted,
          "only filesystem access capabilities");
    check(open("/proc/self/status", O_RDONLY) < 0 && errno == ENOENT, "no proc");
    check(open("/etc/passwd", O_RDONLY) < 0 && errno == ENOENT, "no host root");
    check(open("/files/outside", O_RDONLY) < 0, "absolute symlink cannot leave jail");
    check(open("/umtprd.conf", O_WRONLY) < 0 && errno == EROFS, "immutable configuration");
    check(chroot("/files") < 0 && errno == EPERM, "cannot chroot again");
    check(mount("tmpfs", "/files", "tmpfs", 0, NULL) < 0 && errno == EPERM, "cannot mount");
    check(unshare(CLONE_NEWNS) < 0 && errno == EPERM, "cannot create namespaces");
    check(socket(AF_UNIX, SOCK_STREAM, 0) < 0 && errno == EPERM, "cannot reach control IPC");
    check(socket(AF_INET, SOCK_STREAM, 0) < 0 && errno == EPERM, "cannot reach network");
    check(fork() < 0 && errno == EPERM, "cannot fork");
    pthread_t thread;
    check(!pthread_create(&thread, NULL, thread_check, NULL), "threads remain usable");
    check(!pthread_join(thread, NULL), "thread join");
    char mode[3] = {0};
    int config = open("/umtprd.conf", O_RDONLY);
    check(config >= 0 && read(config, mode, 2) == 2, "read test configuration");
    close(config);
    int fd = open("/files/created", O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (!strcmp(mode, "ro")) check(fd < 0 && errno == EROFS, "read-only bind");
    else {
        check(fd >= 0 && write(fd, "check", 5) == 5 && !fsync(fd), "writable owned RAM file");
        close(fd);
    }
    char *const args[] = {"/files/executable", "--version", NULL};
    execv(args[0], args);
    check(errno == EACCES, "noexec export");
    puts("{\"ok\":true,\"confinement\":true,\"lease_protected\":true,\"capabilities\":10,\"threads\":true}");
    return 0;
}

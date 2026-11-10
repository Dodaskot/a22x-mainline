/* SPDX-License-Identifier: MIT
 * Namespace launcher for the qualified static, fixed-export uMTP responder.
 * Invoked only by usb_files.py with pinned descriptors and a fresh RAM jail.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/magic.h>
#include <linux/seccomp.h>
#include <sched.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>

static void die(const char *what) { perror(what); _exit(111); }
static void require(int ok, const char *what) { if (!ok) { errno = EPERM; die(what); } }
static int number(const char *text)
{
    char *end; errno = 0;
    long n = strtol(text, &end, 10);
    require(!errno && *text && !*end && n >= 3 && n <= 65535, "invalid descriptor");
    return (int)n;
}
static void limit(int resource, rlim_t value)
{
    struct rlimit lim = {value, value};
    if (setrlimit(resource, &lim)) die("setrlimit");
}
static int clone_fd(int fd)
{
    /* An inherited FD retains its old mount namespace across unshare().
     * Clone its exact directory into a detached, nonrecursive mount before
     * unshare, so later attachment never reopens a mutable source pathname. */
    int tree = (int)syscall(SYS_open_tree, fd, "",
                           AT_EMPTY_PATH | OPEN_TREE_CLONE | OPEN_TREE_CLOEXEC);
    if (tree < 0) die("clone pinned directory");
    return tree;
}
static void attach_tree(int tree, const char *target, unsigned long flags)
{
    if (syscall(SYS_move_mount, tree, "", AT_FDCWD, target, MOVE_MOUNT_F_EMPTY_PATH))
        die("attach pinned tree");
    if (mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | flags, NULL)) die("restrict bind mount");
    if (close(tree)) die("close attached tree");
}

#define DENY(nr) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_##nr, 0, 1), \
                 BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM)
static void restrict_process(void)
{
    /* The executable sees the selected filesystem as an administrator, but
     * cannot mount, chroot, create device nodes, escape namespaces, inspect
     * another process or connect to host control sockets. */
    const unsigned long caps = (1UL << CAP_DAC_OVERRIDE) | (1UL << CAP_FOWNER);
    for (int i = 0; i < 64; ++i) {
        if (caps & (1UL << i)) continue;
        if (prctl(PR_CAPBSET_DROP, i, 0, 0, 0) && errno != EINVAL) die("drop bounding capability");
    }
    struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2] = {{0}};
    data[0].effective = data[0].permitted = (unsigned int)caps;
    if (syscall(SYS_capset, &header, data)) die("capset");
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) die("no_new_privs");
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)) die("dumpable");
#if defined(__aarch64__)
    const unsigned int expected_arch = AUDIT_ARCH_AARCH64;
#elif defined(__x86_64__)
    const unsigned int expected_arch = AUDIT_ARCH_X86_64;
#else
#error Unsupported syscall ABI
#endif
    const struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, expected_arch, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        DENY(mount), DENY(umount2), DENY(pivot_root), DENY(chroot),
        DENY(open_tree), DENY(move_mount),
        DENY(unshare), DENY(setns), DENY(reboot), DENY(ptrace),
        DENY(process_vm_readv), DENY(process_vm_writev), DENY(socket),
        DENY(kill), DENY(tkill), DENY(rt_sigqueueinfo), DENY(rt_tgsigqueueinfo), DENY(flock),
        DENY(bpf), DENY(io_uring_setup), DENY(open_by_handle_at),
#ifdef __NR_pidfd_open
        DENY(pidfd_open), DENY(pidfd_getfd), DENY(pidfd_send_signal),
#endif
#ifdef __NR_fork
        DENY(fork), DENY(vfork),
#endif
        /* glibc may try clone3 before clone for pthread creation. */
#ifdef __NR_clone3
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone3, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOSYS),
#endif
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone, 0, 3),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, CLONE_THREAD, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        /* pthread_kill is used by upstream during shutdown. Only own threads. */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_tgkill, 0, 3),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (unsigned int)getpid(), 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)
    };
    struct sock_fprog program = {sizeof(filter) / sizeof(filter[0]), (struct sock_filter *)filter};
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) die("seccomp");
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--version")) {
        puts("a22x-mtp-sandbox 2"); return 0;
    }
    require(argc == 6 && geteuid() == 0, "sandbox arguments/root");
    const char *root = argv[1];
    require(!strncmp(root, "/run/a22x-recovery/usb-files/session-", 37) &&
            !strstr(root, "/../") && strlen(root) < PATH_MAX - 16, "RAM jail path");
    int source = number(argv[2]), ffs = number(argv[3]), lease = number(argv[4]);
    require(source != ffs && source != lease && ffs != lease, "distinct descriptors");
    int readonly = !strcmp(argv[5], "ro");
    require(readonly || !strcmp(argv[5], "rw"), "export access");
    struct stat root_stat, source_stat, ffs_stat, lock_stat;
    require(!lstat(root, &root_stat) && S_ISDIR(root_stat.st_mode) && root_stat.st_uid == 0 &&
            !(root_stat.st_mode & 0022), "private root directory");
    require(!fstat(source, &source_stat) && S_ISDIR(source_stat.st_mode), "pinned source directory");
    require(!fstat(ffs, &ffs_stat) && S_ISDIR(ffs_stat.st_mode), "pinned FunctionFS directory");
    require(!fstat(lease, &lock_stat) && S_ISREG(lock_stat.st_mode) && lock_stat.st_nlink == 1 &&
            lock_stat.st_uid == 0 && !flock(lease, LOCK_EX | LOCK_NB), "inherited operation lease");
    struct statfs root_fs;
    require(!statfs(root, &root_fs) && root_fs.f_type == TMPFS_MAGIC, "RAM root filesystem");
    pid_t supervisor = getppid();
    require(supervisor > 1, "live supervisor");
    if (prctl(PR_SET_PDEATHSIG, SIGKILL)) die("parent death signal");
    require(getppid() == supervisor, "supervisor disappeared");
    int source_tree = clone_fd(source), ffs_tree = clone_fd(ffs);
    if (unshare(CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWNET)) die("private namespaces");
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL)) die("private mount propagation");
    if (mount(root, root, NULL, MS_BIND, NULL)) die("bind jail root");
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/files", root);
    attach_tree(source_tree, path, MS_NODEV | MS_NOSUID | MS_NOEXEC | (readonly ? MS_RDONLY : 0));
    snprintf(path, sizeof(path), "%s/usb", root);
    attach_tree(ffs_tree, path, MS_NODEV | MS_NOSUID | MS_NOEXEC);
    if (mount(NULL, root, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, NULL))
        die("immutable jail root");
    if (chdir(root) || chroot(".") || chdir("/")) die("enter jail");
    if (setgroups(0, NULL)) die("clear supplemental groups");
    /* Preserve only the exact storage lease, never outside directory FDs. */
    if (lease != 3 && dup2(lease, 3) < 0) die("move operation lease");
    if (fcntl(3, F_SETFD, 0)) die("inherit operation lease");
    if (syscall(SYS_close_range, 4U, ~0U, 0)) die("close outside descriptors");
    limit(RLIMIT_CORE, 0);
    limit(RLIMIT_NOFILE, 128);
    limit(RLIMIT_AS, 256 * 1024 * 1024);
    limit(RLIMIT_MSGQUEUE, 128 * 1024);
    restrict_process();
    char *const args[] = {"/umtprd", "-conf", "/umtprd.conf", NULL};
    char *const env[] = {"PATH=/", "LC_ALL=C", "LANG=C", NULL};
    execve(args[0], args, env);
    die("exec static responder");
}

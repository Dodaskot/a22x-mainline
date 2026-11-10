/* SPDX-License-Identifier: GPL-3.0-or-later
 * Fixed-export filesystem boundary for the pinned uMTP-Responder build.
 * The launcher additionally gives the responder a private namespace/chroot.
 * No fallback to ordinary pathname resolution is allowed if openat2 fails.
 */
#define _GNU_SOURCE
#define _LARGEFILE64_SOURCE
#define _FILE_OFFSET_BITS 64
#include "mtp_guard.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/openat2.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef A22X_EXPORT_ROOT
#define A22X_EXPORT_ROOT "/files"
#endif

int a22x_name_ok(const char *name)
{
    size_t n = name ? strlen(name) : 0;
    if (!n || n > NAME_MAX || !strcmp(name, ".") || !strcmp(name, "..") ||
        strchr(name, '/') || strchr(name, '\\')) { errno = EINVAL; return 0; }
    return 1;
}

static const char *relative_path(const char *path)
{
    size_t n = strlen(A22X_EXPORT_ROOT);
    if (!path || strncmp(path, A22X_EXPORT_ROOT, n) ||
        (path[n] && path[n] != '/') || strlen(path) >= PATH_MAX) {
        errno = EACCES; return NULL;
    }
    path += n;
    while (*path == '/') ++path;
    return *path ? path : ".";
}

static int beneath(const char *path, int flags, mode_t mode)
{
    const char *rel = relative_path(path);
    if (!rel) return -1;
    int root = open(A22X_EXPORT_ROOT, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (root < 0) return -1;
    struct open_how how = {
        .flags = (unsigned int)(flags | O_CLOEXEC | O_NOFOLLOW),
        .mode = (flags & O_CREAT) ? mode : 0,
        .resolve = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS |
                   RESOLVE_NO_MAGICLINKS | RESOLVE_NO_XDEV
    };
    int fd = syscall(SYS_openat2, root, rel, &how, sizeof(how));
    int error = errno;
    close(root);
    errno = error;
    return fd;
}

static int parent(const char *path, char name[NAME_MAX + 1])
{
    const char *rel = relative_path(path);
    if (!rel || !strcmp(rel, ".")) { errno = EACCES; return -1; }
    char full[PATH_MAX];
    if (strlen(path) >= sizeof(full)) { errno = ENAMETOOLONG; return -1; }
    strcpy(full, path);
    char *last = strrchr(full, '/');
    if (!last || !a22x_name_ok(last + 1)) return -1;
    strcpy(name, last + 1);
    *last = 0;
    return beneath(full, O_PATH | O_DIRECTORY, 0);
}

int a22x_open(const char *path, int flags, mode_t mode)
{
    /* Inspect the actual inode before a truncation can change it. Nonblocking
     * prevents a cached handle changed to a FIFO from hanging the service. */
    int fd = beneath(path, (flags & ~O_TRUNC) | O_NONBLOCK, mode);
    if (fd < 0) return -1;
    struct stat64 st;
    if (fstat64(fd, &st) || !S_ISREG(st.st_mode)) {
        int error = errno ? errno : EACCES;
        close(fd); errno = error; return -1;
    }
    if ((flags & O_TRUNC) && ftruncate64(fd, 0)) {
        int error = errno; close(fd); errno = error; return -1;
    }
    return fd;
}

DIR *a22x_opendir(const char *path)
{
    int fd = beneath(path, O_RDONLY | O_DIRECTORY, 0);
    if (fd < 0) return NULL;
    DIR *dir = fdopendir(fd);
    if (!dir) { int error = errno; close(fd); errno = error; }
    return dir;
}

int a22x_stat(const char *path, struct stat64 *out)
{
    int fd = beneath(path, O_PATH, 0);
    if (fd < 0) return -1;
    int ret = fstat64(fd, out);
    if (!ret && !S_ISDIR(out->st_mode) && !S_ISREG(out->st_mode)) {
        errno = EACCES; ret = -1;
    }
    int error = errno; close(fd); errno = error;
    return ret;
}

int a22x_mkdir(const char *path, mode_t mode)
{
    char name[NAME_MAX + 1];
    int fd = parent(path, name);
    if (fd < 0) return -1;
    int ret = mkdirat(fd, name, mode), error = errno;
    close(fd); errno = error; return ret;
}

int a22x_remove(const char *path)
{
    char name[NAME_MAX + 1]; struct stat64 st;
    int fd = parent(path, name);
    if (fd < 0) return -1;
    int ret = fstatat64(fd, name, &st, AT_SYMLINK_NOFOLLOW);
    if (!ret) {
        /* Never remove symlinks/special nodes hidden from the PC, including
         * when they occur inside a requested recursive directory deletion. */
        if (!S_ISREG(st.st_mode) && !S_ISDIR(st.st_mode)) {
            errno = EACCES; ret = -1;
        } else ret = unlinkat(fd, name, S_ISDIR(st.st_mode) ? AT_REMOVEDIR : 0);
    }
    int error = errno; close(fd); errno = error; return ret;
}

int a22x_rename(const char *source, const char *target)
{
    char source_name[NAME_MAX + 1], target_name[NAME_MAX + 1];
    int src = parent(source, source_name);
    if (src < 0) return -1;
    int dst = parent(target, target_name);
    if (dst < 0) { int error = errno; close(src); errno = error; return -1; }
    struct stat64 st;
    int ret = fstatat64(src, source_name, &st, AT_SYMLINK_NOFOLLOW);
    if (!ret && !S_ISREG(st.st_mode) && !S_ISDIR(st.st_mode)) { errno = EACCES; ret = -1; }
    if (!ret && !fstatat64(dst, target_name, &st, AT_SYMLINK_NOFOLLOW) &&
        !S_ISREG(st.st_mode) && !S_ISDIR(st.st_mode)) { errno = EACCES; ret = -1; }
    if (!ret) ret = renameat(src, source_name, dst, target_name);
    int error = errno; close(dst); close(src); errno = error; return ret;
}

int a22x_truncate(const char *path, off64_t length)
{
    if (length < 0) { errno = EINVAL; return -1; }
    int fd = a22x_open(path, O_WRONLY, 0);
    if (fd < 0) return -1;
    int ret = ftruncate64(fd, length);
    if (!ret) ret = fsync(fd);
    int error = errno;
    if (close(fd) && !ret) { error = errno; ret = -1; }
    errno = error; return ret;
}

int a22x_write_all(int fd, const void *data, size_t length)
{
    const unsigned char *bytes = data;
    while (length) {
        ssize_t done = write(fd, bytes, length);
        if (done < 0 && errno == EINTR) continue;
        if (done <= 0) { if (!done) errno = EIO; return -1; }
        bytes += done; length -= (size_t)done;
    }
    return 0;
}

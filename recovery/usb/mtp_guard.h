/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef A22X_MTP_GUARD_H
#define A22X_MTP_GUARD_H
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <stddef.h>
int a22x_name_ok(const char *name);
int a22x_open(const char *path, int flags, mode_t mode);
DIR *a22x_opendir(const char *path);
int a22x_stat(const char *path, struct stat64 *out);
int a22x_mkdir(const char *path, mode_t mode);
int a22x_remove(const char *path);
int a22x_rename(const char *source, const char *target);
int a22x_truncate(const char *path, off64_t length);
int a22x_write_all(int fd, const void *data, size_t length);
#endif

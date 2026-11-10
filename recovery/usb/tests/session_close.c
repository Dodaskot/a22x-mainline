/* SPDX-License-Identifier: GPL-3.0-or-later
 * Actual upstream allocator/teardown regression. Disposable regular FDs only.
 */
#include "buildconf.h"
#include <inttypes.h>
#include <pthread.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include "mtp.h"

static void *handles[HASH_TABLE_SIZE];
static int handle_arrays, freed_arrays;
void __real_free(void *ptr);
void __wrap_free(void *ptr)
{
    if (ptr) for (int i = 0; i < handle_arrays; ++i) if (handles[i] == ptr) ++freed_arrays;
    __real_free(ptr);
}
int inotify_handler_rmwatch(mtp_ctx *ctx, int wd) { (void)ctx; (void)wd; return 0; }

int main(void)
{
    mtp_ctx ctx;
    memset(&ctx, 0, sizeof(ctx)); ctx.sync_when_close = 1;
    fs_handles_db *db = init_fs_db(&ctx);
    if (!db) return 2;
    filefoundinfo first = {.isdirectory = 0}, second = {.isdirectory = 0};
    strcpy(first.filename, "first"); strcpy(second.filename, "second");
    fs_entry *a = add_entry(db, &first, 0, 1), *b = add_entry(db, &second, 0, 1);
    fs_entry *root = alloc_root_entry(db, 1);
    if (!a || !b || !root) return 3;
    int initialized = a->file_descriptor == -1 && b->file_descriptor == -1 && root->file_descriptor == -1;
    FILE *one = tmpfile(), *two = tmpfile();
    if (!one || !two) return 4;
    int afd = dup(fileno(one)), bfd = dup(fileno(two));
    if (afd < 0 || bfd < 0) return 5;
    a->file_descriptor = afd; b->file_descriptor = bfd;
    int stdin_copy = open("/dev/null", O_RDONLY);
    if (stdin_copy < 0 || dup2(stdin_copy, 0) < 0) return 6;
    if (stdin_copy != 0) close(stdin_copy);
    for (int i = 0; i < HASH_TABLE_SIZE; ++i)
        if (db->hash_table_by_handle[i].entries) handles[handle_arrays++] = db->hash_table_by_handle[i].entries;
    deinit_fs_db(db);
    int aclosed = fcntl(afd, F_GETFD) < 0 && errno == EBADF;
    int bclosed = fcntl(bfd, F_GETFD) < 0 && errno == EBADF;
    int stdin_open = fcntl(0, F_GETFD) >= 0;
    fclose(one); fclose(two);
    printf("{\"initialized_negative\":%s,\"all_open_entries_closed\":%s,\"stdin_preserved\":%s,\"handle_arrays\":%d,\"freed_arrays\":%d}\n",
           initialized ? "true" : "false", aclosed && bclosed ? "true" : "false",
           stdin_open ? "true" : "false", handle_arrays, freed_arrays);
    return initialized && aclosed && bclosed && stdin_open && handle_arrays == freed_arrays ? 0 : 1;
}

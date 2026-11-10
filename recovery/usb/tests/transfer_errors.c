/* SPDX-License-Identifier: GPL-3.0-or-later
 * Actual patched SendObject operation, with transport/handle plumbing stubbed.
 * No USB traffic; proves ENOSPC and a failed final flush cannot report success.
 */
#include "buildconf.h"
#include <inttypes.h>
#include <pthread.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "mtp.h"
#include "mtp_helpers.h"
#include "mtp_constant.h"
#include "mtp_operations.h"
#include "usb_gadget_fct.h"

static fs_entry entry;
static int selected_fd, close_error, seek_error;
off64_t __real_lseek64(int fd, off64_t offset, int whence);
off64_t __wrap_lseek64(int fd, off64_t offset, int whence)
{ if (seek_error) { errno = EIO; return -1; } return __real_lseek64(fd, offset, whence); }
fs_entry *get_entry_by_handle(fs_handles_db *db, uint32_t handle)
{ (void)db; (void)handle; return &entry; }
int check_handle_access(mtp_ctx *ctx, fs_entry *e, uint32_t h, int wr, uint32_t *response)
{ (void)ctx; (void)e; (void)h; (void)wr; (void)response; return 0; }
int entry_open(fs_handles_db *db, fs_entry *e, int flags, mode_t mode)
{ (void)db; (void)e; (void)flags; (void)mode; return selected_fd; }
int entry_close(fs_handles_db *db, fs_entry *e)
{ (void)db; (void)e; if (close_error) { errno = EIO; return -1; } return 0; }
int read_usb(usb_gadget *ctx, unsigned char *buffer, int maximum)
{ (void)ctx; (void)buffer; (void)maximum; errno = EIO; return -1; }
int mtp_get_storage_index_by_id(mtp_ctx *ctx, uint32_t storage_id)
{ (void)ctx; (void)storage_id; return 0; }

static uint32_t send_once(int64_t expected, int64_t offset, int partial, int bad_container)
{
    mtp_ctx ctx;
    memset(&ctx, 0, sizeof(ctx));
    fs_handles_db db;
    memset(&db, 0, sizeof(db));
    ctx.fs_db = &db;
    ctx.SendObjInfoHandle = 1;
    ctx.SendObjInfoSize = expected;
    ctx.SendObjInfoOffset = offset;
    ctx.usb_rd_buffer_max_size = 8192;
    pthread_mutex_init(&ctx.inotify_mutex, NULL);
    struct { MTP_PACKET_HEADER header; unsigned char bytes[5]; } packet;
    memset(&packet, 0, sizeof(packet));
    packet.header.operation = MTP_CONTAINER_TYPE_DATA;
    packet.header.code = partial ? MTP_OPERATION_SEND_PARTIAL_OBJECT : MTP_OPERATION_SEND_OBJECT;
    packet.header.length = expected == UINT32_MAX ? UINT32_MAX : (uint32_t)(sizeof(MTP_PACKET_HEADER) + expected);
    if (bad_container) packet.header.length++;
    memcpy(packet.bytes, "bytes", 5);
    int size = sizeof(MTP_PACKET_HEADER) + 5, params_size = 0;
    uint32_t params[4] = {0};
    uint32_t result = mtp_op_SendObject(&ctx, &packet.header, &size, params, &params_size);
    pthread_mutex_destroy(&ctx.inotify_mutex);
    return result;
}

int main(void)
{
    selected_fd = open("/dev/full", O_WRONLY);
    if (selected_fd < 0) return 2;
    uint32_t full = send_once(5, 0, 0, 0);
    close(selected_fd);
    FILE *file = tmpfile();
    if (!file) return 3;
    selected_fd = fileno(file);
    close_error = 1;
    uint32_t flush = send_once(5, 0, 0, 0);
    close_error = 0;
    uint32_t good = send_once(5, 0, 0, 0);
    uint32_t incomplete = send_once(10, 0, 0, 0);
    uint32_t overlong = send_once(2, 0, 0, 0);
    uint32_t bad_header = send_once(5, 0, 0, 1);
    uint32_t unknown_size = send_once(UINT32_MAX, 0, 0, 0);
    if (ftruncate(selected_fd, 0) || pwrite(selected_fd, "ORIGINAL", 8, 0) != 8) return 4;
    lseek(selected_fd, 2, SEEK_SET);
    uint32_t bad_seek = send_once(5, -1, 1, 0);
    seek_error = 1;
    uint32_t io_seek = send_once(5, 1, 1, 0);
    seek_error = 0;
    char original[8];
    int unchanged = pread(selected_fd, original, 8, 0) == 8 && !memcmp(original, "ORIGINAL", 8);
    fclose(file);
    int identity = set_giduid(NULL, getuid(), getgid());
    printf("{\"enospc\":%u,\"failed_flush\":%u,\"success\":%u,\"incomplete\":%u,\"overlong\":%u,\"bad_container\":%u,\"bad_seek\":%u,\"io_seek\":%u,\"unchanged\":%s,\"unknown_size\":%u,\"same_identity\":%s}\n",
           full, flush, good, incomplete, overlong, bad_header, bad_seek, io_seek, unchanged ? "true" : "false", unknown_size, !identity ? "true" : "false");
    return (full == MTP_RESPONSE_STORAGE_FULL && flush != MTP_RESPONSE_OK && good == MTP_RESPONSE_OK &&
            incomplete == MTP_RESPONSE_INCOMPLETE_TRANSFER && overlong == MTP_RESPONSE_INVALID_PARAMETER &&
            bad_header == MTP_RESPONSE_INVALID_PARAMETER && bad_seek == MTP_RESPONSE_INVALID_PARAMETER &&
            unchanged && io_seek != MTP_RESPONSE_OK && unknown_size == MTP_RESPONSE_OK && !identity) ? 0 : 1;
}

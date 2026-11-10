/* SPDX-License-Identifier: GPL-3.0-or-later
 * uMTP-Responder SendObject, derived from Viveris Technologies (2018-2025).
 * A22x fixed-export changes: checked offset/length, complete writes, honest
 * storage/flush/incomplete errors. A failed transfer can leave a partial file;
 * it is never reported as a complete successful transfer.
 */
#include "buildconf.h"
#include <inttypes.h>
#include <pthread.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <errno.h>
#include <limits.h>
#include "mtp.h"
#include "mtp_helpers.h"
#include "mtp_constant.h"
#include "mtp_operations.h"
#include "usb_gadget_fct.h"
#include "mtp_guard.h"

uint32_t mtp_op_SendObject(mtp_ctx *ctx, MTP_PACKET_HEADER *header, int *size,
                           uint32_t *ret_params, int *ret_params_size)
{
    (void)ret_params; (void)ret_params_size;
    if (!ctx->fs_db) return MTP_RESPONSE_SESSION_NOT_OPEN;
    if (pthread_mutex_lock(&ctx->inotify_mutex)) return MTP_RESPONSE_GENERAL_ERROR;
    uint32_t response = MTP_RESPONSE_GENERAL_ERROR;
    int partial = header->code == MTP_OPERATION_SEND_PARTIAL_OBJECT;
    int header_size = (int)sizeof(MTP_PACKET_HEADER);
    if (*size < header_size || *size > ctx->usb_rd_buffer_max_size) {
        response = MTP_RESPONSE_INVALID_PARAMETER; goto done;
    }
    if (header->operation == MTP_CONTAINER_TYPE_COMMAND) {
        if (partial) {
            if (*size < header_size + 16 || header->length != (uint32_t)(header_size + 16)) {
                response = MTP_RESPONSE_INVALID_PARAMETER; goto done;
            }
            uint64_t offset = peek64(header, header_size + 4, 8);
            if (offset > INT64_MAX) { response = MTP_RESPONSE_INVALID_PARAMETER; goto done; }
            ctx->SendObjInfoHandle = peek(header, header_size, 4);
            ctx->SendObjInfoOffset = (mtp_offset)offset;
            ctx->SendObjInfoSize = peek(header, header_size + 12, 4);
        }
        if (!check_handle_access(ctx, NULL, ctx->SendObjInfoHandle, 1, &response))
            response = MTP_RESPONSE_NO_RESPONSE;
        goto done;
    }
    if (header->operation != MTP_CONTAINER_TYPE_DATA || ctx->SendObjInfoHandle == UINT32_MAX) {
        response = MTP_RESPONSE_INVALID_OBJECT_HANDLE; goto done;
    }
    /* Normal SendObjectInfo uses 0xffffffff for an unknown size (>4 GiB).
     * SendPartialObject's explicit byte count is always known. */
    int known = partial || ctx->SendObjInfoSize != UINT32_MAX;
    mtp_size remaining = ctx->SendObjInfoSize;
    if (remaining < 0 || ctx->SendObjInfoOffset < 0 ||
        (known && remaining > INT64_MAX - ctx->SendObjInfoOffset)) {
        response = MTP_RESPONSE_INVALID_PARAMETER; goto done;
    }
    uint32_t container_length = (known && remaining <= UINT32_MAX - (uint32_t)header_size)
                                ? (uint32_t)remaining + header_size : UINT32_MAX;
    int payload = *size - header_size;
    if (header->length != container_length || (known && payload > remaining)) {
        response = MTP_RESPONSE_INVALID_PARAMETER; goto done;
    }
    fs_entry *entry = get_entry_by_handle(ctx->fs_db, ctx->SendObjInfoHandle);
    if (!entry) { response = MTP_RESPONSE_INVALID_OBJECT_HANDLE; goto done; }
    if (check_handle_access(ctx, entry, 0, 1, &response)) goto done;
    int flags = partial ? O_RDWR | O_LARGEFILE : O_CREAT | O_WRONLY | O_TRUNC | O_LARGEFILE;
    int file = entry_open(ctx->fs_db, entry, flags, partial ? 0 : 0666);
    if (file < 0) { response = posix_to_mtp_errcode(errno); goto done; }
    int write_error = 0;
    uint32_t transfer_error = 0;
    ctx->transferring_file_data = 1;
    if (lseek64(file, ctx->SendObjInfoOffset, SEEK_SET) != ctx->SendObjInfoOffset)
        write_error = errno ? errno : EIO;
    unsigned char *data = (unsigned char *)header + header_size;
    int packet = payload;
    int first = 1;
    for (;;) {
        if (packet < 0) { transfer_error = MTP_RESPONSE_INCOMPLETE_TRANSFER; break; }
        if (known && packet > remaining) transfer_error = MTP_RESPONSE_INVALID_PARAMETER;
        if (!write_error && !transfer_error && packet && a22x_write_all(file, data, (size_t)packet))
            write_error = errno ? errno : EIO;
        if (known) remaining = packet > remaining ? 0 : remaining - packet;
        int more = packet == ctx->usb_rd_buffer_max_size - (first ? header_size : 0);
        if (partial && known && remaining > 0) more = 1;
        if (!more || ctx->cancel_req) break;
        first = 0;
        data = ctx->rdbuffer2;
        packet = read_usb(ctx->usb_ctx, data, ctx->usb_rd_buffer_max_size);
        if (packet > ctx->usb_rd_buffer_max_size) {
            transfer_error = MTP_RESPONSE_INVALID_PARAMETER; break;
        }
        /* A short USB packet terminates this transfer. Never turn a zero-byte
         * read with an incomplete partial request into an endless read loop. */
        if (!packet && known && remaining) {
            transfer_error = MTP_RESPONSE_INCOMPLETE_TRANSFER; break;
        }
    }
    if (known) ctx->SendObjInfoSize = remaining;
    if (known && remaining && !transfer_error) transfer_error = MTP_RESPONSE_INCOMPLETE_TRANSFER;
    struct stat64 st;
    if (!fstat64(file, &st)) entry->size = st.st_size;
    else if (!write_error) write_error = errno ? errno : EIO;
    ctx->transferring_file_data = 0;
    if (!partial && entry_close(ctx->fs_db, entry) && !write_error)
        write_error = errno ? errno : EIO;
    if (ctx->cancel_req) {
        ctx->cancel_req = 0;
        response = MTP_RESPONSE_NO_RESPONSE;
    } else response = write_error ? posix_to_mtp_errcode(write_error) :
                      (transfer_error ? transfer_error : MTP_RESPONSE_OK);
done:
    if (pthread_mutex_unlock(&ctx->inotify_mutex)) response = MTP_RESPONSE_GENERAL_ERROR;
    return response;
}

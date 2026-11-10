#!/usr/bin/env python3
"""Apply the reviewed A22x patch to a fresh, exact uMTP-Responder 1.8.1 tree.

Input source commit: 8f87f7b19034055e2665eaaf07f84a41aa5f25e0.
The upstream source is kept separately; this fails if a replacement is ambiguous.
"""
from pathlib import Path
import shutil
import sys


def patch(root):
    root = Path(root)
    own = Path(__file__).resolve().parent
    shutil.copy2(own / 'mtp_guard.c', root / 'src/mtp_guard.c')
    shutil.copy2(own / 'mtp_guard.h', root / 'inc/mtp_guard.h')

    def change(name, before, after, count=1):
        path = root / name
        text = path.read_text(errors='surrogateescape')
        if text.count(before) != count:
            raise RuntimeError(f'{name}: expected {count} copies of {before!r}, got {text.count(before)}')
        path.write_text(text.replace(before, after), errors='surrogateescape')

    for name in ('src/fs_handles_db.c', 'src/mtp.c', 'src/mtp_properties.c', 'src/mtp_datasets.c',
                 'src/mtp_operations/mtp_ops_helpers.c',
                 'src/mtp_operations/mtp_op_sendobject.c',
                 'src/mtp_operations/mtp_op_truncateobject.c'):
        change(name, '#include "buildconf.h"', '#include "buildconf.h"\n#include "mtp_guard.h"\n#include <errno.h>')

    name = 'src/fs_handles_db.c'
    change(name, 'opendir (folder)', 'a22x_opendir(folder)', 2)
    change(name, 'lstat64 (tmpstr, &fileStat)', 'a22x_stat(tmpstr, &fileStat)')
    change(name, 'stat64 (path, &fileStat)', 'a22x_stat(path, &fileStat)')
    change(name, 'stat64(path, &entrystat)', 'a22x_stat(path, &entrystat)')
    change(name, 'remove(tmpstr)', 'a22x_remove(tmpstr)')
    change(name, 'remove(folder)', 'a22x_remove(folder)')
    change(name, 'open(full_path, flags, mode)', 'a22x_open(full_path, flags, mode)')
    change(name, '\tentry = &db->pool_head->entries[--db->pool_free_count];\n\tmemset(entry, 0, sizeof(fs_entry));',
           '\tentry = &db->pool_head->entries[--db->pool_free_count];\n\tmemset(entry, 0, sizeof(fs_entry));\n\tentry->file_descriptor = -1;', 2)
    change(name, 'if (entry->file_descriptor > 0)', 'if (entry->file_descriptor >= 0)')
    change(name, '\t\tentry_close(fsh, fsh->entry_list);',
           '\t\tfor (fs_entry *entry = fsh->entry_list; entry; entry = entry->next)\n\t\t\tentry_close(fsh, entry);\n\t\tfor (int i = 0; i < HASH_TABLE_SIZE; ++i)\n\t\t\tfree(fsh->hash_table_by_handle[i].entries);')
    change(name, 'void entry_close(fs_handles_db * db, fs_entry * entry)\n{',
           'int entry_close(fs_handles_db * db, fs_entry * entry)\n{\n\tint ret = 0, saved = 0;')
    change(name, 'if(!entry || !db)\n\t\treturn;', 'if(!entry || !db)\n\t\treturn 0;')
    change(name, '''\t\tif (((mtp_ctx *)db->mtp_ctx)->sync_when_close)
\t\t\tfsync(entry->file_descriptor);

\t\tclose(entry->file_descriptor);''', '''\t\tif (((mtp_ctx *)db->mtp_ctx)->sync_when_close &&
\t\t    (fcntl(entry->file_descriptor, F_GETFL) & O_ACCMODE) != O_RDONLY &&
\t\t    fsync(entry->file_descriptor)) { ret = -1; saved = errno; }
\t\tif (close(entry->file_descriptor) && !ret) { ret = -1; saved = errno; }''')
    change(name, '\tentry->file_descriptor = -1;\n}\n\nfs_entry * get_entry_by_wd',
           '\tentry->file_descriptor = -1;\n\tif (ret) errno = saved;\n\treturn ret;\n}\n\nfs_entry * get_entry_by_wd')
    change('inc/fs_handles_db.h', 'void entry_close(', 'int entry_close(')

    name = 'src/mtp.c'
    change(name, 'dataset_ptr = (datain + sizeof(MTP_PACKET_HEADER));',
           '''if (size < (int)sizeof(MTP_PACKET_HEADER) + 0x35)
\t\treturn MTP_RESPONSE_INVALID_PARAMETER;
\tdataset_ptr = (datain + sizeof(MTP_PACKET_HEADER));
\tunsigned int a22x_chars = peek(dataset_ptr, 0x34, 1);
\tif (!a22x_chars || a22x_chars > 255 ||
\t    size < (int)sizeof(MTP_PACKET_HEADER) + 0x35 + (int)a22x_chars * 2)
\t\treturn MTP_RESPONSE_INVALID_PARAMETER;''')
    change(name, 'unicode2charstring(tmp_str, unicode_str, sizeof(tmp_str));',
           'unicode2charstring(tmp_str, unicode_str, sizeof(tmp_str));\n\t\t\t\tif (!a22x_name_ok(tmp_str)) return MTP_RESPONSE_INVALID_PARAMETER;', 2)
    change(name, 'mkdir(tmp_path, 0777)', 'a22x_mkdir(tmp_path, 0777)')
    change(name, 'file = open(tmp_path,', 'file = a22x_open(tmp_path,')
    change(name, '\treturn MTP_RESPONSE_OK;\n}\n\nint check_and_send_USB_ZLP',
           '\treturn ret_code;\n}\n\nint check_and_send_USB_ZLP')

    name = 'src/mtp_properties.c'
    change(name, 'tmpstr[ sizeof(tmpstr) - 1 ] = 0;',
           'tmpstr[ sizeof(tmpstr) - 1 ] = 0;\n\t\t\t\tif (!a22x_name_ok(tmpstr)) { free(path); return MTP_RESPONSE_INVALID_PARAMETER; }')
    change(name, 'rename(path, path2)', 'a22x_rename(path, path2)')
    change(name, 'stat64(path, &entrystat)', 'a22x_stat(path, &entrystat)')
    change('src/mtp_datasets.c', 'stat64(path, &entrystat)', 'a22x_stat(path, &entrystat)')
    change('src/mtp_operations/mtp_ops_helpers.c', 'remove(path)', 'a22x_remove(path)')
    name = 'src/mtp_operations/mtp_op_truncateobject.c'
    change(name, 'if( !truncate64(full_path, offset) )',
           'if( !set_storage_giduid(ctx, entry->storage_id) && !a22x_truncate(full_path, offset) )')
    change(name, '\t}\n\telse\n\t{\n\t\tresponse_code = MTP_RESPONSE_INVALID_OBJECT_HANDLE;',
           '\t\tfree(full_path);\n\t\trestore_giduid(ctx);\n\t}\n\telse\n\t{\n\t\tresponse_code = MTP_RESPONSE_INVALID_OBJECT_HANDLE;')
    change(name, '#include <errno.h>', '#include <errno.h>\n#include <stdlib.h>', 2)

    name = 'src/mtp_helpers.c'
    change(name, '''\tif(uid >= 0)
\t{
\t\tif(setfsuid(uid))
\t\t\tret = -1;
\t}

\tif(gid >= 0)
\t{
\t\tif(setfsgid(gid))
\t\t\tret = -1;
\t}''', '''\t/* setfsuid/setfsgid return the OLD identity, not an error code. */
\tif(gid >= 0) {
\t\tsetfsgid(gid);
\t\tif ((gid_t)setfsgid((gid_t)-1) != (gid_t)gid) ret = -1;
\t}
\tif(uid >= 0) {
\t\tsetfsuid(uid);
\t\tif ((uid_t)setfsuid((uid_t)-1) != (uid_t)uid) ret = -1;
\t}
\tif (ret) errno = EACCES;''')
    change(name, 'gid = ctx->storages[i].uid;', 'gid = ctx->storages[i].gid;')

    name = 'src/mtp_operations/mtp_op_sendobject.c'
    change(name, '\tint sz;', '\tint sz;\n\tint write_error = 0;')
    change(name, '''if( write(file, tmp_ptr, sz) != sz)
\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t// TODO : Handle this error case properly
\t\t\t\t\t\t\t}''', '''if (a22x_write_all(file, tmp_ptr, sz)) write_error = errno ? errno : EIO;''')
    change(name, '''if( write(file, tmp_ptr, sz) != sz)
\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\t// TODO : Handle this error case properly
\t\t\t\t\t\t\t\t}''', '''/* Continue consuming the USB transaction after an I/O error;
\t\t\t\t\t\t\t\t * report failure rather than desynchronizing the next request. */
\t\t\t\t\t\t\t\tif (!write_error && a22x_write_all(file, tmp_ptr, sz))
\t\t\t\t\t\t\t\t\twrite_error = errno ? errno : EIO;''')
    change(name, '''if( mtp_packet_hdr->code != MTP_OPERATION_SEND_PARTIAL_OBJECT )
\t\t\t\t\t\t\tentry_close(ctx->fs_db, entry);''', '''if (sz < 0 && !write_error) write_error = EIO;
\t\t\t\t\t\tif( mtp_packet_hdr->code != MTP_OPERATION_SEND_PARTIAL_OBJECT ) {
\t\t\t\t\t\t\tif (entry_close(ctx->fs_db, entry) && !write_error)
\t\t\t\t\t\t\t\twrite_error = errno ? errno : EIO;
\t\t\t\t\t\t}''')
    change(name, '\t\t\t\t\t\tresponse_code = MTP_RESPONSE_OK;',
           '\t\t\t\t\t\tresponse_code = write_error ? posix_to_mtp_errcode(write_error) : MTP_RESPONSE_OK;')
    # The complete reviewed operation additionally checks seek/container/actual
    # lengths and the >4 GiB unknown-size sentinel. Keep its control flow in C.
    shutil.copy2(own / 'mtp_sendobject.c', root / name)
    name = 'src/mtp_operations/mtp_op_endeditobject.c'
    change(name, '#include "buildconf.h"', '#include "buildconf.h"\n#include <errno.h>')
    change(name, '\tentry_close(ctx->fs_db, get_entry_by_handle(ctx->fs_db, handle));',
           '\tif (entry_close(ctx->fs_db, get_entry_by_handle(ctx->fs_db, handle)))\n\t\tresponse_code = posix_to_mtp_errcode(errno);')

    # There is exactly one export, selected by the supervisor before launch.
    # Do not keep upstream's addstorage/unlock IPC or an extra singleton flock:
    # the inherited recovery lease cannot be unlocked by the protocol process.
    path = root / 'src/msgqueue.c'
    original = path.read_text()
    path.write_text(original[:original.index('/**')] + '''
#include "buildconf.h"
#include <inttypes.h>
#include <pthread.h>
#include <mqueue.h>
#include <errno.h>
#include "mtp.h"
#include "msgqueue.h"
mqd_t get_message_queue(void) { errno = ENOTSUP; return -1; }
int send_message_queue(char *message) { (void)message; errno = ENOTSUP; return -1; }
int msgqueue_handler_init(mtp_ctx *ctx) { ctx->msgqueue_id = -1; return 0; }
int msgqueue_handler_deinit(mtp_ctx *ctx) { (void)ctx; return 1; }
''')
    change('src/umtprd.c', '\tmqd_t fd = -1;\n', '')
    change('src/umtprd.c', '''\tfd = get_message_queue();
\tif (fd < 0)
\t{
\t\texit(1);
\t}
\tretcode = flock(fd, LOCK_EX | LOCK_NB);
\tif (retcode)
\t{
\t\t// lock failed, umtprd is already running
\t\talready_running = 1;
\t}''', '\tretcode = 0; /* The supervisor owns the singleton and storage leases. */')
    change('src/umtprd.c', '\tPRINT_MSG("uMTP Responder");',
           '\tif (argc == 2 && !strcmp(argv[1], "--a22x-version")) {\n\t\tprintf("uMTP-Responder %s a22x-fixed-export-v1\\n", APP_VERSION); return 0;\n\t}\n\tPRINT_MSG("uMTP Responder");')


if __name__ == '__main__':
    patch(sys.argv[1])

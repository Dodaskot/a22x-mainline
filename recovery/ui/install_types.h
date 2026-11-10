/* SPDX-License-Identifier: MIT */
#ifndef A22X_INSTALL_TYPES_H
#define A22X_INSTALL_TYPES_H

#define INSTALL_TARGETS 16
#define INSTALL_PENDING 16
#define INSTALL_REVIEW_BYTES 65536
#define INSTALL_REVIEW_ROWS 4096

typedef struct {
    char label[64],path[PATH_LEN],action[40],before[80],after[80];
    uint64_t bytes,partition,start;
} InstallTarget;
typedef struct {
    char id[80],operation[40],volume[128],path[PATH_LEN],token[80],state[80];
    bool resumable;
} InstallPending;
typedef struct { uint32_t start,end,color; } InstallReviewRow;
typedef struct {
    bool known,flash_boot,stock,stock_profile,stock_qualified,can_resume,stock_selected;
    bool pick_source,pick_backup,plan_valid,can_execute,resuming,review_bottom_seen;
    bool uncertain,reboot_blocked,stock_replaced,review_resume;
    bool backup_complete,backup_verified,backup_same_device,backup_current,backup_current_required;
    bool backup_userdata_current,backup_userdata_required,recovery_written;
    char boot[80],firmware[160],profile_id[160],status_blockers[2048],stock_blockers[2048],reason[1024],note[1536];
    char source_volume[128],source_path[PATH_LEN],source_token[80],backup_path[PATH_LEN];
    char plan_path[PATH_LEN],plan_hash[80],seal[80],confirmation[96],operation[40],blockers[2048];
    char checkpoint[PATH_LEN],checkpoint_labels[1024],next_action[80],transaction[80],journal_path[PATH_LEN];
    char result_checkpoint[PATH_LEN],result_journal[PATH_LEN];
    uint64_t expires,checkpoint_bytes;
    unsigned hold_ms,path_page,write_count,skip_count,order_count,pending_count;
    int plan_kind,pending_selected;
    int64_t review_scroll,pending_scroll;
    InstallTarget writes[INSTALL_TARGETS],skips[INSTALL_TARGETS];
    char order[INSTALL_TARGETS][64];
    InstallPending pending[INSTALL_PENDING],selected_pending;
    char review[INSTALL_REVIEW_BYTES];size_t review_used;
    InstallReviewRow rows[INSTALL_REVIEW_ROWS];unsigned row_count;
} InstallState;

#endif

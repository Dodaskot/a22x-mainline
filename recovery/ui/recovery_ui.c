/* SPDX-License-Identifier: MIT
 * Native Linux framebuffer/evdev recovery frontend. No Android UI dependencies.
 * Storage operations are delegated to recoveryctl, never implemented by the UI.
 */
#define _GNU_SOURCE
#include <assert.h>
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <drm/drm.h>
#include <sys/un.h>
#include <sys/time.h>
#include <inttypes.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "font_data.h"
#include "font_extra.h"
#include "clock_font.h"
#include "json_min.h"
#include "native_canvas.h"
#include "theme.h"
#include "theme_files.h"
#include "i18n_data.h"
#include "zones_data.h"
#include "kinetic_scroll.h"
#include "terminal_model.h"
#include "terminal_font.h"

#define CW 720
#define CH 1600
#define MAX_BUTTONS 96
#define MAX_BACKUPS 48
#define MAX_INPUTS 16
#define MAX_SLOTS 16
#define JSON_LIMIT (1024*1024)
#define PATH_LEN 1024
#define MAX_VOLUMES 80
#define FILE_BATCH 64
#define FILE_WINDOW_STEP 32
#define FILE_ROW_HEIGHT 116
#define FILE_LIST_TOP 424
#define FILE_LIST_BOTTOM 1228
#define VOLUME_LIST_TOP 240
#define VOLUME_LIST_BOTTOM 1188
#define VOLUME_ROW_HEIGHT 158
#define TEXT_ROW_HEIGHT 28
#define SWIPE_THRESHOLD 12
#define TEXT_LIMIT 65536
#define TERMINAL_COLS 48
#define TERMINAL_ROWS 21
#define TERMINAL_CELL_W 13
#define TERMINAL_CELL_H 24
#define TERMINAL_READ_LIMIT 16384
#define TERMINAL_INPUT_LIMIT 8192
#define TERMINAL_LEFT_POLL_MS 1000u
#define TERMINAL_LEFT_RETRY_MS 5000u
#define TERMINAL_SETTLE_MS 700u
#define DISPLAY_CONTROL_LIMIT 16384
#define DISPLAY_CONTROL_TIMEOUT 5000u
#define POWER_SHORT_PRESS_MS 1500u
#define POWER_MENU_MS 700u
#define SHOT_CHORD_MS 1500u   /* Volume down may be held this long before Power makes a screenshot of it */
#define SHOT_FLASH_MS 260u
#define SHOT_TIMEOUT_MS 20000u
#define NAV_EDGE 26
#define NAV_BOTTOM 1530
#define NAV_BACK_DISTANCE 90
#define NAV_HOME_DISTANCE 110
#define NAV_HOLD_MS 450u
#define NAV_BAR_WIDTH 250
#define NAV_BAR_GROW 40
#define NAV_LIFT_LIMIT 96
#define NAV_PULL_LIMIT 110
#define NAV_SETTLE_MS 220u
/* The pages passed on the way to the open one: Back returns along them (2026-11-10). */
#define NAV_TRAIL 16
#define USB_VOLUME_ROWS 6
#include "install_types.h"

#define ID_PARTS 16
enum Screen { HOME, SPLASH, FLASH, FLASH_REVIEW, LANGUAGE, THEME, TERMINAL_THEME, ANIMATIONS, LOCKSCREEN, ACCESS_REQUEST, OS_MENU, SYSTEMS_SETUP, BACKUP, LIST, DETAIL, PLAN, PROGRESS, RESULT, STORAGE, SYSTEM, POWER, POWER_CONFIRM, POWER_MENU, TOOLS, TOOLS_EXPORT, DISPLAY_SETTINGS, PREPARE_FILESYSTEM, USB_FILES, TERMINAL, SYSTEMS, SYSTEMS_PLAN, SYSTEMS_RECOVER,
    INSTALL, INSTALL_PLAN, INSTALL_RESULT,
    FILE_VOLUMES, FILE_VOLUME, FILE_BROWSER, FILE_INFO, FILE_TEXT, FILE_EDIT, FILE_PROMPT, FILE_CONFIRM, FILE_DISCARD, SYSTEMS_EXTRAS, SYSTEMS_DONE, WIFI, SYSTEMS_NEW, SYSTEMS_CATALOG,
    SYSTEMS_APP_LIST, SYSTEMS_SEARCH, IDENTITY, IDENTITY_RESTORE, SYSTEMS_SHELLS, SYSTEMS_LOCALE, SYSTEMS_TIMEZONE, SYSTEMS_REMOVE, SYSTEMS_IMAGES, ADB_REQUEST, APLAN, FLASH_SUPER, ICON_SHEET, WIPE, WIPE_REVIEW, ANDROID_MENU, SAMSUNG_REVIEW, ANDROID_IMAGE, ROOT_PAGE, ZIP_REVIEW, CARD_PAGE, COPY_DETAIL, ANDROID_LOG };
enum Action { A_BACK=1,A_BACKUP,A_RESTORE,A_VERIFY,A_STORAGE,A_SYSTEM,A_POWER,A_REVIEW_BACKUP,A_REVIEW_RESTORE,A_VERIFY_ONE,A_CONFIRM,A_DONE,A_LIST_PREV,A_LIST_NEXT,A_REBOOT,A_SHUTDOWN,A_POWER_CONFIRM,A_MOUNT,A_UMOUNT,A_REFRESH,
    A_FILES,A_FS_PREV,A_FS_NEXT,A_FS_OPEN,A_FS_MOUNT_RO,A_FS_MOUNT_RW,A_FS_UNMOUNT,A_FS_UP,A_FS_VOLUME,A_FS_REFRESH,
    A_FS_READ,A_FS_EDIT,A_FS_NEW_FILE,A_FS_MKDIR,A_FS_RENAME,A_FS_COPY,A_FS_DELETE,A_FS_PROMPT_NEXT,A_FS_CONFIRM,
    A_FS_SAVE,A_FS_CANCEL_EDIT,A_FS_DISCARD,A_FS_DEST_NEXT,A_FS_CONFIRM_PREV,A_FS_CONFIRM_NEXT,A_FS_CARET,A_FS_LEFT,A_FS_RIGHT,A_FS_UP_LINE,A_FS_DOWN_LINE,
    A_FS_PAGE_UP,A_FS_PAGE_DOWN,A_KB_SHIFT,A_KB_LANG,A_KB_SYMBOLS,A_KB_BACKSPACE,A_KB_SPACE,A_KB_ENTER,
    A_REBOOT_RECOVERY,A_REBOOT_DOWNLOAD,A_TOOLS,A_TOOLS_REFRESH,A_TOOLS_EXPORT,A_TOOLS_DEST_NEXT,A_TOOLS_EXPORT_CONFIRM,
    A_FS_CHMOD,A_FS_CHOWN,A_FS_RESOLVE,A_FS_FOLLOW,A_DISPLAY,A_DISPLAY_REFRESH,A_DISPLAY_60,A_DISPLAY_90,A_REVIEW_WIPE,A_PREPARE_CONTINUE,A_PREPARE_REFRESH,A_DISPLAY_BRIGHTNESS,
    A_PROFILE=100,A_BACKUP_ITEM=200,A_STORE=300,A_USB_FILES=400,A_USB_REFRESH,A_USB_NEXT,A_USB_RO,A_USB_RW,A_USB_START,A_USB_STOP,
    A_TERMINAL=500,A_TERM_REFRESH,A_TERM_START,A_TERM_CLOSE,A_TERM_LIVE,A_TERM_TAB,A_TERM_ESCAPE,A_TERM_INTERRUPT,A_TERM_EOF,A_TERM_LEFT,A_TERM_UP,A_TERM_DOWN,A_TERM_RIGHT,
    A_SYSTEMS=600,A_SYS_REFRESH,A_SYS_FILES,A_SYS_IMPORT,A_SYS_USE_MANIFEST,A_SYS_CANCEL_PICK,A_SYS_PLAN_CONFIRM,A_SYS_PLAN_PREV,A_SYS_PLAN_NEXT,A_SYS_RESUME,A_SYS_ROLLBACK,A_SYS_RECOVER_CONFIRM,A_SYS_REMOVE,A_SYS_REMOVE_CONFIRM,A_SYS_SLOT=700,
    A_INSTALL=800,A_INSTALL_REFRESH,A_INSTALL_BOOT,A_INSTALL_STOCK,A_INSTALL_SOURCE,A_INSTALL_BACKUP,A_INSTALL_REVIEW,A_INSTALL_USE_SOURCE,A_INSTALL_CANCEL_PICK,A_INSTALL_PATH_PREV,A_INSTALL_PATH_NEXT,A_INSTALL_CONFIRM,A_INSTALL_FILES,A_INSTALL_RESULT_DONE,A_INSTALL_POWER,A_INSTALL_PENDING=900,
    A_FS_VOLUME_ITEM=1000,A_FS_ENTRY=2000,A_FS_ENTRY_MENU=2100,A_KB_KEY=3000,A_OS=5000,A_TIMEOUT=5010,A_SYS_DESKTOP=5020,A_SYS_ACCOUNT_KEEP=5030,A_SYS_ACCOUNT_NEW,A_SYS_SETUP_NEXT,A_SYS_FIELD_USER,A_SYS_FIELD_PASS,A_FLASH=5070,A_FS_FORMAT_EXT4=5040,A_FS_FORMAT_VFAT,A_FS_FORMAT_F2FS,A_FS_CHECK,A_FS_RESIZE,A_JOB_CANCEL=5050,A_JOB_KEEP,A_JOB_CANCEL_YES,A_LOCK_SET=5060,A_LOCK_CLEAR,A_FLASH_TARGET=5200,A_FLASH_CONFIRM=5220,A_FLASH_CANCEL_PICK,A_RESTORE_IMAGE,A_LANGUAGE,A_LANGUAGE_ITEM=5240,A_VIBRATION=5260,A_TZ_DOWN=5270,A_TZ_UP,A_THEME=5280,A_TERMINAL_THEME=5300,A_USB_EJECT=5330,A_ANIMATIONS=5340,A_ANIMATION_ITEM=5350,A_LOCK_MODE=5360,A_ACCESS_TOGGLE=5370,A_ACCESS_ALLOW,A_ACCESS_DENY,A_THEME_ITEM=5400,A_TERMINAL_THEME_ITEM=5440,A_THEME_ADD=5480,A_TERMINAL_ADD,A_THEME_USE,A_THEME_RESTORE,A_THEME_CANCEL_PICK,A_LOCK_KEYBOARD,A_THEME_REMOVE=5500,A_NAV_MODE=5520,A_NAV_BACK=5530,A_NAV_HOME,A_NAV_TERMINAL,A_MENU_CLOSE,A_USB_VOLUME_RO=5540,A_USB_VOLUME_RW=5560,A_USB_VOLUME_OFF=5580,A_SYS_WIFI=5600,A_SYS_WIFI_CLEAR,A_SYS_SCRIPT,A_SYS_SCRIPT_CLEAR,A_SYS_USE_SCRIPT,A_SYS_FIELD_SSID,A_SYS_FIELD_WIFI_PASS,A_SYS_APP=5880,A_SYS_MOUNT=5640,A_SYS_MOUNT_RO,A_SYS_MOUNT_RW,A_SYS_MOUNT_CANCEL,A_SYS_DONE_RESTART=5650,A_SYS_DONE_SYSTEMS,A_SYS_DONE_HOME,ICON_LINUX=5100,ICON_PHONE,ICON_FLASH,ICON_CHEVRON,ICON_BACK,ICON_TUNE,ICON_LANGUAGE,ICON_VIBRATE,ICON_DISPLAY,ICON_CLOCK,ICON_LOCK,ICON_THEME,ICON_USB,ICON_KEYBOARD,ICON_ERASE,ICON_TRASH,ICON_NAV,ICON_WIFI,ICON_BROWSER,ICON_TORCH,
    A_WIFI=5700,A_WIFI_SCAN,A_WIFI_DISCONNECT,A_WIFI_HIDDEN,A_WIFI_FIELD_SSID,A_WIFI_FIELD_PASS,A_WIFI_PAGE_PREV,A_WIFI_PAGE_NEXT,A_WIFI_FORGET,A_WIFI_NET=5720,
    A_HOME_THEME=5760,A_BROWSER,
    A_SYS_NEW=5770,A_NEW_INTERNET,A_NEW_FILE,A_CATALOG_WIFI,A_CATALOG_PREV,A_CATALOG_NEXT,A_HOME_CATALOG,A_CATALOG_FAMILY=5780,
    A_APPS_OPEN=5830,A_PKG_OPEN,A_PKG_QUERY,A_PKG_PREV,A_PKG_NEXT,A_APPS_DONE,A_PKG_DONE,A_PKG_FAMILY=5840,A_PKG_RESULT=5850,A_IDENTITY=5950,A_ID_DEST_NEXT,A_ID_BACKUP,A_ID_RESTORE_OPEN,A_ID_RESTORE,A_ID_ITEM=5960,A_CHARGER_ON=5970,A_CHARGER_OFF,
    A_SHELL_ITEM=5980,A_GREETER_ITEM=5990,A_SHELLS_NEXT=6000,A_LOCALE_OPEN=6010,A_TZ_OPEN,A_TZ_RECOVERY,A_TZ_PICK,A_LOCALE_ITEM=6020,
    A_TZ_REGION=6040,A_TZ_ITEM=6100,
    A_NEW_SAVED=7000,A_IMAGES_CATALOG,A_IMAGE_DELETE_CONFIRM,A_IMAGE_DELETE_CANCEL,A_IMAGE_ITEM=7010,A_IMAGE_DELETE=7030,A_ADB_ALLOW=7050,A_ADB_DENY,A_ID_KIND=7100,A_ID_PART=7110,A_APLAN=7130,A_APLAN_REVIEW,A_APLAN_EDIT,A_APLAN_FORGET,A_FLASH_SUPER=7140,A_WIPE=7150,A_WIPE_CACHE,A_WIPE_VOLUMES,A_WIPE_CONFIRM,A_WIPE_ANDROID,
    A_ANDROID=7160,A_SAMSUNG,A_SAMSUNG_CONFIRM,A_ANDROID_CATALOG,A_ANDROID_DOWNLOAD,A_ANDROID_INSTALL,A_ANDROID_DELETE,A_STOCK_BOOT,A_STOCK_BOOT_FLASH,A_ROOT,A_ROOT_REMOVE,A_ROOT_PREPARE,
    A_CATALOG_CATEGORY=7170,A_CATALOG_VERSION=7180,A_CATALOG_KIND=7190,A_CATALOG_REGION=7200,A_CATALOG_FIT=7210,
    A_ROOT_INSTALL=7220 /* ..7223 */,A_ROOT_KEY=7230 /* ..7233: the SuperKey of a tool that needs one */,A_ROOT_KEY_FIELD=7240,A_ZIP=7250,A_ZIP_CONFIRM,A_BACKUP_RENAME=7260,A_BACKUP_DELETE,A_BACKUP_NAME_FIELD,A_ID_DELETE,A_SETTINGS_RESET,
    A_CARD=7270,A_CARD_NEXT,A_CARD_APPLY,A_CARD_LINUX=7280 /* ..7284 */,A_CARD_SWAP=7290 /* ..7293 */,
    A_CATALOG_ITEM=7300 /* ..7389: CATALOG_MAX images, Linux and Android (2026-11-10) */,
    /* 2026-11-11: Restore and Verify in two categories, the copies of Android's partitions, the sheet
       that asks where a file is, and a ROM chosen from a file. */
    A_LIST_CATEGORY=7400 /* ..7401 */,A_COPY_VERIFY=7404,A_COPY_RESTORE,A_COPY_DELETE,A_COPY_REFRESH,A_COPY_ITEM=7410 /* ..7433 */,
    A_COPY_MOUNT=7440 /* ..7443 */,A_COPY_PART=7450 /* ..7457 */,A_SOURCE_REFRESH=7460,A_SOURCE_CANCEL,A_ANDROID_FILE,A_NEW_FORMAT,
    A_TORCH=7480,A_TORCH_DOWN,A_TORCH_UP,A_TORCH_ON,A_TORCH_OFF,A_TORCH_BAR,A_RESET_LATER,A_RESET_FORGET,A_BACKUP_ANDROID,A_ID_MOUNT,
    A_ZIP_ADD,A_ZIP_RESTART,A_ZIP_STAY,A_ZIP_REMOVE /* ..+ZIP_QUEUE-1, below A_SOURCE_ITEM */,
    A_SOURCE_ITEM=7500 /* ..7579: MAX_VOLUMES */,
    A_ALOG_OK=7590,A_ALOG_IMAGES,A_ALOG_STOCK };
enum JobKind { J_NONE,J_INVENTORY,J_LIST,J_BACKUP_PLAN,J_RESTORE_PLAN,J_BACKUP,J_VERIFY,J_RESTORE,J_STORAGE_INVENTORY,J_MOUNT,J_UMOUNT,
    J_FS_INVENTORY,J_FS_MOUNT,J_FS_UNMOUNT,J_FS_LIST,J_FS_READ,J_FS_WRITE,J_FS_MKDIR,J_FS_RENAME,J_FS_COPY,J_FS_DELETE,
    J_FS_STAT,J_FS_CHMOD,J_FS_CHOWN,J_FS_RESOLVE,J_FS_COPY_TREE,J_FS_FORMAT,J_FS_CHECK,J_FS_RESIZE,J_TOOLS_STATUS,J_TOOLS_EXPORT,J_DISPLAY_STATUS,J_DISPLAY_SET,J_WIPE_PLAN,J_WIPE,J_USB_STATUS,J_USB_START,J_USB_STOP,
    J_TERM_STATUS,J_TERM_START,J_TERM_READ,J_TERM_WRITE,J_TERM_RESIZE,J_TERM_STOP,J_SYS_STATUS,J_SYS_IMPORT_PLAN,J_SYS_SWITCH_PLAN,J_SYS_IMPORT,J_SYS_SWITCH,J_SYS_RECOVER,J_SYS_REMOVE,
    J_INSTALL_STATUS,J_INSTALL_BOOT_PLAN,J_INSTALL_STOCK_PLAN,J_INSTALL_RESUME_PLAN,J_INSTALL_EXECUTE,J_FLASH_TARGETS,J_FLASH_CHECK,J_FLASH_WRITE,J_USB_SYNC,J_USB_BACKUP,J_USB_EJECT,J_SYS_ACCOUNT_USER,J_SYS_ACCOUNT_PASS,J_SYS_WIFI_SSID,J_SYS_WIFI_PASS,J_LOCK_ENTER,J_LOCK_SET,J_LOCK_OLD,J_LOCK_CONFIRM,J_LOCK_ACCESS,J_SYSINFO,J_THEME_IMPORT,J_THEME_REMOVE,J_THEME_RESTORE,
    J_WIFI_STATUS,J_WIFI_SCAN,J_WIFI_CONNECT,J_WIFI_DISCONNECT,J_WIFI_FORGET,J_WIFI_SSID,J_WIFI_PASS,J_CATALOG,J_CATALOG_CHECK,J_PKG_SEARCH,J_PKG_QUERY,J_ID_LIST,J_ID_BACKUP,J_ID_BACKUPS,J_ID_RESTORE,J_ID_DELETE,J_CHARGER,J_IMAGES,J_IMAGES_DOWNLOAD,J_IMAGES_DELETE,J_APLAN_SHOW,J_APLAN_PREPARE,J_APLAN_FORGET,J_WIPE_CACHE,J_SAMSUNG_INSPECT,J_SAMSUNG_INSTALL,J_ANDROID_RESET,J_STOCK_BOOT,J_SAMSUNG_UNPACK,J_ROOT_LIST,J_ROOT_INSTALL,J_ROOT_PREPARE,J_ZIP_INSPECT,J_ZIP_RUN,J_BACKUP_RENAME,J_BACKUP_DELETE,J_CARD_LIST,J_CARD_APPLY,
    J_COPY_LIST,J_COPY_VERIFY,J_COPY_RESTORE,J_COPY_DELETE,J_ROM_KIND,
    J_ROOT_SUPERKEY /* a prompt, not a job: APatch's SuperKey */,J_BACKUP_NAME /* a prompt: the new name of a backup */ };
enum ScrollSurface { SCROLL_NONE,SCROLL_FOLDER,SCROLL_VOLUMES,SCROLL_TEXT,SCROLL_PROMPT,SCROLL_TERMINAL,SCROLL_SYSTEMS,SCROLL_TOOLS,SCROLL_INSTALL_REVIEW,SCROLL_INSTALL_PENDING,SCROLL_PAGE };
enum DisplayCommand { DC_NONE,DC_STATUS,DC_BRIGHTNESS,DC_BLANK,DC_WAKE,DC_TOGGLE };

typedef struct { int x,y,w,h; } Rect;
typedef struct { Rect r; int action,hold_ms; bool enabled; int glyph; } Button;
#define SYS_ROWS 48
#define SYS_SUMMARY 4
#define SYS_SECTIONS 8
typedef struct { char label[40],value[72],unit[12];unsigned char section; } SysRow;
#define TRANSITION_DEPTH 8
#define TRANSITION_OPEN_MS 320u
#define TRANSITION_BACK_MS 260u
/* Zoom with blur takes a little longer: the frost has to be seen (2026-11-10). */
#define TRANSITION_BLUR_OPEN_MS 400u
#define TRANSITION_BLUR_BACK_MS 330u
#define TRANSITION_THEME_MS 560u
#define TRANSITION_FADE_MS 240u
#define TRANSITION_DISSOLVE_MS 420u
#define SETTINGS_DELAY_MS 3000u
#define SETTINGS_RETRY_MS 10000u
#define SLEEP_POLL_MS 1000
enum TransitionKind { TR_NONE,TR_OPEN,TR_CLOSE,TR_THEME,TR_SLIDE,TR_SLIDE_BACK,TR_FADE,TR_DISSOLVE };
enum AnimationStyle { ANIM_ZOOM,ANIM_BLUR,ANIM_SLIDE,ANIM_FADE,ANIM_DISSOLVE,ANIM_OFF,ANIMATION_COUNT };
static const char*const animation_codes[ANIMATION_COUNT]={"zoom","blur","slide","fade","dissolve","off"};
enum LockFlow { LF_NONE,LF_SET,LF_CHANGE,LF_REMOVE,LF_MODE,LF_ACCESS };
/* Two finished frames and a moving shape between them: no page is drawn again while it runs. */
typedef struct { enum TransitionKind kind;bool armed,active;uint64_t started,worst;unsigned int duration,frames;Rect origin;int glyph,cx,cy;bool blur;uint32_t*from,*to,*soft_from,*soft_to;int page_from,page_to; } Transition;
typedef struct { int from,to;Rect origin;int glyph; } TransitionStep;
#define FLASH_TARGETS 20  /* A_FLASH_TARGET..A_FLASH_CONFIRM-1: the partitions and those inside super. */
typedef struct { char label[32],title[64];uint64_t bytes,room;bool logical; } FlashTarget;
typedef struct { char path[PATH_LEN],name[192],profile[64],created[80],schema[64],workflow[64]; uint64_t bytes; } BackupEntry;
typedef struct { char label[64],path[PATH_LEN]; uint64_t bytes; bool available,mounted; } PartitionEntry;
/* A copy of Android's partitions on a card (identity_backup.py --kind android copies): an archive or,
   made before 2026-11-11, a folder; and a card that holds none yet because it is not mounted. */
#define COPIES_MAX 24
#define COPY_PARTS 10
/* ZIPs with installers that one hold runs one after another (2026-11-11). */
#define ZIP_QUEUE 6
#define ZIP_RESTART_MS 10000u
typedef struct { char volume[96],token[80],path[PATH_LEN],name[208],sha[72],title[128],what[16],installer[16],changed[160];uint64_t size;unsigned int files;int state; /* 0 waits, 1 finished, 2 failed */ } ZipItem;
#define COPY_WAITING 4
typedef struct { char volume[32],token[72],place[64],path[256],created[32],sha[72],labels[COPY_PARTS][24]; unsigned int parts; uint64_t bytes,file_bytes; bool archive,writable,same; } PhoneCopy;
typedef struct { char id[32],token[72],label[64],fs[16]; uint64_t bytes; } CopyVolume;
typedef struct {
    char id[128],label[160],fs_type[40],mountpoint[PATH_LEN],reason[384],read_only_reason[384],token[256],mount_owner[40];
    bool mounted,read_only,mountable,write_supported;
    uint64_t bytes;
} FileVolume;
typedef struct { char name[256],path[PATH_LEN],type[32],mode[16],token[256],link_target[PATH_LEN],metadata_reason[256]; uint64_t size,uid,gid,nlink;bool metadata_supported; } FileEntry;
typedef struct {char id[128],label[160],mountpoint[PATH_LEN],volume_token[256],mount_token[256];bool writable;} UsbCandidate;
typedef struct {char id[80],name[400];bool active,boot_tested;} SystemSlot;
typedef struct {
    int kind;
    char volume[128],volume_token[256],path[PATH_LEN],destination_volume[128],destination_token[256],destination[PATH_LEN];
    char mode[16],sha256[80],entry_token[256],uid[24],gid[24],text_file[PATH_LEN];
    bool create;
    unsigned int offset;
} FileRequest;
typedef struct {
    pid_t pid;
    int fdout,fderr,kind,status;
    bool reaped,out_eof,err_eof,overflow;
    char *output; size_t used;
    char errline[4096]; size_t errused;
} Job;
typedef struct {
    pid_t pid;
    int fdout,fderr,status;
    enum DisplayCommand command;
    bool reaped,out_eof,err_eof,overflow,timed_out;
    uint64_t deadline;
    char output[DISPLAY_CONTROL_LIMIT+1];size_t used;
} DisplayControl;
/* One visible Wi-Fi network as wifi.py reports it; the name is shown as it is. */
#define WIFI_MAX 32u
typedef struct { char ssid[132],hex[72],security[16],band[16];unsigned int bars;bool secure,supported,connected,saved; } WifiNet;
/* One image of the installer catalog (installer_ui.h). */
#define CATALOG_MAX 90u  /* A_CATALOG_ITEM..A_CATALOG_ITEM+89 */
/* Android images too (2026-11-10): category gsi or port, Android version, type (aosp, gms,
   lineage...), region, the build and its security patch, and whether it fits this phone. */
typedef struct { char id[48],name[64],version[24],family[24],file[96],desktop[16],notes[48],category[12],android[8],variant[16],region[16],build[40],patch[16],fit_note[96];
    uint64_t size,system_bytes;bool minimal,reachable,unavailable,signed_,fits;int tried; /* 1: started on this phone model, -1: did not */ } CatalogImage;
typedef struct { char name[64],file[12],action[12],target[24],reason[96];uint64_t size; } SamsungRow;
typedef struct { char id[24],name[32],version[24],sha256[72],reason[128],error[48],published[16];uint64_t size;bool supported,bundled,needs_key; } RootTool;
#define ROOT_TOOLS 4u
/* A removable medium as sdcard.py lists it (2026-11-11, Partition a card). */
#define CARD_DISKS 4u
#define CARD_PARTS 6u
typedef struct { char id[32],name[64],kind[8],token[72],table[8],busy_code[32],busy[160];uint64_t size,usable,align,min_files;unsigned int part_count;
    struct { unsigned int number;uint64_t size;char fs[16],label[40]; } parts[CARD_PARTS]; } CardDisk;
#define SAMSUNG_ROWS 48u
#define STORE_MAX 16u
typedef struct { char file[208],path[232],name[64],version[32],family[16];uint64_t size;bool checked; } StoredImage;
typedef struct { char name[64],version[48],summary[128];int available; } PkgResult;
#define PKG_RESULTS 24u
#define PACKAGES_MAX 40u
typedef struct {
    uint32_t *pixels,*backdrop;

    bool clip_active;Rect clip;
    enum Screen screen;
    bool preview,device,allow_power,dirty,running,successful,render_native,profile_render;
    char fixture[PATH_LEN],python[PATH_LEN],backend[PATH_LEN],workdir[PATH_LEN],storage_helper[PATH_LEN],filesystem_helper[PATH_LEN],management_helper[PATH_LEN],display_helper[PATH_LEN],usb_helper[PATH_LEN],terminal_helper[PATH_LEN],distro_helper[PATH_LEN],install_helper[PATH_LEN];
    char repositories[8][PATH_LEN],repository_root[8][PATH_LEN],repository_volume[8][32],repository_fs[8][16]; int repository_count,repository_base,repository_pending,store;bool repository_read_only[8];
    char fb_path[PATH_LEN],input_path[PATH_LEN];
    char kernel[128],battery[32],status[128],message[1536];
    char model[160],identity[256],storage_message[256],storage_mountpoint[PATH_LEN];
    bool restore_qualified,storage_known,storage_mounted;
    bool linux_backup_available,linux_restore_available,linux_wipe_available,backup_compact;
    bool tools_known,ssh_available,ssh_running,ssh_ephemeral,logs_export_available,fs_copy_directories;
    char ssh_address[128],ssh_fingerprint[256],ssh_reason[384],logs_reason[384],tools_volume_id[128],tools_volume_token[256];
    unsigned int ssh_port,ssh_keys;
    int tools_volume_index;
    int charger_asked;bool charger_known,charger_off;char charger_note[200];
    bool id_mounting; /* the card is being opened for writing from the page of a copy */
    int id_volume_index,id_kind;uint32_t id_off,id_optional; /* id_optional: Android's own data, in a copy only when chosen */char id_labels_arg[256];bool id_known,id_list_after;
    /* The installation prepared in the Android app (android_plan_ui.h, 2026-11-10). */
    bool aplan_asked,aplan_known,aplan_present,aplan_valid,aplan_opened,aplan_pending,aplan_used,aplan_forget_after,aplan_script_present,aplan_erase,aplan_edit;
    char aplan_sha[72],aplan_catalog[64],aplan_name[96],aplan_version[48],aplan_file[160],aplan_user[40],aplan_hash[140],aplan_script[72],aplan_locale[16],aplan_zone[64],aplan_desktop_id[16],aplan_greeter_id[16],aplan_source[72],aplan_error[320],aplan_helper[PATH_LEN],systems_password_hash[140];
    uint64_t aplan_size,aplan_created;unsigned int aplan_shells,aplan_apps;
    /* A logical partition inside super (2026-11-10): its size now and after the flash, and the image's own size. */
    bool flash_logical;uint64_t flash_size_now,flash_size_after,flash_payload,flash_super_free;unsigned int id_count;uint64_t id_bytes,id_sizes[ID_PARTS];int id_pick;unsigned int id_backup_count;struct{char path[96],created[32],sha[72];unsigned int parts;uint64_t bytes;bool same;}id_backups[8];char id_labels[ID_PARTS][24],id_volume_id[96],id_volume_token[96],id_error[256],id_done[192],id_helper[PATH_LEN];
    int64_t tools_scroll,page_scroll,page_content;
    enum Screen theme_pick,theme_return;uint8_t*theme_image;char theme_remove_code[24];int theme_armed;uint64_t theme_armed_until;
    SysRow sys_rows[SYS_ROWS],sys_summary[SYS_SUMMARY];char sys_titles[SYS_SECTIONS][40],sys_name[48],sys_model[24],sys_codename[24],sys_version[24];const char*sysinfo_root;
    unsigned int sys_row_count,sys_summary_count,sys_section_count;bool sys_tried;
    bool display_known,display_supported,display_service,display_60,display_90;
    unsigned int display_hz,display_requested_hz;
    double display_actual_hz;
    char display_reason[512],display_note[256];
    bool display_light_known,display_brightness_supported,display_blank_supported,display_blank_known,display_blanked;
    int battery_percent;bool battery_charging,battery_refresh;
    /* The interface's own estimate of the charge: see update_battery. */
    double battery_estimate,battery_ocv,battery_ohm;long battery_counted,battery_uv,battery_ua;uint64_t battery_at;unsigned int battery_samples;bool battery_estimated;int queued_action;enum Screen queued_screen;unsigned int saved_hz,saved_brightness,restore_hz,restore_brightness;bool restore_status_asked,display_restoring,storage_prefers_usb;int toast_icon;uint64_t progress_mark_at,progress_mark_done;char progress_mark_phase[128];char toast[120];uint64_t toast_until,hotplug_at,usb_cable_check,usb_cable_lost_at;bool hotplug_pending,usb_cable_seen;unsigned int hotplug_retries;
    bool job_cancel_asked,job_cancelled,locked,lock_enabled,flash_known,flash_pick,flash_review_valid,flash_identical,flash_restore,flash_offline,settings_dirty;
    FlashTarget flash_targets[FLASH_TARGETS];unsigned int flash_target_count,vibration,haptic_active;int flash_selected,haptic_fd,haptic_id,utc_offset,clock_minute;uint64_t flash_bytes,settings_changed_at,hold_felt_at;
    char flash_helper[PATH_LEN],flash_volume[96],flash_volume_token[80],flash_path[PATH_LEN],flash_name[256],flash_kind[96],flash_contents[256],flash_sha[80],flash_identity[80],flash_rollback[96],flash_warnings[512],flash_reason[256],flash_label[24],flash_checksum[96];
    unsigned int screen_timeout_index,display_wanted_percent,display_live_percent,brightness_max;int brightness_fd,display_drag_x;uint64_t last_activity,display_live_at;
    bool display_input_blocked,display_uncertain,display_power_down,display_brightness_drag,display_request_accepted,display_expected_blank;
    unsigned int display_percent,display_last_percent,display_preview_percent,display_requested_percent;
    uint64_t display_raw,display_max,display_requested_raw,display_actual_raw,display_expected_raw,display_power_at;
    bool display_actual_raw_known;
    enum DisplayCommand display_pending_command;
    char display_light_note[384];
    DisplayControl display_control;
    bool usb_known,usb_available,usb_read_only,usb_export_valid,usb_export_writable,usb_request_ro;
    char usb_state[32],usb_reason[512],usb_session[256],usb_export_id[128],usb_request_id[128],usb_request_volume_token[256],usb_request_mount_token[256];
    char usb_export_volume_token[256],usb_export_mount_token[256];
    UsbCandidate usb_candidates[8];int usb_candidate_count,usb_selected,usb_expected;
    TerminalModel terminal;
    bool terminal_known,terminal_available,terminal_paused,terminal_follow,terminal_close_requested,terminal_exit_after_stop,terminal_busy,terminal_left,terminal_left_busy,terminal_resume;
    char terminal_state[32],terminal_session[80],terminal_boot[80],terminal_reason[512];
    unsigned int terminal_rows,terminal_cols;int terminal_expected,terminal_pending,terminal_exit_code;
    uint64_t terminal_typed,terminal_seq,terminal_read_after,terminal_next_poll,terminal_lost,terminal_pending_after,terminal_stop_deadline,terminal_start_deadline;
    int64_t terminal_scroll;
    unsigned char terminal_input[TERMINAL_INPUT_LIMIT];size_t terminal_input_length,terminal_sent;
    bool systems_remove_offer,systems_known,systems_mounted,systems_read_only,systems_initialized,systems_platform,systems_reboot_blocked,systems_pick_manifest,systems_plan_valid;
    bool systems_resume_allowed,systems_rollback_allowed,systems_review_recovery;
    char systems_active[80],systems_volume_token[256],systems_transaction[80],systems_source[80],systems_target[80],systems_direction[16],systems_expected_transaction[80];
    unsigned int systems_desktop,systems_shells,systems_greeter,systems_tz_region;int systems_locale,systems_tz,recovery_tz;bool tz_for_recovery;bool systems_new_account,systems_done_ready;char systems_username[40],systems_password[130],systems_password_file[PATH_LEN];
    char systems_wifi_ssid[64],systems_wifi_password[130],systems_wifi_old_ssid[64],systems_wifi_old_password[130],systems_wifi_file[PATH_LEN];
    bool systems_wifi_saved,systems_wifi_old_saved; /* The key comes from the recovery's saved networks. */
    char systems_done_reason[240];
    /* Packages found in the repositories of the new system (2026-11-08). */
    char systems_packages[PACKAGES_MAX][64];unsigned int systems_package_count,pkg_count,pkg_page,pkg_family;bool pkg_known;
    PkgResult pkg_results[PKG_RESULTS];char pkg_query[48],pkg_error[200],pkg_search_family[24],pkg_search_release[24];
    unsigned int systems_apps;bool systems_pick_script,systems_empty,systems_plan_clean,systems_mount_sheet,systems_mounting,systems_refresh_after;
    enum Screen systems_mount_return;unsigned int catalog_pending; /* Where a userdata mount returns; the catalog image chosen before it (+1). */
    char systems_script_volume[128],systems_script_token[256],systems_script_path[PATH_LEN],systems_script_name[256];
    char systems_done_name[160],systems_done_desktop[160],systems_done_network[320];bool systems_done_clean;
    char systems_reason[1024],systems_note[512],systems_source_volume[128],systems_source_token[256],systems_manifest[PATH_LEN];
    char systems_plan_path[PATH_LEN],systems_plan_seal[80],systems_plan_hash[80],systems_plan_slot[80],systems_plan_name[400],systems_confirmation[384];
    uint64_t systems_required_bytes,systems_available_bytes,systems_plan_expires;
    unsigned int systems_step,systems_total,systems_plan_page,systems_incomplete;int systems_plan_kind,systems_selected,systems_slot_count;
    int64_t systems_scroll;SystemSlot systems_slots[16];
    InstallState install;
    FileEntry fs_resolved;bool fs_link_resolved;
    PartitionEntry partitions[4]; int partition_count;
    char profile[32],plan_path[PATH_LEN],plan_id[128],token[192],plan_backup[PATH_LEN],plan_parts[384],plan_targets[768],blockers[768];
    char progress_phase[128],progress_part[128],last_backend_operation[64];
    uint64_t plan_bytes,progress_done,progress_total;
    bool plan_can_execute,plan_restore,plan_wipe,plan_linux_v2,plan_linux_admitted,inventory_ok,list_for_verify;
    bool plan_space_known,plan_estimate_known;
    uint64_t plan_used_bytes,plan_archive_bytes,plan_free_bytes,plan_reserve_bytes;
    char plan_boot_action[64],plan_qualification[40];
    int plan_kind,prepare_job;
    bool prepare_known,prepare_mounted,prepare_owned,prepare_unmounting;
    char prepare_token[256],prepare_mountpoint[PATH_LEN],prepare_reason[384];
    BackupEntry backups[MAX_BACKUPS]; int backup_count,selected_backup,list_page;
    /* Restore and Verify: Linux snapshots, or the copies of Android's partitions (2026-11-11). */
    int list_category,copy_pick;PhoneCopy copies[COPIES_MAX];unsigned int copy_count,copy_waiting_count,copy_off;CopyVolume copy_waiting[COPY_WAITING];
    bool copies_known,copy_mounting,copy_list_after;char copy_helper[PATH_LEN],copy_error[256],copy_labels_arg[COPY_PARTS*24+8];
    /* The sheet that asks where a file is: SD cards and USB drives, mounted on a tap. */
    bool source_sheet,source_known,rom_pick,gsi_file,rom_origin;
    /* Screenshot: Power with Volume down, in either order; screenshot.py packs the PNG and places it. */
    pid_t shot_pid;char shot_helper[PATH_LEN],shot_raw[PATH_LEN+48],shot_result[PATH_LEN+56];uint64_t shot_flash,shot_deadline,voldown_at;
    unsigned int shot_sequence;bool voldown_held;int voldown_focus;
    /* The flashlight: the camera's LED as the kernel's LED class shows it, in torch mode. */
    char torch_path[PATH_LEN];unsigned int torch_max,torch_level,torch_saved;bool torch_checked,torch_available,torch_on;
    char backup_new_name[72];bool backup_relist; /* Rename and delete of a finished backup (2026-11-11). */
    int64_t tools_content; /* The height of Settings as last drawn. */
    /* Where saved images are (2026-11-11): userdata, or a card when Android holds userdata. */
    char store_volume[32],store_token[72],store_label[64],store_reason[200];
    uint64_t sideload_checked; /* The last look for a file sideloaded over adb. */
    char card_helper[PATH_LEN],card_error[256],card_linux_arg[16],card_swap_arg[16];CardDisk cards[CARD_DISKS];unsigned int card_count,card_linux,card_swap;int card_index;bool card_known;
    Button buttons[MAX_BUTTONS]; int button_count,focus;
    bool pointer_down,pointer_requires_release; int pressed_action,pressed_hold,pressed_glyph; Rect pressed_rect;
    /* The key just pressed shows above the finger; Delete held down repeats (2026-11-08). */
    uint32_t kb_bubble_cp; Rect kb_bubble_rect; uint64_t kb_bubble_until,kb_repeat_at; bool kb_repeated,kb_drawn;
    Transition tr;TransitionStep tr_steps[TRANSITION_DEPTH];int tr_depth;bool animate;
    /* Back returns to the page this one was opened from (2026-11-10), not to a fixed parent:
       the pages passed on the way, Home first. nav_hold: a read ends for a page already left. */
    enum Screen nav_trail[NAV_TRAIL];int nav_depth;bool nav_backing,nav_hold;
    enum Screen job_page;bool job_page_mark,job_orphan,job_completing;
    unsigned int splash_percent,splash_from,animation,lock_mode,lock_pending_mode,access_mode,lock_failures;int lock_flow,lock_start_y,lock_drag,lock_kept_kind,access_kind;
    uint64_t job_started,nav_settle_end,lock_wait_until,lock_shake_until,access_checked;int nav_settle_kind,nav_settle_amount;enum Screen lock_return,access_return;bool lock_frame_pending,lock_opened,lock_keyboard,lock_digits,tools_export_next,terminal_autostart,power_held,gesture_ready,gesture_terminal,usb_volumes_known,usb_mounting,usb_refresh_after;unsigned int nav_mode;int gesture,gesture_x0,gesture_y0,gesture_x,gesture_y,gesture_anchor_y,usb_rows[USB_VOLUME_ROWS],usb_row_count;uint64_t gesture_still_at;uint32_t*menu_backdrop;enum Screen menu_return;char usb_want[128];pid_t settings_pid;uint64_t settings_retry_at;uint32_t*lock_backdrop;
    char lock_note[96],lock_old[128],lock_new[128],lock_kept[PATH_LEN],access_id[40],access_channel[8],access_peer[72],access_prompt[PATH_LEN];
    char adb_id[40],adb_answered[40],adb_fingerprint[64],adb_comment[100],adb_peer[72];uint64_t adb_checked;enum Screen adb_return; /* An ADB key asked about on the phone. */
    uint64_t pressed_at,now;
    int power_intent,exit_code;
    unsigned int plan_sequence,progress_events;
    enum Screen result_return,fs_inventory_destination,fs_confirm_from;
    FileVolume volumes[MAX_VOLUMES]; int volume_count,volume_index;
    char volume_id[128],fs_path[PATH_LEN],fs_status[256];
    FileEntry files[FILE_BATCH],fs_selected; int file_count;
    unsigned int file_offset,file_total; bool files_more;
    int64_t browser_scroll,volume_scroll,scroll_start_offset;
    int scroll_start_x,scroll_start_y;
    bool scroll_touch,scroll_moved,scroll_dragging,scroll_stop_tap;
    enum ScrollSurface scroll_surface,motion_surface;
    ScrollSamples scroll_samples;
    KineticScroll kinetic;
    unsigned int motion_frames;
    uint64_t motion_render_total,motion_render_max;
    FileRequest fs_request,fs_pending;
    char fs_file[PATH_LEN],fs_sha256[80],fs_text[TEXT_LIMIT+1],fs_prompt[PATH_LEN];
    size_t fs_length,fs_cursor,fs_scroll,prompt_cursor,prompt_scroll;
    uint32_t text_rows[TEXT_LIMIT+2];unsigned int text_row_count;
    uint64_t text_cache_hash;size_t text_cache_length;int text_cache_width;
    bool text_cache_prompt,fs_caret_follow,prompt_caret_follow;
    bool fs_truncated,fs_dirty,fs_new_file,kb_shift,kb_uk,kb_symbols;
    int fs_prompt_kind,fs_destination_index;
    unsigned int fs_confirm_page;
    uint32_t keyboard_keys[56]; int keyboard_count;
    int pointer_x,pointer_y;bool tapped; /* the action in hand was started by a finger, not by a key */
    /* Recovery Wi-Fi (wifi_ui.h); the password stays in memory only while it is joined. */
    char wifi_helper[PATH_LEN],wifi_ssid[132],wifi_ssid_hex[72],wifi_address[48],wifi_password[130],wifi_error[256];
    char wifi_edit_ssid[132],wifi_edit_hex[72],wifi_edit_password[130],wifi_password_file[PATH_LEN];
    bool wifi_known,wifi_available,wifi_on,wifi_connected,wifi_edit_fixed,wifi_edit_saved,browser_ready;char wifi_state_path[PATH_LEN];uint64_t wifi_state_at,wifi_state_stamp;unsigned int wifi_page,wifi_count;bool wifi_from_home,theme_from_home;
    WifiNet wifi_nets[WIFI_MAX];
    /* Installer (installer_ui.h): the catalog, and a new system from it or from a file. */
    CatalogImage catalog[CATALOG_MAX];unsigned int catalog_count,catalog_family,catalog_page;bool catalog_known,catalog_https,catalog_network,catalog_after_status,wifi_from_catalog;
    /* The check of the servers that follows each opening of the catalog (2026-11-08). */
    StoredImage saved[STORE_MAX];unsigned int saved_count,saved_delete;uint64_t saved_free;bool saved_known,catalog_saving;
    /* The catalog's filters (2026-11-10): category, Android version, type, region, fits this phone. */
    unsigned int catalog_category,catalog_version,catalog_kind,catalog_region;bool catalog_all_phones;enum Screen catalog_return;int android_selected;
    unsigned int catalog_only; /* 0: both categories (opened from Home); 1: Linux only; 2: Android only (2026-11-11) */
    bool systems_foreign;      /* userdata holds no Linux (Android's data): a new system formats it */
    bool systems_boot_android; /* BOOT holds no Linux kernel for the platform modules: a new system could not start */
    /* Android's request in misc to erase its data, read by init at the start (bcb.json): asked: the page opened for it;
       prompt: Android could not open its data (it did not ask from its settings). */
    bool reset_asked,reset_prompt,reset_checked,reset_page;
    /* How Android's last start ended (android_log.py, read by init): the page opens once for each failed start. */
    bool alog_checked;uint64_t alog_since;unsigned int alog_seconds,alog_line_count;
    char alog_code[20],alog_needs[12],alog_reason[124],alog_detail[204],alog_id[20],alog_seen[20],alog_lines[6][184];
    char systems_boot_source[12]; /* cache or download: where an installation takes the Linux BOOT from then; empty: nowhere */
    bool systems_boot_outdated; /* BOOT holds a Linux kernel, but not the release this recovery was made for */
    /* Samsung's firmware (samsung_firmware.py): the chosen AP and the reviewed plan. */
    char samsung_helper[PATH_LEN],samsung_volume[96],samsung_token[80],samsung_path[PATH_LEN],samsung_build[32],samsung_plan[80],samsung_warnings[768],samsung_blockers[384],samsung_files[96];
    SamsungRow samsung_rows[SAMSUNG_ROWS];unsigned int samsung_row_count;uint64_t samsung_write_bytes;bool samsung_known,samsung_pick,android_installed,android_gsi,wipe_android,stock_boot_pick,flash_after_targets;char stock_boot_path[PATH_LEN],stock_boot_token[80];
    /* Root (root.py): the tools with their newest version, BOOT's state, the stock BOOT on cache. */
    char root_helper[PATH_LEN],root_boot_kind[16],root_boot_release[64],root_stock_path[PATH_LEN],root_cache_token[80];
    RootTool root_tools[ROOT_TOOLS];unsigned int root_count;int root_selected;bool root_known,root_boot_magisk,root_needs_reset,root_verification;
    /* APatch (2026-11-11): the root BOOT carries ("magisk", "apatch" or none), and the SuperKey typed for
       the tool root_key_tool: in memory until the install starts, then in a file of the private RAM folder. */
    char root_boot_root[16],root_superkey[72],root_key_file[PATH_LEN];int root_key_tool;
    /* A ZIP that installs itself (zip_install.py, 2026-11-11): the chosen file and what its review said. */
    char zip_helper[PATH_LEN],zip_volume[96],zip_token[80],zip_path[PATH_LEN],zip_name[208],zip_sha[72],zip_title[128],zip_what[16],zip_installer[16],zip_warnings[512],zip_parts[160];
    uint64_t zip_size;unsigned int zip_files;bool zip_pick,zip_known;
    /* The list of ZIPs to run: zip_adding: the picker is open for one more; zip_restart: restart the system when all finished. */
    ZipItem zip_queue[ZIP_QUEUE];unsigned int zip_count,zip_at,zip_restart_shown;bool zip_adding,zip_restart,zip_ran;uint64_t zip_restart_at;char gsi_helper[PATH_LEN];char saved_note[160],saved_argument[208]; /* Images saved on the phone; saved_delete counts from 1. */
    bool catalog_checked,catalog_check_done;unsigned int catalog_servers,catalog_updated,catalog_gone,catalog_failed;char catalog_problem[120];
    bool systems_wipe,systems_plan_erase,systems_plan_format,systems_plan_boot,systems_adding;char systems_remote_id[48];uint64_t systems_plan_download;
    Job job;
} App;

static volatile sig_atomic_t stop_signal;
static void on_signal(int s){ (void)s;stop_signal=1; }
static uint64_t milliseconds(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;}
static void copy(char *dst,size_t n,const char *src){if(n)snprintf(dst,n,"%s",src?src:"");}
static bool inside(Rect r,int x,int y){return x>=r.x&&y>=r.y&&x<r.x+r.w&&y<r.y+r.h;}
static bool start_display_control(App*a,enum DisplayCommand command,unsigned int percent);
static void poll_display_control(App*a);
static Rect display_slider_rect(void){return(Rect){52,1010,616,108};}
static unsigned int display_slider_percent(int x){if(x<=76)return 1;if(x>=644)return 100;return 1u+(unsigned int)((x-76)*99+284)/568u;}
/* A running brightness change must not disable or move the slider. */
static bool display_slider_ready(const App*a){return a->display_light_known&&a->display_brightness_supported&&a->display_blank_known&&!a->display_blanked&&!a->display_input_blocked;}
static bool display_slider_usable(const App*a){return display_slider_ready(a)&&(!a->job.pid||a->job.kind==J_DISPLAY_STATUS);}
static bool terminal_job(int kind){return kind>=J_TERM_STATUS&&kind<=J_TERM_STOP;}
static bool terminal_background_job(int kind){return kind==J_TERM_READ||kind==J_TERM_WRITE;}
static bool terminal_screen(const App*a){return a->screen==TERMINAL;}
static bool terminal_session_open(const App*a){return a->terminal_session[0]!=0;}
static bool terminal_input_ready(const App*a){return a->screen==TERMINAL&&a->terminal_known&&a->terminal_available&&!a->terminal_paused&&!strcmp(a->terminal_state,"active")&&terminal_session_open(a)&&!a->terminal_close_requested&&(!a->job.pid||terminal_background_job(a->job.kind));}
static const char*access_dir="/run/a22x-recovery/access";
static const char*adb_dir="/run/a22x-recovery/adb";
static bool job_long(int kind);
static void transition_cancel(App*a);
static void access_close(App*a);
static bool lock_kind(int kind){return kind>=J_LOCK_ENTER&&kind<=J_LOCK_ACCESS;}
static bool lock_prompting(const App*a){return a->screen==FILE_PROMPT&&lock_kind(a->fs_prompt_kind);}
/* The password can be typed while an operation runs, or a lock during a backup could not be opened. */
static bool keyboard_enabled(const App*a){return a->screen==TERMINAL?terminal_input_ready(a):(!a->job.pid||lock_prompting(a));}
static Rect terminal_viewport(void){return(Rect){48,244,TERMINAL_COLS*TERMINAL_CELL_W,TERMINAL_ROWS*TERMINAL_CELL_H};}
static int64_t terminal_max_scroll(const App*a){int64_t value=(int64_t)tm_line_count(&a->terminal)*TERMINAL_CELL_H-terminal_viewport().h;return value>0?value:0;}
static void terminal_scroll_to(App*a,int64_t value){a->terminal_scroll=(int64_t)scroll_clamp((double)value,(double)terminal_max_scroll(a));a->terminal_follow=a->terminal_scroll==terminal_max_scroll(a);a->focus=-1;a->dirty=true;}
static void render_terminal(App*a);
static bool terminal_action(App*a,int id);
static void terminal_pump(App*a);
static void complete_terminal_job(App*a,const Json*j,int kind,bool ok);
static bool systems_job(int kind){return kind>=J_SYS_STATUS&&kind<=J_SYS_REMOVE;}
static Rect systems_viewport(void){return(Rect){28,422,664,566};}
static int64_t systems_max_scroll(const App*a){int64_t n=(int64_t)a->systems_slot_count*126-14-systems_viewport().h;return n>0?n:0;}
static void systems_scroll_to(App*a,int64_t value){a->systems_scroll=(int64_t)scroll_clamp((double)value,(double)systems_max_scroll(a));a->focus=-1;a->dirty=true;}
static bool systems_source_allowed(const FileVolume*v){return v&&v->mounted&&(!strncmp(v->id,"sdcard",6)||!strncmp(v->id,"usb-",4))&&v->token[0];}
/* A card or stick not mounted yet is listed too: a tap mounts it read-only (2026-11-08: the list
   had no way to mount it, so nothing could be chosen). */
static bool systems_source_listed(const FileVolume*v){return v&&(!strncmp(v->id,"sdcard",6)||!strncmp(v->id,"usb-",4))&&v->token[0]&&(v->mounted||v->mountable);}
/* Setup scripts saved on the phone (2026-11-10): cache:/a22x-recovery/scripts, written by an Android app or the recovery. */
static bool script_store_volume(const FileVolume*v){return v&&!strcmp(v->id,"cache")&&v->token[0]&&(v->mounted||v->mountable);}
static bool systems_manifest_selected(const App*a);
static void render_systems(App*a);
static void render_systems_plan(App*a);
static void render_systems_recover(App*a);static void render_systems_remove(App*a);
static void render_wifi(App*a);static void wifi_settings_row(App*a,int y,bool enabled);static bool wifi_action(App*a,int id);
static bool images_job(int kind);static void render_systems_new(App*a);static void render_catalog(App*a);static void render_android_menu(App*a);static void render_samsung_review(App*a);static void render_android_image(App*a);static void open_android_image(App*a,int n);static void render_root(App*a);static void render_superkey_prompt(App*a);static void render_zip_review(App*a);static void render_card(App*a);static void sideload_poll(App*a);static void zip_chosen(App*a);static bool start_flash_job(App*a,int kind);static void render_images(App*a);static bool images_action(App*a,int id);static void open_images(App*a);static void adb_poll(App*a);static void render_adb_request(App*a);static bool adb_action(App*a,int id);static bool start_images_job(App*a,int kind,const char*argument);static bool installer_action(App*a,int id);static bool start_catalog_job(App*a);
static void complete_catalog_job(App*a,const Json*j,bool ok,bool check);static void render_package_prompt(App*a);static void render_systems_apps(App*a);static void render_package_search(App*a);static void render_apps_card(App*a,int y);static void complete_package_search(App*a,const Json*j,bool ok);static void render_identity(App*a);static void render_identity_restore(App*a);static void render_aplan(App*a);static void render_wipe(App*a);static void render_wipe_review(App*a);static bool systems_ready(const App*a);static const char*flash_target_label(const App*a);static int render_aplan_card(App*a,int y);static void render_flash_super(App*a);static void render_icon_sheet(App*a);static void android_plan_tick(App*a);static int render_charger_card(App*a,int y,bool free_ui);static void identity_inventory(App*a);static void installer_card(App*a,Rect r,unsigned int tone,int glyph,const char*title,const char*note,int action,bool enabled);
static void render_systems_shells(App*a);static int shells_append_args(const App*a,char**args,int n);static void shells_summary(const App*a,char*out,size_t size);static bool shells_offered(const App*a);static unsigned int shells_extra(const App*a);static int locale_append_args(const App*a,char**args,int n);static void locale_summary(const App*a,char*out,size_t size);static void render_locale_card(App*a,int y);static void render_systems_locale(App*a);static void render_systems_timezone(App*a);
static void render_systems_manifest(App*a);static void render_systems_done(App*a);static void render_systems_extras(App*a);static void render_systems_script(App*a);static void systems_wifi_focus(App*a,int kind);
static void render_systems_setup(App*a);
static void render_copies(App*a,int top);static void render_copy_detail(App*a);static void open_copies(App*a);static bool copy_job(int kind);static bool start_copy_job(App*a,int kind);
static void screenshot_poll(App*a);static void screenshot_wait(App*a);static void take_screenshot(App*a);
static void render_source_sheet(App*a);static void source_open(App*a);static bool source_listed(const App*a,const FileVolume*v);static bool source_back(App*a);static bool source_pick(const App*a);
static bool systems_action(App*a,int id);static bool systems_mount_screen(int screen_);static void catalog_choose(App*a,const CatalogImage*c);
static bool start_systems_job(App*a,int kind);
static int complete_systems_job(App*a,const Json*j,int kind,bool ok);
static Rect tools_viewport(void){return(Rect){28,230,664,1254};}
/* USB files keeps its main button in place under the list, so its list ends higher. */
/* The Android review keeps its hold button under the list (2026-11-10), and the image page two buttons. */
static int page_height(const App*a){return a->screen==LIST?860:(a->screen==USB_FILES?1070:(a->screen==SYSTEMS_SEARCH?1086:(a->screen==SAMSUNG_REVIEW?1074:(a->screen==ANDROID_IMAGE?960:1254))));}
/* Restore and Verify keep their two categories above what scrolls (2026-11-11). */
static int page_top(const App*a){return a->screen==LIST?316:230;}
static Rect page_viewport(const App*a){return(Rect){28,page_top(a),664,page_height(a)};}
static bool page_scrolls(const App*a){return (a->screen==LIST&&a->list_category==1&&!a->install.pick_backup)||a->screen==ROOT_PAGE||a->screen==SAMSUNG_REVIEW||a->screen==ANDROID_IMAGE||a->screen==THEME||a->screen==TERMINAL_THEME||a->screen==SYSTEM||a->screen==USB_FILES||a->screen==SYSTEMS_CATALOG||a->screen==SYSTEMS_SEARCH||a->screen==SYSTEMS_TIMEZONE;}
static int64_t page_max_scroll(const App*a){return a->page_content>page_height(a)?a->page_content-page_height(a):0;}
/* The page measures itself when it is drawn (2026-11-11: a fixed sum left an empty stretch below
   the last card wherever the charging card was absent); the sum serves only before the first frame. */
static int64_t tools_max_scroll(const App*a){
    if(!a->tools_content)return 1308+212+268+(a->lock_enabled?188:0);
    int64_t most=a->tools_content-1254;return most>0?most:0;
}
static void page_scroll_to(App*a,int64_t value){a->page_scroll=(int64_t)scroll_clamp((double)value,(double)page_max_scroll(a));a->focus=-1;a->button_count=0;a->dirty=true;}
static void tools_scroll_to(App*a,int64_t value){a->tools_scroll=(int64_t)scroll_clamp((double)value,(double)tools_max_scroll(a));a->focus=-1;a->button_count=0;a->dirty=true;}
static bool install_job(int kind){return kind>=J_INSTALL_STATUS&&kind<=J_INSTALL_EXECUTE;}
static bool wifi_job(int kind){return kind>=J_WIFI_STATUS&&kind<=J_WIFI_FORGET;}
/* These only read state. The page stays usable while they run: a tap waits for the
 * result instead of the whole page going grey. */
static bool job_quiet(int kind){return wifi_job(kind)||kind==J_APLAN_SHOW||kind==J_APLAN_PREPARE||kind==J_APLAN_FORGET||kind==J_IMAGES||kind==J_CATALOG||kind==J_CATALOG_CHECK||kind==J_PKG_SEARCH||kind==J_ID_LIST||kind==J_ID_BACKUPS||kind==J_CHARGER||kind==J_SYSINFO||kind==J_TOOLS_STATUS||kind==J_DISPLAY_STATUS||kind==J_DISPLAY_SET||kind==J_USB_STATUS||kind==J_SYS_STATUS||kind==J_INSTALL_STATUS||kind==J_STORAGE_INVENTORY||kind==J_FS_INVENTORY||kind==J_FS_LIST||kind==J_LIST||kind==J_FLASH_TARGETS||kind==J_USB_SYNC||kind==J_TERM_STATUS||kind==J_TERM_STOP||kind==J_CARD_LIST||kind==J_COPY_LIST||kind==J_ROM_KIND;}
enum { NAV_GESTURES,NAV_BUTTONS,NAV_OFF };
enum { GESTURE_NONE,GESTURE_BACK,GESTURE_BOTTOM };
static bool flash_job(int kind){return kind>=J_FLASH_TARGETS&&kind<=J_FLASH_WRITE;}
static bool theme_job(int kind){return kind==J_THEME_IMPORT||kind==J_THEME_REMOVE||kind==J_THEME_RESTORE;}
static bool install_source_allowed(const FileVolume*v){return v&&v->mounted&&!v->read_only&&v->write_supported&&!strcmp(v->fs_type,"ext4")&&!strncmp(v->id,"sdcard",6)&&v->token[0];}
static Rect install_review_viewport(void){return(Rect){28,240,664,1016};}
static Rect install_pending_viewport(void){return(Rect){28,430,664,740};}
static int64_t install_review_max_scroll(const App*a){int64_t value=(int64_t)a->install.row_count*TEXT_ROW_HEIGHT+32-install_review_viewport().h;return value>0?value:0;}
static int64_t install_pending_max_scroll(const App*a){int64_t value=(int64_t)a->install.pending_count*202-14-install_pending_viewport().h;return value>0?value:0;}
static void install_scroll_to(App*a,bool pending,int64_t value){int64_t clamped=(int64_t)scroll_clamp((double)value,(double)(pending?install_pending_max_scroll(a):install_review_max_scroll(a)));if(pending)a->install.pending_scroll=clamped;else a->install.review_scroll=clamped;a->focus=-1;a->button_count=0;a->dirty=true;}
static void render_install(App*a);
static void render_install_review(App*a,bool result);
static void render_install_source(App*a);
static bool install_action(App*a,int id);
static bool start_install_job(App*a,int kind);
static int complete_install_job(App*a,const Json*j,int kind,bool ok);
static void complete_flash_job(App*a,const Json*j,int kind,bool ok);
static void action(App*a,int id);
static void update_battery(App*a);static void reset_request_poll(App*a);static void render_android_log(App*a);static void android_log_poll(App*a);static bool android_log_action(App*a,int id);static void zip_item_to_review(App*a,const ZipItem*it);static void settings_save(App*a,bool wait);
static long sysfs_number(const char*path,long fallback);
static void complete_usb_sync(App*a,const Json*j,bool ok);
static void complete_usb_backup(App*a,const Json*j,bool ok);
static void complete_usb_eject(App*a,const Json*j,bool ok);
static void settings_changed(App*a);
static bool start_usb_backup(App*a,int index);
static bool repository_needs_setup(const App*a,int index);
static void scroll_indicator(App*a,Rect track,int64_t offset,int64_t total,uint32_t color);
static void append(char*dst,size_t n,const char*s);
static bool flash_source_allowed(const FileVolume*v);
static void render_flash(App*a);
static void render_flash_review(App*a);
static void render_language(App*a);
static void render_theme(App*a);
static void render_terminal_theme(App*a);
static void lock_frost(App*a);
static void render_animations(App*a);
static void render_lockscreen(App*a);
static void render_power_menu(App*a);
static void render_access(App*a);
static bool flash_action(App*a,int id);
static const char*scroll_surface_name(enum ScrollSurface kind){switch(kind){case SCROLL_FOLDER:return "folders";case SCROLL_VOLUMES:return "volumes";case SCROLL_PROMPT:return "prompt";case SCROLL_TERMINAL:return "terminal";case SCROLL_SYSTEMS:return "systems";case SCROLL_TOOLS:return "tools";case SCROLL_PAGE:return "page";case SCROLL_INSTALL_REVIEW:return "install-review";case SCROLL_INSTALL_PENDING:return "install-pending";default:return "text";}}
static void stop_inertia(App*a,const char*reason){
    if(a->motion_surface!=SCROLL_NONE){
        fprintf(stderr,"[recovery-ui:kinetic-stop] surface=%s offset_px=%" PRId64 " reason=%s frames=%u elapsed_ms=%" PRIu64,scroll_surface_name(a->motion_surface),(int64_t)(a->kinetic.position+.5),reason,a->motion_frames,a->now>=a->kinetic.started_at?a->now-a->kinetic.started_at:0);
        if(a->profile_render)fprintf(stderr," render_max_ms=%" PRIu64 " render_total_ms=%" PRIu64,a->motion_render_max,a->motion_render_total);
        fputc('\n',stderr);
    }
    a->kinetic.active=false;a->kinetic.velocity=0;a->motion_surface=SCROLL_NONE;
}
/* Layout stays on a 720x1600 grid. One unit is 1.5 panel pixels, and every
 * primitive draws at final size. */
static inline int nx(int value){return(value*3)>>1;}
static bool job_quiet(int kind);
static bool ui_free(const App*a){return !a->job.pid||job_quiet(a->job.kind)||(!terminal_screen(a)&&terminal_background_job(a->job.kind));}
/* Text is looked up by its English form at draw time, so messages from helpers
 * translate too. File names and other user data must bypass the lookup. */
static unsigned int ui_language;static bool ui_raw;
static const char*tr(const char*s){
    if(!ui_language||ui_raw||!s[0])return s;
    int lo=0,hi=MESSAGE_COUNT-1;
    while(lo<=hi){int middle=(lo+hi)/2,order=strcmp(s,message_ids[middle]);if(!order){const char*text=message_text[ui_language-1][middle];return text?text:s;}if(order<0)hi=middle-1;else lo=middle+1;}
    return s;
}
#define T(s) tr(s)
static void raw_text(bool raw){ui_raw=raw;}
static Bounds canvas_bounds(const App*a){
    Bounds b={0,0,NW,NH};
    if(a->clip_active){
        int x0=nx(a->clip.x),y0=nx(a->clip.y),x1=nx(a->clip.x+a->clip.w),y1=nx(a->clip.y+a->clip.h);
        if(x0>b.x0)b.x0=x0;
        if(y0>b.y0)b.y0=y0;
        if(x1<b.x1)b.x1=x1;
        if(y1<b.y1)b.y1=y1;
    }
    return b;
}
static void rectangle(App *a,Rect r,uint32_t color){
    int x0=nx(r.x),y0=nx(r.y),x1=nx(r.x+r.w),y1=nx(r.y+r.h);
    if(r.w>0&&x1==x0)x1++;
    if(r.h>0&&y1==y0)y1++;
    canvas_rect(a->pixels,canvas_bounds(a),x0,y0,x1,y1,color,255);
}
/* The theme's backdrop, or its plain ground, once more over a part of the page. */
static void backdrop_again(App*a,Rect r){
    int x0=nx(r.x),y0=nx(r.y),x1=nx(r.x+r.w),y1=nx(r.y+r.h);
    if(x0<0)x0=0;
    if(y0<0)y0=0;
    if(x1>NW)x1=NW;
    if(y1>NH)y1=NH;
    if(x1<=x0||y1<=y0)return;
    if(ui_theme->backdrop&&a->backdrop)for(int y=y0;y<y1;y++)memcpy(a->pixels+(size_t)y*NW+x0,a->backdrop+(size_t)y*NW+x0,(size_t)(x1-x0)*sizeof(uint32_t));
    else canvas_rect(a->pixels,canvas_bounds(a),x0,y0,x1,y1,BG,255);
}
static void gradient(App*a,Rect r,int radius,uint32_t top,uint32_t bottom){canvas_rounded(a->pixels,canvas_bounds(a),nx(r.x),nx(r.y),nx(r.x+r.w),nx(r.y+r.h),(float)radius*1.5f,top,bottom,255);}
static void veil(App*a,Rect r,int radius,uint32_t color,unsigned int alpha){canvas_rounded(a->pixels,canvas_bounds(a),nx(r.x),nx(r.y),nx(r.x+r.w),nx(r.y+r.h),(float)radius*1.5f,color,color,alpha);}
/* Surfaces carry a one-pixel lighter edge, so cards read as raised on the dark page. */
/* Surfaces carry a one-pixel lighter edge, so cards read as raised on the page. On a
 * backdrop they turn to glass: the soft gradient behind already looks blurred. */
static uint32_t surface_edge(uint32_t color){return color==PANEL?EDGE:(color==ORANGE_DARK?ORANGE_EDGE:color);}
static void rounded(App *a,Rect r,int radius,uint32_t color){
    Bounds b=canvas_bounds(a);int x0=nx(r.x),y0=nx(r.y),x1=nx(r.x+r.w),y1=nx(r.y+r.h);float native=(float)radius*1.5f;uint32_t edge=surface_edge(color);
    if(color==PANEL&&ui_theme->surface_alpha<255){
        /* Scrolling lists stay flat: a shadow per row would cost frames for little gain. */
        if(ui_theme->shadow_strength&&!a->clip_active&&r.h>=70)canvas_shadow(a->pixels,b,x0,y0,x1,y1,native,ui_theme->shadow,ui_theme->shadow_strength);
        canvas_rounded(a->pixels,b,x0,y0,x1,y1,native,edge,edge,ui_theme->edge_alpha);canvas_rounded(a->pixels,b,x0+2,y0+2,x1-2,y1-2,native-2.f,color,color,ui_theme->surface_alpha);return;
    }
    if(edge==color){canvas_rounded(a->pixels,b,x0,y0,x1,y1,native,color,color,255);return;}
    canvas_rounded(a->pixels,b,x0,y0,x1,y1,native,edge,edge,255);canvas_rounded(a->pixels,b,x0+2,y0+2,x1-2,y1-2,native-2.f,color,color,255);
}
static void vector_line(App*a,int x0,int y0,int x1,int y1,int width,uint32_t color){canvas_capsule(a->pixels,canvas_bounds(a),(float)x0*1.5f,(float)y0*1.5f,(float)x1*1.5f,(float)y1*1.5f,(float)width*1.5f,color);}
static void vector_circle(App*a,int cx,int cy,int radius,int width,uint32_t color){canvas_ring(a->pixels,canvas_bounds(a),(float)cx*1.5f,(float)cy*1.5f,(float)radius*1.5f,(float)width*1.5f,color);}
static void vector_rectangle(App*a,Rect r,uint32_t color){rectangle(a,r,color);}
static void vector_arc(App*a,int cx,int cy,int radius,int width,float start,float sweep,uint32_t color){
    canvas_arc(a->pixels,canvas_bounds(a),(float)cx*1.5f,(float)cy*1.5f,(float)radius*1.5f,(float)width*1.5f,start,sweep,color);
}
static void line(App *a,int x0,int y0,int x1,int y1,int width,uint32_t color){
    if(y0==y1)rectangle(a,(Rect){x0<x1?x0:x1,y0-width/2,abs(x1-x0),width},color);
    else if(x0==x1)rectangle(a,(Rect){x0-width/2,y0<y1?y0:y1,width,abs(y1-y0)},color);
    else vector_line(a,x0,y0,x1,y1,width,color);
}
static uint32_t utf8_next(const char **cursor){
    const unsigned char*p=(const unsigned char*)*cursor;
    if(!*p)return 0;
    if(*p<0x80){*cursor+=1;return *p;}
    int n=*p>=0xc2&&*p<=0xdf?2:(*p>=0xe0&&*p<=0xef?3:(*p>=0xf0&&*p<=0xf4?4:0));
    if(!n){*cursor+=1;return 0xfffd;}
    uint32_t cp=*p&((1u<<(7-n))-1u);
    for(int k=1;k<n;k++){if((p[k]&0xc0)!=0x80){*cursor+=1;return 0xfffd;}cp=(cp<<6)|(p[k]&63u);}
    if((n==3&&cp<0x800)||(n==4&&cp<0x10000)||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff)){*cursor+=1;return 0xfffd;}
    *cursor+=n;return cp;
}
static size_t utf8_previous(const char*s,size_t position){
    if(!position)return 0;
    position--;while(position&&((unsigned char)s[position]&0xc0)==0x80)position--;
    return position;
}
static size_t utf8_prefix(const char*s,size_t limit){
    const char*p=s,*last=s;while(*p){const char*before=p;(void)utf8_next(&p);if((size_t)(p-s)>limit)return(size_t)(before-s);last=p;}return(size_t)(last-s);
}
static size_t utf8_encode(uint32_t cp,char out[5]){
    size_t n;
    if(cp<0x80){out[0]=(char)cp;n=1;}
    else if(cp<0x800){out[0]=(char)(0xc0|(cp>>6));out[1]=(char)(0x80|(cp&63));n=2;}
    else if(cp<0x10000){out[0]=(char)(0xe0|(cp>>12));out[1]=(char)(0x80|((cp>>6)&63));out[2]=(char)(0x80|(cp&63));n=3;}
    else{out[0]=(char)(0xf0|(cp>>18));out[1]=(char)(0x80|((cp>>12)&63));out[2]=(char)(0x80|((cp>>6)&63));out[3]=(char)(0x80|(cp&63));n=4;}
    out[n]=0;return n;
}
static const Glyph*find_glyph(uint32_t cp,int font,const uint8_t**alpha){
    if(cp>=32&&cp<=126){*alpha=font_alpha;return&glyphs[font][cp-32];}
    int lo=0,hi=EXTRA_GLYPHS;
    while(lo<hi){int middle=(lo+hi)/2;if(extra_codepoints[middle]<cp)lo=middle+1;else hi=middle;}
    if(lo>=EXTRA_GLYPHS||extra_codepoints[lo]!=cp)lo=EXTRA_GLYPHS-1;
    *alpha=extra_font_alpha;return&extra_glyphs[font][lo];
}
/* Advances are 1/64 panel pixel, so text keeps its real spacing at every size. */
static int codepoint_advance(uint32_t cp,int font){
    if(cp=='\t')return glyphs[font][0].advance*4;
    if(cp=='\r'||cp=='\n')return 0;
    const uint8_t*alpha;return find_glyph(cp,font,&alpha)->advance;
}
static int advance_units(int advance){return(advance*2+95)/192;}
static int codepoint_width(uint32_t cp,int font){return advance_units(codepoint_advance(cp,font));}
static void draw_glyph(App*a,Bounds b,int pen,int y,int font,uint32_t color,uint32_t cp){
    if(cp=='\t'||cp=='\r'||cp=='\n')return;
    const uint8_t*alpha;const Glyph*g=find_glyph(cp,font,&alpha);
    if(g->w&&g->h)canvas_mask(a->pixels,b,((pen+32)>>6)+g->x,y+g->y,g->w,g->h,alpha+g->off,color);
}
static int draw_codepoint(App*a,int x,int y,int font,uint32_t color,uint32_t cp){draw_glyph(a,canvas_bounds(a),nx(x)*64,nx(y),font,color,cp);return codepoint_width(cp,font);}
static int text_width(const char *s,int font){s=tr(s);int w=0;while(*s)w+=codepoint_advance(utf8_next(&s),font);return(w*2+191)/192;}
static void text(App *a,int x,int y,int font,uint32_t color,const char *s){
    s=tr(s);Bounds b=canvas_bounds(a);int pen=nx(x)*64,top=nx(y);
    while(*s){uint32_t cp=utf8_next(&s);draw_glyph(a,b,pen,top,font,color,cp);pen+=codepoint_advance(cp,font);}
}
static void centered(App*a,Rect r,int font,uint32_t color,const char*s){text(a,r.x+(r.w-text_width(s,font))/2,r.y+(r.h-(int[]){24,31,40,59}[font])/2,font,color,s);}
static bool wrap_measuring; /* wrapped() counts lines without drawing them. */
static int wrapped(App*a,int x,int y,int width,int font,uint32_t color,const char*src,int max_lines){
    src=tr(src);const int height=(int[]){28,36,46,64}[font];char row[2048];int lines=0;const char*p=src;
    while(lines<max_lines){
        const char*start=p,*end=p,*next=p,*space=NULL;int used=0;
        while(*p){
            const char*before=p;uint32_t cp=utf8_next(&p);int advance=codepoint_advance(cp,font);
            if(cp=='\n'){end=before;next=p;break;}
            if((used+advance>width*96||p-start>2000)&&before>start){
                end=space?space:before;next=space?space+1:before;break;
            }
            if(cp==' ')space=before;
            used+=advance;end=p;next=p;
        }
        size_t n=(size_t)(end-start);if(n>=sizeof(row))n=utf8_prefix(start,sizeof(row)-1);
        memcpy(row,start,n);row[n]=0;if(!wrap_measuring){bool raw=ui_raw;ui_raw=true;text(a,x,y+height*lines,font,color,row);ui_raw=raw;}lines++;
        if(!*next)break;
        p=next;
    }
    return lines*height;
}
/* A paragraph of a page in a tile of its own (2026-11-11: the owner asked for the text of the
   pages in tiles). The text starts where the paragraph did; a warning gets a bar of its colour. */
static int text_tile(App*a,int y,int font,uint32_t color,const char*src,int max_lines);
/* The height wrapped() would take, for a card drawn before its text. */
static int wrapped_height(App*a,int width,int font,const char*src,int max_lines){
    wrap_measuring=true;int height=wrapped(a,0,0,width,font,0,src,max_lines);wrap_measuring=false;return height;
}
static int text_tile(App*a,int y,int font,uint32_t color,const char*src,int max_lines){
    int h=wrapped_height(a,604,font,src,max_lines);bool accent=color!=MUTED&&color!=TEXT;
    rounded(a,(Rect){28,y-16,664,h+32},22,PANEL);
    if(accent)rounded(a,(Rect){38,y-2,5,h+4},3,color);
    wrapped(a,accent?60:52,y,accent?600:612,font,accent?color:color,src,max_lines);
    return h+32;
}
static void ellipsis(App*a,int x,int y,int width,int font,uint32_t color,const char*src){
    src=tr(src);char out[512];size_t n=utf8_prefix(src,sizeof(out)-4);memcpy(out,src,n);out[n]=0;
    bool shortened=src[n]!=0;
    if(shortened||text_width(out,font)>width){
        while(n&&text_width(out,font)+text_width("...",font)>width){n=utf8_previous(out,n);out[n]=0;}
        memcpy(out+n,"...",4);
    }
    bool raw=ui_raw;ui_raw=true;text(a,x,y,font,color,out);ui_raw=raw;
}
static void human_bytes(uint64_t bytes,char*out,size_t n){
    const char*units[]={"B","KiB","MiB","GiB","TiB"};double v=(double)bytes;int i=0;while(v>=1024.0&&i<4){v/=1024.0;i++;}
    if(i==0)snprintf(out,n,"%"PRIu64" B",bytes);else snprintf(out,n,"%.1f %s",v,units[i]);
}
/* Every icon sits on one 48-unit grid and its stroke scales with it. */
static float icon_stroke(int size){return(float)size*.117f;}
static void icon_path(App*a,int x,int y,int size,uint32_t c,const int*p,int points){
    Bounds b=canvas_bounds(a);float scale=(float)size*1.5f/48.f,ox=(float)x*1.5f,oy=(float)y*1.5f,stroke=icon_stroke(size);
    for(int n=0;n+1<points;n++)canvas_capsule(a->pixels,b,ox+(float)p[2*n]*scale,oy+(float)p[2*n+1]*scale,ox+(float)p[2*n+2]*scale,oy+(float)p[2*n+3]*scale,stroke,c);
}
#define ICON_PATH(...) do{static const int points_[]={__VA_ARGS__};icon_path(a,x,y,size,c,points_,(int)(sizeof(points_)/sizeof(points_[0]))/2);}while(0)
static void icon_ring(App*a,int x,int y,int size,uint32_t c,float cx,float cy,float radius){
    float scale=(float)size*1.5f/48.f,stroke=icon_stroke(size);
    canvas_ring(a->pixels,canvas_bounds(a),(float)x*1.5f+cx*scale,(float)y*1.5f+cy*scale,radius*scale+stroke*.5f,stroke,c);
}
static void icon_arc(App*a,int x,int y,int size,uint32_t c,int cx,int cy,int radius,int from,int to){
    const double degree=3.14159265358979323846/180.0;Bounds b=canvas_bounds(a);float scale=(float)size*1.5f/48.f,ox=(float)x*1.5f,oy=(float)y*1.5f,stroke=icon_stroke(size);
    for(int angle=from;angle<to;angle+=5){double p=angle*degree,q=(angle+5)*degree;
        canvas_capsule(a->pixels,b,ox+(float)(cx+radius*cos(p))*scale,oy+(float)(cy+radius*sin(p))*scale,ox+(float)(cx+radius*cos(q))*scale,oy+(float)(cy+radius*sin(q))*scale,stroke,c);}
}
static void icon(App*a,int id,int x,int y,int size,uint32_t c){
    switch(id){
    case A_BACKUP:ICON_PATH(7,29,7,40,41,40,41,29);ICON_PATH(24,31,24,8);ICON_PATH(14,18,24,8,34,18);break;
    case A_RESTORE:ICON_PATH(7,29,7,40,41,40,41,29);ICON_PATH(24,8,24,31);ICON_PATH(14,21,24,31,34,21);break;
    case A_VERIFY:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(15,25,22,32,34,17);break;
    case A_FILES:ICON_PATH(6,38,6,11,19,11,24,17,42,17,42,38,6,38);break;
    case A_STORAGE:ICON_PATH(12,6,31,6,40,15,40,42,12,42,12,6);ICON_PATH(20,14,20,20);ICON_PATH(27,14,27,20);break;
    case A_POWER:icon_arc(a,x,y,size,c,24,26,16,-50,230);ICON_PATH(24,7,24,24);break;
    case A_SYSTEM:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(24,23,24,33);ICON_PATH(24,15,24,15);break;
    case ICON_NAV:ICON_PATH(10,40,38,40);ICON_PATH(24,30,24,8);ICON_PATH(15,17,24,8,33,17);break;
    case ICON_TRASH:ICON_PATH(8,13,40,13);ICON_PATH(18,13,18,7,30,7,30,13);ICON_PATH(12,13,14,41,34,41,36,13);ICON_PATH(20,21,20,33);ICON_PATH(28,21,28,33);break;
    case ICON_KEYBOARD:ICON_PATH(5,12,43,12,43,36,5,36,5,12);ICON_PATH(12,20,13,20);ICON_PATH(20,20,21,20);ICON_PATH(28,20,29,20);ICON_PATH(36,20,37,20);ICON_PATH(15,29,33,29);break;
    case ICON_ERASE:ICON_PATH(18,11,43,11,43,37,18,37,5,24,18,11);ICON_PATH(24,18,36,30);ICON_PATH(36,18,24,30);break;
    case A_OS:ICON_PATH(24,7,42,16,24,25,6,16,24,7);ICON_PATH(6,25,24,34,42,25);ICON_PATH(6,34,24,43,42,34);break;
    case ICON_LINUX:ICON_PATH(6,10,42,10,42,38,6,38,6,10);ICON_PATH(14,18,21,24,14,30);ICON_PATH(25,31,34,31);break;
    case ICON_PHONE:ICON_PATH(14,6,34,6,34,42,14,42,14,6);ICON_PATH(21,36,27,36);break;
    case ICON_FLASH:ICON_PATH(27,6,11,27,23,27,21,42,37,21,25,21,27,6);break;
    case ICON_CHEVRON:ICON_PATH(18,10,32,24,18,38);break;
    case ICON_BACK:ICON_PATH(30,10,16,24,30,38);break;
    case ICON_LANGUAGE:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(6,24,42,24);icon_arc(a,x,y,size,c,44,24,27,138,222);icon_arc(a,x,y,size,c,4,24,27,-42,42);break;
    case ICON_VIBRATE:ICON_PATH(16,8,32,8,32,40,16,40,16,8);ICON_PATH(9,16,5,20,9,24,5,28,9,32);ICON_PATH(39,16,43,20,39,24,43,28,39,32);break;
    case ICON_DISPLAY:icon_ring(a,x,y,size,c,24,24,9);ICON_PATH(24,5,24,9);ICON_PATH(24,39,24,43);ICON_PATH(5,24,9,24);ICON_PATH(39,24,43,24);ICON_PATH(10,10,13,13);ICON_PATH(35,35,38,38);ICON_PATH(38,10,35,13);ICON_PATH(13,35,10,38);break;
    case ICON_CLOCK:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(24,13,24,24,32,29);break;
    case ICON_BROWSER:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(24,11,29,24,24,37,19,24,24,11);break;
    /* A hand torch, head up: the head, the barrel and its switch. */
    case ICON_TORCH:ICON_PATH(13,7,35,7,31,19,17,19,13,7);ICON_PATH(17,19,17,42,31,42,31,19);ICON_PATH(24,26,24,32);break;
    case ICON_WIFI:for(int r=8;r<=26;r+=9)icon_arc(a,x,y,size,c,24,40,r,225,315);ICON_PATH(24,40,24,40);break;
    case ICON_LOCK:ICON_PATH(10,22,38,22,38,42,10,42,10,22);icon_arc(a,x,y,size,c,24,17,10,180,360);ICON_PATH(14,17,14,22);ICON_PATH(34,17,34,22);break;
    case ICON_THEME:icon_ring(a,x,y,size,c,24,24,18);ICON_PATH(24,6,24,42);ICON_PATH(24,12,33,14);ICON_PATH(24,20,41,21);ICON_PATH(24,28,41,28);ICON_PATH(24,36,33,35);break;
    case ICON_USB:ICON_PATH(18,5,30,5,30,17);ICON_PATH(18,5,18,17);ICON_PATH(13,17,35,17,35,39);ICON_PATH(13,17,13,39);icon_arc(a,x,y,size,c,19,37,6,90,180);icon_arc(a,x,y,size,c,29,37,6,0,90);ICON_PATH(19,43,29,43);ICON_PATH(22,10,22,12);ICON_PATH(26,10,26,12);ICON_PATH(24,27,24,29);break;
    case ICON_TUNE:ICON_PATH(6,14,24,14);ICON_PATH(34,14,42,14);icon_ring(a,x,y,size,c,29,14,4);ICON_PATH(6,34,14,34);ICON_PATH(24,34,42,34);icon_ring(a,x,y,size,c,19,34,4);break;
    default:break;
    }
}
static void register_button(App*a,Rect r,int action,bool enabled,int hold){
    if(a->clip_active){int x=r.x>a->clip.x?r.x:a->clip.x,y=r.y>a->clip.y?r.y:a->clip.y;
        int right=r.x+r.w<a->clip.x+a->clip.w?r.x+r.w:a->clip.x+a->clip.w,bottom=r.y+r.h<a->clip.y+a->clip.h?r.y+r.h:a->clip.y+a->clip.h;
        r=(Rect){x,y,right-x,bottom-y};if(r.w<=0||r.h<=0)return;}
    if(a->button_count<MAX_BUTTONS)a->buttons[a->button_count++]=(Button){r,action,hold,enabled,0};
}
static bool pressed(const App*a,int action){return a->pointer_down&&a->pressed_action==action;}
static void surface(App*a,Rect r,int radius,uint32_t color,int action){rounded(a,r,radius,color);if(pressed(a,action))veil(a,r,radius,0xffffffu,16);}
static void icon_tile(App*a,int id,int x,int y,int box,uint32_t fill){
    gradient(a,(Rect){x,y,box,box},box*3/10,fill,fill);int size=box*5/8;icon(a,id,x+(box-size)/2,y+(box-size)/2,size,ORANGE);
}
/* A tile or row remembers its glyph, so the opening page can grow out of it. */
static void button_glyph(App*a,int action,int glyph){if(a->button_count&&a->buttons[a->button_count-1].action==action)a->buttons[a->button_count-1].glyph=glyph;}
static void button(App*a,Rect r,const char*label,int action,bool enabled,bool primary,int hold){
    uint32_t bg=enabled?(primary?ORANGE:PANEL2):(primary?ORANGE_DARK:PANEL),fg=enabled?(primary?ON_ORANGE:TEXT):(primary?TEXT:MUTED);
    int radius=r.w<120?14:(r.h/2>44?44:r.h/2);bool down=enabled&&pressed(a,action);if(down&&ui_theme->bento)radius=radius*3/5; /* The shape answers the touch. */
    rounded(a,r,radius,bg);
    if(down)veil(a,r,radius,primary?0u:0xffffffu,24);
    if(hold&&down){
        /* A hold fills the button from the left, so progress reads without a separate bar. */
        uint64_t elapsed=a->now-a->pressed_at;int fill=(int)((elapsed>=(uint64_t)hold?(uint64_t)hold:elapsed)*(uint64_t)r.w/(uint64_t)hold);
        bool clipped=a->clip_active;Rect saved=a->clip,part={r.x,r.y,fill,r.h};
        if(clipped){int right=part.x+part.w<saved.x+saved.w?part.x+part.w:saved.x+saved.w,bottom=part.y+part.h<saved.y+saved.h?part.y+part.h:saved.y+saved.h;part.x=part.x>saved.x?part.x:saved.x;part.y=part.y>saved.y?part.y:saved.y;part.w=right-part.x;part.h=bottom-part.y;}
        if(part.w>0&&part.h>0){a->clip_active=true;a->clip=part;veil(a,r,radius,primary?ON_ORANGE:ORANGE,primary?70:110);a->clip_active=clipped;a->clip=saved;}
    }
    int cx=r.x+r.w/2,cy=r.y+r.h/2;
    if(!strcmp(label,"↑")||!strcmp(label,"↓")){int direction=!strcmp(label,"↑")?-1:1;
        vector_line(a,cx,cy-15*direction,cx,cy+15*direction,2,fg);vector_line(a,cx,cy+15*direction,cx-8,cy+7*direction,2,fg);vector_line(a,cx,cy+15*direction,cx+8,cy+7*direction,2,fg);}
    else if(!strcmp(label,"←")||!strcmp(label,"→")){int direction=!strcmp(label,"←")?-1:1;
        vector_line(a,cx-15*direction,cy,cx+15*direction,cy,2,fg);vector_line(a,cx+15*direction,cy,cx+7*direction,cy-8,2,fg);vector_line(a,cx+15*direction,cy,cx+7*direction,cy+8,2,fg);}
    else if(!strcmp(label,"...")){for(int n=-1;n<=1;n++)vector_circle(a,cx+n*10,cy,2,2,fg);}
    else{
        /* Translations differ in length: step down a size, then shorten, rather than overflow. */
        int font=text_width(label,1)<=r.w-24?1:0;
        if(text_width(label,font)<=r.w-16)centered(a,r,font,fg,label);else ellipsis(a,r.x+10,r.y+(r.h-24)/2,r.w-20,0,fg,label);
    }
    register_button(a,r,action,enabled,hold);
}
/* The recovery clock (2026-11-09): a tz database zone keeps summer time through its POSIX rule
   (TZ is set when the zone is chosen or loaded); without one, the fixed offset as before. */
static bool recovery_zone(const App*a){return a->recovery_tz>=0&&(unsigned int)a->recovery_tz<TZ_ZONES;}
static void apply_recovery_tz(const App*a){setenv("TZ",recovery_zone(a)?tz_zones[a->recovery_tz].posix:"UTC0",1);tzset();}
static void recovery_time(const App*a,time_t when,struct tm*t){
    if(recovery_zone(a)){localtime_r(&when,t);return;}
    time_t value=when+(time_t)a->utc_offset*60;gmtime_r(&value,t);
}
/* Minutes east of UTC on the recovery clock now. */
static int recovery_offset_now(const App*a){
    if(!recovery_zone(a))return a->utc_offset;
    time_t now=time(NULL);struct tm t;localtime_r(&now,&t);return (int)(t.tm_gmtoff/60);
}
static void clock_text(const App*a,char*now,size_t now_size,char*date,size_t date_size){
    static const char*const days[]={"Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"};
    static const char*const months[]={"January","February","March","April","May","June","July","August","September","October","November","December"};
    time_t value=a->preview?(time_t)1793990700:time(NULL);struct tm t;recovery_time(a,value,&t);
    snprintf(now,now_size,"%02d:%02d",t.tm_hour,t.tm_min);snprintf(date,date_size,T("%s, %d %s"),tr(days[t.tm_wday]),t.tm_mday,tr(months[t.tm_mon]));
}
/* Battery beside the clock: a bolt while charging, a red body and a mark below 20%. */
static int battery_status(App*a,int right){
    if(!a->battery[0])return right;
    bool low=a->battery_percent<20&&!a->battery_charging;uint32_t ink=low?RED:(a->battery_charging?GREEN:TEXT);Bounds b=canvas_bounds(a);
    int width=text_width(a->battery,1),x=right-width;text(a,x,27,1,low?RED:TEXT,a->battery);x-=68;
    int x0=nx(x),y0=nx(31),x1=nx(x+54),y1=nx(57),inner=x1-x0-8,level=inner*a->battery_percent/100;if(level<6)level=6;
    canvas_rounded(a->pixels,b,x0,y0,x1,y1,13.f,ink,ink,70);canvas_rounded(a->pixels,b,x0+4,y0+4,x0+4+level,y1-4,9.f,ink,ink,255);
    canvas_rounded(a->pixels,b,x1+3,y0+11,x1+8,y1-11,3.f,ink,ink,170);
    if(a->battery_charging){x-=36;icon(a,ICON_FLASH,x,27,34,GREEN);}
    else if(low){x-=18;vector_line(a,x+6,33,x+6,47,4,RED);vector_circle(a,x+6,55,3,3,RED);}
    return x;
}
/* Wi-Fi beside the battery: lit arcs show the signal while recovery is joined to a network,
   a dim fan while Wi-Fi is on without one, nothing while it is off. */
static int wifi_status(App*a,int right){
    if(!a->wifi_on&&!a->wifi_connected)return right;
    unsigned int bars=0;for(unsigned int n=0;n<a->wifi_count;n++)if(a->wifi_nets[n].connected)bars=a->wifi_nets[n].bars;
    if(a->wifi_connected&&!bars)bars=3;
    int x=right-48,y=22,size=38;uint32_t c=a->wifi_connected?TEXT:MUTED;
    ICON_PATH(24,40,24,40);
    for(int k=0;k<3;k++)icon_arc(a,x,y,size,!a->wifi_connected?MUTED:((unsigned int)k+2<=bars?TEXT:PANEL2),24,40,8+9*k,225,315);
    return x-4;
}
static void heading(App*a,const char*title,const char*subtitle,bool back){
    icon(a,A_OS,30,28,30,ORANGE);text(a,70,32,0,MUTED,"Mainline Recovery");
    char now[16],date[96];clock_text(a,now,sizeof(now),date,sizeof(date));int clock_w=text_width(now,1);text(a,CW-32-clock_w,27,1,TEXT,now);
    int left=wifi_status(a,battery_status(a,CW-32-clock_w-20));
    if(!a->device||a->preview){const char*mode=a->preview?"PREVIEW":"FILE TEST";int mw=text_width(mode,0)+32,x=left-16-mw;rounded(a,(Rect){x,26,mw,38},19,PANEL2);centered(a,(Rect){x,26,mw,38},0,ORANGE,mode);}
    if(back){
        bool enabled=(ui_free(a)||lock_prompting(a)||(terminal_screen(a)&&terminal_background_job(a->job.kind)))&&(!terminal_screen(a)||a->terminal_expected!=2);
        vector_circle(a,54,126,32,32,pressed(a,A_BACK)?LINE:PANEL2);icon(a,ICON_BACK,36,108,36,TEXT);register_button(a,(Rect){16,84,76,84},A_BACK,enabled,0);
    }
    int title_x=back?102:32;text(a,title_x,text_width(title,3)<=CW-title_x-28?91:100,text_width(title,3)<=CW-title_x-28?3:2,TEXT,title);
    if(subtitle)wrapped(a,back?104:34,164,CW-(back?134:66),0,MUTED,subtitle,2);
}
static void footer(App*a){
    if(terminal_screen(a)&&terminal_session_open(a))text(a,32,1492,0,MUTED,a->terminal_busy?"Command running / it continues after you exit":"Local terminal / storage lease held");
}
static void destination(App*a,int y){
    rounded(a,(Rect){28,y,664,154},24,PANEL);
    text(a,52,y+20,0,ORANGE,"BACKUP DESTINATION");
    const char*p=a->repositories[a->store];ellipsis(a,52,y+55,616,1,TEXT,p[0]?p:"Select an external backup location");
    struct statvfs v;char freebuf[64],msg[128];
    if(a->preview)copy(msg,sizeof(msg),"ext4 microSD - illustrative preview");
    else if(p[0]&&statvfs(p,&v)==0){human_bytes((uint64_t)v.f_bavail*v.f_frsize,freebuf,sizeof(freebuf));snprintf(msg,sizeof(msg),T("%s available"),freebuf);}
    else copy(msg,sizeof(msg),"Not available. Tap to choose storage");
    text(a,52,y+104,0,MUTED,msg);register_button(a,(Rect){28,y,664,154},A_STORAGE,ui_free(a),0);
}
static const char*profile_title(const char*p){return !strcmp(p,"android")?"Android archive":(!strcmp(p,"recovery")?"Recovery image":"Linux boot + rootfs");}

static void row_card(App*a,int y,int h,int glyph,const char*title,const char*subtitle,int action,uint32_t background,bool enabled){
    Rect r={28,y,664,h};bool hero=background==ORANGE_DARK;surface(a,r,26,background,action);
    int box=h>=120?84:64,tx=r.x+20+box+22;icon_tile(a,glyph,r.x+20,y+(h-box)/2,box,hero?ORANGE_EDGE:ICON_FILL);
    int title_font=text_width(title,2)<=r.x+r.w-58-tx?2:1;text(a,tx,y+h/2-46+(title_font==1?6:0),title_font,enabled?TEXT:MUTED,title);ellipsis(a,tx,y+h/2+6,r.x+r.w-58-tx,0,hero?ui_theme->hero_sub:MUTED,subtitle);
    icon(a,ICON_CHEVRON,r.x+r.w-50,y+h/2-16,32,hero?ORANGE:MUTED);register_button(a,r,action,enabled,0);button_glyph(a,action,glyph);
}
static void os_card(App*a,int y,int h,int glyph,const char*title,const char*subtitle,int action,uint32_t background){row_card(a,y,h,glyph,title,subtitle,action,background,ui_free(a));}
/* Bento: a tile's size says how much it matters. Install leads, then what is used most. */
static void bento_tile(App*a,Rect r,unsigned int tone,int id,const char*title,const char*subtitle,bool tonal){
    uint32_t fill=ui_theme->tile_fill[tone],ink=ui_theme->tile_ink[tone];
    if(tonal){canvas_shadow(a->pixels,canvas_bounds(a),nx(r.x),nx(r.y),nx(r.x+r.w),nx(r.y+r.h),54.f,ui_theme->shadow,ui_theme->shadow_strength);gradient(a,r,36,fill,fill);if(pressed(a,id))veil(a,r,36,0,18);}
    else surface(a,r,36,PANEL,id);
    int box=r.h>=200?84:(r.h>=170?72:60),size=box*5/8;
    if(tonal)veil(a,(Rect){r.x+20,r.y+20,box,box},box/2,PANEL,ui_theme->surface_alpha);else gradient(a,(Rect){r.x+20,r.y+20,box,box},box/2,fill,fill);
    icon(a,id,r.x+20+(box-size)/2,r.y+20+(box-size)/2,size,ink);
    int font=subtitle?2:1,top=r.y+r.h-(subtitle?96:58);if(text_width(title,font)>r.w-40)font--;
    text(a,r.x+22,top,font,TEXT,title);if(subtitle)ellipsis(a,r.x+22,top+50,r.w-40,0,tonal?TEXT:MUTED,subtitle);
    register_button(a,r,id,ui_free(a),0);button_glyph(a,id,id);
}
/* The flashlight (2026-11-11, the owner's request; TWRP and OrangeFox have one): the camera's LED in
   torch mode, through the kernel's LED class. A22X_RECOVERY_TORCH names the LED's folder for tests. */
static void toast(App*a,const char*message);
static bool torch_value(const char*folder,const char*name,unsigned int*out){
    char path[PATH_LEN+32],value[24]={0};snprintf(path,sizeof(path),"%s/%s",folder,name);
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return false;
    ssize_t n=read(fd,value,sizeof(value)-1);close(fd);if(n<=0)return false;
    *out=(unsigned int)strtoul(value,NULL,10);return true;
}
static bool torch_ready(App*a){
    if(a->torch_checked)return a->torch_available;
    a->torch_checked=true;if(a->preview)return a->torch_available;
    char found[PATH_LEN]={0};const char*named=getenv("A22X_RECOVERY_TORCH");
    if(named&&*named)copy(found,sizeof(found),named);
    else if(a->device){
        DIR*d=opendir("/sys/class/leds");struct dirent*e;
        while(d&&(e=readdir(d)))if(strstr(e->d_name,"flash")||strstr(e->d_name,"torch")){snprintf(found,sizeof(found),"/sys/class/leds/%.200s",e->d_name);break;}
        if(d)closedir(d);
    }
    unsigned int most=0,now=0;
    if(!found[0]||!torch_value(found,"max_brightness",&most)||!most||!torch_value(found,"brightness",&now))return false;
    /* A light that is on already (the interface was started again) is shown as on. */
    /* The level chosen before is kept in the settings; the first time it is the middle one. */
    copy(a->torch_path,sizeof(a->torch_path),found);a->torch_max=most;a->torch_on=now>0;
    a->torch_level=now>0?now:(a->torch_saved>=1&&a->torch_saved<=most?a->torch_saved:(most+1)/2);
    return a->torch_available=true;
}
/* The level is written and read back: the page shows what the LED took, not what was asked. */
static void torch_set(App*a,bool on,unsigned int level){
    if(!torch_ready(a))return;
    if(level<1)level=1;
    if(level>a->torch_max)level=a->torch_max;
    unsigned int want=on?level:0,back=~0u;bool written=a->preview;
    if(!a->preview){
        char path[PATH_LEN+32],value[16];snprintf(path,sizeof(path),"%s/brightness",a->torch_path);int n=snprintf(value,sizeof(value),"%u",want);
        int fd=open(path,O_WRONLY|O_CLOEXEC);if(fd>=0){written=write(fd,value,(size_t)n)==n;close(fd);}
        written=written&&torch_value(a->torch_path,"brightness",&back)&&back==want;
    }
    fprintf(stderr,"[recovery-ui:torch] want=%u ok=%d\n",want,written);
    if(!written){if(back!=~0u)a->torch_on=back>0;toast(a,"The flashlight did not answer");a->toast_icon=ICON_TORCH;a->dirty=true;return;}
    a->torch_on=on;if(on)a->torch_level=level;a->dirty=true;
}
/* The brightness (2026-11-11): a level is taken at once when the light is on, else it waits for the
   next time. It is kept in the settings. */
static void torch_choose(App*a,unsigned int level){
    if(!torch_ready(a))return;
    if(level<1)level=1;
    if(level>a->torch_max)level=a->torch_max;
    if(level==a->torch_level)return;
    if(a->torch_on)torch_set(a,true,level);else{a->torch_level=level;a->dirty=true;}
    if(a->torch_level==level&&a->torch_saved!=level){a->torch_saved=level;settings_changed(a);}
}
static void torch_step(App*a,int by){if(torch_ready(a)&&(by>0||a->torch_level>1))torch_choose(a,by>0?a->torch_level+1:a->torch_level-1);}
/* The settings may be read after the first frame has asked for the LED: the level kept in them is
   taken then, unless the light is on and shows another. */
static void torch_remembered(App*a){
    if(a->torch_checked&&a->torch_available&&!a->torch_on&&a->torch_saved>=1&&a->torch_saved<=a->torch_max&&a->torch_level!=a->torch_saved){a->torch_level=a->torch_saved;a->dirty=true;}
}
/* A power action takes the light with it, whoever lit it: nothing else is sure to switch the LED off
   before the phone resets or goes dark. */
static void torch_leave(App*a){
    unsigned int now=0;
    if(a->preview||!torch_ready(a)||!torch_value(a->torch_path,"brightness",&now))return;
    if(now||a->torch_on)torch_set(a,false,a->torch_level);
}
/* The level under a finger on the bar: the level whose end of the fill reaches it. */
static unsigned int torch_bar_level(const App*a,Rect r,int x){
    int span=r.w-8,at=x-(r.x+4);
    if(span<1||!a->torch_max)return a->torch_level;
    if(at<=0)return 1;
    if(at>=span)return a->torch_max;
    unsigned int level=(unsigned int)(((int64_t)at*a->torch_max+span-1)/span);
    return level<1?1:(level>a->torch_max?a->torch_max:level);
}
/* The level as a bar with its number. A touch on it takes the level under the finger. */
static void torch_bar(App*a,Rect r){
    char value[24];snprintf(value,sizeof(value),"%u / %u",a->torch_level,a->torch_max);
    rounded(a,r,r.h/2,PANEL2);
    int fill=a->torch_max?(int)((unsigned int)(r.w-8)*a->torch_level/a->torch_max):0;if(fill<r.h-8)fill=r.h-8;
    rounded(a,(Rect){r.x+4,r.y+4,fill,r.h-8},(r.h-8)/2,a->torch_on?ORANGE:ORANGE_DARK);
    if(pressed(a,A_TORCH_BAR))veil(a,r,r.h/2,0xffffffu,16);
    raw_text(true);centered(a,r,1,TEXT,value);raw_text(false);
    register_button(a,r,A_TORCH_BAR,true,0);
}
/* The way to the phone's details sits in the date line, so it takes no room of its own; the
   flashlight, when the phone has one this kernel drives, sits beside it. */
static void home_device(App*a){
    const char*name=a->sys_model[0]?a->sys_model:"About phone";int width=text_width(name,0)+88,x=692-width,y=152;Rect r={x,y,width,48};
    surface(a,r,24,PANEL,A_SYSTEM);icon(a,ICON_PHONE,x+14,y+10,28,ORANGE);text(a,x+50,y+10,0,TEXT,name);icon(a,ICON_CHEVRON,x+width-32,y+13,22,MUTED);
    register_button(a,(Rect){x,y-12,width,72},A_SYSTEM,ui_free(a),0);
    if(torch_ready(a)){
        Rect t={x-64,y,52,48};surface(a,t,24,a->torch_on?ORANGE:PANEL,A_TORCH);icon(a,ICON_TORCH,t.x+11,t.y+9,30,a->torch_on?ui_theme->on_accent:ORANGE);
        register_button(a,(Rect){t.x-8,y-12,t.w+12,72},A_TORCH,true,0);button_glyph(a,A_TORCH,ICON_TORCH);
    }
}
/* The nine small tiles at the bottom of the home page share one size and layout: the icon,
   the title at the same height in all of them, and a short note under it (2026-11-08). */
static void home_cell(App*a,Rect r,int tone,int action,int glyph,const char*title,const char*note,uint32_t note_ink,bool raw_note,bool enabled){
    uint32_t fill=tone<0?ICON_FILL:ui_theme->tile_fill[tone],ink=tone<0?ORANGE:ui_theme->tile_ink[tone];
    surface(a,r,ui_theme->bento?36:26,PANEL,action);
    gradient(a,(Rect){r.x+18,r.y+16,60,60},30,fill,fill);icon(a,glyph,r.x+30,r.y+28,36,enabled?ink:MUTED);
    uint32_t text_ink=enabled?TEXT:MUTED;
    if(text_width(title,1)<=r.w-36)text(a,r.x+20,r.y+90,1,text_ink,title);else wrapped(a,r.x+20,r.y+86,r.w-34,0,text_ink,title,2);
    if(note&&note[0]){if(raw_note)raw_text(true);ellipsis(a,r.x+20,r.y+130,r.w-34,0,note_ink,note);if(raw_note)raw_text(false);}
    register_button(a,r,action,enabled,0);button_glyph(a,action,glyph);
}
static void home_grid(App*a,int y,int h,int gap){
    bool free_ui=ui_free(a),joined=a->wifi_connected&&a->wifi_ssid[0];
    const char*terminal=a->terminal_left_busy&&terminal_session_open(a)?"Running":(a->terminal_resume?"Finished":NULL);
    int row2=y+h+gap,row3=row2+h+gap;
    home_cell(a,(Rect){28,y,212,h},4,A_STORAGE,A_STORAGE,"Storage",NULL,MUTED,false,free_ui);
    home_cell(a,(Rect){254,y,212,h},5,A_POWER,A_POWER,"Power",NULL,MUTED,false,free_ui);
    home_cell(a,(Rect){480,y,212,h},-1,A_TOOLS,ICON_TUNE,"Settings",NULL,MUTED,false,free_ui);
    home_cell(a,(Rect){28,row2,212,h},3,A_TERMINAL,ICON_LINUX,"Terminal",terminal,ORANGE,false,free_ui);
    home_cell(a,(Rect){254,row2,212,h},0,A_USB_FILES,ICON_USB,"USB files",NULL,MUTED,false,free_ui);
    home_cell(a,(Rect){480,row2,212,h},4,A_TOOLS_EXPORT,A_BACKUP,"Export diagnostics",NULL,MUTED,false,free_ui);
    home_cell(a,(Rect){28,row3,212,h},2,A_WIFI,ICON_WIFI,"Wi-Fi",joined?a->wifi_ssid:(a->wifi_known&&!a->wifi_available?"Unavailable":"Not connected"),joined?GREEN:MUTED,joined,free_ui);
    home_cell(a,(Rect){254,row3,212,h},1,A_HOME_THEME,ICON_THEME,"Theme",ui_theme->name,MUTED,false,free_ui);
    home_cell(a,(Rect){480,row3,212,h},0,A_HOME_CATALOG,ICON_BROWSER,"Catalog","Systems from the internet",MUTED,false,free_ui);
}
static void render_home_bento(App*a){
    char now[16],date[96];clock_text(a,now,sizeof(now),date,sizeof(date));heading(a,"A22x Recovery",date,false);
    home_device(a);
    Rect hero={28,212,664,244};canvas_shadow(a->pixels,canvas_bounds(a),nx(hero.x),nx(hero.y),nx(hero.x+hero.w),nx(hero.y+hero.h),66.f,ui_theme->hero_bottom,110);gradient(a,hero,44,ui_theme->hero_top,ui_theme->hero_bottom);
    /* Two soft discs give the hero depth. They stay clear of its rounded corners. */
    a->clip_active=true;a->clip=hero;veil(a,(Rect){506,226,200,200},100,0xffffffu,24);veil(a,(Rect){470,342,140,140},70,0xffffffu,20);a->clip_active=false;
    if(pressed(a,A_OS))veil(a,hero,44,0,30);
    veil(a,(Rect){52,236,84,84},42,0xffffffu,46);icon(a,A_OS,68,252,52,ui_theme->hero_text);
    {int font=text_width("Install",3)<=560?3:2;text(a,52,font==3?336:346,font,ui_theme->hero_text,"Install");}
    if(a->aplan_valid){char ready[160];snprintf(ready,sizeof(ready),T("Ready from the Android app: %s"),a->aplan_name);raw_text(true);ellipsis(a,54,404,560,0,ui_theme->hero_text,ready);raw_text(false);}
    else ellipsis(a,54,404,560,0,ui_theme->hero_sub,"Operating system, Android or a partition");
    icon(a,ICON_CHEVRON,634,316,40,ui_theme->hero_text);register_button(a,hero,A_OS,ui_free(a),0);button_glyph(a,A_OS,A_OS);
    bento_tile(a,(Rect){28,472,324,248},0,A_BACKUP,"Backup","Save a system snapshot",true);bento_tile(a,(Rect){368,472,324,248},1,A_FILES,"Files","Browse and edit files",true);
    bento_tile(a,(Rect){28,736,324,214},2,A_RESTORE,"Restore","Return to a saved state",false);bento_tile(a,(Rect){368,736,324,214},3,A_VERIFY,"Verify","Check backup integrity",false);
    home_grid(a,966,170,16);
}
static void render_home(App*a){
    if(ui_theme->bento){render_home_bento(a);return;}
    char now[16],date[96];clock_text(a,now,sizeof(now),date,sizeof(date));heading(a,"A22x Recovery",date,false);
    home_device(a);
    if(a->aplan_valid){char ready[160];snprintf(ready,sizeof(ready),T("Ready from the Android app: %s"),a->aplan_name);raw_text(true);os_card(a,214,150,A_OS,tr("Install"),ready,A_OS,ORANGE_DARK);raw_text(false);}
    else os_card(a,214,150,A_OS,"Install","Operating system, Android or a partition",A_OS,ORANGE_DARK);
    const char*titles[]={"Backup","Files","Restore","Verify"};
    const char*subs[]={"Save a system snapshot","Browse and edit files","Return to a saved state","Check backup integrity"};
    int ids[]={A_BACKUP,A_FILES,A_RESTORE,A_VERIFY};
    for(int i=0;i<4;i++){
        Rect r={28+(i%2)*344,380+(i/2)*226,320,212};surface(a,r,26,PANEL,ids[i]);icon_tile(a,ids[i],r.x+20,r.y+18,68,ICON_FILL);
        int title_font=text_width(titles[i],2)<=r.w-40?2:1;text(a,r.x+22,r.y+(title_font==2?108:112),title_font,TEXT,titles[i]);ellipsis(a,r.x+22,r.y+160,r.w-40,0,MUTED,subs[i]);register_button(a,r,ids[i],ui_free(a),0);button_glyph(a,ids[i],ids[i]);
    }
    home_grid(a,834,170,16);
    if(a->device&&!a->linux_restore_available)text(a,40,1394,0,ORANGE,"Linux restore awaits qualification");
}
/* Every icon white on black in a grid, for the Android app (tools/export_icons.py, 2026-11-10):
   the app shows the very same icons, tinted like here. Only ever a preview. */
static const struct{int id;const char*name;}sheet_icons[]={
    {A_OS,"install"},{A_BACKUP,"backup"},{A_FILES,"files"},{A_RESTORE,"restore"},{A_VERIFY,"verify"},{A_STORAGE,"storage"},
    {A_POWER,"power"},{A_SYSTEM,"phone_info"},{ICON_LINUX,"terminal"},{ICON_PHONE,"phone"},{ICON_FLASH,"flash"},{ICON_CHEVRON,"chevron"},
    {ICON_BACK,"back"},{ICON_TUNE,"tune"},{ICON_LANGUAGE,"language"},{ICON_VIBRATE,"vibrate"},{ICON_DISPLAY,"display"},{ICON_CLOCK,"clock"},
    {ICON_LOCK,"lock"},{ICON_THEME,"theme"},{ICON_USB,"usb"},{ICON_KEYBOARD,"keyboard"},{ICON_ERASE,"erase"},{ICON_TRASH,"trash"},
    {ICON_NAV,"navigation"},{ICON_WIFI,"wifi"},{ICON_BROWSER,"browser"},{ICON_TORCH,"torch"}};
static void render_icon_sheet(App*a){
    rounded(a,(Rect){0,0,720,1600},0,0x000000u);
    for(unsigned int n=0;n<sizeof(sheet_icons)/sizeof(sheet_icons[0]);n++)icon(a,sheet_icons[n].id,8+(int)(n%6)*118,8+(int)(n/6)*118,96,0xffffffu);
}
static void render_os(App*a){
    heading(a,"Install","Choose what to install on this phone.",true);
    int y=render_aplan_card(a,242);  /* A plan from the Android app comes first (2026-11-10). */
    os_card(a,y,150,ICON_LINUX,"Operating system","Linux from SD or USB stick, switch systems",A_SYSTEMS,PANEL);
    os_card(a,y+166,150,ICON_PHONE,"Android","Samsung firmware, or Android images from the internet",A_ANDROID,PANEL);
    os_card(a,y+332,150,ICON_FLASH,"Flash a partition","BOOT, DTBO, vbmeta and other images",A_FLASH,PANEL);
    /* TWRP's Wipe beside its Install (2026-11-10). */
    os_card(a,y+498,150,ICON_TRASH,"Wipe","Cache, or a whole volume before an installation",A_WIPE,PANEL);
}
static void render_backup(App*a){
    heading(a,"Backup","Choose the system state to save.",true);
    const char*profiles[]={"linux","recovery"};
    const char*descriptions[]={"Boot + used ext4 blocks\nSave the Linux kernel and root filesystem offline.","Recovery partition only\nKeep a copy of this independent recovery."};
    /* Android holds userdata: there is no Linux to save, and the copy of Android comes first. */
    if(a->systems_foreign&&!strcmp(a->profile,"linux"))copy(a->profile,sizeof(a->profile),"recovery");
    for(int i=0;i<2;i++){
        Rect r={28,242+i*204,664,180};
        if(i==0&&a->systems_foreign){
            rounded(a,r,24,PANEL);icon_tile(a,ICON_PHONE,48,r.y+26,64,ICON_FILL);text(a,134,r.y+22,2,TEXT,"Android");
            wrapped(a,134,r.y+82,480,0,MUTED,"BOOT, super and the others, and Android's data as it lies there",3);icon(a,ICON_CHEVRON,640,r.y+74,32,MUTED);
            register_button(a,r,A_BACKUP_ANDROID,ui_free(a),0);continue;
        }
        bool selected=!strcmp(a->profile,profiles[i]);rounded(a,r,24,selected?ORANGE_DARK:PANEL);
        if(selected)gradient(a,(Rect){r.x+8,r.y+25,5,r.h-50},3,ORANGE,ORANGE);
        text(a,54,r.y+22,2,TEXT,profile_title(profiles[i]));wrapped(a,54,r.y+82,568,0,MUTED,descriptions[i],3);
        vector_circle(a,646,r.y+43,13,2,selected?ORANGE:MUTED);if(selected)vector_circle(a,646,r.y+43,7,7,ORANGE);
        register_button(a,r,A_PROFILE+i,ui_free(a),0);
    }
    {Rect r={28,650,664,120};rounded(a,r,24,PANEL);icon_tile(a,ICON_PHONE,46,r.y+20,56,ICON_FILL);text(a,120,r.y+18,1,TEXT,"Phone partitions");
     ellipsis(a,120,r.y+64,500,0,MUTED,"EFS and IMEI, or BOOT, super and others");icon(a,ICON_CHEVRON,640,r.y+44,32,MUTED);register_button(a,r,A_IDENTITY,ui_free(a),0);}
    text_tile(a,800,0,MUTED,a->systems_foreign?"Android holds userdata: its copy is one archive on a card. A Linux backup needs a Linux system on this phone.":
              "Linux backup saves allocated ext4 blocks with compression. The next screen reviews userdata before normal unmount.",3);
    destination(a,930);
    text_tile(a,1106,0,MUTED,!strcmp(a->profile,"linux")&&!a->linux_backup_available&&a->inventory_ok?"Compact Linux backup tools are unavailable in this recovery. Refresh system status to check availability.":"Planning measures the compressed size and checks free space on the destination. Userdata stays offline afterward.",5);
    button(a,(Rect){28,1322,664,94},"Review backup",A_REVIEW_BACKUP,ui_free(a)&&a->repository_count>0&&(strcmp(a->profile,"linux")||a->linux_backup_available||a->preview),true,0);
}
static bool backup_is_linux_v2(const BackupEntry*e){return !strcmp(e->schema,"a22x.backup.v2")&&!strcmp(e->workflow,"linux-partclone-v2")&&!strcmp(e->profile,"linux");}
static void render_prepare(App*a){
    const char*operation=a->prepare_job==J_WIPE_PLAN?"wipe":(a->prepare_job==J_RESTORE_PLAN?"restore":"backup");char subtitle[160];snprintf(subtitle,sizeof(subtitle),T("Prepare userdata for Linux %s."),tr(operation));
    heading(a,"Offline filesystem",subtitle,true);rounded(a,(Rect){28,242,664,316},26,PANEL);text(a,52,266,2,TEXT,"userdata / Linux rootfs");
    text(a,52,328,1,!a->prepare_known?MUTED:(a->prepare_mounted?ORANGE:GREEN),!a->prepare_known?"Reading current mount state...":(a->prepare_mounted?"Currently mounted":"Already unmounted"));
    wrapped(a,52,386,602,0,MUTED,a->prepare_mountpoint[0]?a->prepare_mountpoint:"The offline plan rechecks partition identity and state.",4);
    wrapped(a,40,624,640,1,TEXT,a->prepare_mounted?"Unmount userdata, then review a fresh plan?":"Review a fresh plan with userdata offline?",4);
    text_tile(a,802,0,MUTED,"This uses a normal unmount. Busy filesystems are refused. The backup destination stays mounted and userdata remains offline until you choose to mount it again in Files.",6);
    if(a->prepare_reason[0])text_tile(a,1034,0,ORANGE,a->prepare_reason,5);
    button(a,(Rect){28,1204,664,78},"Refresh mount state",A_PREPARE_REFRESH,ui_free(a),false,0);
    bool admitted=a->prepare_known&&(!a->prepare_mounted||a->prepare_owned)&&a->prepare_token[0];
    button(a,(Rect){28,1320,664,104},a->prepare_mounted?"Hold to unmount and review":"Review offline plan",A_PREPARE_CONTINUE,admitted&&ui_free(a),true,a->prepare_mounted?1000:0);
}
static void list_categories(App*a,int y);static bool list_android(const App*a);
/* Rows of Linux snapshots on a page: five below the two categories, six where a backup is chosen for the old installer. */
static int list_rows(const App*a){return a->install.pick_backup?6:5;}
static void render_list(App*a){
    bool android=list_android(a);int rows=list_rows(a),top=a->install.pick_backup?238:316;
    heading(a,a->install.pick_backup?"Linux backup":(a->list_for_verify?"Verify backup":"Restore"),a->install.pick_backup?"Explicitly select a Linux v2 snapshot for this installation.":(android?"Copies of Android on the SD card and USB drives.":"Select a completed snapshot."),true);
    if(!a->install.pick_backup)list_categories(a,232);
    if(android){render_copies(a,top);return;}
    if(!a->backup_count){rounded(a,(Rect){28,top+8,664,360},26,PANEL);icon(a,A_STORAGE,315,top+37,90,ORANGE);centered(a,(Rect){48,top+150,624,60},2,TEXT,"No completed backups");wrapped(a,62,top+240,596,1,MUTED,a->message[0]?a->message:"Choose an SD card or USB stick.",4);}
    for(int n=0;n<rows;n++){
        int i=a->list_page*rows+n;if(i>=a->backup_count)break;BackupEntry*e=&a->backups[i];Rect r={28,top+n*164,664,146};rounded(a,r,16,PANEL);
        raw_text(true);ellipsis(a,52,r.y+18,598,1,TEXT,e->name);raw_text(false);char bytes[64],meta[256];human_bytes(e->bytes,bytes,sizeof(bytes));snprintf(meta,sizeof(meta),"%s  /  %s",tr(profile_title(e->profile)),e->bytes?bytes:tr("size in manifest"));text(a,52,r.y+64,0,MUTED,meta);ellipsis(a,52,r.y+100,598,0,MUTED,e->created);
        register_button(a,r,A_BACKUP_ITEM+i,ui_free(a)&&(!a->install.pick_backup||(backup_is_linux_v2(e)&&e->path[0]=='/')),0);
    }
    /* Restore also takes a partition image: a dd copy of userdata, a boot image, plain or compressed. */
    bool images=!a->install.pick_backup&&!a->list_for_verify;int pager=images?1212:1260;
    if(a->backup_count>rows){button(a,(Rect){28,pager,320,images?60:72},"Previous",A_LIST_PREV,a->list_page>0,false,0);button(a,(Rect){372,pager,320,images?60:72},"Next",A_LIST_NEXT,(a->list_page+1)*rows<a->backup_count,false,0);}
    if(images)button(a,(Rect){28,1282,664,76},"Restore a partition image",A_RESTORE_IMAGE,ui_free(a),true,0);
    button(a,(Rect){28,1370,664,72},a->install.pick_backup?"Cancel backup selection":"Choose storage",a->install.pick_backup?A_INSTALL_CANCEL_PICK:A_STORAGE,ui_free(a),false,0);
}
/* The rule of the backend's validate_name, and a name that differs from the one it has. */
static bool backup_name_ok(const App*a,const char*s){
    size_t n=strlen(s);if(!n||n>64||strstr(s,".partial-"))return false;
    for(size_t i=0;i<n;i++){unsigned char c=(unsigned char)s[i];bool plain=(c>='0'&&c<='9')||(c>='A'&&c<='Z')||(c>='a'&&c<='z');if(!plain&&!(i&&(c=='.'||c=='_'||c=='-')))return false;}
    return a->selected_backup>=0&&a->selected_backup<a->backup_count&&strcmp(s,a->backups[a->selected_backup].name)!=0;
}
static void render_detail(App*a){
    BackupEntry*e=&a->backups[a->selected_backup];heading(a,"Saved snapshot","Inspect before restoring.",true);
    rounded(a,(Rect){28,242,664,410},26,PANEL);icon(a,A_BACKUP,54,265,72,ORANGE);text(a,54,370,2,TEXT,profile_title(e->profile));
    raw_text(true);wrapped(a,54,432,600,1,TEXT,e->name,3);raw_text(false);wrapped(a,54,536,600,0,MUTED,e->created,2);
    char bytes[64];human_bytes(e->bytes,bytes,sizeof(bytes));text(a,54,596,0,MUTED,e->bytes?bytes:"Size available during verification");
    raw_text(true);wrapped(a,40,676,640,0,MUTED,e->path,3);raw_text(false);
    if(backup_is_linux_v2(e)){
        text_tile(a,786,0,MUTED,"Linux used-block snapshot. Wipe empties userdata only and requires this backup to match its current contents. Boot and recovery are preserved.",5);
        button(a,(Rect){28,984,664,80},"Review userdata wipe",A_REVIEW_WIPE,ui_free(a),false,0);
    }else if(!strcmp(e->profile,"android"))text_tile(a,820,1,ORANGE,"Android state requires its matching retained system/vendor firmware.",4);
    /* 2026-11-11, as TWRP's Restore page: a finished backup is renamed or deleted here. */
    button(a,(Rect){28,1088,252,80},"Rename",A_BACKUP_RENAME,ui_free(a),false,0);
    button(a,(Rect){292,1088,400,80},"Hold 3 s: delete",A_BACKUP_DELETE,ui_free(a),false,3000);
    button(a,(Rect){28,1192,664,92},"Verify this backup",A_VERIFY_ONE,ui_free(a),false,0);
    button(a,(Rect){28,1318,664,96},"Review restore",A_REVIEW_RESTORE,ui_free(a),true,0);
}
static void render_plan(App*a){
    bool destructive=a->plan_restore||a->plan_wipe;
    heading(a,a->plan_wipe?"Review wipe":(a->plan_restore?"Review restore":"Review backup"),destructive?"This action replaces the listed targets.":"Only this reviewed plan can be started.",true);
    if(a->plan_linux_v2){
        rounded(a,(Rect){28,242,664,248},26,PANEL);text(a,52,260,2,TEXT,"Linux / boot + rootfs");
        char used[64],archive[64],available[64],reserve[64],line[192];human_bytes(a->plan_used_bytes,used,sizeof(used));human_bytes(a->plan_archive_bytes,archive,sizeof(archive));human_bytes(a->plan_free_bytes,available,sizeof(available));human_bytes(a->plan_reserve_bytes,reserve,sizeof(reserve));
        snprintf(line,sizeof(line),T("Used ext4 blocks: %s"),a->plan_space_known?used:tr("not reported"));text(a,52,322,0,TEXT,line);
        snprintf(line,sizeof(line),"%s: %s",tr(destructive?"Saved archive":"Measured archive"),a->plan_estimate_known?archive:tr("not reported"));text(a,52,364,0,ORANGE,line);
        snprintf(line,sizeof(line),T("SD free: %s   /   reserve: %s"),a->plan_space_known?available:tr("unknown"),a->plan_space_known?reserve:tr("unknown"));ellipsis(a,52,406,600,0,MUTED,line);
        text(a,40,526,0,ORANGE,destructive?"VERIFIED BACKUP SOURCE":"BACKUP DESTINATION");wrapped(a,40,566,640,0,TEXT,a->plan_backup,4);
        rounded(a,(Rect){28,744,664,252},18,destructive?ui_theme->danger:PANEL);text(a,52,762,1,destructive?RED:TEXT,destructive?"Exact write targets":"Offline used-block snapshot");
        wrapped(a,52,808,604,0,TEXT,destructive?a->plan_targets:"BOOT and allocated ext4 blocks are read and compressed. Existing files on the destination are retained.",5);
        const char*boot=a->plan_wipe?"BOOT preserved / userdata becomes empty ext4":(!strcmp(a->plan_boot_action,"restore-last")?"BOOT restored last, after userdata verification":(a->plan_restore?"BOOT identical / no BOOT write":"Userdata stays unmounted after backup"));ellipsis(a,52,948,604,0,MUTED,boot);
        if(!a->plan_can_execute){text(a,40,1030,0,ORANGE,"PLAN IS BLOCKED");wrapped(a,40,1070,640,0,MUTED,a->blockers[0]?a->blockers:"The backend did not admit this operation.",7);}
        else text_tile(a,1044,0,MUTED,a->plan_wipe?"This creates an empty filesystem, not secure erasure. The saved snapshot remains on SD. Recovery does not reboot automatically.":(a->plan_restore?"Userdata is restored and checked first. Keep power and SD connected. Recovery does not reboot automatically.":"The archive is checked before the snapshot is marked complete. Moving outside the button cancels the hold."),7);
        button(a,(Rect){28,1320,664,104},a->plan_can_execute?(a->plan_wipe?"Hold 3 seconds to wipe":(a->plan_restore?"Hold 3 seconds to restore":"Hold 2 seconds to back up")):"Operation not available",A_CONFIRM,a->plan_can_execute&&ui_free(a),true,destructive?3000:2000);return;
    }
    rounded(a,(Rect){28,242,664,228},26,PANEL);text(a,52,263,2,TEXT,profile_title(a->profile));char bytes[64];human_bytes(a->plan_bytes,bytes,sizeof(bytes));text(a,52,320,1,ORANGE,bytes);wrapped(a,52,375,600,0,MUTED,a->plan_parts,3);
    text(a,40,514,0,ORANGE,a->plan_restore?"BACKUP SOURCE":"BACKUP DESTINATION");wrapped(a,40,554,640,1,TEXT,a->plan_backup,5);
    if(a->plan_restore){rounded(a,(Rect){28,800,664,180},18,ui_theme->danger);text(a,52,816,1,RED,"Targets to replace");wrapped(a,52,860,604,0,MUTED,a->plan_targets[0]?a->plan_targets:"Target paths were not provided by the backend.",4);}
    else{rounded(a,(Rect){28,800,664,180},24,PANEL);text(a,52,822,1,TEXT,"Offline snapshot");wrapped(a,52,872,604,0,MUTED,"A completed manifest is published only after the archive and its decoded hash have been verified.",3);}
    if(!a->plan_can_execute){text(a,40,1030,0,ORANGE,"PLAN IS BLOCKED");wrapped(a,40,1070,640,0,MUTED,a->blockers[0]?a->blockers:"Backend did not admit this operation.",7);}
    else{char id[180];snprintf(id,sizeof(id),T("Plan %.20s"),a->plan_id);text(a,40,1040,0,MUTED,id);text_tile(a,1092,0,MUTED,a->plan_restore?"Release after the hold bar is complete. Moving outside the button cancels confirmation.":"Hold the button to save this exact snapshot. Moving outside cancels confirmation.",4);}
    button(a,(Rect){28,1320,664,104},a->plan_can_execute?(a->plan_restore?"Hold 3 seconds to restore":"Hold 2 seconds to back up"):"Operation not available",A_CONFIRM,a->plan_can_execute&&ui_free(a),true,a->plan_restore?3000:2000);
}
static const char*phase_label(const App*a,const char*phase){
    static const char*const names[][2]={{"verify-source","Checking the source"},{"verify-target","Checking the partition"},{"write","Writing"},{"readback","Reading the partition back"},{"reset-userdata","Formatting userdata"},{"hash-retained-firmware","Checking the firmware"},
        {"check-ext4","Checking the filesystem"},{"compress-boot","Saving BOOT"},{"verify-before-write","Checking the backup"},{"write-used-blocks","Restoring userdata"},{"write-boot","Restoring BOOT"},{"readback-used-blocks","Checking restored data"},{"readback-boot","Checking BOOT"}};
    if(!strcmp(phase,"compact-compress"))return a->job.kind==J_BACKUP?"Compressing userdata":"Measuring the backup size";
    static const char*const imports[][2]={{"image-download","Downloading the system image"},{"image-erase","Erasing userdata"},{"image-unpack","Unpacking the system image"},{"image-copy","Copying the system files"},
        {"image-adapt","Adding phone support"},{"image-provision","Setting up the new system"},{"image-check","Checking the new system"},{"image-finish","Finishing the installation"},{"image-boot-download","Downloading the Linux BOOT"},{"image-boot","Writing the Linux BOOT"}};
    for(unsigned int n=0;n<sizeof(imports)/sizeof(imports[0]);n++)if(!strcmp(phase,imports[n][0]))return imports[n][1];
    /* Samsung's firmware and the Android pages (2026-11-10): samsung_firmware.py and android_gsi.py. */
    static const char*const android[][2]={{"Unpacking","Unpacking the firmware"},{"Verifying","Checking the firmware's MD5"},
        {"Wiping","Wiping cache"},{"Formatting","Formatting userdata for Android"}};
    for(unsigned int n=0;n<sizeof(android)/sizeof(android[0]);n++)if(!strcmp(phase,android[n][0]))return android[n][1];
    /* Partition a card (2026-11-11): sdcard.py. */
    static const char*const card[][2]={{"card-table","Writing the partition table"},{"card-files","Making the partition for files"},
        {"card-linux","Making the partition for Linux"},{"card-swap","Making the swap partition"},{"card-check","Checking the card"}};
    for(unsigned int n=0;n<sizeof(card)/sizeof(card[0]);n++)if(!strcmp(phase,card[n][0]))return card[n][1];
    for(unsigned int n=0;n<sizeof(names)/sizeof(names[0]);n++)if(!strcmp(phase,names[n][0]))return names[n][1];
    return phase;
}
/* The work page in tiles (2026-11-11, the owner's request with a photo of it): a ring with the
   share done, then what it is, how much, how fast and how long, each in a tile of its own. The
   banner at the bottom keeps only what can be done about the work. */
static void stat_tile(App*a,Rect r,const char*label,const char*value,uint32_t color){
    rounded(a,r,22,PANEL);text(a,r.x+20,r.y+16,0,MUTED,label);ellipsis(a,r.x+20,r.y+54,r.w-40,1,color,value);
}
static void note_card(App*a,int y,int glyph,uint32_t ink,const char*message){
    int lines=wrapped_height(a,520,0,message,4),h=lines+48<92?92:lines+48;
    rounded(a,(Rect){28,y,664,h},24,PANEL);gradient(a,(Rect){46,y+(h-56)/2,56,56},17,ICON_FILL,ICON_FILL);icon(a,glyph,57,y+(h-56)/2+11,34,ink);
    wrapped(a,124,y+(h-lines)/2,544,0,TEXT,message,4);
}
static void progress_elapsed(const App*a,char*out,size_t size){
    uint64_t s=a->job_started&&a->now>a->job_started?(a->now-a->job_started)/1000u:0;
    if(s>=3600)snprintf(out,size,"%u:%02u:%02u",(unsigned int)(s/3600),(unsigned int)(s/60%60),(unsigned int)(s%60));
    else snprintf(out,size,"%u:%02u",(unsigned int)(s/60),(unsigned int)(s%60));
}
/* Speed and time left are measured from the start of the current phase, as progress_eta. */
static bool progress_speed(const App*a,char*out,size_t size){
    uint64_t elapsed=a->now>a->progress_mark_at?a->now-a->progress_mark_at:0,done=a->progress_done>a->progress_mark_done?a->progress_done-a->progress_mark_done:0;
    if(elapsed<3000||!done)return false;
    char bytes[48];human_bytes(done*1000u/elapsed,bytes,sizeof(bytes));snprintf(out,size,"%s/s",bytes);return true;
}
static bool progress_left(const App*a,char*out,size_t size){
    uint64_t elapsed=a->now>a->progress_mark_at?a->now-a->progress_mark_at:0,done=a->progress_done>a->progress_mark_done?a->progress_done-a->progress_mark_done:0;
    if(!a->progress_total||elapsed<5000||!done||a->progress_done>=a->progress_total)return false;
    uint64_t seconds=(a->progress_total-a->progress_done)*elapsed/done/1000u;
    if(seconds<60)copy(out,size,tr("Under a minute"));else snprintf(out,size,T("About %u min"),(unsigned int)((seconds+59)/60));
    return true;
}
static void render_progress(App*a){
    heading(a,"Working",NULL,false);
    char elapsed[32];progress_elapsed(a,elapsed,sizeof(elapsed));
    const float top=-1.5707963f,turn=6.2831853f;
    /* Packages installed in the new root: steps of the first-start script, not bytes (2026-11-08). */
    if(!strcmp(a->progress_phase,"image-provision")){
        static const char*const steps[]={"Preparing the new system","Installing the desktop","Installing apps","Building for this phone","Running the setup script","Done"};
        unsigned int step=a->progress_done<5?(unsigned int)a->progress_done:5u;char line[96];
        Rect hero={28,232,664,220};rounded(a,hero,28,PANEL);
        int cx=hero.x+112,cy=hero.y+110;vector_circle(a,cx,cy,78,12,LINE);vector_arc(a,cx,cy,78,12,top,turn*(float)step/5.f,ORANGE);icon(a,A_OS,cx-30,cy-30,60,ORANGE);
        int label=wrapped_height(a,428,1,phase_label(a,a->progress_phase),2);
        wrapped(a,hero.x+220,hero.y+36,428,1,TEXT,phase_label(a,a->progress_phase),2);
        snprintf(line,sizeof(line),T("Step %u of %u"),step+1u,6u);text(a,hero.x+220,hero.y+48+label,1,ORANGE,line);
        if(a->job.pid){char shown[96];snprintf(shown,sizeof(shown),T("Elapsed %s"),elapsed);text(a,hero.x+220,hero.y+100+label,0,MUTED,shown);}
        Rect list={28,hero.y+hero.h+16,664,6*62+28};rounded(a,list,28,PANEL);
        for(unsigned int n=0;n<6;n++){
            int y=list.y+14+(int)n*62,mx=70,my=y+31;bool done=n<step,current=n==step;
            if(done){vector_circle(a,mx,my,15,15,ORANGE);vector_line(a,mx-7,my,mx-2,my+6,3,ON_ORANGE);vector_line(a,mx-2,my+6,mx+8,my-6,3,ON_ORANGE);}
            else if(current){vector_circle(a,mx,my,15,4,ORANGE);vector_circle(a,mx,my,6,6,ORANGE);}
            else vector_circle(a,mx,my,15,3,LINE);
            if(n<5)vector_line(a,mx,my+19,mx,my+43,2,done?ORANGE:LINE);
            ellipsis(a,108,y+14,560,1,current?TEXT:(done?TEXT:MUTED),steps[n]);
        }
        note_card(a,list.y+list.h+16,ICON_WIFI,ORANGE,step==3?"What this system lacks for the phone is built on it (Arch: Phosh's compositor fix and Phrog), up to 45 minutes. Keep the phone powered.":
                  "Packages come over the recovery's Wi-Fi: this takes 10-30 minutes. Keep the phone powered.");
        return;
    }
    Rect hero={28,232,664,476};rounded(a,hero,28,PANEL);
    int cx=CW/2,cy=hero.y+186,radius=138,width=16;
    vector_circle(a,cx,cy,radius,width,LINE);
    if(a->progress_total){
        uint64_t done=a->progress_done>a->progress_total?a->progress_total:a->progress_done;char percent[16];
        snprintf(percent,sizeof(percent),"%u%%",(unsigned int)(done*100/a->progress_total));
        vector_arc(a,cx,cy,radius,width,top,turn*(float)((double)done/(double)a->progress_total),ORANGE);
        centered(a,(Rect){cx-radius,cy-44,radius*2,88},3,TEXT,percent);
    }else{
        /* Nothing to count yet: a quarter of the ring turns. */
        vector_arc(a,cx,cy,radius,width,top+turn*(float)(a->now%1600u)/1600.f,1.6f,ORANGE);icon(a,A_OS,cx-36,cy-36,72,ORANGE);
    }
    centered(a,(Rect){48,hero.y+346,624,56},2,TEXT,a->progress_phase[0]?phase_label(a,a->progress_phase):"Waiting for backend");
    if(a->progress_part[0]){
        int w=text_width(a->progress_part,0)+56;if(w>600)w=600;Rect chip={cx-w/2,hero.y+410,w,44};rounded(a,chip,22,PANEL2);
        raw_text(true);if(text_width(a->progress_part,0)<=w-24)centered(a,chip,0,MUTED,a->progress_part);else ellipsis(a,chip.x+16,chip.y+8,chip.w-32,0,MUTED,a->progress_part);raw_text(false);
    }
    int y=hero.y+hero.h+16;
    if(a->progress_total){
        char done[48],total[48],bytes[112],speed[48],left[64];human_bytes(a->progress_done>a->progress_total?a->progress_total:a->progress_done,done,sizeof(done));
        human_bytes(a->progress_total,total,sizeof(total));snprintf(bytes,sizeof(bytes),"%s / %s",done,total);
        bool fast=progress_speed(a,speed,sizeof(speed)),known=progress_left(a,left,sizeof(left));
        stat_tile(a,(Rect){28,y,324,112},"DONE",bytes,TEXT);stat_tile(a,(Rect){368,y,324,112},"SPEED",fast?speed:"—",fast?TEXT:MUTED);y+=128;
        stat_tile(a,(Rect){28,y,324,112},"ELAPSED",elapsed,TEXT);stat_tile(a,(Rect){368,y,324,112},"LEFT",known?left:"—",known?ORANGE:MUTED);y+=128;
    }else{stat_tile(a,(Rect){28,y,664,112},"ELAPSED",elapsed,TEXT);y+=128;}
    note_card(a,y,ICON_PHONE,ORANGE,"Keep the phone powered and storage connected.");
}
static void render_result(App*a){
    /* In tiles (2026-11-11): what happened beside its sign, then the message, its first paragraph
       apart from the details that follow it. The text is translated whole before it is split. */
    heading(a,a->successful?"Completed":"Not completed",NULL,false);
    bool diagnostic=a->result_return==TOOLS||a->result_return==TOOLS_EXPORT,second=!a->successful&&a->systems_remove_offer;
    second=second||(a->successful&&a->result_return==FLASH&&!a->flash_restore&&a->allow_power&&(!strcmp(flash_target_label(a),"recovery")||!strcmp(flash_target_label(a),"boot")));
    second=second||(a->successful&&a->result_return==ANDROID_MENU&&((a->android_installed&&a->allow_power)||a->stock_boot_path[0]));
    second=second||(a->successful&&a->zip_ran&&a->allow_power);
    int limit=second?1190:1304;
    Rect hero={28,232,664,150};rounded(a,hero,28,PANEL);gradient(a,(Rect){46,hero.y+19,112,112},34,ICON_FILL,ICON_FILL);
    icon(a,a->successful?A_VERIFY:A_SYSTEM,67,hero.y+40,70,a->successful?GREEN:RED);
    const char*status=tr(a->status[0]?a->status:(a->successful?"Completed":"Not completed"));
    {raw_text(true);int h=wrapped_height(a,486,1,status,3);wrapped(a,182,hero.y+(hero.h-h)/2,486,1,a->successful?TEXT:RED,status,3);raw_text(false);}
    char full[sizeof(a->message)];copy(full,sizeof(full),tr(a->message[0]?a->message:(a->successful?"The backend reported success.":"The backend did not report a successful result.")));
    char*details=strstr(full,"\n\n");if(details){*details=0;details+=2;while(*details=='\n')details++;if(!*details)details=NULL;}
    int y=hero.y+hero.h+16,font=diagnostic?0:1,line=diagnostic?28:36;
    raw_text(true);
    {int room=(limit-y-(details?140:16)-48)/line;if(room<2)room=2;int h=wrapped_height(a,616,font,full,room);
     rounded(a,(Rect){28,y,664,h+48},24,PANEL);wrapped(a,52,y+24,616,font,TEXT,full,room);y+=h+48+16;}
    if(details&&limit-y>=100){int room=(limit-y-48)/28,h=wrapped_height(a,616,0,details,room);
        rounded(a,(Rect){28,y,664,h+48},24,PANEL);wrapped(a,52,y+24,616,0,MUTED,details,room);y+=h+48+16;}
    raw_text(false);
    if(!a->successful&&limit-y>=110)note_card(a,y,A_SYSTEM,ORANGE,"No retry is started automatically. Inspect the backend result before another operation.");
    /* "Back to files" only for the file pages themselves (2026-11-10): the same pages choosing an
       image, and the pages listed after them (Wi-Fi, systems, backups of identity...), say "Back". */
    bool files=a->result_return>=FILE_VOLUMES&&a->result_return<=FILE_DISCARD,picking=a->flash_pick||a->install.pick_source||a->theme_pick||a->systems_pick_manifest;
    /* "Back to recovery" only where it goes home: after a flash it goes back to Flash a partition. */
    /* A system the switch refuses can still be removed (2026-11-10: old slots made for another
       platform profile could not be removed at all, the Remove button sat on the switch page). */
    if(!a->successful&&a->systems_remove_offer)button(a,(Rect){28,1206,664,96},"Remove this system",A_SYS_REMOVE,ui_free(a)&&systems_ready(a),false,0);
    /* After a flash of the recovery or BOOT the next step is a restart, as TWRP offers it
       (2026-11-10): the same confirmation with a hold as on the Power page. */
    if(a->successful&&a->result_return==FLASH&&!a->flash_restore&&a->allow_power){
        const char*label=flash_target_label(a);bool recovery=!strcmp(label,"recovery");
        if(recovery||!strcmp(label,"boot"))button(a,(Rect){28,1206,664,96},recovery?"Reboot recovery":"Reboot system",recovery?A_REBOOT_RECOVERY:A_REBOOT,
                                                  ui_free(a)&&(recovery||(!a->systems_reboot_blocked&&!a->install.reboot_blocked)),false,0);
    }
    /* After stock Android the next step is its first start (2026-11-10). */
    /* After installers: the restart that was asked for counts down and can be stopped; otherwise it is offered, as TWRP offers it. */
    if(a->successful&&a->zip_ran&&a->allow_power&&a->zip_restart_at){
        char label[96];unsigned int left=a->zip_restart_at>a->now?(unsigned int)((a->zip_restart_at-a->now+999)/1000):0;
        snprintf(label,sizeof(label),T("Stay in recovery (the system restarts in %u s)"),left);raw_text(true);button(a,(Rect){28,1206,664,96},label,A_ZIP_STAY,true,true,0);raw_text(false);
    }else if(a->successful&&a->zip_ran&&a->allow_power)button(a,(Rect){28,1206,664,96},"Reboot system",A_REBOOT,ui_free(a)&&!a->systems_reboot_blocked&&!a->install.reboot_blocked,false,0);
    else if(a->successful&&a->result_return==ANDROID_MENU&&a->android_installed&&a->allow_power)button(a,(Rect){28,1206,664,96},"Restart into Android",A_REBOOT,ui_free(a),false,0);
    else if(a->successful&&a->result_return==ANDROID_MENU&&a->stock_boot_path[0])button(a,(Rect){28,1206,664,96},"Flash it to BOOT",A_STOCK_BOOT_FLASH,ui_free(a),false,0);
    button(a,(Rect){28,1320,664,102},files&&!picking?"Back to files":(a->result_return==USB_FILES?"Back to USB files":(a->result_return==PREPARE_FILESYSTEM?"Back to preparation":(a->result_return==DISPLAY_SETTINGS?"Back to display":(a->result_return==TOOLS||a->result_return==TOOLS_EXPORT?"Back to tools":(a->result_return==HOME?"Back to recovery":"Back"))))),A_DONE,true,true,0);
}
static bool repository_needs_setup(const App*a,int index){return a->repository_volume[index][0]&&(a->repository_read_only[index]||access(a->repositories[index],F_OK));}
static void render_storage(App*a){
    heading(a,"Storage","SD cards and USB sticks. Choose where backups go.",true);
    int y=236;bool stick=false;
    for(int i=0;i<a->repository_count;i++){
        bool usb=a->repository_volume[i][0]!=0,selected=i==a->store;const char*root=usb?a->repository_root[i]:a->repositories[i];stick=stick||usb;
        Rect r={28,y,664,206};surface(a,r,26,selected?ORANGE_DARK:PANEL,A_STORE+i);icon_tile(a,usb?ICON_USB:A_STORAGE,r.x+20,y+18,64,selected?ORANGE_EDGE:ICON_FILL);
        text(a,r.x+100,y+14,1,TEXT,usb?"USB stick":"microSD");
        struct statvfs v;char b[64],msg[160];bool ready=!a->preview&&statvfs(root,&v)==0;
        if(ready){human_bytes((uint64_t)v.f_bavail*v.f_frsize,b,sizeof(b));snprintf(msg,sizeof(msg),T("%s available"),b);if(a->repository_fs[i][0]){append(msg,sizeof(msg),"  /  ");append(msg,sizeof(msg),a->repository_fs[i]);}}else copy(msg,sizeof(msg),a->preview?"Example external repository":"Not mounted or not available");
        ellipsis(a,r.x+100,y+58,480,0,MUTED,msg);
        if(usb&&repository_needs_setup(a,i))ellipsis(a,r.x+100,y+90,480,0,ORANGE,"Tap to use for backups");else{raw_text(true);ellipsis(a,r.x+100,y+90,480,0,MUTED,a->repositories[i]);raw_text(false);}
        vector_circle(a,646,y+50,17,4,selected?ORANGE:MUTED);if(selected)vector_circle(a,646,y+50,8,8,ORANGE);
        register_button(a,(Rect){r.x,y,r.w,126},A_STORE+i,ui_free(a),0);
        if(usb)button(a,(Rect){r.x+20,y+128,300,62},"Remove safely",A_USB_EJECT,ui_free(a),false,0);
        else if(a->storage_helper[0])button(a,(Rect){r.x+20,y+128,300,62},a->storage_mounted?"Unmount":"Mount",a->storage_mounted?A_UMOUNT:A_MOUNT,ui_free(a),false,0);
        y+=222;
    }
    if(!stick)wrapped(a,40,y+6,640,0,MUTED,"Insert a USB stick and it appears here by itself.",2);
    text_tile(a,1390,0,MUTED,"The destination must be external to the partitions being saved or restored. Format volumes in Files.",3);
}
/* One line of the details: what it is on the left, the value on the right. */
static void system_row(App*a,int y,const SysRow*row){
    char line[96];snprintf(line,sizeof(line),row->unit[0]?"%s %s":"%s",row->value,tr(row->unit));
    int label=text_width(row->label,0),left=52+label+24,room=668-left,width=text_width(line,0);
    text(a,52,y+18,0,MUTED,row->label);ellipsis(a,width<=room?668-width:left,y+18,room,0,TEXT,line);
}
static void render_system(App*a){
    heading(a,"About phone","Read from this phone each time.",true);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=242-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    Rect card={28,y,664,164};gradient(a,card,40,ui_theme->hero_top,ui_theme->hero_bottom);veil(a,(Rect){52,y+34,96,96},48,0xffffffu,46);icon(a,ICON_PHONE,72,y+54,56,ui_theme->hero_text);
    ellipsis(a,172,y+36,500,2,ui_theme->hero_text,a->sys_name[0]?a->sys_name:"This phone");
    {char line[64];snprintf(line,sizeof(line),a->sys_codename[0]?"%s  /  %s":"%s",a->sys_model,a->sys_codename);ellipsis(a,174,y+94,500,0,ui_theme->hero_sub,line);}
    y+=180;
    if(a->sys_summary_count){
        int gap=12,width=(664-gap*(int)(a->sys_summary_count-1))/(int)a->sys_summary_count;
        for(unsigned int n=0;n<a->sys_summary_count;n++){
            const SysRow*row=&a->sys_summary[n];Rect r={28+(int)n*(width+gap),y,width,118};char value[96];snprintf(value,sizeof(value),row->unit[0]?"%s %s":"%s",row->value,tr(row->unit));
            rounded(a,r,28,PANEL);centered(a,(Rect){r.x+4,y+16,r.w-8,50},text_width(value,1)<=r.w-16?1:0,TEXT,value);centered(a,(Rect){r.x+4,y+68,r.w-8,36},0,MUTED,row->label);
        }
        y+=134;
    }
    if(!a->sys_section_count){wrapped(a,40,y+10,640,0,MUTED,a->job.pid&&a->job.kind==J_SYSINFO?"Reading the phone...":"The phone details are not available yet.",3);y+=90;}
    for(unsigned int section=0;section<a->sys_section_count;section++){
        unsigned int count=0;for(unsigned int n=0;n<a->sys_row_count;n++)if(a->sys_rows[n].section==section)count++;
        if(!count)continue;
        text(a,40,y+8,0,ORANGE,a->sys_titles[section]);y+=46;rounded(a,(Rect){28,y,664,(int)count*60+12},26,PANEL);y+=6;
        unsigned int shown=0;
        for(unsigned int n=0;n<a->sys_row_count;n++){
            if(a->sys_rows[n].section!=section)continue;
            if(shown++)line(a,52,y,668,y,1,LINE);
            system_row(a,y,&a->sys_rows[n]);y+=60;
        }
        y+=26;
    }
    text(a,40,y+8,0,ORANGE,"Partitions");y+=46;
    for(int i=0;i<a->partition_count;i++){
        PartitionEntry*p=&a->partitions[i];char bytes[64],state[160];human_bytes(p->bytes,bytes,sizeof(bytes));
        rounded(a,(Rect){28,y,664,92},20,PANEL);text(a,48,y+10,1,TEXT,p->label);snprintf(state,sizeof(state),"%s / %s",p->available?bytes:tr("unavailable"),tr(p->mounted?"mounted":"offline"));text(a,664-text_width(state,0),y+14,0,p->mounted?ORANGE:MUTED,state);ellipsis(a,48,y+55,612,0,MUTED,p->path);y+=106;
    }
    if(!a->partition_count){rounded(a,(Rect){28,y,664,84},26,PANEL);ellipsis(a,52,y+26,616,0,MUTED,a->message[0]?a->message:"No partition inventory received.");y+=102;}
    /* On a card, because a theme's picture may be behind this text. */
    rounded(a,(Rect){28,y,664,206},26,PANEL);wrapped(a,52,y+18,616,0,MUTED,"Recovery runs from RAM. Mounted Linux volumes can be maintained independently of the installed system. Encrypted volumes require their own unlock support.",4);
    ellipsis(a,52,y+160,616,0,a->restore_qualified?GREEN:ORANGE,a->restore_qualified?"Restore admitted for regular-file fixtures":"Device restore is not qualified in this build");y+=224;
    button(a,(Rect){28,y,664,84},"Refresh inventory",A_REFRESH,ui_free(a),false,0);y+=108;
    a->page_content=y+a->page_scroll-230;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
}
/* Holding Power: what a phone offers, over a blurred copy of the page. */
static void render_power_menu(App*a){
    static const char*const labels[4]={"Power off","Reboot system","Reboot recovery","Samsung Download"};
    static const int actions[4]={A_SHUTDOWN,A_REBOOT,A_REBOOT_RECOVERY,A_REBOOT_DOWNLOAD},glyphs[4]={A_POWER,ICON_LINUX,A_OS,A_RESTORE};static const unsigned int tones[4]={5,0,3,4};
    for(int n=0;n<4;n++){
        Rect r={70,440+n*170,580,150};bool enabled=a->allow_power&&!a->job.pid&&(actions[n]!=A_REBOOT||(!a->systems_reboot_blocked&&!a->install.reboot_blocked));
        uint32_t fill=ui_theme->tile_fill[tones[n]],ink=ui_theme->tile_ink[tones[n]];surface(a,r,44,PANEL,actions[n]);
        gradient(a,(Rect){r.x+26,r.y+31,88,88},44,fill,fill);icon(a,glyphs[n],r.x+46,r.y+51,48,ink);
        ellipsis(a,r.x+140,r.y+52,420,text_width(labels[n],2)<=420?2:1,enabled?TEXT:MUTED,labels[n]);register_button(a,r,actions[n],enabled,0);
    }
    if(torch_ready(a)){
        /* The flashlight, above the four ways to leave: a tap switches it and the menu stays. */
        Rect r={70,290,580,120};surface(a,r,44,a->torch_on?ORANGE_DARK:PANEL,A_TORCH);
        gradient(a,(Rect){r.x+26,r.y+22,76,76},38,a->torch_on?ORANGE:ui_theme->tile_fill[1],a->torch_on?ORANGE:ui_theme->tile_fill[1]);icon(a,ICON_TORCH,r.x+44,r.y+40,40,a->torch_on?ui_theme->on_accent:ui_theme->tile_ink[1]);
        char state[48];snprintf(state,sizeof(state),"%s, %u/%u",tr(a->torch_on?"On":"Off"),a->torch_level,a->torch_max);
        text(a,r.x+128,r.y+22,1,TEXT,"Flashlight");raw_text(true);text(a,r.x+128,r.y+66,0,a->torch_on?GREEN:MUTED,state);raw_text(false);
        /* Dimmer and brighter first: they lie on the pill, and the pill itself switches. */
        button(a,(Rect){r.x+r.w-196,r.y+24,84,72},"−",A_TORCH_DOWN,a->torch_level>1,false,0);
        button(a,(Rect){r.x+r.w-100,r.y+24,84,72},"+",A_TORCH_UP,a->torch_level<a->torch_max,false,0);
        register_button(a,r,A_TORCH,true,0);
    }
    centered(a,(Rect){40,1140,640,44},0,TEXT,a->job.pid?"Operation in progress":(terminal_session_open(a)&&a->terminal_busy?"The command running in the terminal will be stopped.":"Tap outside to close"));
    register_button(a,(Rect){0,0,CW,CH},A_MENU_CLOSE,true,0);
}
static void render_power(App*a){
    heading(a,"Power","Choose the next boot mode.",true);
    const char*labels[]={"Reboot system","Reboot recovery","Samsung Download","Power off"};
    const char*notes[]={"Start the installed system: Linux or Android","Start this recovery again","Mode for flashing from a computer","Turn the phone off"};
    const int actions[]={A_REBOOT,A_REBOOT_RECOVERY,A_REBOOT_DOWNLOAD,A_SHUTDOWN},glyphs[]={ICON_LINUX,A_OS,A_RESTORE,A_POWER};
    for(int n=0;n<4;n++)row_card(a,250+n*166,150,glyphs[n],labels[n],notes[n],actions[n],PANEL,a->allow_power&&ui_free(a)&&(n!=0||(!a->systems_reboot_blocked&&!a->install.reboot_blocked)));
    if(a->systems_reboot_blocked||a->install.reboot_blocked)text_tile(a,930,0,RED,a->install.reboot_blocked?"Inspect the pending image installation in Settings before booting.":"Complete or roll back the pending system switch before booting Linux.",3);
    text_tile(a,1020,0,MUTED,a->allow_power?"Choose the next boot mode, then hold to confirm. Completed writes are synchronized and recovery mounts are unmounted by the supervisor.":"Power actions are disabled in this build configuration.",4);
}
static const char*power_confirmation(int intent){
    switch(intent){case 200:return "Restart into the installed system?";case 201:return "Power off the phone?";
    case 202:return "Reboot to recovery?";case 203:return "Restart into Samsung Download mode?";default:return "No power action selected.";}
}
static void settings_row(App*a,int y,int glyph,const char*title,const char*value,int action,bool enabled){
    Rect r={28,y,664,104};surface(a,r,24,PANEL,action);icon_tile(a,glyph,r.x+18,y+20,64,ICON_FILL);
    text(a,r.x+100,y+14,1,enabled||!a->tools_known?TEXT:MUTED,title);ellipsis(a,r.x+100,y+60,500,0,MUTED,value);
    icon(a,ICON_CHEVRON,r.x+r.w-48,y+36,32,MUTED);register_button(a,r,action,enabled,0);button_glyph(a,action,glyph);
}
static void utc_label(int minutes,char*out,size_t size){
    int value=minutes<0?-minutes:minutes;if(value%60)snprintf(out,size,"UTC%c%d:%02d",minutes<0?'-':'+',value/60,value%60);else snprintf(out,size,"UTC%c%d",minutes<0?'-':'+',value/60);
}
static void render_tools(App*a){
    heading(a,"Settings","Language, vibration, time and tools.",true);
    a->tools_scroll=(int64_t)scroll_clamp((double)a->tools_scroll,(double)tools_max_scroll(a));int o=(int)a->tools_scroll,y=242-o;bool free_ui=ui_free(a);
    a->clip_active=true;a->clip=tools_viewport();
    settings_row(a,y,ICON_LANGUAGE,"Language",language_names[ui_language],A_LANGUAGE,free_ui);y+=116;
    settings_row(a,y,ICON_THEME,"Theme",ui_theme->name,A_THEME,free_ui);y+=116;
    settings_row(a,y,ICON_LINUX,"Terminal colors",ui_terminal->name,A_TERMINAL_THEME,free_ui);y+=116;
    {static const char*const styles[ANIMATION_COUNT]={"Zoom","Zoom with blur","Slide","Fade","Blur dissolve","Off"};settings_row(a,y,ICON_DISPLAY,"Animations",styles[a->animation],A_ANIMATIONS,free_ui);y+=116;}
    rounded(a,(Rect){28,y,664,180},24,PANEL);icon_tile(a,ICON_NAV,46,y+16,56,ICON_FILL);text(a,120,y+26,1,TEXT,"Navigation");
    {const char*ways[]={"Gestures","Buttons","Off"};for(unsigned int n=0;n<3;n++)button(a,(Rect){46+(int)n*212,y+88,204,72},ways[n],A_NAV_MODE+(int)n,free_ui,a->nav_mode==n,0);}
    y+=192;
    rounded(a,(Rect){28,y,664,180},24,PANEL);icon_tile(a,ICON_VIBRATE,46,y+16,56,ICON_FILL);text(a,120,y+26,1,TEXT,"Vibration");
    const char*levels[]={"Off","Light","Medium","Strong"};
    for(unsigned int n=0;n<4;n++)button(a,(Rect){46+(int)n*158,y+88,150,72},levels[n],A_VIBRATION+(int)n,free_ui,a->vibration==n,0);
    y+=192;settings_row(a,y,ICON_DISPLAY,"Display","Refresh rate, timeout and brightness",A_DISPLAY,free_ui);y+=116;
    if(torch_ready(a)){
        /* The switch, and under it the brightness in every level the LED has. */
        rounded(a,(Rect){28,y,664,268},24,PANEL);icon_tile(a,ICON_TORCH,46,y+16,56,ICON_FILL);text(a,120,y+26,1,TEXT,"Flashlight");
        button(a,(Rect){46,y+88,306,72},"On",A_TORCH_ON,true,a->torch_on,0);
        button(a,(Rect){368,y+88,306,72},"Off",A_TORCH_OFF,true,!a->torch_on,0);
        button(a,(Rect){46,y+176,96,72},"−",A_TORCH_DOWN,a->torch_level>1,false,0);
        torch_bar(a,(Rect){154,y+176,412,72});
        button(a,(Rect){578,y+176,96,72},"+",A_TORCH_UP,a->torch_level<a->torch_max,false,0);
        y+=280;
    }
    y+=render_charger_card(a,y,free_ui);
    wifi_settings_row(a,y,free_ui);y+=116;
    char now[16],date[96],zone[48],line[144];clock_text(a,now,sizeof(now),date,sizeof(date));
    if(recovery_zone(a))copy(zone,sizeof(zone),tz_zones[a->recovery_tz].city);else utc_label(a->utc_offset,zone,sizeof(zone));snprintf(line,sizeof(line),"%s  %s",now,date);
    rounded(a,(Rect){28,y,664,180},24,PANEL);icon_tile(a,ICON_CLOCK,46,y+16,56,ICON_FILL);text(a,120,y+14,1,TEXT,"Date and time");ellipsis(a,120,y+52,540,0,MUTED,line);
    button(a,(Rect){46,y+88,150,72},"−",A_TZ_DOWN,free_ui&&a->utc_offset>-720,false,0);raw_text(recovery_zone(a));button(a,(Rect){204,y+88,312,72},zone,A_TZ_PICK,free_ui,false,0);raw_text(false);button(a,(Rect){524,y+88,150,72},"+",A_TZ_UP,free_ui&&a->utc_offset<840,false,0);
    y+=192;{
        static const char*const modes[3]={"Never","At start","Always"};static const char*const notes[3]={"Set, but the screen is not locked","Asked when recovery starts","Asked at start and after the screen turns off"};
        int height=a->lock_enabled?368:180;rounded(a,(Rect){28,y,664,height},24,PANEL);icon_tile(a,ICON_LOCK,46,y+16,56,ICON_FILL);text(a,120,y+14,1,TEXT,"Recovery password");ellipsis(a,120,y+52,540,0,MUTED,a->lock_enabled?notes[a->lock_mode]:"Off");
        button(a,(Rect){46,y+88,306,72},a->lock_enabled?"Change":"Set password",A_LOCK_SET,free_ui,false,0);button(a,(Rect){368,y+88,306,72},"Remove",A_LOCK_CLEAR,free_ui&&a->lock_enabled,false,0);
        if(a->lock_enabled){
            text(a,46,y+172,0,MUTED,"Lock screen");for(int n=0;n<3;n++)button(a,(Rect){46+n*212,y+204,204,68},modes[n],A_LOCK_MODE+n,free_ui,a->lock_mode==(unsigned int)n,0);
            button(a,(Rect){46,y+284,628,68},a->access_mode?"Computer access: ask on the phone":"Computer access: open",A_ACCESS_TOGGLE,free_ui,false,0);
        }
        y+=height-180;
    }
    y+=192;button(a,(Rect){28,y,664,84},"Refresh status",A_TOOLS_REFRESH,free_ui,false,0);y+=108;
    rounded(a,(Rect){28,y,664,330},24,PANEL);text(a,52,y+22,0,ORANGE,"USB SSH");
    text(a,52,y+62,2,a->tools_known&&a->ssh_running?GREEN:MUTED,!a->tools_known?(a->job.pid?"Checking status...":"Status unavailable"):(a->ssh_running?"SSH is running":(a->ssh_available?"SSH is stopped":"SSH unavailable")));
    char endpoint[192];if(a->tools_known&&a->ssh_address[0])snprintf(endpoint,sizeof(endpoint),"%s:%u",a->ssh_address,a->ssh_port);else copy(endpoint,sizeof(endpoint),"USB address unavailable");
    ellipsis(a,52,y+118,606,1,TEXT,endpoint);
    char keys[128];if(a->tools_known)snprintf(keys,sizeof(keys),T("Authorized keys: %u  /  key-only login"),a->ssh_keys);else copy(keys,sizeof(keys),"Authorization status unavailable");text(a,52,y+166,0,a->tools_known&&a->ssh_keys?GREEN:ORANGE,keys);
    wrapped(a,52,y+212,606,0,MUTED,a->ssh_reason[0]?a->ssh_reason:(a->ssh_running?"Connect from the computer attached by USB.":"Refresh to read the current service status."),3);y+=346;
    rounded(a,(Rect){28,y,664,264},24,PANEL);text(a,52,y+24,0,ORANGE,"SERVER HOST KEY FINGERPRINT");
    wrapped(a,52,y+72,606,0,TEXT,a->ssh_fingerprint[0]?a->ssh_fingerprint:"No host key reported.",3);
    wrapped(a,52,y+168,606,0,MUTED,a->ssh_ephemeral?"This key is temporary and may change on the next recovery boot.":"Compare this fingerprint before accepting the SSH host key.",3);
    /* 2026-11-11, as TWRP's Restore defaults. */
    y+=284;
    /* 2026-11-11: the screenshot keys, said where settings are looked for. */
    y+=12+text_tile(a,y+12,0,MUTED,"Screenshot: press Volume down and Power together. It is saved to A22xScreenshots on an SD card or a USB drive, else on cache.",4);
    wrapped(a,40,y,640,0,MUTED,"A reset restores the default theme, terminal colours, animation, navigation, vibration, screen timeout, brightness and refresh rate. The language, time zone, password and lock, Wi-Fi networks and keys stay.",5);
    y+=160;button(a,(Rect){28,y,664,84},"Hold 3 seconds: reset the settings",A_SETTINGS_RESET,free_ui,false,3000);
    {int64_t content=y+84+28+o-230;if(content!=a->tools_content){a->tools_content=content;if(a->tools_scroll>tools_max_scroll(a)){a->tools_scroll=tools_max_scroll(a);a->dirty=true;}}}
    a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->tools_scroll,(int64_t)tools_max_scroll(a)+1254,ORANGE);
}
static void theme_backdrop(App*a){
    const Theme*t=ui_theme;if(!t->backdrop)return;
    if(!a->backdrop)a->backdrop=malloc((size_t)NW*NH*sizeof(uint32_t));
    if(a->backdrop)canvas_backdrop(a->backdrop,t->backdrop,t->backdrop_w?(int)t->backdrop_w:BACKDROP_W,t->backdrop_h?(int)t->backdrop_h:BACKDROP_H);
}
static void theme_apply(App*a,unsigned int index){
    if(index>=THEME_COUNT)return;
    ui_theme=&themes[index];theme_backdrop(a);a->dirty=true;
}
static void theme_apply_file(App*a,ThemeFile*f){
    uint8_t*pixels=theme_file_image(f);f->theme.backdrop=pixels;f->theme.backdrop_w=f->header.image_w;f->theme.backdrop_h=f->header.image_h;
    ui_theme=&f->theme;theme_backdrop(a);free(a->theme_image);a->theme_image=pixels;a->dirty=true;
}
static bool theme_select(App*a,const char*code){
    for(unsigned int n=0;n<THEME_COUNT;n++)if(!strcmp(code,themes[n].code)){theme_apply(a,n);return true;}
    for(unsigned int n=0;n<theme_file_count;n++)if(theme_file_ui(&theme_files[n])&&!strcmp(code,theme_files[n].header.code)){theme_apply_file(a,&theme_files[n]);return true;}
    return false;
}
static bool terminal_select(const char*code){
    for(unsigned int n=0;n<TERMINAL_SCHEME_COUNT;n++)if(!strcmp(code,terminal_schemes[n].code)){ui_terminal=&terminal_schemes[n];return true;}
    for(unsigned int n=0;n<theme_file_count;n++)if(theme_file_terminal(&theme_files[n])&&!strcmp(code,theme_files[n].header.code)){ui_terminal=&theme_files[n].scheme;return true;}
    return false;
}
/* The list is rebuilt, so what is in use is found again by name. */
static void theme_reload(App*a){
    char theme[24],scheme[32];copy(theme,sizeof(theme),ui_theme->code);copy(scheme,sizeof(scheme),ui_terminal->code);
    ui_theme=&themes[0];ui_terminal=&terminal_schemes[0];theme_files_scan();
    if(!theme_select(a,theme))theme_apply(a,0);
    (void)terminal_select(scheme);
}
/* The first tap asks, the second removes. A theme is not lost to a brush of the thumb. */
static void theme_trash(App*a,int cx,int cy,int action,uint32_t ink){
    bool armed=a->theme_armed==action&&a->now<a->theme_armed_until;Rect r={cx-34,cy-34,68,68};
    if(armed)gradient(a,r,34,RED,RED);else if(pressed(a,action))veil(a,r,34,ink,40);
    icon(a,ICON_TRASH,cx-19,cy-19,38,armed?ui_theme->bg:ink);register_button(a,r,action,ui_free(a),0);
}
/* A small phone in the theme's own colors, or the theme's own picture when it has one. */
static void theme_card(App*a,const Theme*t,const ThemeFile*f,const char*note,int y,int action){
    Rect r={28,y,664,210};int x=r.x+20,top=r.y+18;bool selected=t==ui_theme;surface(a,r,30,selected?ORANGE_DARK:PANEL,action);
    if(f)theme_trash(a,570,r.y+105,A_THEME_REMOVE+(int)(f-theme_files),MUTED);
    if(f&&f->thumb)canvas_picture(a->pixels,canvas_bounds(a),nx(x+11),nx(top),f->thumb,(int)f->header.thumb_w,(int)f->header.thumb_h,24.f);
    else{
        uint32_t high=t->bg,low=t->bg;
        if(f){high=f->header.preview_top;low=f->header.preview_bottom;}
        else if(t->backdrop){const uint8_t*p=t->backdrop+(10*BACKDROP_W+100)*3,*q=t->backdrop+(250*BACKDROP_W+40)*3;high=(uint32_t)p[0]<<16|(uint32_t)p[1]<<8|p[2];low=(uint32_t)q[0]<<16|(uint32_t)q[1]<<8|q[2];}
        gradient(a,(Rect){x,top,100,174},18,high,low);gradient(a,(Rect){x+10,top+12,80,50},12,t->hero_top,t->hero_bottom);
        gradient(a,(Rect){x+10,top+70,36,40},10,t->tile_fill[0],t->tile_fill[0]);gradient(a,(Rect){x+54,top+70,36,40},10,t->tile_fill[1],t->tile_fill[1]);
        veil(a,(Rect){x+10,top+118,80,20},8,t->panel,220);gradient(a,(Rect){x+10,top+146,80,16},8,t->accent,t->accent);
    }
    int room=f?356:440;ellipsis(a,r.x+144,r.y+60,room,2,TEXT,t->name);ellipsis(a,r.x+144,r.y+116,room,0,MUTED,note);
    vector_circle(a,644,r.y+105,17,4,selected?ORANGE:MUTED);if(selected)vector_circle(a,644,r.y+105,8,8,ORANGE);register_button(a,r,action,ui_free(a),0);
}
static void render_theme(App*a){
    static const char*const notes[THEME_COUNT]={"Dark and calm, the classic look","Light glass on a soft gradient","Dark glass with northern lights"};
    heading(a,"Theme","Choose how recovery looks.",true);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=236-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    for(unsigned int n=0;n<THEME_COUNT;n++){theme_card(a,&themes[n],NULL,notes[n],y,A_THEME_ITEM+(int)n);y+=226;}
    for(unsigned int n=0;n<theme_file_count;n++){
        const ThemeFile*f=&theme_files[n];if(!theme_file_ui(f))continue;
        theme_card(a,&f->theme,f,f->header.note[0]?f->header.note:"From a theme file",y,A_THEME_ITEM+THEME_COUNT+(int)n);y+=226;
    }
    button(a,(Rect){28,y,664,84},"Add a theme from a file",A_THEME_ADD,ui_free(a),false,0);y+=100;
    if(theme_files_hidden){button(a,(Rect){28,y,664,84},"Return removed themes",A_THEME_RESTORE,ui_free(a),false,0);y+=100;}
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
}
static void render_animations(App*a){
    static const char*const names[ANIMATION_COUNT]={"Zoom","Zoom with blur","Slide","Fade","Blur dissolve","Off"};
    static const char*const notes[ANIMATION_COUNT]={"The page grows out of the tile, with its icon","The page grows while the one behind goes soft","Pages slide sideways, without blur","Pages melt into each other, without blur","The old page blurs away and the new one sharpens","Pages change at once"};
    heading(a,"Animations","How one page gives way to the next.",true);
    for(int n=0;n<(int)ANIMATION_COUNT;n++){
        Rect r={28,232+n*176,664,160};bool selected=(int)a->animation==n;int action=A_ANIMATION_ITEM+n;surface(a,r,26,selected?ORANGE_DARK:PANEL,action);
        text(a,r.x+28,r.y+30,1,TEXT,names[n]);ellipsis(a,r.x+28,r.y+86,540,0,MUTED,notes[n]);
        vector_circle(a,644,r.y+80,17,4,selected?ORANGE:MUTED);if(selected)vector_circle(a,644,r.y+80,8,8,ORANGE);register_button(a,r,action,ui_free(a),0);
    }
    text_tile(a,1300,0,MUTED,"A new theme always spreads as a circle, unless animations are off.",2);
}
static int clock_index(char c){return c==':'?10:(c>='0'&&c<='9'?c-'0':0);}
static void clock_draw(App*a,int center,int top,uint32_t color,const char*s){
    Bounds b=canvas_bounds(a);int width=0,y=nx(top);for(const char*p=s;*p;p++)width+=clock_glyphs[clock_index(*p)].advance;
    int pen=nx(center)*64-width/2;
    for(const char*p=s;*p;p++){const Glyph*g=&clock_glyphs[clock_index(*p)];canvas_mask(a->pixels,b,((pen+32)>>6)+g->x,y+g->y,g->w,g->h,clock_font_alpha+g->off,color);pen+=g->advance;}
}
/* The theme's page colour over the blur keeps the clock readable on any picture. */
static void lock_frost(App*a){
    if(!a->lock_backdrop)a->lock_backdrop=malloc((size_t)NW*NH*sizeof(uint32_t));
    if(!a->lock_backdrop)return;
    canvas_blur(a->lock_backdrop,a->pixels);canvas_rect(a->lock_backdrop,(Bounds){0,0,NW,NH},0,0,NW,NH,BG,120);
}
/* A phone lock screen: the time over a frosted copy of the page, opened by a swipe. */
static void render_lockscreen(App*a){
    char now[16],date[96];clock_text(a,now,sizeof(now),date,sizeof(date));int lift=a->lock_drag*3/4;
    icon(a,A_OS,30,28,30,TEXT);text(a,70,32,0,TEXT,"Mainline Recovery");(void)battery_status(a,CW-32);
    icon(a,ICON_LOCK,CW/2-24,170-lift,48,TEXT);clock_draw(a,CW/2,232-lift,TEXT,now);centered(a,(Rect){28,452-lift,664,56},1,TEXT,date);
    if(a->job.pid&&job_long(a->job.kind)&&a->progress_total){char line[96];snprintf(line,sizeof(line),T("Operation running: %u%%"),(unsigned int)(a->progress_done*100/a->progress_total));centered(a,(Rect){28,516-lift,664,44},0,TEXT,line);}
    int y=1360-lift/3;vector_line(a,CW/2-24,y+24,CW/2,y,5,TEXT);vector_line(a,CW/2,y,CW/2+24,y+24,5,TEXT);centered(a,(Rect){28,y+40,664,44},0,TEXT,"Swipe up to unlock");
}
static void render_access(App*a){
    heading(a,"Computer access","A computer is asking to use this phone.",false);
    rounded(a,(Rect){28,236,664,250},26,PANEL);icon_tile(a,ICON_PHONE,52,262,84,ICON_FILL);
    text(a,160,264,2,TEXT,!strcmp(a->access_channel,"uart")?"UART console":!strcmp(a->access_channel,"adb")?"ADB connection":"SSH connection");
    {const char*cable=tr("Cable");raw_text(true);ellipsis(a,160,326,500,1,MUTED,a->access_peer[0]?a->access_peer:cable);raw_text(false);}
    wrapped(a,52,394,616,0,MUTED,"It can read and change everything here.",2);
    wrapped(a,40,520,640,1,TEXT,"Allow this computer only if you started this yourself.",3);
    text_tile(a,650,0,MUTED,"Access stays open until the phone locks or restarts. The computer never sees the password.",3);
    button(a,(Rect){28,1190,664,90},"Deny",A_ACCESS_DENY,true,false,0);button(a,(Rect){28,1320,664,102},"Allow with password",A_ACCESS_ALLOW,true,true,0);
}
static void terminal_card(App*a,const TerminalScheme*t,const ThemeFile*f,int y,int action){
    Rect r={28,y,664,110};bool selected=t==ui_terminal;
    uint32_t bg=t->follow_theme?ui_theme->terminal_bg:t->bg,fg=t->follow_theme?ui_theme->terminal_fg:t->fg;const uint32_t*palette=t->follow_theme?ui_theme->terminal_palette:t->palette;
    static const uint32_t model[16]=TM_ANSI_PALETTE;if(!palette)palette=model;
    gradient(a,r,24,selected?ORANGE:LINE,selected?ORANGE:LINE);gradient(a,(Rect){r.x+3,r.y+3,r.w-6,r.h-6},22,bg,bg);if(pressed(a,action))veil(a,r,24,fg,30);
    text(a,r.x+24,r.y+16,1,fg,t->name);
    for(unsigned int c=0;c<6;c++)vector_circle(a,r.x+38+(int)c*40,r.y+78,12,12,palette[c+1]);
    if(selected){vector_circle(a,640,r.y+55,17,17,ORANGE);vector_line(a,632,r.y+55,638,r.y+62,4,ON_ORANGE);vector_line(a,638,r.y+62,649,r.y+48,4,ON_ORANGE);}
    if(f)theme_trash(a,570,r.y+55,A_THEME_REMOVE+(int)(f-theme_files),fg);
    register_button(a,r,action,ui_free(a),0);
}
static void render_terminal_theme(App*a){
    heading(a,"Terminal colors","Background and text of the terminal.",true);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=236-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    for(unsigned int n=0;n<TERMINAL_SCHEME_COUNT;n++){terminal_card(a,&terminal_schemes[n],NULL,y,A_TERMINAL_THEME_ITEM+(int)n);y+=122;}
    for(unsigned int n=0;n<theme_file_count;n++){
        const ThemeFile*f=&theme_files[n];if(!theme_file_terminal(f))continue;
        terminal_card(a,&f->scheme,f,y,A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+(int)n);y+=122;
    }
    button(a,(Rect){28,y,664,84},"Add colors from a file",A_TERMINAL_ADD,ui_free(a),false,0);y+=100;
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
}
static void render_language(App*a){
    heading(a,"Language",NULL,true);
    for(unsigned int n=0;n<LANGUAGE_COUNT;n++){
        Rect r={28,214+(int)n*112,664,100};int action=A_LANGUAGE_ITEM+(int)n;bool selected=n==ui_language;surface(a,r,24,selected?ORANGE_DARK:PANEL,action);
        raw_text(true);text(a,56,r.y+30,1,TEXT,language_names[n]);raw_text(false);vector_circle(a,644,r.y+50,17,4,selected?ORANGE:MUTED);if(selected)vector_circle(a,644,r.y+50,8,8,ORANGE);register_button(a,r,action,ui_free(a),0);
    }
}

static bool usb_session_open(const App*a){return a->usb_session[0]&&strcmp(a->usb_state,"stopped");}
static bool usb_volume_listed(const FileVolume*v){return v->mountable&&v->token[0]&&(!strcmp(v->id,"userdata")||!strcmp(v->id,"cache")||!strncmp(v->id,"sdcard",6)||!strncmp(v->id,"usb-",4));}
static void render_usb(App*a){
    heading(a,"USB files","Share one mounted volume with this computer.",true);
    bool active=usb_session_open(a),stopped=a->usb_known&&!strcmp(a->usb_state,"stopped")&&!a->usb_session[0];
    UsbCandidate*c=a->usb_selected>=0&&a->usb_selected<a->usb_candidate_count?&a->usb_candidates[a->usb_selected]:NULL;
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=242-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    rounded(a,(Rect){28,y,664,166},24,PANEL);text(a,52,y+18,0,ORANGE,"FILE TRANSFER / MTP");
    const char*status=!a->usb_known?(a->job.pid?"Reading USB status...":"Status unavailable"):(!a->usb_available&&!active?"Unavailable":(!strcmp(a->usb_state,"active")?"Sharing is active":(stopped?"Sharing is stopped":a->usb_state)));
    text(a,52,y+60,2,active?GREEN:MUTED,status);text(a,52,y+118,0,MUTED,"Open the phone in your computer's file manager.");y+=182;
    /* Volumes are mounted here, so sharing one needs no trip through Files. */
    a->usb_row_count=0;for(int n=0;n<a->volume_count&&a->usb_row_count<USB_VOLUME_ROWS;n++)if(usb_volume_listed(&a->volumes[n]))a->usb_rows[a->usb_row_count++]=n;
    if(a->usb_row_count){
        text(a,40,y+8,0,ORANGE,"VOLUMES");y+=46;
        for(int k=0;k<a->usb_row_count;k++){
            const FileVolume*v=&a->volumes[a->usb_rows[k]];bool free_row=stopped&&ui_free(a);rounded(a,(Rect){28,y,664,112},24,PANEL);
            raw_text(true);ellipsis(a,52,y+16,v->mounted?440:300,1,TEXT,v->label[0]?v->label:v->id);raw_text(false);
            ellipsis(a,52,y+66,v->mounted?440:300,0,v->mounted?GREEN:MUTED,v->mounted?(v->read_only||!v->write_supported?"Mounted read-only":"Mounted with write access"):"Not mounted");
            if(v->mounted)button(a,(Rect){500,y+20,174,72},"Unmount",A_USB_VOLUME_OFF+k,free_row,false,0);
            else{button(a,(Rect){356,y+20,150,72},"Read",A_USB_VOLUME_RO+k,free_row,false,0);button(a,(Rect){516,y+20,158,72},"Hold: write",A_USB_VOLUME_RW+k,free_row&&v->write_supported,false,1000);}
            y+=124;
        }
        y+=8;
    }
    rounded(a,(Rect){28,y,664,196},24,PANEL);text(a,52,y+16,0,ORANGE,active?"SHARED VOLUME":"SELECTED VOLUME");
    wrapped(a,52,y+56,606,1,TEXT,active?(a->usb_export_id[0]?a->usb_export_id:"No valid export reported"):(c?(c->label[0]?c->label:c->id):"No mounted volume available"),2);
    ellipsis(a,52,y+146,606,0,MUTED,active?(a->usb_export_valid?(a->usb_export_writable?"Read and write access":"Read-only access"):"Refresh to inspect the export"):(c?c->mountpoint:"Mount a volume above."));y+=212;
    button(a,(Rect){28,y,664,78},"Choose next volume",A_USB_NEXT,stopped&&ui_free(a)&&a->usb_candidate_count>1,false,0);y+=94;
    bool ro=active?!a->usb_export_writable:(a->usb_read_only||!c||!c->writable);
    button(a,(Rect){28,y,320,88},"Read only",A_USB_RO,stopped&&ui_free(a)&&c,ro,0);
    button(a,(Rect){372,y,320,88},"Read and write",A_USB_RW,stopped&&ui_free(a)&&c&&c->writable,!ro,0);y+=104;
    rounded(a,(Rect){28,y,664,218},24,PANEL);wrapped(a,52,y+16,616,0,a->usb_reason[0]?ORANGE:MUTED,a->usb_reason[0]?a->usb_reason:"New files belong to root:root (0644; folders 0755). MTP has limited Linux metadata support. Stop sharing before mounting, editing in recovery, or making an offline snapshot.",6);y+=234;
    button(a,(Rect){28,y,664,78},"Refresh USB status",A_USB_REFRESH,ui_free(a),false,0);y+=94;
    a->page_content=y+a->page_scroll-230;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1070},a->page_scroll,a->page_content,ORANGE);
    if(active)button(a,(Rect){28,1320,664,104},"Stop sharing",A_USB_STOP,ui_free(a),true,0);
    else button(a,(Rect){28,1320,664,104},ro?"Hold 2 seconds to share read-only":"Hold 2 seconds to share read / write",A_USB_START,stopped&&a->usb_available&&c&&ui_free(a),true,2000);
}
static const struct { const char*label;unsigned int seconds; } screen_timeouts[]={{"30 s",30},{"1 min",60},{"2 min",120},{"5 min",300},{"10 min",600},{"Never",0}};
#define SCREEN_TIMEOUT_DEFAULT 2
/* The active rate looks selected, like the screen timeout, yet has nothing to do when tapped. */
static void display_mode_button(App*a,Rect r,const char*label,int action,bool ready,bool active){
    button(a,r,label,action,ready,active,0);
    if(active&&a->button_count&&a->buttons[a->button_count-1].action==action)a->buttons[a->button_count-1].enabled=false;
}
static void render_display(App*a){
    heading(a,"Display","Refresh rate, screen timeout and brightness.",true);
    rounded(a,(Rect){28,226,664,296},24,PANEL);text(a,52,246,0,ORANGE,"CURRENT DISPLAY MODE");
    /* The requested rate shows at once, so nothing on the page changes while the panel switches. */
    unsigned int shown=a->display_requested_hz?a->display_requested_hz:a->display_hz;
    char current[96];if(shown)snprintf(current,sizeof(current),"%u Hz",shown);else copy(current,sizeof(current),a->job.pid?"Reading display...":"Unavailable");
    text(a,52,282,2,shown?GREEN:MUTED,current);
    text(a,CW-52-text_width("1080 × 2408",0),296,0,MUTED,"1080 × 2408");
    /* Reading the state, and the check after a brightness change, leave this page usable. */
    bool busy=a->display_control.pid&&a->display_control.command>DC_BRIGHTNESS,ready=(a->display_known||a->display_requested_hz)&&a->display_supported&&a->display_service&&ui_free(a)&&!busy;
    display_mode_button(a,(Rect){52,360,296,84},"60 Hz",A_DISPLAY_60,ready&&a->display_60,shown==60);
    display_mode_button(a,(Rect){372,360,296,84},"90 Hz",A_DISPLAY_90,ready&&a->display_90,shown==90);
    if(a->display_requested_hz){char waiting[144];snprintf(waiting,sizeof(waiting),T("Applying and checking %u Hz..."),a->display_requested_hz);wrapped(a,52,456,616,0,ORANGE,waiting,2);}
    else wrapped(a,52,456,616,0,a->display_reason[0]?ORANGE:MUTED,a->display_reason[0]?a->display_reason:(a->display_note[0]?a->display_note:"The active mode is read again after each change."),2);
    rounded(a,(Rect){28,536,664,362},24,PANEL);text(a,52,560,0,ORANGE,"SCREEN TIMEOUT");
    for(unsigned int n=0;n<6;n++){Rect r={52+(int)(n%3)*212,604+(int)(n/3)*96,192,80};
        button(a,r,screen_timeouts[n].label,A_TIMEOUT+(int)n,a->display_blank_supported||!a->display_light_known,a->screen_timeout_index==n,0);}
    wrapped(a,52,802,610,0,MUTED,a->display_light_known&&!a->display_blank_supported?"Screen blanking is unavailable on this boot.":"The screen turns off when the phone is not touched. Press Power to wake it; operations keep running.",3);
    rounded(a,(Rect){28,916,664,288},24,PANEL);text(a,52,942,0,ORANGE,"BRIGHTNESS");
    bool light_ready=display_slider_ready(a);unsigned int percent=a->display_brightness_drag?a->display_preview_percent:(a->display_wanted_percent?a->display_wanted_percent:(a->display_requested_percent?a->display_requested_percent:a->display_percent));
    char light[80];if(a->display_light_known&&a->display_brightness_supported)snprintf(light,sizeof(light),"%u%%",percent);else copy(light,sizeof(light),a->display_control.pid?"Reading...":"Unavailable");
    text(a,668-text_width(light,1),940,1,light_ready?TEXT:MUTED,light);
    /* While dragging the handle stays under the finger, not on the nearest percent. */
    int knob=a->display_brightness_drag?(a->display_drag_x<76?76:(a->display_drag_x>644?644:a->display_drag_x)):76+(int)((percent?percent-1:0)*568u/99u);
    rounded(a,(Rect){56,1050,608,28},14,PANEL2);if(light_ready||a->display_brightness_drag)rounded(a,(Rect){56,1050,knob-56+14,28},14,ORANGE);
    if(a->display_brightness_drag)veil(a,(Rect){knob-40,1024,80,80},40,ORANGE,50);
    vector_circle(a,knob,1064,a->display_brightness_drag?28:24,a->display_brightness_drag?28:24,light_ready?ORANGE:MUTED);vector_circle(a,knob,1064,a->display_brightness_drag?20:17,a->display_brightness_drag?20:17,ui_theme->knob);
    if(a->button_count<MAX_BUTTONS)a->buttons[a->button_count++]=(Button){display_slider_rect(),A_DISPLAY_BRIGHTNESS,0,display_slider_usable(a),0};
    text(a,52,1126,0,MUTED,"1%");text(a,590,1126,0,MUTED,"100%");
    const char*light_note=a->display_brightness_drag?"Adjusting brightness.":(a->display_light_note[0]?a->display_light_note:"Short press Power to turn the screen off or on.");
    wrapped(a,40,1224,640,0,a->display_uncertain?ORANGE:MUTED,light_note,3);
    button(a,(Rect){28,1320,664,90},"Refresh display status",A_DISPLAY_REFRESH,ui_free(a)&&!busy,false,0);
}
/* Only the drag uses this path. display.py still checks the final value on release. */
static bool brightness_direct(App*a,unsigned int percent){
    if(!a->device||a->brightness_fd<0)return false;
    if(!a->brightness_fd){
        a->brightness_fd=-1;DIR*d=opendir("/sys/class/backlight");if(!d)return false;
        char name[256]={0};unsigned int found=0;for(struct dirent*e;(e=readdir(d))!=NULL;)if(e->d_name[0]!='.'){found++;copy(name,sizeof(name),e->d_name);}
        closedir(d);if(found!=1)return false;
        char path[320];snprintf(path,sizeof(path),"/sys/class/backlight/%s/max_brightness",name);FILE*f=fopen(path,"r");if(!f)return false;
        unsigned int maximum=0;int fields=fscanf(f,"%u",&maximum);fclose(f);if(fields!=1||!maximum)return false;
        snprintf(path,sizeof(path),"/sys/class/backlight/%s/brightness",name);int fd=open(path,O_WRONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<=0)return false;
        a->brightness_fd=fd;a->brightness_max=maximum;
    }
    /* Same rounding as percent_raw() in display.py. */
    unsigned int raw=(a->brightness_max*percent+50u)/100u;char value[16];int length=snprintf(value,sizeof(value),"%u",raw?raw:1u);
    return pwrite(a->brightness_fd,value,(size_t)length,0)==length;
}
static void render_tools_export(App*a){
    heading(a,"Export diagnostics","Choose an already mounted writable volume.",true);
    FileVolume*v=a->tools_volume_index>=0&&a->tools_volume_index<a->volume_count?&a->volumes[a->tools_volume_index]:NULL;
    rounded(a,(Rect){28,252,664,300},24,PANEL);text(a,52,278,0,ORANGE,"DESTINATION VOLUME");
    raw_text(v!=NULL);wrapped(a,52,328,606,1,TEXT,v?(v->label[0]?v->label:v->id):"No mounted writable volume",3);raw_text(false);
    if(v){ellipsis(a,52,450,606,0,MUTED,v->id);ellipsis(a,52,492,606,0,MUTED,v->mountpoint);}
    button(a,(Rect){28,592,664,88},"Choose next volume",A_TOOLS_DEST_NEXT,ui_free(a)&&v,false,0);
    text_tile(a,760,1,MUTED,v?"A new diagnostic archive will be written to this volume. Existing archives are retained.":"Open Files, select a supported volume and enable write access, then return here.",5);
    text_tile(a,1008,0,MUTED,"Diagnostic logs may include filenames, hardware identifiers and network details. Inspect the archive before sharing it.",4);
    button(a,(Rect){28,1320,664,104},"Hold 2 seconds to export",A_TOOLS_EXPORT_CONFIRM,ui_free(a)&&v&&v->mounted&&!v->read_only&&v->write_supported&&v->token[0],true,2000);
}
static FileVolume*current_volume(App*a){
    if(a->volume_index<0||a->volume_index>=a->volume_count)return NULL;
    FileVolume*v=&a->volumes[a->volume_index];
    return strcmp(v->id,a->volume_id)?NULL:v;
}
static bool files_writable(App*a){FileVolume*v=current_volume(a);return v&&v->mounted&&!v->read_only&&v->write_supported;}
static bool file_is_directory(const FileEntry*e){return !strcmp(e->type,"directory")||!strcmp(e->type,"dir");}
static bool file_is_regular(const FileEntry*e){return !strcmp(e->type,"file")||!strcmp(e->type,"regular");}
static bool file_is_executable(const FileEntry*e){return file_is_regular(e)&&e->metadata_supported&&e->mode[0]&&(strtoul(e->mode,NULL,8)&0111u);}
static uint32_t file_color(const FileEntry*e){return file_is_directory(e)?ORANGE:(file_is_executable(e)?GREEN:(file_is_regular(e)?FILE_BLUE:(!strcmp(e->type,"symlink")?LINK_PURPLE:MUTED)));}
static const char*file_type_label(const FileEntry*e){return file_is_directory(e)?"Folder":(file_is_executable(e)?"Executable":(file_is_regular(e)?"File":(!strcmp(e->type,"symlink")?"Symbolic link":"Special file / blocked")));}
static void file_icon(App*a,const FileEntry*e,int x,int y){
    uint32_t color=file_color(e);
    if(file_is_directory(e)){icon(a,A_FILES,x,y,38,color);return;}
    if(!strcmp(e->type,"symlink")){vector_circle(a,x+12,y+13,9,2,color);vector_circle(a,x+25,y+26,9,2,color);vector_line(a,x+12,y+13,x+25,y+26,3,color);return;}
    if(!file_is_regular(e)){vector_circle(a,x+19,y+19,16,2,color);vector_line(a,x+8,y+30,x+30,y+8,3,color);return;}
    if(file_is_executable(e)){
        vector_line(a,x+3,y+6,x+35,y+6,2,color);vector_line(a,x+3,y+6,x+3,y+32,2,color);vector_line(a,x+35,y+6,x+35,y+32,2,color);vector_line(a,x+3,y+32,x+35,y+32,2,color);
        vector_line(a,x+9,y+13,x+15,y+19,2,color);vector_line(a,x+15,y+19,x+9,y+25,2,color);vector_line(a,x+21,y+25,x+29,y+25,2,color);return;
    }
    vector_line(a,x+8,y+3,x+24,y+3,2,color);vector_line(a,x+24,y+3,x+31,y+10,2,color);vector_line(a,x+31,y+10,x+31,y+35,2,color);vector_line(a,x+31,y+35,x+8,y+35,2,color);vector_line(a,x+8,y+35,x+8,y+3,2,color);
    vector_line(a,x+24,y+3,x+24,y+10,2,color);vector_line(a,x+24,y+10,x+31,y+10,2,color);vector_line(a,x+13,y+18,x+26,y+18,2,color);vector_line(a,x+13,y+25,x+26,y+25,2,color);
}
static Rect browser_viewport(void){return(Rect){28,FILE_LIST_TOP,664,FILE_LIST_BOTTOM-FILE_LIST_TOP};}
static int64_t browser_max_scroll(const App*a){int64_t extent=(int64_t)a->file_total*FILE_ROW_HEIGHT-14-(FILE_LIST_BOTTOM-FILE_LIST_TOP);return extent>0?extent:0;}
static void browser_clamp(App*a){int64_t maximum=browser_max_scroll(a);if(a->browser_scroll<0)a->browser_scroll=0;if(a->browser_scroll>maximum)a->browser_scroll=maximum;}
static void reset_browser(App*a){stop_inertia(a,"folder-change");a->file_offset=a->file_total=0;a->file_count=0;a->files_more=false;a->browser_scroll=0;}
static const char*files_operation_title(int kind){
    switch(kind){case J_FS_MOUNT:return "Enable writes";case J_FS_WRITE:return "Save text";case J_FS_MKDIR:return "New folder";
    case J_FS_RENAME:return "Rename";case J_FS_COPY:return "Copy file";case J_FS_COPY_TREE:return "Copy folder";
    case J_FS_CHMOD:return "Permissions";case J_FS_CHOWN:return "Owner and group";case J_FS_DELETE:return "Delete";case J_FS_CHECK:return "Check and repair";case J_FS_RESIZE:return "Grow to fill";default:return "File operation";}
}
static void files_note(App*a,int y){if(a->fs_status[0])ellipsis(a,36,y,648,0,MUTED,a->fs_status);}
static Rect volume_viewport(void){return(Rect){28,VOLUME_LIST_TOP,664,VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP};}
static int64_t volume_max_scroll(const App*a){int64_t extent=(int64_t)a->volume_count*VOLUME_ROW_HEIGHT-16-(VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP);return extent>0?extent:0;}
static void volume_scroll_to(App*a,int64_t value){a->volume_scroll=(int64_t)scroll_clamp((double)value,(double)volume_max_scroll(a));a->focus=-1;a->dirty=true;}
static void scroll_indicator(App*a,Rect track,int64_t offset,int64_t total,uint32_t color){
    int64_t maximum=total-track.h;if(maximum<=0)return;
    int thumb=(int)((int64_t)track.h*track.h/total);if(thumb<28)thumb=28;
    int y=track.y+(int)(offset*(track.h-thumb)/maximum);rounded(a,(Rect){track.x,y,track.w,thumb},3,color);
}
enum VolumeKind { V_FILESYSTEM,V_SD,V_LOCKED,V_IMAGE,V_RAW,V_UNSUPPORTED };
static enum VolumeKind volume_kind(const FileVolume*v){
    if(strstr(v->fs_type,"crypto")||strstr(v->fs_type,"LUKS"))return V_LOCKED;
    if(!strcmp(v->fs_type,"raw")||!v->fs_type[0])return V_RAW;
    if(!strncmp(v->id,"sdcard",6))return V_SD;
    if(!strcmp(v->fs_type,"erofs")||!strcmp(v->fs_type,"squashfs"))return V_IMAGE;
    return v->mountable?V_FILESYSTEM:V_UNSUPPORTED;
}
static uint32_t volume_color(const FileVolume*v){enum VolumeKind kind=volume_kind(v);return kind==V_FILESYSTEM?FILE_BLUE:(kind==V_SD?GREEN:(kind==V_LOCKED?ORANGE:(kind==V_IMAGE?LINK_PURPLE:MUTED)));}
static const char*volume_type_label(const FileVolume*v){const char*labels[]={"Filesystem","microSD","Encrypted","Read-only image","Raw partition","Unsupported"};return labels[volume_kind(v)];}
static void volume_icon(App*a,const FileVolume*v,int x,int y){
    uint32_t c=volume_color(v);enum VolumeKind kind=volume_kind(v);
    if(!strncmp(v->id,"usb-",4)){icon(a,ICON_USB,x-2,y-2,48,c);return;}
    if(kind==V_SD){
        vector_line(a,x+10,y+3,x+26,y+3,2,c);vector_line(a,x+26,y+3,x+36,y+13,2,c);vector_line(a,x+36,y+13,x+36,y+41,2,c);vector_line(a,x+36,y+41,x+10,y+41,2,c);vector_line(a,x+10,y+41,x+10,y+3,2,c);
        for(int n=0;n<3;n++)vector_line(a,x+16+n*5,y+8,x+16+n*5,y+15,2,c);
    }else if(kind==V_LOCKED){
        vector_circle(a,x+22,y+13,10,2,c);vector_rectangle(a,(Rect){x+8,y+18,28,25},PANEL);
        vector_line(a,x+8,y+18,x+36,y+18,2,c);vector_line(a,x+8,y+18,x+8,y+42,2,c);vector_line(a,x+36,y+18,x+36,y+42,2,c);vector_line(a,x+8,y+42,x+36,y+42,2,c);vector_circle(a,x+22,y+28,3,3,c);vector_line(a,x+22,y+28,x+22,y+35,2,c);
    }else if(kind==V_RAW||kind==V_UNSUPPORTED){
        vector_line(a,x+10,y+10,x+34,y+10,2,c);vector_line(a,x+10,y+10,x+10,y+34,2,c);vector_line(a,x+34,y+10,x+34,y+34,2,c);vector_line(a,x+10,y+34,x+34,y+34,2,c);
        for(int n=0;n<3;n++){int at=15+n*7;vector_line(a,x+at,y+4,x+at,y+9,2,c);vector_line(a,x+at,y+35,x+at,y+40,2,c);vector_line(a,x+4,y+at,x+9,y+at,2,c);vector_line(a,x+35,y+at,x+40,y+at,2,c);}
    }else{
        vector_line(a,x+5,y+6,x+39,y+6,2,c);vector_line(a,x+5,y+6,x+5,y+39,2,c);vector_line(a,x+39,y+6,x+39,y+39,2,c);vector_line(a,x+5,y+39,x+39,y+39,2,c);vector_line(a,x+5,y+28,x+39,y+28,2,c);vector_circle(a,x+31,y+34,2,2,c);
        if(kind==V_IMAGE){vector_line(a,x+12,y+13,x+31,y+13,2,c);vector_line(a,x+12,y+20,x+25,y+20,2,c);}
        else{vector_circle(a,x+22,y+17,6,2,c);vector_line(a,x+22,y+17,x+31,y+24,2,c);}
    }
}
static void render_file_volumes(App*a){
    heading(a,a->theme_pick?"Theme file":a->install.pick_source?"Image source":(a->flash_pick?(a->zip_pick?"ZIP with an installer":(a->samsung_pick?(a->stock_boot_pick?"Stock BOOT":"Samsung firmware"):"Image source")):(a->systems_pick_manifest?(a->systems_pick_script?"Setup script source":"System image source"):"Files")),a->theme_pick?"Choose where the theme file is stored.":a->flash_pick?(a->zip_pick?"Choose where the ZIP is stored.":"Choose where the image is stored."):a->install.pick_source?"Select writable ext4 microSD. The source remains unchanged.":(a->systems_pick_manifest?(a->systems_pick_script?"Choose where the script is stored.":"Choose the microSD card or USB stick with the image; one that is not mounted opens read-only."):"Swipe to browse volumes. Open read-only by default."),true);a->volume_scroll=(int64_t)scroll_clamp((double)a->volume_scroll,(double)volume_max_scroll(a));
    int first=(int)(a->volume_scroll/VOLUME_ROW_HEIGHT);a->clip_active=true;a->clip=volume_viewport();
    for(int i=first;i<a->volume_count;i++){
        int y=VOLUME_LIST_TOP+(int)((int64_t)i*VOLUME_ROW_HEIGHT-a->volume_scroll);if(y>=VOLUME_LIST_BOTTOM)break;FileVolume*v=&a->volumes[i];
        Rect r={28,y,664,142};rounded(a,r,24,PANEL);volume_icon(a,v,49,y+45);
        raw_text(true);ellipsis(a,116,r.y+14,550,1,volume_color(v),v->label[0]?v->label:v->id);raw_text(false);
        char linebuf[256],size[64];human_bytes(v->bytes,size,sizeof(size));
        snprintf(linebuf,sizeof(linebuf),"%s  /  %.24s  /  %s",tr(volume_type_label(v)),v->fs_type[0]?v->fs_type:tr("raw"),v->bytes?size:tr("size unknown"));
        ellipsis(a,116,r.y+58,550,0,MUTED,linebuf);
        ellipsis(a,116,r.y+98,550,0,v->mounted?GREEN:(v->mountable?MUTED:ORANGE),v->mounted?(v->read_only||!v->write_supported?"Mounted read-only":"Mounted with write access"):(v->mountable?"Not mounted":(v->reason[0]?v->reason:"File browsing unavailable")));
        register_button(a,r,A_FS_VOLUME_ITEM+i,ui_free(a)&&(!a->systems_pick_manifest||systems_source_listed(v)||(a->systems_pick_script&&script_store_volume(v)))&&(!a->install.pick_source||install_source_allowed(v))&&(!a->flash_pick||flash_source_allowed(v))&&(!a->theme_pick||flash_source_allowed(v)),0);
    }
    a->clip_active=false;scroll_indicator(a,(Rect){684,VOLUME_LIST_TOP,6,VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP},a->volume_scroll,(int64_t)a->volume_count*VOLUME_ROW_HEIGHT-16,ORANGE);
    if(!a->volume_count)wrapped(a,48,300,624,1,MUTED,a->job.pid?"Reading storage volumes...":"No volume inventory received. Tap Refresh to try again.",5);
    button(a,(Rect){28,1220,200,74},"↑",A_FS_PREV,ui_free(a)&&a->volume_scroll>0,false,0);
    int last=(int)((a->volume_scroll+VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP+VOLUME_ROW_HEIGHT-1)/VOLUME_ROW_HEIGHT);if(last>a->volume_count)last=a->volume_count;
    char count[64];if(a->volume_count)snprintf(count,sizeof(count),"%d–%d / %d",first+1,last,a->volume_count);else copy(count,sizeof(count),"No volumes");centered(a,(Rect){240,1220,240,74},0,MUTED,count);
    button(a,(Rect){492,1220,200,74},"↓",A_FS_NEXT,ui_free(a)&&a->volume_scroll<volume_max_scroll(a),false,0);
    button(a,(Rect){28,1330,664,84},"Refresh volumes",A_FS_REFRESH,ui_free(a),false,0);
    files_note(a,1444);
}
static void render_file_volume(App*a){
    FileVolume*v=current_volume(a);heading(a,"Volume",v?NULL:"Volume unavailable",true);
    if(v){raw_text(true);wrapped(a,104,164,CW-134,0,MUTED,v->label[0]?v->label:v->id,2);raw_text(false);}
    if(!v){wrapped(a,48,290,624,1,MUTED,"This volume is no longer available. Return to Files and refresh the list.",5);return;}
    rounded(a,(Rect){28,240,664,350},24,PANEL);
    text(a,52,262,0,ORANGE,"FILESYSTEM");text(a,52,302,2,TEXT,v->fs_type[0]?v->fs_type:"Not mountable");
    char bytes[64];human_bytes(v->bytes,bytes,sizeof(bytes));text(a,52,365,1,MUTED,bytes);
    text(a,52,416,0,v->mounted?GREEN:MUTED,v->mounted?(v->read_only||!v->write_supported?"Mounted read-only":"Write access enabled"):"Not mounted");
    wrapped(a,52,460,602,0,MUTED,v->mountpoint[0]?v->mountpoint:v->id,3);
    const char*reason=v->reason[0]?v->reason:(!v->write_supported&&v->read_only_reason[0]?v->read_only_reason:"Open read-only to inspect files. Enable writes only when you want to make changes.");
    wrapped(a,40,634,640,1,v->mountable?MUTED:ORANGE,reason,6);
    bool can_rw=v->mountable&&v->write_supported;
    if(v->mounted){
        button(a,(Rect){28,1000,664,90},"Open files",A_FS_OPEN,ui_free(a),true,0);
        button(a,(Rect){28,1120,320,86},v->read_only?"Enable writes":"Read-only",v->read_only?A_FS_MOUNT_RW:A_FS_MOUNT_RO,ui_free(a)&&(!v->read_only||can_rw),false,0);
        button(a,(Rect){372,1120,320,86},"Unmount",A_FS_UNMOUNT,ui_free(a),false,0);
    }else{
        bool internal=!strcmp(v->id,"userdata")||!strcmp(v->id,"cache");
        bool admitted=!a->flash_pick&&!a->theme_pick&&(internal||!strncmp(v->id,"sdcard",6)||!strncmp(v->id,"usb-",4));
        if(!admitted){
            button(a,(Rect){28,1000,664,90},"Mount read-only",A_FS_MOUNT_RO,ui_free(a)&&v->mountable,true,0);
            button(a,(Rect){28,1120,664,86},"Mount with write access",A_FS_MOUNT_RW,ui_free(a)&&can_rw,false,0);
        }else{
            button(a,(Rect){28,1000,664,80},"Mount read-only",A_FS_MOUNT_RO,ui_free(a)&&v->mountable,true,0);
            button(a,(Rect){28,1092,664,76},"Mount with write access",A_FS_MOUNT_RW,ui_free(a)&&can_rw,false,0);
            /* Like the file system repair and resize in TWRP: e2fsck and resize2fs, so ext2/3/4 only;
               the recovery has no F2FS or FAT fsck. */
            bool ext=!strcmp(v->fs_type,"ext4")||!strcmp(v->fs_type,"ext3")||!strcmp(v->fs_type,"ext2");int y=1180;
            button(a,(Rect){28,y,664,76},ext?"Hold: check and repair":"Check and repair: ext4 only",A_FS_CHECK,ui_free(a)&&ext,false,ext?2000:0);y+=88;
            if(ext){button(a,(Rect){28,y,664,76},"Hold: grow to fill the partition",A_FS_RESIZE,ui_free(a),false,2000);y+=88;}
            button(a,(Rect){28,y,320,76},"Hold: erase, ext4",A_FS_FORMAT_EXT4,ui_free(a),false,internal?6000:4000);
            button(a,(Rect){372,y,320,76},internal?"Hold: erase, F2FS":"Hold: erase, FAT32",internal?A_FS_FORMAT_F2FS:A_FS_FORMAT_VFAT,ui_free(a),false,internal?6000:4000);
            wrapped(a,40,y+88,640,0,ORANGE,internal?"Formatting erases every system and /home here. Android needs F2FS, Linux ext4.":"Formatting erases everything on this volume, including backups.",2);
            files_note(a,y+164);
            return;
        }
    }
    text_tile(a,1270,0,MUTED,"Encrypted Android data needs a working decryption service. Raw boot images and firmware partitions cannot be browsed as folders.",5);
    files_note(a,1444);
}
static void render_file_browser(App*a){
    FileVolume*v=current_volume(a);char sub[256];snprintf(sub,sizeof(sub),"%.150s  /  %s",v?(v->label[0]?v->label:v->id):tr("Volume"),tr(files_writable(a)?"Writable":"Read-only"));
    heading(a,a->theme_pick?"Choose a theme file":a->install.pick_source?(a->install.stock_selected?"Stock manifest":"BOOT image"):(a->flash_pick?(a->rom_pick?"Choose the ROM":(a->zip_pick?"Choose the ZIP":(a->samsung_pick?"Choose the AP file":"Choose image"))):(a->systems_pick_manifest?(a->systems_pick_script?"Choose a setup script":"Choose system image"):"Files")),sub,true);rounded(a,(Rect){28,230,664,94},14,PANEL);wrapped(a,46,246,628,0,TEXT,a->fs_path,2);
    button(a,(Rect){28,340,208,64},"Up",A_FS_UP,ui_free(a)&&strcmp(a->fs_path,"/"),false,0);
    button(a,(Rect){256,340,208,64},"Volume",A_FS_VOLUME,ui_free(a),false,0);
    button(a,(Rect){484,340,208,64},"Refresh",A_FS_REFRESH,ui_free(a),false,0);
    browser_clamp(a);unsigned int first=(unsigned int)(a->browser_scroll/FILE_ROW_HEIGHT);
    a->clip_active=true;a->clip=browser_viewport();
    for(unsigned int index=first;index<a->file_total;index++){
        int y=FILE_LIST_TOP+(int)((int64_t)index*FILE_ROW_HEIGHT-a->browser_scroll);if(y>=FILE_LIST_BOTTOM)break;
        Rect r={28,y,664,102};rounded(a,r,14,PANEL);
        if(index<a->file_offset||index-a->file_offset>=(unsigned int)a->file_count){text(a,48,r.y+32,0,MUTED,"Loading...");continue;}
        int n=(int)(index-a->file_offset);FileEntry*e=&a->files[n];
        bool usable=file_is_directory(e)||file_is_regular(e);
        {uint32_t bar=file_color(e);gradient(a,(Rect){35,r.y+20,5,62},3,bar,bar);}file_icon(a,e,48,r.y+30);
        /* A file's name is never translated (2026-11-11: the folder "backup" was listed as a word of the interface). */
        raw_text(true);ellipsis(a,96,r.y+13,476,1,file_color(e),e->name);raw_text(false);
        char desc[128],bytes[64];human_bytes(e->size,bytes,sizeof(bytes));snprintf(desc,sizeof(desc),"%s%s%s",tr(file_type_label(e)),file_is_regular(e)?"  /  ":"",file_is_regular(e)?bytes:"");
        ellipsis(a,96,r.y+62,476,0,MUTED,desc);
        register_button(a,(Rect){r.x,r.y,548,r.h},A_FS_ENTRY+n,ui_free(a)&&usable,0);
        button(a,(Rect){594,r.y+17,78,68},"...",A_FS_ENTRY_MENU+n,ui_free(a),false,0);
    }
    a->clip_active=false;
    if(!a->file_total)wrapped(a,48,490,624,1,MUTED,a->job.pid?"Reading folder...":"This folder is empty.",4);
    int64_t maximum=browser_max_scroll(a);
    if(maximum>0){Rect track={684,FILE_LIST_TOP,6,FILE_LIST_BOTTOM-FILE_LIST_TOP};rounded(a,track,3,PANEL2);
        int thumb=(int)((int64_t)track.h*track.h/((int64_t)a->file_total*FILE_ROW_HEIGHT));if(thumb<28)thumb=28;
        int y=track.y+(int)(a->browser_scroll*(track.h-thumb)/maximum);rounded(a,(Rect){track.x,y,track.w,thumb},3,ORANGE);}
    button(a,(Rect){28,1258,140,68},"↑",A_FS_PREV,ui_free(a)&&a->browser_scroll>0,false,0);
    unsigned int last=(unsigned int)((a->browser_scroll+(FILE_LIST_BOTTOM-FILE_LIST_TOP)+FILE_ROW_HEIGHT-1)/FILE_ROW_HEIGHT);if(last>a->file_total)last=a->file_total;
    char count[96];if(a->file_total)snprintf(count,sizeof(count),"%u–%u / %u",first+1,last,a->file_total);else copy(count,sizeof(count),"Swipe to scroll");
    centered(a,(Rect){184,1258,352,68},0,MUTED,count);
    button(a,(Rect){552,1258,140,68},"↓",A_FS_NEXT,ui_free(a)&&a->browser_scroll<maximum,false,0);
    if(a->install.pick_source)button(a,(Rect){28,1358,664,82},"Cancel image selection",A_INSTALL_CANCEL_PICK,ui_free(a),false,0);
    else if(a->systems_pick_manifest)button(a,(Rect){28,1358,664,82},"Cancel selection",A_SYS_CANCEL_PICK,ui_free(a),false,0);
    else if(a->flash_pick)button(a,(Rect){28,1358,664,82},"Cancel selection",A_FLASH_CANCEL_PICK,ui_free(a),false,0);
    else if(a->theme_pick)button(a,(Rect){28,1358,664,82},"Cancel selection",A_THEME_CANCEL_PICK,ui_free(a),false,0);
    else{button(a,(Rect){28,1358,320,82},"New text file",A_FS_NEW_FILE,ui_free(a)&&files_writable(a),true,0);
    button(a,(Rect){372,1358,320,82},"New folder",A_FS_MKDIR,ui_free(a)&&files_writable(a),false,0);}
    files_note(a,1460);
}
static bool theme_file_name(const char*name){return theme_file_suffix(name,".a22xtheme");}
static void render_file_info(App*a){
    if(a->install.pick_source){render_install_source(a);return;}
    if(a->systems_pick_manifest){if(a->systems_pick_script)render_systems_script(a);else render_systems_manifest(a);return;}
    heading(a,"File actions",file_type_label(&a->fs_selected),true);rounded(a,(Rect){28,242,664,354},24,PANEL);
    raw_text(true);wrapped(a,52,264,608,1,TEXT,a->fs_selected.name,2);wrapped(a,52,354,608,0,MUTED,a->fs_selected.path,3);raw_text(false);
    char bytes[64],metadata[192];human_bytes(a->fs_selected.size,bytes,sizeof(bytes));text(a,52,456,0,ORANGE,bytes);
    snprintf(metadata,sizeof(metadata),"Mode %s  /  UID %llu  /  GID %llu",a->fs_selected.mode[0]?a->fs_selected.mode:"unknown",(unsigned long long)a->fs_selected.uid,(unsigned long long)a->fs_selected.gid);
    wrapped(a,52,506,608,0,TEXT,metadata,2);
    bool ordinary=file_is_regular(&a->fs_selected)||file_is_directory(&a->fs_selected);
    if(!strcmp(a->fs_selected.type,"symlink")){
        text(a,40,632,0,ORANGE,"LINK TEXT");wrapped(a,40,676,640,0,TEXT,a->fs_selected.link_target[0]?a->fs_selected.link_target:"No link text returned",5);
        text(a,40,856,0,ORANGE,"RESOLVED INSIDE THIS VOLUME");wrapped(a,40,900,640,0,TEXT,a->fs_link_resolved?a->fs_resolved.path:"Resolve before opening the target.",5);
        button(a,(Rect){28,1080,664,88},"Resolve link",A_FS_RESOLVE,ui_free(a)&&a->fs_selected.token[0],false,0);
        button(a,(Rect){28,1194,664,88},"Open resolved target",A_FS_FOLLOW,ui_free(a)&&a->fs_link_resolved&&(file_is_regular(&a->fs_resolved)||file_is_directory(&a->fs_resolved)),true,0);
    }else{
        if(theme_file_name(a->fs_selected.name)&&file_is_regular(&a->fs_selected)){button(a,(Rect){28,626,320,88},"Open as text",A_FS_READ,ui_free(a),false,0);button(a,(Rect){372,626,320,88},"Use this theme",A_THEME_USE,ui_free(a),true,0);}
        else button(a,(Rect){28,626,664,88},file_is_directory(&a->fs_selected)?"Open folder":"Open as text",A_FS_READ,ui_free(a)&&ordinary,true,0);
        button(a,(Rect){28,738,320,88},"Rename",A_FS_RENAME,ui_free(a)&&ordinary&&files_writable(a),false,0);
        button(a,(Rect){372,738,320,88},file_is_directory(&a->fs_selected)?"Copy folder":"Copy file",A_FS_COPY,ui_free(a)&&(file_is_regular(&a->fs_selected)||(file_is_directory(&a->fs_selected)&&a->fs_copy_directories))&&a->fs_selected.token[0],false,0);
        bool metadata_ok=ui_free(a)&&files_writable(a)&&a->fs_selected.metadata_supported&&a->fs_selected.token[0];
        button(a,(Rect){28,850,320,88},"Permissions",A_FS_CHMOD,metadata_ok,false,0);
        button(a,(Rect){372,850,320,88},"Owner / group",A_FS_CHOWN,metadata_ok,false,0);
        button(a,(Rect){28,962,664,88},"Delete",A_FS_DELETE,ui_free(a)&&ordinary&&files_writable(a),false,0);
        text_tile(a,1094,0,MUTED,a->fs_selected.metadata_reason[0]?a->fs_selected.metadata_reason:"Copy and rename require a new destination. Delete removes a regular file or an empty folder.",4);
        files_note(a,1220);
        if(!files_writable(a))text_tile(a,1260,0,ORANGE,current_volume(a)&&current_volume(a)->write_supported?"Read-only volume. Enable writes in Volume.":"This volume is available for reading only.",2);
    }
    button(a,(Rect){28,1358,664,82},"Volume settings",A_FS_VOLUME,ui_free(a),false,0);
}
typedef struct {size_t end,next;int width;} TextRow;
static TextRow text_row(const char*s,size_t start,int width){
    const char*p=s+start;size_t end=start;int x=0;
    while(*p){const char*before=p;uint32_t cp=utf8_next(&p);if(cp=='\n')return(TextRow){(size_t)(before-s),(size_t)(p-s),x};
        int w=codepoint_width(cp,0);if(x+w>width&&before>s+start)return(TextRow){(size_t)(before-s),(size_t)(before-s),x};x+=w;end=(size_t)(p-s);}
    return(TextRow){end,end,x};
}
static size_t row_start_at(const char*s,size_t position,int width){
    size_t start=0;while(s[start]){TextRow r=text_row(s,start,width);if(position<r.next||(!s[r.next]&&position==r.end)||r.next==start)return start;start=r.next;}return start;
}
static size_t previous_row(const char*s,size_t start,int width){
    size_t row=0,previous=0;while(row<start&&s[row]){previous=row;TextRow r=text_row(s,row,width);if(r.next<=row||r.next>=start)return previous;row=r.next;}return previous;
}
static Rect text_box(const App*a){return a->screen==FILE_PROMPT?(Rect){28,448,664,198}:(a->screen==FILE_EDIT?(Rect){28,232,664,586}:(Rect){28,232,664,936});}
static Rect text_content(Rect box){return(Rect){box.x+18,box.y+14,box.w-36,box.h-28};}
static void text_layout(App*a,bool prompt,int width){
    const char*s=prompt?a->fs_prompt:a->fs_text;uint64_t hash=UINT64_C(14695981039346656037);size_t length=0;
    while(s[length]){hash^=(unsigned char)s[length++];hash*=UINT64_C(1099511628211);}
    if(a->text_row_count&&a->text_cache_prompt==prompt&&a->text_cache_width==width&&a->text_cache_length==length&&a->text_cache_hash==hash)return;
    a->text_cache_prompt=prompt;a->text_cache_width=width;a->text_cache_length=length;a->text_cache_hash=hash;a->text_row_count=0;
    size_t row=0;
    while(a->text_row_count<TEXT_LIMIT+1){
        a->text_rows[a->text_row_count++]=(uint32_t)row;TextRow next=text_row(s,row,width);
        if(next.next<=row)break;
        row=next.next;if(!s[row]&&next.next==next.end)break;
    }
}
static unsigned int text_cursor_row(const App*a,size_t position){
    unsigned int lo=0,hi=a->text_row_count;
    while(lo+1<hi){unsigned int middle=lo+(hi-lo)/2;if(a->text_rows[middle]<=position)lo=middle;else hi=middle;}
    return lo;
}
static int64_t text_max_scroll(App*a,bool prompt,Rect box){
    Rect content=text_content(box);text_layout(a,prompt,content.w);
    int64_t maximum=(int64_t)a->text_row_count*TEXT_ROW_HEIGHT-content.h;return maximum>0?maximum:0;
}
static void text_scroll_to(App*a,bool prompt,int64_t value){
    size_t*scroll=prompt?&a->prompt_scroll:&a->fs_scroll;
    *scroll=(size_t)scroll_clamp((double)value,(double)text_max_scroll(a,prompt,text_box(a)));
    if(prompt)a->prompt_caret_follow=false;else a->fs_caret_follow=false;
    a->focus=-1;a->dirty=true;
}
static void text_viewport(App*a,Rect box,bool prompt,bool caret){
    const char*s=prompt?a->fs_prompt:a->fs_text;size_t cursor=prompt?a->prompt_cursor:a->fs_cursor,*scroll=prompt?&a->prompt_scroll:&a->fs_scroll;
    bool*follow=prompt?&a->prompt_caret_follow:&a->fs_caret_follow;Rect content=text_content(box);rounded(a,box,14,PANEL);
    int64_t maximum=text_max_scroll(a,prompt,box);*scroll=(size_t)scroll_clamp((double)*scroll,(double)maximum);
    if(caret&&*follow){
        size_t top=(size_t)text_cursor_row(a,cursor)*TEXT_ROW_HEIGHT;
        if(top<*scroll)*scroll=top;else if(top+TEXT_ROW_HEIGHT>*scroll+(size_t)content.h)*scroll=top+TEXT_ROW_HEIGHT-(size_t)content.h;
        *scroll=(size_t)scroll_clamp((double)*scroll,(double)maximum);*follow=false;
    }
    bool previous_clip=a->clip_active;Rect previous_rect=a->clip;a->clip_active=true;a->clip=content;
    unsigned int first=(unsigned int)(*scroll/TEXT_ROW_HEIGHT);
    for(unsigned int index=first;index<a->text_row_count;index++){
        int y=content.y+(int)((int64_t)index*TEXT_ROW_HEIGHT-(int64_t)*scroll);if(y>=content.y+content.h)break;
        size_t row=a->text_rows[index];TextRow r=text_row(s,row,content.w);const char*p=s+row;int x=content.x;
        while((size_t)(p-s)<r.end){size_t pos=(size_t)(p-s);if(caret&&pos==cursor)rectangle(a,(Rect){x,y,2,25},ORANGE);uint32_t cp=utf8_next(&p);x+=draw_codepoint(a,x,y,0,TEXT,cp);}
        if(caret&&cursor==r.end&&(r.next>r.end||!s[r.end]))rectangle(a,(Rect){x,y,2,25},ORANGE);
    }
    a->clip_active=previous_clip;a->clip=previous_rect;
    scroll_indicator(a,(Rect){box.x+box.w-10,content.y,4,content.h},(int64_t)*scroll,(int64_t)a->text_row_count*TEXT_ROW_HEIGHT,ORANGE);
    if(caret)register_button(a,box,A_FS_CARET,!a->job.pid,0);
}
static uint32_t uppercase_cp(uint32_t cp){
    if(cp>='a'&&cp<='z')return cp-32;
    if(cp>=0x430&&cp<=0x44f)return cp-32;
    if(cp==0x456)return 0x406;
    if(cp==0x457)return 0x407;
    if(cp==0x454)return 0x404;
    if(cp==0x491)return 0x490;
    return cp;
}
static void keyboard_row(App*a,const char*s,int y,int x,int width){
    const char*p=s;int count=0;while(*p){(void)utf8_next(&p);count++;}if(!count)return;
    int w=(width-(count-1)*6)/count;p=s;
    for(int k=0;k<count&&a->keyboard_count<(int)(sizeof(a->keyboard_keys)/sizeof(a->keyboard_keys[0]));k++){
        uint32_t cp=utf8_next(&p);if(a->kb_shift)cp=uppercase_cp(cp);char label[5];utf8_encode(cp,label);
        int key=a->keyboard_count++;a->keyboard_keys[key]=cp;button(a,(Rect){x+k*(w+6),y,w,70},label,A_KB_KEY+key,keyboard_enabled(a),false,0);
    }
}
#define KB_BUBBLE_MS 160
#define KB_REPEAT_DELAY_MS 450
#define KB_REPEAT_MS 70
static void keyboard_key(App*a,Rect r,uint32_t cp){
    char label[5];utf8_encode(cp,label);int key=a->keyboard_count++;a->keyboard_keys[key]=cp;button(a,r,label,A_KB_KEY+key,keyboard_enabled(a),false,0);
}
/* The pressed key, large and in a circle above the finger that covers it. */
static void key_bubble(App*a){
    uint32_t cp=0;Rect r={0,0,0,0};
    if(!a->kb_drawn)return;
    if(a->pointer_down&&a->pressed_action>=A_KB_KEY&&a->pressed_action<A_KB_KEY+a->keyboard_count){cp=a->keyboard_keys[a->pressed_action-A_KB_KEY];r=a->pressed_rect;}
    else if(a->kb_bubble_until&&a->now<a->kb_bubble_until){cp=a->kb_bubble_cp;r=a->kb_bubble_rect;}
    if(!cp||cp==' ')return;
    int radius=48,cx=r.x+r.w/2,cy=r.y-radius-12;if(cx<radius+10)cx=radius+10;if(cx>CW-radius-10)cx=CW-radius-10;
    Rect disc={cx-radius,cy-radius,2*radius,2*radius};
    canvas_shadow(a->pixels,canvas_bounds(a),nx(disc.x+8),nx(disc.y+10),nx(disc.x+disc.w-8),nx(disc.y+disc.h+4),30.f,ui_theme->shadow,ui_theme->shadow_strength);
    rounded(a,disc,radius,ORANGE);
    char label[5];utf8_encode(cp,label);raw_text(true);centered(a,disc,3,ON_ORANGE,label);raw_text(false);
}
static void render_keyboard(App*a){
    a->keyboard_count=0;a->kb_drawn=true;
    const char*rows[4];rows[0]="1234567890";
    if(a->kb_symbols){rows[1]=a->screen==TERMINAL?"[]{}<>_+-=~`":"[]{}<>_+-=";rows[2]="!@#$%^&*()";rows[3]="\\|/:;\"',.?";}
    else if(a->kb_uk){rows[1]="йцукенгшщзхї";rows[2]="фівапролджє";rows[3]="ячсмитьбюґ";}
    else{rows[1]="qwertyuiop";rows[2]="asdfghjkl";rows[3]="zxcvbnm";}
    keyboard_row(a,rows[0],930,28,664);keyboard_row(a,rows[1],1010,28,664);keyboard_row(a,rows[2],1090,28,664);
    button(a,(Rect){28,1170,74,70},a->kb_shift?"ABC":"Shift",A_KB_SHIFT,keyboard_enabled(a),a->kb_shift,0);
    keyboard_row(a,rows[3],1170,108,474);
    /* Delete is wide, drawn as a key and repeats when held. A prompt has its own Next button below
     * the keyboard, so no key under Delete moves on to the next page (2026-11-08). */
    Rect erase={590,1170,102,70};button(a,erase,"",A_KB_BACKSPACE,keyboard_enabled(a),false,0);
    icon(a,ICON_ERASE,erase.x+erase.w/2-22,erase.y+erase.h/2-22,44,keyboard_enabled(a)?TEXT:MUTED);
    const int y=1262;
    button(a,(Rect){28,y,88,70},a->kb_uk?"UK":"EN",A_KB_LANG,keyboard_enabled(a),false,0);
    button(a,(Rect){122,y,80,70},a->kb_symbols?"abc":"#+=",A_KB_SYMBOLS,keyboard_enabled(a),false,0);
    if(a->screen==FILE_PROMPT){
        button(a,(Rect){208,y,330,70},"Space",A_KB_SPACE,keyboard_enabled(a),false,0);
        keyboard_key(a,(Rect){544,y,70,70},'.');keyboard_key(a,(Rect){620,y,72,70},'-');
    }else{
        button(a,(Rect){208,y,292,70},"Space",A_KB_SPACE,keyboard_enabled(a),false,0);
        keyboard_key(a,(Rect){506,y,72,70},'.');
        button(a,(Rect){584,y,108,70},"Enter",A_KB_ENTER,keyboard_enabled(a),false,0);
    }
}
static void cursor_buttons(App*a,int y){
    button(a,(Rect){28,y,154,64},"←",A_FS_LEFT,ui_free(a),false,0);button(a,(Rect){198,y,154,64},"↑",A_FS_UP_LINE,ui_free(a),false,0);
    button(a,(Rect){368,y,154,64},"↓",A_FS_DOWN_LINE,ui_free(a),false,0);button(a,(Rect){538,y,154,64},"→",A_FS_RIGHT,ui_free(a),false,0);
}
static void render_file_text(App*a,bool editing){
    heading(a,editing?"Edit text":"Text file",a->fs_file,true);
    text_viewport(a,editing?(Rect){28,232,664,586}:(Rect){28,232,664,936},false,editing);
    size_t cursor_y=(size_t)text_cursor_row(a,a->fs_cursor)*TEXT_ROW_HEIGHT;bool outside=editing&&(cursor_y<a->fs_scroll||cursor_y+TEXT_ROW_HEIGHT>a->fs_scroll+558);
    char info[160];snprintf(info,sizeof(info),"%zu %s%s%s%s",a->fs_length,tr("bytes"),a->fs_truncated?tr(" / preview truncated"):"",a->fs_dirty?tr(" / unsaved"):"",outside?tr(" / tap text to move cursor"):"");ellipsis(a,34,editing?824:1180,652,0,a->fs_truncated?ORANGE:MUTED,info);
    if(editing){
        cursor_buttons(a,856);render_keyboard(a);
        button(a,(Rect){28,1370,256,84},"Cancel",A_FS_CANCEL_EDIT,ui_free(a),false,0);
        button(a,(Rect){304,1370,388,84},"Review save",A_FS_SAVE,ui_free(a)&&!a->fs_truncated&&(a->fs_dirty||a->fs_new_file),true,0);
    }else{
        button(a,(Rect){28,1230,320,74},"↑",A_FS_PAGE_UP,ui_free(a)&&a->fs_scroll>0,false,0);
        button(a,(Rect){372,1230,320,74},"↓",A_FS_PAGE_DOWN,ui_free(a)&&(int64_t)a->fs_scroll<text_max_scroll(a,false,text_box(a)),false,0);
        button(a,(Rect){28,1354,320,88},"Edit text",A_FS_EDIT,ui_free(a)&&!a->fs_truncated&&files_writable(a),true,0);
        button(a,(Rect){372,1354,320,88},"Volume",A_FS_VOLUME,ui_free(a),false,0);
        if(a->fs_truncated)text(a,34,1458,0,ORANGE,"Editing disabled: file exceeds the 64 KiB UI limit.");
        else if(!files_writable(a))text(a,34,1458,0,MUTED,current_volume(a)&&current_volume(a)->write_supported?"Enable writes in Volume before editing.":"This volume is available for reading only.");
    }
}
/* The password page. Digits come first, as on a phone; a password with letters is one tap away, on the keyboard. */
#define PAD_RADIUS 84
#define PAD_TOP 610
#define PAD_PITCH 186
#define PAD_SHADOW_SPREAD 22.f
#define PAD_SHADOW_DROP 5
static const int pad_columns[3]={150,360,570};
static void pad_digit(App*a,int center,int middle,int digit,uint32_t color){
    const Glyph*g=&pad_digit_glyphs[digit],*zero=&pad_digit_glyphs[0];
    canvas_mask(a->pixels,canvas_bounds(a),nx(center)-g->w/2,nx(middle)-zero->h/2+g->y-zero->y,g->w,g->h,pad_digit_alpha+g->off,color);
}
static void pad_key(App*a,int cx,int cy,int digit){
    int key=a->keyboard_count++,action=A_KB_KEY+key;a->keyboard_keys[key]=(uint32_t)('0'+digit);
    Rect r={cx-PAD_RADIUS,cy-PAD_RADIUS,2*PAD_RADIUS,2*PAD_RADIUS};bool down=pressed(a,action);
    /* The theme's button color, solid enough to stand out from any page behind it. */
    veil(a,r,PAD_RADIUS,down?ORANGE:PANEL2,down?200:235);
    pad_digit(a,cx,cy,digit,TEXT);
    register_button(a,r,action,keyboard_enabled(a),0);
}
/* All the shadows go first: drawn with its key, each would fall across the keys before it. */
static void pad_shadows(App*a){
    if(!ui_theme->shadow_strength)return;
    for(int n=0;n<10;n++){
        int column=n<9?n%3:1,row=n<9?n/3:3;
        canvas_disc_shadow(a->pixels,canvas_bounds(a),nx(pad_columns[column]),nx(PAD_TOP+row*PAD_PITCH),(float)PAD_RADIUS*1.5f,PAD_SHADOW_SPREAD,PAD_SHADOW_DROP,ui_theme->shadow,ui_theme->shadow_strength/2);
    }
}
static void pad_side(App*a,int cx,int cy,int glyph,const char*label,int action){
    Rect r={cx-PAD_RADIUS,cy-PAD_RADIUS,2*PAD_RADIUS,2*PAD_RADIUS};if(pressed(a,action))veil(a,r,PAD_RADIUS,ORANGE,120);
    if(glyph)icon(a,glyph,cx-26,cy-(label?44:26),52,TEXT);
    if(label)centered(a,(Rect){r.x-16,cy+(glyph?14:-20),r.w+32,40},0,TEXT,label);
    register_button(a,r,action,true,0);
}
static void pad_dots(App*a,int cy){
    unsigned int typed=0,shown;for(const char*p=a->fs_prompt;*p;typed++)(void)utf8_next(&p);
    shown=typed>16?16:typed;
    int gap=shown>10?36:46,shake=0;
    if(a->now<a->lock_shake_until){float left=(float)(a->lock_shake_until-a->now)/400.f;shake=(int)(sinf(left*22.f)*16.f*left);}
    int x=CW/2-gap*(int)(shown-1)/2+shake;
    for(unsigned int n=0;n<shown;n++,x+=gap)vector_circle(a,x,cy,12,12,TEXT);
}
/* The button says what will happen: this page opens the phone, keeps a password or allows a computer. */
static const char*lock_button(const App*a){
    switch(a->fs_prompt_kind){
    case J_LOCK_ENTER:return "Unlock";
    case J_LOCK_ACCESS:return "Allow";
    case J_LOCK_SET:return "Next";
    case J_LOCK_CONFIRM:return a->lock_flow==LF_CHANGE?"Change password":"Set password";
    default:return a->lock_flow==LF_REMOVE?"Remove password":(a->lock_flow==LF_CHANGE?"Next":"Confirm");
    }
}
static void render_lock_prompt(App*a){
    int kind=a->fs_prompt_kind;
    const char*title=kind==J_LOCK_ENTER?"Enter password":kind==J_LOCK_SET?"New password":kind==J_LOCK_OLD?"Current password":kind==J_LOCK_CONFIRM?"Repeat the password":"Allow this computer";
    const char*note=kind==J_LOCK_ENTER?"Recovery is locked.":kind==J_LOCK_SET?"At least 4 characters.":kind==J_LOCK_OLD?"Needed to change the protection.":kind==J_LOCK_CONFIRM?"Type it once more.":"Enter the recovery password.";
    heading(a,"",NULL,true);centered(a,(Rect){100,92,520,70},2,TEXT,title);centered(a,(Rect){40,170,640,44},0,MUTED,note);
    pad_dots(a,340);
    char problem_note[96];copy(problem_note,sizeof(problem_note),a->lock_note);if(a->now<a->lock_wait_until)snprintf(problem_note,sizeof(problem_note),T("Too many attempts. Try again in %u s"),(unsigned int)((a->lock_wait_until-a->now+999)/1000));
    if(problem_note[0])centered(a,(Rect){28,384,664,44},0,RED,problem_note);
    bool ready=a->fs_prompt[0]!=0;
    if(a->lock_keyboard){
        render_keyboard(a);
        button(a,(Rect){28,1418,150,84},"123",A_LOCK_KEYBOARD,true,false,0);button(a,(Rect){190,1418,502,84},lock_button(a),A_FS_PROMPT_NEXT,ready,true,0);
        return;
    }
    a->keyboard_count=0;pad_shadows(a);
    for(int n=0;n<9;n++)pad_key(a,pad_columns[n%3],PAD_TOP+(n/3)*PAD_PITCH,n+1);
    int bottom=PAD_TOP+3*PAD_PITCH;pad_side(a,pad_columns[0],bottom,ICON_KEYBOARD,"Keyboard",A_LOCK_KEYBOARD);pad_key(a,pad_columns[1],bottom,0);
    if(ready)pad_side(a,pad_columns[2],bottom,ICON_ERASE,NULL,A_KB_BACKSPACE);else pad_side(a,pad_columns[2],bottom,0,"Cancel",A_BACK);
    button(a,(Rect){28,1410,664,92},lock_button(a),A_FS_PROMPT_NEXT,ready,true,0);
}
/* One field of the new account: the name as typed, the password as dots, large and centred. */
static void account_field(App*a,int y,const char*label,const char*value,bool secret,bool focused,int action){
    Rect r={28,y,664,132};
    if(focused){rounded(a,r,24,ORANGE);rounded(a,(Rect){r.x+3,r.y+3,r.w-6,r.h-6},22,PANEL);}else rounded(a,r,24,PANEL);
    text(a,52,y+16,0,focused?ORANGE:MUTED,label);
    int middle=y+84,width=0;
    if(secret&&value[0]){
        unsigned int count=0;for(const char*p=value;*p;count++)(void)utf8_next(&p);
        unsigned int shown=count>18?18:count;int gap=32;width=(int)(shown-1)*gap+20;
        for(unsigned int n=0;n<shown;n++)vector_circle(a,CW/2-width/2+10+(int)n*gap,middle,10,10,TEXT);
    }else if(value[0]){
        raw_text(true);width=text_width(value,2);centered(a,(Rect){r.x+24,y+48,r.w-48,72},2,TEXT,value);raw_text(false);
        if(width>r.w-48)width=r.w-48;
    }else if(!focused)centered(a,(Rect){r.x+24,y+60,r.w-48,48},0,MUTED,"Tap to type");
    if(focused)rectangle(a,(Rect){CW/2+width/2+(width?8:0),middle-22,3,44},ORANGE);
    register_button(a,r,action,true,0);
}
static bool systems_wifi_password_ok(const char*p);
static void render_account_prompt(App*a){
    /* Joining from the Wi-Fi page of recovery, or choosing the network of a new system. */
    bool joining=a->fs_prompt_kind==J_WIFI_SSID||a->fs_prompt_kind==J_WIFI_PASS;
    bool wifi=joining||a->fs_prompt_kind==J_SYS_WIFI_SSID||a->fs_prompt_kind==J_SYS_WIFI_PASS;
    bool naming=a->fs_prompt_kind==(joining?J_WIFI_SSID:(wifi?J_SYS_WIFI_SSID:J_SYS_ACCOUNT_USER));
    const char*kept_name=joining?a->wifi_edit_ssid:(wifi?a->systems_wifi_ssid:a->systems_username);
    const char*kept_secret=joining?a->wifi_edit_password:(wifi?a->systems_wifi_password:a->systems_password);
    const char*name=naming?a->fs_prompt:kept_name,*secret=naming?kept_secret:a->fs_prompt;
    heading(a,joining?"Join a network":(wifi?"Wi-Fi network":"New account"),
            joining?"Recovery joins it now. Nothing is saved.":(wifi?"The new system joins it at the first start.":"For the new system."),true);
    account_field(a,236,wifi?"NETWORK NAME":"USER NAME",name,false,naming,joining?A_WIFI_FIELD_SSID:(wifi?A_SYS_FIELD_SSID:A_SYS_FIELD_USER));
    account_field(a,388,"PASSWORD",secret,true,!naming,joining?A_WIFI_FIELD_PASS:(wifi?A_SYS_FIELD_WIFI_PASS:A_SYS_FIELD_PASS));
    bool ready=a->fs_prompt[0]!=0;
    if(wifi){
        bool long_name=strlen(name)>32,bad=!systems_wifi_password_ok(secret);
        ready=naming?ready&&!long_name:!bad&&kept_name[0]&&strlen(kept_name)<=32;
        wrapped(a,40,542,640,0,long_name||bad?RED:MUTED,long_name?"A network name has at most 32 bytes.":(bad?"A Wi-Fi password has 8 to 63 characters.":"Leave the password empty for an open network."),3);
    }else wrapped(a,40,542,640,0,MUTED,"The name uses lowercase letters, digits, - and _. The password signs in and confirms sudo.",3);
    render_keyboard(a);
    button(a,(Rect){28,1370,256,84},"Cancel",A_BACK,ui_free(a),false,0);
    button(a,(Rect){304,1370,388,84},naming?"Next":(joining?"Connect":"Done"),A_FS_PROMPT_NEXT,ui_free(a)&&ready,true,0);
}
static void render_backup_name_prompt(App*a){
    heading(a,"Rename the backup","Only the name changes: its files stay as they are.",true);
    account_field(a,236,"NEW NAME",a->fs_prompt,false,true,A_BACKUP_NAME_FIELD);
    bool ready=backup_name_ok(a,a->fs_prompt);
    wrapped(a,40,394,640,0,a->fs_prompt[0]&&!ready?ORANGE:MUTED,"Up to 64 Latin letters, digits, dots, hyphens and underscores; the first is a letter or a digit.",3);
    render_keyboard(a);
    button(a,(Rect){28,1370,256,84},"Cancel",A_BACK,ui_free(a),false,0);
    button(a,(Rect){304,1370,388,84},"Rename",A_FS_PROMPT_NEXT,ui_free(a)&&ready,true,0);
}
static void render_file_prompt(App*a){
    if(lock_kind(a->fs_prompt_kind)){render_lock_prompt(a);return;}
    if(a->fs_prompt_kind==J_PKG_QUERY){render_package_prompt(a);return;}
    if(a->fs_prompt_kind==J_ROOT_SUPERKEY){render_superkey_prompt(a);return;}
    if(a->fs_prompt_kind==J_BACKUP_NAME){render_backup_name_prompt(a);return;}
    if(a->fs_prompt_kind>=J_SYS_ACCOUNT_USER){render_account_prompt(a);return;}
    const char*title=a->fs_prompt_kind==J_SYS_ACCOUNT_USER?"User name":(a->fs_prompt_kind==J_SYS_ACCOUNT_PASS?"Password":(a->fs_prompt_kind==J_FS_WRITE?"New text file":files_operation_title(a->fs_prompt_kind)));
    bool metadata=a->fs_prompt_kind==J_FS_CHMOD||a->fs_prompt_kind==J_FS_CHOWN;
    bool copying=a->fs_prompt_kind==J_FS_COPY||a->fs_prompt_kind==J_FS_COPY_TREE;
    bool selected=copying||metadata||a->fs_prompt_kind==J_FS_RENAME;
    heading(a,title,metadata?(a->fs_prompt_kind==J_FS_CHMOD?"Enter the Unix mode as four octal digits.":"Enter numeric UID:GID for this entry."):(a->fs_prompt_kind>=J_SYS_ACCOUNT_USER?"For the new system.":"Enter a path relative to the volume root."),true);
    bool account=a->fs_prompt_kind>=J_SYS_ACCOUNT_USER;
    if(!account){rounded(a,(Rect){28,236,664,142},16,PANEL);
    text(a,48,254,0,ORANGE,selected?"SELECTED ENTRY":"CURRENT FOLDER");
    raw_text(true);wrapped(a,48,296,624,0,TEXT,selected?a->fs_selected.path:a->fs_path,2);raw_text(false);}
    text(a,34,410,0,ORANGE,metadata?(a->fs_prompt_kind==J_FS_CHMOD?"NEW MODE / OCTAL":"NEW OWNER / UID:GID"):(a->fs_prompt_kind>=J_SYS_ACCOUNT_USER?"VALUE":"NEW PATH"));text_viewport(a,(Rect){28,448,664,198},true,true);
    if(copying){
        FileVolume*v=a->fs_destination_index>=0&&a->fs_destination_index<a->volume_count?&a->volumes[a->fs_destination_index]:NULL;
        char label[240];snprintf(label,sizeof(label),T("To: %.170s"),v?(v->label[0]?v->label:v->id):tr("No writable volume"));
        button(a,(Rect){28,700,664,86},label,A_FS_DEST_NEXT,ui_free(a),false,0);
        text(a,34,800,0,MUTED,"Tap destination to cycle mounted writable volumes.");
    }else if(!account)wrapped(a,34,700,648,0,MUTED,metadata?(a->fs_prompt_kind==J_FS_CHMOD?"Changes only this entry. No recursive mode changes.":"Changes only this entry. The kernel may clear setuid, setgid or file capabilities when ownership changes."):"Existing names are never overwritten by this action.",4);
    cursor_buttons(a,856);render_keyboard(a);
    button(a,(Rect){28,1370,256,84},"Cancel",A_BACK,ui_free(a),false,0);
    button(a,(Rect){304,1370,388,84},a->fs_prompt_kind==J_FS_WRITE?"Write text":(a->fs_prompt_kind>=J_SYS_ACCOUNT_USER?"Next":"Review"),A_FS_PROMPT_NEXT,ui_free(a)&&a->fs_prompt[0],true,0);
}
static unsigned int path_pages(const char*s,int width,unsigned int lines){
    size_t row=0;unsigned int count=1;while(s[row]){TextRow r=text_row(s,row,width);if(r.next<=row||!s[r.next])break;count++;row=r.next;}return(count+lines-1)/lines;
}
static unsigned int confirmation_pages(const FileRequest*q){
    unsigned int pages=q->kind==J_FS_MOUNT?1:path_pages(q->path,606,7);
    if(q->kind==J_FS_RENAME||q->kind==J_FS_COPY||q->kind==J_FS_COPY_TREE){unsigned int other=path_pages(q->destination,640,6);if(other>pages)pages=other;}return pages;
}
static void confirmation_path(App*a,int x,int y,int width,const char*s,unsigned int page,unsigned int lines){
    size_t row=0;for(unsigned int n=0;n<page*lines&&s[row];n++){TextRow r=text_row(s,row,width);if(r.next<=row)break;row=r.next;}
    for(unsigned int n=0;n<lines&&s[row];n++){TextRow r=text_row(s,row,width);char part[PATH_LEN];size_t size=r.end-row;if(size>=sizeof(part))size=sizeof(part)-1;memcpy(part,s+row,size);part[size]=0;text(a,x,y+(int)n*28,0,TEXT,part);if(r.next<=row)break;row=r.next;}
}
static void render_file_confirmation(App*a){
    FileRequest*q=&a->fs_pending;heading(a,files_operation_title(q->kind),"Review the exact target before continuing.",true);
    rounded(a,(Rect){28,240,664,420},24,PANEL);text(a,52,264,0,ORANGE,"VOLUME");wrapped(a,52,306,606,1,TEXT,q->volume,2);
    text(a,52,394,0,ORANGE,q->kind==J_FS_MOUNT?"ACCESS":"PATH");confirmation_path(a,52,436,606,q->kind==J_FS_MOUNT?"Read and write":q->path,a->fs_confirm_page,7);
    if(q->kind==J_FS_RENAME||q->kind==J_FS_COPY||q->kind==J_FS_COPY_TREE){text(a,40,712,0,ORANGE,"DESTINATION");confirmation_path(a,40,754,640,q->destination,a->fs_confirm_page,6);if(q->kind==J_FS_COPY||q->kind==J_FS_COPY_TREE)ellipsis(a,40,936,640,0,MUTED,q->destination_volume);}
    if(q->kind==J_FS_CHMOD){text(a,40,712,0,ORANGE,"NEW MODE / OCTAL");text(a,40,766,2,TEXT,q->mode);}
    if(q->kind==J_FS_CHOWN){char owner[96];snprintf(owner,sizeof(owner),T("UID %s  /  GID %s"),q->uid,q->gid);text(a,40,712,0,ORANGE,"NEW OWNER");text(a,40,766,1,TEXT,owner);}
    const char*note=q->kind==J_FS_DELETE?"This deletes the selected file or empty folder. There is no undo.":
        (q->kind==J_FS_WRITE?(q->create?"Create this text file. An existing file will not be overwritten.":"Replace this file only if its contents still match the version you opened."):
        (q->kind==J_FS_MOUNT?"Write access allows changes on this volume. Nothing is formatted.":
        (q->kind==J_FS_CHMOD||q->kind==J_FS_CHOWN?"Change only this exact entry if it still matches the version you inspected. This action is not recursive.":"The destination must be a new name. Existing files will not be replaced.")));
    wrapped(a,40,1050,640,1,q->kind==J_FS_DELETE?RED:MUTED,note,4);
    unsigned int pages=confirmation_pages(q);
    if(pages>1){char label[48];snprintf(label,sizeof(label),T("Path %u / %u"),a->fs_confirm_page+1,pages);button(a,(Rect){28,1224,180,64},"Previous",A_FS_CONFIRM_PREV,a->fs_confirm_page>0,false,0);centered(a,(Rect){224,1224,272,64},0,MUTED,label);button(a,(Rect){512,1224,180,64},"Next",A_FS_CONFIRM_NEXT,a->fs_confirm_page+1<pages,false,0);}
    button(a,(Rect){28,1330,664,108},q->kind==J_FS_DELETE?"Hold 3 seconds to delete":"Hold 2 seconds to confirm",A_FS_CONFIRM,ui_free(a)&&a->fs_confirm_page+1>=pages,true,q->kind==J_FS_DELETE?3000:2000);
}
/* Long work shows one banner. Only work that can stop without damage offers Cancel. */
static bool job_long(int kind){return !terminal_job(kind)&&!job_quiet(kind)&&kind!=J_NONE&&kind!=J_INVENTORY&&kind!=J_DISPLAY_SET&&kind!=J_USB_BACKUP&&kind!=J_USB_EJECT;}
static bool job_cancellable(int kind){return kind==J_IMAGES_DOWNLOAD||kind==J_BACKUP||kind==J_VERIFY||kind==J_SYS_IMPORT||kind==J_FS_COPY||kind==J_FS_COPY_TREE||kind==J_FLASH_CHECK||kind==J_BACKUP_PLAN||kind==J_RESTORE_PLAN||kind==J_WIPE_PLAN||kind==J_SYS_IMPORT_PLAN||kind==J_SYS_SWITCH_PLAN||kind==J_INSTALL_BOOT_PLAN||kind==J_INSTALL_STOCK_PLAN||kind==J_INSTALL_RESUME_PLAN;}
static void render_job_banner(App*a){
    if(!a->job.pid||!job_long(a->job.kind))return;
    bool can=job_cancellable(a->job.kind);int h=can?(a->job_cancel_asked?300:250):190,y=1470-h;
    if(a->screen==PROGRESS){
        /* The page above shows the work itself (2026-11-11): the share and the phase were here twice. */
        if(!can||a->job_cancelled){
            Rect r={28,1360,664,76};rounded(a,r,24,PANEL);icon(a,a->job_cancelled?ICON_CLOCK:ICON_LOCK,50,r.y+20,36,MUTED);
            ellipsis(a,104,r.y+24,568,0,MUTED,a->job_cancelled?"Cancelling...":"This step cannot be stopped safely.");return;
        }
        if(a->job_cancel_asked){
            Rect r={28,1232,664,206};rounded(a,r,26,PANEL);text(a,52,r.y+26,1,ORANGE,"Stop this operation?");
            button(a,(Rect){52,r.y+98,296,84},"Keep going",A_JOB_KEEP,true,false,0);button(a,(Rect){372,r.y+98,296,84},"Stop",A_JOB_CANCEL_YES,true,true,0);return;
        }
        button(a,(Rect){28,1352,664,84},"Cancel",A_JOB_CANCEL,true,false,0);return;
    }
    /* Everything behind the banner stays visible but disabled while a job runs; under the banner
       itself the backdrop is painted again, so that the page's own button does not show through the
       glass (2026-11-11: the download button stood behind Cancel). */
    backdrop_again(a,(Rect){0,y-14,CW,h+28});
    rounded(a,(Rect){20,y,680,h},26,PANEL);
    ellipsis(a,48,y+22,624,1,TEXT,a->job_cancelled?"Cancelling...":(a->progress_phase[0]?phase_label(a,a->progress_phase):"Working"));
    Rect track={48,y+78,624,12};rounded(a,track,6,LINE);
    if(a->progress_total){uint64_t done=a->progress_done>a->progress_total?a->progress_total:a->progress_done;rounded(a,(Rect){track.x,track.y,(int)((double)done/(double)a->progress_total*track.w),track.h},6,ORANGE);
        char line[64];snprintf(line,sizeof(line),"%u%%",(unsigned int)(done*100/a->progress_total));text(a,48,y+102,0,MUTED,line);}
    else{rounded(a,(Rect){track.x+(int)((a->now/8)%500),track.y,124,track.h},6,ORANGE);text(a,48,y+102,0,MUTED,"Please wait");}
    if(!can){text(a,48,y+140,0,MUTED,"This step cannot be stopped safely.");return;}
    if(a->job_cancelled)return;
    if(a->job_cancel_asked){
        text(a,48,y+142,1,ORANGE,"Stop this operation?");
        button(a,(Rect){48,y+200,300,76},"Keep going",A_JOB_KEEP,true,false,0);
        button(a,(Rect){372,y+200,300,76},"Stop",A_JOB_CANCEL_YES,true,true,0);
    }else button(a,(Rect){48,y+150,624,76},"Cancel",A_JOB_CANCEL,true,false,0);
}
static void toast(App*a,const char*message){copy(a->toast,sizeof(a->toast),message);a->toast_icon=ICON_USB;a->toast_until=a->now+3500;a->dirty=true;}
/* A saved refresh rate is a convenience: if it does not return, say so and stay on the page. */
static void display_restore_failed(App*a){a->display_restoring=false;toast(a,"The saved refresh rate was not applied");a->toast_icon=ICON_DISPLAY;}
static void render_toast(App*a){
    if(!a->toast_until)return;
    int width=text_width(a->toast,0)+96;if(width>CW-40)width=CW-40;int x=(CW-width)/2;
    Rect r={x,1420,width,68};canvas_rounded(a->pixels,canvas_bounds(a),nx(r.x),nx(r.y),nx(r.x+r.w),nx(r.y+r.h),51.f,TOAST_FILL,TOAST_FILL,255);
    icon(a,a->toast_icon,x+22,1436,36,ORANGE);ellipsis(a,x+68,1442,width-90,0,TOAST_TEXT,a->toast);
}
/* The kernel draws this same picture from tools/generate_boot_splash.py, one per built-in theme, so it
 * stays plain and flat; /init tells the kernel and this interface the saved theme (2026-11-10), and the
 * bar carries on from where the kernel left it. */
#define SPLASH_BAR_X 180
#define SPLASH_BAR_Y 980
#define SPLASH_BAR_W 360
#define SPLASH_BAR_H 8
static void render_splash(App*a){
    icon_tile(a,A_OS,CW/2-88,600,176,ICON_FILL);centered(a,(Rect){0,812,CW,70},3,TEXT,"A22x Recovery");centered(a,(Rect){0,888,CW,44},0,MUTED,"Mainline Recovery");
    if(a->preview&&!a->splash_percent)return;
    gradient(a,(Rect){SPLASH_BAR_X,SPLASH_BAR_Y,SPLASH_BAR_W,SPLASH_BAR_H},4,PANEL2,PANEL2);
    int filled=(int)(SPLASH_BAR_W*a->splash_percent/100u);if(filled>=SPLASH_BAR_H)gradient(a,(Rect){SPLASH_BAR_X,SPLASH_BAR_Y,filled,SPLASH_BAR_H},4,ORANGE,ORANGE);
}
/* The last picture before a power action: what happens next, in the recovery's own look. */
static const char*farewell_text(int intent){
    return intent==200?"Restarting the system":(intent==201?"Turning off":(intent==202?"Restarting recovery":"Opening Samsung Download"));
}
/* A built-in theme for the boot splash and the farewell: a theme from a file falls back to Graphite,
   as /init does for the kernel's picture. */
static const Theme*splash_theme(void){
    for(unsigned int n=0;n<THEME_COUNT;n++)if(ui_theme==&themes[n])return ui_theme;
    return &themes[0];
}
static void render_farewell(App*a,int intent){
    /* The look of the boot splash, in the same theme: it closes what the splash opened. */
    const Theme*chosen=ui_theme;ui_theme=splash_theme();
    a->clip_active=false;a->button_count=0;
    rectangle(a,(Rect){0,0,CW,CH},BG);
    icon_tile(a,intent==201?A_POWER:A_OS,CW/2-88,560,176,ICON_FILL);
    centered(a,(Rect){0,772,CW,70},3,TEXT,"A22x Recovery");
    centered(a,(Rect){28,860,664,60},2,TEXT,farewell_text(intent));
    centered(a,(Rect){28,930,664,44},0,MUTED,intent==201?"The screen goes dark when the phone is off.":(intent==203?"Keep the cable connected to flash from the computer.":"Do not unplug the phone or remove storage."));
    ui_theme=chosen;
}
static bool nav_open(const App*a){
    return !a->locked&&(ui_free(a)||(terminal_screen(a)&&terminal_background_job(a->job.kind)))&&a->screen!=SPLASH&&a->screen!=PROGRESS&&a->screen!=POWER_MENU&&a->screen!=LOCKSCREEN&&a->screen!=ACCESS_REQUEST;
}
/* What going back means on this page: the arrow it shows, or the button that ends it. */
static int nav_back_action(const App*a){
    /* The sheet that asks where a file is covers the page's own Back: Back closes the sheet. */
    if(a->source_sheet)return A_BACK;
    for(int n=0;n<a->button_count;n++)if(a->buttons[n].enabled&&a->buttons[n].action==A_BACK)return A_BACK;
    for(int n=0;n<a->button_count;n++)if(a->buttons[n].enabled&&a->buttons[n].action==A_DONE)return A_DONE;
    return 0;
}
/* Follows the finger at first, then slows down and never passes the limit. */
static int nav_stretch(int distance,int limit){return distance<=0?0:(int)((float)limit*(1.f-expf(-(float)distance/(float)limit))+.5f);}
static int nav_amount(const App*a){return a->gesture==GESTURE_BACK?nav_stretch(abs(a->gesture_x-a->gesture_x0),NAV_PULL_LIMIT):(a->gesture==GESTURE_BOTTOM?nav_stretch(a->gesture_y0-a->gesture_y,NAV_LIFT_LIMIT):0);}
/* Let go, the same way back: quickly at first, slowly at the end. */
static int nav_settling(const App*a){
    if(a->now>=a->nav_settle_end)return 0;
    float left=(float)(a->nav_settle_end-a->now)/(float)NAV_SETTLE_MS;return (int)((float)a->nav_settle_amount*left*left*left+.5f);
}
static void render_nav(App*a){
    if(!nav_open(a))return;
    if(a->nav_mode==NAV_BUTTONS){
        static const int actions[3]={A_NAV_BACK,A_NAV_HOME,A_NAV_TERMINAL},columns[3]={150,360,570};bool back=nav_back_action(a)!=0;
        for(int n=0;n<3;n++){
            Rect r={columns[n]-90,1520,180,76};bool enabled=n!=0||back;uint32_t ink=enabled?TEXT:MUTED;if(pressed(a,actions[n]))veil(a,r,38,TEXT,30);
            if(n==0)icon(a,ICON_BACK,columns[n]-22,1536,44,ink);else if(n==1)vector_circle(a,columns[n],1558,17,4,ink);else icon(a,ICON_LINUX,columns[n]-22,1536,44,ink);
            register_button(a,r,actions[n],enabled,0);
        }
    }else if(a->nav_mode==NAV_GESTURES){
        int kind=a->gesture?a->gesture:a->nav_settle_kind,amount=a->gesture?nav_amount(a):nav_settling(a);
        int lift=kind==GESTURE_BOTTOM?amount:0,width=NAV_BAR_WIDTH+lift*NAV_BAR_GROW/NAV_LIFT_LIMIT;
        veil(a,(Rect){CW/2-width/2,1572-lift,width,11},6,TEXT,a->gesture_ready?230:150);
        if(a->gesture_terminal){gradient(a,(Rect){CW/2-44,1440-lift,88,88},44,ORANGE,ORANGE);icon(a,ICON_LINUX,CW/2-24,1460-lift,48,ON_ORANGE);}
        if(kind==GESTURE_BACK&&amount>0){
            /* It comes out from behind the edge. */
            bool left=a->gesture_x0<CW/2;int cx=left?amount-44:CW+44-amount;
            uint32_t fill=a->gesture_ready?ORANGE:PANEL2,ink=a->gesture_ready?ON_ORANGE:TEXT;veil(a,(Rect){cx-38,a->gesture_y-38,76,76},38,fill,235);
            if(left)icon(a,ICON_BACK,cx-20,a->gesture_y-20,40,ink);else{vector_line(a,cx-8,a->gesture_y-14,cx+6,a->gesture_y,4,ink);vector_line(a,cx+6,a->gesture_y,cx-8,a->gesture_y+14,4,ink);}
        }
    }
}
static void render(App*a){
    a->clip_active=false;a->button_count=0;a->kb_drawn=false;const Theme*chosen=ui_theme;if(a->screen==SPLASH)ui_theme=splash_theme();
    if(a->screen==SPLASH)rectangle(a,(Rect){0,0,CW,CH},BG);
    else if(a->screen==POWER_MENU&&a->menu_backdrop)memcpy(a->pixels,a->menu_backdrop,(size_t)NW*NH*sizeof(uint32_t));
    else if(a->locked&&a->lock_backdrop&&a->screen!=SPLASH)memcpy(a->pixels,a->lock_backdrop,(size_t)NW*NH*sizeof(uint32_t));
    else if(ui_theme->backdrop&&a->backdrop)memcpy(a->pixels,a->backdrop,(size_t)NW*NH*sizeof(uint32_t));else rectangle(a,(Rect){0,0,CW,CH},BG);
    switch(a->screen){
    case ANIMATIONS:render_animations(a);break;case LOCKSCREEN:render_lockscreen(a);break;case ACCESS_REQUEST:render_access(a);break;
    case HOME:render_home(a);break;case SPLASH:if(a->preview&&a->exit_code>=200&&a->exit_code<=203){render_farewell(a,a->exit_code);break;}render_splash(a);break;case FLASH:render_flash(a);break;case FLASH_REVIEW:render_flash_review(a);break;case LANGUAGE:render_language(a);break;case THEME:render_theme(a);break;case TERMINAL_THEME:render_terminal_theme(a);break;case OS_MENU:render_os(a);break;case SYSTEMS_SETUP:render_systems_setup(a);break;case BACKUP:render_backup(a);break;case LIST:render_list(a);break;case DETAIL:render_detail(a);break;case PLAN:render_plan(a);break;case PREPARE_FILESYSTEM:render_prepare(a);break;case USB_FILES:render_usb(a);break;case PROGRESS:render_progress(a);break;case RESULT:render_result(a);break;case STORAGE:render_storage(a);break;case SYSTEM:render_system(a);break;case POWER:render_power(a);break;
    case POWER_MENU:render_power_menu(a);break;
    case POWER_CONFIRM:heading(a,"Power action",NULL,true);wrapped(a,40,350,640,2,TEXT,power_confirmation(a->power_intent),4);if(terminal_session_open(a)&&a->terminal_busy)wrapped(a,40,640,640,1,RED,"The command running in the terminal will be stopped.",3);button(a,(Rect){28,1220,664,110},"Hold 2 seconds to confirm",A_POWER_CONFIRM,a->allow_power&&!a->job.pid&&a->power_intent>=200&&a->power_intent<=203&&(a->power_intent!=200||(!a->systems_reboot_blocked&&!a->install.reboot_blocked)),true,2000);break;
    case TOOLS:render_tools(a);break;case TOOLS_EXPORT:render_tools_export(a);break;case DISPLAY_SETTINGS:render_display(a);break;case WIFI:render_wifi(a);break;case SYSTEMS_NEW:render_systems_new(a);break;case SYSTEMS_IMAGES:render_images(a);break;case ADB_REQUEST:render_adb_request(a);break;case SYSTEMS_CATALOG:render_catalog(a);break;
    case TERMINAL:render_terminal(a);break;
    case SYSTEMS:render_systems(a);break;case SYSTEMS_EXTRAS:render_systems_extras(a);break;case SYSTEMS_SHELLS:render_systems_shells(a);break;case SYSTEMS_LOCALE:render_systems_locale(a);break;case SYSTEMS_TIMEZONE:render_systems_timezone(a);break;case SYSTEMS_APP_LIST:render_systems_apps(a);break;case SYSTEMS_SEARCH:render_package_search(a);break;case IDENTITY:render_identity(a);break;case APLAN:render_aplan(a);break;case FLASH_SUPER:render_flash_super(a);break;case ICON_SHEET:render_icon_sheet(a);break;case WIPE:render_wipe(a);break;case ANDROID_MENU:render_android_menu(a);break;case SAMSUNG_REVIEW:render_samsung_review(a);break;case ANDROID_IMAGE:render_android_image(a);break;case ROOT_PAGE:render_root(a);break;case ZIP_REVIEW:render_zip_review(a);break;case CARD_PAGE:render_card(a);break;case COPY_DETAIL:render_copy_detail(a);break;case WIPE_REVIEW:render_wipe_review(a);break;case ANDROID_LOG:render_android_log(a);break;case IDENTITY_RESTORE:render_identity_restore(a);break;case SYSTEMS_DONE:render_systems_done(a);break;case SYSTEMS_PLAN:render_systems_plan(a);break;case SYSTEMS_RECOVER:render_systems_recover(a);break;case SYSTEMS_REMOVE:render_systems_remove(a);break;
    case INSTALL:render_install(a);break;case INSTALL_PLAN:render_install_review(a,false);break;case INSTALL_RESULT:render_install_review(a,true);break;
    case FILE_VOLUMES:render_file_volumes(a);break;case FILE_VOLUME:render_file_volume(a);break;case FILE_BROWSER:render_file_browser(a);break;
    case FILE_INFO:render_file_info(a);break;case FILE_TEXT:render_file_text(a,false);break;case FILE_EDIT:render_file_text(a,true);break;
    case FILE_PROMPT:render_file_prompt(a);break;case FILE_CONFIRM:render_file_confirmation(a);break;
    case FILE_DISCARD:heading(a,"Discard edits?","The file on storage has not been changed.",true);wrapped(a,40,360,640,2,TEXT,"Leave without saving these edits?",4);button(a,(Rect){28,1200,664,100},"Keep editing",A_BACK,true,true,0);button(a,(Rect){28,1330,664,100},"Hold 1 second to discard",A_FS_DISCARD,true,false,1000);break;
    }
    /* Locked pages show the time and the password, not what the phone is doing. */
    if(!a->locked&&a->screen!=SPLASH&&a->screen!=POWER_MENU){footer(a);render_job_banner(a);render_nav(a);}
    key_bubble(a);
    ui_theme=chosen;
    render_toast(a);
    /* The moment a screenshot is taken: the page lights up and settles (the picture itself was composed before). */
    if(a->shot_flash&&a->now>=a->shot_flash&&a->now-a->shot_flash<SHOT_FLASH_MS)veil(a,(Rect){0,0,CW,CH},0,0xffffffu,(int)(96-96*(a->now-a->shot_flash)/SHOT_FLASH_MS));
    if(a->focus>=0&&a->focus<a->button_count){Rect r=a->buttons[a->focus].r;line(a,r.x+10,r.y+5,r.x+r.w-10,r.y+5,2,ORANGE);line(a,r.x+10,r.y+r.h-5,r.x+r.w-10,r.y+r.h-5,2,ORANGE);}
    a->dirty=false;
}
static void cancel_pointer(App*a){a->gesture=GESTURE_NONE;a->gesture_ready=a->gesture_terminal=false;a->nav_settle_end=0;stop_inertia(a,"cancel");if(a->pointer_down)a->pointer_requires_release=true;a->pressed_action=0;a->pointer_down=false;a->display_brightness_drag=false;a->scroll_touch=a->scroll_moved=a->scroll_dragging=a->scroll_stop_tap=false;a->scroll_surface=SCROLL_NONE;a->dirty=true;}
/* The page under a finger changed or was read again: what the finger pressed is gone. A navigation
 * gesture belongs to no page and goes on (2026-11-11, on the phone: Home swiped while the volumes
 * were still read was dropped when that read ended). */
static void page_changed(App*a){
    if(a->gesture&&a->pointer_down){a->pressed_action=0;a->dirty=true;return;}
    cancel_pointer(a);
}
static bool files_screen(enum Screen s){return s>=FILE_VOLUMES&&s<=FILE_DISCARD;}
/* Where the user is, whatever covers it for now: the lock, the power menu, a question from a computer. */
static enum Screen nav_page(const App*a){
    if(a->locked)return a->lock_return;
    return a->screen==POWER_MENU?a->menu_return:(a->screen==ADB_REQUEST?a->adb_return:(a->screen==ACCESS_REQUEST?a->access_return:a->screen));
}
/* Shown in passing (a progress, a result, a question, a prompt, the lock): never a place to go back to. */
static bool nav_passing(enum Screen s){
    return s==SPLASH||s==PROGRESS||s==RESULT||s==LOCKSCREEN||s==ACCESS_REQUEST||s==ADB_REQUEST||s==POWER_MENU||s==POWER_CONFIRM||
           s==FILE_PROMPT||s==FILE_CONFIRM||s==FILE_DISCARD||s==SYSTEMS_DONE||s==INSTALL_RESULT||s==ICON_SHEET;
}
/* Can be opened again as it was left: a menu, a list, a page of settings, a folder. A review, an
 * editor or a step of a setup is reached only through the pages before it. */
static bool nav_stays(enum Screen s){
    switch(s){
    case HOME:case OS_MENU:case ANDROID_MENU:case TOOLS:case BACKUP:case SYSTEMS:case WIPE:case ROOT_PAGE:case STORAGE:case SYSTEM:case POWER:
    case FLASH:case FLASH_SUPER:case SYSTEMS_NEW:case SYSTEMS_CATALOG:case SYSTEMS_IMAGES:case ANDROID_IMAGE:case APLAN:case INSTALL:case LIST:
    case USB_FILES:case TOOLS_EXPORT:case DISPLAY_SETTINGS:case WIFI:case THEME:case TERMINAL_THEME:case ANIMATIONS:case LANGUAGE:case IDENTITY:
    case FILE_VOLUMES:case FILE_VOLUME:case FILE_BROWSER:case TERMINAL:case CARD_PAGE:return true;
    default:return false;
    }
}
/* The trail of pages: Home starts it again, a page already on it ends it there, any other joins it. */
static void nav_visit(App*a,enum Screen s){
    if(s==HOME){a->nav_trail[0]=HOME;a->nav_depth=1;return;}
    if(nav_passing(s))return;
    for(int n=0;n<a->nav_depth;n++)if(a->nav_trail[n]==s){a->nav_depth=n+1;return;}
    if(a->nav_depth==NAV_TRAIL){memmove(a->nav_trail+1,a->nav_trail+2,(NAV_TRAIL-2)*sizeof(a->nav_trail[0]));a->nav_depth--;}
    a->nav_trail[a->nav_depth++]=s;
}
/* The page this one was opened from, when it can be opened again as it was left; else the usual parent. */
static enum Screen came_from(const App*a,enum Screen parent){
    if(a->nav_depth<2||a->nav_trail[a->nav_depth-1]!=a->screen)return parent;
    enum Screen from=a->nav_trail[a->nav_depth-2];return nav_stays(from)?from:parent;
}
/* The page a file was being chosen for: the last one before the pages of files. */
static enum Screen came_from_picker(const App*a,enum Screen parent){
    if(!a->nav_depth||a->nav_trail[a->nav_depth-1]!=a->screen)return parent;
    for(int n=a->nav_depth-1;n>=0;n--)if(!files_screen(a->nav_trail[n]))return nav_stays(a->nav_trail[n])?a->nav_trail[n]:parent;
    return parent;
}
/* The page under a progress or a result: the last one that was opened to stay. */
static enum Screen nav_top(const App*a){return a->nav_depth?a->nav_trail[a->nav_depth-1]:HOME;}
/* While locked no page opens but the lock pages. Whatever asks for one, a finished operation
 * included, simply gets it after the unlock: this is the one door, so nothing can go around it. */
static void screen(App*a,enum Screen s){
    /* A read that ends for a page the user has left opens nothing: the data is kept, the page is not. */
    if(a->nav_hold){a->dirty=true;return;}
    if(a->locked&&s!=LOCKSCREEN&&s!=ACCESS_REQUEST&&s!=POWER_MENU&&!(s==FILE_PROMPT&&lock_kind(a->fs_prompt_kind))){if(s!=FILE_PROMPT)a->lock_return=s;a->dirty=true;return;}
    /* A result stands for the page it returns to: what opens from it comes after that page. */
    if(a->screen==RESULT&&s!=RESULT)nav_visit(a,a->result_return);
    /* Going back, the page that is left leaves the trail, wherever its handler goes. */
    if(a->nav_backing&&s!=a->screen&&a->nav_depth>1&&a->nav_trail[a->nav_depth-1]==a->screen)a->nav_depth--;
    nav_visit(a,s);
    a->screen=s;a->focus=-1;a->button_count=0;a->dirty=true;
    /* A question, the lock or a result takes the touch away entirely. */
    if(nav_passing(s))cancel_pointer(a);else page_changed(a);
}
static void reopen(App*a,enum Screen s);
static void problem(App*a,const char*code,const char*message){
    /* The helper that refused cannot know the reason is a shell left running. */
    if(a->terminal_left&&terminal_session_open(a)&&!strcmp(code,"OPERATION_BUSY"))message="A command is running in the terminal and holds storage. Wait for it to end, or stop it in the terminal.";
    copy(a->status,sizeof(a->status),code);copy(a->message,sizeof(a->message),message);a->successful=false;a->result_return=HOME;screen(a,RESULT);}

static const char*job_name(int kind){
    if(kind==J_APLAN_SHOW)return "android-plan-show";
    if(kind==J_APLAN_PREPARE)return "android-plan-prepare";
    if(kind==J_APLAN_FORGET)return "android-plan-forget";
    if(kind==J_WIPE_CACHE)return "wipe-cache";
    if(kind==J_ANDROID_RESET)return "android-reset";
    if(kind==J_STOCK_BOOT)return "stock-boot";
    if(kind==J_SAMSUNG_UNPACK)return "samsung-unpack";
    if(kind==J_ROOT_LIST)return "root-list";
    if(kind==J_ROOT_INSTALL)return "root-install";
    if(kind==J_ROOT_PREPARE)return "root-prepare";
    if(kind==J_ZIP_INSPECT)return "zip-inspect";
    if(kind==J_ZIP_RUN)return "zip-run";
    if(kind==J_BACKUP_RENAME)return "rename-backup";
    if(kind==J_BACKUP_DELETE)return "delete-backup";
    if(kind==J_ID_DELETE)return "identity-delete";
    if(kind==J_CARD_LIST)return "card-list";
    if(kind==J_COPY_LIST)return "copies-list";
    if(kind==J_COPY_VERIFY)return "copy-verify";
    if(kind==J_COPY_RESTORE)return "copy-restore";
    if(kind==J_COPY_DELETE)return "copy-delete";
    if(kind==J_ROM_KIND)return "rom-kind";
    if(kind==J_CARD_APPLY)return "card-apply";
    if(kind==J_SAMSUNG_INSPECT)return "samsung-inspect";
    if(kind==J_SAMSUNG_INSTALL)return "samsung-install";
    if(kind==J_IMAGES)return "systems-images";
    if(kind==J_IMAGES_DOWNLOAD)return "systems-images-download";
    if(kind==J_IMAGES_DELETE)return "systems-images-delete";
    if(kind==J_CATALOG)return "systems-catalog";
    if(kind==J_CATALOG_CHECK)return "systems-catalog-check";
    if(kind==J_PKG_SEARCH)return "package-search";
    if(kind==J_ID_LIST)return "identity-list";
    if(kind==J_ID_BACKUP)return "identity-backup";
    if(kind==J_CHARGER)return "charger-mode";
    if(kind==J_ID_BACKUPS)return "identity-backups";
    if(kind==J_ID_RESTORE)return "identity-restore";
    if(wifi_job(kind))return (const char*[]){"wifi-status","wifi-scan","wifi-connect","wifi-disconnect","wifi-forget"}[kind-J_WIFI_STATUS];
    if(kind>=J_SYSINFO)return kind==J_SYSINFO?"phone-details":(kind==J_THEME_IMPORT?"theme-import":(kind==J_THEME_REMOVE?"theme-remove":"theme-restore"));
    const char*n[]={"","inventory","list","backup-plan","restore-plan","backup","verify","restore","inventory","mount","umount",
        "files-inventory","files-mount","files-unmount","files-list","files-read","files-write","files-mkdir","files-rename","files-copy","files-delete",
        "files-stat","files-chmod","files-chown","files-resolve","files-copy-tree","files-format","files-check","files-resize","diagnostic-status","diagnostic-export","display-status","display-set","wipe-plan","wipe","usb-status","usb-start","usb-stop",
        "terminal-status","terminal-start","terminal-read","terminal-write","terminal-resize","terminal-stop","systems-status","systems-import-plan","systems-switch-plan","systems-import","systems-switch","systems-recover","systems-remove",
        "install-status","install-boot-plan","install-stock-plan","install-resume-plan","install-execute","flash-targets","flash-check","flash-write","usb-sync","usb-backup","usb-eject"};
    return kind>=0&&kind<=J_USB_EJECT?n[kind]:"";
}
static bool file_job(int kind){return kind>=J_FS_INVENTORY&&kind<=J_FS_RESIZE;}
static bool private_workdir(const char*path){
    struct stat s;if(!path[0]||path[0]!='/')return false;
    if(lstat(path,&s)<0){if(errno!=ENOENT||mkdir(path,0700)<0)return false;if(lstat(path,&s)<0)return false;}
    return S_ISDIR(s.st_mode)&&s.st_uid==geteuid()&&!(s.st_mode&0022);
}
static bool launch_job(App*a,int kind,char*const*args){
    screenshot_wait(a);
    int out[2],err[2];
    if(pipe2(out,O_CLOEXEC)<0){problem(a,"PIPE_FAILED",strerror(errno));return false;}
    if(pipe2(err,O_CLOEXEC)<0){close(out[0]);close(out[1]);problem(a,"PIPE_FAILED",strerror(errno));return false;}
    pid_t pid=fork();
    if(pid<0){close(out[0]);close(out[1]);close(err[0]);close(err[1]);problem(a,"FORK_FAILED",strerror(errno));return false;}
    if(pid==0){
        if(dup2(out[1],STDOUT_FILENO)<0||dup2(err[1],STDERR_FILENO)<0)_exit(126);
        close(out[0]);close(out[1]);close(err[0]);close(err[1]);setenv("PYTHONUNBUFFERED","1",1);
        /* Display helpers also run unprompted at startup and must never hold a storage lease. */
        if(kind==J_DISPLAY_STATUS||kind==J_DISPLAY_SET){(void)close_range(3,~0u,0);unsetenv("A22X_RECOVERY_LOCK_FD");}
        execv(a->python,args);
        const char failure[]="{\"ok\":false,\"error\":{\"code\":\"BACKEND_EXEC_FAILED\",\"message\":\"Could not start the configured Python backend\"}}\n";
        ssize_t ignored=write(STDOUT_FILENO,failure,sizeof(failure)-1);(void)ignored;_exit(127);
    }
    close(out[1]);close(err[1]);fcntl(out[0],F_SETFL,fcntl(out[0],F_GETFL)|O_NONBLOCK);fcntl(err[0],F_SETFL,fcntl(err[0],F_GETFL)|O_NONBLOCK);
    a->job=(Job){.pid=pid,.fdout=out[0],.fderr=err[0],.kind=kind,.output=calloc(JSON_LIMIT+1,1)};
    if(!a->job.output)a->job.overflow=true;
    a->job_started=milliseconds();a->progress_done=a->progress_total=0;a->progress_mark_phase[0]=0;copy(a->progress_phase,sizeof(a->progress_phase),"Preparing");a->progress_part[0]=0;copy(a->last_backend_operation,sizeof(a->last_backend_operation),job_name(kind));
    a->result_return=HOME;
    /* Its page is the one the action that started it ends on; a job started by another job's end keeps that job's page. */
    if(!a->job_completing){a->job_orphan=false;a->job_page_mark=true;}
    if(kind==J_FS_LIST){a->pressed_action=0;a->button_count=0;a->focus=-1;}
    else if(!terminal_background_job(kind))stop_inertia(a,"operation");
    if(!terminal_job(kind)&&kind!=J_INSTALL_STATUS&&kind!=J_SYS_STATUS&&kind!=J_INVENTORY&&kind!=J_STORAGE_INVENTORY&&kind!=J_FS_INVENTORY&&kind!=J_FS_LIST&&kind!=J_TOOLS_STATUS&&kind!=J_DISPLAY_STATUS&&kind!=J_DISPLAY_SET&&kind!=J_USB_STATUS&&kind!=J_FLASH_TARGETS&&kind!=J_USB_SYNC&&kind!=J_USB_BACKUP&&kind!=J_USB_EJECT&&kind!=J_SYSINFO&&!wifi_job(kind)&&kind!=J_CATALOG&&kind!=J_CATALOG_CHECK&&kind!=J_PKG_SEARCH&&kind!=J_ID_LIST&&kind!=J_ID_BACKUPS&&kind!=J_CHARGER&&!images_job(kind)&&kind!=J_APLAN_SHOW&&kind!=J_APLAN_PREPARE&&kind!=J_APLAN_FORGET&&kind!=J_ROOT_LIST&&kind!=J_CARD_LIST&&kind!=J_COPY_LIST&&kind!=J_ROM_KIND)screen(a,PROGRESS);
    else if(kind==J_INVENTORY&&a->screen!=RESULT)copy(a->status,sizeof(a->status),"Reading device inventory...");
    if(!terminal_background_job(kind))a->dirty=true;
    return true;
}
static bool start_job(App*a,int kind){
    if(a->job.pid||a->preview)return false;
    if((kind==J_RESTORE||kind==J_WIPE)&&a->device&&(!a->plan_linux_v2||!a->plan_linux_admitted)){
        problem(a,kind==J_WIPE?"DEVICE_WIPE_NOT_QUALIFIED":"DEVICE_RESTORE_NOT_QUALIFIED","Only a freshly reviewed qualified Linux used-block plan can write these targets.");return false;
    }
    bool storage_job=kind>=J_STORAGE_INVENTORY&&kind<=J_UMOUNT;
    if(storage_job&&(!a->device||!a->storage_helper[0]))return false;
    char name[128];char*args[32];int n=0;args[n++]=a->python;args[n++]=storage_job?a->storage_helper:a->backend;
    if(!storage_job){if(a->device)args[n++]="--device";else{args[n++]="--fixture-root";args[n++]=a->fixture;}}
    args[n++]=(char*)job_name(kind);
    if(kind==J_LIST){args[n++]="--repository";args[n++]=a->repositories[a->store];}
    else if(kind==J_BACKUP_PLAN){
        if(!private_workdir(a->workdir)){problem(a,"WORKDIR_UNAVAILABLE","A private runtime directory is required for reviewed plans.");return false;}
        struct timespec ts;clock_gettime(CLOCK_REALTIME,&ts);struct tm tm;gmtime_r(&ts.tv_sec,&tm);char date[40];strftime(date,sizeof(date),"%Y%m%d-%H%M%S",&tm);
        snprintf(name,sizeof(name),"%s-%s-%03ld",a->profile,date,ts.tv_nsec/1000000L);
        if(snprintf(a->plan_path,sizeof(a->plan_path),"%s/plan-%ld-%u.json",a->workdir,(long)getpid(),++a->plan_sequence)>=(int)sizeof(a->plan_path)){problem(a,"PATH_TOO_LONG","Runtime plan path is too long.");return false;}
        args[n++]="--profile";args[n++]=a->profile;args[n++]="--repository";args[n++]=a->repositories[a->store];args[n++]="--name";args[n++]=name;args[n++]="--output";args[n++]=a->plan_path;
        if(!strcmp(a->profile,"linux")&&(a->backup_compact||a->device)){args[n++]="--format";args[n++]="partclone-ext4";}
    }else if(kind==J_RESTORE_PLAN||kind==J_WIPE_PLAN){
        if(!private_workdir(a->workdir)){problem(a,"WORKDIR_UNAVAILABLE","A private runtime directory is required for reviewed plans.");return false;}
        if(snprintf(a->plan_path,sizeof(a->plan_path),"%s/plan-%ld-%u.json",a->workdir,(long)getpid(),++a->plan_sequence)>=(int)sizeof(a->plan_path)){problem(a,"PATH_TOO_LONG","Runtime plan path is too long.");return false;}
        args[n++]="--backup";args[n++]=a->backups[a->selected_backup].path;args[n++]="--output";args[n++]=a->plan_path;
    }else if(kind==J_VERIFY){args[n++]="--backup";args[n++]=a->backups[a->selected_backup].path;}
    else if(kind==J_BACKUP_RENAME){args[n++]="--backup";args[n++]=a->backups[a->selected_backup].path;args[n++]="--name";args[n++]=a->backup_new_name;}
    /* A backup is deleted only under its own name: the backend compares it with the folder it loads. */
    else if(kind==J_BACKUP_DELETE){args[n++]="--backup";args[n++]=a->backups[a->selected_backup].path;args[n++]="--confirm";args[n++]=a->backups[a->selected_backup].name;}
    else if(kind==J_BACKUP||kind==J_RESTORE||kind==J_WIPE){
        int expected=kind==J_BACKUP?J_BACKUP_PLAN:(kind==J_WIPE?J_WIPE_PLAN:J_RESTORE_PLAN);
        if(!a->plan_can_execute||a->plan_kind!=expected||!a->token[0]||!a->plan_path[0]){problem(a,"PLAN_NOT_ADMITTED","No current executable plan is available for this operation.");return false;}
        args[n++]="--plan";args[n++]=a->plan_path;args[n++]="--confirm";args[n++]=a->token;
    }
    args[n]=NULL;return launch_job(a,kind,args);
}
/* A backup folder exists only once its medium is mounted: lead to Storage instead of failing on the path. */
static bool destination_ready(App*a){
    if(a->preview||!a->device||!access(a->repositories[a->store],F_OK))return true;
    screen(a,STORAGE);toast(a,"Choose storage first");a->toast_icon=A_STORAGE;return false;
}
static void files_problem(App*a,const char*code,const char*message,enum Screen back){problem(a,code,message);a->result_return=back;}
static bool start_tools_job(App*a,int kind){
    if(a->job.pid||a->preview||(kind!=J_TOOLS_STATUS&&kind!=J_TOOLS_EXPORT))return false;
    if(!a->management_helper[0]){files_problem(a,"TOOLS_UNAVAILABLE","The management helper is not configured.",TOOLS);return false;}
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->management_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_TOOLS_STATUS?"status":"export";
    if(kind==J_TOOLS_EXPORT){
        FileVolume*v=a->tools_volume_index>=0&&a->tools_volume_index<a->volume_count?&a->volumes[a->tools_volume_index]:NULL;
        if(!v||!v->mounted||v->read_only||!v->write_supported||!v->token[0]||strcmp(v->id,a->tools_volume_id)||strcmp(v->token,a->tools_volume_token)){
            files_problem(a,"DESTINATION_CHANGED","Refresh the destination list and review a mounted writable volume.",TOOLS);return false;
        }
        args[n++]="--volume";args[n++]=a->tools_volume_id;args[n++]="--expected-volume-token";args[n++]=a->tools_volume_token;args[n++]="--confirm";
    }
    args[n]=NULL;bool ok=launch_job(a,kind,args);a->result_return=TOOLS;return ok;
}
static bool start_display_job(App*a,int kind){
    if(a->job.pid||a->preview||(kind!=J_DISPLAY_STATUS&&kind!=J_DISPLAY_SET)||(kind==J_DISPLAY_SET&&a->display_control.pid))return false;
    if(!a->display_helper[0]){files_problem(a,"DISPLAY_UNAVAILABLE","The display helper is not configured.",DISPLAY_SETTINGS);return false;}
    char hz[16];char*args[12];int n=0;args[n++]=a->python;args[n++]=a->display_helper;
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_DISPLAY_STATUS?"status":"set";
    if(kind==J_DISPLAY_SET){
        bool allowed=a->display_known&&a->display_supported&&a->display_service&&((a->display_requested_hz==60&&a->display_60)||(a->display_requested_hz==90&&a->display_90));
        if(!allowed){a->display_requested_hz=0;files_problem(a,"DISPLAY_MODE_UNAVAILABLE","Refresh display status and choose a reported available mode.",DISPLAY_SETTINGS);return false;}
        snprintf(hz,sizeof(hz),"%u",a->display_requested_hz);args[n++]="--hz";args[n++]=hz;
    }
    args[n]=NULL;bool ok=launch_job(a,kind,args);a->result_return=DISPLAY_SETTINGS;return ok;
}
static bool start_usb_job(App*a,int kind){
    if(a->job.pid||a->preview||kind<J_USB_STATUS||kind>J_USB_STOP)return false;
    if(!a->usb_helper[0]){files_problem(a,"USB_FILES_UNAVAILABLE","The USB file transfer helper is not configured.",USB_FILES);return false;}
    char*args[24];int n=0;args[n++]=a->python;args[n++]=a->usb_helper;args[n++]="--mode";args[n++]=a->device?"device":"fixture";
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_USB_STATUS?"status":(kind==J_USB_START?"start":"stop");
    if(kind==J_USB_START){
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard edited text before sharing this storage with the computer.",FILE_EDIT);return false;}
        if(!a->usb_known||!a->usb_available||strcmp(a->usb_state,"stopped")||a->usb_session[0]||a->usb_selected<0||a->usb_selected>=a->usb_candidate_count)return false;
        UsbCandidate*c=&a->usb_candidates[a->usb_selected];if(!c->id[0]||!c->volume_token[0]||!c->mount_token[0])return false;
        a->usb_request_ro=a->usb_read_only||!c->writable;copy(a->usb_request_id,sizeof(a->usb_request_id),c->id);copy(a->usb_request_volume_token,sizeof(a->usb_request_volume_token),c->volume_token);copy(a->usb_request_mount_token,sizeof(a->usb_request_mount_token),c->mount_token);
        args[n++]="--volume";args[n++]=a->usb_request_id;args[n++]="--expected-volume-token";args[n++]=a->usb_request_volume_token;args[n++]="--expected-mount-token";args[n++]=a->usb_request_mount_token;
        if(a->usb_request_ro)args[n++]="--read-only";
        a->usb_expected=1;
    }else if(kind==J_USB_STOP){if(!a->usb_known||!usb_session_open(a))return false;args[n++]="--expected-session-token";args[n++]=a->usb_session;a->usb_expected=2;}
    args[n]=NULL;bool launched=launch_job(a,kind,args);a->result_return=USB_FILES;return launched;
}
static FileRequest file_request(App*a,int kind){
    FileRequest q={.kind=kind};FileVolume*v=current_volume(a);
    if(v){copy(q.volume,sizeof(q.volume),v->id);copy(q.volume_token,sizeof(q.volume_token),v->token);}
    copy(q.path,sizeof(q.path),kind==J_FS_READ?a->fs_file:(kind==J_FS_STAT||kind==J_FS_RESOLVE?a->fs_selected.path:a->fs_path));q.offset=a->file_offset;
    if(kind==J_FS_STAT||kind==J_FS_RESOLVE)copy(q.entry_token,sizeof(q.entry_token),a->fs_selected.token);
    return q;
}
static const char*filesystem_verb(int kind){
    const char*verbs[]={"inventory","mount","unmount","list","read","write","mkdir","rename","copy","delete","stat","chmod","chown","resolve","copy-tree","format","check","resize"};
    return file_job(kind)?verbs[kind-J_FS_INVENTORY]:"";
}
static bool write_edit_source(App*a,FileRequest*q){
    if(a->fs_truncated||a->fs_length>TEXT_LIMIT||strlen(a->fs_text)!=a->fs_length){files_problem(a,"TEXT_NOT_COMPLETE","The complete file must be loaded before saving.",FILE_EDIT);return false;}
    if(!private_workdir(a->workdir)){files_problem(a,"WORKDIR_UNAVAILABLE","The private RAM work directory is unavailable.",FILE_EDIT);return false;}
    if(snprintf(q->text_file,sizeof(q->text_file),"%s/text-%ld-%u.txt",a->workdir,(long)getpid(),++a->plan_sequence)>=(int)sizeof(q->text_file))return false;
    int fd=open(q->text_file,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0){files_problem(a,"TEXT_STAGE_FAILED",strerror(errno),FILE_EDIT);return false;}
    size_t done=0;bool ok=true;
    while(done<a->fs_length){ssize_t n=write(fd,a->fs_text+done,a->fs_length-done);if(n<0&&errno==EINTR)continue;if(n<=0){ok=false;break;}done+=(size_t)n;}
    if(fsync(fd)<0)ok=false;
    if(close(fd)<0)ok=false;
    if(!ok){unlink(q->text_file);q->text_file[0]=0;files_problem(a,"TEXT_STAGE_FAILED","Could not prepare the complete edited text in RAM.",FILE_EDIT);}
    return ok;
}
static bool start_files_request(App*a,const FileRequest*request){
    if(a->job.pid||a->preview)return false;
    if(!a->filesystem_helper[0]){files_problem(a,"FILES_UNAVAILABLE","The filesystem helper is not configured.",FILE_VOLUMES);return false;}
    FileRequest q=*request;
    if(!file_job(q.kind))return false;
    if(q.kind!=J_FS_INVENTORY&&(!q.volume[0]||!q.volume_token[0])){files_problem(a,"VOLUME_IDENTITY_MISSING","Refresh the volume list before continuing.",FILE_VOLUMES);return false;}
    if((q.kind==J_FS_CHMOD||q.kind==J_FS_CHOWN||q.kind==J_FS_RESOLVE||q.kind==J_FS_COPY_TREE)&&!q.entry_token[0]){files_problem(a,"ENTRY_IDENTITY_MISSING","Inspect the selected entry again before continuing.",FILE_INFO);return false;}
    if(q.kind==J_FS_WRITE&&!write_edit_source(a,&q))return false;
    a->fs_request=q;
    char offset[32],limit[16];snprintf(offset,sizeof(offset),"%u",q.offset);snprintf(limit,sizeof(limit),"%d",FILE_BATCH);char*args[40];int n=0;
    args[n++]=a->python;args[n++]=a->filesystem_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=(char*)filesystem_verb(q.kind);
    if(q.kind!=J_FS_INVENTORY){args[n++]="--volume";args[n++]=q.volume;args[n++]="--expected-volume-token";args[n++]=q.volume_token;}
    if(q.kind==J_FS_MOUNT){args[n++]="--mode";args[n++]=q.mode;if(!strcmp(q.mode,"rw"))args[n++]="--confirm";}
    else if(q.kind==J_FS_FORMAT){args[n++]="--fs";args[n++]=q.mode;args[n++]="--confirm";}
    else if(q.kind==J_FS_CHECK||q.kind==J_FS_RESIZE)args[n++]="--confirm";
    else if(q.kind>=J_FS_LIST){
        args[n++]="--path";args[n++]=q.path;
        if(q.kind==J_FS_LIST){args[n++]="--offset";args[n++]=offset;args[n++]="--limit";args[n++]=limit;}
        else if(q.kind==J_FS_READ){args[n++]="--max-bytes";args[n++]="65536";}
        else if(q.kind==J_FS_WRITE){args[n++]="--text-file";args[n++]=q.text_file;if(q.create)args[n++]="--create";else{args[n++]="--expected-sha256";args[n++]=q.sha256;}args[n++]="--confirm";}
        else{
            if(q.kind==J_FS_RENAME||q.kind==J_FS_COPY||q.kind==J_FS_COPY_TREE){args[n++]="--destination";args[n++]=q.destination;}
            if(q.kind==J_FS_COPY||q.kind==J_FS_COPY_TREE){args[n++]="--destination-volume";args[n++]=q.destination_volume;args[n++]="--expected-destination-volume-token";args[n++]=q.destination_token;}
            if(q.kind==J_FS_CHMOD){args[n++]="--mode";args[n++]=q.mode;}
            if(q.kind==J_FS_CHOWN){if(q.uid[0]){args[n++]="--uid";args[n++]=q.uid;}if(q.gid[0]){args[n++]="--gid";args[n++]=q.gid;}}
            if(q.entry_token[0]&&q.kind!=J_FS_STAT){args[n++]="--expected-entry-token";args[n++]=q.entry_token;}
            if(q.kind!=J_FS_STAT&&q.kind!=J_FS_RESOLVE)args[n++]="--confirm";
        }
    }
    args[n]=NULL;
    bool launched=launch_job(a,q.kind,args);
    if(!launched&&q.text_file[0]){unlink(q.text_file);a->fs_request.text_file[0]=0;}
    return launched;
}
static bool start_files_job(App*a,int kind){FileRequest q=file_request(a,kind);return start_files_request(a,&q);}
static bool begin_linux_prepare(App*a,int kind){
    if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard the edited text before preparing an offline operation.",FILE_EDIT);return false;}
    if(a->usb_known&&usb_session_open(a)){files_problem(a,"USB_EXPORT_ACTIVE","Stop USB file sharing before preparing an offline backup, wipe or restore.",USB_FILES);return false;}
    if(a->job.pid)return false;
    a->plan_can_execute=a->plan_linux_admitted=false;a->token[0]=0;
    a->prepare_job=kind;a->prepare_known=a->prepare_mounted=a->prepare_owned=a->prepare_unmounting=false;
    a->prepare_token[0]=a->prepare_mountpoint[0]=a->prepare_reason[0]=0;a->fs_inventory_destination=PREPARE_FILESYSTEM;
    screen(a,PREPARE_FILESYSTEM);return start_files_job(a,J_FS_INVENTORY);
}
static void prepare_inventory(App*a){
    a->prepare_known=false;a->prepare_mounted=a->prepare_owned=false;a->prepare_token[0]=a->prepare_mountpoint[0]=a->prepare_reason[0]=0;
    for(int n=0;n<a->volume_count;n++){FileVolume*v=&a->volumes[n];if(strcmp(v->id,"userdata"))continue;
        a->prepare_known=true;a->prepare_mounted=v->mounted;a->prepare_owned=!strcmp(v->mount_owner,"filesystem");
        copy(a->prepare_token,sizeof(a->prepare_token),v->token);copy(a->prepare_mountpoint,sizeof(a->prepare_mountpoint),v->mountpoint);
        if(v->mounted&&!a->prepare_owned)copy(a->prepare_reason,sizeof(a->prepare_reason),"This mount is not owned by the recovery Files helper. Close its owner and unmount it before continuing.");
        break;
    }
    if(!a->prepare_known)copy(a->prepare_reason,sizeof(a->prepare_reason),"Userdata was not present in the fresh volume inventory. Refresh before continuing.");
    screen(a,PREPARE_FILESYSTEM);
}
static bool browser_maybe_load(App*a){
    if(a->screen!=FILE_BROWSER||a->job.pid||a->preview||!a->file_total)return false;
    unsigned int first=(unsigned int)(a->browser_scroll/FILE_ROW_HEIGHT);
    unsigned int last=(unsigned int)((a->browser_scroll+FILE_LIST_BOTTOM-FILE_LIST_TOP+FILE_ROW_HEIGHT-1)/FILE_ROW_HEIGHT);
    if(last>a->file_total)last=a->file_total;
    uint64_t end=(uint64_t)a->file_offset+(unsigned int)a->file_count;
    bool near_edge=first<a->file_offset||last>end||(a->file_offset&&first<a->file_offset+12u)||(end<a->file_total&&(uint64_t)last+12u>end);
    unsigned int wanted=first>16u?((first-16u)/FILE_WINDOW_STEP)*FILE_WINDOW_STEP:0;
    if(!near_edge||(wanted==a->file_offset&&a->file_count))return false;
    FileRequest q=file_request(a,J_FS_LIST);q.offset=wanted;return start_files_request(a,&q);
}
static void browser_scroll_to(App*a,int64_t offset){
    a->browser_scroll=offset;browser_clamp(a);a->focus=-1;a->dirty=true;browser_maybe_load(a);
}
static enum ScrollSurface scroll_candidate(const App*a,int x,int y){
    if(a->screen==FILE_BROWSER&&inside(browser_viewport(),x,y)&&(!a->job.pid||a->job.kind==J_FS_LIST))return SCROLL_FOLDER;
    if(a->screen==TERMINAL&&terminal_session_open(a)&&inside(terminal_viewport(),x,y)&&(!a->job.pid||terminal_background_job(a->job.kind)))return SCROLL_TERMINAL;
    if(a->job.pid)return SCROLL_NONE;
    if(a->screen==FILE_VOLUMES&&inside(volume_viewport(),x,y))return SCROLL_VOLUMES;
    if(a->screen==SYSTEMS&&!a->systems_transaction[0]&&inside(systems_viewport(),x,y))return SCROLL_SYSTEMS;
    if(a->screen==TOOLS&&inside(tools_viewport(),x,y))return SCROLL_TOOLS;
    if(page_scrolls(a)&&inside(page_viewport(a),x,y))return SCROLL_PAGE;
    if((a->screen==INSTALL_PLAN||a->screen==INSTALL_RESULT)&&inside(install_review_viewport(),x,y))return SCROLL_INSTALL_REVIEW;
    if(a->screen==INSTALL&&a->install.pending_count&&inside(install_pending_viewport(),x,y))return SCROLL_INSTALL_PENDING;
    if((a->screen==FILE_TEXT||a->screen==FILE_EDIT||(a->screen==FILE_PROMPT&&!lock_prompting(a)))&&inside(text_box(a),x,y))return a->screen==FILE_PROMPT?SCROLL_PROMPT:SCROLL_TEXT;
    return SCROLL_NONE;
}
static int64_t scroll_offset(const App*a,enum ScrollSurface kind){
    if(kind==SCROLL_TOOLS)return a->tools_scroll;
    if(kind==SCROLL_PAGE)return a->page_scroll;
    if(kind==SCROLL_INSTALL_REVIEW)return a->install.review_scroll;
    if(kind==SCROLL_INSTALL_PENDING)return a->install.pending_scroll;
    return kind==SCROLL_SYSTEMS?a->systems_scroll:(kind==SCROLL_FOLDER?a->browser_scroll:(kind==SCROLL_VOLUMES?a->volume_scroll:(kind==SCROLL_TERMINAL?a->terminal_scroll:(int64_t)(kind==SCROLL_PROMPT?a->prompt_scroll:a->fs_scroll))));
}
static int64_t scroll_limit(App*a,enum ScrollSurface kind){
    if(kind==SCROLL_TOOLS)return tools_max_scroll(a);
    if(kind==SCROLL_PAGE)return page_max_scroll(a);
    if(kind==SCROLL_INSTALL_REVIEW)return install_review_max_scroll(a);
    if(kind==SCROLL_INSTALL_PENDING)return install_pending_max_scroll(a);
    return kind==SCROLL_SYSTEMS?systems_max_scroll(a):(kind==SCROLL_FOLDER?browser_max_scroll(a):(kind==SCROLL_VOLUMES?volume_max_scroll(a):(kind==SCROLL_TERMINAL?terminal_max_scroll(a):text_max_scroll(a,kind==SCROLL_PROMPT,text_box(a)))));
}
static void apply_scroll(App*a,enum ScrollSurface kind,int64_t value){
    if(kind==SCROLL_FOLDER)browser_scroll_to(a,value);
    else if(kind==SCROLL_VOLUMES)volume_scroll_to(a,value);
    else if(kind==SCROLL_TERMINAL)terminal_scroll_to(a,value);
    else if(kind==SCROLL_SYSTEMS)systems_scroll_to(a,value);
    else if(kind==SCROLL_TOOLS)tools_scroll_to(a,value);
    else if(kind==SCROLL_PAGE)page_scroll_to(a,value);
    else if(kind==SCROLL_INSTALL_REVIEW||kind==SCROLL_INSTALL_PENDING)install_scroll_to(a,kind==SCROLL_INSTALL_PENDING,value);
    else text_scroll_to(a,kind==SCROLL_PROMPT,value);
}
static void begin_inertia(App*a,enum ScrollSurface kind,uint64_t now){
    double velocity=scroll_release_velocity(&a->scroll_samples,now);
    if(!kinetic_begin(&a->kinetic,(double)scroll_offset(a,kind),velocity,(double)scroll_limit(a,kind),now))return;
    a->motion_surface=kind;a->motion_frames=0;a->motion_render_total=a->motion_render_max=0;a->dirty=true;
    fprintf(stderr,"[recovery-ui:kinetic-start] surface=%s offset_px=%" PRId64 " velocity_px_s=%.1f\n",scroll_surface_name(kind),scroll_offset(a,kind),velocity);
}
static void tick_inertia(App*a,uint64_t now){
    if(!a->kinetic.active)return;
    a->now=now;
    if(a->pointer_down||(a->job.pid&&a->job.kind!=J_FS_LIST&&!terminal_background_job(a->job.kind))){stop_inertia(a,"interrupted");return;}
    enum ScrollSurface kind=a->motion_surface;
    bool correct=(kind==SCROLL_FOLDER&&a->screen==FILE_BROWSER)||(kind==SCROLL_VOLUMES&&a->screen==FILE_VOLUMES)||(kind==SCROLL_TEXT&&(a->screen==FILE_TEXT||a->screen==FILE_EDIT))||(kind==SCROLL_PROMPT&&a->screen==FILE_PROMPT)||(kind==SCROLL_TERMINAL&&a->screen==TERMINAL)||(kind==SCROLL_SYSTEMS&&a->screen==SYSTEMS)||(kind==SCROLL_TOOLS&&a->screen==TOOLS)||(kind==SCROLL_PAGE&&page_scrolls(a))||(kind==SCROLL_INSTALL_REVIEW&&(a->screen==INSTALL_PLAN||a->screen==INSTALL_RESULT))||(kind==SCROLL_INSTALL_PENDING&&a->screen==INSTALL);
    if(!correct){stop_inertia(a,"screen-change");return;}
    if(kinetic_advance(&a->kinetic,now,(double)scroll_limit(a,kind)))apply_scroll(a,kind,(int64_t)(a->kinetic.position+.5));
    if(!a->kinetic.active)stop_inertia(a,"settled");
}
static void jstring(const Json*j,int o,const char*key,char*out,size_t n){if(json_string(j,json_get(j,o,key),out,n)<0)out[0]=0;}
static bool jequals(const Json*j,int o,const char*key,const char*expected){char value[160];jstring(j,o,key,value,sizeof(value));return !strcmp(value,expected);}
static void append(char*dst,size_t n,const char*s){size_t have=strlen(dst);if(have<n-1)snprintf(dst+have,n-have,"%s",s);}
static void parse_progress(App*a,const char*line_text){
    JsonToken t[128];Json j;if(json_parse(&j,line_text,t,128)<1||t[0].type!=J_OBJECT)return;char event[64];jstring(&j,0,"event",event,sizeof(event));if(strcmp(event,"progress"))return;
    a->progress_events++;
    {char phase[128]={0};jstring(&j,0,"phase",phase,sizeof(phase));if(strcmp(phase,a->progress_mark_phase)){copy(a->progress_mark_phase,sizeof(a->progress_mark_phase),phase);a->progress_mark_at=milliseconds();a->progress_mark_done=json_uint(&j,json_get(&j,0,"bytes_done"),0);}}
    jstring(&j,0,"phase",a->progress_phase,sizeof(a->progress_phase));jstring(&j,0,"partition",a->progress_part,sizeof(a->progress_part));a->progress_done=json_uint(&j,json_get(&j,0,"bytes_done"),0);a->progress_total=json_uint(&j,json_get(&j,0,"bytes_total"),0);a->dirty=true;
}
static void parse_list(App*a,const Json*j){
    a->backup_count=0;a->list_page=0;int array=json_get(j,0,"backups");if(array<0||j->tokens[array].type!=J_ARRAY)return;
    for(int i=array+1;i<j->count&&j->tokens[i].start<j->tokens[array].end&&a->backup_count<MAX_BACKUPS;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT)continue;
        BackupEntry*e=&a->backups[a->backup_count];memset(e,0,sizeof(*e));jstring(j,i,"path",e->path,sizeof(e->path));if(!e->path[0])jstring(j,i,"backup_path",e->path,sizeof(e->path));
        jstring(j,i,"name",e->name,sizeof(e->name));if(!e->name[0]){const char*s=strrchr(e->path,'/');copy(e->name,sizeof(e->name),s?s+1:e->path);}
        jstring(j,i,"profile",e->profile,sizeof(e->profile));jstring(j,i,"created_at",e->created,sizeof(e->created));if(!e->created[0])jstring(j,i,"created_utc",e->created,sizeof(e->created));e->bytes=json_uint(j,json_get(j,i,"raw_bytes"),0);
        jstring(j,i,"schema",e->schema,sizeof(e->schema));jstring(j,i,"workflow",e->workflow,sizeof(e->workflow));
        if(e->path[0])a->backup_count++;
    }
}
static void parse_inventory(App*a,const Json*j){
    int device=json_get(j,0,"device"),caps=json_get(j,0,"capabilities");
    jstring(j,device,"model",a->model,sizeof(a->model));jstring(j,device,"identity",a->identity,sizeof(a->identity));
    a->restore_qualified=json_bool(j,json_get(j,caps,"restore"),0)&&!a->device;
    bool linux=jequals(j,caps,"linux_workflow","linux-partclone-v2");
    a->linux_backup_available=linux&&json_bool(j,json_get(j,caps,"linux_used_block_backup"),0);
    a->linux_restore_available=linux&&json_bool(j,json_get(j,caps,"linux_restore"),0);
    a->linux_wipe_available=linux&&json_bool(j,json_get(j,caps,"linux_wipe"),0);
    a->partition_count=0;int parts=json_get(j,0,"partitions");
    if(parts>=0&&j->tokens[parts].type==J_ARRAY)for(int i=parts+1;i<j->count&&j->tokens[i].start<j->tokens[parts].end&&a->partition_count<4;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT)continue;
        PartitionEntry*p=&a->partitions[a->partition_count++];memset(p,0,sizeof(*p));jstring(j,i,"label",p->label,sizeof(p->label));jstring(j,i,"path",p->path,sizeof(p->path));p->bytes=json_uint(j,json_get(j,i,"size_bytes"),0);p->available=json_bool(j,json_get(j,i,"available"),0);p->mounted=json_bool(j,json_get(j,i,"mounted"),0);
    }
}
static void parse_storage(App*a,const Json*j){
    a->storage_known=true;a->storage_mounted=json_bool(j,json_get(j,0,"mounted"),0);
    jstring(j,0,"mountpoint",a->storage_mountpoint,sizeof(a->storage_mountpoint));
    char filesystem[32],bytes[64];jstring(j,0,"filesystem",filesystem,sizeof(filesystem));human_bytes(json_uint(j,json_get(j,0,"free_bytes"),0),bytes,sizeof(bytes));
    copy(a->repository_fs[0],sizeof(a->repository_fs[0]),a->storage_mounted?filesystem:"");if(a->storage_mounted)snprintf(a->storage_message,sizeof(a->storage_message),T("%.120s / %.24s / %s free"),a->storage_mountpoint,filesystem,bytes);
    else copy(a->storage_message,sizeof(a->storage_message),"Insert an ext4 microSD, then tap Mount microSD.");
}
static bool safe_token(const char*s){if(!*s||strlen(s)>180)return false;for(;*s;s++)if(!isalnum((unsigned char)*s)&&*s!='-'&&*s!='_')return false;return true;}
static bool linux_plan_contract(App*a,const Json*j,int kind,int summary){
    bool destructive=kind==J_RESTORE_PLAN||kind==J_WIPE_PLAN;
    if(!jequals(j,0,"schema","a22x.plan.v2")||!jequals(j,0,"operation",job_name(kind))||
       !jequals(j,summary,"workflow","linux-partclone-v2")||!jequals(j,summary,"backup_schema","a22x.backup.v2")||
       !jequals(j,summary,"data_format","partclone-ext4-v2+zstd")||!jequals(j,summary,"profile","linux")||
       json_bool(j,json_get(j,summary,"destructive"),-1)!=(int)destructive||
       json_uint(j,json_get(j,summary,"required_hold_ms"),UINT64_MAX)!=(destructive?3000u:0u))return false;
    int targets=json_get(j,summary,"write_targets");if(targets<0||j->tokens[targets].type!=J_ARRAY)return false;
    if(!destructive)return jequals(j,summary,"boot_action","read-only-backup")&&targets+1==json_next(j,targets);
    int qualification=json_get(j,summary,"qualification");
    if(!json_bool(j,json_get(j,qualification,"eligible"),0))return false;
    int blocks=json_get(j,qualification,"blockers");if(blocks<0||j->tokens[blocks].type!=J_ARRAY||blocks+1!=json_next(j,blocks))return false;
    if(a->device){
        char capsule[80];jstring(j,qualification,"capsule_id",capsule,sizeof(capsule));if(strlen(capsule)!=64||!jequals(j,qualification,"status","qualified"))return false;
        for(const char*p=capsule;*p;p++)if(!isxdigit((unsigned char)*p))return false;
        if(!a->inventory_ok||(kind==J_WIPE_PLAN?!a->linux_wipe_available:!a->linux_restore_available))return false;
    }else if(!jequals(j,qualification,"status","fixture-only"))return false;
    int count=0;
    for(int i=targets+1;i<j->count&&j->tokens[i].start<j->tokens[targets].end;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT||count>=2||(kind==J_WIPE_PLAN&&count))return false;
        const char*label=count==0?"userdata":"boot";const char*act=count==0?(kind==J_WIPE_PLAN?"format-ext4":"restore-used-blocks"):"restore-raw-last";
        char path[PATH_LEN];jstring(j,i,"path",path,sizeof(path));uint64_t size=json_uint(j,json_get(j,i,"size_bytes"),0);
        if(!jequals(j,i,"label",label)||!jequals(j,i,"action",act)||path[0]!='/'||!size)return false;
        if(a->device){bool match=false;for(int n=0;n<a->partition_count;n++){PartitionEntry*p=&a->partitions[n];if(!strcmp(p->label,label)&&p->available&&!strcmp(p->path,path)&&p->bytes==size)match=true;}if(!match)return false;}
        count++;
    }
    if(kind==J_WIPE_PLAN)return count==1&&jequals(j,summary,"boot_action","preserve");
    return (count==1&&jequals(j,summary,"boot_action","skip-identical"))||(count==2&&jequals(j,summary,"boot_action","restore-last"));
}
static void parse_plan(App*a,const Json*j,int kind){
    char returned_path[PATH_LEN];jstring(j,0,"plan_path",returned_path,sizeof(returned_path));jstring(j,0,"plan_id",a->plan_id,sizeof(a->plan_id));jstring(j,0,"confirmation_token",a->token,sizeof(a->token));
    a->plan_kind=kind;a->plan_restore=kind==J_RESTORE_PLAN;a->plan_wipe=kind==J_WIPE_PLAN;
    a->plan_linux_admitted=a->plan_linux_v2=a->plan_space_known=a->plan_estimate_known=false;a->plan_boot_action[0]=a->plan_qualification[0]=0;
    a->plan_used_bytes=a->plan_archive_bytes=a->plan_free_bytes=a->plan_reserve_bytes=0;
    a->plan_can_execute=json_bool(j,json_get(j,0,"can_execute"),0)&&safe_token(a->token)&&returned_path[0]&&!strcmp(returned_path,a->plan_path);
    a->plan_parts[0]=a->plan_targets[0]=a->blockers[0]=a->plan_backup[0]=0;int summary=json_get(j,0,"summary");
    if(summary>=0){jstring(j,summary,"profile",a->profile,sizeof(a->profile));jstring(j,summary,"backup_path",a->plan_backup,sizeof(a->plan_backup));a->plan_bytes=json_uint(j,json_get(j,summary,"raw_bytes"),0);int parts=json_get(j,summary,"partitions");
        a->plan_linux_v2=jequals(j,0,"schema","a22x.plan.v2")||jequals(j,summary,"workflow","linux-partclone-v2");
        int space=json_get(j,summary,"space");a->plan_space_known=space>=0&&j->tokens[space].type==J_OBJECT;
        a->plan_used_bytes=json_uint(j,json_get(j,space,"userdata_used_bytes"),0);a->plan_archive_bytes=json_uint(j,json_get(j,space,"estimated_archive_bytes"),UINT64_MAX);
        a->plan_estimate_known=a->plan_archive_bytes!=UINT64_MAX;if(!a->plan_estimate_known)a->plan_archive_bytes=0;
        a->plan_free_bytes=json_uint(j,json_get(j,space,"free_bytes"),0);a->plan_reserve_bytes=json_uint(j,json_get(j,space,"reserve_bytes"),0);
        jstring(j,summary,"boot_action",a->plan_boot_action,sizeof(a->plan_boot_action));jstring(j,json_get(j,summary,"qualification"),"status",a->plan_qualification,sizeof(a->plan_qualification));
        if(parts>=0&&j->tokens[parts].type==J_ARRAY)for(int i=parts+1;i<j->count&&j->tokens[i].start<j->tokens[parts].end;i=json_next(j,i)){char p[80];if(json_string(j,i,p,sizeof(p))>=0){if(a->plan_parts[0])append(a->plan_parts,sizeof(a->plan_parts),"  +  ");append(a->plan_parts,sizeof(a->plan_parts),p);}}
        int targets=json_get(j,summary,"write_targets");
        if(targets>=0&&j->tokens[targets].type==J_ARRAY)for(int i=targets+1;i<j->count&&j->tokens[i].start<j->tokens[targets].end;i=json_next(j,i)){
            char label[64],path[PATH_LEN],act[64];jstring(j,i,"label",label,sizeof(label));jstring(j,i,"path",path,sizeof(path));jstring(j,i,"action",act,sizeof(act));
            if(!strcmp(act,"format-ext4"))copy(act,sizeof(act),tr("Create empty ext4 filesystem"));else if(!strcmp(act,"restore-used-blocks"))copy(act,sizeof(act),tr("Restore filesystem blocks"));else if(!strcmp(act,"restore-raw-last"))copy(act,sizeof(act),tr("Restore BOOT last"));
            if(label[0]&&path[0]){if(a->plan_targets[0])append(a->plan_targets,sizeof(a->plan_targets),"\n");append(a->plan_targets,sizeof(a->plan_targets),label);append(a->plan_targets,sizeof(a->plan_targets),"  ");append(a->plan_targets,sizeof(a->plan_targets),path);if(act[0]){append(a->plan_targets,sizeof(a->plan_targets),"\n");append(a->plan_targets,sizeof(a->plan_targets),act);}}
        }
    }
    if(!a->plan_backup[0])copy(a->plan_backup,sizeof(a->plan_backup),a->plan_restore||a->plan_wipe?a->backups[a->selected_backup].path:a->repositories[a->store]);
    int blocks=json_get(j,0,"blockers");if(blocks>=0&&j->tokens[blocks].type==J_ARRAY)for(int i=blocks+1;i<j->count&&j->tokens[i].start<j->tokens[blocks].end;i=json_next(j,i)){char b[256];if(json_string(j,i,b,sizeof(b))>=0){if(a->blockers[0])append(a->blockers,sizeof(a->blockers),"\n");append(a->blockers,sizeof(a->blockers),b);}}
    if(strcmp(returned_path,a->plan_path))append(a->blockers,sizeof(a->blockers),"\nPLAN_PATH_MISMATCH");
    if(a->blockers[0])a->plan_can_execute=false;
    bool expected_v2=a->plan_wipe||(kind==J_BACKUP_PLAN&&!strcmp(a->profile,"linux")&&(a->backup_compact||a->device))||(a->plan_restore&&backup_is_linux_v2(&a->backups[a->selected_backup]));
    if(a->plan_linux_v2||expected_v2){
        a->plan_linux_v2=true;a->plan_linux_admitted=linux_plan_contract(a,j,kind,summary);
        if(!a->plan_linux_admitted){a->plan_can_execute=false;append(a->blockers,sizeof(a->blockers),"\nLINUX_PLAN_NOT_QUALIFIED_OR_INVALID");}
    }
    if((a->plan_restore||a->plan_wipe)&&a->device&&!a->plan_linux_admitted){a->plan_can_execute=false;append(a->blockers,sizeof(a->blockers),a->plan_wipe?"\nDEVICE_WIPE_NOT_QUALIFIED":"\nDEVICE_RESTORE_NOT_QUALIFIED");}
    if((a->plan_restore||a->plan_wipe)&&!a->plan_targets[0]){a->plan_can_execute=false;append(a->blockers,sizeof(a->blockers),"\nMISSING_WRITE_TARGETS");}
    a->prepare_job=J_NONE;a->prepare_unmounting=false;a->successful=true;
    screen(a,PLAN);
}
static int volume_order(const void*left,const void*right){
    const FileVolume*a=left,*b=right;
    int aa=a->mounted?0:(a->mountable?1:2),bb=b->mounted?0:(b->mountable?1:2);
    if(aa!=bb)return aa-bb;
    bool au=!strcmp(a->id,"userdata"),bu=!strcmp(b->id,"userdata");if(au!=bu)return au?-1:1;
    return strcmp(a->label[0]?a->label:a->id,b->label[0]?b->label:b->id);
}
static void parse_file_volumes(App*a,const Json*j){
    a->volume_count=0;a->volume_index=-1;int array=json_get(j,0,"volumes");
    int capabilities=json_get(j,0,"capabilities");a->fs_copy_directories=json_bool(j,json_get(j,capabilities,"copy_directories"),0);
    if(array>=0&&j->tokens[array].type==J_ARRAY){
        for(int i=array+1;i<j->count&&j->tokens[i].start<j->tokens[array].end&&a->volume_count<MAX_VOLUMES;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT)continue;
            FileVolume*v=&a->volumes[a->volume_count];memset(v,0,sizeof(*v));
            jstring(j,i,"id",v->id,sizeof(v->id));if(!v->id[0])continue;
            jstring(j,i,"label",v->label,sizeof(v->label));jstring(j,i,"fs_type",v->fs_type,sizeof(v->fs_type));
            jstring(j,i,"mountpoint",v->mountpoint,sizeof(v->mountpoint));jstring(j,i,"reason",v->reason,sizeof(v->reason));
            jstring(j,i,"read_only_reason",v->read_only_reason,sizeof(v->read_only_reason));
            jstring(j,i,"volume_token",v->token,sizeof(v->token));
            jstring(j,i,"mount_owner",v->mount_owner,sizeof(v->mount_owner));
            v->mounted=json_bool(j,json_get(j,i,"mounted"),0);v->read_only=json_bool(j,json_get(j,i,"read_only"),1);v->mountable=json_bool(j,json_get(j,i,"mountable"),0);
            v->write_supported=json_bool(j,json_get(j,i,"write_supported"),0);
            v->bytes=json_uint(j,json_get(j,i,"size_bytes"),json_uint(j,json_get(j,i,"size"),0));
            if(!strcmp(v->id,a->volume_id))a->volume_index=a->volume_count;
            a->volume_count++;
        }
    }
    qsort(a->volumes,(size_t)a->volume_count,sizeof(a->volumes[0]),volume_order);
    a->volume_index=-1;for(int i=0;i<a->volume_count;i++)if(!strcmp(a->volumes[i].id,a->volume_id))a->volume_index=i;
    a->volume_scroll=(int64_t)scroll_clamp((double)a->volume_scroll,(double)volume_max_scroll(a));
    a->button_count=0;a->focus=-1;page_changed(a);
}
static void tools_select_volume(App*a,int index){
    a->tools_volume_index=index;a->tools_volume_id[0]=a->tools_volume_token[0]=0;
    if(index>=0&&index<a->volume_count){copy(a->tools_volume_id,sizeof(a->tools_volume_id),a->volumes[index].id);copy(a->tools_volume_token,sizeof(a->tools_volume_token),a->volumes[index].token);}
}
static bool parse_file_entry(const Json*j,int object,FileEntry*e){
    memset(e,0,sizeof(*e));if(object<0||j->tokens[object].type!=J_OBJECT)return false;
    jstring(j,object,"name",e->name,sizeof(e->name));jstring(j,object,"path",e->path,sizeof(e->path));jstring(j,object,"type",e->type,sizeof(e->type));
    e->size=json_uint(j,json_get(j,object,"size"),json_uint(j,json_get(j,object,"size_bytes"),0));
    e->uid=json_uint(j,json_get(j,object,"uid"),0);e->gid=json_uint(j,json_get(j,object,"gid"),0);e->nlink=json_uint(j,json_get(j,object,"nlink"),0);
    uint64_t mode=json_uint(j,json_get(j,object,"mode"),UINT64_MAX);if(mode<=07777)snprintf(e->mode,sizeof(e->mode),"%04o",(unsigned int)mode);
    jstring(j,object,"entry_token",e->token,sizeof(e->token));jstring(j,object,"link_target",e->link_target,sizeof(e->link_target));
    e->metadata_supported=json_bool(j,json_get(j,object,"metadata_supported"),0);jstring(j,object,"metadata_reason",e->metadata_reason,sizeof(e->metadata_reason));
    return e->path[0]&&e->type[0];
}
static bool parse_file_list(App*a,const Json*j){
    int array=json_get(j,0,"entries");if(array<0||j->tokens[array].type!=J_ARRAY)return false;
    char path[PATH_LEN];jstring(j,0,"path",path,sizeof(path));if(!path[0]||strcmp(path,a->fs_request.path))return false;
    uint64_t offset=json_uint(j,json_get(j,0,"offset"),UINT64_MAX),total=json_uint(j,json_get(j,0,"total"),UINT64_MAX);
    if(offset!=a->fs_request.offset||total>UINT32_MAX)return false;
    a->file_count=0;a->file_offset=(unsigned int)offset;a->file_total=(unsigned int)total;a->files_more=json_bool(j,json_get(j,0,"has_more"),0);
    FileVolume*v=current_volume(a);if(v)v->read_only=json_bool(j,json_get(j,0,"read_only"),1);
    for(int i=array+1;i<j->count&&j->tokens[i].start<j->tokens[array].end;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT||a->file_count>=FILE_BATCH)return false;
        FileEntry*e=&a->files[a->file_count];if(!parse_file_entry(j,i,e)||!e->name[0])return false;
        a->file_count++;
    }
    uint64_t expected=offset<total?total-offset:0;if(expected>FILE_BATCH)expected=FILE_BATCH;
    if((uint64_t)a->file_count!=expected)return false;
    copy(a->fs_path,sizeof(a->fs_path),path);browser_clamp(a);a->pressed_action=0;a->button_count=0;a->focus=-1;
    return true;
}
static bool valid_sha256(const char*s){if(strlen(s)!=64)return false;for(;*s;s++)if(!isxdigit((unsigned char)*s))return false;return true;}
static bool parse_file_text(App*a,const Json*j){
    int n=json_string(j,json_get(j,0,"content"),a->fs_text,sizeof(a->fs_text));
    if(n<0)return false;
    a->fs_length=(size_t)n;a->fs_truncated=json_bool(j,json_get(j,0,"truncated"),1);
    jstring(j,0,"sha256",a->fs_sha256,sizeof(a->fs_sha256));if(!a->fs_truncated&&!valid_sha256(a->fs_sha256))return false;
    char path[PATH_LEN];jstring(j,0,"path",path,sizeof(path));if(!path[0])return false;copy(a->fs_file,sizeof(a->fs_file),path);
    a->fs_cursor=a->fs_scroll=0;a->fs_dirty=false;a->fs_new_file=false;
    FileVolume*v=current_volume(a);if(v)v->read_only=json_bool(j,json_get(j,0,"read_only"),1);
    return true;
}
static enum Screen file_error_return(int kind){
    if(kind==J_FS_WRITE)return FILE_EDIT;
    if(kind==J_FS_INVENTORY)return FILE_VOLUMES;
    if(kind==J_FS_MOUNT||kind==J_FS_UNMOUNT||kind==J_FS_FORMAT||kind==J_FS_CHECK||kind==J_FS_RESIZE)return FILE_VOLUME;
    if(kind==J_FS_LIST)return FILE_BROWSER;
    if(kind==J_FS_READ)return FILE_BROWSER;
    if(kind==J_FS_MKDIR||kind==J_FS_RENAME||kind==J_FS_COPY||kind==J_FS_COPY_TREE||kind==J_FS_CHMOD||kind==J_FS_CHOWN)return FILE_PROMPT;
    return FILE_INFO;
}
static int complete_files_job(App*a,const Json*j,int kind){
    a->successful=true;
    if(kind==J_FS_INVENTORY){
        parse_file_volumes(a,j);
        if(a->source_sheet){a->source_known=true;a->dirty=true;return J_NONE;}
        if(a->fs_inventory_destination==PREPARE_FILESYSTEM&&a->prepare_job!=J_NONE){prepare_inventory(a);return J_NONE;}
        if(a->fs_inventory_destination==USB_FILES){a->usb_volumes_known=true;a->dirty=true;screen(a,USB_FILES);return J_NONE;}
        if(a->fs_inventory_destination==IDENTITY){identity_inventory(a);return J_NONE;}
        if(a->fs_inventory_destination==TOOLS_EXPORT){
            int chosen=-1;for(int n=0;n<a->volume_count;n++){FileVolume*v=&a->volumes[n];if(!v->mounted||v->read_only||!v->write_supported||!v->token[0])continue;
                if(chosen<0)chosen=n;
                if(!strncmp(v->id,"sdcard",6)){chosen=n;break;}}
            tools_select_volume(a,chosen);screen(a,TOOLS_EXPORT);return J_NONE;
        }
        if(a->fs_inventory_destination==FILE_BROWSER&&current_volume(a)&&current_volume(a)->mounted)return J_FS_LIST;
        screen(a,current_volume(a)&&a->fs_inventory_destination==FILE_VOLUME?FILE_VOLUME:FILE_VOLUMES);
    }else if(kind==J_FS_LIST){
        if(!parse_file_list(a,j)){files_problem(a,"INVALID_FOLDER_RESULT","The folder result was incomplete. Refresh before continuing.",FILE_VOLUME);return J_NONE;}
        if(a->screen!=FILE_BROWSER)screen(a,FILE_BROWSER);else a->dirty=true;
    }else if(kind==J_FS_READ){
        if(!parse_file_text(a,j)){files_problem(a,"INVALID_TEXT_RESULT","The complete UTF-8 text and file hash were not returned. Editing is disabled.",FILE_BROWSER);return J_NONE;}
        screen(a,FILE_TEXT);
    }else if(kind==J_FS_STAT||kind==J_FS_CHMOD||kind==J_FS_CHOWN){
        FileEntry entry;if(!parse_file_entry(j,json_get(j,0,"entry"),&entry)||strcmp(entry.path,a->fs_request.path)){
            files_problem(a,"INVALID_ENTRY_RESULT","The selected entry was not returned. Inspect it again before continuing.",FILE_BROWSER);return J_NONE;}
        a->fs_selected=entry;a->fs_link_resolved=false;
        if(kind==J_FS_STAT){FileVolume*v=current_volume(a);if(v){v->read_only=json_bool(j,json_get(j,0,"read_only"),1);v->write_supported=json_bool(j,json_get(j,0,"write_supported"),0);}}
        if(kind==J_FS_CHMOD||kind==J_FS_CHOWN)copy(a->fs_status,sizeof(a->fs_status),kind==J_FS_CHMOD?"Permissions updated":"Owner updated; actual permissions shown");
        screen(a,FILE_INFO);
    }else if(kind==J_FS_RESOLVE){
        char resolved[PATH_LEN];jstring(j,0,"resolved_path",resolved,sizeof(resolved));
        if(!parse_file_entry(j,json_get(j,0,"entry"),&a->fs_resolved)||strcmp(resolved,a->fs_resolved.path)){
            files_problem(a,"INVALID_LINK_RESULT","The link target was not returned. No target was opened.",FILE_INFO);return J_NONE;}
        a->fs_link_resolved=true;screen(a,FILE_INFO);
    }else if(kind==J_FS_FORMAT){
        /* 2026-11-08: without the refresh the progress page stayed after a finished format. */
        copy(a->fs_status,sizeof(a->fs_status),"Formatted");a->fs_inventory_destination=FILE_VOLUME;reset_browser(a);return J_FS_INVENTORY;
    }else if(kind==J_FS_CHECK){
        char result[16];jstring(j,0,"result",result,sizeof(result));
        copy(a->fs_status,sizeof(a->fs_status),!strcmp(result,"repaired")?"Check: errors repaired":"Check: no errors found");
        a->fs_inventory_destination=FILE_VOLUME;reset_browser(a);return J_FS_INVENTORY;
    }else if(kind==J_FS_RESIZE){
        char result[16],size[32];jstring(j,0,"result",result,sizeof(result));human_bytes(json_uint(j,json_get(j,0,"bytes_after"),0),size,sizeof(size));
        snprintf(a->fs_status,sizeof(a->fs_status),!strcmp(result,"grown")?T("Grown to %s"):T("Already fills the partition: %s"),size);
        a->fs_inventory_destination=FILE_VOLUME;reset_browser(a);return J_FS_INVENTORY;
    }else if(kind==J_FS_MOUNT&&a->id_mounting){
        /* Back on the page of the copy: the volumes are read again and the card is chosen. */
        a->id_mounting=false;a->fs_inventory_destination=IDENTITY;screen(a,IDENTITY);return J_FS_INVENTORY;
    }else if(kind==J_FS_MOUNT&&a->copy_mounting){
        a->copy_mounting=false;a->copies_known=false;a->copy_list_after=true;screen(a,LIST);return J_NONE;
    }else if(kind==J_FS_MOUNT&&a->systems_mounting){
        a->systems_mounting=false;a->systems_known=false;a->systems_reason[0]=0;a->systems_refresh_after=true;
        screen(a,systems_mount_screen((int)a->systems_mount_return)?a->systems_mount_return:SYSTEMS);return J_NONE;
    }else if(kind==J_FS_MOUNT||kind==J_FS_UNMOUNT){
        copy(a->fs_status,sizeof(a->fs_status),kind==J_FS_UNMOUNT?"Volume unmounted":(!strcmp(a->fs_request.mode,"rw")?"Write access enabled":"Mounted read-only"));
        a->fs_inventory_destination=a->usb_mounting?USB_FILES:(kind==J_FS_UNMOUNT&&a->prepare_unmounting?PREPARE_FILESYSTEM:(kind==J_FS_UNMOUNT?FILE_VOLUME:FILE_BROWSER));
        if(a->usb_mounting){a->usb_mounting=false;a->usb_refresh_after=true;}
        reset_browser(a);return J_FS_INVENTORY;
    }else if(kind==J_FS_WRITE){
        copy(a->fs_status,sizeof(a->fs_status),"Text saved. Reading the stored file.");a->fs_dirty=false;a->fs_new_file=false;return J_FS_READ;
    }else{
        copy(a->fs_status,sizeof(a->fs_status),kind==J_FS_COPY_TREE?"Folder tree copied":(kind==J_FS_COPY?"File copied":(kind==J_FS_RENAME?"Renamed":(kind==J_FS_MKDIR?"Folder created":"Deleted"))));
        reset_browser(a);return J_FS_LIST;
    }
    return J_NONE;
}
static bool complete_tools_job(App*a,const Json*j,int kind){
    if(kind==J_TOOLS_STATUS){
        int ssh=json_get(j,0,"ssh"),logs=json_get(j,0,"logs");if(ssh<0||logs<0||j->tokens[ssh].type!=J_OBJECT||j->tokens[logs].type!=J_OBJECT)return false;
        a->ssh_available=json_bool(j,json_get(j,ssh,"available"),0);a->ssh_running=json_bool(j,json_get(j,ssh,"running"),0);
        a->ssh_ephemeral=json_bool(j,json_get(j,ssh,"host_key_ephemeral"),0);a->ssh_keys=(unsigned int)json_uint(j,json_get(j,ssh,"authorized_keys_count"),0);
        a->ssh_port=(unsigned int)json_uint(j,json_get(j,ssh,"port"),22);jstring(j,ssh,"address",a->ssh_address,sizeof(a->ssh_address));
        jstring(j,ssh,"host_fingerprint",a->ssh_fingerprint,sizeof(a->ssh_fingerprint));jstring(j,ssh,"reason",a->ssh_reason,sizeof(a->ssh_reason));
        a->logs_export_available=json_bool(j,json_get(j,logs,"export_available"),0);jstring(j,logs,"reason",a->logs_reason,sizeof(a->logs_reason));
        a->tools_known=true;a->successful=true;
        if(a->tools_export_next){a->tools_export_next=false;if(a->logs_export_available){a->fs_inventory_destination=TOOLS_EXPORT;screen(a,TOOLS_EXPORT);tools_select_volume(a,-1);start_files_job(a,J_FS_INVENTORY);}else files_problem(a,"EXPORT_UNAVAILABLE",a->logs_reason[0]?a->logs_reason:"Diagnostics cannot be exported now.",HOME);return true;}
        screen(a,TOOLS);return true;
    }
    char path[PATH_LEN],sha[80],bytes[64];jstring(j,0,"archive_path",path,sizeof(path));jstring(j,0,"sha256",sha,sizeof(sha));
    if(!path[0]||!valid_sha256(sha))return false;
    human_bytes(json_uint(j,json_get(j,0,"bytes"),0),bytes,sizeof(bytes));
    a->successful=true;a->result_return=HOME;copy(a->status,sizeof(a->status),"Diagnostics exported");
    snprintf(a->message,sizeof(a->message),T("Archive: %s\nSize: %s\nSHA-256: %s"),path,bytes,sha);screen(a,RESULT);return true;
}
static double json_display_timing(const Json*j,int token){
    if(token<0||j->tokens[token].type!=J_PRIMITIVE)return 0;
    int length=j->tokens[token].end-j->tokens[token].start;if(length<1||length>63)return 0;
    char value[64],*end;memcpy(value,j->text+j->tokens[token].start,(size_t)length);value[length]=0;
    errno=0;double timing=strtod(value,&end);return errno||*end||timing<=0||timing>1000?0:timing;
}
static bool json_is_null(const Json*j,int token){return token>=0&&j->tokens[token].type==J_PRIMITIVE&&j->tokens[token].end-j->tokens[token].start==4&&!memcmp(j->text+j->tokens[token].start,"null",4);}
static bool parse_display_light(App*a,const Json*j){
    int brightness=json_bool(j,json_get(j,0,"brightness_supported"),-1),blank_support=json_bool(j,json_get(j,0,"blank_supported"),-1);
    int blank_token=json_get(j,0,"blanked"),blank=json_bool(j,blank_token,-1);
    if(brightness<0||blank_support<0||(blank<0&&!json_is_null(j,blank_token))||(blank_support&&blank<0))return false;
    uint64_t percent=json_uint(j,json_get(j,0,"brightness_percent"),UINT64_MAX),maximum=json_uint(j,json_get(j,0,"brightness_max"),UINT64_MAX);
    uint64_t raw=json_uint(j,json_get(j,0,"brightness_raw"),UINT64_MAX),requested=json_uint(j,json_get(j,0,"brightness_requested_raw"),UINT64_MAX);
    int actual_token=json_get(j,0,"brightness_actual_raw");uint64_t actual=json_uint(j,actual_token,UINT64_MAX);
    uint64_t last=json_uint(j,json_get(j,0,"last_nonzero_percent"),UINT64_MAX);
    if(brightness&&(blank<0||maximum==0||maximum>UINT32_MAX||raw>maximum||requested>maximum||percent>100||last<1||last>100||(blank?percent!=0:percent==0)||(actual==UINT64_MAX?!json_is_null(j,actual_token):actual>maximum)))return false;
    bool changed=blank>=0&&(!a->display_blank_known||a->display_blanked!=(bool)blank);
    a->display_light_known=true;a->display_brightness_supported=brightness;a->display_blank_supported=blank_support;
    a->display_blank_known=blank>=0;if(blank>=0)a->display_blanked=blank;
    a->display_percent=brightness?(unsigned int)percent:0;a->display_last_percent=brightness?(unsigned int)last:0;
    a->display_max=brightness?maximum:0;a->display_raw=brightness?raw:0;a->display_requested_raw=brightness?requested:0;
    a->display_actual_raw_known=brightness&&actual!=UINT64_MAX;a->display_actual_raw=a->display_actual_raw_known?actual:0;
    if(changed)cancel_pointer(a);
    a->dirty=true;return true;
}
static bool parse_display_status(App*a,const Json*j){
    int modes=json_get(j,0,"available_hz");if(modes<0||j->tokens[modes].type!=J_ARRAY)return false;
    /* Old refresh-only fixtures remain usable. Live light controls require the
     * full explicit capability/readback contract, independently of DRM modes. */
    if(!a->display_control.pid&&json_get(j,0,"brightness_supported")>=0&&!parse_display_light(a,j))return false;
    a->display_60=a->display_90=false;
    for(int i=modes+1;i<j->count&&j->tokens[i].start<j->tokens[modes].end;i=json_next(j,i)){
        uint64_t hz=json_uint(j,i,0);if(hz==60)a->display_60=true;if(hz==90)a->display_90=true;
    }
    a->display_supported=json_bool(j,json_get(j,0,"supported"),0);a->display_service=json_bool(j,json_get(j,0,"service_running"),0);
    uint64_t current=json_uint(j,json_get(j,0,"current_hz"),0);a->display_hz=current==60||current==90?(unsigned int)current:0;
    a->display_actual_hz=json_display_timing(j,json_get(j,0,"actual_hz"));jstring(j,0,"reason",a->display_reason,sizeof(a->display_reason));
    a->display_known=true;return true;
}
static const char*display_command_name(enum DisplayCommand command){
    switch(command){case DC_STATUS:return "status";case DC_BRIGHTNESS:return "brightness";case DC_BLANK:return "blank";case DC_WAKE:return "wake";case DC_TOGGLE:return "toggle";default:return "unknown";}
}
static bool start_display_control(App*a,enum DisplayCommand command,unsigned int percent){
    if(a->display_control.pid||a->preview||command<DC_STATUS||command>DC_TOGGLE||!a->display_helper[0]||(command==DC_BRIGHTNESS&&(percent<1||percent>100)))return false;
    char amount[8];char*args[12];int n=0;args[n++]=a->python;args[n++]=a->display_helper;
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=(char*)display_command_name(command);
    if(command==DC_BRIGHTNESS){snprintf(amount,sizeof(amount),"%u",percent);args[n++]="--percent";args[n++]=amount;}
    args[n]=NULL;int out[2],err[2];
    if(pipe2(out,O_CLOEXEC)<0){copy(a->display_light_note,sizeof(a->display_light_note),"Could not start display control.");return false;}
    if(pipe2(err,O_CLOEXEC)<0){close(out[0]);close(out[1]);copy(a->display_light_note,sizeof(a->display_light_note),"Could not start display control.");return false;}
    pid_t pid=fork();
    if(pid<0){close(out[0]);close(out[1]);close(err[0]);close(err[1]);copy(a->display_light_note,sizeof(a->display_light_note),"Could not start display control.");return false;}
    if(pid==0){
        if(dup2(out[1],STDOUT_FILENO)<0||dup2(err[1],STDERR_FILENO)<0)_exit(126);
        close(out[0]);close(out[1]);close(err[0]);close(err[1]);
        /* Display never inherits a storage-operation lease or a live input FD. */
        if(close_range(3,~0u,0)<0)_exit(126);
        unsetenv("A22X_RECOVERY_LOCK_FD");setenv("PYTHONUNBUFFERED","1",1);execv(a->python,args);_exit(127);
    }
    close(out[1]);close(err[1]);fcntl(out[0],F_SETFL,fcntl(out[0],F_GETFL)|O_NONBLOCK);fcntl(err[0],F_SETFL,fcntl(err[0],F_GETFL)|O_NONBLOCK);
    a->display_control=(DisplayControl){.pid=pid,.fdout=out[0],.fderr=err[0],.command=command,.deadline=milliseconds()+DISPLAY_CONTROL_TIMEOUT};
    if(command!=DC_STATUS){
        a->display_pending_command=command;a->display_requested_percent=percent;a->display_request_accepted=false;
        a->display_expected_raw=0;
        /* A brightness check must not interrupt the finger still on the slider. */
        if(command!=DC_BRIGHTNESS){a->display_input_blocked=true;cancel_pointer(a);copy(a->display_light_note,sizeof(a->display_light_note),"Applying display control and reading the result...");}
    }
    a->dirty=true;return true;
}
static void complete_display_control(App*a){
    DisplayControl*c=&a->display_control;enum DisplayCommand command=c->command;int status=c->status;
    JsonToken tokens[512];Json doc;bool parsed=!c->overflow&&!c->timed_out&&json_parse(&doc,c->output,tokens,512)>0&&tokens[0].type==J_OBJECT;
    bool ok=parsed&&WIFEXITED(status)&&WEXITSTATUS(status)==0&&json_bool(&doc,json_get(&doc,0,"ok"),-1)==1&&jequals(&doc,0,"operation",display_command_name(command));
    c->pid=0;
    bool observed=ok&&parse_display_light(a,&doc)&&parse_display_status(a,&doc);
    fprintf(stderr,"[recovery-ui:display-control] operation=%s exit=%d observed=%d timeout=%d blanked=%d percent=%u\n",display_command_name(command),WIFEXITED(status)?WEXITSTATUS(status):-1,observed,c->timed_out,a->display_blank_known?(int)a->display_blanked:-1,a->display_percent);
    if(command!=DC_STATUS){
        a->display_request_accepted=observed&&a->display_blank_known;
        if(a->display_request_accepted){a->display_expected_blank=a->display_blanked;a->display_expected_raw=a->display_requested_raw;}
        a->display_uncertain=!a->display_request_accepted;
        /* A lost reply may follow a completed change. Only status is automatic. */
        if(!start_display_control(a,DC_STATUS,0)){a->display_uncertain=true;a->display_input_blocked=true;copy(a->display_light_note,sizeof(a->display_light_note),"Display outcome unknown. Press Power to request wake.");}
        return;
    }
    enum DisplayCommand pending=a->display_pending_command;
    if(observed){
        bool verified=a->display_request_accepted&&a->display_blank_known&&a->display_blanked==a->display_expected_blank;
        if(pending==DC_BRIGHTNESS)verified=verified&&a->display_brightness_supported&&!a->display_blanked&&a->display_requested_raw==a->display_expected_raw&&a->display_raw==a->display_expected_raw;
        if(pending!=DC_NONE){
            if(verified)snprintf(a->display_light_note,sizeof(a->display_light_note),a->display_blanked?"Screen is off. Short press Power to wake.":"Brightness %u%% confirmed. Power turns the screen off.",a->display_percent);
            else if(a->display_blank_known)snprintf(a->display_light_note,sizeof(a->display_light_note),T("Readback: screen %s, brightness %u%%. No command was repeated."),tr(a->display_blanked?"off":"on"),a->display_percent);
            else copy(a->display_light_note,sizeof(a->display_light_note),"Display state is unavailable. Press Power to request wake.");
        }else if(!a->display_brightness_supported)copy(a->display_light_note,sizeof(a->display_light_note),"Brightness control is unavailable. Refresh to check again.");
        a->display_uncertain=pending!=DC_NONE&&!a->display_blank_known;
        a->display_input_blocked=a->display_uncertain||(a->display_blank_known&&a->display_blanked);
    }else{
        a->display_light_known=false;a->display_brightness_supported=a->display_blank_supported=false;
        a->display_uncertain=a->display_uncertain||pending!=DC_NONE;
        a->display_input_blocked=a->display_uncertain||(a->display_blank_known&&a->display_blanked);
        copy(a->display_light_note,sizeof(a->display_light_note),a->display_uncertain?"Display outcome unknown. Press Power to request wake.":"Display status is unavailable. Refresh to check again.");
    }
    a->display_pending_command=DC_NONE;a->display_requested_percent=0;a->dirty=true;
}
static void poll_display_control(App*a){
    DisplayControl*c=&a->display_control;if(!c->pid)return;char data[4096];
    for(int stream=0;stream<2;stream++){
        int fd=stream?c->fderr:c->fdout;bool*eof=stream?&c->err_eof:&c->out_eof;
        if(*eof)continue;
        for(int batch=0;batch<4;batch++){
            ssize_t got=read(fd,data,sizeof(data));
            if(got==0){*eof=true;close(fd);break;}
            if(got<0){if(errno!=EINTR&&errno!=EAGAIN&&errno!=EWOULDBLOCK){*eof=true;close(fd);}break;}
            if(!stream){if(c->used+(size_t)got<=DISPLAY_CONTROL_LIMIT){memcpy(c->output+c->used,data,(size_t)got);c->used+=(size_t)got;c->output[c->used]=0;}else c->overflow=true;}
        }
    }
    if(!c->reaped){pid_t got=waitpid(c->pid,&c->status,WNOHANG);if(got==c->pid)c->reaped=true;else if(got<0&&errno==ECHILD){c->reaped=true;c->status=127<<8;}}
    if(milliseconds()>=c->deadline&&!c->timed_out){
        c->timed_out=true;if(!c->reaped)(void)kill(c->pid,SIGKILL);
        if(!c->out_eof){close(c->fdout);c->out_eof=true;}if(!c->err_eof){close(c->fderr);c->err_eof=true;}
    }
    if(c->reaped&&c->out_eof&&c->err_eof)complete_display_control(a);
    if(!a->display_control.pid&&a->display_wanted_percent){unsigned int want=a->display_wanted_percent;a->display_wanted_percent=0;if(want!=a->display_percent&&a->display_brightness_supported)(void)start_display_control(a,DC_BRIGHTNESS,want);}
}
static bool parse_usb_status(App*a,const Json*j){
    if(!jequals(j,0,"mode",a->device?"device":"fixture"))return false;
    char previous[128]={0};if(a->usb_selected>=0&&a->usb_selected<a->usb_candidate_count)copy(previous,sizeof(previous),a->usb_candidates[a->usb_selected].id);
    jstring(j,0,"state",a->usb_state,sizeof(a->usb_state));
    if(strcmp(a->usb_state,"stopped")&&strcmp(a->usb_state,"starting")&&strcmp(a->usb_state,"active")&&strcmp(a->usb_state,"stopping")&&strcmp(a->usb_state,"failed"))return false;
    a->usb_available=json_bool(j,json_get(j,0,"available"),0);jstring(j,0,"reason",a->usb_reason,sizeof(a->usb_reason));jstring(j,0,"session_token",a->usb_session,sizeof(a->usb_session));
    a->usb_candidate_count=0;a->usb_selected=-1;int candidates=json_get(j,0,"candidates");
    if(candidates<0||j->tokens[candidates].type!=J_ARRAY)return false;
    for(int i=candidates+1;i<j->count&&j->tokens[i].start<j->tokens[candidates].end&&a->usb_candidate_count<8;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT)continue;
        UsbCandidate*c=&a->usb_candidates[a->usb_candidate_count];memset(c,0,sizeof(*c));
        jstring(j,i,"id",c->id,sizeof(c->id));jstring(j,i,"display_label",c->label,sizeof(c->label));if(!c->label[0])jstring(j,i,"label",c->label,sizeof(c->label));
        jstring(j,i,"mountpoint",c->mountpoint,sizeof(c->mountpoint));jstring(j,i,"volume_token",c->volume_token,sizeof(c->volume_token));jstring(j,i,"mount_token",c->mount_token,sizeof(c->mount_token));
        c->writable=jequals(j,i,"actual_access","rw");if(!c->id[0]||!c->volume_token[0]||!c->mount_token[0]||(!c->writable&&!jequals(j,i,"actual_access","ro")))continue;
        if(!strcmp(previous,c->id))a->usb_selected=a->usb_candidate_count;
        a->usb_candidate_count++;
    }
    if(a->usb_selected<0&&a->usb_candidate_count)a->usb_selected=0;
    if(a->usb_selected>=0&&!a->usb_candidates[a->usb_selected].writable)a->usb_read_only=true;
    a->usb_export_valid=a->usb_export_writable=false;a->usb_export_id[0]=a->usb_export_volume_token[0]=a->usb_export_mount_token[0]=0;
    int exports=json_get(j,0,"exports");if(exports<0||j->tokens[exports].type!=J_ARRAY)return false;
    if(exports+1<json_next(j,exports)){
        int item=exports+1;if(j->tokens[item].type!=J_OBJECT)return false;
        jstring(j,item,"label",a->usb_export_id,sizeof(a->usb_export_id));jstring(j,item,"volume_token",a->usb_export_volume_token,sizeof(a->usb_export_volume_token));jstring(j,item,"mount_token",a->usb_export_mount_token,sizeof(a->usb_export_mount_token));
        a->usb_export_writable=jequals(j,item,"access","rw");
        a->usb_export_valid=json_next(j,item)==json_next(j,exports)&&a->usb_export_id[0]&&a->usb_export_volume_token[0]&&a->usb_export_mount_token[0]&&(a->usb_export_writable||jequals(j,item,"access","ro"));
    }
    a->usb_known=true;return true;
}
static const char*themes_helper="/opt/a22x-recovery/themes.py";
static const char*sysinfo_helper="/opt/a22x-recovery/sysinfo.py";
static bool start_sysinfo_job(App*a){
    a->sys_tried=true;if(a->job.pid||a->preview||(!a->device&&!a->sysinfo_root))return false;
    char*args[8];int n=0;args[n++]=a->python;args[n++]="-B";args[n++]=(char*)sysinfo_helper;
    if(a->sysinfo_root){args[n++]="--root";args[n++]=(char*)a->sysinfo_root;}
    args[n]=NULL;return launch_job(a,J_SYSINFO,args);
}
static void sysinfo_row(const Json*j,int row,SysRow*out,unsigned int section){
    memset(out,0,sizeof(*out));out->section=(unsigned char)section;char*fields[3]={out->label,out->value,out->unit};const size_t sizes[3]={sizeof(out->label),sizeof(out->value),sizeof(out->unit)};
    int end=json_next(j,row),item=row+1;
    for(int n=0;n<3&&item<end;n++,item=json_next(j,item))if(json_string(j,item,fields[n],sizes[n])<0)fields[n][0]=0;
}
/* Details that cannot be read leave the previous ones: a failed refresh is no reason to show less. */
static void complete_sysinfo(App*a,const Json*j){
    int summary=json_get(j,0,"summary"),sections=json_get(j,0,"sections");
    if(summary<0||sections<0||j->tokens[summary].type!=J_ARRAY||j->tokens[sections].type!=J_ARRAY)return;
    jstring(j,0,"name",a->sys_name,sizeof(a->sys_name));jstring(j,0,"model",a->sys_model,sizeof(a->sys_model));jstring(j,0,"codename",a->sys_codename,sizeof(a->sys_codename));jstring(j,0,"version",a->sys_version,sizeof(a->sys_version));
    a->sys_summary_count=a->sys_section_count=a->sys_row_count=0;
    for(int i=summary+1,end=json_next(j,summary);i<end&&a->sys_summary_count<SYS_SUMMARY;i=json_next(j,i))if(j->tokens[i].type==J_ARRAY)sysinfo_row(j,i,&a->sys_summary[a->sys_summary_count++],0);
    for(int i=sections+1,end=json_next(j,sections);i<end&&a->sys_section_count<SYS_SECTIONS;i=json_next(j,i)){
        if(j->tokens[i].type!=J_OBJECT)continue;
        unsigned int section=a->sys_section_count++;jstring(j,i,"title",a->sys_titles[section],sizeof(a->sys_titles[section]));
        int rows=json_get(j,i,"rows");if(rows<0||j->tokens[rows].type!=J_ARRAY)continue;
        for(int r=rows+1,last=json_next(j,rows);r<last&&a->sys_row_count<SYS_ROWS;r=json_next(j,r))if(j->tokens[r].type==J_ARRAY)sysinfo_row(j,r,&a->sys_rows[a->sys_row_count++],section);
    }
    a->dirty=true;
}
static bool start_theme_job(App*a,int kind){
    if(a->job.pid||a->preview)return false;
    char*args[16];int n=0;args[n++]=a->python;args[n++]="-B";args[n++]=(char*)themes_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_THEME_REMOVE){args[n++]="remove";args[n++]="--code";args[n++]=a->theme_remove_code;}
    else if(kind==J_THEME_RESTORE)args[n++]="restore";
    else{
        FileVolume*v=current_volume(a);if(!v){files_problem(a,"VOLUME_IDENTITY_MISSING","Refresh the volume list before continuing.",a->theme_return);return false;}
        args[n++]="import";args[n++]="--volume";args[n++]=v->id;args[n++]="--expected-volume-token";args[n++]=v->token;args[n++]="--path";args[n++]=a->fs_selected.path;
    }
    args[n]=NULL;return launch_job(a,kind,args);
}
static void complete_theme_job(App*a,const Json*j,int kind,bool ok){
    enum Screen back=a->theme_return;bool from_terminal=back==TERMINAL_THEME;a->theme_pick=HOME;
    if(!ok){
        char code[128]="THEME_FAILED",message[512]="The theme helper did not return a valid result.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        files_problem(a,code,message,back);return;
    }
    theme_reload(a);a->successful=true;a->page_scroll=0;
    if(kind!=J_THEME_IMPORT){settings_changed(a);toast(a,kind==J_THEME_RESTORE?"Removed themes are back":(from_terminal?"Colors removed":"Theme removed"));screen(a,back);return;}
    char code[24]={0};jstring(j,0,"code",code,sizeof(code));
    bool interface=json_bool(j,json_get(j,0,"interface"),0),terminal=json_bool(j,json_get(j,0,"terminal"),0),kept=json_bool(j,json_get(j,0,"kept"),0);
    if(interface&&!from_terminal)(void)theme_select(a,code);
    if(terminal&&(from_terminal||!interface))(void)terminal_select(code);
    settings_changed(a);
    toast(a,!kept?"Added until restart: no writable storage":(from_terminal&&!terminal?"This file has no terminal colors":(!interface?"Terminal colors added":"Theme added")));
    screen(a,!interface&&!from_terminal?TERMINAL_THEME:back);
}
static void theme_file_chosen(App*a){start_theme_job(a,J_THEME_IMPORT);}
#include "terminal_ui.h"
#include "source_ui.h"
#include "systems_ui.h"
#include "install_ui.h"
#include "wifi_ui.h"
#include "installer_ui.h"
#include "images_ui.h"
#include "adb_ui.h"
#include "packages_ui.h"
#include "identity_ui.h"
#include "charger_ui.h"
#include "shells_ui.h"
#include "locale_ui.h"
#include "android_plan_ui.h"
#include "wipe_ui.h"
#include "androidlog_ui.h"
#include "android_ui.h"
#include "card_ui.h"
#include "copies_ui.h"
/* The reply of the job that has just ended is freed, and only it. A handler may start the next
   job, which then owns a->job with a buffer of its own (2026-11-11: exporting diagnostics from
   Home reads the status and then the volumes; the second read lost its buffer to the first one's
   clean-up and ended as BACKEND_FAILED). */
static void job_reply_done(App*a,char*ended){if(a->job.output==ended)a->job.output=NULL;free(ended);}
static void complete_job(App*a){
    Job*j=&a->job;char*const ended=j->output;int kind=j->kind,next_files_job=J_NONE;bool next_display_status=false,next_usb_status=false;JsonToken*tok=calloc(8192,sizeof(*tok));Json doc;bool parsed=tok&&j->output&&!j->overflow&&json_parse(&doc,j->output,tok,8192)>0&&tok[0].type==J_OBJECT;
    if(terminal_background_job(kind)){} /* Shell input/output never enter UI diagnostics. */
    else if(file_job(kind)||kind==J_TOOLS_STATUS||kind==J_TOOLS_EXPORT||kind>=J_USB_STATUS)fprintf(stderr,"[recovery-ui:%s] exit=%d; result details omitted from log\n",job_name(kind),WIFEXITED(j->status)?WEXITSTATUS(j->status):-1);
    else fprintf(stderr,"[recovery-ui:%s] exit=%d result=%s\n",job_name(kind),WIFEXITED(j->status)?WEXITSTATUS(j->status):-1,j->output?j->output:"(no result buffer)");
    bool ok=parsed&&WIFEXITED(j->status)&&WEXITSTATUS(j->status)==0&&json_bool(&doc,json_get(&doc,0,"ok"),0);
    if(kind==J_CATALOG_CHECK&&!ok)fprintf(stderr,"[recovery-ui:catalog-check] parsed=%d used=%zu overflow=%d status=%d head=%.60s\n",parsed,j->used,j->overflow,j->status,j->output?j->output:"-");
    j->pid=0;a->job_cancel_asked=false;
    if(a->job_cancelled){
        a->job_cancelled=false;free(tok);job_reply_done(a,ended);a->successful=false;copy(a->status,sizeof(a->status),"Operation cancelled");copy(a->message,sizeof(a->message),"The operation was stopped.");
        if(kind==J_SYS_IMPORT)systems_forget_plan(a);
        /* A download has no progress page: it returns to the page it was started on (2026-11-10). */
        enum Screen here=nav_page(a);
        a->result_return=kind==J_SYS_IMPORT?SYSTEMS:kind==J_IMAGES_DOWNLOAD?(here==ANDROID_IMAGE||here==SYSTEMS_CATALOG?here:SYSTEMS_IMAGES):(kind==J_FLASH_CHECK?FLASH:(kind==J_FS_COPY||kind==J_FS_COPY_TREE?FILE_BROWSER:HOME));screen(a,RESULT);return;
    }
    if(terminal_job(kind)){complete_terminal_job(a,parsed?&doc:NULL,kind,ok);free(tok);if(j->output){memset(j->output,0,j->used);job_reply_done(a,ended);}return;}
    if(systems_job(kind)){int next=complete_systems_job(a,parsed?&doc:NULL,kind,ok);free(tok);job_reply_done(a,ended);if(next)start_systems_job(a,next);
        if(kind==J_SYS_STATUS&&a->catalog_after_status&&!a->job.pid){a->catalog_after_status=false;start_catalog_job(a);}
        /* Userdata mounted from the saved images page: its list is read now. */
        if(kind==J_SYS_STATUS&&(a->screen==SYSTEMS_IMAGES||a->screen==ANDROID_IMAGE)&&!a->job.pid&&(systems_ready(a)||a->screen==ANDROID_IMAGE)&&!a->saved_known)start_images_job(a,J_IMAGES,NULL);
        /* The image chosen before userdata was mounted goes on to the install pages. */
        if(kind==J_SYS_STATUS&&a->catalog_pending&&a->screen==SYSTEMS_CATALOG&&!a->job.pid){
            unsigned int n=a->catalog_pending-1;a->catalog_pending=0;
            if(n<a->catalog_count&&a->catalog[n].reachable&&systems_ready(a))catalog_choose(a,&a->catalog[n]);}
        /* The plan from the Android app goes on once userdata is known (2026-11-10). */
        if(kind==J_SYS_STATUS&&a->aplan_pending&&!a->job.pid)android_plan_after_status(a);
        if(kind==J_SYS_IMPORT&&a->aplan_forget_after&&!a->job.pid)start_aplan_job(a,J_APLAN_FORGET);
        return;}
    /* The list starts the server check from its completion: that new job owns a->job, so this
       reply is taken off it first. Freeing j->output afterwards dropped the check's own buffer and
       every check then reported that the servers did not answer (2026-11-08, found on the phone). */
    if(kind==J_CATALOG||kind==J_CATALOG_CHECK){char*reply=j->output;j->output=NULL;complete_catalog_job(a,parsed?&doc:NULL,ok,kind==J_CATALOG_CHECK);free(tok);free(reply);return;}
    if(kind==J_CHARGER){complete_charger_job(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    if(images_job(kind)){complete_images_job(a,parsed?&doc:NULL,kind,ok);free(tok);job_reply_done(a,ended);return;}
    /* Preparing a plan starts the status of Systems from its completion: that new job owns a->job,
       so this reply is taken off it first, as for the catalog. */
    if(aplan_job(kind)){char*reply=j->output;j->output=NULL;complete_aplan_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(wipe_job(kind)){complete_wipe_job(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    /* An unpacked firmware ZIP starts the review of its AP: that new job owns a->job (2026-11-10). */
    if(samsung_job(kind)){char*reply=j->output;j->output=NULL;complete_samsung_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(root_job(kind)){char*reply=j->output;j->output=NULL;complete_root_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(zip_job(kind)){char*reply=j->output;j->output=NULL;complete_zip_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(card_job(kind)){char*reply=j->output;j->output=NULL;complete_card_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    /* A deleted copy reads the cards again: that job owns a->job, so this reply is taken off it first. */
    if(copy_job(kind)){char*reply=j->output;j->output=NULL;complete_copy_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    /* A deleted copy reads the list again: that job owns a->job, so this reply is taken off it first. */
    if(kind==J_ID_LIST||kind==J_ID_BACKUP||kind==J_ID_BACKUPS||kind==J_ID_RESTORE||kind==J_ID_DELETE){char*reply=j->output;j->output=NULL;complete_identity_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(kind==J_PKG_SEARCH){complete_package_search(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    if(wifi_job(kind)){int next=complete_wifi_job(a,parsed?&doc:NULL,kind,ok);free(tok);job_reply_done(a,ended);if(next)start_wifi_job(a,next);return;}
    if(kind==J_USB_SYNC){complete_usb_sync(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    if(kind==J_USB_BACKUP){complete_usb_backup(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    if(kind==J_USB_EJECT){complete_usb_eject(a,parsed?&doc:NULL,ok);free(tok);job_reply_done(a,ended);return;}
    /* The list of partitions can start the check of a chosen image (2026-11-10, "Flash it to BOOT"):
       that new job owns a->job, so this reply is taken off it first, as for the catalog. */
    if(flash_job(kind)){char*reply=j->output;j->output=NULL;complete_flash_job(a,parsed?&doc:NULL,kind,ok);free(tok);free(reply);return;}
    if(kind==J_SYSINFO){if(ok)complete_sysinfo(a,&doc);free(tok);job_reply_done(a,ended);return;}
    if(theme_job(kind)){complete_theme_job(a,parsed?&doc:NULL,kind,ok);free(tok);job_reply_done(a,ended);return;}
    if(install_job(kind)){int next=complete_install_job(a,parsed?&doc:NULL,kind,ok);free(tok);job_reply_done(a,ended);if(next)start_install_job(a,next);return;}
    if(!ok){
        char code[128]="BACKEND_FAILED",msg[1536]="The backend did not return a valid success result.";
        if(parsed){int err=json_get(&doc,0,"error");if(err>=0){if(doc.tokens[err].type==J_OBJECT){jstring(&doc,err,"code",code,sizeof(code));jstring(&doc,err,"message",msg,sizeof(msg));}else if(doc.tokens[err].type==J_STRING)json_string(&doc,err,msg,sizeof(msg));}}
        if(j->overflow)copy(msg,sizeof(msg),"Backend output exceeded the bounded result buffer.");
        /* A result on screen keeps its own words (2026-11-10: the reason the phone stayed in recovery was lost here). */
        if(kind==J_INVENTORY){if(a->screen!=RESULT){copy(a->status,sizeof(a->status),code);copy(a->message,sizeof(a->message),msg);}a->inventory_ok=false;a->dirty=true;}
        else if(file_job(kind)){
            if(a->prepare_job!=J_NONE&&a->fs_inventory_destination==PREPARE_FILESYSTEM){a->prepare_unmounting=false;files_problem(a,code,msg,PREPARE_FILESYSTEM);}
            /* The list of volumes only helps: sharing what is mounted works without it. */
            else if(kind==J_FS_INVENTORY&&a->fs_inventory_destination==USB_FILES){a->usb_volumes_known=true;a->usb_refresh_after=false;a->volume_count=0;a->dirty=true;}
            /* The sheet says that no card was found, and offers to read again. */
            else if(kind==J_FS_INVENTORY&&a->source_sheet){a->source_known=true;a->volume_count=0;a->dirty=true;}
            else if(a->usb_mounting){a->usb_mounting=false;files_problem(a,code,msg,USB_FILES);}
            else if(a->systems_mounting){a->systems_mounting=false;files_problem(a,code,msg,SYSTEMS);}
            else if(a->copy_mounting){a->copy_mounting=false;files_problem(a,code,msg,LIST);}
            else if(a->id_mounting){a->id_mounting=false;files_problem(a,code,msg,IDENTITY);}
            else files_problem(a,code,msg,kind==J_FS_INVENTORY&&a->fs_inventory_destination==TOOLS_EXPORT?TOOLS:file_error_return(kind));
        }
        else if(kind==J_TOOLS_STATUS||kind==J_TOOLS_EXPORT){if(kind==J_TOOLS_STATUS){a->tools_known=false;a->logs_export_available=false;}files_problem(a,code,msg,kind==J_TOOLS_EXPORT?HOME:TOOLS);}
        else if(kind==J_DISPLAY_STATUS||kind==J_DISPLAY_SET){a->display_known=false;a->display_hz=a->display_requested_hz=0;a->display_actual_hz=0;if(a->display_restoring)display_restore_failed(a);else files_problem(a,code,msg,DISPLAY_SETTINGS);}
        else if(kind>=J_USB_STATUS&&kind<=J_USB_STOP){a->usb_known=false;a->usb_expected=0;files_problem(a,code,msg,USB_FILES);}
        else if(kind==J_LIST&&a->install.pick_backup)files_problem(a,code,msg,INSTALL);
        else if(kind==J_BACKUP_RENAME||kind==J_BACKUP_DELETE){memset(a->backup_new_name,0,sizeof(a->backup_new_name));files_problem(a,code,msg,DETAIL);}
        else problem(a,code,msg);
    }else if(kind==J_INVENTORY){parse_inventory(a,&doc);a->inventory_ok=true;if(a->screen!=RESULT){copy(a->status,sizeof(a->status),a->device?"Device inventory received":"Regular-file test environment");a->successful=true;a->message[0]=0;}a->dirty=true;}
    else if(file_job(kind))next_files_job=complete_files_job(a,&doc,kind);
    else if(kind==J_TOOLS_STATUS||kind==J_TOOLS_EXPORT){if(!complete_tools_job(a,&doc,kind))files_problem(a,"INVALID_TOOLS_RESULT","The management helper returned an incomplete result.",TOOLS);}
    else if(kind==J_DISPLAY_SET){a->display_known=false;a->display_hz=0;a->display_actual_hz=0;next_display_status=true;}
    else if(kind==J_DISPLAY_STATUS){
        bool restoring=a->display_restoring;
        if(!parse_display_status(a,&doc)){a->display_known=false;a->display_hz=a->display_requested_hz=0;if(restoring)display_restore_failed(a);else files_problem(a,"INVALID_DISPLAY_RESULT","The display helper did not return a complete status.",DISPLAY_SETTINGS);}
        else if(a->display_requested_hz&&(!a->display_supported||!a->display_service||a->display_hz!=a->display_requested_hz||a->display_actual_hz<a->display_requested_hz-1.0||a->display_actual_hz>a->display_requested_hz+1.0)){
            char reason[192];snprintf(reason,sizeof(reason),T("The requested %u Hz mode was not confirmed by the subsequent status read. Reported mode: %u Hz."),a->display_requested_hz,a->display_hz);
            a->display_requested_hz=0;if(restoring)display_restore_failed(a);else files_problem(a,"DISPLAY_MODE_NOT_APPLIED",reason,DISPLAY_SETTINGS);
        }else{
            if(a->display_requested_hz&&!restoring)snprintf(a->display_note,sizeof(a->display_note),T("%u Hz display mode is active."),a->display_hz);
            if(!restoring)a->successful=true;
            a->display_requested_hz=0;a->display_restoring=false;a->dirty=true;
        }
    }
    else if(kind==J_USB_START||kind==J_USB_STOP){a->usb_known=false;next_usb_status=true;}
    else if(kind==J_USB_STATUS){
        if(!parse_usb_status(a,&doc)){a->usb_known=false;a->usb_expected=0;files_problem(a,"INVALID_USB_STATUS","The USB file helper returned an incomplete state. Refresh before changing sharing.",USB_FILES);}
        else{
            bool pending=!strcmp(a->usb_state,"starting")||!strcmp(a->usb_state,"stopping");
            bool verified=a->usb_expected==0||(a->usb_expected==1&&!strcmp(a->usb_state,"active")&&usb_session_open(a)&&a->usb_export_valid&&!strcmp(a->usb_export_id,a->usb_request_id)&&!strcmp(a->usb_export_volume_token,a->usb_request_volume_token)&&!strcmp(a->usb_export_mount_token,a->usb_request_mount_token)&&a->usb_export_writable!=a->usb_request_ro)||(a->usb_expected==2&&!strcmp(a->usb_state,"stopped")&&!a->usb_session[0]);
            if(!verified&&!pending){a->usb_expected=0;files_problem(a,"USB_STATE_NOT_CONFIRMED","The requested USB export or stop was not confirmed. Inspect and refresh the current state before continuing.",USB_FILES);}
            else{
                if(!pending)a->usb_expected=0;
                a->successful=true;screen(a,USB_FILES);
                for(int n=0;a->usb_want[0]&&n<a->usb_candidate_count;n++)if(!strcmp(a->usb_candidates[n].id,a->usb_want)){a->usb_selected=n;a->usb_read_only=true;}
                a->usb_want[0]=0;
                if(!a->usb_volumes_known){a->fs_inventory_destination=USB_FILES;next_files_job=J_FS_INVENTORY;}
            }
        }
    }
    else if(kind>=J_STORAGE_INVENTORY&&kind<=J_UMOUNT){parse_storage(a,&doc);screen(a,STORAGE);}
    else if(kind==J_LIST){a->message[0]=0;parse_list(a,&doc);screen(a,LIST);}
    else if(kind==J_BACKUP_RENAME||kind==J_BACKUP_DELETE){
        /* The list is read again: a renamed backup sorts elsewhere and a deleted one is gone. */
        toast(a,kind==J_BACKUP_RENAME?"Backup renamed":"Backup deleted");a->toast_icon=A_BACKUP;a->successful=true;
        memset(a->backup_new_name,0,sizeof(a->backup_new_name));a->backup_relist=true;
    }
    else if(kind==J_BACKUP_PLAN||kind==J_RESTORE_PLAN||kind==J_WIPE_PLAN)parse_plan(a,&doc,kind);
    else{a->successful=true;
        /* The title says what was done (2026-11-11: it was the name of the helper's command, "verify"). */
        copy(a->status,sizeof(a->status),kind==J_BACKUP?"Backup saved":(kind==J_VERIFY?"Backup verified":(kind==J_WIPE?"Userdata wiped":"Restore completed")));
        copy(a->message,sizeof(a->message),kind==J_BACKUP?(a->plan_linux_v2?"Backup completed and verified. Userdata remains offline.":"Backup completed and verified."):(kind==J_VERIFY?"The backup passed integrity verification.":(kind==J_WIPE?"Userdata is now an empty ext4 filesystem. Boot and the SD snapshot were preserved.":"The reviewed restore completed and was verified. Userdata remains offline.")));a->plan_can_execute=a->plan_linux_admitted=false;a->token[0]=0;screen(a,RESULT);}
    free(tok);job_reply_done(a,ended);a->dirty=true;
    if(a->backup_relist){a->backup_relist=false;a->list_for_verify=false;if(!start_job(a,J_LIST))screen(a,LIST);return;}
    if(kind==J_FS_WRITE&&a->fs_request.text_file[0]){unlink(a->fs_request.text_file);a->fs_request.text_file[0]=0;}
    if(next_files_job!=J_NONE)start_files_job(a,next_files_job);
    else if(kind==J_FS_INVENTORY&&a->id_list_after){a->id_list_after=false;start_identity_job(a,J_ID_LIST);}
    else if(a->systems_refresh_after){a->systems_refresh_after=false;start_systems_job(a,J_SYS_STATUS);}
    else if(a->copy_list_after){a->copy_list_after=false;if(!start_copy_job(a,J_COPY_LIST))a->copies_known=true;}
    else if(kind==J_FS_INVENTORY&&a->prepare_unmounting&&a->screen==PREPARE_FILESYSTEM){
        a->prepare_unmounting=false;
        if(a->prepare_known&&!a->prepare_mounted&&a->prepare_token[0])start_job(a,a->prepare_job);
        else copy(a->prepare_reason,sizeof(a->prepare_reason),"Userdata is still mounted or changed. Review its current state before another attempt.");
    }
    else if(kind==J_FS_INVENTORY&&a->usb_refresh_after){a->usb_refresh_after=false;start_usb_job(a,J_USB_STATUS);}
    else if(next_display_status)start_display_job(a,J_DISPLAY_STATUS);
    else if(kind==J_TOOLS_STATUS&&a->screen==TOOLS)start_charger_job(a,0);
    else if(kind==J_INVENTORY&&a->screen==SYSTEM)start_sysinfo_job(a);
    else if(next_usb_status)start_usb_job(a,J_USB_STATUS);
    else if(kind==J_FS_LIST&&a->screen==FILE_BROWSER&&a->successful)browser_maybe_load(a);
}
/* A read that fills the page it was started for. When the user has left that page before it
 * ends (Home, or a Back that needs no read), its end opens nothing: 2026-11-10, the list of
 * volumes came back over the home page, and Back from it led home instead of the picker's menu. */
static bool job_page_read(int kind){
    return kind==J_FS_INVENTORY||kind==J_FS_LIST||kind==J_SYS_STATUS||kind==J_STORAGE_INVENTORY||kind==J_LIST||kind==J_USB_STATUS||kind==J_TOOLS_STATUS||
           kind==J_FLASH_TARGETS||kind==J_INSTALL_STATUS||kind==J_ID_LIST||kind==J_ID_BACKUPS||kind==J_APLAN_PREPARE||kind==J_CARD_LIST||kind==J_COPY_LIST;
}
static void tap_action(App*a,int id);
static void poll_job(App*a){
    Job*j=&a->job;if(!j->pid)return;char data[8192];
    if(a->job_page_mark){a->job_page_mark=false;a->job_page=nav_page(a);}
    for(int stream=0;stream<2;stream++){
        int fd=stream?j->fderr:j->fdout;bool*eof=stream?&j->err_eof:&j->out_eof;if(*eof)continue;
        for(;;){ssize_t n=read(fd,data,sizeof(data));if(n==0){*eof=true;close(fd);break;}if(n<0){if(errno!=EAGAIN&&errno!=EWOULDBLOCK&&errno!=EINTR){*eof=true;close(fd);}break;}
            if(!stream){if(j->output&&j->used+(size_t)n<=JSON_LIMIT){memcpy(j->output+j->used,data,(size_t)n);j->used+=(size_t)n;j->output[j->used]=0;}else j->overflow=true;}
            else if(!terminal_job(j->kind))for(ssize_t k=0;k<n;k++){if(data[k]=='\n'){j->errline[j->errused]=0;fprintf(stderr,"[recovery-ui:%s] %s\n",job_name(j->kind),j->errline);parse_progress(a,j->errline);j->errused=0;}else if(j->errused<sizeof(j->errline)-1)j->errline[j->errused++]=data[k];}
        }
    }
    if(!j->reaped){pid_t got=waitpid(j->pid,&j->status,WNOHANG);if(got==j->pid)j->reaped=true;}
    if(j->reaped&&j->out_eof&&j->err_eof){
        a->job_orphan=a->job_orphan||(job_page_read(j->kind)&&nav_page(a)!=a->job_page);
        a->job_completing=true;a->nav_hold=a->job_orphan;
        complete_job(a);
        a->nav_hold=a->job_completing=false;
        /* A job started by this end belongs to the page it ended on, and stays without one if this had none. */
        if(a->job.pid)a->job_page=nav_page(a);else a->job_orphan=false;
        /* A tap that waited for the read is made now, with its page transition. */
        if(a->queued_action&&!a->job.pid){int id=a->queued_action;a->queued_action=0;if(a->queued_screen==a->screen)tap_action(a,id);}
    }
}
static bool relative_path(const char*source,char*out,size_t capacity){
    if(!source[0])return false;
    size_t length=strlen(source);if(length+2>capacity||strpbrk(source,"\r\n"))return false;
    if(source[0]=='/')memcpy(out,source,length+1);else{out[0]='/';memcpy(out+1,source,length+1);}
    length=strlen(out);while(length>1&&out[length-1]=='/')out[--length]=0;
    const char*p=out+1;
    while(*p){const char*end=strchr(p,'/');size_t n=end?(size_t)(end-p):strlen(p);if(!n||(n==1&&p[0]=='.')||(n==2&&p[0]=='.'&&p[1]=='.'))return false;if(!end)break;p=end+1;}
    return true;
}
static bool child_path(const char*directory,const char*name,char*out,size_t capacity){
    int n=snprintf(out,capacity,"%s%s%s",directory,strcmp(directory,"/")?"/":"",name);return n>=0&&(size_t)n<capacity;
}
static void parent_path(char*path){
    char*slash=strrchr(path,'/');if(!slash||slash==path)copy(path,PATH_LEN,"/");else *slash=0;
}
static void select_volume(App*a,int index){
    if(index<0||index>=a->volume_count)return;
    a->volume_index=index;copy(a->volume_id,sizeof(a->volume_id),a->volumes[index].id);copy(a->fs_path,sizeof(a->fs_path),"/");
    reset_browser(a);a->fs_status[0]=a->fs_sha256[0]=0;a->fs_dirty=false;a->fs_new_file=false;
    memset(&a->fs_selected,0,sizeof(a->fs_selected));a->fs_file[0]=0;
    if(a->volumes[index].mounted){screen(a,FILE_BROWSER);start_files_job(a,J_FS_LIST);}else screen(a,FILE_VOLUME);
}
static void open_selected_file(App*a){
    if(file_is_directory(&a->fs_selected)){copy(a->fs_path,sizeof(a->fs_path),a->fs_selected.path);reset_browser(a);screen(a,FILE_BROWSER);start_files_job(a,J_FS_LIST);}
    else if(file_is_regular(&a->fs_selected)){copy(a->fs_file,sizeof(a->fs_file),a->fs_selected.path);a->fs_sha256[0]=0;a->fs_dirty=false;start_files_job(a,J_FS_READ);}
}
static int next_writable_volume(App*a,int index){
    for(int n=0;n<a->volume_count;n++){index=(index+1+a->volume_count)%a->volume_count;if(a->volumes[index].mounted&&!a->volumes[index].read_only&&a->volumes[index].write_supported)return index;}
    return -1;
}
static void begin_file_prompt(App*a,int kind){
    a->fs_prompt_kind=kind;a->prompt_scroll=0;
    if(kind==J_FS_RENAME)copy(a->fs_prompt,sizeof(a->fs_prompt),a->fs_selected.path);
    else if(kind==J_FS_CHMOD)copy(a->fs_prompt,sizeof(a->fs_prompt),a->fs_selected.mode);
    else if(kind==J_FS_CHOWN)snprintf(a->fs_prompt,sizeof(a->fs_prompt),"%llu:%llu",(unsigned long long)a->fs_selected.uid,(unsigned long long)a->fs_selected.gid);
    else if(kind==J_FS_COPY||kind==J_FS_COPY_TREE){
        if(snprintf(a->fs_prompt,sizeof(a->fs_prompt),"%s.copy",a->fs_selected.path)>=(int)sizeof(a->fs_prompt)){files_problem(a,"PATH_TOO_LONG","Choose a shorter destination path.",FILE_INFO);return;}
        a->fs_destination_index=files_writable(a)?a->volume_index:next_writable_volume(a,-1);
    }else if(!child_path(a->fs_path,kind==J_FS_MKDIR?"New folder":"new.txt",a->fs_prompt,sizeof(a->fs_prompt))){files_problem(a,"PATH_TOO_LONG","The current folder path is too long for this interface.",FILE_BROWSER);return;}
    a->prompt_cursor=strlen(a->fs_prompt);a->prompt_caret_follow=true;screen(a,FILE_PROMPT);
}
static void confirm_file_request(App*a,FileRequest q,enum Screen from){a->fs_pending=q;a->fs_confirm_from=from;a->fs_confirm_page=0;screen(a,FILE_CONFIRM);}
static bool numeric_owner(const char*s,size_t length){
    if(!length||length>10)return false;
    uint64_t number=0;
    for(size_t n=0;n<length;n++){if(s[n]<'0'||s[n]>'9')return false;number=number*10u+(unsigned int)(s[n]-'0');}
    return number<UINT32_MAX;
}
static void accept_file_prompt(App*a){
    if(a->fs_prompt_kind==J_FS_CHMOD||a->fs_prompt_kind==J_FS_CHOWN){
        if(!files_writable(a)||!a->fs_selected.metadata_supported||!a->fs_selected.token[0]){files_problem(a,"METADATA_UNAVAILABLE","Inspect a supported entry on a writable volume before continuing.",FILE_INFO);return;}
        FileRequest q=file_request(a,a->fs_prompt_kind);copy(q.path,sizeof(q.path),a->fs_selected.path);copy(q.entry_token,sizeof(q.entry_token),a->fs_selected.token);
        if(q.kind==J_FS_CHMOD){
            bool valid=strlen(a->fs_prompt)==4;for(const char*p=a->fs_prompt;*p;p++)if(*p<'0'||*p>'7')valid=false;
            if(!valid){files_problem(a,"INVALID_MODE","Use exactly four octal digits, for example 0644 or 0755.",FILE_PROMPT);return;}
            copy(q.mode,sizeof(q.mode),a->fs_prompt);
        }else{
            const char*colon=strchr(a->fs_prompt,':');if(!colon||!numeric_owner(a->fs_prompt,(size_t)(colon-a->fs_prompt))||!numeric_owner(colon+1,strlen(colon+1))){files_problem(a,"INVALID_OWNER","Enter numeric UID:GID, for example 1000:1000. Each ID must be between 0 and 4294967294.",FILE_PROMPT);return;}
            size_t length=(size_t)(colon-a->fs_prompt);memcpy(q.uid,a->fs_prompt,length);q.uid[length]=0;copy(q.gid,sizeof(q.gid),colon+1);
        }
        confirm_file_request(a,q,FILE_PROMPT);return;
    }
    char path[PATH_LEN];
    if(!relative_path(a->fs_prompt,path,sizeof(path))||!strcmp(path,"/")){files_problem(a,"INVALID_PATH","Enter a file or folder path without '.' or '..' components.",FILE_PROMPT);return;}
    if(a->fs_prompt_kind==J_FS_WRITE){
        copy(a->fs_file,sizeof(a->fs_file),path);a->fs_text[0]=a->fs_sha256[0]=0;a->fs_length=a->fs_cursor=a->fs_scroll=0;
        a->fs_new_file=true;a->fs_dirty=false;a->fs_truncated=false;screen(a,FILE_EDIT);return;
    }
    FileRequest q=file_request(a,a->fs_prompt_kind);
    if(q.kind==J_FS_MKDIR)copy(q.path,sizeof(q.path),path);
    else{copy(q.path,sizeof(q.path),a->fs_selected.path);copy(q.destination,sizeof(q.destination),path);}
    if(q.kind==J_FS_COPY||q.kind==J_FS_COPY_TREE){
        if(a->fs_destination_index<0||a->fs_destination_index>=a->volume_count||!a->volumes[a->fs_destination_index].mounted||a->volumes[a->fs_destination_index].read_only||!a->volumes[a->fs_destination_index].write_supported){files_problem(a,"DESTINATION_UNAVAILABLE","Mount a destination volume with write access before copying.",FILE_PROMPT);return;}
        FileVolume*v=&a->volumes[a->fs_destination_index];copy(q.destination_volume,sizeof(q.destination_volume),v->id);copy(q.destination_token,sizeof(q.destination_token),v->token);
        if(q.kind==J_FS_COPY_TREE)copy(q.entry_token,sizeof(q.entry_token),a->fs_selected.token);
    }
    confirm_file_request(a,q,FILE_PROMPT);
}
static char*editable_text(App*a,size_t**cursor,size_t**scroll,size_t*capacity){
    if(a->screen==FILE_PROMPT){*cursor=&a->prompt_cursor;*scroll=&a->prompt_scroll;*capacity=sizeof(a->fs_prompt);return a->fs_prompt;}
    if(a->screen==FILE_EDIT){*cursor=&a->fs_cursor;*scroll=&a->fs_scroll;*capacity=sizeof(a->fs_text);return a->fs_text;}
    return NULL;
}
static void insert_character(App*a,uint32_t cp){
    size_t*cursor=NULL,*scroll=NULL,capacity=0;char*s=editable_text(a,&cursor,&scroll,&capacity);(void)scroll;
    if(!s||!cp)return;
    if(cp=='\n'&&a->screen==FILE_PROMPT){accept_file_prompt(a);return;}
    char encoded[5];size_t count=utf8_encode(cp,encoded),length=strlen(s);
    if(length+count>=capacity){copy(a->fs_status,sizeof(a->fs_status),"Text limit reached");a->dirty=true;return;}
    if(*cursor>length)*cursor=length;
    memmove(s+*cursor+count,s+*cursor,length-*cursor+1);memcpy(s+*cursor,encoded,count);*cursor+=count;
    if(a->screen==FILE_EDIT){a->fs_length=length+count;a->fs_dirty=true;a->fs_caret_follow=true;}else a->prompt_caret_follow=true;a->dirty=true;
}
static void erase_character(App*a){
    size_t*cursor=NULL,*scroll=NULL,capacity=0;char*s=editable_text(a,&cursor,&scroll,&capacity);(void)scroll;(void)capacity;if(!s||!*cursor)return;
    size_t length=strlen(s),previous=utf8_previous(s,*cursor),removed=*cursor-previous;
    memmove(s+previous,s+*cursor,length-*cursor+1);*cursor=previous;
    if(a->screen==FILE_EDIT){a->fs_length=length-removed;a->fs_dirty=true;a->fs_caret_follow=true;}else a->prompt_caret_follow=true;a->dirty=true;
}
static size_t position_at_x(const char*s,size_t row,int x,int width){
    TextRow r=text_row(s,row,width);const char*p=s+row;int at=0;
    while((size_t)(p-s)<r.end){const char*before=p;uint32_t cp=utf8_next(&p);int w=codepoint_width(cp,0);if(x<at+w/2)return(size_t)(before-s);at+=w;}
    return r.end;
}
static void move_text_cursor(App*a,int action_id){
    size_t*cursor=NULL,*scroll=NULL,capacity=0;char*s=editable_text(a,&cursor,&scroll,&capacity);(void)scroll;(void)capacity;if(!s)return;
    size_t length=strlen(s);if(*cursor>length)*cursor=length;
    if(action_id==A_FS_LEFT)*cursor=utf8_previous(s,*cursor);
    else if(action_id==A_FS_RIGHT){const char*p=s+*cursor;if(*p)(void)utf8_next(&p);*cursor=(size_t)(p-s);}
    else{
        const int width=628;size_t row=row_start_at(s,*cursor,width);const char*p=s+row;int x=0;
        while((size_t)(p-s)<*cursor&&*p){uint32_t cp=utf8_next(&p);if(cp!='\n')x+=codepoint_width(cp,0);}
        size_t target=row;
        if(action_id==A_FS_UP_LINE)target=previous_row(s,row,width);
        else{TextRow r=text_row(s,row,width);target=r.next;}
        *cursor=position_at_x(s,target,x,width);
    }
    if(a->screen==FILE_EDIT)a->fs_caret_follow=true;else a->prompt_caret_follow=true;
    a->dirty=true;
}
static void tap_text_cursor(App*a){
    size_t*cursor=NULL,*scroll=NULL,capacity=0;char*s=editable_text(a,&cursor,&scroll,&capacity);(void)capacity;if(!s)return;
    Rect content=text_content(text_box(a));bool prompt=a->screen==FILE_PROMPT;text_layout(a,prompt,content.w);
    int64_t y=(int64_t)a->pointer_y-content.y+(int64_t)*scroll;if(y<0)y=0;
    unsigned int row=(unsigned int)(y/TEXT_ROW_HEIGHT);if(row>=a->text_row_count)row=a->text_row_count-1;
    *cursor=position_at_x(s,a->text_rows[row],a->pointer_x-content.x,content.w);
    if(prompt)a->prompt_caret_follow=false;else a->fs_caret_follow=false;
    if(a->profile_render)fprintf(stderr,"[recovery-ui:caret] surface=%s cursor_byte=%zu offset_px=%zu\n",prompt?"prompt":"text",*cursor,*scroll);
    a->dirty=true;
}
static void leave_editor(App*a){if(a->fs_dirty||a->fs_new_file)screen(a,FILE_DISCARD);else screen(a,FILE_TEXT);}
static bool files_action(App*a,int id){
    if(id>=A_FS_VOLUME_ITEM&&id<A_FS_VOLUME_ITEM+a->volume_count){select_volume(a,id-A_FS_VOLUME_ITEM);return true;}
    if(id>=A_FS_ENTRY&&id<A_FS_ENTRY+a->file_count){
        a->fs_selected=a->files[id-A_FS_ENTRY];
        /* A theme file is mostly picture data, so its page of actions is more use than its text. */
        if(file_is_regular(&a->fs_selected)&&theme_file_name(a->fs_selected.name)){a->fs_link_resolved=false;screen(a,FILE_INFO);start_files_job(a,J_FS_STAT);}else open_selected_file(a);
        return true;
    }
    if(id>=A_FS_ENTRY_MENU&&id<A_FS_ENTRY_MENU+a->file_count){a->fs_selected=a->files[id-A_FS_ENTRY_MENU];a->fs_link_resolved=false;screen(a,FILE_INFO);start_files_job(a,J_FS_STAT);return true;}
    if(id>=A_KB_KEY&&id<A_KB_KEY+a->keyboard_count&&(a->screen==FILE_EDIT||a->screen==FILE_PROMPT)){insert_character(a,a->keyboard_keys[id-A_KB_KEY]);return true;}
    if(id==A_BACK&&files_screen(a->screen)){
        /* Files opens from Home, Systems, Wipe and Install: Back returns there (2026-11-10: it always went home). */
        if(a->screen==FILE_VOLUMES)reopen(a,came_from(a,HOME));
        /* The volume's page opened from a folder returns to the folder while the volume is still mounted. */
        else if(a->screen==FILE_VOLUME){if(came_from(a,FILE_VOLUMES)==FILE_BROWSER&&current_volume(a)&&current_volume(a)->mounted)reopen(a,FILE_BROWSER);else screen(a,FILE_VOLUMES);}
        else if(a->screen==FILE_BROWSER){if(strcmp(a->fs_path,"/")){parent_path(a->fs_path);reset_browser(a);start_files_job(a,J_FS_LIST);}else screen(a,came_from(a,FILE_VOLUME)==FILE_VOLUMES?FILE_VOLUMES:FILE_VOLUME);}
        else if(a->screen==FILE_CONFIRM)screen(a,a->fs_confirm_from);
        else if(a->screen==FILE_EDIT)leave_editor(a);
        else if(a->screen==FILE_DISCARD)screen(a,FILE_EDIT);
        else if(a->screen==FILE_PROMPT)screen(a,a->fs_prompt_kind==J_FS_COPY||a->fs_prompt_kind==J_FS_COPY_TREE||a->fs_prompt_kind==J_FS_RENAME||a->fs_prompt_kind==J_FS_CHMOD||a->fs_prompt_kind==J_FS_CHOWN?FILE_INFO:FILE_BROWSER);
        else{screen(a,FILE_BROWSER);start_files_job(a,J_FS_LIST);}
        return true;
    }
    switch(id){
    case A_FILES:a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    case A_FS_PREV:
        if(a->screen==FILE_VOLUMES)volume_scroll_to(a,a->volume_scroll-(VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP)/2);
        else if(a->screen==FILE_BROWSER)browser_scroll_to(a,a->browser_scroll-(FILE_LIST_BOTTOM-FILE_LIST_TOP)/2);
        return true;
    case A_FS_NEXT:
        if(a->screen==FILE_VOLUMES)volume_scroll_to(a,a->volume_scroll+(VOLUME_LIST_BOTTOM-VOLUME_LIST_TOP)/2);
        else if(a->screen==FILE_BROWSER)browser_scroll_to(a,a->browser_scroll+(FILE_LIST_BOTTOM-FILE_LIST_TOP)/2);
        return true;
    case A_FS_VOLUME:screen(a,FILE_VOLUME);return true;
    case A_FS_OPEN:if(current_volume(a)&&current_volume(a)->mounted){reset_browser(a);start_files_job(a,J_FS_LIST);}return true;
    case A_FS_MOUNT_RO:case A_FS_MOUNT_RW:{FileRequest q=file_request(a,J_FS_MOUNT);copy(q.mode,sizeof(q.mode),id==A_FS_MOUNT_RW?"rw":"ro");
        if(id==A_FS_MOUNT_RW&&(!current_volume(a)||!current_volume(a)->write_supported))return true;
        if(id==A_FS_MOUNT_RW)confirm_file_request(a,q,FILE_VOLUME);else start_files_request(a,&q);return true;}
    case A_FS_UNMOUNT:start_files_job(a,J_FS_UNMOUNT);return true;
    case A_FS_FORMAT_EXT4:case A_FS_FORMAT_VFAT:case A_FS_FORMAT_F2FS:{FileRequest q=file_request(a,J_FS_FORMAT);copy(q.mode,sizeof(q.mode),id==A_FS_FORMAT_EXT4?"ext4":(id==A_FS_FORMAT_F2FS?"f2fs":"vfat"));start_files_request(a,&q);return true;}
    case A_FS_CHECK:case A_FS_RESIZE:{FileRequest q=file_request(a,id==A_FS_CHECK?J_FS_CHECK:J_FS_RESIZE);start_files_request(a,&q);return true;}
    case A_FS_UP:if(strcmp(a->fs_path,"/")){parent_path(a->fs_path);reset_browser(a);start_files_job(a,J_FS_LIST);}return true;
    case A_FS_REFRESH:a->fs_inventory_destination=a->screen==FILE_BROWSER?FILE_BROWSER:(a->screen==FILE_VOLUME?FILE_VOLUME:FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    case A_FS_READ:open_selected_file(a);return true;
    case A_FS_EDIT:if(!a->fs_truncated&&valid_sha256(a->fs_sha256)&&files_writable(a)){a->fs_new_file=false;screen(a,FILE_EDIT);}return true;
    case A_FS_NEW_FILE:if(files_writable(a))begin_file_prompt(a,J_FS_WRITE);return true;
    case A_FS_MKDIR:if(files_writable(a))begin_file_prompt(a,J_FS_MKDIR);return true;
    case A_FS_RENAME:if(files_writable(a))begin_file_prompt(a,J_FS_RENAME);return true;
    case A_FS_COPY:if(file_is_regular(&a->fs_selected))begin_file_prompt(a,J_FS_COPY);else if(file_is_directory(&a->fs_selected)&&a->fs_copy_directories&&a->fs_selected.token[0])begin_file_prompt(a,J_FS_COPY_TREE);return true;
    case A_FS_CHMOD:case A_FS_CHOWN:if(files_writable(a)&&a->fs_selected.metadata_supported&&a->fs_selected.token[0])begin_file_prompt(a,id==A_FS_CHMOD?J_FS_CHMOD:J_FS_CHOWN);return true;
    case A_FS_RESOLVE:if(!strcmp(a->fs_selected.type,"symlink")&&a->fs_selected.token[0]){a->fs_link_resolved=false;start_files_job(a,J_FS_RESOLVE);}return true;
    case A_FS_FOLLOW:if(a->fs_link_resolved){a->fs_selected=a->fs_resolved;a->fs_link_resolved=false;open_selected_file(a);}return true;
    case A_FS_DELETE:if(files_writable(a)){FileRequest q=file_request(a,J_FS_DELETE);copy(q.path,sizeof(q.path),a->fs_selected.path);confirm_file_request(a,q,FILE_INFO);}return true;
    case A_FS_PROMPT_NEXT:accept_file_prompt(a);return true;
    case A_FS_DEST_NEXT:a->fs_destination_index=next_writable_volume(a,a->fs_destination_index);a->dirty=true;return true;
    case A_FS_SAVE:if(!a->fs_truncated&&files_writable(a)&&(a->fs_new_file||valid_sha256(a->fs_sha256))){FileRequest q=file_request(a,J_FS_WRITE);copy(q.path,sizeof(q.path),a->fs_file);copy(q.sha256,sizeof(q.sha256),a->fs_sha256);q.create=a->fs_new_file;confirm_file_request(a,q,FILE_EDIT);}return true;
    case A_FS_CONFIRM:if(a->fs_confirm_page+1>=confirmation_pages(&a->fs_pending))start_files_request(a,&a->fs_pending);return true;
    case A_FS_CONFIRM_PREV:if(a->fs_confirm_page)a->fs_confirm_page--;a->dirty=true;return true;
    case A_FS_CONFIRM_NEXT:if(a->fs_confirm_page+1<confirmation_pages(&a->fs_pending))a->fs_confirm_page++;a->dirty=true;return true;
    case A_FS_CANCEL_EDIT:leave_editor(a);return true;
    case A_FS_DISCARD:a->fs_dirty=false;a->fs_new_file=false;a->fs_text[0]=0;a->fs_length=0;screen(a,FILE_BROWSER);start_files_job(a,J_FS_LIST);return true;
    case A_FS_CARET:tap_text_cursor(a);return true;
    case A_FS_LEFT:case A_FS_RIGHT:case A_FS_UP_LINE:case A_FS_DOWN_LINE:move_text_cursor(a,id);return true;
    case A_FS_PAGE_UP:text_scroll_to(a,false,(int64_t)a->fs_scroll-text_content(text_box(a)).h/2);return true;
    case A_FS_PAGE_DOWN:text_scroll_to(a,false,(int64_t)a->fs_scroll+text_content(text_box(a)).h/2);return true;
    case A_KB_SHIFT:a->kb_shift=!a->kb_shift;a->dirty=true;return true;
    case A_KB_LANG:a->kb_uk=!a->kb_uk;a->dirty=true;return true;
    case A_KB_SYMBOLS:a->kb_symbols=!a->kb_symbols;a->dirty=true;return true;
    case A_KB_BACKSPACE:erase_character(a);return true;
    case A_KB_SPACE:insert_character(a,' ');return true;
    case A_KB_ENTER:insert_character(a,'\n');return true;
    default:return false;
    }
}
#include "flash_ui.h"
/* A stick is picked up from kernel uevents and opened read-only, with no taps needed. */
static int hotplug_open(void){
    int fd=socket(AF_NETLINK,SOCK_DGRAM|SOCK_CLOEXEC|SOCK_NONBLOCK,NETLINK_KOBJECT_UEVENT);if(fd<0)return -1;
    struct sockaddr_nl address;memset(&address,0,sizeof(address));address.nl_family=AF_NETLINK;address.nl_groups=1;
    if(bind(fd,(struct sockaddr*)&address,sizeof(address))<0){close(fd);return -1;}
    return fd;
}
static void hotplug_read(App*a,int fd){
    char data[4096];
    for(;;){
        struct sockaddr_nl from;socklen_t size=sizeof(from);ssize_t n=recvfrom(fd,data,sizeof(data)-1,0,(struct sockaddr*)&from,&size);
        if(n<=0)return;
        if(from.nl_pid)continue;
        data[n]=0;bool block=false,usb=false;
        for(char*p=data;p<data+n;p+=strlen(p)+1){if(!strcmp(p,"SUBSYSTEM=power_supply"))a->battery_refresh=true;else if(!strcmp(p,"SUBSYSTEM=block"))block=true;else if(!strncmp(p,"DEVPATH=",8)&&strstr(p,"/usb"))usb=true;}
        if(block&&usb){a->hotplug_pending=true;a->hotplug_at=a->now+700;a->hotplug_retries=0;}
    }
}
/* Each mounted stick with a suitable filesystem offers itself as a backup destination. */
static void usb_repositories(App*a,const Json*j){
    char selected[PATH_LEN];copy(selected,sizeof(selected),a->repositories[a->store]);a->repository_count=a->repository_base;
    int list=json_get(j,0,"volumes");
    if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list)&&a->repository_count<8;i=json_next(j,i)){
        char id[32]={0},fs[16]={0},mountpoint[PATH_LEN]={0};jstring(j,i,"id",id,sizeof(id));jstring(j,i,"fs_type",fs,sizeof(fs));jstring(j,i,"mountpoint",mountpoint,sizeof(mountpoint));
        if(!id[0]||!mountpoint[0]||(strcmp(fs,"ext4")&&strcmp(fs,"exfat")))continue;
        int n=a->repository_count++;copy(a->repository_volume[n],sizeof(a->repository_volume[n]),id);copy(a->repository_root[n],sizeof(a->repository_root[n]),mountpoint);
        copy(a->repository_fs[n],sizeof(a->repository_fs[n]),fs);snprintf(a->repositories[n],sizeof(a->repositories[n]),"%.1000s/A22xBackups",mountpoint);a->repository_read_only[n]=json_bool(j,json_get(j,i,"read_only"),1)!=0;
    }
    for(int n=a->repository_base;n<8;n++)if(n>=a->repository_count)a->repository_volume[n][0]=0;
    a->store=0;for(int n=0;n<a->repository_count;n++)if(!strcmp(a->repositories[n],selected))a->store=n;
    /* Any stick may be the one inserted now, so writing starts only from a tap on its card. */
    if(a->storage_prefers_usb&&!a->store&&a->repository_count>a->repository_base&&!repository_needs_setup(a,a->repository_base))a->store=a->repository_base;
}
static bool start_usb_eject(App*a){
    if(a->job.pid||a->preview)return false;
    char*args[8];int n=0;args[n++]=a->python;args[n++]=a->filesystem_helper;if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="usb-eject";args[n]=NULL;if(!launch_job(a,J_USB_EJECT,args))return false;a->result_return=STORAGE;return true;
}
static void complete_usb_eject(App*a,const Json*j,bool ok){
    if(!ok){char code[96]="USB_EJECT_FAILED",message[384]="Close whatever is using the stick and try again.";if(j){int error=json_get(j,0,"error");if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}files_problem(a,code,message,STORAGE);return;}
    usb_repositories(a,j);toast(a,"You can remove the USB stick now");
}
static bool start_usb_backup(App*a,int index){
    if(a->job.pid||a->preview||index<a->repository_base||index>=a->repository_count)return false;
    char*args[10];int n=0;args[n++]=a->python;args[n++]=a->filesystem_helper;if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="usb-backup";args[n++]="--volume";args[n++]=a->repository_volume[index];args[n++]="--confirm";args[n]=NULL;
    a->repository_pending=index;if(!launch_job(a,J_USB_BACKUP,args))return false;a->result_return=STORAGE;return true;
}
static void complete_usb_backup(App*a,const Json*j,bool ok){
    if(!ok){char code[96]="USB_BACKUP_FAILED",message[384]="The USB stick could not be opened for writing.";if(j){int error=json_get(j,0,"error");if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}files_problem(a,code,message,STORAGE);return;}
    a->repository_read_only[a->repository_pending]=false;a->store=a->repository_pending;a->storage_prefers_usb=true;settings_changed(a);toast(a,"Backups will be saved to the USB stick");
}
static bool start_usb_sync(App*a){
    if(a->job.pid||a->preview||!a->filesystem_helper[0])return false;
    char*args[8];int n=0;args[n++]=a->python;args[n++]=a->filesystem_helper;if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="usb-sync";args[n]=NULL;enum Screen back=a->result_return;if(!launch_job(a,J_USB_SYNC,args))return false;a->result_return=back;return true;
}
static void complete_usb_sync(App*a,const Json*j,bool ok){
    if(!ok){if(a->hotplug_retries<4){a->hotplug_retries++;a->hotplug_pending=true;a->hotplug_at=a->now+1500;}return;}
    int mounted=json_get(j,0,"mounted"),dropped=json_get(j,0,"dropped");
    bool added=mounted>=0&&j->tokens[mounted].type==J_ARRAY&&mounted+1<json_next(j,mounted),removed=dropped>=0&&j->tokens[dropped].type==J_ARRAY&&dropped+1<json_next(j,dropped);
    usb_repositories(a,j);
    if(added)toast(a,"USB stick connected");else if(removed)toast(a,"USB stick removed");
    if(removed&&!strncmp(a->volume_id,"usb-",4)&&files_screen(a->screen)){a->volume_index=-1;a->volume_id[0]=0;screen(a,FILE_VOLUMES);}
    if((added||removed)&&a->screen==FILE_VOLUMES){a->fs_inventory_destination=FILE_VOLUMES;start_files_job(a,J_FS_INVENTORY);}
}
/* Rebinding the gadget with a stick in the port drops the controller out of host mode,
 * so file sharing ends as soon as the cable leaves, before a stick can arrive. */
static void usb_cable_watch(App*a){
    if(!a->device||!usb_session_open(a)){a->usb_cable_seen=false;a->usb_cable_lost_at=0;return;}
    if(a->now<a->usb_cable_check)return;
    a->usb_cable_check=a->now+400;char state[32]={0};FILE*f=fopen("/sys/class/udc/musb-hdrc.2.auto/state","r");if(!f)return;
    bool read=fgets(state,sizeof(state),f)!=NULL;fclose(f);if(!read)return;
    if(strncmp(state,"not attached",12)){a->usb_cable_seen=true;a->usb_cable_lost_at=0;return;}
    if(!a->usb_cable_seen)return;
    if(!a->usb_cable_lost_at)a->usb_cable_lost_at=a->now;
    else if(a->now-a->usb_cable_lost_at>=600&&!a->job.pid&&start_usb_job(a,J_USB_STOP)){a->usb_cable_lost_at=0;toast(a,"USB files stopped: cable removed");}
}
static void display_restore_job(App*a,int kind){enum Screen back=a->result_return;a->display_restoring=start_display_job(a,kind);a->result_return=back;}
/* The panel starts at 60 Hz with its own brightness, so saved values return after startup.
   This runs behind whatever is on screen, the password prompt included, and never opens a page. */
static void restore_display(App*a){
    if(!a->job.pid)a->display_restoring=false;
    if(!a->display_helper[0])a->restore_hz=a->restore_brightness=0;
    if(a->preview||a->job.pid||a->display_control.pid)return;
    if(a->restore_brightness&&a->display_light_known){unsigned int percent=a->restore_brightness;a->restore_brightness=0;if(percent!=a->display_percent)(void)start_display_control(a,DC_BRIGHTNESS,percent);return;}
    if(!a->restore_hz)return;
    if(!a->display_known){if(a->restore_status_asked)a->restore_hz=0;else{a->restore_status_asked=true;display_restore_job(a,J_DISPLAY_STATUS);}return;}
    unsigned int hz=a->restore_hz;a->restore_hz=0;
    bool allowed=a->display_supported&&a->display_service&&((hz==60&&a->display_60)||(hz==90&&a->display_90));
    if(a->display_hz!=hz&&allowed){a->display_requested_hz=hz;display_restore_job(a,J_DISPLAY_SET);}
}
/* The page behind becomes the frosted picture, so nothing readable stays on the panel. */
static void lock_clear_secrets(App*a);
/* Waking always starts from the lock screen, not from a password left half typed. */
static void lock_restart(App*a){
    int kind=a->fs_prompt_kind;memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->lock_note[0]=0;a->fs_prompt_kind=0;
    screen(a,kind==J_LOCK_ACCESS?ACCESS_REQUEST:LOCKSCREEN);a->lock_frame_pending=true;
}
static void lock_show(App*a){
    enum Screen back=a->screen==SPLASH?HOME:(a->screen==POWER_MENU?a->menu_return:a->screen);bool blanked=a->display_blank_known&&a->display_blanked;
    /* A password being set or changed does not come back after the lock. */
    if(back==FILE_PROMPT&&lock_kind(a->fs_prompt_kind)){lock_clear_secrets(a);a->fs_prompt[0]=0;a->fs_prompt_kind=0;back=TOOLS;}
    a->locked=false;a->screen=back;render(a);a->locked=true;lock_frost(a);
    a->lock_return=back;a->lock_kept_kind=0;a->lock_kept[0]=0;
    if(back==FILE_PROMPT&&!lock_kind(a->fs_prompt_kind)){a->lock_kept_kind=a->fs_prompt_kind;copy(a->lock_kept,sizeof(a->lock_kept),a->fs_prompt);}
    a->fs_prompt_kind=0;a->fs_prompt[0]=0;a->lock_drag=0;a->lock_note[0]=0;transition_cancel(a);
    char path[192];snprintf(path,sizeof(path),"%s/granted",access_dir);(void)unlink(path);
    screen(a,LOCKSCREEN);a->lock_frame_pending=blanked;
}
/* A waiting computer shows as a request over whatever is open; if it gives up, so does the page. */
static void access_poll(App*a){
    if(!a->lock_enabled||!a->access_mode||a->now-a->access_checked<400)return;
    a->access_checked=a->now;char found[64]={0},path[256];DIR*d=opendir(access_dir);
    if(d){struct dirent*e;while((e=readdir(d)))if(!strncmp(e->d_name,"request-",8)&&strlen(e->d_name)<sizeof(found)&&(!found[0]||strstr(e->d_name,a->access_id[0]?a->access_id:"\1")))copy(found,sizeof(found),e->d_name);closedir(d);}
    if(a->access_id[0]){
        if(!strstr(found,a->access_id))access_close(a);
        else if(a->screen!=ACCESS_REQUEST&&!(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_LOCK_ACCESS)){a->access_return=a->screen;screen(a,ACCESS_REQUEST);}
        return;
    }
    if(!found[0])return;
    snprintf(path,sizeof(path),"%s/%s",access_dir,found);int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return;
    char data[256];ssize_t n=read(fd,data,sizeof(data)-1);close(fd);if(n<=0)return;data[n]=0;
    JsonToken t[16];Json j;if(json_parse(&j,data,t,16)<1)return;
    jstring(&j,0,"id",a->access_id,sizeof(a->access_id));jstring(&j,0,"channel",a->access_channel,sizeof(a->access_channel));jstring(&j,0,"peer",a->access_peer,sizeof(a->access_peer));
    a->access_return=a->screen;a->access_kind=a->fs_prompt_kind;copy(a->access_prompt,sizeof(a->access_prompt),a->fs_prompt);a->last_activity=a->now;screen(a,ACCESS_REQUEST);
}
/* The kernel stops drawing before this returns, and the bar goes on from the same place. */
#define SPLASH_SYSFS "/sys/module/mediatek_drm/parameters/"
static void splash_takeover(App*a){
    char value[16]={0};int fd=open(SPLASH_SYSFS "boot_splash_percent",O_RDONLY|O_CLOEXEC);if(fd>=0){if(read(fd,value,sizeof(value)-1)>0)a->splash_from=(unsigned int)strtoul(value,NULL,10);close(fd);}
    fd=open(SPLASH_SYSFS "boot_splash_stop",O_WRONLY|O_CLOEXEC);if(fd>=0){if(write(fd,"1",1)<0){}close(fd);}
    a->splash_from=a->splash_from>95?95:a->splash_from;a->splash_percent=a->splash_from;
}
static void hotplug_tick(App*a){
    restore_display(a);
    android_plan_tick(a);
    if(a->display_blank_known&&a->display_blanked&&a->screen!=SPLASH){
        if(a->screen==POWER_MENU&&a->locked&&!a->power_held){a->screen=a->menu_return;a->dirty=true;}
        if(a->lock_enabled&&a->lock_mode==2&&!a->locked)lock_show(a);
        else if(a->locked&&lock_prompting(a))lock_restart(a);
    }
    access_poll(a);
    adb_poll(a);
    sideload_poll(a);
    reset_request_poll(a);
    android_log_poll(a);
    /* The restart asked for after the last installer: it counts down on the result page, and leaving the page stops it. */
    if(a->zip_restart_at){
        if(a->screen!=RESULT)a->zip_restart_at=0;
        else if(a->now>=a->zip_restart_at){
            /* A read that runs in the background is waited for: the restart follows it. */
            if(!a->job.pid){
                a->zip_restart_at=0;
                if(a->allow_power&&!a->systems_reboot_blocked&&!a->install.reboot_blocked){if(a->settings_dirty)settings_save(a,true);a->power_intent=200;a->exit_code=200;a->running=false;}
                a->dirty=true;
            }
        }else{unsigned int left=(unsigned int)((a->zip_restart_at-a->now+999)/1000);if(left!=a->zip_restart_shown){a->zip_restart_shown=left;a->dirty=true;}}
    }
    screenshot_poll(a);
    if(a->battery_refresh){a->battery_refresh=false;update_battery(a);a->dirty=true;}
    wifi_state_poll(a);
    if(a->toast_until&&a->now>=a->toast_until){a->toast_until=0;a->dirty=true;}
    usb_cable_watch(a);
    if(!a->hotplug_pending||a->now<a->hotplug_at||a->job.pid||a->screen==SPLASH)return;
    a->hotplug_pending=false;
    if(a->usb_known&&usb_session_open(a)){if(start_usb_job(a,J_USB_STOP))toast(a,"Insert the USB stick again");return;}
    (void)start_usb_sync(a);
}
/* Recovery keeps preferences through settings.py. Its exit codes: 0 done, 1 no,
 * 2 error, 3 kept in RAM only. */
static const char*settings_helper="/opt/a22x-recovery/settings.py";
static int settings_run(App*a,char*const*args,char*out,size_t size){
    int pipefd[2];if(out&&pipe2(pipefd,O_CLOEXEC)<0)return 2;
    pid_t pid=fork();if(pid<0){if(out){close(pipefd[0]);close(pipefd[1]);}return 2;}
    if(!pid){if(out&&dup2(pipefd[1],STDOUT_FILENO)<0)_exit(2);(void)close_range(3,~0u,0);unsetenv("A22X_RECOVERY_LOCK_FD");execv(a->python,args);_exit(2);}
    if(out){close(pipefd[1]);size_t used=0;for(;;){ssize_t n=read(pipefd[0],out+used,size-1-used);if(n<=0)break;used+=(size_t)n;if(used>=size-1)break;}out[used]=0;close(pipefd[0]);}
    int status=2;waitpid(pid,&status,0);return WIFEXITED(status)?WEXITSTATUS(status):2;
}
static void settings_load(App*a){
    if(!a->device)return;
    char data[768],*args[]={a->python,"-B",(char*)settings_helper,"status",NULL};if(settings_run(a,args,data,sizeof(data))){copy(a->status,sizeof(a->status),"Settings could not be read. Defaults are shown, and nothing is saved.");a->successful=false;return;}
    JsonToken t[64];Json j;if(json_parse(&j,data,t,64)<1)return;
    char code[8]={0};jstring(&j,0,"language",code,sizeof(code));for(unsigned int n=0;n<LANGUAGE_COUNT;n++)if(!strcmp(code,language_codes[n]))ui_language=n;
    theme_files_scan(); /* The settings helper has just brought imported themes to RAM. */
    char theme[24]={0};jstring(&j,0,"theme",theme,sizeof(theme));(void)theme_select(a,theme);
    char scheme[32]={0};jstring(&j,0,"terminal",scheme,sizeof(scheme));(void)terminal_select(scheme);
    char storage[8]={0};jstring(&j,0,"storage",storage,sizeof(storage));a->storage_prefers_usb=!strcmp(storage,"usb");
    a->saved_hz=a->restore_hz=(unsigned int)json_uint(&j,json_get(&j,0,"refresh"),0);a->saved_brightness=a->restore_brightness=(unsigned int)json_uint(&j,json_get(&j,0,"brightness"),0);
    a->vibration=(unsigned int)json_uint(&j,json_get(&j,0,"vibration"),2);if(a->vibration>3)a->vibration=2;
    uint64_t timeout=json_uint(&j,json_get(&j,0,"timeout"),SCREEN_TIMEOUT_DEFAULT);a->screen_timeout_index=timeout<6?(unsigned int)timeout:SCREEN_TIMEOUT_DEFAULT;
    int value=json_get(&j,0,"utc_offset");if(value>=0)a->utc_offset=(int)strtol(j.text+j.tokens[value].start,NULL,10);
    char zone_id[64]={0};jstring(&j,0,"timezone",zone_id,sizeof(zone_id));a->recovery_tz=-1;
    for(unsigned int n=0;n<TZ_ZONES&&zone_id[0];n++)if(!strcmp(tz_zones[n].id,zone_id))a->recovery_tz=(int)n;
    apply_recovery_tz(a);
    char motion[12]={0};jstring(&j,0,"animation",motion,sizeof(motion));for(unsigned int n=0;n<ANIMATION_COUNT;n++)if(!strcmp(motion,animation_codes[n]))a->animation=n;
    a->nav_mode=(unsigned int)json_uint(&j,json_get(&j,0,"navigation"),0);if(a->nav_mode>NAV_OFF)a->nav_mode=NAV_GESTURES;
    a->torch_saved=(unsigned int)json_uint(&j,json_get(&j,0,"torch"),0);if(a->torch_saved>255)a->torch_saved=0;
    jstring(&j,0,"android_log",a->alog_seen,sizeof(a->alog_seen));
    torch_remembered(a);
    a->lock_mode=(unsigned int)json_uint(&j,json_get(&j,0,"lock"),1);if(a->lock_mode>2)a->lock_mode=1;a->access_mode=json_uint(&j,json_get(&j,0,"access"),1)!=0;
    a->lock_enabled=json_bool(&j,json_get(&j,0,"password"),0)==1;a->lock_digits=json_bool(&j,json_get(&j,0,"password_digits"),0)==1;a->locked=a->lock_enabled&&a->lock_mode>0;
}
/* Quick changes are written once, shortly after the last tap, from a detached child. */
static void settings_changed(App*a){a->settings_dirty=true;a->settings_changed_at=a->now;a->dirty=true;}
static void settings_save(App*a,bool wait);
/* Exit 3 means the settings stayed in RAM, because a terminal or an SSH session holds the storage
 * lease. They would be gone after a reboot, so the save is repeated until it reaches the disk. */
static void settings_poll(App*a){
    if(!a->settings_pid)return;
    int status=0;pid_t got=waitpid(a->settings_pid,&status,WNOHANG);if(!got)return;
    a->settings_pid=0;
    if(got>0&&WIFEXITED(status)&&WEXITSTATUS(status)==3){a->settings_dirty=true;a->settings_retry_at=a->now+SETTINGS_RETRY_MS;}
}
static void settings_flush(App*a){
    settings_poll(a);
    if(!a->settings_dirty||a->settings_pid||a->job.pid||a->pointer_down||a->now<a->settings_retry_at||a->now-a->settings_changed_at<SETTINGS_DELAY_MS)return;
    settings_save(a,false);
}
/* Every save mounts cache, so changes are gathered first; a power action must not lose them. */
static void settings_save(App*a,bool wait){
    a->settings_dirty=false;if(!a->device)return;
    char refresh[8],brightness[8];snprintf(refresh,sizeof(refresh),"%u",a->saved_hz);snprintf(brightness,sizeof(brightness),"%u",a->saved_brightness);
    char vibration[8],timeout[8],offset[16];snprintf(vibration,sizeof(vibration),"%u",a->vibration);snprintf(timeout,sizeof(timeout),"%u",a->screen_timeout_index);snprintf(offset,sizeof(offset),"%d",a->utc_offset);
    char lock[8],access[8],navigation[8];snprintf(lock,sizeof(lock),"%u",a->lock_mode);snprintf(access,sizeof(access),"%u",a->access_mode);snprintf(navigation,sizeof(navigation),"%u",a->nav_mode);
    char torch[8];snprintf(torch,sizeof(torch),"%u",a->torch_saved>255?0u:a->torch_saved);
    char*args[38]={a->python,"-B",(char*)settings_helper,"set","--navigation",navigation,"--animation",(char*)animation_codes[a->animation],"--lock",lock,"--access",access,"--language",(char*)language_codes[ui_language],"--theme",(char*)ui_theme->code,"--terminal",(char*)ui_terminal->code,"--storage",a->storage_prefers_usb?"usb":"sd","--refresh",refresh,"--brightness",brightness,"--vibration",vibration,"--timeout",timeout,"--utc-offset",offset,"--timezone",recovery_zone(a)?(char*)tz_zones[a->recovery_tz].id:"","--torch",torch,"--android-log",a->alog_seen,NULL};
    pid_t pid=fork();if(pid<0)return;
    if(!pid){(void)close_range(3,~0u,0);unsetenv("A22X_RECOVERY_LOCK_FD");execv(a->python,args);_exit(2);}
    if(wait)waitpid(pid,NULL,0);else a->settings_pid=pid;
}
static bool lock_secret_file(const char*path,const char*secret){
    int fd=open(path,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);if(fd<0)return false;
    bool written=write(fd,secret,strlen(secret))==(ssize_t)strlen(secret);close(fd);if(!written)unlink(path);return written;
}
/* Secrets reach the helper through private files, never through its arguments. */
static int lock_call(App*a,const char*operation,const char*secret,const char*old){
    char path[128],old_path[128];const char*base=getenv("A22X_SETTINGS_RAM")?"/tmp":"/run/a22x-recovery";snprintf(path,sizeof(path),"%s/lock-entry-%ld",base,(long)getpid());snprintf(old_path,sizeof(old_path),"%s/lock-old-%ld",base,(long)getpid());
    if(secret&&!lock_secret_file(path,secret))return 2;
    if(old&&!lock_secret_file(old_path,old)){if(secret)unlink(path);return 2;}
    char*args[10];int n=0;args[n++]=a->python;args[n++]="-B";args[n++]=(char*)settings_helper;args[n++]=(char*)operation;
    if(secret){args[n++]="--secret-file";args[n++]=path;}
    if(old){args[n++]="--old-secret-file";args[n++]=old_path;}
    args[n]=NULL;int rc=settings_run(a,args,NULL,0);if(secret)unlink(path);if(old)unlink(old_path);return rc;
}
/* A password of digits is asked on the pad. Repeating one keeps the keyboard it was typed on. */
static void lock_prompt(App*a,int kind){
    if(kind==J_LOCK_SET)a->lock_keyboard=false;else if(kind!=J_LOCK_CONFIRM)a->lock_keyboard=!a->lock_digits;
    a->fs_prompt_kind=kind;a->fs_prompt[0]=0;a->prompt_cursor=0;a->prompt_scroll=0;screen(a,FILE_PROMPT);
}
/* A write that failed is tried again with the next save, so a password is not lost at reboot. */
static void lock_result(App*a,int rc,const char*done){copy(a->status,sizeof(a->status),rc==0?done:(rc==3?"Saved until reboot: no writable storage":"The password could not be changed"));a->successful=rc==0;if(rc==3)settings_changed(a);}
/* Motor pulses. The motor needs some time to spin up, so length carries most of the strength. */
static const struct { unsigned short magnitude,length; } vibration_levels[4]={{0,0},{0xa000,35},{0xd000,50},{0xffff,70}};
static void haptic(App*a,unsigned int factor){
    if(!a->device||!a->vibration||a->haptic_fd<0)return;
    if(!a->haptic_fd){
        a->haptic_fd=-1;a->haptic_id=-1;
        for(int n=0;n<16;n++){
            char path[96],name[64]={0};snprintf(path,sizeof(path),"/sys/class/input/event%d/device/name",n);FILE*f=fopen(path,"r");if(!f)continue;
            bool found=fgets(name,sizeof(name),f)&&strstr(name,"haptic");fclose(f);if(!found)continue;
            snprintf(path,sizeof(path),"/dev/input/event%d",n);int fd=open(path,O_RDWR|O_CLOEXEC);if(fd>0)a->haptic_fd=fd;break;
        }
        if(a->haptic_fd<0)return;
    }
    struct ff_effect effect;memset(&effect,0,sizeof(effect));effect.type=FF_RUMBLE;effect.id=(short)a->haptic_id;effect.replay.length=(unsigned short)(vibration_levels[a->vibration].length*factor);
    effect.u.rumble.strong_magnitude=vibration_levels[a->vibration].magnitude;
    if(ioctl(a->haptic_fd,EVIOCSFF,&effect)<0)return;
    a->haptic_id=effect.id;struct input_event play;memset(&play,0,sizeof(play));play.type=EV_FF;play.code=(unsigned short)effect.id;play.value=1;
    if(write(a->haptic_fd,&play,sizeof(play))<0)a->haptic_id=-1;
}
static void lock_clear_secrets(App*a){memset(a->lock_old,0,sizeof(a->lock_old));memset(a->lock_new,0,sizeof(a->lock_new));a->lock_flow=LF_NONE;}
static void lock_done(App*a){lock_clear_secrets(a);a->lock_note[0]=0;a->fs_prompt_kind=0;screen(a,TOOLS);}
static void lock_wrong(App*a){
    a->lock_failures++;a->lock_shake_until=a->now+400;haptic(a,3);copy(a->lock_note,sizeof(a->lock_note),"Wrong password");
    if(a->lock_failures>=5)a->lock_wait_until=a->now+30000u*(a->lock_failures-4);
    a->dirty=true;
}
static void access_file(const char*name){char path[192];snprintf(path,sizeof(path),"%s/%s",access_dir,name);int fd=open(path,O_WRONLY|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);if(fd>=0)close(fd);}
/* Back to whatever the request interrupted, typed text included. */
static void access_close(App*a){
    a->access_id[0]=0;a->lock_note[0]=0;a->fs_prompt_kind=a->access_kind;copy(a->fs_prompt,sizeof(a->fs_prompt),a->access_prompt);memset(a->access_prompt,0,sizeof(a->access_prompt));
    a->prompt_cursor=strlen(a->fs_prompt);screen(a,a->access_return);
}
static void lock_open(App*a){
    a->locked=false;a->lock_opened=true;a->lock_note[0]=0;a->fs_prompt_kind=a->lock_kept_kind;copy(a->fs_prompt,sizeof(a->fs_prompt),a->lock_kept);memset(a->lock_kept,0,sizeof(a->lock_kept));
    a->prompt_cursor=strlen(a->fs_prompt);screen(a,a->lock_return);
}
/* One step of a password flow. Every step that changes protection first proves the current password. */
static void lock_submit(App*a){
    int kind=a->fs_prompt_kind;char entered[128];copy(entered,sizeof(entered),a->fs_prompt);
    memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->prompt_scroll=0;a->lock_note[0]=0;a->dirty=true;
    if(kind==J_LOCK_SET){
        if(strlen(entered)<4)copy(a->lock_note,sizeof(a->lock_note),"Use at least 4 characters");
        else{copy(a->lock_new,sizeof(a->lock_new),entered);lock_prompt(a,J_LOCK_CONFIRM);}
    }else if(kind==J_LOCK_CONFIRM){
        if(strcmp(entered,a->lock_new)){lock_prompt(a,J_LOCK_SET);copy(a->lock_note,sizeof(a->lock_note),"The passwords do not match");}
        else{int rc=lock_call(a,"set-password",a->lock_new,a->lock_flow==LF_CHANGE?a->lock_old:NULL);if(rc==0||rc==3){a->lock_enabled=true;a->lock_digits=strspn(a->lock_new,"0123456789")==strlen(a->lock_new);}lock_result(a,rc,"Password saved");lock_done(a);}
    }else if(a->now<a->lock_wait_until){/* The note on the page counts down. */
    }else if(lock_call(a,"verify",entered,NULL))lock_wrong(a);
    else{
        a->lock_failures=0;a->lock_wait_until=0;
        if(kind==J_LOCK_ENTER)lock_open(a);
        else if(kind==J_LOCK_ACCESS){access_file("granted");access_close(a);}
        else if(a->lock_flow==LF_CHANGE){copy(a->lock_old,sizeof(a->lock_old),entered);lock_prompt(a,J_LOCK_SET);}
        else if(a->lock_flow==LF_REMOVE){int rc=lock_call(a,"clear-password",entered,NULL);if(rc==0||rc==3)a->lock_enabled=false;lock_result(a,rc,"Password removed");lock_done(a);}
        else if(a->lock_flow==LF_MODE){a->lock_mode=a->lock_pending_mode;settings_changed(a);lock_done(a);}
        else if(a->lock_flow==LF_ACCESS){a->access_mode=!a->access_mode;settings_changed(a);lock_done(a);}
    }
    memset(entered,0,sizeof(entered));
}
static void lock_cancel(App*a){
    int kind=a->fs_prompt_kind;memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->lock_note[0]=0;
    if(kind==J_LOCK_ENTER)screen(a,LOCKSCREEN);else if(kind==J_LOCK_ACCESS)screen(a,ACCESS_REQUEST);else lock_done(a);
}
/* A page seen before, opened again with the read that keeps it current. */
static void reopen(App*a,enum Screen s){
    screen(a,s);if(a->screen!=s)return;
    if(s==SYSTEMS)start_systems_job(a,J_SYS_STATUS);
    else if(s==FILE_BROWSER)start_files_job(a,J_FS_LIST);
    /* Root is read again: what was flashed meanwhile ("Remove root") changed what BOOT carries. */
    else if(s==ROOT_PAGE){a->root_known=false;start_root_job(a,J_ROOT_LIST);}
    /* The card is read again: it may have been partitioned, mounted or pulled out meanwhile. */
    else if(s==CARD_PAGE){a->card_known=false;start_card_job(a,J_CARD_LIST);}
}
/* Settings back to their defaults (2026-11-11). The language and the time zone stay: they say who
 * uses the phone, not how the recovery behaves. So do the password, the lock mode and the access
 * from a computer: a reset of looks must neither lock the owner out nor open the phone. Brightness
 * and refresh rate are no longer kept, so the next start of the recovery comes up with its own. */
static void settings_reset(App*a){
    (void)theme_select(a,themes[0].code);(void)terminal_select("auto");a->storage_prefers_usb=false;
    a->vibration=2;a->screen_timeout_index=SCREEN_TIMEOUT_DEFAULT;a->animation=0;a->nav_mode=NAV_GESTURES;
    a->saved_hz=a->saved_brightness=0;
    settings_changed(a);toast(a,"The settings are back to their defaults");
}
/* A finished backup is renamed or deleted on its page; the settings are reset on theirs. */
static bool manage_action(App*a,int id){
    if(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_BACKUP_NAME){
        bool done=(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER)&&backup_name_ok(a,a->fs_prompt);
        if(id==A_BACKUP_NAME_FIELD||((id==A_FS_PROMPT_NEXT||id==A_KB_ENTER)&&!done))return true;
        if(id!=A_BACK&&!done)return false; /* The keys of the keyboard. */
        if(done)copy(a->backup_new_name,sizeof(a->backup_new_name),a->fs_prompt);
        memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->fs_prompt_kind=0;screen(a,DETAIL);
        if(done&&!start_job(a,J_BACKUP_RENAME))memset(a->backup_new_name,0,sizeof(a->backup_new_name));
        return true;
    }
    if(id==A_SETTINGS_RESET&&a->screen==TOOLS){if(ui_free(a))settings_reset(a);return true;}
    if(a->screen!=DETAIL||a->job.pid||a->selected_backup<0||a->selected_backup>=a->backup_count)return false;
    if(id==A_BACKUP_RENAME){
        a->fs_prompt_kind=J_BACKUP_NAME;copy(a->fs_prompt,sizeof(a->fs_prompt),a->backups[a->selected_backup].name);
        a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;screen(a,FILE_PROMPT);return true;
    }
    if(id==A_BACKUP_DELETE){(void)start_job(a,J_BACKUP_DELETE);return true;}
    return false;
}
static void action_run(App*a,int id){
    if(a->screen==POWER_MENU){
        if(id==A_TORCH){haptic(a,1);torch_set(a,!a->torch_on,a->torch_level);return;}
        if(id==A_TORCH_DOWN||id==A_TORCH_UP){haptic(a,1);torch_step(a,id==A_TORCH_UP?1:-1);return;}
        int intent=id==A_REBOOT?200:(id==A_SHUTDOWN?201:(id==A_REBOOT_RECOVERY?202:(id==A_REBOOT_DOWNLOAD?203:0)));
        if(!intent){a->screen=a->menu_return;a->button_count=0;a->focus=-1;a->dirty=true;return;}
        if(!a->allow_power||a->job.pid||(intent==200&&(a->systems_reboot_blocked||a->install.reboot_blocked)))return;
        if(a->settings_dirty)settings_save(a,true);
        a->power_intent=intent;a->exit_code=intent;a->running=false;return;
    }
    haptic(a,1);
    if(a->screen==ACCESS_REQUEST){
        if(id==A_ACCESS_ALLOW)lock_prompt(a,J_LOCK_ACCESS);
        else if(id==A_ACCESS_DENY){char name[64];snprintf(name,sizeof(name),"denied-%s.json",a->access_id);access_file(name);access_close(a);}
        return;
    }
    if(lock_prompting(a)){if(id==A_BACK)lock_cancel(a);else if(id==A_LOCK_KEYBOARD){a->lock_keyboard=!a->lock_keyboard;a->dirty=true;}else if(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER)lock_submit(a);else(void)files_action(a,id);return;}
    if(a->locked)return;
    /* The flashlight waits for nothing: it is no job and touches no storage. */
    if(id==A_TORCH){torch_set(a,!a->torch_on,a->torch_level);return;}
    if(id==A_TORCH_DOWN||id==A_TORCH_UP){torch_step(a,id==A_TORCH_UP?1:-1);return;}
    if(id==A_TORCH_ON||id==A_TORCH_OFF){if((id==A_TORCH_ON)!=a->torch_on)torch_set(a,id==A_TORCH_ON,a->torch_level);return;}
    /* Stopping the restart that counts down after installers waits for nothing either. */
    if(id==A_ZIP_STAY){a->zip_restart_at=0;a->dirty=true;return;}
    /* A finger on the bar takes the level under it; chosen with the keys, the bar is one step brighter. */
    if(id==A_TORCH_BAR){if(a->tapped)torch_choose(a,torch_bar_level(a,a->pressed_rect,a->pointer_x));else torch_step(a,1);return;}
    /* Going back and local settings need no job; anything else waits for a read in progress. */
    bool local=id==A_BACK||(id>=A_NAV_MODE&&id<A_NAV_MODE+3)||id==A_LANGUAGE||id==A_THEME||id==A_HOME_THEME||id==A_TERMINAL_THEME||id==A_ANIMATIONS||(id>=A_ANIMATION_ITEM&&id<A_ANIMATION_ITEM+(int)ANIMATION_COUNT)||(id>=A_TERMINAL_THEME_ITEM&&id<A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+THEME_FILE_MAX)||(id>=A_LANGUAGE_ITEM&&id<A_LANGUAGE_ITEM+LANGUAGE_COUNT)||(id>=A_THEME_ITEM&&id<A_THEME_ITEM+THEME_COUNT+THEME_FILE_MAX)||(id>=A_VIBRATION&&id<A_VIBRATION+4)||id==A_TZ_DOWN||id==A_TZ_UP||(id>=A_TIMEOUT&&id<=A_TIMEOUT+5);
    if(a->job.pid&&ui_free(a)&&!local&&id!=A_SOURCE_CANCEL&&!a->preview){a->queued_action=id;a->queued_screen=a->screen;return;}
    if(source_action(a,id))return;
    if(id>=A_VIBRATION&&id<A_VIBRATION+4){a->vibration=(unsigned int)(id-A_VIBRATION);haptic(a,1);settings_changed(a);return;}
    if(id>=A_NAV_MODE&&id<A_NAV_MODE+3){a->nav_mode=(unsigned int)(id-A_NAV_MODE);settings_changed(a);return;}
    if(id==A_LANGUAGE){screen(a,LANGUAGE);return;}
    if(id==A_THEME||id==A_HOME_THEME){a->theme_from_home=id==A_HOME_THEME;a->page_scroll=0;screen(a,THEME);return;}
    if(id==A_TERMINAL_THEME){a->page_scroll=0;screen(a,TERMINAL_THEME);return;}
    if(id==A_ANIMATIONS){screen(a,ANIMATIONS);return;}
    if(id>=A_ANIMATION_ITEM&&id<A_ANIMATION_ITEM+(int)ANIMATION_COUNT){a->animation=(unsigned int)(id-A_ANIMATION_ITEM);settings_changed(a);a->dirty=true;return;}
    if(id==A_BACK&&a->screen==ANIMATIONS){screen(a,TOOLS);return;}
    if(id>=A_TERMINAL_THEME_ITEM&&id<A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+THEME_FILE_MAX){
        int n=id-A_TERMINAL_THEME_ITEM;if(n<TERMINAL_SCHEME_COUNT)ui_terminal=&terminal_schemes[n];else if((unsigned int)(n-TERMINAL_SCHEME_COUNT)<theme_file_count)ui_terminal=&theme_files[n-TERMINAL_SCHEME_COUNT].scheme;
        settings_changed(a);return;
    }
    if(id==A_BACK&&a->screen==TERMINAL_THEME){screen(a,TOOLS);return;}
    if(id>=A_THEME_ITEM&&id<A_THEME_ITEM+THEME_COUNT+THEME_FILE_MAX){
        int n=id-A_THEME_ITEM;if(n<THEME_COUNT)theme_apply(a,(unsigned int)n);else if((unsigned int)(n-THEME_COUNT)<theme_file_count)theme_apply_file(a,&theme_files[n-THEME_COUNT]);
        settings_changed(a);return;
    }
    if(id==A_BACK&&a->screen==THEME){screen(a,a->theme_from_home?HOME:TOOLS);return;}
    if(id>=A_LANGUAGE_ITEM&&id<A_LANGUAGE_ITEM+LANGUAGE_COUNT){ui_language=(unsigned int)(id-A_LANGUAGE_ITEM);settings_changed(a);screen(a,TOOLS);return;}
    if(id==A_BACK&&a->screen==LANGUAGE){screen(a,TOOLS);return;}
    if(id==A_TZ_DOWN||id==A_TZ_UP){
        int offset=recovery_offset_now(a)+(id==A_TZ_UP?30:-30);if(offset<-720)offset=-720;if(offset>840)offset=840;
        a->utc_offset=offset;a->recovery_tz=-1;apply_recovery_tz(a);settings_changed(a);return;}
    if(id==A_TZ_PICK){a->tz_for_recovery=true;a->systems_tz_region=recovery_zone(a)?tz_zones[a->recovery_tz].region:0u;a->page_scroll=0;screen(a,SYSTEMS_TIMEZONE);return;}
    if(id==A_JOB_CANCEL){a->job_cancel_asked=true;a->dirty=true;return;}
    if(id==A_JOB_KEEP){a->job_cancel_asked=false;a->dirty=true;return;}
    if(id==A_JOB_CANCEL_YES){if(a->job.pid&&job_cancellable(a->job.kind)){kill(a->job.pid,SIGTERM);a->job_cancelled=true;}a->job_cancel_asked=false;a->dirty=true;return;}
    if(id==A_LOCK_SET){a->lock_flow=a->lock_enabled?LF_CHANGE:LF_SET;lock_prompt(a,a->lock_enabled?J_LOCK_OLD:J_LOCK_SET);return;}
    if(id==A_LOCK_CLEAR){a->lock_flow=LF_REMOVE;lock_prompt(a,J_LOCK_OLD);return;}
    if(id>=A_LOCK_MODE&&id<A_LOCK_MODE+3){if((unsigned int)(id-A_LOCK_MODE)!=a->lock_mode){a->lock_pending_mode=(unsigned int)(id-A_LOCK_MODE);a->lock_flow=LF_MODE;lock_prompt(a,J_LOCK_OLD);}return;}
    if(id==A_ACCESS_TOGGLE){a->lock_flow=LF_ACCESS;lock_prompt(a,J_LOCK_OLD);return;}
    if(terminal_action(a,id))return;
    /* Wi-Fi handles Back itself, also while it searches or joins. */
    if(wifi_action(a,id))return;
    if(adb_action(a,id))return;
    if(images_action(a,id))return;
    if(installer_action(a,id))return;
    if(packages_action(a,id))return;
    if(id==A_BACK&&a->screen==IDENTITY&&!a->job.pid){screen(a,BACKUP);return;}
    if(id==A_BACK&&a->screen==IDENTITY_RESTORE&&!a->job.pid){screen(a,IDENTITY);return;}
    if(!a->job.pid&&identity_action(a,id))return;
    if(android_plan_action(a,id))return;
    if(wipe_action(a,id))return;
    if(android_log_action(a,id))return;
    if(root_action(a,id))return;
    if(manage_action(a,id))return;
    if(card_action(a,id))return;
    if(copies_action(a,id))return;
    if(zip_action(a,id))return;
    if(samsung_action(a,id))return;
    if((id==A_CHARGER_ON||id==A_CHARGER_OFF)&&!a->job.pid){start_charger_job(a,id==A_CHARGER_ON?1:2);return;}
    if(a->job.pid&&!(id==A_BACK&&(a->job.kind==J_DISPLAY_SET||a->job.kind==J_DISPLAY_STATUS))){
        /* Back pressed while the page is still being read is not lost: it follows the read (2026-11-10:
           it was dropped, the arrow answered with a buzz and nothing else). */
        if(id==A_BACK&&ui_free(a)&&!a->preview){a->queued_action=A_BACK;a->queued_screen=a->screen;}
        return;
    }
    if(theme_action(a,id))return;
    if(flash_action(a,id))return;
    if(locale_action(a,id))return;
    if(shells_action(a,id))return;
    if(systems_action(a,id))return;
    if(install_action(a,id))return;
    if(files_action(a,id))return;
    if(id>=A_PROFILE&&id<A_PROFILE+2){copy(a->profile,sizeof(a->profile),(const char*[]){"linux","recovery"}[id-A_PROFILE]);a->dirty=true;return;}
    if(id>=A_STORE&&id<A_STORE+a->repository_count){int index=id-A_STORE;if(repository_needs_setup(a,index))(void)start_usb_backup(a,index);else{a->store=index;a->storage_prefers_usb=a->repository_volume[index][0]!=0;settings_changed(a);a->dirty=true;}return;}
    if(id==A_USB_EJECT){(void)start_usb_eject(a);return;}
    if(id>=A_BACKUP_ITEM&&id<A_BACKUP_ITEM+a->backup_count){a->selected_backup=id-A_BACKUP_ITEM;screen(a,DETAIL);return;}
    switch(id){
    case A_BACK:if(a->screen==DETAIL)screen(a,LIST);else if(a->screen==PLAN)screen(a,a->plan_restore||a->plan_wipe?DETAIL:BACKUP);else if(a->screen==PREPARE_FILESYSTEM){int prepared=a->prepare_job;a->prepare_job=J_NONE;a->prepare_unmounting=false;screen(a,prepared==J_BACKUP_PLAN?BACKUP:DETAIL);}else if(a->screen==POWER_CONFIRM)screen(a,POWER);else if(a->screen==OS_MENU)screen(a,HOME);else if(a->screen==DISPLAY_SETTINGS)screen(a,TOOLS);else reopen(a,came_from(a,HOME));break;
    case A_BACKUP:if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard the edited text before leaving the editor.",FILE_EDIT);break;}if(strcmp(a->profile,"linux")&&strcmp(a->profile,"recovery"))copy(a->profile,sizeof(a->profile),"linux");screen(a,BACKUP);
        /* Whether userdata is Android's decides what the page offers first (2026-11-11: it offered a Linux backup on a phone with Android). */
        if(!a->systems_known&&a->distro_helper[0]&&!a->preview&&(a->device||a->fixture[0]))(void)start_systems_job(a,J_SYS_STATUS);
        break;
    case A_RESTORE:case A_VERIFY:
        a->page_scroll=0;
        /* 2026-11-11: two categories. Android's copies are looked for on every mounted card; with no
           storage for Linux snapshots the page opens there, so a copy on a stick is found at once. */
        if(a->list_category==1||(a->device&&!a->preview&&access(a->repositories[a->store],F_OK))){a->list_for_verify=id==A_VERIFY;open_copies(a);break;}
        /* Restore opens without backup storage too: a partition image needs none. */
        if(id==A_RESTORE&&a->device&&!a->preview&&access(a->repositories[a->store],F_OK)){a->list_for_verify=a->install.pick_backup=false;a->backup_count=a->list_page=0;copy(a->message,sizeof(a->message),"Choose an SD card or USB stick with backups, or restore a partition image.");screen(a,LIST);break;}
        if(!destination_ready(a))break;
        a->list_for_verify=id==A_VERIFY;start_job(a,J_LIST);break;
    case A_STORAGE:screen(a,STORAGE);if(a->storage_helper[0])start_job(a,J_STORAGE_INVENTORY);break;case A_SYSTEM:a->page_scroll=0;screen(a,SYSTEM);start_sysinfo_job(a);break;case A_POWER:screen(a,POWER);break;
    case A_MOUNT:start_job(a,J_MOUNT);break;case A_UMOUNT:start_job(a,J_UMOUNT);break;case A_REFRESH:start_job(a,J_INVENTORY);break;
    case A_REVIEW_BACKUP:if(!destination_ready(a))break;a->plan_can_execute=false;a->token[0]=0;a->backup_compact=!strcmp(a->profile,"linux");if(a->backup_compact)begin_linux_prepare(a,J_BACKUP_PLAN);else start_job(a,J_BACKUP_PLAN);break;
    case A_REVIEW_RESTORE:a->plan_can_execute=false;a->token[0]=0;if(backup_is_linux_v2(&a->backups[a->selected_backup]))begin_linux_prepare(a,J_RESTORE_PLAN);else start_job(a,J_RESTORE_PLAN);break;
    case A_REVIEW_WIPE:if(backup_is_linux_v2(&a->backups[a->selected_backup]))begin_linux_prepare(a,J_WIPE_PLAN);break;
    case A_PREPARE_REFRESH:if(a->prepare_job!=J_NONE){a->prepare_known=false;a->prepare_unmounting=false;a->fs_inventory_destination=PREPARE_FILESYSTEM;start_files_job(a,J_FS_INVENTORY);}break;
    case A_PREPARE_CONTINUE:
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or discard the editor buffer before preparing an offline operation.",FILE_EDIT);break;}
        if(a->prepare_job==J_NONE||!a->prepare_known||!a->prepare_token[0])break;
        if(a->prepare_mounted){if(!a->prepare_owned)break;FileRequest q={.kind=J_FS_UNMOUNT};copy(q.volume,sizeof(q.volume),"userdata");copy(q.volume_token,sizeof(q.volume_token),a->prepare_token);a->prepare_unmounting=true;start_files_request(a,&q);}
        else start_job(a,a->prepare_job);
        break;
    case A_VERIFY_ONE:if(start_job(a,J_VERIFY))a->result_return=DETAIL;break; /* Back to the backup that was checked. */
    case A_CONFIRM:if(a->plan_can_execute)start_job(a,a->plan_wipe?J_WIPE:(a->plan_restore?J_RESTORE:J_BACKUP));break;
    case A_DONE:a->systems_remove_offer=false;a->zip_ran=false;a->zip_restart_at=0;screen(a,a->result_return);if(a->result_return==DISPLAY_SETTINGS){a->display_note[0]=0;start_display_job(a,J_DISPLAY_STATUS);}else if(a->result_return==PREPARE_FILESYSTEM){a->prepare_known=false;a->fs_inventory_destination=PREPARE_FILESYSTEM;start_files_job(a,J_FS_INVENTORY);}else if(a->result_return==USB_FILES)start_usb_job(a,J_USB_STATUS);else if(a->result_return==CARD_PAGE&&a->screen==CARD_PAGE){a->card_known=false;start_card_job(a,J_CARD_LIST);}break;
    case A_USB_FILES:case A_USB_REFRESH:
        if(id==A_USB_FILES)a->page_scroll=0;
        a->usb_volumes_known=false;
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard edited text before opening USB file sharing.",FILE_EDIT);break;}
        a->usb_known=false;if(id==A_USB_FILES)a->usb_read_only=true;screen(a,USB_FILES);start_usb_job(a,J_USB_STATUS);break;
    case A_USB_VOLUME_RO...A_USB_VOLUME_RO+USB_VOLUME_ROWS-1:case A_USB_VOLUME_RW...A_USB_VOLUME_RW+USB_VOLUME_ROWS-1:case A_USB_VOLUME_OFF...A_USB_VOLUME_OFF+USB_VOLUME_ROWS-1:{
        int row=id>=A_USB_VOLUME_OFF?id-A_USB_VOLUME_OFF:(id>=A_USB_VOLUME_RW?id-A_USB_VOLUME_RW:id-A_USB_VOLUME_RO);if(row>=a->usb_row_count||usb_session_open(a))break;
        FileVolume*v=&a->volumes[a->usb_rows[row]];a->volume_index=a->usb_rows[row];copy(a->volume_id,sizeof(a->volume_id),v->id);copy(a->usb_want,sizeof(a->usb_want),v->id);a->usb_mounting=true;
        FileRequest q=file_request(a,id>=A_USB_VOLUME_OFF?J_FS_UNMOUNT:J_FS_MOUNT);if(id<A_USB_VOLUME_OFF)copy(q.mode,sizeof(q.mode),id>=A_USB_VOLUME_RW?"rw":"ro");
        if(!start_files_request(a,&q))a->usb_mounting=false;
        break;
    }
    case A_USB_NEXT:if(a->usb_candidate_count&&!usb_session_open(a)){a->usb_selected=(a->usb_selected+1)%a->usb_candidate_count;a->usb_read_only=true;a->dirty=true;}break;
    case A_USB_RO:a->usb_read_only=true;a->dirty=true;break;
    case A_USB_RW:if(a->usb_selected>=0&&a->usb_selected<a->usb_candidate_count&&a->usb_candidates[a->usb_selected].writable){a->usb_read_only=false;a->dirty=true;}break;
    case A_USB_START:start_usb_job(a,J_USB_START);break;
    case A_USB_STOP:start_usb_job(a,J_USB_STOP);break;
    case A_OS:screen(a,OS_MENU);break;
    case A_TIMEOUT...A_TIMEOUT+5:a->screen_timeout_index=(unsigned int)(id-A_TIMEOUT);a->last_activity=a->now;settings_changed(a);break;
    case A_DISPLAY:case A_DISPLAY_REFRESH:if(id==A_DISPLAY_REFRESH||!a->display_hz)a->display_known=false;a->display_requested_hz=0;a->display_note[0]=a->display_reason[0]=0;screen(a,DISPLAY_SETTINGS);start_display_job(a,J_DISPLAY_STATUS);break;
    case A_DISPLAY_60:case A_DISPLAY_90:{unsigned int hz=id==A_DISPLAY_60?60:90;if(a->display_known&&a->display_hz!=hz){a->saved_hz=hz;settings_changed(a);a->display_requested_hz=hz;a->display_note[0]=0;start_display_job(a,J_DISPLAY_SET);}break;}
    case A_TOOLS:case A_TOOLS_REFRESH:if(id==A_TOOLS)a->tools_scroll=0;else{a->tools_known=a->logs_export_available=a->ssh_available=a->ssh_running=a->ssh_ephemeral=false;a->ssh_keys=a->ssh_port=0;a->ssh_address[0]=a->ssh_fingerprint[0]=a->ssh_reason[0]=a->logs_reason[0]=0;}screen(a,TOOLS);start_tools_job(a,J_TOOLS_STATUS);break;
    case A_TOOLS_EXPORT:if(a->tools_known&&a->logs_export_available){a->fs_inventory_destination=TOOLS_EXPORT;screen(a,TOOLS_EXPORT);tools_select_volume(a,-1);start_files_job(a,J_FS_INVENTORY);}
        else{a->tools_export_next=true;if(!start_tools_job(a,J_TOOLS_STATUS))a->tools_export_next=false;}break;
    case A_TOOLS_DEST_NEXT:tools_select_volume(a,next_writable_volume(a,a->tools_volume_index));a->dirty=true;break;
    case A_TOOLS_EXPORT_CONFIRM:if(a->tools_known&&a->logs_export_available)start_tools_job(a,J_TOOLS_EXPORT);break;
    case A_LIST_PREV:if(a->list_page>0)a->list_page--;a->dirty=true;break;
    case A_LIST_NEXT:if((a->list_page+1)*list_rows(a)<a->backup_count)a->list_page++;a->dirty=true;break;
    case A_REBOOT:case A_SHUTDOWN:case A_REBOOT_RECOVERY:case A_REBOOT_DOWNLOAD:if(a->allow_power&&(id!=A_REBOOT||(!a->systems_reboot_blocked&&!a->install.reboot_blocked))){a->power_intent=id==A_REBOOT?200:(id==A_SHUTDOWN?201:(id==A_REBOOT_RECOVERY?202:203));screen(a,POWER_CONFIRM);}break;
    case A_POWER_CONFIRM:if(a->allow_power&&ui_free(a)&&a->power_intent>=200&&a->power_intent<=203&&(a->power_intent!=200||(!a->systems_reboot_blocked&&!a->install.reboot_blocked))){if(a->settings_dirty)settings_save(a,true);a->exit_code=a->power_intent;a->running=false;}break;
    default:break;
    }
}
/* Every tap, key and gesture ends here. Going back is marked for the trail of pages, and a read
 * started by the action belongs to the page the action ends on. */
static void action(App*a,int id){
    bool was=a->nav_backing;a->nav_backing=id==A_BACK;
    action_run(a,id);
    a->nav_backing=was;
    if(a->job_page_mark&&a->job.pid){a->job_page_mark=false;a->job_page=nav_page(a);}
}
static int button_at(App*a,int x,int y){for(int i=0;i<a->button_count;i++)if(a->buttons[i].enabled&&inside(a->buttons[i].r,x,y))return i;return -1;}
/* Page transitions. Both pages are finished frames, so a frame costs two copies of the panel,
 * not a render, and fits 90 Hz. State changes at once; only the picture catches up. */
static float ease_out(float t){float u=1.f-t;return 1.f-u*u*u;}
static float ease_in_out(float t){if(t<.5f)return 4.f*t*t*t;float u=2.f-2.f*t;return 1.f-u*u*u*.5f;}
static void transition_cancel(App*a){if(a->tr.active||a->tr.armed)a->dirty=true;a->tr.active=a->tr.armed=false;}
static void transition_arm(App*a,enum TransitionKind kind,unsigned int duration,Rect origin,int glyph,int cx,int cy){
    a->tr.kind=kind;a->tr.duration=duration;a->tr.origin=origin;a->tr.glyph=glyph;a->tr.cx=cx;a->tr.cy=cy;a->tr.blur=false;a->tr.page_from=a->tr.page_to=-1;
    memcpy(a->tr.from,a->pixels,(size_t)NW*NH*sizeof(uint32_t));a->tr.active=false;a->tr.armed=true;a->dirty=true;
}
static bool transition_soft_frames(App*a){
    if(!a->tr.soft_from)a->tr.soft_from=malloc((size_t)NW*NH*sizeof(uint32_t));
    if(!a->tr.soft_to)a->tr.soft_to=malloc((size_t)NW*NH*sizeof(uint32_t));
    return a->tr.soft_from&&a->tr.soft_to;
}
static void transition_start(App*a){
    render(a);memcpy(a->tr.to,a->pixels,(size_t)NW*NH*sizeof(uint32_t));
    /* Blur is worked out once here; each frame then only mixes finished pictures. */
    if(a->tr.kind==TR_DISSOLVE||a->tr.blur){
        if(!transition_soft_frames(a)){a->tr.blur=false;if(a->tr.kind==TR_DISSOLVE)a->tr.kind=TR_FADE;}
        else{canvas_blur(a->tr.soft_from,a->tr.from);canvas_blur(a->tr.soft_to,a->tr.to);}
    }
    a->tr.armed=false;a->tr.active=true;a->tr.started=milliseconds();a->tr.frames=0;a->tr.worst=0;
}
/* One row: the inner frame between left and right, the outer frame elsewhere, soft at both edges. */
static void transition_row(uint32_t*out,const uint32_t*outer,const uint32_t*inner,float left,float right){
    if(left<0)left=0;
    if(right>(float)NW)right=(float)NW;
    if(right<=left){memcpy(out,outer,(size_t)NW*4u);return;}
    int l=(int)left,r=(int)right;
    memcpy(out,outer,(size_t)l*4u);
    if(l==r){out[l]=canvas_blend(outer[l],inner[l],(unsigned int)((right-left)*255.f));memcpy(out+l+1,outer+l+1,(size_t)(NW-l-1)*4u);return;}
    out[l]=canvas_blend(outer[l],inner[l],(unsigned int)(((float)l+1.f-left)*255.f));
    memcpy(out+l+1,inner+l+1,(size_t)(r-l-1)*4u);
    if(r<NW){out[r]=canvas_blend(outer[r],inner[r],(unsigned int)((right-(float)r)*255.f));memcpy(out+r+1,outer+r+1,(size_t)(NW-r-1)*4u);}
}
static void transition_circle(App*a,float t){
    float cx=(float)a->tr.cx,cy=(float)a->tr.cy,wide=cx>(float)NW-cx?cx:(float)NW-cx,tall=cy>(float)NH-cy?cy:(float)NH-cy;
    float radius=ease_in_out(t)*(sqrtf(wide*wide+tall*tall)+2.f);
    for(int y=0;y<NH;y++){
        float dy=(float)y+.5f-cy;uint32_t*out=a->pixels+(size_t)y*NW;const uint32_t*from=a->tr.from+(size_t)y*NW,*to=a->tr.to+(size_t)y*NW;
        if(fabsf(dy)>=radius){memcpy(out,from,(size_t)NW*4u);continue;}
        float half=sqrtf(radius*radius-dy*dy);transition_row(out,from,to,cx-half,cx+half);
    }
}
/* The page opens out of the tapped tile and closes back into it, with the tile's glyph growing
 * from the tile to the middle of the screen and fading into the page. */
/* How far one picture has given way to the other, 0..255, while u runs from start to end. */
static unsigned int transition_ramp(float u,float start,float end){
    float v=(u-start)/(end-start);v=v<=0?0:(v>=1?1:v);return (unsigned int)(v*v*(3.f-2.f*v)*255.f+.5f);
}
static void transition_zoom(App*a,float t){
    bool opening=a->tr.kind==TR_OPEN;float grown=opening?ease_out(t):1.f-ease_out(t);
    const uint32_t*inner=opening?a->tr.to:a->tr.from,*outer=opening?a->tr.from:a->tr.to;Rect o=a->tr.origin;
    /* With blur (2026-11-10: it could not be told from the plain zoom, only the shrinking rim
       behind the page was mixed) the page behind frosts over at once, and the page itself comes
       out of the frost while it grows. Closing is the same film run backwards. */
    const uint32_t*soft_outer=NULL,*soft_inner=NULL;unsigned int frost=0,focus=255;
    if(a->tr.blur){
        float u=opening?t:1.f-t;soft_outer=opening?a->tr.soft_from:a->tr.soft_to;soft_inner=opening?a->tr.soft_to:a->tr.soft_from;
        frost=transition_ramp(u,0.f,opening?.3f:.45f);focus=transition_ramp(u,.2f,.85f);
    }
    static uint32_t line[NW],clear[NW];
    float left=(float)nx(o.x)*(1.f-grown),top=(float)nx(o.y)*(1.f-grown),right=(float)nx(o.x+o.w)+((float)NW-(float)nx(o.x+o.w))*grown,bottom=(float)nx(o.y+o.h)+((float)NH-(float)nx(o.y+o.h))*grown,radius=54.f*(1.f-grown);
    for(int y=0;y<NH;y++){
        uint32_t*out=a->pixels+(size_t)y*NW;const uint32_t*in=inner+(size_t)y*NW,*away=outer+(size_t)y*NW;float py=(float)y+.5f;
        const uint32_t*behind=soft_outer?soft_outer+(size_t)y*NW:NULL;bool mixing=frost>0&&frost<255;
        /* A finished picture is copied; only a frame in between is mixed. */
        if(frost>=255)away=behind;
        if(py<top||py>=bottom){if(mixing)canvas_mix_run(out,away,behind,NW,frost);else memcpy(out,away,(size_t)NW*4u);continue;}
        float edge=py-top<bottom-py?py-top:bottom-py,dy=radius-edge,inset=dy>0?radius-sqrtf(radius*radius-dy*dy):0;
        if(focus<255){
            /* The page between its two edges, with the pixel either side that the edges blend. */
            const uint32_t*frosted=soft_inner+(size_t)y*NW;int l=(int)(left+inset)-1,r=(int)(right-inset)+2;l=l<0?0:l;r=r>NW?NW:r;
            if(!focus)in=frosted;else if(r>l){canvas_mix_run(clear+l,frosted+l,in+l,r-l,focus);in=clear;}
        }
        if(mixing){
            /* Only what stays visible beside the page needs mixing. */
            int l=(int)(left+inset)+2,r=(int)(right-inset)-1;l=l>NW?NW:l;r=r<0?0:r;
            canvas_mix_run(line,away,behind,l,frost);canvas_mix_run(line+r,away+r,behind+r,NW-r,frost);
            transition_row(out,line,in,left+inset,right-inset);
        }else transition_row(out,away,in,left+inset,right-inset);
    }
    if(!a->tr.glyph)return;
    float shown=opening?(t<.55f?1.f:(1.f-t)/.45f):(t<.4f?t/.4f:1.f);
    if(shown<=0)return;
    int disc=56+(int)(grown*134.f),size=disc*5/8,cx=o.x+o.w/2+(int)((float)(CW/2-(o.x+o.w/2))*grown),cy=o.y+o.h/2+(int)((float)(CH*2/5-(o.y+o.h/2))*grown);
    a->clip_active=false;gradient(a,(Rect){cx-disc/2,cy-disc/2,disc,disc},disc/2,ORANGE,ORANGE);icon(a,a->tr.glyph,cx-size/2,cy-size/2,size,ON_ORANGE);
    if(shown>=1.f)return;
    unsigned int alpha=(unsigned int)(shown*255.f);int x0=nx(cx-disc/2)-2,x1=nx(cx+disc/2)+2,y0=nx(cy-disc/2)-2,y1=nx(cy+disc/2)+2;
    if(x0<0)x0=0;
    if(y0<0)y0=0;
    if(x1>NW)x1=NW;
    if(y1>NH)y1=NH;
    /* The glyph fades into the page as it is drawn at this moment, frosted or clear. */
    for(int y=y0;y<y1;y++){
        uint32_t*row=a->pixels+(size_t)y*NW;const uint32_t*page=inner+(size_t)y*NW,*frosted=focus<255?soft_inner+(size_t)y*NW:NULL;
        for(int x=x0;x<x1;x++)row[x]=canvas_blend(frosted?canvas_blend(frosted[x],page[x],focus):page[x],row[x],alpha);
    }
}
static void transition_slide(App*a,float t,bool back){
    int moved=(int)(ease_out(t)*(float)NW);moved=moved>NW?NW:moved;
    for(int y=0;y<NH;y++){
        uint32_t*out=a->pixels+(size_t)y*NW;const uint32_t*from=a->tr.from+(size_t)y*NW,*to=a->tr.to+(size_t)y*NW;
        if(back){memcpy(out,to+(NW-moved)/3,(size_t)moved*4u);memcpy(out+moved,from,(size_t)(NW-moved)*4u);}
        else{memcpy(out,from+moved/3,(size_t)(NW-moved)*4u);memcpy(out+NW-moved,to,(size_t)moved*4u);}
    }
}
static void transition_mix(App*a,const uint32_t*from,const uint32_t*to,float amount){canvas_mix_run(a->pixels,from,to,NW*NH,(unsigned int)(amount*255.f+.5f));}
/* Sharp old page to soft old page, soft to soft, soft new page to sharp. */
static void transition_dissolve(App*a,float t){
    if(t<.4f)transition_mix(a,a->tr.from,a->tr.soft_from,t/.4f);
    else if(t<.6f)transition_mix(a,a->tr.soft_from,a->tr.soft_to,(t-.4f)/.2f);
    else transition_mix(a,a->tr.soft_to,a->tr.to,(t-.6f)/.4f);
}
static void transition_compose(App*a,float t){
    switch(a->tr.kind){
    case TR_THEME:transition_circle(a,t);break;case TR_SLIDE:transition_slide(a,t,false);break;case TR_SLIDE_BACK:transition_slide(a,t,true);break;
    case TR_FADE:transition_mix(a,a->tr.from,a->tr.to,ease_in_out(t));break;case TR_DISSOLVE:transition_dissolve(a,t);break;default:transition_zoom(a,t);break;
    }
}
/* False once the time is up, so the caller draws the live page again. */
static bool transition_frame(App*a,uint64_t now){
    uint64_t elapsed=now>a->tr.started?now-a->tr.started:0;float t=a->tr.duration?(float)elapsed/(float)a->tr.duration:1.f;
    if(t>=1.f){
        fprintf(stderr,"[recovery-ui:transition] kind=%d frames=%u elapsed_ms=%" PRIu64 " worst_ms=%" PRIu64 " from=%d to=%d\n",(int)a->tr.kind,a->tr.frames,elapsed,a->tr.worst,a->tr.page_from,a->tr.page_to);
        a->tr.active=false;return false;
    }
    transition_compose(a,t);a->dirty=true;return true;
}
static void transition_after(App*a,int id,int before,const Theme*theme){
    bool opened=a->lock_opened;a->lock_opened=false;
    if(!a->animate||a->animation==ANIM_OFF)return;
    /* The new look spreads out of the small phone of the theme that was chosen. */
    if(ui_theme!=theme){Rect chosen=a->pressed_rect;transition_arm(a,TR_THEME,TRANSITION_THEME_MS,chosen,0,nx(chosen.x+70),nx(chosen.y+105));return;}
    if((int)a->screen==before)return;
    /* Unlocking sharpens the frosted picture into the page behind it. */
    if(opened){transition_arm(a,TR_FADE,TRANSITION_DISSOLVE_MS,(Rect){0,0,0,0},0,0,0);return;}
    bool back=id==A_BACK;Rect origin={CW/2-60,CH/2-60,120,120};int glyph=0;
    if(back){for(int n=a->tr_depth-1;n>=0;n--)if(a->tr_steps[n].from==(int)a->screen){origin=a->tr_steps[n].origin;glyph=a->tr_steps[n].glyph;a->tr_depth=n;break;}}
    else{
        if(before==HOME)a->tr_depth=0;
        if(a->tr_depth==TRANSITION_DEPTH){memmove(a->tr_steps,a->tr_steps+1,sizeof(a->tr_steps)-sizeof(a->tr_steps[0]));a->tr_depth--;}
        a->tr_steps[a->tr_depth++]=(TransitionStep){before,(int)a->screen,a->pressed_rect,a->pressed_glyph};origin=a->pressed_rect;glyph=a->pressed_glyph;
    }
    switch(a->animation){
    case ANIM_SLIDE:transition_arm(a,back?TR_SLIDE_BACK:TR_SLIDE,TRANSITION_BACK_MS,origin,0,0,0);break;
    case ANIM_FADE:transition_arm(a,TR_FADE,TRANSITION_FADE_MS,origin,0,0,0);break;
    case ANIM_DISSOLVE:transition_arm(a,TR_DISSOLVE,TRANSITION_DISSOLVE_MS,origin,0,0,0);break;
    default:{bool blur=a->animation==ANIM_BLUR;
        transition_arm(a,back?TR_CLOSE:TR_OPEN,blur?(back?TRANSITION_BLUR_BACK_MS:TRANSITION_BLUR_OPEN_MS):(back?TRANSITION_BACK_MS:TRANSITION_OPEN_MS),origin,glyph,0,0);a->tr.blur=blur;break;}
    }
    a->tr.page_from=before;a->tr.page_to=(int)a->screen;
}
#define UNLOCK_SWIPE 150
static void lock_ask(App*a){lock_prompt(a,J_LOCK_ENTER);if(a->animate&&a->animation!=ANIM_OFF)transition_arm(a,TR_FADE,TRANSITION_FADE_MS,(Rect){0,0,0,0},0,0,0);}
static void power_menu_open(App*a){
    if(a->screen==POWER_MENU||a->screen==SPLASH)return;
    if(!a->menu_backdrop)a->menu_backdrop=malloc((size_t)NW*NH*sizeof(uint32_t));
    if(!a->menu_backdrop)return;
    transition_cancel(a);cancel_pointer(a);canvas_blur(a->menu_backdrop,a->pixels);canvas_rect(a->menu_backdrop,(Bounds){0,0,NW,NH},0,0,NW,NH,BG,120);
    a->menu_return=a->screen;a->screen=POWER_MENU;a->button_count=0;a->focus=-1;haptic(a,2);a->dirty=true;
    if(a->animate&&a->animation!=ANIM_OFF)transition_arm(a,TR_FADE,TRANSITION_FADE_MS,(Rect){0,0,0,0},0,0,0);
}
static void tap_action(App*a,int id);
/* Home from anywhere that can be left; what cannot be left at once goes back one step instead. */
static void nav_home(App*a){
    if(a->screen==HOME)return;
    int back=nav_back_action(a);if(!back)return;
    if(a->fs_dirty||a->fs_new_file||lock_prompting(a)||(terminal_screen(a)&&terminal_session_open(a))){
        bool shell=terminal_screen(a);tap_action(a,back);
        /* The shell is left as Back leaves it; then it is Home all the same, not the page under it. */
        if(!shell||terminal_screen(a)||a->screen==HOME)return;
    }
    a->flash_pick=a->samsung_pick=a->stock_boot_pick=a->zip_pick=a->rom_pick=a->source_sheet=false;a->theme_pick=HOME;a->install.pick_source=false;a->install.pick_backup=false;a->systems_pick_manifest=a->systems_pick_script=false;a->fs_prompt_kind=0;
    a->flash_after_targets=a->catalog_saving=a->wifi_from_catalog=false;a->catalog_return=HOME;a->catalog_only=0;
    memset(a->root_superkey,0,sizeof(a->root_superkey));a->root_key_tool=-1;
    int before=(int)a->screen;const Theme*theme=ui_theme;screen(a,HOME);transition_after(a,A_BACK,before,theme);
}
static void tap_action(App*a,int id){
    if(id==A_NAV_BACK){id=nav_back_action(a);if(!id)return;}
    else if(id==A_NAV_HOME){nav_home(a);return;}
    else if(id==A_NAV_TERMINAL){if(terminal_screen(a))return;id=A_TERMINAL;}
    int before=(int)a->screen;const Theme*theme=ui_theme;a->tapped=true;action(a,id);a->tapped=false;transition_after(a,id,before,theme);
}
/* What only time can tell: Power held long enough, a finger resting after a swipe up. */
static void held_tick(App*a,bool dark){
    if(a->pointer_down&&a->pressed_action==A_KB_BACKSPACE&&!a->scroll_moved&&a->now>=a->pressed_at+KB_REPEAT_DELAY_MS&&a->now>=a->kb_repeat_at){
        unsigned int vibration=a->vibration;a->vibration=0;a->kb_repeat_at=a->now+KB_REPEAT_MS;a->kb_repeated=true;
        action(a,A_KB_BACKSPACE);a->vibration=vibration;a->dirty=true;
    }
    if(a->display_power_down&&!a->power_held&&a->now>=a->display_power_at&&a->now-a->display_power_at>=POWER_MENU_MS){a->power_held=true;if(dark)(void)start_display_control(a,DC_WAKE,0);power_menu_open(a);}
    if(a->gesture==GESTURE_BOTTOM&&a->gesture_ready&&!a->gesture_terminal&&a->now-a->gesture_still_at>=NAV_HOLD_MS){a->gesture_terminal=true;haptic(a,2);a->dirty=true;}
}
/* Gestures start at the edges no page uses. A touch that begins there is the gesture's alone. */
static bool nav_gesture(App*a,bool down,int x,int y,uint64_t now){
    if(a->gesture){
        if(down){
            int distance=a->gesture==GESTURE_BACK?abs(x-a->gesture_x0):a->gesture_y0-y;bool ready=distance>=(a->gesture==GESTURE_BACK?NAV_BACK_DISTANCE:NAV_HOME_DISTANCE);
            if(ready&&!a->gesture_ready)haptic(a,1);
            a->gesture_ready=ready;if(!ready)a->gesture_terminal=false;
            if(abs(y-a->gesture_anchor_y)>16){a->gesture_anchor_y=y;a->gesture_still_at=now;}
            a->gesture_x=x;a->gesture_y=y;a->dirty=true;return true;
        }
        int kind=a->gesture;bool ready=a->gesture_ready,terminal=a->gesture_terminal;
        a->nav_settle_kind=kind;a->nav_settle_amount=nav_amount(a);a->nav_settle_end=now+NAV_SETTLE_MS;
        a->gesture=GESTURE_NONE;a->gesture_ready=a->gesture_terminal=false;a->pointer_down=false;a->dirty=true;
        if(ready){a->pressed_rect=(Rect){CW/2-60,CH-180,120,120};a->pressed_glyph=0;tap_action(a,kind==GESTURE_BACK?A_NAV_BACK:(terminal?A_NAV_TERMINAL:A_NAV_HOME));}
        return true;
    }
    if(!down||a->pointer_down||a->nav_mode!=NAV_GESTURES||!nav_open(a))return false;
    bool bottom=y>=NAV_BOTTOM,edge=(x<=NAV_EDGE||x>=CW-NAV_EDGE)&&y>=170&&y<NAV_BOTTOM;if(!bottom&&!edge)return false;
    stop_inertia(a,"gesture");a->gesture=bottom?GESTURE_BOTTOM:GESTURE_BACK;a->gesture_x0=a->gesture_x=x;a->gesture_y0=a->gesture_y=a->gesture_anchor_y=y;a->gesture_still_at=now;a->gesture_ready=a->gesture_terminal=false;
    a->pointer_down=true;a->pressed_action=0;a->dirty=true;return true;
}
static void pointer(App*a,bool down,int x,int y,uint64_t now){
    a->now=now;a->pointer_x=x;a->pointer_y=y;a->last_activity=now;
    if(down&&!a->pointer_down)transition_cancel(a);
    if(a->screen==LOCKSCREEN&&!a->display_input_blocked&&!(a->display_blank_known&&a->display_blanked)){
        if(down&&!a->pointer_down){a->pointer_down=true;a->lock_start_y=y;a->lock_drag=0;}
        else if(down){a->lock_drag=a->lock_start_y>y?a->lock_start_y-y:0;}
        else{bool open=a->lock_drag>=UNLOCK_SWIPE;a->pointer_down=false;a->lock_drag=0;if(open)lock_ask(a);}
        a->dirty=true;return;
    }
    if(a->display_input_blocked||(a->display_blank_known&&a->display_blanked)||a->display_power_down){
        /* An off-screen contact, including one held through wake, is consumed
         * through its release. It can never activate the newly visible screen. */
        a->pointer_requires_release=down;return;
    }
    if(a->pointer_requires_release){if(!down)a->pointer_requires_release=false;return;}
    if(a->display_brightness_drag){
        if(a->screen!=DISPLAY_SETTINGS||!display_slider_usable(a)){cancel_pointer(a);return;}
        a->display_drag_x=x;a->display_preview_percent=display_slider_percent(x);a->dirty=true;
        /* The panel follows the finger: a direct write takes about 3 ms, a helper start 100 ms or more. */
        if(down&&a->display_preview_percent!=a->display_live_percent&&now-a->display_live_at>=24){
            a->display_live_at=now;a->display_live_percent=a->display_preview_percent;(void)brightness_direct(a,a->display_preview_percent);}
        if(!down){unsigned int percent=a->display_preview_percent;a->display_brightness_drag=false;a->pointer_down=false;a->pressed_action=0;(void)brightness_direct(a,percent);a->saved_brightness=percent;settings_changed(a);
            /* One helper call confirms the final value and keeps its saved state. */
            if(a->display_control.pid)a->display_wanted_percent=percent;
            else if(percent!=a->display_percent)(void)start_display_control(a,DC_BRIGHTNESS,percent);
        }
        return;
    }
    if(down&&!a->pointer_down&&a->screen==DISPLAY_SETTINGS&&display_slider_usable(a)&&inside(display_slider_rect(),x,y)){a->display_live_at=now;
        stop_inertia(a,"brightness");a->pointer_down=a->display_brightness_drag=true;a->pressed_action=0;
        a->display_drag_x=x;a->display_preview_percent=a->display_live_percent=display_slider_percent(x);(void)brightness_direct(a,a->display_preview_percent);a->dirty=true;return;
    }
    if(nav_gesture(a,down,x,y,now))return;
    if(down&&!a->pointer_down){
        enum ScrollSurface moving=a->kinetic.active?a->motion_surface:SCROLL_NONE;stop_inertia(a,"touch");
        a->pointer_down=true;a->scroll_moved=a->scroll_dragging=false;a->scroll_surface=scroll_candidate(a,x,y);a->scroll_touch=a->scroll_surface!=SCROLL_NONE;
        a->scroll_stop_tap=moving!=SCROLL_NONE&&moving==a->scroll_surface;
        a->scroll_start_x=x;a->scroll_start_y=y;a->scroll_start_offset=a->scroll_touch?scroll_offset(a,a->scroll_surface):0;
        a->scroll_samples.count=0;if(a->scroll_touch)scroll_sample(&a->scroll_samples,now,(double)a->scroll_start_offset);
        int i=button_at(a,x,y);if(i>=0&&!a->scroll_stop_tap){Button*b=&a->buttons[i];a->pressed_action=b->action;a->pressed_hold=b->hold_ms;a->pressed_rect=b->r;a->pressed_glyph=b->glyph;a->pressed_at=now;}else a->pressed_action=0;a->kb_repeated=false;a->kb_repeat_at=0;a->kb_bubble_until=0;a->dirty=true;
    }else if(down&&a->pointer_down){
        if(a->scroll_touch){
            int dx=x-a->scroll_start_x,dy=y-a->scroll_start_y;
            if(abs(dx)>SWIPE_THRESHOLD||abs(dy)>SWIPE_THRESHOLD){a->scroll_moved=true;a->pressed_action=0;}
            if(a->scroll_moved&&abs(dy)>=abs(dx)&&abs(dy)>SWIPE_THRESHOLD)a->scroll_dragging=true;
            if(a->scroll_dragging)apply_scroll(a,a->scroll_surface,a->scroll_start_offset-dy);
            scroll_sample(&a->scroll_samples,now,(double)scroll_offset(a,a->scroll_surface));
        }
        if(a->pressed_action&&!inside(a->pressed_rect,x,y))a->pressed_action=0;
        a->dirty=true;
    }else if(!down&&a->pointer_down){
        int id=a->pressed_action;bool accepted=id&&!a->scroll_moved&&!a->scroll_stop_tap&&inside(a->pressed_rect,x,y)&&now>=a->pressed_at&&now-a->pressed_at>=(uint64_t)a->pressed_hold;
        /* The key stays up a moment after the finger lifts; a Delete that repeated has done its work. */
        if(accepted&&a->kb_drawn&&id>=A_KB_KEY&&id<A_KB_KEY+a->keyboard_count){a->kb_bubble_cp=a->keyboard_keys[id-A_KB_KEY];a->kb_bubble_rect=a->pressed_rect;a->kb_bubble_until=now+KB_BUBBLE_MS;}
        if(id==A_KB_BACKSPACE&&a->kb_repeated)accepted=false;
        enum ScrollSurface kind=a->scroll_surface;bool fling=a->scroll_touch&&a->scroll_dragging;
        if(a->scroll_touch){
            scroll_sample(&a->scroll_samples,now,(double)scroll_offset(a,kind));
            if(a->scroll_moved){
                if(kind==SCROLL_FOLDER)fprintf(stderr,"[recovery-ui:scroll] offset_px=%" PRId64 " first_index=%" PRId64 " total=%u cached_first=%u cached_count=%d tap_suppressed=1\n",a->browser_scroll,a->browser_scroll/FILE_ROW_HEIGHT,a->file_total,a->file_offset,a->file_count);
                else if(kind==SCROLL_VOLUMES)fprintf(stderr,"[recovery-ui:volumes-scroll] offset_px=%" PRId64 " first_index=%" PRId64 " total=%d tap_suppressed=1\n",a->volume_scroll,a->volume_scroll/VOLUME_ROW_HEIGHT,a->volume_count);
                else if(kind==SCROLL_SYSTEMS)fprintf(stderr,"[recovery-ui:systems-scroll] offset_px=%" PRId64 " first_slot=%" PRId64 " tap_suppressed=1\n",a->systems_scroll,a->systems_scroll/126);
                else if(kind==SCROLL_TOOLS||kind==SCROLL_PAGE||kind==SCROLL_INSTALL_REVIEW||kind==SCROLL_INSTALL_PENDING)fprintf(stderr,"[recovery-ui:%s-scroll] offset_px=%" PRId64 " tap_suppressed=1\n",scroll_surface_name(kind),scroll_offset(a,kind));
                else if(kind==SCROLL_TERMINAL)fprintf(stderr,"[recovery-ui:terminal-scroll] offset_px=%" PRId64 " first_row=%" PRId64 " rows=%u tap_suppressed=1\n",a->terminal_scroll,a->terminal_scroll/TERMINAL_CELL_H,tm_line_count(&a->terminal));
                else fprintf(stderr,"[recovery-ui:text-scroll] offset_px=%" PRId64 " first_row=%" PRId64 " cursor_byte=%zu tap_suppressed=1\n",scroll_offset(a,kind),scroll_offset(a,kind)/TEXT_ROW_HEIGHT,kind==SCROLL_PROMPT?a->prompt_cursor:a->fs_cursor);
            }else if(a->scroll_stop_tap)fprintf(stderr,"[recovery-ui:scroll-stop-tap] surface=%s offset_px=%" PRId64 " tap_suppressed=1\n",scroll_surface_name(kind),scroll_offset(a,kind));
        }
        a->pointer_down=false;a->pressed_action=0;a->scroll_touch=a->scroll_moved=a->scroll_dragging=a->scroll_stop_tap=false;a->scroll_surface=SCROLL_NONE;a->dirty=true;
        if(fling)begin_inertia(a,kind,now);else if(accepted)tap_action(a,id);
    }
}

typedef struct { int fd,drm; uint8_t*map;uint32_t*frame;size_t length;struct fb_var_screeninfo v;struct fb_fix_screeninfo f;int vx,vy,vw,vh; } Display;
static uint32_t component(unsigned c,struct fb_bitfield f){if(!f.length)return 0;uint32_t max=(1u<<f.length)-1u;return ((c*max+127u)/255u)<<f.offset;}
/* The card with the panel on it. Its number depends on which other DRM drivers are built. */
static int open_panel_card(void){
    for(int n=0;n<8;n++){
        char path[64];snprintf(path,sizeof(path),"/sys/class/drm/card%d-DSI-1",n);if(access(path,F_OK))continue;
        snprintf(path,sizeof(path),"/dev/dri/card%d",n);return open(path,O_RDWR|O_CLOEXEC);
    }
    fprintf(stderr,"[recovery-ui] no DSI panel card: frames are not synchronized\n");return -1;
}
static bool open_display(Display*d,const char*path){
    memset(d,0,sizeof(*d));d->fd=open(path,O_RDWR|O_CLOEXEC);if(d->fd<0)return false;
    if(ioctl(d->fd,FBIOGET_FSCREENINFO,&d->f)<0||ioctl(d->fd,FBIOGET_VSCREENINFO,&d->v)<0)goto bad;
    if(d->f.type!=FB_TYPE_PACKED_PIXELS||d->f.visual!=FB_VISUAL_TRUECOLOR||d->v.grayscale||d->v.nonstd||!d->v.xres||!d->v.yres||d->v.xres>8192||d->v.yres>8192)goto bad;
    if(d->v.bits_per_pixel!=16&&d->v.bits_per_pixel!=24&&d->v.bits_per_pixel!=32)goto bad;
    if((uint64_t)d->v.xoffset+d->v.xres>d->v.xres_virtual||(uint64_t)d->v.yoffset+d->v.yres>d->v.yres_virtual)goto bad;
    struct fb_bitfield fields[]={d->v.red,d->v.green,d->v.blue,d->v.transp};uint32_t used=0;
    for(int i=0;i<4;i++){if(fields[i].length>8||fields[i].offset+fields[i].length>d->v.bits_per_pixel||fields[i].msb_right)goto bad;if(i<3&&!fields[i].length)goto bad;uint32_t mask=fields[i].length?((1u<<fields[i].length)-1u)<<fields[i].offset:0;if(mask&used)goto bad;used|=mask;}
    uint64_t row_end=((uint64_t)d->v.xoffset+d->v.xres)*(d->v.bits_per_pixel/8u),last=((uint64_t)d->v.yoffset+d->v.yres-1u)*d->f.line_length+row_end;
    if(row_end>d->f.line_length||last>d->f.smem_len||!d->f.smem_len)goto bad;
    d->length=d->f.smem_len;d->map=mmap(NULL,d->length,PROT_READ|PROT_WRITE,MAP_SHARED,d->fd,0);if(d->map==MAP_FAILED){d->map=NULL;goto bad;}
    if((uint64_t)d->v.xres*CH<=(uint64_t)d->v.yres*CW){d->vw=(int)d->v.xres;d->vh=d->vw*CH/CW;}else{d->vh=(int)d->v.yres;d->vw=d->vh*CW/CH;}
    d->vx=((int)d->v.xres-d->vw)/2;d->vy=((int)d->v.yres-d->vh)/2;
    (void)ioctl(d->fd,FBIOBLANK,FB_BLANK_UNBLANK);d->drm=open_panel_card();return true;
bad:close(d->fd);d->fd=-1;errno=ENOTSUP;return false;
}
static bool compose_native(const App*a,uint32_t*frame,int width,int height,int vx,int vy,int vw,int vh){
    if(!frame||width<1||height<1||width>8192||height>8192||(uint64_t)width*height>16u*1024u*1024u||vw<1||vh<1)return false;
    int xmap[8192];for(int x=0;x<width;x++)xmap[x]=x>=vx&&x<vx+vw?(x-vx)*NW/vw:-1;
    for(int y=0;y<height;y++){
        int source=y>=vy&&y<vy+vh?(y-vy)*NH/vh:-1;uint32_t*row=frame+(size_t)y*width;
        if(source>=0&&vw==NW&&width==NW&&!vx)memcpy(row,a->pixels+(size_t)source*NW,(size_t)NW*sizeof(*row));
        else for(int x=0;x<width;x++)row[x]=source>=0&&xmap[x]>=0?a->pixels[(size_t)source*NW+(size_t)xmap[x]]:BG;
    }
    return true;
}
/* The panel has one framebuffer, so a frame written during scanout tears. Starting the
 * copy at vblank keeps it ahead of the scanline. FBIO_WAITFORVSYNC is refused while
 * display.py is DRM master, but the DRM wait works for any client. */
static void wait_vblank(Display*d){
    if(d->drm<=0)return;
    union drm_wait_vblank wait;memset(&wait,0,sizeof(wait));wait.request.type=_DRM_VBLANK_RELATIVE;wait.request.sequence=1;
    if(ioctl(d->drm,DRM_IOCTL_WAIT_VBLANK,&wait)==0||errno==EINTR)return;
    /* A panel that is off has no vblank, which says nothing about the next frame. Giving up
     * here tore every frame after the first lock. Only a lost device ends the waiting. */
    if(errno==ENODEV||errno==EBADF){close(d->drm);d->drm=-1;return;}
    struct timespec pause={0,8000000};nanosleep(&pause,NULL);
}
static bool present(const App*a,Display*d){
    if((uint64_t)d->v.xres*d->v.yres>16u*1024u*1024u)return false;
    if(!(a->display_blank_known&&a->display_blanked))wait_vblank(d);
    /* The panel matches the canvas: copy rows straight into the framebuffer. */
    if(d->v.bits_per_pixel==32&&d->v.red.offset==16&&d->v.green.offset==8&&d->v.blue.offset==0&&!d->v.transp.length&&d->v.xres==NW&&d->vw==NW&&d->vh==NH&&!d->vx){
        for(unsigned y=0;y<d->v.yres;y++){
            uint32_t*out=(uint32_t*)(d->map+((size_t)y+d->v.yoffset)*d->f.line_length+(size_t)d->v.xoffset*4u);int source=(int)y-d->vy;
            memcpy(out,a->pixels+(size_t)(source<0?0:(source<NH?source:NH-1))*NW,(size_t)NW*4u);
        }
        return true;
    }
    if(!d->frame){d->frame=malloc((size_t)d->v.xres*d->v.yres*sizeof(*d->frame));if(!d->frame)return false;}
    if(!compose_native(a,d->frame,(int)d->v.xres,(int)d->v.yres,d->vx,d->vy,d->vw,d->vh))return false;
    unsigned bpp=d->v.bits_per_pixel/8u;
    bool fast=d->v.bits_per_pixel==32&&d->v.red.offset==16&&d->v.green.offset==8&&d->v.blue.offset==0&&d->v.red.length==8&&d->v.green.length==8&&d->v.blue.length==8&&(!d->v.transp.length||(d->v.transp.offset==24&&d->v.transp.length==8));
    for(unsigned y=0;y<d->v.yres;y++){
        const uint32_t*row=d->frame+(size_t)y*d->v.xres;uint8_t*outrow=d->map+((size_t)y+d->v.yoffset)*d->f.line_length+(size_t)d->v.xoffset*bpp;
        if(fast&&!d->v.transp.length){memcpy(outrow,row,(size_t)d->v.xres*4u);continue;}
        for(unsigned x=0;x<d->v.xres;x++){
            uint32_t c=row[x],out=fast?(c|0xff000000u):(component((c>>16)&255u,d->v.red)|component((c>>8)&255u,d->v.green)|component(c&255u,d->v.blue)|component(255,d->v.transp));
            memcpy(outrow+(size_t)x*bpp,&out,bpp);
        }
    }
    return true;
}
/* On a little core a frame takes 20 ms, on a big one 8 ms, and 90 Hz leaves 11. The
 * scheduler keeps this mostly idle process on a little core, so it is placed on the
 * fastest cluster. Helpers must keep every core: backups compress on all of them. */
static cpu_set_t ui_every_cpu;static bool ui_cpus_changed;
static void restore_child_cpus(void){if(ui_cpus_changed)(void)sched_setaffinity(0,sizeof(ui_every_cpu),&ui_every_cpu);}
/* Nothing touches a dark screen, so its CPU floor goes with it. */
static char ui_floor_path[400];static long ui_floor_awake,ui_floor_asleep;
static void ui_cpu_floor(bool awake){
    long value=awake?ui_floor_awake:ui_floor_asleep;if(!ui_floor_path[0]||value<=0)return;
    FILE*f=fopen(ui_floor_path,"w");if(f){fprintf(f,"%ld\n",value);fclose(f);}
}
static void prefer_fast_cores(void){
    char best[320]={0};long best_speed=0;DIR*d=opendir("/sys/devices/system/cpu/cpufreq");if(!d)return;
    for(struct dirent*e;(e=readdir(d))!=NULL;){
        if(strncmp(e->d_name,"policy",6))continue;
        char path[320];snprintf(path,sizeof(path),"/sys/devices/system/cpu/cpufreq/%s/cpuinfo_max_freq",e->d_name);long speed=sysfs_number(path,0);
        if(speed>best_speed){best_speed=speed;snprintf(best,sizeof(best),"/sys/devices/system/cpu/cpufreq/%s",e->d_name);}
    }
    closedir(d);if(!best[0])return;
    char path[384];snprintf(path,sizeof(path),"%s/related_cpus",best);FILE*f=fopen(path,"r");if(!f)return;
    cpu_set_t fast;CPU_ZERO(&fast);for(int cpu;fscanf(f,"%d",&cpu)==1;)if(cpu>=0&&cpu<CPU_SETSIZE)CPU_SET(cpu,&fast);
    fclose(f);if(!CPU_COUNT(&fast)||sched_getaffinity(0,sizeof(ui_every_cpu),&ui_every_cpu))return;
    if(pthread_atfork(NULL,NULL,restore_child_cpus)||sched_setaffinity(0,sizeof(fast),&fast))return;
    ui_cpus_changed=true;
    /* Touch must not wait for the governor to climb from its lowest step. */
    snprintf(ui_floor_path,sizeof(ui_floor_path),"%s/scaling_min_freq",best);ui_floor_awake=best_speed*2/3;
    snprintf(path,sizeof(path),"%s/cpuinfo_min_freq",best);ui_floor_asleep=sysfs_number(path,0);ui_cpu_floor(true);
}
static void close_display(Display*d){if(d->drm>0)close(d->drm);free(d->frame);if(d->map)munmap(d->map,d->length);if(d->fd>=0)close(d->fd);}
static bool write_ppm(const App*a,const char*path){
    FILE*f=fopen(path,"wb");if(!f)return false;int width=a->render_native?1080:CW,height=a->render_native?2408:CH;
    uint32_t*frame=malloc((size_t)width*height*sizeof(*frame));if(!frame){fclose(f);return false;}
    if(!compose_native(a,frame,width,height,0,a->render_native?4:0,width,a->render_native?2400:CH)){free(frame);fclose(f);return false;}
    fprintf(f,"P6\n%d %d\n255\n",width,height);
    for(int y=0;y<height;y++)for(int x=0;x<width;x++){
        uint32_t color=frame[(size_t)y*width+x];
        unsigned char rgb[]={(unsigned char)(color>>16),(unsigned char)(color>>8),(unsigned char)color};
        if(fwrite(rgb,1,3,f)!=3){free(frame);fclose(f);return false;}
    }
    free(frame);return fclose(f)==0;
}

#include "screenshot_ui.h"

typedef struct {int id,x,y;bool have_x,have_y;} Slot;
typedef struct {int fd,slot,primary_id,xmin,xmax,ymin,ymax,last_x,last_y;bool mt,down,dropped,need_release,clock_monotonic;Slot slots[MAX_SLOTS];char name[128];} Input;
static int axis_to_pixel(int value,int low,int high,int count){if(high<=low||count<1)return -1;if(value<low)value=low;if(value>high)value=high;return(int)(((int64_t)value-low)*(count-1)/(high-low));}
static void resync_input(Input*i){
    i->down=false;i->primary_id=-1;i->need_release=true;
    for(int s=0;s<MAX_SLOTS;s++){i->slots[s].id=-1;i->slots[s].have_x=i->slots[s].have_y=false;}
    if(i->mt){
        struct input_absinfo slot;int xs[MAX_SLOTS+1]={ABS_MT_POSITION_X},ys[MAX_SLOTS+1]={ABS_MT_POSITION_Y},ids[MAX_SLOTS+1]={ABS_MT_TRACKING_ID};
        if(ioctl(i->fd,EVIOCGABS(ABS_MT_SLOT),&slot)<0||slot.minimum!=0||slot.maximum<0||slot.maximum>=MAX_SLOTS)return;
        size_t n=(size_t)(slot.maximum+2)*sizeof(int);
        if(ioctl(i->fd,EVIOCGMTSLOTS(n),xs)<0||ioctl(i->fd,EVIOCGMTSLOTS(n),ys)<0||ioctl(i->fd,EVIOCGMTSLOTS(n),ids)<0)return;
        bool active=false;
        for(int s=0;s<=slot.maximum;s++){i->slots[s]=(Slot){.id=ids[s+1],.x=xs[s+1],.y=ys[s+1],.have_x=true,.have_y=true};if(ids[s+1]>=0)active=true;}
        i->slot=slot.value>=0&&slot.value<=slot.maximum?slot.value:0;i->need_release=active;
    }else{
        struct input_absinfo x,y;
        if(ioctl(i->fd,EVIOCGABS(ABS_X),&x)==0&&ioctl(i->fd,EVIOCGABS(ABS_Y),&y)==0)i->slots[0]=(Slot){.id=-1,.x=x.value,.y=y.value,.have_x=true,.have_y=true};
    }
}
static bool setup_input(Input*i,const char*path){
    memset(i,0,sizeof(*i));i->fd=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(i->fd<0)return false;for(int n=0;n<MAX_SLOTS;n++)i->slots[n].id=-1;i->primary_id=-1;
    int clock_id=CLOCK_MONOTONIC;i->clock_monotonic=ioctl(i->fd,EVIOCSCLOCKID,&clock_id)==0;
    (void)ioctl(i->fd,EVIOCGNAME(sizeof(i->name)),i->name);struct input_absinfo x,y,slot;
    if(ioctl(i->fd,EVIOCGABS(ABS_MT_POSITION_X),&x)==0&&ioctl(i->fd,EVIOCGABS(ABS_MT_POSITION_Y),&y)==0&&ioctl(i->fd,EVIOCGABS(ABS_MT_SLOT),&slot)==0&&slot.minimum==0&&slot.maximum>=0&&slot.maximum<MAX_SLOTS){i->mt=true;i->xmin=x.minimum;i->xmax=x.maximum;i->ymin=y.minimum;i->ymax=y.maximum;}
    else if(ioctl(i->fd,EVIOCGABS(ABS_X),&x)==0&&ioctl(i->fd,EVIOCGABS(ABS_Y),&y)==0){i->xmin=x.minimum;i->xmax=x.maximum;i->ymin=y.minimum;i->ymax=y.maximum;}
    else{i->xmin=i->xmax=i->ymin=i->ymax=0;}
    resync_input(i);return true;
}
/* Blanks only from a known lit state; an uncertain panel is left alone. */
static bool screen_timeout_tick(App*a){
    unsigned int seconds=screen_timeouts[a->screen_timeout_index].seconds;
    if(!seconds||!a->display_blank_supported||!a->display_blank_known||a->display_blanked||a->display_uncertain||a->display_control.pid||a->display_power_down||a->pointer_down)return false;
    if(a->now<a->last_activity||a->now-a->last_activity<(uint64_t)seconds*1000u)return false;
    a->last_activity=a->now;return start_display_control(a,DC_BLANK,0);
}
static void key_event(App*a,unsigned code,int value){
    a->last_activity=a->now;
    if(code==KEY_VOLUMEDOWN){if(value==1){a->voldown_held=true;a->voldown_at=a->now;a->voldown_focus=a->focus;}else if(value==0)a->voldown_held=false;}
    if(code==KEY_POWER){
        if(value==1&&!a->display_power_down){a->display_power_down=true;a->display_power_at=a->now;cancel_pointer(a);
            /* 2026-11-11: Volume down first and then Power is a screenshot too, as on Android where the two
               are pressed together (only Power first was taken, so half the attempts moved the focus and
               blanked the screen instead). The step the volume key made is taken back before the picture. */
            if(a->voldown_held&&a->now>=a->voldown_at&&a->now-a->voldown_at<=SHOT_CHORD_MS&&!(a->display_blank_known&&a->display_blanked)){
                a->power_held=true;if(a->focus!=a->voldown_focus){a->focus=a->voldown_focus;render(a);}
                take_screenshot(a);
            }
        }
        else if(value==0&&a->display_power_down){
            bool short_press=!a->power_held&&a->now>=a->display_power_at&&a->now-a->display_power_at<=POWER_SHORT_PRESS_MS;
            a->display_power_down=false;a->power_held=false;
            if(short_press&&!a->display_control.pid){
                if(a->display_uncertain||(a->display_blank_known&&a->display_blanked))(void)start_display_control(a,DC_WAKE,0);
                else if(a->display_light_known&&!a->display_blank_supported){copy(a->display_light_note,sizeof(a->display_light_note),"Screen blanking is unavailable; brightness controls remain separate.");a->dirty=true;}
                else (void)start_display_control(a,DC_TOGGLE,0);
            }
        }
        return; /* Repeat events and long holds never select a focused action. */
    }
    /* Power held and Volume down: a screenshot; releasing Power then neither blanks nor wakes. */
    if(code==KEY_VOLUMEDOWN&&value==1&&a->display_power_down&&!(a->display_blank_known&&a->display_blanked)){a->power_held=true;take_screenshot(a);return;}
    if(a->display_input_blocked||(a->display_blank_known&&a->display_blanked)||a->display_power_down)return;
    if(value==1)stop_inertia(a,"key");
    if(a->screen==TERMINAL&&(value==1||value==2)){
        int key=code==KEY_ENTER?A_KB_ENTER:(code==KEY_BACKSPACE?A_KB_BACKSPACE:(code==KEY_TAB?A_TERM_TAB:(code==KEY_ESC?A_TERM_ESCAPE:(code==KEY_UP?A_TERM_UP:(code==KEY_DOWN?A_TERM_DOWN:(code==KEY_LEFT?A_TERM_LEFT:(code==KEY_RIGHT?A_TERM_RIGHT:0)))))));
        if(key){terminal_action(a,key);return;}
    }
    if(a->job.pid)return;
    if((code==KEY_VOLUMEUP||code==KEY_VOLUMEDOWN||code==KEY_UP||code==KEY_DOWN)&&value==1){cancel_pointer(a);int direction=(code==KEY_VOLUMEUP||code==KEY_UP)?-1:1;int f=a->focus;
        for(int n=0;n<a->button_count;n++){f=(f+direction+a->button_count)%a->button_count;if(a->buttons[f].enabled){a->focus=f;break;}}a->dirty=true;
    }else if(code==KEY_ENTER&&a->focus>=0&&a->focus<a->button_count&&value!=2){Rect r=a->buttons[a->focus].r;pointer(a,value==1,r.x+r.w/2,r.y+r.h/2,a->now);}
    else if((code==KEY_BACK||code==KEY_ESC)&&value==1)tap_action(a,A_BACK);
}
static void input_sync(App*a,Input*i,const Display*d){
    /* Power/volume-only nodes also emit SYN_REPORT. They do not own a touch
     * release and must not clear a contact held on the touchscreen. */
    if(i->xmax<=i->xmin||i->ymax<=i->ymin)return;
    int count=0,which=-1;
    for(int n=0;n<(i->mt?MAX_SLOTS:1);n++)if(i->slots[n].id>=0){count++;which=n;}
    if(count>1){cancel_pointer(a);i->need_release=true;i->down=false;return;}
    if(i->need_release){if(count==0){i->need_release=false;a->pointer_requires_release=false;}return;}
    if(count==0){if(i->down){pointer(a,false,i->last_x,i->last_y,a->now);i->down=false;i->primary_id=-1;}return;}
    Slot*s=&i->slots[which];if(!s->have_x||!s->have_y)return;
    if(i->down&&s->id!=i->primary_id){cancel_pointer(a);i->down=false;i->need_release=true;return;}
    int px=axis_to_pixel(s->x,i->xmin,i->xmax,(int)d->v.xres),py=axis_to_pixel(s->y,i->ymin,i->ymax,(int)d->v.yres);if(px<0||py<0)return;
    int x=(px-d->vx)*CW/d->vw,y=(py-d->vy)*CH/d->vh;
    pointer(a,true,x,y,a->now);i->down=true;i->primary_id=s->id;
    /* Release coordinates are kept separately from raw ABS slots. */
    i->last_x=x;i->last_y=y;
}
static void handle_input(App*a,Input*i,const Display*d){
    struct input_event e;
    while(read(i->fd,&e,sizeof(e))==(ssize_t)sizeof(e)){
        if(e.type==EV_SYN&&e.code==SYN_DROPPED){cancel_pointer(a);i->dropped=true;i->need_release=true;i->down=false;continue;}
        if(i->dropped){if(e.type==EV_SYN&&e.code==SYN_REPORT){i->dropped=false;resync_input(i);}continue;}
        if(e.type==EV_KEY){
            /* BTN_TOUCH accompanies Type-B slots; it is not a navigation key.
             * Let SYN_REPORT stop inertia and suppress that same contact. */
            if(e.code==BTN_TOUCH){if(!i->mt)i->slots[0].id=e.value?0:-1;}
            else{a->now=i->clock_monotonic&&e.time.tv_sec>=0&&(e.time.tv_sec||e.time.tv_usec)?(uint64_t)e.time.tv_sec*1000u+(uint64_t)e.time.tv_usec/1000u:milliseconds();key_event(a,e.code,e.value);}
        }else if(e.type==EV_ABS){
            if(e.code==ABS_MT_SLOT){i->slot=e.value>=0&&e.value<MAX_SLOTS?e.value:-1;continue;}
            int slot=i->mt?i->slot:0;if(slot<0||slot>=MAX_SLOTS)continue;Slot*s=&i->slots[slot];
            /* Type-B slot axes persist across tracking IDs; unchanged axes may be omitted. */
            if(e.code==ABS_MT_TRACKING_ID)s->id=e.value;
            else if(e.code==(i->mt?ABS_MT_POSITION_X:ABS_X)){s->x=e.value;s->have_x=true;}
            else if(e.code==(i->mt?ABS_MT_POSITION_Y:ABS_Y)){s->y=e.value;s->have_y=true;}
        }else if(e.type==EV_SYN&&e.code==SYN_REPORT){
            a->now=i->clock_monotonic&&e.time.tv_sec>=0&&(e.time.tv_sec||e.time.tv_usec)?(uint64_t)e.time.tv_sec*1000u+(uint64_t)e.time.tv_usec/1000u:milliseconds();
            input_sync(a,i,d);
        }
    }
}
static long sysfs_number(const char*path,long fallback){FILE*f=fopen(path,"r");long value=fallback;if(f){if(fscanf(f,"%ld",&value)!=1)value=fallback;fclose(f);}return value;}
/* The battery as the kernel shows it, and its curve from the device tree: the open-circuit voltage
   at each level, and the cell's resistance. A22X_RECOVERY_BATTERY names a folder that holds both for
   tests. */
#define BATTERY_POINTS 40
static struct{unsigned int uv[BATTERY_POINTS],percent[BATTERY_POINTS],count;long resistance;bool read;char folder[PATH_LEN],tree[PATH_LEN];}battery_curve;
static long battery_number(const char*name,long fallback){char path[PATH_LEN+48];snprintf(path,sizeof(path),"%s/%s",battery_curve.folder,name);return sysfs_number(path,fallback);}
static void battery_curve_read(void){
    if(battery_curve.read)return;
    battery_curve.read=true;battery_curve.count=0;battery_curve.resistance=0;
    const char*test=getenv("A22X_RECOVERY_BATTERY");
    copy(battery_curve.folder,sizeof(battery_curve.folder),test&&*test?test:"/sys/class/power_supply/battery");
    copy(battery_curve.tree,sizeof(battery_curve.tree),test&&*test?test:"/sys/firmware/devicetree/base/battery");
    char path[PATH_LEN+48];unsigned char data[BATTERY_POINTS*8];
    snprintf(path,sizeof(path),"%s/ocv-capacity-table-0",battery_curve.tree);
    int fd=open(path,O_RDONLY|O_CLOEXEC);ssize_t n=fd>=0?read(fd,data,sizeof(data)):-1;if(fd>=0)close(fd);
    #define BE32(p) (((unsigned int)(p)[0]<<24)|((unsigned int)(p)[1]<<16)|((unsigned int)(p)[2]<<8)|(unsigned int)(p)[3])
    for(ssize_t at=0;at+8<=n;at+=8){
        unsigned int uv=BE32(data+at),percent=BE32(data+at+4),k=battery_curve.count;
        /* From full to empty, each point below the one before: anything else is no curve. */
        if(percent>100||uv<2000000||uv>5000000||(k&&(uv>=battery_curve.uv[k-1]||percent>=battery_curve.percent[k-1]))){battery_curve.count=0;break;}
        battery_curve.uv[k]=uv;battery_curve.percent[k]=percent;battery_curve.count++;
    }
    snprintf(path,sizeof(path),"%s/factory-internal-resistance-micro-ohms",battery_curve.tree);
    fd=open(path,O_RDONLY|O_CLOEXEC);n=fd>=0?read(fd,data,4):-1;if(fd>=0)close(fd);
    if(n==4){unsigned int r=BE32(data);if(r>=10000&&r<=1000000)battery_curve.resistance=(long)r;}
    #undef BE32
}
/* The level the curve gives for an open-circuit voltage, between its points. */
static double battery_curve_level(long uv){
    unsigned int n=battery_curve.count;
    if(n<2)return -1;
    if(uv>=(long)battery_curve.uv[0])return battery_curve.percent[0];
    for(unsigned int k=1;k<n;k++)if(uv>=(long)battery_curve.uv[k]){
        double span=(double)battery_curve.uv[k-1]-(double)battery_curve.uv[k];
        return battery_curve.percent[k]+((double)uv-(double)battery_curve.uv[k])/span*((double)battery_curve.percent[k-1]-(double)battery_curve.percent[k]);
    }
    return battery_curve.percent[n-1];
}
/* 2026-11-11, the owner: "Android shows 90 %, here 15 %". The cell stood at 3.79 V, about 30 %: the
   kernel counts charge from one reading of the voltage taken under load while the phone starts, and in
   the flat middle of the curve that reading can be 30 points off (15, then 9, then 20 after a
   restart); Android showed a number it had kept from days before. So the interface keeps its own
   estimate. The kernel's counted charge gives the changes. Where the charge stands comes from the
   voltage, taken back to open circuit with the measured current and the cell's resistance, and
   smoothed: the estimate starts there, and afterwards is drawn to it one point at a time whenever
   the two are four points or more apart. A charger that says Full is believed. */
/* 2026-11-12, the owner: the percent still jumps, it used to be right, look how stock does it.
   Stock keeps the percent it showed in the PMIC's clock domain and counts charge from there; the
   voltage speaks only at the ends. From kernel 124 the recovery's gauge does the same (it starts
   from that kept number, so Android and the recovery go on from one another's, and corrects itself
   with care), and says so with its attribute soc_start. Then its number is shown as it is, and the
   estimate below is for older kernels only. */
static bool battery_gauge_keeps(void){char path[PATH_LEN+48];snprintf(path,sizeof(path),"%s/device/soc_start",battery_curve.folder);return access(path,R_OK)==0;}
#define BATTERY_SETTLE 6u        /* readings at the start that set the estimate outright */
#define BATTERY_APART 4.0        /* points between estimate and voltage before a correction */
#define BATTERY_STEP_MS 30000.0  /* one point of correction takes this long */
static void update_battery(App*a){
    battery_curve_read();
    long capacity=battery_number("capacity",-1);if(capacity<0||capacity>100)return;
    char status[24]={0},path[PATH_LEN+48];snprintf(path,sizeof(path),"%s/status",battery_curve.folder);
    FILE*f=fopen(path,"r");if(f){if(!fgets(status,sizeof(status),f))status[0]=0;fclose(f);}
    /* The status alone can stay at Charging while the phone powers a stick, so the current decides. */
    long current=battery_number("current_now",1);
    bool full=!strncmp(status,"Full",4);
    a->battery_charging=full||(!strncmp(status,"Charging",8)&&current>0);
    double shown=(double)capacity;
    long uv=battery_number("voltage_now",-1),counted=battery_number("charge_now",-1),whole=battery_number("charge_full",-1);
    /* The resistance between the cell and where the voltage is measured is more than the cell's own
       (on the phone, 2026-11-11: 3.810 V at +0.16 A and 3.729 V at -0.28 A, 185 mOhm against the 120 of
       the device tree; the estimate fell 8 points during a long copy and would have climbed back
       after it). It is learned from the phone itself: when the current steps by 0.2 A or more within
       15 seconds, the step of the voltage over the step of the current is the resistance then. */
    if(a->battery_ohm<=0)a->battery_ohm=(double)battery_curve.resistance/1e6;
    if(uv>0&&a->battery_uv>0&&a->now>a->battery_at&&a->now-a->battery_at<=15000){
        double di=(double)(current-a->battery_ua)/1e6,dv=(double)(uv-a->battery_uv)/1e6;
        if(di>=0.2||di<=-0.2){double seen=dv/di;if(seen>=0.06&&seen<=0.40)a->battery_ohm+=(seen-a->battery_ohm)*0.25;}
    }
    a->battery_uv=uv;a->battery_ua=current;
    double open=uv>0&&battery_curve.count?battery_curve_level(uv-(long)((double)current*a->battery_ohm)):-1;
    bool kept=battery_gauge_keeps();
    if(kept){
        if(a->battery_estimated){a->battery_estimated=false;if(!getenv("A22X_RECOVERY_BATTERY"))unlink("/run/a22x-recovery/battery-level");}
    }else if(open>=0&&counted>=0&&whole>0){
        long flow=current<0?-current:current;
        /* A heavy current bends the voltage more than one resistance explains: it counts for less, or not at all. */
        double weight=flow<=300000?1.0:(flow>=700000?0.0:0.35);
        if(!a->battery_estimated){a->battery_estimated=true;a->battery_estimate=a->battery_ocv=open;a->battery_samples=1;}
        else{
            double seconds=a->now>a->battery_at?(double)(a->now-a->battery_at)/1000.0:0.0,moved=(double)(counted-a->battery_counted)*100.0/(double)whole;
            /* More than three amperes could have moved is the kernel taking a new start, not charge. */
            double most=3.0e6*seconds/3600.0*100.0/(double)whole+0.3;
            if(moved>=-most&&moved<=most)a->battery_estimate+=moved;
            if(weight>0){a->battery_ocv+=(open-a->battery_ocv)*0.25*weight;a->battery_samples++;}
            double apart=a->battery_ocv-a->battery_estimate;
            /* A cell that is drained for minutes sags more than its resistance explains, and one that is
               charged stands higher (on the phone, 2026-11-11: a copy at 0.2 to 0.5 A drew the estimate
               down ten points in twelve minutes, and it climbed back afterwards). So the voltage is
               believed at four points apart only against the current: while the cell is drained, that
               it holds more; while it is charged, that it holds less. The other way it must be three
               times as far apart. */
            double up=current>60000?BATTERY_APART*3:BATTERY_APART,down=current<-60000?BATTERY_APART*3:BATTERY_APART;
            if(a->battery_samples<=BATTERY_SETTLE)a->battery_estimate=a->battery_ocv;
            else if(apart>=up||apart<=-down){
                double step=seconds*1000.0/BATTERY_STEP_MS;if(step>1.0)step=1.0;
                a->battery_estimate+=apart>0?step:-step;
            }
        }
        if(full)a->battery_estimate=a->battery_ocv=100.0;
        if(a->battery_estimate<0)a->battery_estimate=0;
        if(a->battery_estimate>100)a->battery_estimate=100;
        a->battery_counted=counted;a->battery_at=a->now;shown=a->battery_estimate;
    }
    int percent=(int)(shown+0.5);if(percent>100)percent=100;
    /* Not full until the charger says so: 100 beside a charging bolt would be a promise. */
    if(!full&&a->battery_charging&&percent>99&&(a->battery_estimated||kept))percent=99;
    if(percent!=a->battery_percent||!a->battery[0]){
        /* The same number for the page of the phone's details. */
        const char*run=getenv("A22X_RECOVERY_BATTERY")?NULL:"/run/a22x-recovery/battery-level";
        if(run&&a->battery_estimated){FILE*level=fopen(run,"w");if(level){fprintf(level,"%d\n",percent);fclose(level);}}
    }
    a->battery_percent=percent;snprintf(a->battery,sizeof(a->battery),"%d%%",percent);
}
static void initialize(App*a){
    a->lock_mode=1;a->access_mode=1;
    memset(a,0,sizeof(*a));a->screen_timeout_index=SCREEN_TIMEOUT_DEFAULT;a->pixels=calloc((size_t)NW*NH,sizeof(uint32_t));if(!a->pixels){perror("canvas");exit(1);}a->running=true;a->dirty=true;a->focus=-1;
    a->systems_locale=a->systems_tz=a->recovery_tz=-1;  /* The recovery's language and clock until others are picked. */
    copy(a->python,sizeof(a->python),"/usr/bin/python3");copy(a->backend,sizeof(a->backend),"/opt/a22x-recovery/backend/recoveryctl.py");copy(a->workdir,sizeof(a->workdir),"/run/a22x-recovery/plans/ui");copy(a->fb_path,sizeof(a->fb_path),"/dev/fb0");copy(a->profile,sizeof(a->profile),"linux");
    copy(a->filesystem_helper,sizeof(a->filesystem_helper),"/opt/a22x-recovery/filesystem.py");copy(a->management_helper,sizeof(a->management_helper),"/opt/a22x-recovery/management.py");copy(a->fs_path,sizeof(a->fs_path),"/");
    const char*display_helper=getenv("A22X_RECOVERY_DISPLAY_HELPER");copy(a->display_helper,sizeof(a->display_helper),display_helper&&*display_helper?display_helper:"/opt/a22x-recovery/display.py");
    {const char*wifi_helper=getenv("A22X_RECOVERY_WIFI_HELPER");copy(a->wifi_helper,sizeof(a->wifi_helper),wifi_helper&&*wifi_helper?wifi_helper:"/opt/a22x-recovery/wifi.py");
     const char*wifi_state=getenv("A22X_RECOVERY_WIFI_STATE");copy(a->wifi_state_path,sizeof(a->wifi_state_path),wifi_state&&*wifi_state?wifi_state:"/run/a22x-wifi/state.json");}
    const char*usb_helper=getenv("A22X_RECOVERY_USB_FILES_HELPER");copy(a->usb_helper,sizeof(a->usb_helper),usb_helper&&*usb_helper?usb_helper:"/opt/a22x-recovery/usb_files.py");a->usb_selected=-1;a->usb_read_only=true;
    const char*sysinfo=getenv("A22X_RECOVERY_SYSINFO_HELPER"),*themes_tool=getenv("A22X_RECOVERY_THEMES_HELPER"),*bundled=getenv("A22X_THEMES_BUNDLED"),*imported=getenv("A22X_THEMES_RAM");
    if(sysinfo&&*sysinfo)sysinfo_helper=sysinfo;
    if(themes_tool&&*themes_tool)themes_helper=themes_tool;
    if(bundled&&*bundled)theme_folders[0]=bundled;
    if(imported&&*imported)theme_folders[1]=imported;
    a->sysinfo_root=getenv("A22X_SYSINFO_ROOT");theme_files_scan();
    const char*terminal_helper=getenv("A22X_RECOVERY_TERMINAL_HELPER");copy(a->terminal_helper,sizeof(a->terminal_helper),terminal_helper&&*terminal_helper?terminal_helper:"/opt/a22x-recovery/terminal.py");a->terminal_follow=true;
    const char*distro_helper=getenv("A22X_RECOVERY_DISTRO_HELPER");copy(a->distro_helper,sizeof(a->distro_helper),distro_helper&&*distro_helper?distro_helper:"/opt/a22x-recovery/distro.py");
    const char*root_helper=getenv("A22X_RECOVERY_ROOT_HELPER");copy(a->root_helper,sizeof(a->root_helper),root_helper&&*root_helper?root_helper:"/opt/a22x-recovery/root.py");a->root_selected=-1;
    {const char*shot_helper=getenv("A22X_RECOVERY_SCREENSHOT_HELPER");copy(a->shot_helper,sizeof(a->shot_helper),shot_helper&&*shot_helper?shot_helper:"/opt/a22x-recovery/screenshot.py");}
    {const char*card_helper=getenv("A22X_RECOVERY_CARD_HELPER");copy(a->card_helper,sizeof(a->card_helper),card_helper&&*card_helper?card_helper:"/opt/a22x-recovery/sdcard.py");a->card_index=-1;}
    const char*zip_helper=getenv("A22X_RECOVERY_ZIP_HELPER");copy(a->zip_helper,sizeof(a->zip_helper),zip_helper&&*zip_helper?zip_helper:"/opt/a22x-recovery/zip_install.py");
    const char*gsi_helper=getenv("A22X_RECOVERY_GSI_HELPER");copy(a->gsi_helper,sizeof(a->gsi_helper),gsi_helper&&*gsi_helper?gsi_helper:"/opt/a22x-recovery/android_gsi.py");a->android_selected=-1;
    const char*samsung_helper=getenv("A22X_RECOVERY_SAMSUNG_HELPER");copy(a->samsung_helper,sizeof(a->samsung_helper),samsung_helper&&*samsung_helper?samsung_helper:"/opt/a22x-recovery/samsung_firmware.py");
    const char*flash_helper=getenv("A22X_RECOVERY_FLASH_HELPER");copy(a->flash_helper,sizeof(a->flash_helper),flash_helper&&*flash_helper?flash_helper:"/opt/a22x-recovery/flash.py");a->flash_selected=-1;a->vibration=2;a->systems_selected=-1;
    const char*install_helper=getenv("A22X_RECOVERY_INSTALL_HELPER");copy(a->install_helper,sizeof(a->install_helper),install_helper&&*install_helper?install_helper:"/opt/a22x-recovery/install.py");a->install.pending_selected=-1;
    const char*profile=getenv("A22X_RECOVERY_PROFILE_RENDER");a->profile_render=profile&&*profile&&strcmp(profile,"0");
    a->volume_index=a->fs_destination_index=a->tools_volume_index=-1;a->fs_inventory_destination=FILE_VOLUMES;a->nav_depth=1; /* The trail starts at Home. */
    struct utsname u;if(uname(&u)==0)snprintf(a->kernel,sizeof(a->kernel),T("Linux %.85s / %.24s"),u.release,u.machine);
}
static void sample_document(App*a,unsigned int lines){
    size_t used=0;
    for(unsigned int n=0;n<lines;n++){
        char linebuf[160];int length=snprintf(linebuf,sizeof(linebuf),"line-%03u  Ґіїє  Linux configuration\n",n);
        if(length<=0||used+(size_t)length>=sizeof(a->fs_text))break;
        memcpy(a->fs_text+used,linebuf,(size_t)length);used+=(size_t)length;
    }
    a->fs_text[used]=0;a->fs_length=used;a->fs_cursor=a->fs_scroll=0;a->fs_caret_follow=false;a->text_row_count=0;
}
static void sample_system(App*a);
static bool preview(App*a,const char*name){
    copy(a->sys_model,sizeof(a->sys_model),"SM-A226B");copy(a->sys_version,sizeof(a->sys_version),"0.6");
    a->preview=true;a->inventory_ok=true;a->successful=true;a->battery_percent=92;copy(a->battery,sizeof(a->battery),"92%");copy(a->status,sizeof(a->status),"Illustrative UI preview - not a phone capture");copy(a->kernel,sizeof(a->kernel),"Kernel and device status appear here");copy(a->repositories[0],PATH_LEN,"/mnt/backup/A22xBackups");a->repository_count=1;copy(a->battery,sizeof(a->battery),"--");
    if(!strcmp(name,"home"))a->screen=HOME;else if(!strcmp(name,"backup")||!strcmp(name,"backup-android")){a->screen=BACKUP;a->linux_backup_available=true;a->systems_foreign=!strcmp(name,"backup-android");}else if(!strcmp(name,"plan")){a->screen=PLAN;a->plan_can_execute=true;a->plan_bytes=53427044352ULL;copy(a->plan_parts,sizeof(a->plan_parts),"boot  +  userdata");copy(a->plan_backup,sizeof(a->plan_backup),"/mnt/backup/A22xBackups/linux-example");copy(a->plan_id,sizeof(a->plan_id),"example-plan-preview");}
    else if(!strcmp(name,"linux-plan")||!strcmp(name,"linux-wipe")||!strcmp(name,"linux-restore")||!strcmp(name,"linux-space-blocked")){
        a->screen=PLAN;a->plan_linux_v2=a->plan_linux_admitted=a->plan_space_known=a->plan_estimate_known=true;a->plan_can_execute=strcmp(name,"linux-space-blocked");
        a->plan_wipe=!strcmp(name,"linux-wipe");a->plan_restore=!strcmp(name,"linux-restore");a->plan_used_bytes=25ULL*1024*1024*1024;a->plan_archive_bytes=11ULL*1024*1024*1024;a->plan_free_bytes=a->plan_can_execute?32ULL*1024*1024*1024:1024*1024*1024;a->plan_reserve_bytes=64*1024*1024;
        copy(a->plan_backup,sizeof(a->plan_backup),"/mnt/backup/A22xBackups/linux-example");copy(a->plan_boot_action,sizeof(a->plan_boot_action),a->plan_wipe?"preserve":(a->plan_restore?"restore-last":"read-only-backup"));
        copy(a->plan_targets,sizeof(a->plan_targets),a->plan_wipe?"userdata  /dev/mmcblk0p54\nCreate empty ext4 filesystem":"userdata  /dev/mmcblk0p54\nRestore filesystem blocks\nboot  /dev/mmcblk0p41\nRestore BOOT last");
        if(!a->plan_can_execute)copy(a->blockers,sizeof(a->blockers),"INSUFFICIENT_SPACE\nThe measured archive and free-space reserve do not fit on this destination.");
    }
    else if(!strcmp(name,"linux-prepare")){a->screen=PREPARE_FILESYSTEM;a->prepare_job=J_BACKUP_PLAN;a->prepare_known=a->prepare_mounted=a->prepare_owned=true;copy(a->prepare_token,sizeof(a->prepare_token),"preview");copy(a->prepare_mountpoint,sizeof(a->prepare_mountpoint),"/mnt/volumes/userdata");}
    else if(!strcmp(name,"usb-files")||!strcmp(name,"usb-active")||!strcmp(name,"usb-unavailable")){
        a->screen=USB_FILES;a->usb_known=true;a->usb_available=strcmp(name,"usb-unavailable");copy(a->usb_state,sizeof(a->usb_state),!strcmp(name,"usb-active")?"active":"stopped");
        if(!strcmp(name,"usb-files")){a->usb_candidate_count=1;a->usb_selected=0;UsbCandidate*c=&a->usb_candidates[0];copy(c->id,sizeof(c->id),"userdata");copy(c->label,sizeof(c->label),"Linux / userdata");copy(c->mountpoint,sizeof(c->mountpoint),"/mnt/volumes/userdata");c->writable=true;a->usb_read_only=false;}
        else if(!strcmp(name,"usb-active")){copy(a->usb_session,sizeof(a->usb_session),"preview-session");copy(a->usb_export_id,sizeof(a->usb_export_id),"userdata");a->usb_export_valid=a->usb_export_writable=true;}
        else copy(a->usb_reason,sizeof(a->usb_reason),"USB file transfer is unavailable in this illustrative preview. Refresh status to read the helper's actual reason.");
    }
    else if(!strcmp(name,"device-restore")){a->screen=PLAN;a->device=true;a->plan_restore=true;copy(a->profile,sizeof(a->profile),"android");a->plan_bytes=53460598784ULL;copy(a->plan_parts,sizeof(a->plan_parts),"boot  +  metadata  +  userdata");copy(a->plan_backup,sizeof(a->plan_backup),"/mnt/backup/A22xBackups/android-example");copy(a->plan_targets,sizeof(a->plan_targets),"userdata  /dev/mmcblk0p54\nmetadata  /dev/mmcblk0p31\nboot  /dev/mmcblk0p41");copy(a->blockers,sizeof(a->blockers),"DEVICE_RESTORE_NOT_QUALIFIED\nPhysical partition writes are not admitted in this prototype.");}
    else if(!strcmp(name,"restore")){a->screen=LIST;a->backup_count=2;for(int i=0;i<2;i++){BackupEntry*e=&a->backups[i];copy(e->profile,sizeof(e->profile),i?"android":"linux");copy(e->name,sizeof(e->name),i?"Android - saved state":"Linux - saved state");copy(e->created,sizeof(e->created),"Illustrative snapshot entry");e->bytes=53393489920ULL;}}
    else if(!strcmp(name,"snapshot")||!strcmp(name,"snapshot-linux")||!strcmp(name,"backup-name")){
        /* 2026-11-11: the page of one backup with Rename and Delete, and the prompt for its new name. */
        bool compact=!strcmp(name,"snapshot-linux");a->backup_count=1;a->selected_backup=0;BackupEntry*e=&a->backups[0];memset(e,0,sizeof(*e));
        copy(e->profile,sizeof(e->profile),compact?"linux":"recovery");copy(e->name,sizeof(e->name),compact?"linux-20261108-221500-417":"recovery-20261108-221500-417");
        copy(e->created,sizeof(e->created),"2026-11-08T22:15:00Z");e->bytes=compact?6871947674ULL:41943040ULL;
        snprintf(e->path,sizeof(e->path),"/mnt/volumes/sdcard-p1/A22xBackups/%s",e->name);
        if(compact){copy(e->schema,sizeof(e->schema),"a22x.backup.v2");copy(e->workflow,sizeof(e->workflow),"linux-partclone-v2");}
        a->screen=DETAIL;
        if(!strcmp(name,"backup-name")){a->fs_prompt_kind=J_BACKUP_NAME;copy(a->fs_prompt,sizeof(a->fs_prompt),"before-root");a->prompt_cursor=strlen(a->fs_prompt);a->screen=FILE_PROMPT;}
    }
    else if(!strcmp(name,"restore-android")||!strcmp(name,"restore-android-empty")||!strcmp(name,"restore-android-reading")||!strcmp(name,"verify-android")||!strcmp(name,"copy-detail")||!strcmp(name,"copy-folder")||!strcmp(name,"copy-data")||!strcmp(name,"copy-data-on")){
        /* 2026-11-11: Android's category of Restore and Verify, and the page of one copy; copy-data: a copy that holds Android's own data too. */
        a->screen=LIST;a->list_category=1;a->list_for_verify=!strcmp(name,"verify-android");a->copies_known=strcmp(name,"restore-android-reading")!=0;a->copy_pick=-1;
        static const char*const labels[]={"boot","dtbo","vbmeta","vbmeta_system","vbmeta_vendor","super","prism","optics"};
        if(strcmp(name,"restore-android-empty")&&strcmp(name,"restore-android-reading")){
            a->copy_count=3;
            for(unsigned int n=0;n<3;n++){
                PhoneCopy*c=&a->copies[n];memset(c,0,sizeof(*c));copy(c->volume,sizeof(c->volume),n==2?"sdcard-p1":"usb-p1");copy(c->place,sizeof(c->place),n==2?"alarm_root":"SANDISK");
                memset(c->sha,'a'+(int)n,64);c->sha[64]=0;c->same=true;c->writable=n!=2;c->archive=n!=1;c->parts=n==2?2:8;c->bytes=n==2?33554432ULL+7239368704ULL:8040562688ULL;c->file_bytes=n==1?0:(n==2?3865470566ULL:4402341478ULL);
                snprintf(c->created,sizeof(c->created),"2026-11-%02uT%02u:10:50Z",11-n,8+n*3);
                snprintf(c->path,sizeof(c->path),"/A22xAndroidBackups/android-202611%02u-%02u1050%s",11-n,8+n*3,c->archive?".tar.zst":"");
                for(unsigned int k=0;k<c->parts;k++)copy(c->labels[k],sizeof(c->labels[k]),n==2?(k?"super":"boot"):labels[k]);
            }
        }
        if(!strncmp(name,"copy-data",9)){
            PhoneCopy*c=&a->copies[0];c->parts=10;copy(c->labels[8],sizeof(c->labels[8]),"metadata");copy(c->labels[9],sizeof(c->labels[9]),"userdata");c->bytes+=53431222272ULL;c->file_bytes+=1116691497ULL;
            a->screen=COPY_DETAIL;a->copy_pick=0;a->copy_off=!strcmp(name,"copy-data-on")?0:copy_data_parts(c);
        }else if(strcmp(name,"copy-detail")&&strcmp(name,"copy-folder")){
            a->copy_waiting_count=1;CopyVolume*v=&a->copy_waiting[0];memset(v,0,sizeof(*v));copy(v->id,sizeof(v->id),"sdcard-p2");copy(v->token,sizeof(v->token),"preview-volume-token");copy(v->label,sizeof(v->label),"SD card");copy(v->fs,sizeof(v->fs),"exfat");v->bytes=63864569856ULL;
        }else{a->screen=COPY_DETAIL;a->copy_pick=!strcmp(name,"copy-folder")?1:0;a->copy_off=!strcmp(name,"copy-folder")?0:1u<<6;if(!strcmp(name,"copy-folder"))a->copies[1].writable=false;}
    }
    else if(!strcmp(name,"torch-home")||!strcmp(name,"torch-menu")||!strcmp(name,"torch-settings")){
        /* 2026-11-11: the flashlight on Home, in the power menu and in Settings. */
        a->torch_checked=a->torch_available=true;a->torch_max=13;a->torch_level=9;a->torch_on=strcmp(name,"torch-settings")!=0;
        if(!strcmp(name,"torch-menu")){a->menu_return=HOME;a->screen=POWER_MENU;}
        else if(!strcmp(name,"torch-settings")){a->screen=TOOLS;a->tools_scroll=600;}
    }
    else if(!strcmp(name,"identity-copies")){
        a->screen=IDENTITY_RESTORE;a->id_kind=0;a->volumes[1].read_only=false;identity_select(a,1);a->id_backup_count=8;a->id_pick=1;
        for(unsigned int n=0;n<8;n++){
            snprintf(a->id_backups[n].path,sizeof(a->id_backups[n].path),"/A22xIdentityBackups/identity-202611%02u-221500",11-n);
            snprintf(a->id_backups[n].created,sizeof(a->id_backups[n].created),"2026-11-%02uT22:15:00Z",11-n);
            memset(a->id_backups[n].sha,'a',64);a->id_backups[n].sha[64]=0;a->id_backups[n].parts=7;a->id_backups[n].bytes=56u<<20;a->id_backups[n].same=n!=3;
        }
    }
    else if(!strcmp(name,"card")||!strcmp(name,"card-busy")||!strcmp(name,"card-none")||!strcmp(name,"card-reading")){
        /* 2026-11-11: Partition a card, with the card a phone in use holds. */
        a->screen=CARD_PAGE;a->card_known=strcmp(name,"card-reading")!=0;a->card_count=0;a->card_index=-1;a->card_linux=16;a->card_swap=2;
        if(!strcmp(name,"card")||!strcmp(name,"card-busy")){
            CardDisk*c=&a->cards[0];memset(c,0,sizeof(*c));a->card_count=1;a->card_index=0;
            copy(c->id,sizeof(c->id),"sdcard");copy(c->name,sizeof(c->name),"SD64G");copy(c->kind,sizeof(c->kind),"sd");copy(c->table,sizeof(c->table),"mbr");memset(c->token,'c',64);
            c->size=63864569856ULL;c->usable=63862472704ULL;c->align=4u<<20;c->min_files=256u<<20;c->part_count=2;
            c->parts[0].number=1;c->parts[0].size=268435456ULL;copy(c->parts[0].fs,sizeof(c->parts[0].fs),"vfat");
            c->parts[1].number=2;c->parts[1].size=63590891520ULL;copy(c->parts[1].fs,sizeof(c->parts[1].fs),"ext4");copy(c->parts[1].label,sizeof(c->parts[1].label),"alarm_root");
            if(!strcmp(name,"card-busy")){copy(c->busy_code,sizeof(c->busy_code),"CARD_MOUNTED");copy(c->busy,sizeof(c->busy),"Unmount the card in Files first");}
        }
    }
    else if(!strcmp(name,"storage")){a->screen=STORAGE;a->repository_count=2;copy(a->repositories[1],PATH_LEN,"/mnt/volumes/usb-p1/A22xBackups");copy(a->repository_volume[1],32,"usb-p1");copy(a->repository_root[1],PATH_LEN,"/mnt/volumes/usb-p1");a->repository_read_only[1]=true;a->storage_known=true;a->storage_mounted=true;}else if(!strcmp(name,"system"))a->screen=SYSTEM;
    else if(!strcmp(name,"power")){a->screen=POWER;a->allow_power=true;}
    else if(!strcmp(name,"download-confirm")){a->screen=POWER_CONFIRM;a->power_intent=203;a->allow_power=true;}
    else if(!strcmp(name,"os"))screen(a,OS_MENU);
    else if(!strcmp(name,"battery-charging")||!strcmp(name,"battery-low")){a->screen=HOME;bool low=!strcmp(name,"battery-low");a->battery_percent=low?12:64;copy(a->battery,sizeof(a->battery),low?"12%":"64%");a->battery_charging=!low;}
    else if(!strcmp(name,"terminal-colors"))a->screen=TERMINAL_THEME;
    else if(!strcmp(name,"splash"))a->screen=SPLASH;
    else if(!strcmp(name,"splash-bar")){a->screen=SPLASH;a->splash_percent=60;}
    else if(!strcmp(name,"animations"))a->screen=ANIMATIONS;
    else if(!strcmp(name,"tools-lock")){a->screen=TOOLS;a->lock_enabled=true;a->tools_scroll=620;}
    else if(!strcmp(name,"tools-charger")){a->screen=TOOLS;a->charger_known=a->charger_off=true;a->tools_scroll=520;}
    else if(!strcmp(name,"lockscreen")||!strcmp(name,"lock-password")||!strcmp(name,"lock-wrong")||!strcmp(name,"lock-keyboard")){
        a->screen=HOME;render(a);lock_frost(a);a->locked=true;a->screen=LOCKSCREEN;
        if(name[4]=='-'){a->fs_prompt_kind=J_LOCK_ENTER;copy(a->fs_prompt,sizeof(a->fs_prompt),"12345");a->screen=FILE_PROMPT;a->lock_keyboard=name[5]=='k';if(name[5]=='w')copy(a->lock_note,sizeof(a->lock_note),"Wrong password");}
    }
    else if(!strcmp(name,"about")){a->screen=SYSTEM;sample_system(a);}
    else if(!strcmp(name,"about-scroll")){a->screen=SYSTEM;sample_system(a);a->page_content=2000;a->page_scroll=500;}
    else if(!strcmp(name,"power-menu")){a->allow_power=true;screen(a,HOME);render(a);power_menu_open(a);}
    else if(!strcmp(name,"terminal-running")){a->screen=HOME;memset(a->terminal_session,'1',64);a->terminal_session[64]=0;a->terminal_left=a->terminal_left_busy=true;}
    else if(!strcmp(name,"terminal-finished")){a->screen=HOME;a->terminal_resume=true;}
    else if(!strcmp(name,"power-terminal")){a->allow_power=true;a->screen=POWER_CONFIRM;a->power_intent=201;memset(a->terminal_session,'1',64);a->terminal_session[64]=0;a->terminal_busy=true;}
    else if(!strcmp(name,"account")||!strcmp(name,"account-name")){
        a->screen=FILE_PROMPT;copy(a->systems_username,sizeof(a->systems_username),"olena");copy(a->systems_password,sizeof(a->systems_password),"секрет12");
        a->fs_prompt_kind=0;systems_account_focus(a,!strcmp(name,"account")?J_SYS_ACCOUNT_PASS:J_SYS_ACCOUNT_USER);
    }
    else if(!strcmp(name,"progress-import")){
        a->screen=PROGRESS;a->job.pid=1;a->job.kind=J_SYS_IMPORT;copy(a->progress_phase,sizeof(a->progress_phase),"image-unpack");
        copy(a->progress_part,sizeof(a->progress_part),"mobian-rockchip-phosh-13.0.img.xz");a->progress_total=1026326552ULL;a->progress_done=412000000ULL;
        a->now=milliseconds();a->job_started=a->now-185000u;copy(a->progress_mark_phase,sizeof(a->progress_mark_phase),"image-unpack");a->progress_mark_at=a->now-120000u;a->progress_mark_done=0;
    }
    else if(!strncmp(name,"farewell-",9)){int intent=200+atoi(name+9);if(intent<200||intent>203)intent=200;a->screen=SPLASH;a->exit_code=intent;}
    else if(!strcmp(name,"restore-empty")){a->screen=LIST;a->backup_count=0;copy(a->message,sizeof(a->message),"Choose an SD card or USB stick with backups, or restore a partition image.");}
    else if(!strcmp(name,"restore-image")||!strcmp(name,"restore-image-mounted")){
        a->screen=FLASH_REVIEW;a->flash_restore=a->flash_review_valid=true;a->flash_offline=!strcmp(name,"restore-image");copy(a->flash_label,sizeof(a->flash_label),"userdata");
        copy(a->flash_name,sizeof(a->flash_name),"userdata.img.zst");copy(a->flash_kind,sizeof(a->flash_kind),"ext4 filesystem image");a->flash_bytes=3456532659ULL;
        copy(a->flash_sha,sizeof(a->flash_sha),"51e89bf2746f00592082347074d48010f4be8ebe4c8aa4d27142d04c51ca35ce");copy(a->flash_identity,sizeof(a->flash_identity),a->flash_sha);
        copy(a->flash_checksum,sizeof(a->flash_checksum),"userdata.img.zst.transport.json");copy(a->flash_warnings,sizeof(a->flash_warnings),tr("Everything on userdata is replaced by this image"));
        if(!a->flash_offline){append(a->flash_warnings,sizeof(a->flash_warnings),"\n");append(a->flash_warnings,sizeof(a->flash_warnings),tr("The partition is mounted. Unmount it in Files before writing"));}
    }
    else if(!strcmp(name,"nav-buttons")){a->nav_mode=NAV_BUTTONS;a->screen=HOME;}
    else if(!strcmp(name,"nav-off")){a->nav_mode=NAV_OFF;a->screen=TOOLS;}
    else if(!strcmp(name,"nav-back")){a->screen=TOOLS;a->gesture=GESTURE_BACK;a->gesture_x0=4;a->gesture_x=120;a->gesture_y=800;a->gesture_ready=true;}
    else if(!strcmp(name,"nav-terminal")){a->screen=HOME;a->gesture=GESTURE_BOTTOM;a->gesture_y0=1590;a->gesture_y=1400;a->gesture_ready=a->gesture_terminal=true;}
    else if(!strcmp(name,"lock-new")){a->fs_prompt_kind=J_LOCK_SET;a->screen=FILE_PROMPT;}
    else if(!strcmp(name,"lock-repeat")){a->fs_prompt_kind=J_LOCK_CONFIRM;a->lock_flow=LF_CHANGE;copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->screen=FILE_PROMPT;}
    else if(!strcmp(name,"access")){a->screen=ACCESS_REQUEST;copy(a->access_channel,sizeof(a->access_channel),"ssh");copy(a->access_peer,sizeof(a->access_peer),"172.16.42.2");}
    else if(!strcmp(name,"result-done")||!strcmp(name,"result-failed")||!strcmp(name,"progress-cancel")||!strcmp(name,"progress-wait")){
        /* 2026-11-11: the work and result pages in tiles. */
        if(!strcmp(name,"result-done")){a->screen=RESULT;a->successful=true;copy(a->status,sizeof(a->status),"The installer finished");a->result_return=ANDROID_MENU;
            copy(a->message,sizeof(a->message),"Changed: boot\n\n- Patching ramdisk\n- Pre-init storage partition: cache\n- Repacking boot image\n- Flashing new boot image\n- Unmounting partitions\n- Done");}
        else if(!strcmp(name,"result-failed")){a->screen=RESULT;a->successful=false;copy(a->status,sizeof(a->status),"ZIP_INSTALLER_FAILED");a->result_return=ANDROID_MENU;
            copy(a->message,sizeof(a->message),"The installer ended with an error. Changed: Nothing\n\nE3004: This package is for device: gta4xlwifi; this device is a22x.\nUpdater process ended with ERROR: 7");}
        else{a->screen=PROGRESS;a->job.pid=1;a->job.kind=!strcmp(name,"progress-cancel")?J_IMAGES_DOWNLOAD:J_ZIP_RUN;a->now=2000000;a->job_started=a->now-1013000u;
            copy(a->progress_phase,sizeof(a->progress_phase),!strcmp(name,"progress-cancel")?"image-download":"Unpacking");
            if(!strcmp(name,"progress-cancel")){copy(a->progress_part,sizeof(a->progress_part),"lineage-22.2-20250621-UNOFFICIAL-gsi_arm64_vN.img.gz");a->progress_total=1086466161ULL;a->progress_done=402000000ULL;a->progress_mark_at=a->now-95000u;a->progress_mark_done=0;}}
    }
    else if(!strcmp(name,"progress")){a->screen=PROGRESS;a->job.pid=1;a->job.kind=J_BACKUP_PLAN;copy(a->progress_phase,sizeof(a->progress_phase),"compact-compress");copy(a->progress_part,sizeof(a->progress_part),"userdata");a->progress_done=14926253014ULL;a->progress_total=24103593114ULL;a->now=720000;a->progress_mark_at=1;a->progress_mark_done=0;}
    else if(!strcmp(name,"theme"))a->screen=THEME;
    else if(!strcmp(name,"language"))a->screen=LANGUAGE;
    else if(!strcmp(name,"systems-setup")){copy(a->systems_manifest,sizeof(a->systems_manifest),"/Downloads/Fedora-Workstation-Live-45.iso");a->systems_desktop=2;screen(a,SYSTEMS_SETUP);}
    else if(!strcmp(name,"systems-locale")){a->systems_desktop=2;screen(a,SYSTEMS_LOCALE);}
    else if(!strcmp(name,"systems-timezone")){a->systems_desktop=2;a->utc_offset=60;a->systems_tz_region=0;for(unsigned int i=0;i<TZ_ZONES;i++)if(!strcmp(tz_zones[i].id,"Europe/Kyiv"))a->systems_tz=(int)i;screen(a,SYSTEMS_TIMEZONE);}
    else if(!strcmp(name,"systems-shells")){copy(a->systems_manifest,sizeof(a->systems_manifest),"/Downloads/debian-13-arm64.tar.xz");a->systems_desktop=2;a->systems_shells=(1u<<4)|(1u<<6);a->systems_greeter=1;screen(a,SYSTEMS_SHELLS);}
    else if(!strcmp(name,"systems-shells-gnome")){copy(a->systems_manifest,sizeof(a->systems_manifest),"/Downloads/debian-13-arm64.tar.xz");a->systems_desktop=4;a->systems_shells=(1u<<3)|(1u<<2);a->systems_greeter=4;screen(a,SYSTEMS_SHELLS);}
    else if(!strcmp(name,"tools")||!strcmp(name,"tools-end")){
        a->screen=TOOLS;a->tools_known=a->ssh_available=a->ssh_running=a->ssh_ephemeral=a->logs_export_available=true;a->ssh_keys=1;a->ssh_port=22;
        copy(a->ssh_address,sizeof(a->ssh_address),"172.16.42.1");copy(a->ssh_fingerprint,sizeof(a->ssh_fingerprint),"SHA256:ExamplePreviewKeyNotARealPhoneFingerprint");
        if(!strcmp(name,"tools-end")){a->screen=TOOLS;render(a);a->tools_scroll=tools_max_scroll(a);} /* 2026-11-11: the reset of the settings is the last thing on the page. */
    }
    else if(!strcmp(name,"terminal")||!strcmp(name,"terminal-unicode")||!strcmp(name,"terminal-scroll")||!strcmp(name,"terminal-ready")){
        a->screen=TERMINAL;a->terminal_known=a->terminal_available=true;terminal_reset_view(a);
        copy(a->terminal_state,sizeof(a->terminal_state),!strcmp(name,"terminal-ready")?"stopped":"active");
        if(strcmp(name,"terminal-ready")){
            memset(a->terminal_session,'1',64);a->terminal_session[64]=0;a->terminal_rows=TERMINAL_ROWS;a->terminal_cols=TERMINAL_COLS;
            const char*intro="\033[1;32mroot@recovery\033[0m:~# printf 'Hello'\r\n\033[36mУкраїнська: Ґґ Іі Її Єє\033[0m\r\nRecovery RAM shell\r\n";tm_feed(&a->terminal,intro,strlen(intro));
            for(unsigned n=0;n<(!strcmp(name,"terminal-unicode")?8u:70u);n++){char linebuf[160];int length=snprintf(linebuf,sizeof(linebuf),"\033[%um%03u\033[0m  /mnt/volumes/userdata  line\r\n",n%2?32:34,n);tm_feed(&a->terminal,linebuf,(size_t)length);}
            const char*prompt="\033[1;32mroot@recovery\033[0m:~# ";tm_feed(&a->terminal,prompt,strlen(prompt));a->terminal_scroll=terminal_max_scroll(a);
            if(!strcmp(name,"terminal-scroll")){a->terminal_scroll=terminal_max_scroll(a)/2+11;a->terminal_follow=false;}
        }
    }
    else if(!strcmp(name,"identity")||!strcmp(name,"identity-saved")){
        a->screen=IDENTITY;a->id_known=true;static const char*const labels[]={"efs","sec_efs","nvdata","nvcfg","persist","protect1","protect2"};
        a->id_count=7;a->id_bytes=0;for(unsigned int n=0;n<7;n++){copy(a->id_labels[n],sizeof(a->id_labels[0]),labels[n]);a->id_sizes[n]=8u<<20;a->id_bytes+=8u<<20;}
        a->volumes[1].read_only=false;identity_select(a,1);
        if(!strcmp(name,"identity-saved"))copy(a->id_done,sizeof(a->id_done),"/A22xIdentityBackups/identity-20261108-221500");
    }
    else if(!strcmp(name,"android-plan")||!strcmp(name,"android-plan-bad")||!strcmp(name,"os-plan")||!strcmp(name,"home-plan")){
        /* 2026-11-10: an installation prepared in the Android app, as install_plan.py shows it. */
        a->aplan_known=a->aplan_present=true;a->aplan_valid=strcmp(name,"android-plan-bad")!=0;a->aplan_erase=true;
        copy(a->aplan_name,sizeof(a->aplan_name),"Ubuntu Server");copy(a->aplan_version,sizeof(a->aplan_version),"24.04.3");a->aplan_size=216215656u;
        copy(a->aplan_catalog,sizeof(a->aplan_catalog),"ubuntu-24.04");copy(a->aplan_file,sizeof(a->aplan_file),"ubuntu-24.04.3-preinstalled-server-arm64.img.xz");
        copy(a->aplan_desktop_id,sizeof(a->aplan_desktop_id),"phosh");a->aplan_shells=1u<<3;copy(a->aplan_greeter_id,sizeof(a->aplan_greeter_id),"auto");
        a->aplan_apps=(1u<<8)|(1u<<15);copy(a->aplan_user,sizeof(a->aplan_user),"user");copy(a->aplan_hash,sizeof(a->aplan_hash),"$6$preview$x");
        copy(a->aplan_locale,sizeof(a->aplan_locale),"uk_UA");copy(a->aplan_zone,sizeof(a->aplan_zone),"Europe/Kyiv");copy(a->aplan_script,sizeof(a->aplan_script),"my-setup.sh");a->aplan_script_present=true;
        copy(a->aplan_sha,sizeof(a->aplan_sha),"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
        if(!a->aplan_valid)copy(a->aplan_error,sizeof(a->aplan_error),"This recovery does not know the image windows-11");
        a->screen=!strcmp(name,"os-plan")?OS_MENU:(!strcmp(name,"home-plan")?HOME:APLAN);
    }
    else if(!strcmp(name,"wipe")||!strcmp(name,"wipe-review"))a->screen=!strcmp(name,"wipe")?WIPE:WIPE_REVIEW;
    else if(!strcmp(name,"wipe-android")){a->wipe_android=true;a->screen=WIPE_REVIEW;}
    else if(!strncmp(name,"android-log",11)){
        /* What the owner met (kernel), init giving up for another reason (init), and a panic. */
        const char*kind=name[11]=='-'?name+12:"kernel";a->screen=ANDROID_LOG;a->alog_seconds=9;copy(a->alog_id,sizeof(a->alog_id),"bb9507f313de2d66");
        if(!strcmp(kind,"init")){copy(a->alog_code,sizeof(a->alog_code),"INIT_GAVE_UP");a->alog_seconds=12;copy(a->alog_reason,sizeof(a->alog_reason),"reboot,bootloader,bootstrap-apexd-failed");
            copy(a->alog_lines[0],184,"[1:init]init: Service apexd-bootstrap has 'reboot_on_failure' option and failed, shutting down system.");
            copy(a->alog_lines[1],184,"[1:init]reboot: Restarting system with command 'bootstrap-apexd-failed'");a->alog_line_count=2;}
        else if(!strcmp(kind,"panic")){copy(a->alog_code,sizeof(a->alog_code),"KERNEL_PANIC");a->alog_seconds=20;copy(a->alog_detail,sizeof(a->alog_detail),"Attempted to kill init! exitcode=0x00000b00");
            copy(a->alog_lines[0],184,"[1:init]Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000b00");a->alog_line_count=1;}
        else{copy(a->alog_code,sizeof(a->alog_code),"KERNEL_TOO_OLD");copy(a->alog_needs,sizeof(a->alog_needs),"4.19");copy(a->alog_reason,sizeof(a->alog_reason),"reboot,bpfloader-failed");
            copy(a->alog_lines[0],184,"[861:netbpfload]NetBpfLoad: Android V requires kernel 4.19.");
            copy(a->alog_lines[1],184,"[1:init]init: Service 'bpfloader' (pid 861) exited with status 1 waiting took 0.015000 seconds");
            copy(a->alog_lines[2],184,"[1:init]init: Service bpfloader has 'reboot_on_failure' option and failed, shutting down system.");
            copy(a->alog_lines[3],184,"[1:init]init: Reboot start, reason: reboot,bpfloader-failed, reboot_target: bpfloader-failed");
            copy(a->alog_lines[4],184,"[1:init]reboot: Restarting system with command 'bpfloader-failed'");a->alog_line_count=5;}
    }
    else if(!strcmp(name,"reset-asked")||!strcmp(name,"reset-prompt")){a->wipe_android=a->reset_asked=a->reset_page=true;a->reset_prompt=!strcmp(name,"reset-prompt");a->allow_power=true;a->screen=WIPE_REVIEW;}
    else if(!strcmp(name,"android"))a->screen=ANDROID_MENU;
    else if(!strcmp(name,"root")||!strcmp(name,"root-magisk")||!strcmp(name,"root-vbmeta")||!strcmp(name,"root-apatch")||!strcmp(name,"root-superkey")||!strcmp(name,"zip-review")||!strcmp(name,"zip-queue")){
        /* 2026-11-10: the Root page as root.py lists it on this phone (stock Android, or Magisk in BOOT);
           2026-11-11: with APatch, whose tool and image come with the recovery. */
        static const struct{const char*id,*name,*version,*reason;uint64_t size;bool supported,bundled;}rows[]={
            {"magisk","Magisk","v31.1","",10679283,true,false},{"apatch","APatch","KernelPatch 0.13.9","",296336,true,true}};
        a->root_count=0;
        for(unsigned int i=0;i<sizeof(rows)/sizeof(rows[0]);i++){RootTool*t=&a->root_tools[a->root_count++];memset(t,0,sizeof(*t));
            copy(t->id,sizeof(t->id),rows[i].id);copy(t->name,sizeof(t->name),rows[i].name);copy(t->version,sizeof(t->version),rows[i].version);
            copy(t->reason,sizeof(t->reason),rows[i].reason);t->size=rows[i].size;t->supported=rows[i].supported;t->bundled=t->needs_key=rows[i].bundled;copy(t->published,sizeof(t->published),"2026-03-21");
            if(t->supported)copy(t->sha256,sizeof(t->sha256),"89cefac0a318afa39fb3880c0000000000000000000000000000000000000000");}
        copy(a->root_boot_kind,sizeof(a->root_boot_kind),"android");copy(a->root_boot_release,sizeof(a->root_boot_release),"4.14.186-27218697");
        a->root_boot_magisk=!strcmp(name,"root-magisk");a->root_verification=!strcmp(name,"root-vbmeta");a->root_needs_reset=!strcmp(name,"root");
        copy(a->root_boot_root,sizeof(a->root_boot_root),a->root_boot_magisk?"magisk":"");a->root_key_tool=-1;
        if(a->root_boot_magisk)copy(a->root_stock_path,sizeof(a->root_stock_path),"/a22x-recovery/saved-boot/stock-A226BXXSBDZC3-5df570768942.img");
        a->root_known=true;a->screen=ROOT_PAGE;
        if(!strcmp(name,"zip-review")||!strcmp(name,"zip-queue")){
        copy(a->zip_name,sizeof(a->zip_name),"Magisk-v31.1.zip");copy(a->zip_title,sizeof(a->zip_title),"Magisk");copy(a->zip_what,sizeof(a->zip_what),"magisk");copy(a->zip_installer,sizeof(a->zip_installer),"script");
        copy(a->zip_sha,sizeof(a->zip_sha),"89cefac0a318afa39fb3880c45c87b036c05bc1ebf02a7a715b7f8d7c26ac6e7");a->zip_size=10679283;a->zip_files=77;a->zip_known=true;
        copy(a->zip_parts,sizeof(a->zip_parts),"boot, dtbo, vbmeta, super, system, vendor, product, odm");a->screen=ZIP_REVIEW;a->allow_power=true;
        copy(a->zip_volume,sizeof(a->zip_volume),"sdcard-p1");copy(a->zip_path,sizeof(a->zip_path),"/Magisk-v31.1.zip");zip_item_from_review(a,&a->zip_queue[0]);a->zip_count=1;
        if(!strcmp(name,"zip-queue")){
            /* 2026-11-11: three ZIPs that one hold runs in order, and a restart asked for afterwards. */
            static const char*const more[]={"lineage-22.2-20261101-UNOFFICIAL-a22x.zip","MindTheGapps-15.0.0-arm64-20261020.zip"};
            for(unsigned int n=0;n<2;n++){a->zip_queue[n+1]=a->zip_queue[0];copy(a->zip_queue[n+1].name,sizeof(a->zip_queue[0].name),more[n]);snprintf(a->zip_queue[n+1].path,sizeof(a->zip_queue[0].path),"/%s",more[n]);}
            ZipItem first=a->zip_queue[1];a->zip_queue[1]=a->zip_queue[2];a->zip_queue[2]=a->zip_queue[0];a->zip_queue[0]=first;a->zip_count=3;a->zip_restart=true;
        }}
    if(!strcmp(name,"root-apatch")){a->root_key_tool=1;copy(a->root_superkey,sizeof(a->root_superkey),"Preview-Key-2026");}
        if(!strcmp(name,"root-superkey")){a->root_key_tool=1;a->fs_prompt_kind=J_ROOT_SUPERKEY;copy(a->fs_prompt,sizeof(a->fs_prompt),"Preview-Key-2026");a->prompt_cursor=strlen(a->fs_prompt);a->screen=FILE_PROMPT;}
    }
    else if(!strcmp(name,"samsung-review")){
        /* Samsung's A226BXXSBDZC3 as samsung_firmware.py reads it (2026-11-10). */
        static const struct{const char*file,*name,*action,*target,*reason;uint64_t size;}rows[]={
            {"AP","boot.img.lz4","write","boot","",14197578u},{"AP","recovery.img.lz4","kept","","This recovery stays",25952224u},
            {"AP","super.img.lz4","write","super","",4146139410u},{"AP","userdata.img.lz4","instead","","Formatted for Android instead, as Samsung's recovery does after Odin",1199093275u},
            {"AP","dpm-verified.img.lz4","refused","","Firmware of the bootloader chain stays as it is",121570u},{"AP","vbmeta.img.lz4","write","vbmeta","",3223u},
            {"AP","dtbo.img.lz4","write","dtbo","",36614u},{"AP","tee-verified.img.lz4","refused","","Firmware of the bootloader chain stays as it is",506493u},
            {"AP","vbmeta_system.img.lz4","write","vbmeta_system","",2578u},{"CSC","A22X_EUR_OPEN.pit","refused","","A partition table: it would repartition the phone",8064u},
            {"CSC","cache.img.lz4","instead","","Wiped instead; the recovery's own folder stays",1551u},{"CSC","omr.img.lz4","kept","","Kept as it is",1326u},
            {"CSC","prism.img.lz4","write","prism","",472812488u},{"CSC","optics.img.lz4","write","optics","",582406u},
            {"BL","preloader.img.lz4","refused","","The bootloader chain is never written by this recovery",302196u},
            {"BL","lk-verified.img.lz4","refused","","The bootloader chain is never written by this recovery",1076763u},
            {"CP","md1img.img.lz4","kept","","The modem stays as it is",41848476u}};
        a->samsung_row_count=0;for(size_t n=0;n<sizeof(rows)/sizeof(rows[0]);n++){SamsungRow*r=&a->samsung_rows[a->samsung_row_count++];memset(r,0,sizeof(*r));
            copy(r->file,sizeof(r->file),rows[n].file);copy(r->name,sizeof(r->name),rows[n].name);copy(r->action,sizeof(r->action),rows[n].action);
            copy(r->target,sizeof(r->target),rows[n].target);copy(r->reason,sizeof(r->reason),rows[n].reason);r->size=rows[n].size;}
        copy(a->samsung_build,sizeof(a->samsung_build),"A226BXXSBDZC3");copy(a->samsung_files,sizeof(a->samsung_files),"AP  +  CSC  +  BL  +  CP");
        copy(a->samsung_plan,sizeof(a->samsung_plan),"preview");a->samsung_write_bytes=4619413905ULL;a->samsung_known=true;a->screen=SAMSUNG_REVIEW;
    }
    else if(!strcmp(name,"android-catalog")||!strcmp(name,"android-catalog-all")||!strncmp(name,"android-image",13)){
        /* Google's GSIs of the catalog (2026-11-10). */
        static const struct{const char*id,*name,*android,*variant,*build,*patch,*file;uint64_t size,system;bool fits;}images[]={
            {"gsi-13-aosp","Android 13","13","aosp","TQ3A.230805.001.A3","2023-08-05","aosp_arm64-exp-TQ3A.230805.001.A3-10454027-66e107ed.zip",832870349u,1899503616u,true},
            {"gsi-13-google","Android 13","13","gms","TQ3A.230805.001.A3","2023-08-05","gsi_gms_arm64-exp-TQ3A.230805.001.A3-10454027-3adcae59.zip",1474601277u,3241099264u,true},
            {"gsi-14-aosp","Android 14","14","aosp","UQ1A.231205.015","2023-12-05","aosp_arm64-exp-UQ1A.231205.015-11084887-e291b838.zip",935880656u,2059816960u,true},
            {"gsi-14-google","Android 14","14","gms","UQ1A.231205.015","2023-12-05","gsi_gms_arm64-exp-UQ1A.231205.015-11084887-2026a0e7.zip",1425799880u,3066781696u,true},
            {"gsi-15-aosp","Android 15","15","aosp","BP1A.250405.005.C1","2025-04-05","aosp_arm64-exp-BP1A.250405.005.C1-13151952-61d23231.zip",888211669u,1899589632u,true},
            {"gsi-15-google","Android 15","15","gms","BP1A.250405.005.C1","2025-04-05","gsi_gms_arm64-exp-BP1A.250405.005.C1-13151952-48d6db2a.zip",1586772058u,3276304384u,true},
            {"gsi-16-aosp","Android 16","16","aosp","BP4A.251205.006","2025-12-05","aosp_arm64-exp-BP4A.251205.006-14401865-2171cf0e.zip",905400114u,1889701888u,false},
            {"gsi-16-google","Android 16","16","gms","BP4A.251205.006","2025-12-05","gsi_gms_arm64-exp-BP4A.251205.006-14401865-f8760221.zip",1623557069u,3301928960u,false}};
        a->systems_known=a->systems_platform=a->systems_mounted=true;a->systems_read_only=false;a->catalog_count=0;
        for(size_t n=0;n<sizeof(images)/sizeof(images[0]);n++){CatalogImage*c=&a->catalog[a->catalog_count++];memset(c,0,sizeof(*c));
            copy(c->id,sizeof(c->id),images[n].id);copy(c->name,sizeof(c->name),images[n].name);copy(c->android,sizeof(c->android),images[n].android);
            copy(c->variant,sizeof(c->variant),images[n].variant);copy(c->build,sizeof(c->build),images[n].build);copy(c->patch,sizeof(c->patch),images[n].patch);
            copy(c->file,sizeof(c->file),images[n].file);copy(c->category,sizeof(c->category),"gsi");copy(c->region,sizeof(c->region),"Global");copy(c->family,sizeof(c->family),"android");
            c->size=images[n].size;c->system_bytes=images[n].system;c->fits=images[n].fits;c->reachable=true;}
        CatalogImage*l=&a->catalog[a->catalog_count++];memset(l,0,sizeof(*l));copy(l->id,sizeof(l->id),"debian-13");copy(l->name,sizeof(l->name),"Debian");
        copy(l->version,sizeof(l->version),"13 trixie");copy(l->family,sizeof(l->family),"debian");copy(l->file,sizeof(l->file),"debian.qcow2");copy(l->category,sizeof(l->category),"linux");l->reachable=true;l->size=337641472u;
        a->catalog_known=a->catalog_network=true;a->catalog_category=CATEGORY_ANDROID;a->catalog_all_phones=!strcmp(name,"android-catalog-all");a->screen=SYSTEMS_CATALOG;
        if(!strncmp(name,"android-image",13)){a->android_selected=!strcmp(name,"android-image-16")?6:0;a->saved_known=true;a->saved_count=0;a->screen=ANDROID_IMAGE;}
        if(!strcmp(name,"android-image-card")||!strcmp(name,"android-image-card-saved")||!strcmp(name,"android-image-nocard")){
            /* 2026-11-11: Android holds userdata, so the store is on a card, or there is no card for it. */
            a->systems_mounted=a->systems_platform=false;
            if(strcmp(name,"android-image-nocard")){copy(a->store_volume,sizeof(a->store_volume),"sdcard-p1");memset(a->store_token,'d',64);a->store_token[64]=0;copy(a->store_label,sizeof(a->store_label),"SD64G");}
            else copy(a->store_reason,sizeof(a->store_reason),"Insert an SD card or attach a USB drive: while Android uses userdata, downloaded images are kept there");
            if(!strcmp(name,"android-image-card-saved")){StoredImage*s=&a->saved[0];memset(s,0,sizeof(*s));copy(s->file,sizeof(s->file),a->catalog[0].file);
                snprintf(s->path,sizeof(s->path),"/.a22x-images/%s",s->file);s->size=a->catalog[0].size;s->checked=true;a->saved_count=1;}
        }
    }
    else if(!strcmp(name,"flash-super")||!strcmp(name,"flash-list")||!strcmp(name,"flash-gsi")){
        /* 2026-11-10: the partitions inside super, as flash.py lists them on this phone. */
        static const struct{const char*label,*title;uint64_t bytes,room;bool logical;}rows[]={
            {"boot","Kernel and ramdisk",32ull<<20,0,false},{"dtbo","Device tree overlays",8ull<<20,0,false},{"vbmeta","Verified boot data",64ull<<10,0,false},
            {"super","Android system partitions",6904ull<<20,0,false},{"recovery","This recovery, from a new image",40ull<<20,0,false},
            {"super/system","Logical partition in super",4361ull<<20,159ull<<20,true},{"super/odm","Logical partition in super",4ull<<20,159ull<<20,true},
            {"super/product","Logical partition in super",1333ull<<20,159ull<<20,true},{"super/vendor","Logical partition in super",1043ull<<20,159ull<<20,true}};
        a->flash_target_count=0;for(size_t i=0;i<sizeof(rows)/sizeof(rows[0]);i++){FlashTarget*f=&a->flash_targets[a->flash_target_count++];memset(f,0,sizeof(*f));
            copy(f->label,sizeof(f->label),rows[i].label);copy(f->title,sizeof(f->title),rows[i].title);f->bytes=rows[i].bytes;f->room=rows[i].room;f->logical=rows[i].logical;}
        a->flash_known=true;a->flash_super_free=162ull<<20;
        if(!strcmp(name,"flash-gsi")){
            a->flash_selected=5;a->flash_logical=true;a->flash_size_now=4361ull<<20;a->flash_size_after=4500ull<<20;a->flash_payload=4500ull<<20;a->flash_review_valid=true;a->flash_offline=true;
            copy(a->flash_name,sizeof(a->flash_name),"system-squeak-arm64-ab-vndklite-gapps.img");copy(a->flash_kind,sizeof(a->flash_kind),"ext4 file system image");a->flash_bytes=2100ull<<20;
            copy(a->flash_sha,sizeof(a->flash_sha),"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");copy(a->flash_warnings,sizeof(a->flash_warnings),"This partition is too large to save first");
            a->screen=FLASH_REVIEW;
        }else a->screen=!strcmp(name,"flash-super")?FLASH_SUPER:FLASH;
    }
    else if(!strcmp(name,"icon-sheet"))a->screen=ICON_SHEET;
    else if(!strcmp(name,"android-backup")||!strcmp(name,"android-backup-data")||!strcmp(name,"android-backup-data-on")){
        /* 2026-11-10: the Android partitions of the same page, with dtbo left out; -data: Android's own data offered too (2026-11-11). */
        a->screen=IDENTITY;a->id_kind=1;a->id_known=true;static const char*const labels[]={"boot","dtbo","vbmeta","vbmeta_system","vbmeta_vendor","super","prism","optics","metadata","userdata"};
        static const uint64_t sizes[]={64u<<20,8u<<20,8u<<20,8u<<20,8u<<20,8800ull<<20,1200ull<<20,400ull<<20,32u<<20,53397667328ULL};
        a->id_count=strcmp(name,"android-backup")?10:8;a->id_bytes=0;for(unsigned int n=0;n<a->id_count;n++){copy(a->id_labels[n],sizeof(a->id_labels[0]),labels[n]);a->id_sizes[n]=sizes[n];a->id_bytes+=sizes[n];}
        a->id_optional=a->id_count>8?3u<<8:0;a->id_off=1u<<1|(strcmp(name,"android-backup-data-on")?a->id_optional:0);a->volumes[1].read_only=false;identity_select(a,1);
    }
    else if(!strcmp(name,"apps-page")||!strcmp(name,"package-search")){
        /* The apps page, and a search in the repositories of a Debian image from the catalog. */
        a->systems_apps=(1u<<0)|(1u<<2)|(1u<<3)|(1u<<10)|(1u<<12)|(1u<<22);a->screen=!strcmp(name,"apps-page")?SYSTEMS_APP_LIST:SYSTEMS_SEARCH;
        CatalogImage*c=&a->catalog[0];memset(c,0,sizeof(*c));a->catalog_count=1;copy(c->id,sizeof(c->id),"debian-13");copy(c->name,sizeof(c->name),"Debian");
        copy(c->version,sizeof(c->version),"13 trixie");copy(c->family,sizeof(c->family),"debian");copy(a->systems_remote_id,sizeof(a->systems_remote_id),"debian-13");
        static const struct{const char*n,*v,*s;int av;}found[]={{"keepassxc","2.7.10+dfsg1-1","Cross Platform Password Manager",1},{"keepassxc-full","2.7.10+dfsg1-1","Cross Platform Password Manager (full build)",1},
            {"keepassxc-minimal","2.7.10+dfsg1-1","Cross Platform Password Manager (minimal build)",1},{"webext-keepassxc-browser","1.9.7+repack1-1","KeePassXC integration for Firefox and Chromium",1},
            {"keepass2","2.57+dfsg-1","Password manager",0}};
        for(size_t n=0;n<sizeof(found)/sizeof(found[0]);n++){PkgResult*r=&a->pkg_results[n];copy(r->name,sizeof(r->name),found[n].n);copy(r->version,sizeof(r->version),found[n].v);copy(r->summary,sizeof(r->summary),found[n].s);r->available=found[n].av;}
        a->pkg_count=5;a->pkg_known=true;copy(a->pkg_query,sizeof(a->pkg_query),"keepass");copy(a->systems_packages[0],sizeof(a->systems_packages[0]),"keepassxc");copy(a->systems_packages[1],sizeof(a->systems_packages[1]),"htop");a->systems_package_count=2;
    }
    else if(!strncmp(name,"systems",7)){
        a->screen=SYSTEMS;a->systems_known=a->systems_mounted=a->systems_initialized=a->systems_platform=true;copy(a->systems_active,sizeof(a->systems_active),"original");a->systems_slot_count=!strcmp(name,"systems-scroll")?16:3;
        for(int n=0;n<a->systems_slot_count;n++){SystemSlot*slot=&a->systems_slots[n];snprintf(slot->id,sizeof(slot->id),n?"linux-%02d":"original",n);snprintf(slot->name,sizeof(slot->name),n?"ARM64 Linux %02d — підготовлений пакет":"Current Linux / original",n);slot->active=n==0;slot->boot_tested=n==0;}
        if(!strcmp(name,"systems-scroll"))a->systems_scroll=491;
        else if(!strcmp(name,"systems-unmounted")||!strcmp(name,"systems-mount")){a->systems_mounted=false;a->systems_slot_count=0;a->systems_active[0]=0;copy(a->systems_volume_token,sizeof(a->systems_volume_token),"preview-userdata");a->systems_mount_sheet=!strcmp(name,"systems-mount");}
        else if(!strcmp(name,"systems-pending")||!strcmp(name,"systems-resume")){
            a->systems_reboot_blocked=a->systems_resume_allowed=a->systems_rollback_allowed=true;copy(a->systems_transaction,sizeof(a->systems_transaction),"switch-example");copy(a->systems_expected_transaction,sizeof(a->systems_expected_transaction),"switch-example");copy(a->systems_source,sizeof(a->systems_source),"original");copy(a->systems_target,sizeof(a->systems_target),"linux-01");copy(a->systems_direction,sizeof(a->systems_direction),"resume");a->systems_step=7;a->systems_total=28;
            if(!strcmp(name,"systems-resume"))a->screen=SYSTEMS_RECOVER;
        }else if(!strcmp(name,"systems-import")||!strcmp(name,"systems-switch")||!strcmp(name,"systems-remove")){
            a->screen=!strcmp(name,"systems-remove")?SYSTEMS_REMOVE:SYSTEMS_PLAN;a->systems_plan_valid=true;a->systems_plan_expires=(uint64_t)time(NULL)+600;a->systems_plan_kind=!strcmp(name,"systems-import")?J_SYS_IMPORT_PLAN:J_SYS_SWITCH_PLAN;
            copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),"linux-01");copy(a->systems_plan_name,sizeof(a->systems_plan_name),"ARM64 Linux — prepared phone bundle");copy(a->systems_source_volume,sizeof(a->systems_source_volume),"sdcard-p1");copy(a->systems_manifest,sizeof(a->systems_manifest),"/Linux/підготовлена-система/bundle.json");a->systems_required_bytes=6ULL*1024*1024*1024;a->systems_available_bytes=31ULL*1024*1024*1024;
        }else if(!strcmp(name,"systems-manifest")){
            a->screen=FILE_INFO;a->systems_pick_manifest=true;a->volume_count=1;a->volume_index=0;FileVolume*v=&a->volumes[0];copy(v->id,sizeof(v->id),"sdcard-p1");copy(v->token,sizeof(v->token),"preview-volume-token");v->mounted=true;v->read_only=true;
            copy(a->fs_selected.name,sizeof(a->fs_selected.name),"bundle.json");copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/Linux/підготовлена-система/bundle.json");copy(a->fs_selected.type,sizeof(a->fs_selected.type),"file");copy(a->fs_selected.token,sizeof(a->fs_selected.token),"preview-entry-token");
        }else if(!strcmp(name,"systems-empty")){a->systems_initialized=false;a->systems_empty=true;a->systems_slot_count=0;a->systems_active[0]=0;}
        else if(!strcmp(name,"systems-setup-clean")){a->systems_empty=true;a->systems_new_account=true;copy(a->systems_username,sizeof(a->systems_username),"user");copy(a->systems_manifest,sizeof(a->systems_manifest),"/ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz");a->systems_desktop=2;a->screen=SYSTEMS_SETUP;}
        else if(!strcmp(name,"systems-extras")||!strcmp(name,"systems-clean")){
            a->systems_empty=true;a->systems_desktop=2;a->systems_apps=(1u<<0)|(1u<<1)|(1u<<7)|(1u<<8);copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),"Дім 5G");copy(a->systems_wifi_password,sizeof(a->systems_wifi_password),"preview-password");
            copy(a->systems_script_name,sizeof(a->systems_script_name),"налаштування.sh");copy(a->systems_script_path,sizeof(a->systems_script_path),"/scripts/налаштування.sh");a->screen=SYSTEMS_EXTRAS;
            if(!strcmp(name,"systems-clean")){
                a->screen=SYSTEMS_PLAN;a->systems_plan_valid=a->systems_plan_clean=true;a->systems_plan_expires=(uint64_t)time(NULL)+600;a->systems_plan_kind=J_SYS_IMPORT_PLAN;
                copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),"ubuntu-26-04-1-preinstalled-server-arm64-raspi");copy(a->systems_plan_name,sizeof(a->systems_plan_name),"ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz");
                copy(a->systems_source_volume,sizeof(a->systems_source_volume),"usb-sda1");copy(a->systems_manifest,sizeof(a->systems_manifest),"/ubuntu-26.04.1-preinstalled-server-arm64+raspi.img.xz");
                a->systems_required_bytes=11811160064ULL;a->systems_available_bytes=52076478464ULL;
            }
        }else if(!strcmp(name,"systems-done")||!strcmp(name,"systems-done-beside")){
            a->systems_done_clean=!strcmp(name,"systems-done");copy(a->systems_done_name,sizeof(a->systems_done_name),"Manjaro ARM Linux");
            snprintf(a->systems_done_desktop,sizeof(a->systems_done_desktop),T("Desktop: %s  /  apps: %u"),tr("Plasma Mobile"),4u);
            snprintf(a->systems_done_network,sizeof(a->systems_done_network),T("Wi-Fi: %s  /  script: %s"),"Hotspot","—");a->screen=SYSTEMS_DONE;
        }else if(!strcmp(name,"systems-refused")){
            problem(a,"POWER_REFUSED","The phone stayed in recovery: a backup, restore or installation is still running. Wait until it finishes, then try again.");
        }else if(!strcmp(name,"systems-wifi")){
            copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),"Дім 5G");copy(a->systems_wifi_password,sizeof(a->systems_wifi_password),"short");
            a->screen=FILE_PROMPT;a->fs_prompt_kind=0;systems_wifi_focus(a,J_SYS_WIFI_PASS);
        }else if(!strcmp(name,"systems-script")){
            a->screen=FILE_INFO;a->systems_pick_manifest=a->systems_pick_script=true;a->volume_count=1;a->volume_index=0;FileVolume*v=&a->volumes[0];copy(v->id,sizeof(v->id),"usb-sda1");copy(v->token,sizeof(v->token),"preview-volume-token");v->mounted=true;v->read_only=true;
            copy(a->fs_selected.name,sizeof(a->fs_selected.name),"налаштування.sh");copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/scripts/налаштування.sh");copy(a->fs_selected.type,sizeof(a->fs_selected.type),"file");copy(a->fs_selected.token,sizeof(a->fs_selected.token),"preview-entry-token");a->fs_selected.size=2310;
        }else if(strcmp(name,"systems"))return false;
    }
    else if(!strcmp(name,"adb-request")){a->screen=ADB_REQUEST;copy(a->adb_id,sizeof(a->adb_id),"a1b2c3d4");copy(a->adb_fingerprint,sizeof(a->adb_fingerprint),"SHA256:V2xpdmU0Ym9h3K8sJxQz1R0hX7kLmN9pQrStUvWxYz0");copy(a->adb_comment,sizeof(a->adb_comment),"user@laptop");copy(a->adb_peer,sizeof(a->adb_peer),"172.16.42.2:41234");}
    else if(!strcmp(name,"saved-images")||!strcmp(name,"saved-images-delete")){
        /* The images saved on the phone (2026-11-10), and the question before one is deleted. */
        static const struct{const char*file,*name,*version,*family;uint64_t size;bool checked;}saved[]={
            {"ubuntu-24.04-server-cloudimg-arm64-root.tar.xz","Ubuntu Server","24.04 LTS","ubuntu",216215656u,true},
            {"mobian-rockchip-phosh-13.0.img.xz","Mobian","13 Phosh","mobian",1026326552u,true},
            {"Fedora-Cloud-Base-Generic-45-1.4.aarch64.qcow2","","","",596705280u,false}};
        a->systems_known=a->systems_platform=a->systems_mounted=true;a->systems_read_only=false;a->saved_count=0;
        for(size_t n=0;n<sizeof(saved)/sizeof(saved[0]);n++){StoredImage*v=&a->saved[a->saved_count++];memset(v,0,sizeof(*v));
            copy(v->file,sizeof(v->file),saved[n].file);snprintf(v->path,sizeof(v->path),"/.a22x-images/%s",saved[n].file);copy(v->name,sizeof(v->name),saved[n].name);
            copy(v->version,sizeof(v->version),saved[n].version);copy(v->family,sizeof(v->family),saved[n].family);v->size=saved[n].size;v->checked=saved[n].checked;}
        a->saved_known=true;a->saved_free=21368029184u;a->saved_delete=!strcmp(name,"saved-images-delete")?2:0;a->screen=SYSTEMS_IMAGES;
    }
    else if(!strcmp(name,"new-system")||!strcmp(name,"add-system")||!strcmp(name,"catalog")||!strcmp(name,"catalog-online")||!strcmp(name,"catalog-linux")||!strcmp(name,"new-system-android")||!strncmp(name,"source-sheet",12)){
        /* The installer: a new system page and the catalog (the first without network). */
        static const struct{const char*id,*name,*version,*family,*file;uint64_t size;bool minimal,reachable,signed_;}images[]={
            {"debian-13","Debian","13 trixie","debian","debian-13-genericcloud-arm64-20261103-2651.qcow2",337641472u,true,true,false},
            {"ubuntu-2604","Ubuntu Server","26.04 LTS","ubuntu","ubuntu-26.04-server-cloudimg-arm64-root.tar.xz",248069160u,true,true,true},
            {"ubuntu-2404","Ubuntu Server","24.04 LTS","ubuntu","ubuntu-24.04-server-cloudimg-arm64-root.tar.xz",216215656u,true,true,true},
            {"fedora-45","Fedora Cloud Base","45","fedora","Fedora-Cloud-Base-Generic-45-1.4.aarch64.qcow2",596705280u,true,true,true},
            {"archlinuxarm","Arch Linux ARM","aarch64","arch","ArchLinuxARM-aarch64-latest.tar.gz",829367415u,true,true,true},
            {"manjaro-minimal","Manjaro ARM Minimal","20261012","manjaro","Manjaro-ARM-minimal-generic-efi-20261012.img.xz",588345368u,true,true,false},
            {"mobian-phosh","Mobian","13 Phosh","mobian","mobian-rockchip-phosh-13.0.img.xz",1026326552u,false,true,false}};
        a->systems_known=a->systems_platform=a->systems_mounted=true;a->systems_read_only=false;a->systems_empty=false;a->catalog_count=0;
        for(size_t n=0;n<sizeof(images)/sizeof(images[0]);n++){CatalogImage*c=&a->catalog[a->catalog_count++];memset(c,0,sizeof(*c));
            copy(c->id,sizeof(c->id),images[n].id);copy(c->name,sizeof(c->name),images[n].name);copy(c->version,sizeof(c->version),images[n].version);
            copy(c->family,sizeof(c->family),images[n].family);copy(c->file,sizeof(c->file),images[n].file);c->size=images[n].size;c->minimal=images[n].minimal;c->reachable=images[n].reachable;c->signed_=images[n].signed_;}
        a->catalog_known=true;a->catalog_network=strcmp(name,"catalog")&&strcmp(name,"catalog-linux");a->screen=!strcmp(name,"new-system")||!strcmp(name,"add-system")||!strcmp(name,"new-system-android")||!strncmp(name,"source-sheet",12)?SYSTEMS_NEW:SYSTEMS_CATALOG;a->systems_adding=!strcmp(name,"add-system");
        if(!strcmp(name,"catalog-linux")){a->catalog_only=1;a->catalog_network=true;}
        if(!strcmp(name,"new-system-android")||!strncmp(name,"source-sheet",12)){
            /* 2026-11-11: Android on userdata. Nothing is mounted; the file is chosen at once. */
            a->systems_mounted=false;a->systems_read_only=true;a->systems_foreign=true;a->systems_boot_android=!strcmp(name,"new-system-android");copy(a->systems_volume_token,sizeof(a->systems_volume_token),"preview-volume-token");
            if(!strncmp(name,"source-sheet",12)){
                a->source_sheet=a->source_known=true;a->systems_pick_manifest=true;a->systems_wipe=true;a->volume_count=0;
                if(!strcmp(name,"source-sheet")){
                    a->volume_count=3;const char*ids[]={"usb-p1","sdcard-p1","cache"},*labels[]={"SANDISK","alarm_root","cache"},*types[]={"exfat","ext4","ext4"};
                    for(int n=0;n<3;n++){FileVolume*v=&a->volumes[n];memset(v,0,sizeof(*v));copy(v->id,sizeof(v->id),ids[n]);copy(v->label,sizeof(v->label),labels[n]);copy(v->fs_type,sizeof(v->fs_type),types[n]);copy(v->token,sizeof(v->token),"preview-volume-token");
                        v->mounted=n!=1;v->mountable=true;v->read_only=true;v->write_supported=true;v->bytes=n==0?62109253632ULL:(n==1?15707668480ULL:209715200ULL);}
                }
            }
        }
        for(unsigned int n=0;n<a->catalog_count;n++)if(!strcmp(a->catalog[n].family,"mobian"))copy(a->catalog[n].notes,sizeof(a->catalog[n].notes),"Phone shell");
        if(!strcmp(name,"catalog-online")){a->catalog_check_done=true;a->catalog_servers=7;a->catalog_updated=1;}
    }
    else if(!strcmp(name,"provision")){
        /* The progress page while the new system gets its packages in recovery. */
        a->screen=PROGRESS;a->job.pid=1;a->job.kind=J_SYS_IMPORT;a->job_started=1;a->now=1+754000;
        copy(a->progress_phase,sizeof(a->progress_phase),"image-provision");a->progress_done=1;a->progress_total=5;
    }
    else if(!strcmp(name,"flash-list")){
        /* The partitions as the phone lists them, recovery included. */
        static const struct{const char*label,*title;uint64_t bytes;}rows[]={{"boot","Kernel and ramdisk",33554432u},{"dtbo","Device tree overlays",8388608u},
            {"vbmeta","Verified boot data",65536u},{"vbmeta_system","Verified boot data for system",65536u},{"vbmeta_vendor","Verified boot data for vendor",65536u},
            {"super","Android system partitions",7193231360ull},{"prism","Samsung region data",738197504u},{"optics","Samsung carrier data",25165824u},
            {"recovery","This recovery, from a new image",41943040u}};
        a->flash_known=true;a->flash_target_count=0;
        for(size_t n=0;n<sizeof(rows)/sizeof(rows[0]);n++){FlashTarget*t=&a->flash_targets[a->flash_target_count++];memset(t,0,sizeof(*t));
            copy(t->label,sizeof(t->label),rows[n].label);copy(t->title,sizeof(t->title),rows[n].title);t->bytes=rows[n].bytes;}
        a->screen=FLASH;
    }
    else if(!strcmp(name,"home-wifi")){
        /* The home page with recovery joined to a network: the status bar and the bottom row. */
        a->screen=HOME;a->wifi_known=a->wifi_available=a->wifi_on=a->wifi_connected=true;a->wifi_count=1;
        copy(a->wifi_ssid,sizeof(a->wifi_ssid),"Hotspot");memset(&a->wifi_nets[0],0,sizeof(WifiNet));
        copy(a->wifi_nets[0].ssid,sizeof(a->wifi_nets[0].ssid),"Hotspot");a->wifi_nets[0].bars=3;a->wifi_nets[0].connected=true;
    }
    else if(!strncmp(name,"wifi",4)){
        /* Recovery Wi-Fi: the list, a joined network, the password page and an empty search. */
        static const struct{const char*ssid,*security,*band;unsigned int bars;bool secure,supported;}nets[]={
            {"Дім 5G","WPA2","5 GHz",4,true,true},{"Hotspot","WPA2","2.4 GHz",3,true,true},{"Кав'ярня біля парку","Open","2.4 GHz",2,false,true},
            {"Office-WPA3","WPA3","5 GHz",2,true,false},{"Сусіди","WPA2","2.4 GHz",1,true,true}};
        a->wifi_known=a->wifi_available=a->wifi_on=true;a->wifi_count=0;
        for(size_t n=0;n<sizeof(nets)/sizeof(nets[0]);n++){
            WifiNet*w=&a->wifi_nets[a->wifi_count++];memset(w,0,sizeof(*w));copy(w->ssid,sizeof(w->ssid),nets[n].ssid);
            for(size_t k=0;nets[n].ssid[k]&&k<32;k++)snprintf(w->hex+2*k,3,"%02x",(unsigned char)nets[n].ssid[k]);
            copy(w->security,sizeof(w->security),nets[n].security);copy(w->band,sizeof(w->band),nets[n].band);
            w->bars=nets[n].bars;w->secure=nets[n].secure;w->supported=nets[n].supported;
        }
        if(!strcmp(name,"wifi-connected")){
            WifiNet joined=a->wifi_nets[1];memmove(&a->wifi_nets[1],&a->wifi_nets[0],sizeof(WifiNet));a->wifi_nets[0]=joined;a->wifi_nets[0].connected=true;
            a->wifi_connected=true;copy(a->wifi_ssid,sizeof(a->wifi_ssid),"Hotspot");copy(a->wifi_address,sizeof(a->wifi_address),"192.168.1.23");
        }else if(!strcmp(name,"wifi-error"))copy(a->wifi_error,sizeof(a->wifi_error),"The password is wrong.");
        else if(!strcmp(name,"wifi-empty")){a->wifi_count=0;a->wifi_known=a->wifi_on=false;}
        else if(strcmp(name,"wifi")&&strcmp(name,"wifi-password")&&strcmp(name,"wifi-keypress"))return false;
        if(!strcmp(name,"wifi-password")||!strcmp(name,"wifi-keypress")){
            copy(a->wifi_edit_ssid,sizeof(a->wifi_edit_ssid),a->wifi_nets[0].ssid);copy(a->wifi_edit_hex,sizeof(a->wifi_edit_hex),a->wifi_nets[0].hex);a->wifi_edit_fixed=true;
            copy(a->wifi_edit_password,sizeof(a->wifi_edit_password),"secret12");a->screen=FILE_PROMPT;a->fs_prompt_kind=0;wifi_focus(a,J_WIFI_PASS);
            /* The bubble of a pressed key: "k" in the middle row of the English layout. */
            if(!strcmp(name,"wifi-keypress")){a->kb_uk=false;a->kb_bubble_cp='k';a->kb_bubble_rect=(Rect){28+7*(int)((664-8*6)/9+6),1090,(664-8*6)/9,70};a->kb_bubble_until=UINT64_MAX;}
        }else a->screen=WIFI;
    }
    else if(!strcmp(name,"display")||!strcmp(name,"display-unavailable")){
        a->screen=DISPLAY_SETTINGS;a->display_known=true;
        if(!strcmp(name,"display")){a->display_supported=a->display_service=a->display_60=a->display_90=true;a->display_hz=90;a->display_actual_hz=89.9999;copy(a->display_note,sizeof(a->display_note),"90 Hz display mode is active.");a->display_light_known=a->display_brightness_supported=a->display_blank_supported=a->display_blank_known=true;a->display_percent=a->display_last_percent=64;a->display_raw=a->display_requested_raw=163;a->display_max=255;}
        else copy(a->display_reason,sizeof(a->display_reason),"The display helper did not report an available mode. Refresh status after the display service starts.");
    }
    else if(!strcmp(name,"files")||!strcmp(name,"file-volume")||!strcmp(name,"volume-scroll")||!strcmp(name,"file-browser")||!strcmp(name,"file-scroll")||!strcmp(name,"file-editor")||!strcmp(name,"editor-scroll")||!strcmp(name,"text-scroll")||!strcmp(name,"file-confirm")||!strcmp(name,"file-locked")||!strcmp(name,"file-info")||!strcmp(name,"file-symlink")||!strcmp(name,"file-permissions")||!strcmp(name,"tools-export")){
        a->volume_count=5;a->volume_index=0;copy(a->volume_id,sizeof(a->volume_id),"userdata");
        const char*ids[]={"userdata","sdcard-p1","cache","android-data","system"};
        const char*labels[]={"Linux / userdata","microSD","Cache","Android data","System"};
        const char*types[]={"ext4","ext4","ext4","crypto / unsupported","erofs"};
        for(int n=0;n<5;n++){FileVolume*v=&a->volumes[n];copy(v->id,sizeof(v->id),ids[n]);copy(v->label,sizeof(v->label),labels[n]);copy(v->fs_type,sizeof(v->fs_type),types[n]);copy(v->token,sizeof(v->token),"preview-volume-token");v->mounted=n<2;v->mountable=n!=3;v->read_only=n!=0;v->write_supported=n<3;v->bytes=(n==0?50:8)*1024ULL*1024*1024;}
        copy(a->volumes[3].reason,sizeof(a->volumes[3].reason),"Filesystem unavailable; Android data may require decryption.");
        copy(a->volumes[4].read_only_reason,sizeof(a->volumes[4].read_only_reason),"This filesystem is read-only.");
        if(!strcmp(name,"tools-export")){a->screen=TOOLS_EXPORT;a->volumes[1].read_only=false;tools_select_volume(a,1);}
        else if(!strcmp(name,"file-volume")){a->screen=FILE_VOLUME;a->volume_index=2;copy(a->volume_id,sizeof(a->volume_id),"cache");copy(a->fs_status,sizeof(a->fs_status),"Check: errors repaired");}
        else if(!strcmp(name,"file-info")||!strcmp(name,"file-symlink")||!strcmp(name,"file-permissions")){
            a->screen=FILE_INFO;copy(a->fs_selected.name,sizeof(a->fs_selected.name),"налаштування.conf");copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/home/user/налаштування.conf");
            copy(a->fs_selected.type,sizeof(a->fs_selected.type),"file");copy(a->fs_selected.mode,sizeof(a->fs_selected.mode),"0644");copy(a->fs_selected.token,sizeof(a->fs_selected.token),"preview-entry-token");
            a->fs_selected.uid=a->fs_selected.gid=1000;a->fs_selected.nlink=1;a->fs_selected.size=1536;a->fs_selected.metadata_supported=true;
            if(!strcmp(name,"file-symlink")){copy(a->fs_selected.name,sizeof(a->fs_selected.name),"current-config");copy(a->fs_selected.type,sizeof(a->fs_selected.type),"symlink");copy(a->fs_selected.link_target,sizeof(a->fs_selected.link_target),"../user/налаштування.conf");a->fs_resolved=a->fs_selected;copy(a->fs_resolved.type,sizeof(a->fs_resolved.type),"file");a->fs_link_resolved=true;}
            if(!strcmp(name,"file-permissions"))begin_file_prompt(a,J_FS_CHMOD);
        }
        else if(!strcmp(name,"files")||!strcmp(name,"volume-scroll")){
            a->screen=FILE_VOLUMES;
            if(!strcmp(name,"volume-scroll")){
                a->volume_count=24;a->volume_scroll=79;copy(a->volumes[0].label,sizeof(a->volumes[0].label),"userdata — коренева система Linux");
                for(int n=5;n<a->volume_count;n++){FileVolume*v=&a->volumes[n];snprintf(v->id,sizeof(v->id),"partition-%02d",n);copy(v->fs_type,sizeof(v->fs_type),"raw");v->bytes=32u*1024u*1024u;}
                copy(a->volumes[5].id,sizeof(a->volumes[5].id),"boot");copy(a->volumes[6].id,sizeof(a->volumes[6].id),"alarm_root");copy(a->volumes[6].fs_type,sizeof(a->volumes[6].fs_type),"ext4");a->volumes[6].mountable=a->volumes[6].write_supported=true;
            }
        }
        else if(!strcmp(name,"file-locked")){a->screen=FILE_VOLUME;a->volume_index=3;copy(a->volume_id,sizeof(a->volume_id),"android-data");}
        else if(!strcmp(name,"file-browser")||!strcmp(name,"file-scroll")){
            a->screen=FILE_BROWSER;copy(a->fs_path,sizeof(a->fs_path),"/home/user");a->file_count=7;a->file_total=11;a->files_more=true;
            const char*names[]={"Документи","Зображення","config.ini","запустити-linux.sh","system-link","службовий-сокет","Дуже довга українська назва — резервна копія налаштувань системи 2026.txt"};
            const char*types[]={"directory","directory","file","file","symlink","other","file"};
            for(int n=0;n<7;n++){FileEntry*e=&a->files[n];copy(e->name,sizeof(e->name),names[n]);child_path(a->fs_path,names[n],e->path,sizeof(e->path));copy(e->type,sizeof(e->type),types[n]);copy(e->mode,sizeof(e->mode),n==3?"0755":"0644");e->metadata_supported=true;e->size=n<2?0:1536;}
            if(!strcmp(name,"file-scroll")){a->file_count=FILE_BATCH;a->file_total=180;a->browser_scroll=57;for(int n=7;n<FILE_BATCH;n++){FileEntry*e=&a->files[n];snprintf(e->name,sizeof(e->name),"Налаштування-%03d.conf",n);child_path(a->fs_path,e->name,e->path,sizeof(e->path));copy(e->type,sizeof(e->type),"file");copy(e->mode,sizeof(e->mode),"0644");e->size=2048;}}
        }else if(!strcmp(name,"file-editor")||!strcmp(name,"editor-scroll")||!strcmp(name,"text-scroll")){
            a->screen=FILE_EDIT;copy(a->fs_file,sizeof(a->fs_file),"/home/user/налаштування.conf");copy(a->fs_text,sizeof(a->fs_text),"# Налаштування Linux\n# Зміни зберігаються після підтвердження.\n\n[display]\nbrightness=80\n\n[notes]\nmessage=Привіт, Україно!\n");a->fs_length=a->fs_cursor=strlen(a->fs_text);a->fs_dirty=true;a->kb_uk=true;
            if(strcmp(name,"file-editor")){sample_document(a,240);a->fs_scroll=!strcmp(name,"editor-scroll")?519:1079;a->screen=!strcmp(name,"editor-scroll")?FILE_EDIT:FILE_TEXT;const char*at=strstr(a->fs_text,"line-021");a->fs_cursor=at?(size_t)(at-a->fs_text)+10:0;}
        }else{
            FileRequest q={.kind=J_FS_COPY};copy(q.volume,sizeof(q.volume),"userdata");copy(q.path,sizeof(q.path),"/home/user/налаштування.conf");copy(q.destination_volume,sizeof(q.destination_volume),"sdcard-p1");copy(q.destination,sizeof(q.destination),"/Документи/налаштування.conf");confirm_file_request(a,q,FILE_PROMPT);
        }
    }else return false;
    return true;
}
static Button*test_button(App*a,int id){for(int n=0;n<a->button_count;n++)if(a->buttons[n].action==id)return&a->buttons[n];return NULL;}
static bool test_press(App*a,int id,unsigned int duration){
    render(a);Button*b=test_button(a,id);if(!b||!b->enabled)return false;
    int x=b->r.x+b->r.w/2,y=b->r.y+b->r.h/2;uint64_t now=milliseconds();pointer(a,true,x,y,now);pointer(a,false,x,y,now+duration);return true;
}
static int test_linux_plan_guards(void){
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->preview=true;a->device=true;a->inventory_ok=true;a->linux_restore_available=a->linux_wipe_available=true;
    copy(a->plan_path,sizeof(a->plan_path),"/private/review.json");a->partition_count=2;
    copy(a->partitions[0].label,sizeof(a->partitions[0].label),"userdata");copy(a->partitions[0].path,sizeof(a->partitions[0].path),"/dev/mmcblk0p54");a->partitions[0].bytes=53393489920ULL;a->partitions[0].available=true;
    copy(a->partitions[1].label,sizeof(a->partitions[1].label),"boot");copy(a->partitions[1].path,sizeof(a->partitions[1].path),"/dev/mmcblk0p41");a->partitions[1].bytes=33554432;a->partitions[1].available=true;
    copy(a->backups[0].schema,sizeof(a->backups[0].schema),"a22x.backup.v2");copy(a->backups[0].workflow,sizeof(a->backups[0].workflow),"linux-partclone-v2");copy(a->backups[0].profile,sizeof(a->backups[0].profile),"linux");
    const char*base="{\"ok\":true,\"schema\":\"a22x.plan.v2\",\"operation\":\"restore-plan\",\"plan_path\":\"/private/review.json\",\"plan_id\":\"abc\",\"confirmation_token\":\"RESTORE-abc\",\"can_execute\":true,\"blockers\":[],\"summary\":{\"profile\":\"linux\",\"workflow\":\"linux-partclone-v2\",\"backup_schema\":\"a22x.backup.v2\",\"data_format\":\"partclone-ext4-v2+zstd\",\"destructive\":true,\"required_hold_ms\":3000,\"qualification\":{\"status\":\"qualified\",\"eligible\":true,\"capsule_id\":\"1111111111111111111111111111111111111111111111111111111111111111\",\"blockers\":[]},\"boot_action\":\"skip-identical\",\"write_targets\":[{\"label\":\"userdata\",\"path\":\"/dev/mmcblk0p54\",\"size_bytes\":53393489920,\"action\":\"restore-used-blocks\"}]}}";
    JsonToken tokens[512];Json j;int tests=0;assert(json_parse(&j,base,tokens,512)>0);parse_plan(a,&j,J_RESTORE_PLAN);assert(a->plan_can_execute&&a->plan_linux_admitted&&a->plan_restore&&a->plan_kind==J_RESTORE_PLAN);render(a);assert(test_button(a,A_CONFIRM)->hold_ms==3000&&test_press(a,A_CONFIRM,2999)&&a->screen==PLAN&&!a->job.pid);tests++;
    const char*changes[][2]={
        {"a22x.plan.v2","a22x.plan.v1"},{"restore-plan","wipe-plan"},{"/private/review.json","/private/other.json"},
        {"linux-partclone-v2","raw-v1"},{"a22x.backup.v2","a22x.backup.v1"},{"partclone-ext4-v2+zstd","raw+gzip"},
        {"\"destructive\":true","\"destructive\":false"},{"\"required_hold_ms\":3000","\"required_hold_ms\":2000"},
        {"\"status\":\"qualified\"","\"status\":\"fixture-only\""},{"\"eligible\":true","\"eligible\":false"},
        {"1111111111111111111111111111111111111111111111111111111111111111",""},{"skip-identical","restore-last"},
        {"\"label\":\"userdata\"","\"label\":\"metadata\""},{"/dev/mmcblk0p54","/dev/mmcblk0"},
        {"53393489920","53393489921"},{"restore-used-blocks","format-ext4"},{"\"blockers\":[]","\"blockers\":[\"INSUFFICIENT_SPACE\"]"}
    };
    for(unsigned int n=0;n<sizeof(changes)/sizeof(changes[0]);n++){
        char changed[4096];const char*start=strstr(base,changes[n][0]);assert(start);size_t prefix=(size_t)(start-base);assert(prefix<sizeof(changed));memcpy(changed,base,prefix);snprintf(changed+prefix,sizeof(changed)-prefix,"%s%s",changes[n][1],start+strlen(changes[n][0]));
        assert(json_parse(&j,changed,tokens,512)>0);parse_plan(a,&j,J_RESTORE_PLAN);assert(!a->plan_can_execute);render(a);assert(!test_button(a,A_CONFIRM)->enabled);tests++;
    }
    assert(json_parse(&j,base,tokens,512)>0);a->linux_restore_available=false;parse_plan(a,&j,J_RESTORE_PLAN);assert(!a->plan_can_execute&&!a->plan_linux_admitted);tests++;
    a->fs_dirty=true;copy(a->fs_text,sizeof(a->fs_text),"unsaved text");a->fs_length=strlen(a->fs_text);assert(!begin_linux_prepare(a,J_WIPE_PLAN)&&a->result_return==FILE_EDIT&&!strcmp(a->fs_text,"unsaved text")&&!a->job.pid);tests++;
    a->fs_dirty=false;a->volume_count=1;copy(a->volumes[0].id,sizeof(a->volumes[0].id),"userdata");copy(a->volumes[0].token,sizeof(a->volumes[0].token),"token");a->volumes[0].mounted=true;copy(a->volumes[0].mount_owner,sizeof(a->volumes[0].mount_owner),"external");prepare_inventory(a);render(a);assert(a->prepare_known&&a->prepare_mounted&&!a->prepare_owned&&!test_button(a,A_PREPARE_CONTINUE)->enabled);copy(a->volumes[0].mount_owner,sizeof(a->volumes[0].mount_owner),"filesystem");prepare_inventory(a);render(a);assert(test_button(a,A_PREPARE_CONTINUE)->enabled&&test_button(a,A_PREPARE_CONTINUE)->hold_ms==1000);tests++;
    a->device=false;assert(json_parse(&j,"{\"ok\":true,\"mode\":\"fixture\",\"available\":true,\"state\":\"stopped\",\"session_token\":null,\"exports\":[],\"candidates\":[{\"id\":\"userdata\",\"actual_access\":\"ro\",\"volume_token\":\"v\",\"mount_token\":\"m\"}]}",tokens,512)>0);assert(parse_usb_status(a,&j)&&a->usb_candidate_count==1&&a->usb_read_only);screen(a,USB_FILES);render(a);assert(test_button(a,A_USB_START)->enabled&&!test_button(a,A_USB_RW)->enabled&&!usb_session_open(a));tests++;
    assert(json_parse(&j,"{\"ok\":true,\"mode\":\"fixture\",\"available\":true,\"state\":\"active\",\"session_token\":\"session\",\"candidates\":[],\"exports\":[{\"label\":\"userdata\",\"volume_token\":\"v\",\"mount_token\":\"m\",\"access\":\"rw\"}]}",tokens,512)>0);assert(parse_usb_status(a,&j)&&a->usb_export_valid&&a->usb_export_writable&&usb_session_open(a));render(a);assert(test_button(a,A_USB_STOP)->enabled&&!test_button(a,A_USB_START));assert(!begin_linux_prepare(a,J_BACKUP_PLAN)&&!strcmp(a->status,"USB_EXPORT_ACTIVE")&&!a->job.pid);tests++;
    a->fs_dirty=true;screen(a,FILE_EDIT);action(a,A_USB_FILES);assert(!a->job.pid&&a->result_return==FILE_EDIT&&!strcmp(a->status,"UNSAVED_EDITS")&&!strcmp(a->fs_text,"unsaved text"));tests++;
    free(a->pixels);free(a);return tests;
}
/* 2026-11-11: the screenshot keys in either order. Volume down first used to step through the
   buttons and Power then blanked the screen; now the step is taken back and the picture taken. */
static int test_screenshot_keys(void){
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->preview=true;a->inventory_ok=true;a->now=10000;a->screen=HOME;render(a);
    assert(a->focus<0&&a->button_count>3&&!a->shot_flash);
    key_event(a,KEY_VOLUMEDOWN,1);assert(a->focus>=0&&a->voldown_held);              /* the key steps to the first button */
    a->now+=150;key_event(a,KEY_POWER,1);
    assert(a->power_held&&a->shot_flash&&a->focus<0&&a->toast[0]);                     /* taken, and the step taken back */
    a->now+=120;key_event(a,KEY_POWER,0);assert(!a->power_held&&!a->display_power_down&&!a->display_control.pid&&a->screen==HOME);
    key_event(a,KEY_VOLUMEDOWN,0);assert(!a->voldown_held);
    /* Power held first and Volume down then, as before. */
    a->shot_flash=0;a->toast[0]=0;a->now+=3000;key_event(a,KEY_POWER,1);assert(!a->shot_flash);
    a->now+=200;key_event(a,KEY_VOLUMEDOWN,1);assert(a->shot_flash&&a->power_held&&a->focus<0);
    a->now+=100;key_event(a,KEY_POWER,0);key_event(a,KEY_VOLUMEDOWN,0);
    /* Volume down held long before Power is no screenshot: the two keys keep their own work. */
    a->shot_flash=0;a->now+=3000;key_event(a,KEY_VOLUMEDOWN,1);a->now+=SHOT_CHORD_MS+1;key_event(a,KEY_POWER,1);
    assert(!a->shot_flash&&!a->power_held);key_event(a,KEY_POWER,0);key_event(a,KEY_VOLUMEDOWN,0);
    /* A dark screen is not photographed. */
    a->display_blank_known=a->display_blanked=true;a->shot_flash=0;a->now+=3000;key_event(a,KEY_VOLUMEDOWN,1);a->now+=100;key_event(a,KEY_POWER,1);
    assert(!a->shot_flash);
    /* The flash fades and ends by itself. */
    a->display_blanked=false;a->shot_flash=a->now;screenshot_poll(a);assert(a->shot_flash);a->now+=SHOT_FLASH_MS;screenshot_poll(a);assert(!a->shot_flash);
    free(a->pixels);free(a);return 1;
}
/* 2026-11-11: several ZIPs that one hold runs one after another. Replies of the helper are fed in by
   hand; the jobs the list starts are real children that are dropped at once. */
static bool zip_test_reply(App*a,const char*reply,int kind,bool ok){
    char text[1536];JsonToken t[96];Json j;copy(text,sizeof(text),reply);
    if(json_parse(&j,text,t,96)<1)return false;
    complete_zip_job(a,&j,kind,ok);return true;
}
static void zip_test_drop(App*a){
    if(!a->job.pid)return;
    (void)kill(a->job.pid,SIGKILL);(void)waitpid(a->job.pid,NULL,0);
    if(!a->job.out_eof)close(a->job.fdout);
    if(!a->job.err_eof)close(a->job.fderr);
    free(a->job.output);memset(&a->job,0,sizeof(a->job));
}
static void zip_test_choose(App*a,const char*path,bool adding,const char*name){
    char reply[640];copy(a->zip_volume,sizeof(a->zip_volume),"sdcard-p1");copy(a->zip_token,sizeof(a->zip_token),"token");copy(a->zip_path,sizeof(a->zip_path),path);a->zip_adding=adding;
    snprintf(reply,sizeof(reply),"{\"ok\":true,\"operation\":\"inspect\",\"name\":\"%s\",\"sha256\":\"%064d\",\"title\":\"\",\"what\":\"zip\",\"installer\":\"script\",\"size\":1000,\"files\":3,\"warnings\":[],\"partitions\":[\"boot\",\"super\"]}",name,7);
    assert(zip_test_reply(a,reply,J_ZIP_INSPECT,true));
}
static int test_zip_queue(void){
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->inventory_ok=true;a->now=10000;a->allow_power=true;a->fixture[0]='/';
    copy(a->python,sizeof(a->python),"/bin/true");copy(a->zip_helper,sizeof(a->zip_helper),"zip_install.py");
    /* A ZIP chosen afresh is a list of one; one that is added joins it; the same file again keeps its place. */
    zip_test_choose(a,"/a.zip",false,"a.zip");assert(a->zip_count==1&&a->zip_known&&a->screen==ZIP_REVIEW&&!a->zip_adding);
    zip_test_choose(a,"/b.zip",true,"b.zip");assert(a->zip_count==2&&!strcmp(a->zip_queue[0].name,"a.zip")&&!strcmp(a->zip_queue[1].name,"b.zip")&&!a->zip_adding);
    a->toast[0]=0;zip_test_choose(a,"/a.zip",true,"a.zip");assert(a->zip_count==2&&a->toast[0]);
    zip_test_choose(a,"/c.zip",true,"c.zip");assert(a->zip_count==3);
    render(a);assert(test_button(a,A_ZIP_ADD)&&test_button(a,A_ZIP_REMOVE+2)&&!test_button(a,A_ZIP_REMOVE+3)&&test_button(a,A_ZIP_RESTART)&&test_button(a,A_ZIP_CONFIRM)->hold_ms==5000);
    /* One taken out: the rest moves up, and the review shown is the last one's. */
    assert(test_press(a,A_ZIP_REMOVE,50)&&a->zip_count==2&&!strcmp(a->zip_queue[0].name,"b.zip")&&!strcmp(a->zip_path,"/c.zip"));
    zip_test_choose(a,"/a.zip",true,"a.zip");assert(a->zip_count==3&&!strcmp(a->zip_queue[2].name,"a.zip"));
    /* The hold runs the first; each one that finishes starts the next; the result names all three. */
    assert(test_press(a,A_ZIP_CONFIRM,5100)&&a->job.pid&&a->job.kind==J_ZIP_RUN&&a->zip_at==0&&!strcmp(a->zip_path,"/b.zip"));zip_test_drop(a);
    assert(zip_test_reply(a,"{\"ok\":true,\"operation\":\"run\",\"changed\":[\"boot\"],\"log\":[\"one\"]}",J_ZIP_RUN,true)&&a->zip_at==1&&a->job.pid&&!strcmp(a->zip_path,"/c.zip")&&a->screen!=RESULT);zip_test_drop(a);
    assert(zip_test_reply(a,"{\"ok\":true,\"operation\":\"run\",\"changed\":[\"super\",\"boot\"],\"log\":[\"two\"]}",J_ZIP_RUN,true)&&a->zip_at==2&&a->job.pid);zip_test_drop(a);
    assert(zip_test_reply(a,"{\"ok\":true,\"operation\":\"run\",\"changed\":[],\"log\":[\"three\"]}",J_ZIP_RUN,true)&&!a->job.pid&&a->screen==RESULT&&a->successful&&a->zip_ran&&!a->zip_restart_at);
    assert(!strcmp(a->status,"The installers finished")&&strstr(a->message,"boot super")&&strstr(a->message,"1. b.zip: finished")&&strstr(a->message,"3. a.zip: finished")&&strstr(a->message,"three")&&!strstr(a->message,"one"));
    render(a);assert(test_button(a,A_REBOOT)&&!test_button(a,A_ZIP_STAY));
    assert(test_press(a,A_DONE,50)&&!a->zip_ran);
    /* The second fails: the third is not run, and the result says which is which. */
    a->screen=ZIP_REVIEW;assert(test_press(a,A_ZIP_CONFIRM,5100)&&a->job.pid);zip_test_drop(a);
    assert(zip_test_reply(a,"{\"ok\":true,\"operation\":\"run\",\"changed\":[\"boot\"],\"log\":[\"one\"]}",J_ZIP_RUN,true)&&a->job.pid);zip_test_drop(a);
    assert(zip_test_reply(a,"{\"ok\":false,\"operation\":\"run\",\"error\":{\"code\":\"ZIP_INSTALLER_FAILED\",\"message\":\"x\",\"details\":{\"changed\":[],\"log\":[\"bad\"]}}}",J_ZIP_RUN,false));
    assert(!a->job.pid&&a->screen==RESULT&&!a->successful&&!strcmp(a->status,"ZIP_INSTALLER_FAILED")&&strstr(a->message,"1. b.zip: finished")&&strstr(a->message,"2. c.zip: failed")&&strstr(a->message,"3. a.zip: not run")&&strstr(a->message,"bad"));
    render(a);assert(!test_button(a,A_REBOOT)&&!test_button(a,A_ZIP_STAY));
    /* A ZIP chosen afresh is a list of one again, shown as one ZIP was before. */
    zip_test_choose(a,"/d.zip",false,"d.zip");assert(a->zip_count==1&&!strcmp(a->zip_queue[0].name,"d.zip"));
    render(a);assert(test_button(a,A_ZIP_ADD)&&!test_button(a,A_ZIP_REMOVE));
    /* The restart that was asked for counts down on the result page; Stay in recovery stops it. */
    assert(test_press(a,A_ZIP_RESTART,50)&&a->zip_restart);
    assert(test_press(a,A_ZIP_CONFIRM,5100)&&a->job.pid);zip_test_drop(a);a->now=50000;
    assert(zip_test_reply(a,"{\"ok\":true,\"operation\":\"run\",\"changed\":[\"boot\"],\"log\":[\"done\"]}",J_ZIP_RUN,true)&&a->screen==RESULT&&a->zip_restart_at==50000+ZIP_RESTART_MS&&!strcmp(a->status,"The installer finished"));
    render(a);assert(test_button(a,A_ZIP_STAY)&&!test_button(a,A_REBOOT));
    a->now=55000;hotplug_tick(a);assert(a->running&&a->zip_restart_at&&a->zip_restart_shown==5);
    /* Stay works at once, also while a read runs in the background (the tick may have started one). */
    action(a,A_ZIP_STAY);assert(!a->zip_restart_at&&a->running);zip_test_drop(a);render(a);assert(test_button(a,A_REBOOT));
    /* Left alone, it restarts the system; leaving the page first stops it; a read in the background is waited for. */
    a->zip_restart_at=a->now+ZIP_RESTART_MS;a->screen=HOME;hotplug_tick(a);zip_test_drop(a);assert(!a->zip_restart_at&&a->running);
    a->screen=RESULT;a->zip_restart_at=a->now+ZIP_RESTART_MS;a->now+=ZIP_RESTART_MS;a->job.pid=1;hotplug_tick(a);assert(a->running&&a->zip_restart_at);
    a->job.pid=0;hotplug_tick(a);assert(!a->running&&a->exit_code==200&&a->power_intent==200);zip_test_drop(a);
    /* No more than six. */
    a->running=true;a->exit_code=0;zip_test_choose(a,"/1.zip",false,"1.zip");
    for(int n=2;n<=8;n++){char path[16],name[16];snprintf(path,sizeof(path),"/%d.zip",n);snprintf(name,sizeof(name),"%d.zip",n);zip_test_choose(a,path,true,name);}
    assert(a->zip_count==ZIP_QUEUE);render(a);assert(!test_button(a,A_ZIP_ADD)->enabled);
    free(a->pixels);free(a);return 1;
}
/* 2026-11-11: a factory reset asked for in Android's settings arrives as a request in misc; init reads
   it and the page of Data for Android opens for it. A file stands in for what init left. */
static int test_reset_request(void){
    char path[]="/tmp/a22x-bcb-XXXXXX",helper[]="/tmp/a22x-bcb-helper-XXXXXX";int fd=mkstemp(path);assert(fd>=0);
    #define BCB_FILE(text) do{FILE*f=fopen(path,"w");assert(f);fputs(text,f);fclose(f);}while(0)
    BCB_FILE("{\"ok\":true,\"command\":\"boot-recovery\",\"recovery\":[\"recovery\",\"--wipe_data\",\"--reason=MasterClearConfirm\"],\"reason\":\"MasterClearConfirm\",\"wipe\":true,\"cleared\":false}\n");
    close(fd);setenv("A22X_RECOVERY_BCB",path,1);
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->inventory_ok=true;a->now=10000;a->fixture[0]='/';a->fixture[1]=0;
    /* Only from the home page, and not while a job runs or the phone is locked. */
    a->screen=TOOLS;reset_request_poll(a);assert(!a->reset_checked&&a->screen==TOOLS);
    a->screen=HOME;a->locked=true;reset_request_poll(a);assert(!a->reset_checked);a->locked=false;
    reset_request_poll(a);assert(a->reset_checked&&a->reset_asked&&!a->reset_prompt&&a->reset_page&&a->wipe_android&&a->screen==WIPE_REVIEW);
    render(a);assert(test_button(a,A_RESET_LATER)&&test_button(a,A_RESET_FORGET)&&test_button(a,A_WIPE_CONFIRM)&&test_button(a,A_WIPE_CONFIRM)->hold_ms==5000);
    /* Not now: home; the request stays, and the page does not open by itself a second time. */
    assert(test_press(a,A_RESET_LATER,50)&&a->screen==HOME&&a->reset_asked&&!a->reset_page&&!a->wipe_android);
    reset_request_poll(a);assert(a->screen==HOME);
    /* Wipe, Data for Android: the same page, as Android's request; Back goes home too. */
    a->screen=WIPE;render(a);assert(test_press(a,A_WIPE_ANDROID,50)&&a->screen==WIPE_REVIEW&&a->reset_page&&a->wipe_android);
    action(a,A_BACK);assert(a->screen==HOME&&a->reset_asked);
    /* Forget: a helper that fails leaves everything as it was, and says so. */
    a->screen=WIPE;render(a);assert(test_press(a,A_WIPE_ANDROID,50)&&a->screen==WIPE_REVIEW);
    copy(a->python,sizeof(a->python),"/nonexistent/python");a->toast[0]=0;
    render(a);assert(test_press(a,A_RESET_FORGET,50)&&a->reset_asked&&a->screen==WIPE_REVIEW&&a->toast[0]);
    /* A helper that clears it: the request is gone, the file init left with it, and the page is home. */
    fd=mkstemp(helper);assert(fd>=0);assert(write(fd,"exit 0\n",7)==7);close(fd);setenv("A22X_RECOVERY_BCB_HELPER",helper,1);
    copy(a->python,sizeof(a->python),"/bin/sh");a->toast[0]=0;
    render(a);assert(test_press(a,A_RESET_FORGET,50)&&!a->reset_asked&&!a->reset_page&&!a->wipe_android&&a->screen==HOME&&a->toast[0]&&access(path,F_OK)!=0);
    a->screen=WIPE;render(a);assert(test_press(a,A_WIPE_ANDROID,50)&&a->screen==WIPE_REVIEW&&!a->reset_page);  /* the plain page again */
    /* Android that could not open its data asks with a prompt. */
    BCB_FILE("{\"ok\":true,\"command\":\"boot-recovery\",\"recovery\":[\"recovery\",\"--prompt_and_wipe_data\",\"--reason=fs_mgr_mount_all\"],\"wipe\":true}\n");
    a->reset_checked=false;a->wipe_android=false;a->screen=HOME;reset_request_poll(a);assert(a->reset_asked&&a->reset_prompt&&a->screen==WIPE_REVIEW);render(a);
    /* Nothing asked, or nothing left by init: the home page stays. */
    BCB_FILE("{\"ok\":true,\"command\":\"\",\"recovery\":[],\"reason\":\"\",\"wipe\":false,\"cleared\":false}\n");
    a->reset_checked=a->reset_asked=a->reset_page=a->wipe_android=false;a->screen=HOME;reset_request_poll(a);assert(a->reset_checked&&!a->reset_asked&&a->screen==HOME);
    unlink(path);a->reset_checked=false;reset_request_poll(a);assert(a->reset_checked&&!a->reset_asked&&a->screen==HOME);
    unlink(helper);unsetenv("A22X_RECOVERY_BCB");unsetenv("A22X_RECOVERY_BCB_HELPER");
    #undef BCB_FILE
    free(a->pixels);free(a);return 1;
}
/* 2026-11-11: the charge shown is the interface's own estimate. A folder stands in for the kernel's
   battery and for the curve in the device tree. */
static int test_battery(void){
    char folder[]="/tmp/a22x-battery-XXXXXX",path[112];assert(mkdtemp(folder));
    #define BATTERY_FILE(name,format,value) do{snprintf(path,sizeof(path),"%s/%s",folder,name);FILE*f=fopen(path,"w");assert(f);fprintf(f,format,value);fclose(f);}while(0)
    /* The phone's own curve, as the device tree holds it: big-endian pairs of microvolts and percent. */
    static const unsigned int curve[][2]={{4357000,100},{4280721,95},{4221383,90},{4165774,85},{4110504,80},{4070075,75},{3996095,70},{3963975,65},{3931597,60},{3880589,55},{3842700,50},
        {3819808,45},{3802441,40},{3787965,35},{3776921,30},{3762025,25},{3743598,20},{3716495,15},{3691341,10},{3650438,5},{3400000,0}};
    snprintf(path,sizeof(path),"%s/ocv-capacity-table-0",folder);FILE*table=fopen(path,"wb");assert(table);
    for(size_t n=0;n<sizeof(curve)/sizeof(*curve);n++)for(int k=0;k<2;k++){unsigned char b[4]={(unsigned char)(curve[n][k]>>24),(unsigned char)(curve[n][k]>>16),(unsigned char)(curve[n][k]>>8),(unsigned char)curve[n][k]};fwrite(b,1,4,table);}
    fclose(table);
    snprintf(path,sizeof(path),"%s/factory-internal-resistance-micro-ohms",folder);table=fopen(path,"wb");assert(table);{unsigned char b[4]={0,1,0xd4,0xc0};fwrite(b,1,4,table);}fclose(table); /* 120000 */
    setenv("A22X_RECOVERY_BATTERY",folder,1);battery_curve.read=false;
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->now=100000;
    /* What the owner met: the kernel says 15 %, the cell stands at 3.79 V while 0.15 A flows in. */
    BATTERY_FILE("capacity","%d\n",15);BATTERY_FILE("status","%s\n","Charging");BATTERY_FILE("current_now","%d\n",150000);
    BATTERY_FILE("voltage_now","%d\n",3790000);BATTERY_FILE("charge_now","%d\n",750000);BATTERY_FILE("charge_full","%d\n",5000000);
    update_battery(a);
    assert(battery_curve.count==21&&battery_curve.resistance==120000);
    assert(a->battery_estimated&&a->battery_percent>=27&&a->battery_percent<=30&&a->battery_charging&&!strncmp(a->battery,"2",1));
    assert(battery_curve_level(4400000)==100&&battery_curve_level(3300000)==0&&battery_curve_level(3842700)==50&&fabs(battery_curve_level(3831254)-47.5)<0.01);
    /* Charge counted by the kernel moves the estimate: 50 mAh of 5000 is one point. */
    int before=a->battery_percent;double start=a->battery_estimate;
    a->now+=10000;BATTERY_FILE("charge_now","%d\n",800000);update_battery(a);
    /* While it settles the voltage decides; afterwards the counted charge does. */
    for(int n=0;n<8;n++){a->now+=10000;update_battery(a);}
    assert(a->battery_samples>BATTERY_SETTLE&&fabs(a->battery_estimate-start)<1.0&&a->battery_percent>=before-1&&a->battery_percent<=before+1);
    start=a->battery_estimate;a->now+=120000;BATTERY_FILE("charge_now","%d\n",850000);update_battery(a); /* 1.5 A for two minutes */
    assert(fabs(a->battery_estimate-start-1.0)<0.05);
    /* A new start taken by the kernel (20 points in ten seconds) is no charge. */
    start=a->battery_estimate;a->now+=10000;BATTERY_FILE("charge_now","%d\n",1850000);update_battery(a);
    assert(fabs(a->battery_estimate-start)<0.05);
    /* An estimate that drifted is drawn to the voltage, one point in thirty seconds, and stops within four points. */
    a->battery_estimate=60.0;double far=a->battery_estimate-a->battery_ocv;assert(far>25);
    a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-59.0)<0.05);
    a->now+=15000;update_battery(a);assert(fabs(a->battery_estimate-58.5)<0.05);
    a->battery_estimate=a->battery_ocv+3.5;a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-a->battery_ocv-3.5)<0.05);
    /* While the cell is charged its voltage stands high: that it holds more than the estimate is believed only from twelve points on. */
    a->battery_estimate=a->battery_ocv-8.0;a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-(a->battery_ocv-8.0))<0.05);
    a->battery_estimate=a->battery_ocv-13.0;a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-(a->battery_ocv-12.0))<0.05);
    /* While it is drained its voltage sags: that it holds less is believed only from twelve points on, that it holds more at four. */
    {double level=a->battery_ocv;BATTERY_FILE("current_now","%d\n",-200000);BATTERY_FILE("voltage_now","%d\n",3790000-42000);   /* the same open-circuit voltage at 120 mOhm */
     a->battery_ohm=0.120;a->battery_uv=0;a->battery_estimate=level+8.0;a->now+=30000;update_battery(a);assert(fabs(a->battery_ocv-level)<0.6&&fabs(a->battery_estimate-(level+8.0))<0.05);
     a->battery_estimate=a->battery_ocv+13.0;a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-(a->battery_ocv+12.0))<0.6);
     a->battery_estimate=a->battery_ocv-5.0;a->now+=30000;update_battery(a);assert(fabs(a->battery_estimate-(a->battery_ocv-4.0))<0.6);
     BATTERY_FILE("current_now","%d\n",150000);BATTERY_FILE("voltage_now","%d\n",3790000);a->battery_uv=0;a->now+=30000;update_battery(a);}
    /* A heavy current says little about the cell: the smoothed voltage level does not move for it. */
    double held=a->battery_ocv;BATTERY_FILE("current_now","%d\n",-2000000);BATTERY_FILE("voltage_now","%d\n",3500000);
    a->now+=10000;update_battery(a);assert(a->battery_ocv==held&&!a->battery_charging);
    /* The resistance is learned from a step of the current: 0.44 A and 81 mV apart is 185 mOhm, a quarter of the way at a time. */
    BATTERY_FILE("current_now","%d\n",160000);BATTERY_FILE("voltage_now","%d\n",3810000);a->now+=10000;update_battery(a);a->battery_ohm=0.120;
    BATTERY_FILE("current_now","%d\n",-278000);BATTERY_FILE("voltage_now","%d\n",3729000);a->now+=10000;update_battery(a);
    assert(fabs(a->battery_ohm-(0.120+(0.0810/0.438-0.120)*0.25))<0.0005);
    /* A step that took too long, or one that gives no sensible resistance, teaches nothing. */
    double learned=a->battery_ohm;BATTERY_FILE("current_now","%d\n",160000);BATTERY_FILE("voltage_now","%d\n",3810000);a->now+=60000;update_battery(a);assert(a->battery_ohm==learned);
    BATTERY_FILE("current_now","%d\n",-278000);BATTERY_FILE("voltage_now","%d\n",3815000);a->now+=10000;update_battery(a);assert(a->battery_ohm==learned);
    /* Charging near the top is 99 until the charger says Full; then it is 100. */
    BATTERY_FILE("current_now","%d\n",200000);BATTERY_FILE("voltage_now","%d\n",4390000);a->battery_estimate=a->battery_ocv=100.0;
    a->now+=10000;update_battery(a);assert(a->battery_percent==99&&a->battery_charging);
    BATTERY_FILE("status","%s\n","Full");BATTERY_FILE("current_now","%d\n",0);a->now+=10000;update_battery(a);assert(a->battery_percent==100&&!strcmp(a->battery,"100%"));
    /* Below 20 % and not charging the mark is red: the estimate decides, not the kernel's number. */
    BATTERY_FILE("status","%s\n","Discharging");BATTERY_FILE("current_now","%d\n",-300000);BATTERY_FILE("voltage_now","%d\n",3650000);BATTERY_FILE("capacity","%d\n",64);
    a->battery_estimated=false;a->now+=10000;update_battery(a);assert(a->battery_percent<20&&a->battery_percent>=8&&!a->battery_charging);
    /* A gauge that keeps the percent itself (kernel 124) is shown as it is, whatever the voltage says;
       still no 100 beside the charging bolt before the charger says Full. */
    {char device[96];snprintf(device,sizeof(device),"%s/device",folder);assert(!mkdir(device,0700));BATTERY_FILE("device/soc_start","%s\n","stored 64 41");
     a->now+=10000;update_battery(a);assert(a->battery_percent==64&&!a->battery_estimated&&!strcmp(a->battery,"64%"));
     BATTERY_FILE("capacity","%d\n",63);a->now+=10000;update_battery(a);assert(a->battery_percent==63);
     BATTERY_FILE("status","%s\n","Charging");BATTERY_FILE("current_now","%d\n",900000);BATTERY_FILE("capacity","%d\n",100);a->now+=10000;update_battery(a);assert(a->battery_percent==99&&a->battery_charging);
     BATTERY_FILE("status","%s\n","Full");a->now+=10000;update_battery(a);assert(a->battery_percent==100);
     BATTERY_FILE("status","%s\n","Discharging");BATTERY_FILE("current_now","%d\n",-300000);BATTERY_FILE("capacity","%d\n",64);
     snprintf(path,sizeof(path),"%s/device/soc_start",folder);unlink(path);rmdir(device);}
    /* Without the curve the kernel's number is shown as it is. */
    snprintf(path,sizeof(path),"%s/ocv-capacity-table-0",folder);unlink(path);battery_curve.read=false;a->battery_estimated=false;
    a->now+=10000;update_battery(a);assert(a->battery_percent==64&&!a->battery_estimated);
    /* A curve that does not fall from point to point is no curve. */
    table=fopen(path,"wb");assert(table);{unsigned char b[16]={0,0x3e,0x7a,0x10,0,0,0,50, 0,0x40,0x7a,0x10,0,0,0,40};fwrite(b,1,16,table);}fclose(table);
    battery_curve.read=false;a->now+=10000;update_battery(a);assert(battery_curve.count==0&&a->battery_percent==64);
    static const char*const names[]={"capacity","status","current_now","voltage_now","charge_now","charge_full","ocv-capacity-table-0","factory-internal-resistance-micro-ohms"};
    for(size_t n=0;n<sizeof(names)/sizeof(*names);n++){snprintf(path,sizeof(path),"%s/%s",folder,names[n]);unlink(path);}
    rmdir(folder);unsetenv("A22X_RECOVERY_BATTERY");battery_curve.read=false;
    #undef BATTERY_FILE
    free(a->pixels);free(a);return 1;
}
/* 2026-11-12: how Android's last start ended, as init left it in RAM. The page opens once from the
   home page for a start that failed, never for an ordinary restart or a log that just ends, and
   not again for the start the owner has read about. */
static int test_android_log(void){
    char path[]="/tmp/a22x-alog-XXXXXX";int fd=mkstemp(path);assert(fd>=0);close(fd);setenv("A22X_RECOVERY_ANDROID_LOG",path,1);
    #define ALOG_FILE(text) do{FILE*f=fopen(path,"w");assert(f);fputs(text,f);fclose(f);}while(0)
    static const char failed[]="{\"ok\":true,\"operation\":\"read\",\"found\":true,\"code\":\"KERNEL_TOO_OLD\",\"restart\":\"bpfloader-failed\",\"reason\":\"reboot,bpfloader-failed\",\"needs\":\"4.19\",\"detail\":\"\",\"kernel\":\"\",\"seconds\":9.1,"
        "\"lines\":[\"[861:netbpfload]NetBpfLoad: Android V requires kernel 4.19.\",\"[1:init]reboot: Restarting system with command 'bpfloader-failed'\"],\"id\":\"bb9507f313de2d66\",\"log_lines\":2395}";
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->inventory_ok=true;a->now=100000;
    ALOG_FILE(failed);
    a->screen=TOOLS;android_log_poll(a);assert(!a->alog_checked);                       /* only from the home page */
    a->screen=HOME;a->locked=true;android_log_poll(a);assert(!a->alog_checked);a->locked=false;
    android_log_poll(a);assert(a->alog_checked&&a->screen==ANDROID_LOG&&!strcmp(a->alog_code,"KERNEL_TOO_OLD")&&!strcmp(a->alog_needs,"4.19")&&a->alog_seconds==9&&a->alog_line_count==2);
    render(a);assert(test_button(a,A_ALOG_OK)&&test_button(a,A_ALOG_IMAGES)&&test_button(a,A_ALOG_STOCK));
    assert(!a->alog_seen[0]&&test_press(a,A_ALOG_OK,50)&&a->screen==HOME&&!strcmp(a->alog_seen,"bb9507f313de2d66")&&a->settings_dirty);
    /* The same start after the recovery was restarted: read about already, nothing opens. */
    a->alog_checked=false;a->alog_code[0]=0;android_log_poll(a);assert(a->alog_checked&&a->screen==HOME);
    /* Another failed start opens the page again; Back reads it as well, and the pages of Android are one tap away. */
    a->alog_checked=false;copy(a->alog_seen,sizeof(a->alog_seen),"0000000000000000");android_log_poll(a);assert(a->screen==ANDROID_LOG);
    render(a);assert(test_press(a,A_ALOG_IMAGES,50)&&a->screen==ANDROID_MENU&&!strcmp(a->alog_seen,"bb9507f313de2d66"));
    a->alog_checked=false;a->alog_seen[0]=0;a->screen=HOME;android_log_poll(a);assert(a->screen==ANDROID_LOG);action(a,A_BACK);assert(a->screen==HOME&&a->alog_seen[0]);
    /* An ordinary restart, a log that just ends, no Android log at all: nothing opens. */
    a->alog_seen[0]=0;
    ALOG_FILE("{\"ok\":true,\"found\":true,\"code\":\"\",\"restart\":\"recovery\",\"id\":\"1111111111111111\"}");a->alog_checked=false;android_log_poll(a);assert(a->alog_checked&&a->screen==HOME);
    ALOG_FILE("{\"ok\":true,\"found\":true,\"code\":\"CUT_SHORT\",\"restart\":null,\"id\":\"2222222222222222\"}");a->alog_checked=false;android_log_poll(a);assert(a->alog_checked&&a->screen==HOME&&!a->alog_code[0]);
    ALOG_FILE("{\"ok\":true,\"found\":false,\"code\":\"\"}");a->alog_checked=false;android_log_poll(a);assert(a->alog_checked&&a->screen==HOME);
    /* init is still reading: the page waits for the answer, but not for ever. */
    unlink(path);a->alog_checked=false;a->alog_since=0;android_log_poll(a);assert(!a->alog_checked);
    a->now+=5000;ALOG_FILE(failed);android_log_poll(a);assert(a->alog_checked&&a->screen==ANDROID_LOG);action(a,A_BACK);
    unlink(path);a->alog_checked=false;a->alog_since=0;android_log_poll(a);a->now+=21000;android_log_poll(a);assert(a->alog_checked&&a->screen==HOME);
    #undef ALOG_FILE
    unsetenv("A22X_RECOVERY_ANDROID_LOG");free(a->pixels);free(a);return 1;
}
/* 2026-11-11: the flashlight is the camera's LED in the kernel's LED class; what is shown is what
   the LED took. A folder of two files stands in for it. */
static int test_torch(void){
    char folder[]="/tmp/a22x-torch-XXXXXX",path[96];assert(mkdtemp(folder));
    #define TORCH_FILE(name,value) do{snprintf(path,sizeof(path),"%s/%s",folder,name);FILE*f=fopen(path,"w");assert(f);fputs(value,f);fclose(f);}while(0)
    TORCH_FILE("max_brightness","7\n");TORCH_FILE("brightness","0\n");setenv("A22X_RECOVERY_TORCH",folder,1);
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->inventory_ok=true;a->now=10000;a->screen=HOME;render(a);
    unsigned int now=9;assert(torch_ready(a)&&a->torch_max==7&&!a->torch_on&&a->torch_level==4&&test_button(a,A_TORCH));
    assert(test_press(a,A_TORCH,50)&&a->torch_on&&torch_value(folder,"brightness",&now)&&now==4);
    render(a);assert(test_press(a,A_TORCH,50)&&!a->torch_on&&torch_value(folder,"brightness",&now)&&now==0);
    /* Settings: On and Off, and the brightness: - and + through every level, or a touch on the bar.
       While the light is off a step is kept for the next time. */
    a->screen=TOOLS;a->tools_scroll=500;render(a);assert(test_button(a,A_TORCH_ON)&&test_button(a,A_TORCH_OFF)&&test_button(a,A_TORCH_UP)&&test_button(a,A_TORCH_DOWN)&&test_button(a,A_TORCH_BAR));
    assert(test_press(a,A_TORCH_UP,50)&&!a->torch_on&&a->torch_level==5&&a->torch_saved==5&&a->settings_dirty&&torch_value(folder,"brightness",&now)&&now==0);
    assert(test_press(a,A_TORCH_OFF,50)&&!a->torch_on&&torch_value(folder,"brightness",&now)&&now==0); /* off already */
    assert(test_press(a,A_TORCH_ON,50)&&a->torch_on&&torch_value(folder,"brightness",&now)&&now==5);
    assert(test_press(a,A_TORCH_ON,50)&&a->torch_on&&torch_value(folder,"brightness",&now)&&now==5);   /* on already */
    assert(test_press(a,A_TORCH_UP,50)&&test_press(a,A_TORCH_UP,50)&&torch_value(folder,"brightness",&now)&&now==7&&a->torch_level==7);
    render(a);assert(!test_button(a,A_TORCH_UP)->enabled&&!test_press(a,A_TORCH_UP,50)&&a->torch_level==7); /* the brightest: no further */
    for(int n=0;n<6;n++)assert(test_press(a,A_TORCH_DOWN,50));
    assert(torch_value(folder,"brightness",&now)&&now==1&&a->torch_level==1&&a->torch_saved==1);
    render(a);assert(!test_button(a,A_TORCH_DOWN)->enabled);
    /* The bar takes the level under the finger: its ends are the dimmest and the brightest. */
    {Button*bar=test_button(a,A_TORCH_BAR);assert(bar);Rect r=bar->r;int y=r.y+r.h/2;uint64_t t=milliseconds();
     pointer(a,true,r.x+r.w-6,y,t);pointer(a,false,r.x+r.w-6,y,t+50);assert(a->torch_level==7&&a->torch_saved==7&&torch_value(folder,"brightness",&now)&&now==7);
     render(a);t=milliseconds();pointer(a,true,r.x+6,y,t);pointer(a,false,r.x+6,y,t+50);assert(a->torch_level==1&&torch_value(folder,"brightness",&now)&&now==1);
     assert(test_press(a,A_TORCH_BAR,50)&&a->torch_level==4&&torch_value(folder,"brightness",&now)&&now==4);       /* its middle */
     assert(torch_bar_level(a,r,r.x-50)==1&&torch_bar_level(a,r,r.x+r.w+50)==7);
     for(unsigned int n=1;n<=7;n++)assert(torch_bar_level(a,r,r.x+4+(int)((unsigned int)(r.w-8)*n/7)-2)==n);      /* just inside each level's end */
     /* Chosen with the keys there is no finger to place: the bar is one step brighter. */
     action(a,A_TORCH_BAR);assert(a->torch_level==5&&torch_value(folder,"brightness",&now)&&now==5);
     assert(test_press(a,A_TORCH_DOWN,50)&&torch_value(folder,"brightness",&now)&&now==4);}
    /* The power menu switches it and stays open; its - and + step too; the level last used comes back. */
    power_menu_open(a);render(a);assert(a->screen==POWER_MENU&&test_button(a,A_TORCH)&&test_button(a,A_TORCH_UP));
    assert(test_press(a,A_TORCH,50)&&a->screen==POWER_MENU&&!a->torch_on&&torch_value(folder,"brightness",&now)&&now==0);
    assert(test_press(a,A_TORCH,50)&&a->screen==POWER_MENU&&a->torch_on&&torch_value(folder,"brightness",&now)&&now==4);
    assert(test_press(a,A_TORCH_UP,50)&&a->screen==POWER_MENU&&torch_value(folder,"brightness",&now)&&now==5);
    assert(test_press(a,A_TORCH_DOWN,50)&&a->screen==POWER_MENU&&torch_value(folder,"brightness",&now)&&now==4);
    assert(test_press(a,A_MENU_CLOSE,50)&&a->screen==TOOLS);
    /* A level kept in the settings is the one a fresh start uses, when the LED has it. */
    {App*b=calloc(1,sizeof(*b));assert(b);initialize(b);b->torch_saved=6;assert(torch_ready(b)&&b->torch_level==4);   /* the light is on at 4: shown as it is */
     b->torch_checked=false;b->torch_on=false;TORCH_FILE("brightness","0\n");assert(torch_ready(b)&&b->torch_level==6);
     b->torch_checked=false;b->torch_saved=99;assert(torch_ready(b)&&b->torch_level==4);
     /* Settings read after the first frame: their level is taken, unless the LED has none such. */
     b->torch_saved=2;torch_remembered(b);assert(b->torch_level==2);b->torch_saved=99;torch_remembered(b);assert(b->torch_level==2);
     b->torch_saved=0;torch_remembered(b);assert(b->torch_level==2);free(b->pixels);free(b);}
    TORCH_FILE("brightness","4\n");
    /* A power action takes the light with it: the one this interface lit, and one lit by other hands. */
    a->torch_on=true;torch_leave(a);assert(!a->torch_on&&torch_value(folder,"brightness",&now)&&now==0&&a->torch_level==4);
    TORCH_FILE("brightness","3\n");torch_leave(a);assert(!a->torch_on&&torch_value(folder,"brightness",&now)&&now==0);
    torch_leave(a);assert(torch_value(folder,"brightness",&now)&&now==0);
    TORCH_FILE("brightness","4\n");a->torch_on=true;
    /* An LED that does not take the value: said so, and shown as it is. */
    snprintf(path,sizeof(path),"%s/brightness",folder);assert(chmod(path,0444)==0);
    if(geteuid()){a->toast[0]=0;render(a);assert(test_press(a,A_TORCH_OFF,50)&&a->torch_on&&a->toast[0]);}
    /* A phone without the LED shows no flashlight anywhere. */
    unsetenv("A22X_RECOVERY_TORCH");a->torch_checked=a->torch_available=false;a->screen=HOME;render(a);assert(!torch_ready(a)&&!test_button(a,A_TORCH));
    chmod(path,0644);unlink(path);snprintf(path,sizeof(path),"%s/max_brightness",folder);unlink(path);rmdir(folder);
    #undef TORCH_FILE
    free(a->pixels);free(a->menu_backdrop);free(a);return 1;
}
/* 2026-11-11, as TWRP's Restore defaults: a hold on the last button of Settings puts the ways the
   recovery behaves back; the language and the time zone are the user's and stay. */
static int test_settings_reset(void){
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);a->preview=true;a->inventory_ok=true;
    unsigned int language=ui_language;const Theme*theme=ui_theme;const TerminalScheme*scheme=ui_terminal;
    ui_language=1;(void)theme_select(a,themes[THEME_COUNT-1].code);assert(ui_theme!=&themes[0]);
    a->animation=3;a->nav_mode=NAV_BUTTONS;a->vibration=0;a->screen_timeout_index=5;a->saved_hz=90;a->saved_brightness=40;a->lock_mode=0;a->access_mode=false;a->storage_prefers_usb=true;a->utc_offset=120;
    a->screen=TOOLS;render(a);assert(a->tools_content>1254);a->tools_scroll=tools_max_scroll(a);render(a);
    Button*b=test_button(a,A_SETTINGS_RESET);assert(b&&b->enabled&&b->hold_ms==3000&&b->r.y+b->r.h==1484-28);
    /* The page ends with the button: nothing scrolls past it, with or without the lock rows. */
    a->lock_enabled=true;render(a);assert(a->tools_scroll<tools_max_scroll(a));a->tools_scroll=tools_max_scroll(a);render(a);b=test_button(a,A_SETTINGS_RESET);assert(b&&b->r.y+b->r.h==1484-28);
    a->lock_enabled=false;render(a);assert(a->tools_scroll==tools_max_scroll(a));render(a);b=test_button(a,A_SETTINGS_RESET);assert(b&&b->r.y+b->r.h==1484-28);
    assert(test_press(a,A_SETTINGS_RESET,600)&&a->animation==3&&ui_theme!=&themes[0]); /* A tap changes nothing. */
    assert(test_press(a,A_SETTINGS_RESET,3100));
    assert(ui_theme==&themes[0]&&a->animation==0&&a->nav_mode==NAV_GESTURES&&a->vibration==2&&a->screen_timeout_index==SCREEN_TIMEOUT_DEFAULT);
    assert(!a->saved_hz&&!a->saved_brightness&&!a->storage_prefers_usb&&a->settings_dirty&&a->screen==TOOLS);
    /* Who uses the phone and who may reach it stay as they were set. */
    assert(ui_language==1&&a->utc_offset==120&&a->lock_mode==0&&!a->access_mode);
    ui_language=language;ui_theme=theme;ui_terminal=scheme;free(a->pixels);free(a);return 1;
}
static int self_test(void){
    App*a=calloc(1,sizeof(*a));assert(a);initialize(a);int tests=0;
    JsonToken tokens[64];Json j;assert(json_parse(&j,"{\"ok\":true,\"nested\":{\"x\":7},\"name\":\"quote\\\"\",\"arr\":[1,2]}",tokens,64)>0);assert(json_bool(&j,json_get(&j,0,"ok"),0)==1);char s[64];assert(json_string(&j,json_get(&j,0,"name"),s,sizeof(s))>=0&&!strcmp(s,"quote\""));assert(json_uint(&j,json_get(&j,json_get(&j,0,"nested"),"x"),0)==7);tests++;
    assert(json_parse(&j,"{\"ok\":tru}",tokens,64)<0);assert(json_parse(&j,"{\"ok\":true}garbage",tokens,64)<0);assert(json_parse(&j,"[1,]",tokens,64)<0);tests++;
    assert(axis_to_pixel(0,0,1079,1080)==0);assert(axis_to_pixel(1079,0,1079,1080)==1079);assert(axis_to_pixel(2407,0,2407,2408)==2407);assert(axis_to_pixel(2,1,1,100)==-1);tests++;
    assert(component(255,(struct fb_bitfield){16,8,0})==0xff0000);assert(component(255,(struct fb_bitfield){11,5,0})==0xf800);tests++;
    a->screen=HOME;render(a);pointer(a,true,180,500,100);pointer(a,false,180,500,200);assert(a->screen==BACKUP);tests++;
    a->allow_power=true;a->screen=POWER_CONFIRM;a->power_intent=200;render(a);pointer(a,true,300,1260,1000);pointer(a,false,300,1260,2999);assert(a->running);tests++;
    pointer(a,true,300,1260,3000);pointer(a,true,4,4,5001);pointer(a,false,300,1260,5200);assert(a->running);tests++;
    pointer(a,true,300,1260,6000);pointer(a,false,300,1260,8000);assert(!a->running&&a->exit_code==200);tests++;
    a->running=true;a->allow_power=false;a->exit_code=0;render(a);pointer(a,true,300,1260,9000);pointer(a,false,300,1260,13000);assert(a->running&&a->exit_code==0);tests++;
    a->screen=PLAN;a->plan_can_execute=false;render(a);assert(button_at(a,300,1360)<0);tests++;
    assert(safe_token("CONFIRM_0123456789abcdef"));assert(!safe_token("x; reboot"));assert(!safe_token(""));tests++;
    assert(json_parse(&j,"{\"path\":\"/backup/\\u0410\\ud83d\\ude00\",\"ok\":\"true\"}",tokens,64)>0);assert(json_string(&j,json_get(&j,0,"path"),s,sizeof(s))>0&&!strcmp(s,"/backup/\320\220\360\237\230\200"));assert(!json_bool(&j,json_get(&j,0,"ok"),0));tests++;
    assert(json_parse(&j,"{\"path\":\"x\\u0000y\"}",tokens,64)>0);assert(json_string(&j,json_get(&j,0,"path"),s,sizeof(s))<0);assert(json_parse(&j,"{\"path\":\"\\ud800\"}",tokens,64)>0);assert(json_string(&j,json_get(&j,0,"path"),s,sizeof(s))<0);tests++;
    Display d={.v={.xres=1080,.yres=2408},.vx=0,.vy=4,.vw=1080,.vh=2400};Input input={.mt=true,.xmin=0,.xmax=1079,.ymin=0,.ymax=2407,.primary_id=-1};for(int k=0;k<MAX_SLOTS;k++)input.slots[k].id=-1;
    a->screen=HOME;render(a);input.slots[0]=(Slot){.id=42,.x=270,.y=754,.have_x=true,.have_y=true};a->now=15000;input_sync(a,&input,&d);assert(input.down&&a->pressed_action==A_BACKUP);a->now=15050;input_sync(a,&input,&d);assert(input.slots[0].x==270&&input.slots[0].y==754&&a->pressed_action==A_BACKUP);input.slots[0].id=-1;a->now=15100;input_sync(a,&input,&d);assert(a->screen==BACKUP);tests++;
    a->allow_power=true;a->screen=POWER_CONFIRM;a->power_intent=200;a->running=true;a->exit_code=0;render(a);input.slots[0]=(Slot){.id=45,.x=450,.y=1894,.have_x=true,.have_y=true};a->now=16000;input_sync(a,&input,&d);assert(a->pressed_action==A_POWER_CONFIRM);input.slots[1]=(Slot){.id=46,.x=451,.y=1894,.have_x=true,.have_y=true};a->now=18100;input_sync(a,&input,&d);input.slots[1].id=-1;input_sync(a,&input,&d);input.slots[0].id=-1;input_sync(a,&input,&d);assert(a->running&&!a->pointer_down&&a->exit_code==0);tests++;
    int pipefd[2];assert(pipe2(pipefd,O_NONBLOCK|O_CLOEXEC)==0);input.fd=pipefd[0];input.slots[0]=(Slot){.id=47,.x=450,.y=1894,.have_x=true,.have_y=true};a->now=19000;input_sync(a,&input,&d);struct input_event events[]={{.type=EV_SYN,.code=SYN_DROPPED},{.type=EV_SYN,.code=SYN_REPORT},{.type=EV_ABS,.code=ABS_MT_TRACKING_ID,.value=-1},{.type=EV_SYN,.code=SYN_REPORT}};assert(write(pipefd[1],events,sizeof(events))==(ssize_t)sizeof(events));a->now=22000;handle_input(a,&input,&d);assert(a->running&&!a->pointer_down);close(pipefd[0]);close(pipefd[1]);tests++;
    assert(pipe2(pipefd,O_NONBLOCK|O_CLOEXEC)==0);input.fd=pipefd[0];input.need_release=false;input.slot=0;input.slots[0]=(Slot){.id=-1,.x=270,.y=754,.have_x=true,.have_y=true};a->screen=HOME;render(a);struct input_event repeated[]={{.type=EV_ABS,.code=ABS_MT_TRACKING_ID,.value=48},{.type=EV_SYN,.code=SYN_REPORT}};assert(write(pipefd[1],repeated,sizeof(repeated))==(ssize_t)sizeof(repeated));a->now=23000;handle_input(a,&input,&d);assert(a->pressed_action==A_BACKUP);repeated[0].value=-1;assert(write(pipefd[1],repeated,sizeof(repeated))==(ssize_t)sizeof(repeated));a->now=23100;handle_input(a,&input,&d);assert(a->screen==BACKUP);close(pipefd[0]);close(pipefd[1]);tests++;
    copy(a->plan_path,sizeof(a->plan_path),"/private/expected.json");assert(json_parse(&j,"{\"plan_path\":\"/private/wrong.json\",\"plan_id\":\"x\",\"confirmation_token\":\"BACKUP-abc\",\"can_execute\":true,\"summary\":{\"profile\":\"linux\",\"partitions\":[\"boot\"]},\"blockers\":[]}",tokens,64)>0);parse_plan(a,&j,J_BACKUP_PLAN);assert(!a->plan_can_execute&&strstr(a->blockers,"PLAN_PATH_MISMATCH"));tests++;
    a->device=true;a->plan_can_execute=true;assert(!start_job(a,J_RESTORE)&&!a->job.pid&&!strcmp(a->status,"DEVICE_RESTORE_NOT_QUALIFIED"));tests++;
    a->device=false;
    const char*unicode="Ґіїєé";const char*up=unicode;assert(utf8_next(&up)==0x490);assert(utf8_next(&up)==0x456);assert(utf8_previous(unicode,4)==2);assert(utf8_prefix(unicode,3)==2);char encoded[5];assert(utf8_encode(0x491,encoded)==2&&!strcmp(encoded,"ґ"));const uint8_t*alpha;const Glyph*g=find_glyph(0x491,0,&alpha);assert(alpha==extra_font_alpha&&g->advance>0);tests++;
    char normalized[PATH_LEN];assert(relative_path("Документи/файл.txt",normalized,sizeof(normalized))&&!strcmp(normalized,"/Документи/файл.txt"));assert(!relative_path("/a/../b",normalized,sizeof(normalized)));assert(!relative_path("/a//b",normalized,sizeof(normalized)));assert(!relative_path("/a/./b",normalized,sizeof(normalized)));assert(!relative_path("/a\nb",normalized,sizeof(normalized)));assert(relative_path("/",normalized,sizeof(normalized))&&!strcmp(normalized,"/"));tests++;
    JsonToken ft[256];Json fj;
    assert(json_parse(&fj,"{\"volumes\":[{\"id\":\"boot\",\"mountable\":false},{\"id\":\"sdcard-p1\",\"mounted\":true,\"mountable\":true,\"read_only\":false},{\"id\":\"userdata\",\"mounted\":true,\"mountable\":true,\"read_only\":false,\"write_supported\":true},{\"id\":\"cache\",\"mountable\":true,\"write_supported\":true}]}",ft,256)>0);copy(a->volume_id,sizeof(a->volume_id),"userdata");parse_file_volumes(a,&fj);assert(a->volume_count==4&&a->volume_index==0&&!strcmp(a->volumes[0].id,"userdata")&&!strcmp(a->volumes[3].id,"boot"));assert(files_writable(a));assert(next_writable_volume(a,0)==0);tests++;
    a->volume_index=1;copy(a->volume_id,sizeof(a->volume_id),"sdcard-p1");assert(!files_writable(a));a->screen=FILE_VOLUME;render(a);assert(!test_button(a,A_FS_MOUNT_RO)||test_button(a,A_FS_MOUNT_RO)->enabled);a->volumes[1].read_only=true;render(a);assert(!test_button(a,A_FS_MOUNT_RW)->enabled);a->screen=FILE_BROWSER;render(a);assert(!test_button(a,A_FS_NEW_FILE)->enabled&&!test_button(a,A_FS_MKDIR)->enabled);tests++;
    copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/stale-other-volume");copy(a->fs_file,sizeof(a->fs_file),"/stale-file");select_volume(a,3);assert(a->screen==FILE_VOLUME&&!a->fs_selected.path[0]&&!a->fs_file[0]);assert(file_error_return(J_FS_MKDIR)==FILE_PROMPT&&file_error_return(J_FS_READ)==FILE_BROWSER);tests++;
    assert(json_parse(&fj,"{\"content\":\"ї\\n\",\"sha256\":null,\"truncated\":true,\"path\":\"/huge.txt\",\"read_only\":false}",ft,256)>0);assert(parse_file_text(a,&fj)&&a->fs_length==3&&a->fs_truncated&&!a->fs_sha256[0]);a->screen=FILE_TEXT;render(a);assert(!test_button(a,A_FS_EDIT)->enabled);assert(json_parse(&fj,"{\"content\":\"text\",\"sha256\":null,\"truncated\":false,\"path\":\"/text.txt\"}",ft,256)>0);assert(!parse_file_text(a,&fj));tests++;
    a->volume_index=0;copy(a->volume_id,sizeof(a->volume_id),"userdata");a->screen=FILE_EDIT;a->fs_truncated=false;a->fs_text[0]=0;a->fs_length=a->fs_cursor=a->fs_scroll=0;a->kb_uk=true;render(a);assert(a->keyboard_count<56&&a->button_count<MAX_BUTTONS);int letter=-1;for(int n=0;n<a->keyboard_count;n++)if(a->keyboard_keys[n]==0x491)letter=A_KB_KEY+n;assert(letter>=A_KB_KEY&&test_press(a,letter,50));assert(!strcmp(a->fs_text,"ґ")&&a->fs_cursor==2);assert(test_press(a,A_KB_BACKSPACE,50)&&!a->fs_text[0]&&a->fs_cursor==0);for(int n=0;n<a->button_count;n++){Rect r=a->buttons[n].r;assert(r.x>=0&&r.y>=0&&r.x+r.w<=CW&&r.y+r.h<=CH);}tests++;
    insert_character(a,0x456);insert_character(a,0x457);insert_character(a,'x');move_text_cursor(a,A_FS_LEFT);erase_character(a);assert(!strcmp(a->fs_text,"іx")&&a->fs_cursor==2&&a->fs_length==3);move_text_cursor(a,A_FS_LEFT);insert_character(a,0x454);assert(!strcmp(a->fs_text,"єіx")&&a->fs_cursor==2);tests++;
    memset(a->fs_text,'a',TEXT_LIMIT-1);a->fs_text[TEXT_LIMIT-1]=0;a->fs_length=a->fs_cursor=TEXT_LIMIT-1;insert_character(a,0x491);assert(a->fs_length==TEXT_LIMIT-1);insert_character(a,'b');assert(a->fs_length==TEXT_LIMIT&&a->fs_text[TEXT_LIMIT]==0);insert_character(a,'c');assert(a->fs_length==TEXT_LIMIT);tests++;
    assert(row_start_at("ab\n",3,628)==3);assert(row_start_at("ab\ncd",3,628)==3);int narrow=codepoint_width(0x457,0);TextRow row=text_row("її",0,narrow);assert(row.end==2&&row.next==2&&row_start_at("її",2,narrow)==2);copy(a->fs_text,sizeof(a->fs_text),"ab\n");a->fs_length=3;a->fs_cursor=3;a->fs_scroll=0;a->pointer_x=55;a->pointer_y=290;tap_text_cursor(a);assert(a->fs_cursor==3);tests++;
    FileRequest long_path={.kind=J_FS_DELETE};memset(long_path.path,'x',sizeof(long_path.path)-1);long_path.path[0]='/';confirm_file_request(a,long_path,FILE_INFO);assert(confirmation_pages(&long_path)>1);render(a);assert(!test_button(a,A_FS_CONFIRM)->enabled);while(a->fs_confirm_page+1<confirmation_pages(&long_path))action(a,A_FS_CONFIRM_NEXT);render(a);assert(test_button(a,A_FS_CONFIRM)->enabled);tests++;
    a->screen=FILE_EDIT;a->fs_dirty=true;leave_editor(a);assert(a->screen==FILE_DISCARD);assert(test_press(a,A_FS_DISCARD,999)&&a->screen==FILE_DISCARD);assert(test_press(a,A_BACK,50)&&a->screen==FILE_EDIT);tests++;
    a->screen=HOME;render(a);d.map=calloc((size_t)1080*2408,4);assert(d.map);d.length=(size_t)1080*2408*4;d.v.bits_per_pixel=32;d.f.line_length=1080*4;d.v.red=(struct fb_bitfield){16,8,0};d.v.green=(struct fb_bitfield){8,8,0};d.v.blue=(struct fb_bitfield){0,8,0};assert(present(a,&d));uint32_t*screen_pixels=(uint32_t*)d.map;assert(screen_pixels[0]==BG&&screen_pixels[2407*1080+1079]==BG);assert(screen_pixels[(4+750)*1080+270]==a->pixels[(size_t)nx(500)*NW+(size_t)nx(180)]);free(d.frame);d.frame=NULL;free(d.map);tests++;
    for(int n=0;n<2;n++){a->allow_power=true;a->running=true;a->exit_code=0;screen(a,POWER);assert(test_press(a,n?A_REBOOT_DOWNLOAD:A_REBOOT_RECOVERY,50)&&a->screen==POWER_CONFIRM&&a->power_intent==202+n);assert(test_press(a,A_POWER_CONFIRM,1999)&&a->running&&!a->exit_code);assert(test_press(a,A_POWER_CONFIRM,2000)&&!a->running&&a->exit_code==202+n);tests++;}
    a->running=true;a->exit_code=0;a->power_intent=204;screen(a,POWER_CONFIRM);assert(!test_press(a,A_POWER_CONFIRM,5000));action(a,A_POWER_CONFIRM);assert(a->running&&!a->exit_code);a->power_intent=202;a->job.pid=1;action(a,A_POWER_CONFIRM);assert(a->running&&!a->exit_code);a->job.pid=0;a->allow_power=false;assert(!test_press(a,A_POWER_CONFIRM,5000));tests++;
    a->fs_dirty=a->fs_new_file=false;copy(a->profile,sizeof(a->profile),"android");action(a,A_BACKUP);assert(!strcmp(a->profile,"linux"));render(a);assert(test_button(a,A_PROFILE)&&test_button(a,A_PROFILE+1)&&!test_button(a,A_PROFILE+2));assert(test_press(a,A_PROFILE+1,50)&&!strcmp(a->profile,"recovery"));tests++;
    assert(file_job(J_FS_COPY_TREE)&&!file_job(J_TOOLS_STATUS)&&!file_job(J_TOOLS_EXPORT));assert(numeric_owner("0",1)&&numeric_owner("4294967294",10)&&!numeric_owner("4294967295",10)&&!numeric_owner("-1",2)&&!numeric_owner("2;reboot",8));tests++;
    assert(json_parse(&fj,"{\"entry\":{\"name\":\"file\",\"path\":\"/file\",\"type\":\"file\",\"mode\":420,\"uid\":1000,\"gid\":1001,\"nlink\":2,\"entry_token\":\"token\",\"metadata_supported\":false,\"metadata_reason\":\"Hard link\"}}",ft,256)>0);assert(parse_file_entry(&fj,json_get(&fj,0,"entry"),&a->fs_selected)&&!strcmp(a->fs_selected.mode,"0644")&&a->fs_selected.uid==1000&&a->fs_selected.gid==1001&&!a->fs_selected.metadata_supported);a->volume_index=0;copy(a->volume_id,sizeof(a->volume_id),"userdata");screen(a,FILE_INFO);render(a);assert(!test_button(a,A_FS_CHMOD)->enabled&&!test_button(a,A_FS_CHOWN)->enabled);tests++;
    a->fs_selected.metadata_supported=true;memset(a->fs_selected.token,'1',64);a->fs_selected.token[64]=0;begin_file_prompt(a,J_FS_CHMOD);copy(a->fs_prompt,sizeof(a->fs_prompt),"0888");accept_file_prompt(a);assert(a->screen==RESULT&&!a->job.pid&&!strcmp(a->status,"INVALID_MODE")&&a->result_return==FILE_PROMPT);begin_file_prompt(a,J_FS_CHMOD);copy(a->fs_prompt,sizeof(a->fs_prompt),"0750");accept_file_prompt(a);assert(a->screen==FILE_CONFIRM&&!strcmp(a->fs_pending.mode,"0750")&&!strcmp(a->fs_pending.entry_token,a->fs_selected.token)&&!strcmp(a->fs_pending.path,"/file"));assert(test_press(a,A_FS_CONFIRM,1999)&&a->screen==FILE_CONFIRM&&!a->job.pid);tests++;
    begin_file_prompt(a,J_FS_CHOWN);copy(a->fs_prompt,sizeof(a->fs_prompt),"0:-1");accept_file_prompt(a);assert(a->screen==RESULT&&!strcmp(a->status,"INVALID_OWNER")&&!a->job.pid);begin_file_prompt(a,J_FS_CHOWN);copy(a->fs_prompt,sizeof(a->fs_prompt),"1000:1001");accept_file_prompt(a);assert(a->screen==FILE_CONFIRM&&!strcmp(a->fs_pending.uid,"1000")&&!strcmp(a->fs_pending.gid,"1001")&&!strcmp(a->fs_pending.entry_token,a->fs_selected.token));tests++;
    assert(json_parse(&fj,"{\"ssh\":{\"available\":true,\"running\":false,\"address\":\"172.16.42.1\",\"port\":22,\"authorized_keys_count\":0,\"host_key_ephemeral\":true,\"host_fingerprint\":\"SHA256:example\",\"reason\":\"No authorized key\"},\"logs\":{\"export_available\":false}}",ft,256)>0);assert(complete_tools_job(a,&fj,J_TOOLS_STATUS)&&a->tools_known&&!a->ssh_running&&!a->ssh_keys&&a->ssh_ephemeral&&!strcmp(a->ssh_fingerprint,"SHA256:example"));a->tools_scroll=200;render(a);assert(!test_button(a,A_TOOLS_EXPORT)&&!test_button(a,A_TERMINAL)&&!test_button(a,A_USB_FILES));tools_select_volume(a,-1);screen(a,TOOLS_EXPORT);render(a);assert(!test_button(a,A_TOOLS_EXPORT_CONFIRM)->enabled);tests++;
    a->preview=true;reset_browser(a);a->file_total=180;a->file_count=FILE_BATCH;a->fs_file[0]=0;copy(a->fs_path,sizeof(a->fs_path),"/");
    for(int n=0;n<FILE_BATCH;n++){FileEntry*e=&a->files[n];memset(e,0,sizeof(*e));snprintf(e->name,sizeof(e->name),"entry-%03d",n);snprintf(e->path,sizeof(e->path),"/entry-%03d",n);copy(e->type,sizeof(e->type),"file");}
    screen(a,FILE_BROWSER);render(a);pointer(a,true,250,1050,30000);pointer(a,true,250,1029,30030);assert(a->scroll_dragging&&a->browser_scroll==21&&!a->pressed_action);pointer(a,true,250,550,30060);pointer(a,false,250,550,30080);assert(a->screen==FILE_BROWSER&&a->browser_scroll==500&&a->browser_scroll%FILE_ROW_HEIGHT&&!a->fs_file[0]&&!a->job.pid);tests++;
    render(a);pointer(a,true,250,550,31000);pointer(a,true,250,1050,31040);pointer(a,false,250,1050,31080);assert(a->browser_scroll==0&&a->screen==FILE_BROWSER&&!a->fs_file[0]);tests++;
    render(a);pointer(a,true,250,700,32000);pointer(a,true,250,1800,32040);pointer(a,false,250,1800,32080);assert(a->browser_scroll==0);a->browser_scroll=browser_max_scroll(a)-100;render(a);pointer(a,true,250,1000,33000);pointer(a,true,250,-200,33040);pointer(a,false,250,-200,33080);assert(a->browser_scroll==browser_max_scroll(a)&&a->screen==FILE_BROWSER);tests++;
    a->browser_scroll=0;render(a);pointer(a,true,250,500,34000);pointer(a,true,500,500,34040);pointer(a,false,250,500,34080);assert(!a->browser_scroll&&!a->fs_file[0]&&a->screen==FILE_BROWSER);render(a);pointer(a,true,631,1050,35000);pointer(a,true,631,550,35040);pointer(a,false,631,550,35080);assert(a->browser_scroll==500&&a->screen==FILE_BROWSER&&!a->job.pid);tests++;
    stop_inertia(a,"test-reset");a->browser_scroll=0;render(a);pointer(a,true,250,500,36000);pointer(a,true,255,505,36040);pointer(a,false,255,505,36080);assert(!strcmp(a->fs_file,"/entry-000")&&!a->browser_scroll);tests++;
    a->browser_scroll=57;render(a);for(int n=0;n<a->button_count;n++){Button*b=&a->buttons[n];if((b->action>=A_FS_ENTRY&&b->action<A_FS_ENTRY+FILE_BATCH)||(b->action>=A_FS_ENTRY_MENU&&b->action<A_FS_ENTRY_MENU+FILE_BATCH)){assert(b->r.y>=FILE_LIST_TOP&&b->r.y+b->r.h<=FILE_LIST_BOTTOM);}}assert(a->pixels[(size_t)nx(420)*NW+(size_t)nx(100)]==BG&&a->pixels[(size_t)nx(1232)*NW+(size_t)nx(100)]==BG);tests++;
    a->fs_request.offset=64;copy(a->fs_request.path,sizeof(a->fs_request.path),"/");assert(json_parse(&fj,"{\"path\":\"/\",\"offset\":64,\"total\":66,\"has_more\":false,\"entries\":[{\"name\":\"e64\",\"path\":\"/e64\",\"type\":\"file\"},{\"name\":\"e65\",\"path\":\"/e65\",\"type\":\"file\"}]}",ft,256)>0);assert(parse_file_list(a,&fj)&&a->file_count==2&&a->file_offset==64&&a->file_total==66);assert(json_parse(&fj,"{\"path\":\"/\",\"offset\":0,\"total\":66,\"entries\":[]}",ft,256)>0);assert(!parse_file_list(a,&fj));tests++;
    screen(a,FILE_BROWSER);a->file_count=1;a->file_total=1;a->file_offset=0;a->browser_scroll=0;a->fs_request.offset=0;a->fs_file[0]=a->fs_selected.path[0]=0;
    copy(a->files[0].name,sizeof(a->files[0].name),"old.txt");copy(a->files[0].path,sizeof(a->files[0].path),"/old.txt");copy(a->files[0].type,sizeof(a->files[0].type),"file");render(a);assert(test_button(a,A_FS_ENTRY)->enabled);
    assert(json_parse(&fj,"{\"path\":\"/\",\"offset\":0,\"total\":1,\"entries\":[{\"name\":\"new.txt\",\"path\":\"/new.txt\",\"type\":\"file\"}]}",ft,256)>0);assert(parse_file_list(a,&fj)&&!a->button_count&&a->focus==-1);
    pointer(a,true,250,470,28000);pointer(a,false,250,470,28100);assert(a->screen==FILE_BROWSER&&!a->fs_file[0]&&!a->fs_selected.path[0]);render(a);assert(test_button(a,A_FS_ENTRY)->enabled);tests++;
    assert(json_parse(&fj,"{\"supported\":true,\"available_hz\":[60,90],\"current_hz\":60,\"actual_hz\":59.9998,\"service_running\":true,\"reason\":null}",ft,256)>0);assert(parse_display_status(a,&fj)&&a->display_hz==60&&a->display_90&&a->display_actual_hz>59.99);screen(a,DISPLAY_SETTINGS);render(a);assert(test_button(a,A_DISPLAY_90)->enabled&&!test_button(a,A_DISPLAY_60)->enabled);tests++;
    assert(json_parse(&fj,"{\"supported\":false,\"available_hz\":[],\"current_hz\":null,\"actual_hz\":null,\"service_running\":false,\"reason\":\"Unavailable\"}",ft,256)>0);assert(parse_display_status(a,&fj)&&!a->display_hz&&!a->display_actual_hz);render(a);assert(!test_button(a,A_DISPLAY_60)->enabled&&!test_button(a,A_DISPLAY_90)->enabled);tests++;
    FileEntry typed={0};copy(typed.type,sizeof(typed.type),"file");copy(typed.mode,sizeof(typed.mode),"0644");assert(!file_is_executable(&typed)&&file_color(&typed)==FILE_BLUE&&!strcmp(file_type_label(&typed),"File"));copy(typed.mode,sizeof(typed.mode),"0755");assert(!file_is_executable(&typed)&&!strcmp(file_type_label(&typed),"File"));typed.metadata_supported=true;assert(file_is_executable(&typed)&&file_color(&typed)==GREEN);copy(typed.type,sizeof(typed.type),"symlink");assert(!file_is_executable(&typed)&&file_color(&typed)==LINK_PURPLE);copy(typed.type,sizeof(typed.type),"directory");assert(file_color(&typed)==ORANGE);copy(typed.type,sizeof(typed.type),"other");assert(file_color(&typed)==MUTED&&strstr(file_type_label(&typed),"blocked"));tests++;
    ScrollSamples samples={0};scroll_sample(&samples,100,0);scroll_sample(&samples,150,200);scroll_sample(&samples,180,320);assert(scroll_release_velocity(&samples,180)==SCROLL_MAX_SPEED);scroll_sample(&samples,350,320);assert(!scroll_release_velocity(&samples,350));tests++;
    samples.count=0;scroll_sample(&samples,1000,0);scroll_sample(&samples,1100,12);assert(!scroll_release_velocity(&samples,1100));scroll_sample(&samples,900,20);assert(samples.count==1&&!scroll_release_velocity(&samples,900));tests++;
    KineticScroll one={0},many={0};assert(kinetic_begin(&one,100,2000,10000,1000));many=one;assert(kinetic_advance(&one,1100,10000));for(uint64_t t=1010;t<=1100;t+=10)(void)kinetic_advance(&many,t,10000);assert(scroll_abs(one.position-many.position)<.001&&scroll_abs(one.velocity-many.velocity)<.001);tests++;
    assert(!kinetic_begin(&one,10,80,1000,1000));assert(kinetic_begin(&one,500,3500,1000,1000));assert(kinetic_advance(&one,2000,1000)&&one.position==1000&&!one.active);assert(kinetic_begin(&one,500,-3500,1000,2000));assert(kinetic_advance(&one,3000,1000)&&!one.position&&!one.active);tests++;
    a->preview=true;a->volume_count=MAX_VOLUMES;a->volume_index=-1;a->volume_id[0]=0;a->volume_scroll=0;
    for(int n=0;n<a->volume_count;n++){FileVolume*v=&a->volumes[n];memset(v,0,sizeof(*v));snprintf(v->id,sizeof(v->id),"volume-%02d",n);copy(v->fs_type,sizeof(v->fs_type),"ext4");v->mountable=v->write_supported=v->read_only=true;}
    screen(a,FILE_VOLUMES);render(a);pointer(a,true,300,1000,40000);pointer(a,true,300,969,40030);assert(a->volume_scroll==31&&a->scroll_dragging);pointer(a,true,300,500,40060);pointer(a,false,300,500,40080);assert(a->volume_scroll==500&&a->kinetic.active&&a->volume_index==-1);tick_inertia(a,40120);assert(a->volume_scroll>500&&a->volume_scroll%VOLUME_ROW_HEIGHT&&a->screen==FILE_VOLUMES);tests++;
    int64_t volume_at=a->volume_scroll;render(a);pointer(a,true,300,500,40130);pointer(a,false,300,500,40160);assert(!a->kinetic.active&&a->volume_scroll==volume_at&&a->volume_index==-1&&a->screen==FILE_VOLUMES);tests++;
    volume_scroll_to(a,INT64_MAX);render(a);assert(a->volume_scroll==volume_max_scroll(a)&&test_press(a,A_FS_VOLUME_ITEM+MAX_VOLUMES-1,50)&&a->screen==FILE_VOLUME&&!strcmp(a->volume_id,"volume-79"));tests++;
    screen(a,FILE_VOLUMES);volume_scroll_to(a,500);render(a);assert(kinetic_begin(&a->kinetic,500,1000,(double)volume_max_scroll(a),41000));a->motion_surface=SCROLL_VOLUMES;
    assert(json_parse(&fj,"{\"volumes\":[{\"id\":\"replacement\",\"fs_type\":\"ext4\",\"mountable\":true}]}",ft,256)>0);parse_file_volumes(a,&fj);assert(!a->kinetic.active&&!a->button_count&&!a->pointer_down&&a->volume_index==-1);pointer(a,true,300,311,41010);pointer(a,false,300,311,41040);assert(a->screen==FILE_VOLUMES&&a->volume_index==-1);tests++;
    render(a);pointer(a,true,300,311,41100);assert(a->pressed_action==A_FS_VOLUME_ITEM);parse_file_volumes(a,&fj);render(a);pointer(a,true,300,311,41130);pointer(a,false,300,311,41160);assert(!a->pointer_requires_release&&a->screen==FILE_VOLUMES&&a->volume_index==-1);pointer(a,true,300,311,41200);pointer(a,false,300,311,41230);assert(a->screen==FILE_VOLUME&&!strcmp(a->volume_id,"replacement"));tests++;
    FileVolume category={0};copy(category.id,sizeof(category.id),"sdcard-p1");copy(category.fs_type,sizeof(category.fs_type),"ext4");category.mountable=true;assert(volume_kind(&category)==V_SD&&volume_color(&category)==GREEN);copy(category.fs_type,sizeof(category.fs_type),"crypto_LUKS");assert(volume_kind(&category)==V_LOCKED);copy(category.id,sizeof(category.id),"system");copy(category.fs_type,sizeof(category.fs_type),"erofs");assert(volume_kind(&category)==V_IMAGE);copy(category.fs_type,sizeof(category.fs_type),"raw");assert(volume_kind(&category)==V_RAW);tests++;
    sample_document(a,200);a->fs_dirty=false;screen(a,FILE_EDIT);render(a);pointer(a,true,250,750,45000);pointer(a,true,250,400,45060);pointer(a,false,250,400,45100);assert(a->fs_scroll==350&&a->kinetic.active&&!a->fs_cursor&&!a->fs_dirty);tick_inertia(a,45140);size_t text_at=a->fs_scroll;assert(text_at>350&&text_at%TEXT_ROW_HEIGHT);render(a);assert(a->fs_scroll==text_at&&!a->fs_cursor&&!a->fs_dirty);tests++;
    pointer(a,true,100,400,45150);pointer(a,false,100,400,45180);assert(!a->kinetic.active&&a->fs_scroll==text_at&&!a->fs_cursor);render(a);pointer(a,true,100,400,45200);pointer(a,false,100,400,45230);
    unsigned int expected_row=(unsigned int)((text_at+400-246)/TEXT_ROW_HEIGHT);char needle[32];snprintf(needle,sizeof(needle),"line-%03u",expected_row);const char*row_begin=strstr(a->fs_text,needle);assert(row_begin&&a->fs_cursor>=(size_t)(row_begin-a->fs_text)&&a->fs_cursor<(size_t)(strchr(row_begin,'\n')-a->fs_text)&&((unsigned char)a->fs_text[a->fs_cursor]&0xc0)!=0x80&&!a->fs_dirty);tests++;
    size_t at_cursor=a->fs_cursor,original_length=a->fs_length;char*original=strdup(a->fs_text);assert(original);text_scroll_to(a,false,4000);assert(kinetic_begin(&a->kinetic,(double)a->fs_scroll,1000,(double)text_max_scroll(a,false,text_box(a)),45300));a->motion_surface=SCROLL_TEXT;a->kb_uk=true;render(a);letter=-1;for(int n=0;n<a->keyboard_count;n++)if(a->keyboard_keys[n]==0x491)letter=A_KB_KEY+n;
    assert(letter>=0&&test_press(a,letter,50)&&!a->kinetic.active&&a->fs_cursor==at_cursor+2&&a->fs_length==original_length+2&&a->fs_dirty);assert(!strncmp(original,a->fs_text,at_cursor)&&!memcmp(a->fs_text+at_cursor,"ґ",2)&&!strcmp(original+at_cursor,a->fs_text+at_cursor+2));free(original);render(a);size_t caret_top=(size_t)text_cursor_row(a,a->fs_cursor)*TEXT_ROW_HEIGHT;assert(caret_top>=a->fs_scroll&&caret_top+TEXT_ROW_HEIGHT<=a->fs_scroll+558);tests++;
    sample_document(a,150);a->fs_dirty=false;screen(a,FILE_EDIT);render(a);pointer(a,true,250,750,46000);pointer(a,true,250,450,46060);pointer(a,true,250,450,46300);pointer(a,false,250,450,46350);assert(a->fs_scroll==300&&!a->kinetic.active&&!a->fs_cursor&&!a->fs_dirty);tests++;
    memset(a->fs_text,'\n',TEXT_LIMIT);a->fs_text[TEXT_LIMIT]=0;a->fs_length=TEXT_LIMIT;a->fs_cursor=0;a->fs_caret_follow=false;text_scroll_to(a,false,INT64_MAX);render(a);assert(a->text_row_count==TEXT_LIMIT+1&&(int64_t)a->fs_scroll==text_max_scroll(a,false,text_box(a))&&!a->fs_cursor);tests++;
    a->clip_active=false;rectangle(a,(Rect){0,0,CW,CH},BG);a->clip_active=true;a->clip=(Rect){40,40,20,20};vector_line(a,35,35,80,80,2,TEXT);a->clip_active=false;
    uint32_t*native=malloc((size_t)1080*2408*sizeof(*native)),*logical=malloc((size_t)CW*CH*sizeof(*logical));assert(native&&logical);assert(compose_native(a,native,1080,2408,0,4,1080,2400)&&compose_native(a,logical,CW,CH,0,0,CW,CH));unsigned int partial=0,different=0;
    for(int yy=0;yy<2408;yy++)for(int xx=0;xx<1080;xx++){uint32_t color=native[(size_t)yy*1080+xx];if(xx<60||xx>=90||yy<64||yy>=94)assert(color==BG);else{if(color!=BG&&color!=TEXT)partial++;if(color!=logical[((yy-4)*CH/2400)*CW+xx*CW/1080])different++;}}
    assert(partial&&different);free(native);free(logical);tests++;
    /* A status read must not grey the page, and a tap during it must not be lost. */
    a->preview=false;screen(a,HOME);a->job.pid=123456;a->job.kind=J_TOOLS_STATUS;render(a);assert(test_button(a,A_TERMINAL)&&test_button(a,A_TERMINAL)->enabled&&test_button(a,A_FILES)->enabled);
    action(a,A_TERMINAL);assert(a->queued_action==A_TERMINAL&&a->screen==HOME);a->queued_action=0;
    a->job.kind=J_BACKUP;render(a);assert(!test_button(a,A_TERMINAL)->enabled);a->job.pid=0;a->job.kind=J_NONE;a->preview=true;tests++;
    for(int n=1;n<MESSAGE_COUNT;n++)assert(strcmp(message_ids[n-1],message_ids[n])<0);
    for(unsigned int n=0;n<THEME_COUNT;n++){
        /* Every theme must keep every home action reachable and render without a backdrop gap. */
        theme_apply(a,n);a->preview=true;screen(a,HOME);render(a);for(unsigned int k=0;k<7;k++){static const int actions[]={A_OS,A_BACKUP,A_FILES,A_RESTORE,A_VERIFY,A_STORAGE,A_POWER};assert(test_button(a,actions[k]));}assert(test_button(a,A_TOOLS));screen(a,THEME);render(a);assert(test_button(a,A_THEME_ITEM+(int)n));screen(a,TOOLS);render(a);assert(test_button(a,A_THEME));
    }
    theme_apply(a,0);tests++;
    ui_language=1;assert(strcmp(tr("Settings"),"Settings")&&!strcmp(tr("no such text"),"no such text"));raw_text(true);assert(!strcmp(tr("Settings"),"Settings"));raw_text(false);ui_language=0;assert(!strcmp(tr("Settings"),"Settings"));tests++;
    tests+=test_linux_plan_guards();tests+=test_settings_reset();tests+=test_screenshot_keys();tests+=test_android_log();tests+=test_torch();tests+=test_battery();tests+=test_reset_request();tests+=test_zip_queue();free(a->pixels);free(a);printf("{\"ok\":true,\"ui_self_tests\":%d,\"device_io\":false}\n",tests);return 0;
}
static bool await_job(App*a){
    uint64_t deadline=milliseconds()+60000;
    while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec t={0,1000000};nanosleep(&t,NULL);}
    return !a->job.pid;
}
static bool workflow_settle(App*a,unsigned int interval){
    unsigned int ticks=0;uint64_t now=a->kinetic.last_time;
    while(a->kinetic.active&&ticks<200){now+=interval;tick_inertia(a,now);if(!await_job(a)||!a->successful)return false;render(a);ticks++;}
    return ticks>0&&!a->kinetic.active&&ticks<200;
}
static void workflow_swipe(App*a,int x,int start_y,int end_y,uint64_t now){
    render(a);pointer(a,true,x,start_y,now);pointer(a,true,x,start_y+(end_y-start_y)/2,now+30);pointer(a,true,x,end_y,now+60);pointer(a,false,x,end_y,now+80);
}
static bool workflow_volumes(App*a){
#define VOLUME_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[volumes-workflow] failed at line %d: %s; screen=%d status=%s\n",__LINE__,#expression,a->screen,a->status);return false;}}while(0)
    VOLUME_CHECK(!a->device&&a->fixture[0]);a->fs_inventory_destination=FILE_VOLUMES;
    VOLUME_CHECK(start_files_job(a,J_FS_INVENTORY)&&await_job(a)&&a->successful&&a->volume_count==80&&a->volume_index==-1);
    uint64_t at=milliseconds();workflow_swipe(a,250,1050,550,at);VOLUME_CHECK(a->volume_scroll==500&&a->kinetic.active&&workflow_settle(a,16)&&a->volume_scroll>500&&a->screen==FILE_VOLUMES&&a->volume_index==-1);
    at=milliseconds();workflow_swipe(a,250,1050,550,at);tick_inertia(a,at+120);render(a);int64_t stopped=a->volume_scroll;
    pointer(a,true,250,650,at+130);pointer(a,false,250,650,at+160);VOLUME_CHECK(!a->kinetic.active&&a->volume_scroll==stopped&&a->screen==FILE_VOLUMES&&a->volume_index==-1);
    volume_scroll_to(a,volume_max_scroll(a)-100);at=milliseconds();workflow_swipe(a,250,1050,550,at);
    VOLUME_CHECK(a->volume_scroll==volume_max_scroll(a)&&!a->kinetic.active&&!strcmp(a->volumes[79].id,"zzzz-last"));
    VOLUME_CHECK(test_press(a,A_FS_VOLUME_ITEM+79,50)&&a->screen==FILE_VOLUME&&!strcmp(a->volume_id,"zzzz-last")&&!a->job.pid);
#undef VOLUME_CHECK
    return true;
}
static bool workflow_type(App*a,const char*value,bool replace){
    if(a->screen!=FILE_EDIT&&a->screen!=FILE_PROMPT)return false;
    if(replace){size_t*cursor=NULL,*scroll=NULL,capacity=0;char*s=editable_text(a,&cursor,&scroll,&capacity);(void)capacity;*cursor=strlen(s);while(*cursor)erase_character(a);*scroll=0;}
    while(*value)insert_character(a,utf8_next(&value));
    return true;
}
static int workflow_find_entry(App*a,const char*name){
    browser_scroll_to(a,0);if(!await_job(a)||a->screen!=FILE_BROWSER)return -1;
    for(;;){
        for(int n=0;n<a->file_count;n++)if(!strcmp(a->files[n].name,name)){
            browser_scroll_to(a,((int64_t)a->file_offset+n)*FILE_ROW_HEIGHT);if(!await_job(a)||a->screen!=FILE_BROWSER)return -1;
            render(a);for(int k=0;k<a->file_count;k++)if(!strcmp(a->files[k].name,name)&&test_button(a,A_FS_ENTRY_MENU+k))return k;
            return -1;
        }
        if((uint64_t)a->file_offset+(unsigned int)a->file_count>=a->file_total)return -1;
        unsigned int previous=a->file_offset;browser_scroll_to(a,((int64_t)previous+FILE_WINDOW_STEP+16)*FILE_ROW_HEIGHT);
        if(!await_job(a)||a->screen!=FILE_BROWSER||a->file_offset<=previous)return -1;
    }
}
static bool workflow_files(App*a,const char*what){
#define FS_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[files-workflow] failed at line %d: %s; screen=%d status=%s message=%s\n",__LINE__,#expression,a->screen,a->status,a->message);return false;}}while(0)
    FS_CHECK(!a->device&&a->fixture[0]);
    a->fs_inventory_destination=FILE_VOLUMES;FS_CHECK(start_files_job(a,J_FS_INVENTORY)&&await_job(a)&&a->successful);
    int userdata=-1;for(int n=0;n<a->volume_count;n++)if(!strcmp(a->volumes[n].id,"userdata"))userdata=n;FS_CHECK(userdata>=0);
    FS_CHECK(test_press(a,A_FS_VOLUME_ITEM+userdata,50));FS_CHECK(a->screen==FILE_VOLUME);
    FS_CHECK(test_press(a,A_FS_MOUNT_RO,50)&&await_job(a)&&a->successful&&a->screen==FILE_BROWSER);
    FS_CHECK(!files_writable(a)&&!test_press(a,A_FS_NEW_FILE,50)&&!test_press(a,A_FS_MKDIR,50));
    if(!strcmp(what,"files-fling-60")||!strcmp(what,"files-fling-90")){
        unsigned int interval=!strcmp(what,"files-fling-90")?11:16;a->display_hz=interval==11?90:60;
        FS_CHECK(a->file_total==200&&a->file_count==FILE_BATCH);browser_scroll_to(a,42*FILE_ROW_HEIGHT);FS_CHECK(await_job(a)&&a->file_offset==0);
        uint64_t at=milliseconds();workflow_swipe(a,250,1050,550,at);int64_t released=a->browser_scroll;
        FS_CHECK(a->kinetic.active&&workflow_settle(a,interval)&&a->browser_scroll>released&&a->file_offset>=32&&a->file_count<=FILE_BATCH&&a->screen==FILE_BROWSER&&!a->fs_file[0]&&!a->fs_selected.path[0]);
        at=milliseconds();workflow_swipe(a,250,550,1050,at);released=a->browser_scroll;FS_CHECK(a->kinetic.active&&workflow_settle(a,interval)&&a->browser_scroll<released&&a->file_offset==0&&!a->fs_file[0]&&!a->fs_selected.path[0]);
        at=milliseconds();workflow_swipe(a,250,1050,550,at);tick_inertia(a,at+120);FS_CHECK(await_job(a));render(a);int64_t stopped=a->browser_scroll;
        pointer(a,true,250,650,at+130);pointer(a,false,250,650,at+160);FS_CHECK(!a->kinetic.active&&a->browser_scroll==stopped&&a->screen==FILE_BROWSER&&!a->fs_file[0]&&!a->fs_selected.path[0]);
        render(a);int first=(int)(a->browser_scroll/FILE_ROW_HEIGHT)-((int)a->file_offset)+1;FS_CHECK(first>=0&&first<a->file_count);char path[PATH_LEN];copy(path,sizeof(path),a->files[first].path);
        FS_CHECK(test_press(a,A_FS_ENTRY+first,50)&&await_job(a)&&a->successful&&a->screen==FILE_TEXT&&!strcmp(a->fs_file,path));return true;
    }
    if(!strcmp(what,"files-scroll")){
        FS_CHECK(a->file_total==200&&a->file_count==FILE_BATCH&&!a->browser_scroll);render(a);
        uint64_t at=milliseconds();pointer(a,true,250,1050,at);pointer(a,true,250,1029,at+30);FS_CHECK(a->browser_scroll==21&&a->scroll_dragging&&!a->pressed_action);
        pointer(a,true,250,550,at+80);pointer(a,false,250,550,at+100);FS_CHECK(a->browser_scroll==500&&a->screen==FILE_BROWSER&&!a->fs_file[0]&&!a->fs_selected.path[0]);
        render(a);pointer(a,true,250,550,at+200);pointer(a,true,250,1050,at+250);pointer(a,false,250,1050,at+300);FS_CHECK(!a->browser_scroll&&a->screen==FILE_BROWSER&&!a->fs_file[0]);
        render(a);pointer(a,true,250,470,at+400);pointer(a,true,250,475,at+450);pointer(a,false,250,475,at+480);
        FS_CHECK(await_job(a)&&a->successful&&a->screen==FILE_TEXT&&!strcmp(a->fs_file,"/entry-000.txt")&&!strcmp(a->fs_text,"entry 0\n"));
        FS_CHECK(test_press(a,A_BACK,50)&&await_job(a)&&a->screen==FILE_BROWSER);a->fs_file[0]=a->fs_selected.path[0]=0;
        int iterations=0;while(a->browser_scroll<browser_max_scroll(a)){
            int64_t previous=a->browser_scroll;render(a);at=milliseconds();pointer(a,true,250,1050,at);pointer(a,true,250,550,at+60);pointer(a,false,250,550,at+80);
            FS_CHECK(await_job(a)&&a->successful&&a->screen==FILE_BROWSER&&a->browser_scroll>previous&&a->file_count<=FILE_BATCH&&!a->fs_file[0]&&!a->fs_selected.path[0]&&++iterations<60);
        }
        int last=-1;for(int n=0;n<a->file_count;n++)if(!strcmp(a->files[n].name,"entry-199.txt"))last=n;
        FS_CHECK(last>=0&&a->file_offset>0&&test_press(a,A_FS_ENTRY+last,50)&&await_job(a)&&a->screen==FILE_TEXT&&!strcmp(a->fs_file,"/entry-199.txt")&&!strcmp(a->fs_text,"entry 199\n"));
        FS_CHECK(test_press(a,A_BACK,50)&&await_job(a)&&a->screen==FILE_BROWSER);render(a);at=milliseconds();pointer(a,true,250,550,at);pointer(a,true,250,1050,at+60);pointer(a,false,250,1050,at+80);
        FS_CHECK(await_job(a)&&a->screen==FILE_BROWSER&&a->browser_scroll==browser_max_scroll(a)-500&&!test_press(a,A_FS_NEW_FILE,50));return true;
    }
    FS_CHECK(test_press(a,A_FS_VOLUME,50)&&test_press(a,A_FS_MOUNT_RW,50)&&a->screen==FILE_CONFIRM);
    FS_CHECK(test_press(a,A_FS_CONFIRM,1999)&&!a->job.pid&&a->screen==FILE_CONFIRM);
    render(a);Button*confirm=test_button(a,A_FS_CONFIRM);FS_CHECK(confirm);int x=confirm->r.x+100,y=confirm->r.y+40;uint64_t now=milliseconds();
    pointer(a,true,x,y,now);pointer(a,true,4,4,now+2100);pointer(a,false,x,y,now+2200);FS_CHECK(!a->job.pid&&a->screen==FILE_CONFIRM);
    FS_CHECK(test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful&&a->screen==FILE_BROWSER&&files_writable(a));
    if(!strcmp(what,"files-editor-motion")){
        int entry=workflow_find_entry(a,"long-scroll.txt");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY+entry,50)&&await_job(a)&&a->screen==FILE_TEXT&&a->fs_length>8000&&!a->fs_truncated);
        size_t initial_length=a->fs_length;char*original=strdup(a->fs_text);FS_CHECK(original);uint64_t at=milliseconds();workflow_swipe(a,250,1050,550,at);
        FS_CHECK(a->kinetic.active&&workflow_settle(a,11)&&a->fs_scroll>500&&!a->fs_cursor&&!a->fs_dirty);
        FS_CHECK(test_press(a,A_FS_EDIT,50)&&a->screen==FILE_EDIT);size_t viewer_offset=a->fs_scroll;render(a);FS_CHECK(a->fs_scroll==viewer_offset);
        at=milliseconds();workflow_swipe(a,250,750,350,at);tick_inertia(a,at+120);size_t stopped=a->fs_scroll;render(a);pointer(a,true,120,450,at+130);pointer(a,false,120,450,at+160);FS_CHECK(!a->kinetic.active&&a->fs_scroll==stopped&&!a->fs_cursor);
        render(a);pointer(a,true,120,450,at+180);pointer(a,false,120,450,at+210);FS_CHECK(a->fs_cursor>0&&a->fs_cursor<a->fs_length&&((unsigned char)a->fs_text[a->fs_cursor]&0xc0)!=0x80);size_t cursor=a->fs_cursor;
        at=milliseconds();workflow_swipe(a,250,750,500,at);FS_CHECK(a->kinetic.active);a->kb_uk=true;render(a);int letter=-1;for(int n=0;n<a->keyboard_count;n++)if(a->keyboard_keys[n]==0x491)letter=A_KB_KEY+n;
        FS_CHECK(letter>=0&&test_press(a,letter,50)&&!a->kinetic.active&&a->fs_cursor==cursor+2&&a->fs_length==initial_length+2&&a->fs_dirty);
        FS_CHECK(!strncmp(original,a->fs_text,cursor)&&!memcmp(a->fs_text+cursor,"ґ",2)&&!strcmp(original+cursor,a->fs_text+cursor+2));free(original);
        FS_CHECK(test_press(a,A_FS_SAVE,50)&&test_press(a,A_FS_CONFIRM,1999)&&!a->job.pid&&a->screen==FILE_CONFIRM);
        FS_CHECK(test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful&&a->screen==FILE_TEXT&&a->fs_length==initial_length+2&&!a->fs_dirty&&valid_sha256(a->fs_sha256));
        text_scroll_to(a,false,519);return true;
    }
    if(!strcmp(what,"files-large-read")){
        int entry=workflow_find_entry(a,"large.txt");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY+entry,50)&&await_job(a));
        FS_CHECK(a->successful&&a->screen==FILE_TEXT&&a->fs_truncated&&a->fs_length<=TEXT_LIMIT&&!a->fs_sha256[0]);
        FS_CHECK(!test_press(a,A_FS_EDIT,50)&&a->screen==FILE_TEXT);return true;
    }
    if(!strcmp(what,"files-stale-write")){
        int entry=workflow_find_entry(a,"stale.txt");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY+entry,50)&&await_job(a)&&a->screen==FILE_TEXT);
        FS_CHECK(test_press(a,A_FS_EDIT,50)&&workflow_type(a,"Changed in UI\n",true));memset(a->fs_sha256,'0',64);a->fs_sha256[64]=0;
        FS_CHECK(test_press(a,A_FS_SAVE,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a));
        FS_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==FILE_EDIT&&!strcmp(a->status,"FILE_CHANGED"));return true;
    }
    if(!strcmp(what,"files-metadata")||!strcmp(what,"files-stale-metadata")){
        int entry=workflow_find_entry(a,"stale.txt");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&a->screen==FILE_INFO);
        FS_CHECK(a->fs_selected.metadata_supported&&valid_sha256(a->fs_selected.token));
        if(!strcmp(what,"files-stale-metadata")){memset(a->fs_selected.token,'0',64);a->fs_selected.token[64]=0;}
        FS_CHECK(test_press(a,A_FS_CHMOD,50)&&workflow_type(a,"0640",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&a->screen==FILE_CONFIRM);
        FS_CHECK(test_press(a,A_FS_CONFIRM,1999)&&!a->job.pid&&a->screen==FILE_CONFIRM);
        FS_CHECK(test_press(a,A_FS_CONFIRM,2000)&&await_job(a));
        if(!strcmp(what,"files-stale-metadata")){FS_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==FILE_PROMPT&&!strcmp(a->status,"ENTRY_CHANGED"));return true;}
        FS_CHECK(a->successful&&a->screen==FILE_INFO&&!strcmp(a->fs_selected.mode,"0640"));
        FS_CHECK(test_press(a,A_FS_CHOWN,50)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful&&a->screen==FILE_INFO);
        FS_CHECK(!strcmp(a->fs_selected.mode,"0640"));
        FS_CHECK(test_press(a,A_BACK,50)&&await_job(a));entry=workflow_find_entry(a,"hard.txt");
        FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&a->screen==FILE_INFO);
        FS_CHECK(!a->fs_selected.metadata_supported&&!test_press(a,A_FS_CHMOD,50)&&!test_press(a,A_FS_CHOWN,50));return true;
    }
    if(!strcmp(what,"files-link")||!strcmp(what,"files-link-escape")){
        int entry=workflow_find_entry(a,!strcmp(what,"files-link")?"symlink":"escape-link");
        FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&a->screen==FILE_INFO);
        FS_CHECK(a->fs_selected.link_target[0]&&!test_press(a,A_FS_CHMOD,50)&&!test_press(a,A_FS_FOLLOW,50));
        FS_CHECK(test_press(a,A_FS_RESOLVE,50)&&await_job(a));
        if(!strcmp(what,"files-link-escape")){FS_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==FILE_INFO&&!a->fs_link_resolved);return true;}
        FS_CHECK(a->successful&&a->screen==FILE_INFO&&a->fs_link_resolved&&!strcmp(a->fs_resolved.path,"/stale.txt"));
        FS_CHECK(test_press(a,A_FS_FOLLOW,50)&&await_job(a)&&a->successful&&a->screen==FILE_TEXT&&!strcmp(a->fs_file,"/stale.txt"));return true;
    }
    if(!strcmp(what,"files-copy-tree")||!strcmp(what,"files-stale-tree")){
        FS_CHECK(a->fs_copy_directories);int entry=workflow_find_entry(a,"tree");
        FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful);
        if(!strcmp(what,"files-stale-tree")){memset(a->fs_selected.token,'0',64);a->fs_selected.token[64]=0;}
        FS_CHECK(test_press(a,A_FS_COPY,50)&&a->fs_prompt_kind==J_FS_COPY_TREE&&test_press(a,A_FS_DEST_NEXT,50));
        FS_CHECK(a->fs_destination_index>=0&&!strcmp(a->volumes[a->fs_destination_index].id,"sdcard-p1"));
        FS_CHECK(workflow_type(a,"/copied-tree",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a));
        if(!strcmp(what,"files-stale-tree")){FS_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==FILE_PROMPT&&!strcmp(a->status,"ENTRY_CHANGED"));return true;}
        FS_CHECK(a->successful&&a->screen==FILE_BROWSER);return true;
    }
    FS_CHECK(!strcmp(what,"files-roundtrip"));
    FS_CHECK(browser_max_scroll(a)>0&&a->file_count<=FILE_BATCH);FS_CHECK(test_press(a,A_FS_NEXT,50)&&await_job(a)&&a->browser_scroll>0);FS_CHECK(test_press(a,A_FS_PREV,50)&&await_job(a)&&!a->browser_scroll);
    FS_CHECK(test_press(a,A_FS_MKDIR,50)&&workflow_type(a,"/UI-файли",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful);
    /* A failed mkdir must return to its prompt, never a selection from another volume. */
    FS_CHECK(test_press(a,A_FS_MKDIR,50)&&workflow_type(a,"/UI-файли",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a));
    FS_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==FILE_PROMPT);FS_CHECK(test_press(a,A_DONE,50)&&a->screen==FILE_PROMPT&&test_press(a,A_BACK,50)&&a->screen==FILE_BROWSER);
    int entry=workflow_find_entry(a,"UI-файли");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY+entry,50)&&await_job(a)&&!a->file_count);
    const char*name="нотатки ; $(printf nope).txt";
    FS_CHECK(test_press(a,A_FS_NEW_FILE,50)&&workflow_type(a,"/UI-файли/нотатки ; $(printf nope).txt",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&a->screen==FILE_EDIT);
    FS_CHECK(workflow_type(a,"Перевірка UI\nvalue=1\nliteral=$HOME; `uname`\n",true));
    FS_CHECK(test_press(a,A_FS_SAVE,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful&&a->screen==FILE_TEXT&&valid_sha256(a->fs_sha256));
    FS_CHECK(test_press(a,A_FS_EDIT,50));a->fs_cursor=a->fs_length;FS_CHECK(workflow_type(a,"дописано=так\n",false));
    FS_CHECK(test_press(a,A_FS_SAVE,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful&&strstr(a->fs_text,"дописано=так\n"));
    FS_CHECK(test_press(a,A_BACK,50)&&await_job(a));entry=workflow_find_entry(a,name);FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful);
    FS_CHECK(test_press(a,A_FS_COPY,50)&&test_press(a,A_FS_DEST_NEXT,50));FS_CHECK(a->fs_destination_index>=0&&!strcmp(a->volumes[a->fs_destination_index].id,"sdcard-p1"));
    FS_CHECK(workflow_type(a,"/скопійовано.txt",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful);
    entry=workflow_find_entry(a,name);FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&test_press(a,A_FS_RENAME,50));
    FS_CHECK(workflow_type(a,"/UI-файли/перейменовано.txt",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&test_press(a,A_FS_CONFIRM,2000)&&await_job(a)&&a->successful);
    entry=workflow_find_entry(a,"перейменовано.txt");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&test_press(a,A_FS_DELETE,50));
    FS_CHECK(test_press(a,A_FS_CONFIRM,2999)&&!a->job.pid&&a->screen==FILE_CONFIRM);FS_CHECK(test_press(a,A_FS_CONFIRM,3000)&&await_job(a)&&a->successful&&!a->file_count);
    FS_CHECK(test_press(a,A_FS_UP,50)&&await_job(a));entry=workflow_find_entry(a,"UI-файли");FS_CHECK(entry>=0&&test_press(a,A_FS_ENTRY_MENU+entry,50)&&await_job(a)&&a->successful&&test_press(a,A_FS_DELETE,50)&&test_press(a,A_FS_CONFIRM,3000)&&await_job(a)&&a->successful);
    FS_CHECK(test_press(a,A_FS_VOLUME,50)&&test_press(a,A_FS_MOUNT_RO,50)&&await_job(a)&&a->successful&&!files_writable(a));
    FS_CHECK(test_press(a,A_FS_VOLUME,50)&&test_press(a,A_FS_UNMOUNT,50)&&await_job(a)&&a->successful&&current_volume(a)&&!current_volume(a)->mounted);
#undef FS_CHECK
    return true;
}
static bool workflow_tools(App*a,const char*what){
#define TOOLS_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[tools-workflow] failed at line %d: %s; screen=%d status=%s message=%s\n",__LINE__,#expression,a->screen,a->status,a->message);return false;}}while(0)
    TOOLS_CHECK(!a->device&&a->fixture[0]);screen(a,HOME);
    TOOLS_CHECK(test_press(a,A_TOOLS,50)&&await_job(a)&&a->successful&&a->screen==TOOLS&&a->tools_known);
    TOOLS_CHECK(a->ssh_available&&a->ssh_keys==1&&a->ssh_ephemeral&&a->ssh_fingerprint[0]);
    if(!strcmp(what,"tools-status"))return true;
    screen(a,HOME);TOOLS_CHECK(test_press(a,A_TOOLS_EXPORT,50)&&await_job(a)&&a->successful&&a->screen==TOOLS_EXPORT);
    if(!strcmp(what,"tools-no-volume")){TOOLS_CHECK(a->tools_volume_index<0&&!test_press(a,A_TOOLS_EXPORT_CONFIRM,2000));return true;}
    TOOLS_CHECK(a->tools_volume_index>=0&&!strcmp(a->tools_volume_id,"sdcard-p1")&&valid_sha256(a->tools_volume_token));
    TOOLS_CHECK(test_press(a,A_TOOLS_EXPORT_CONFIRM,1999)&&!a->job.pid&&a->screen==TOOLS_EXPORT);
    if(!strcmp(what,"tools-stale-export")){memset(a->tools_volume_token,'0',64);a->tools_volume_token[64]=0;copy(a->volumes[a->tools_volume_index].token,sizeof(a->volumes[0].token),a->tools_volume_token);}
    TOOLS_CHECK(test_press(a,A_TOOLS_EXPORT_CONFIRM,2000)&&await_job(a)&&a->screen==RESULT&&a->result_return==HOME);
    if(!strcmp(what,"tools-stale-export")){TOOLS_CHECK(!a->successful&&!strcmp(a->status,"VOLUME_CHANGED"));return true;}
    TOOLS_CHECK(a->successful&&strstr(a->message,"SHA-256:")&&strstr(a->message,".tar.gz"));
#undef TOOLS_CHECK
    return true;
}
static bool workflow_display(App*a,const char*what){
#define DISPLAY_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[display-workflow] failed at line %d: %s; screen=%d status=%s\n",__LINE__,#expression,a->screen,a->status);return false;}}while(0)
    DISPLAY_CHECK(!a->device&&a->fixture[0]);screen(a,TOOLS);
    DISPLAY_CHECK(test_press(a,A_DISPLAY,50)&&await_job(a)&&a->successful&&a->screen==DISPLAY_SETTINGS&&a->display_known);
    if(!strcmp(what,"display-no-service")){DISPLAY_CHECK(!a->display_service&&!a->display_supported&&!a->display_hz&&!test_press(a,A_DISPLAY_60,50)&&!test_press(a,A_DISPLAY_90,50));return true;}
    DISPLAY_CHECK(a->display_hz==60&&a->display_actual_hz>59.99&&!test_press(a,A_DISPLAY_60,50));
    if(!strcmp(what,"display-unsupported")){DISPLAY_CHECK(!a->display_90&&!test_press(a,A_DISPLAY_90,50));return true;}
    DISPLAY_CHECK(test_press(a,A_DISPLAY_90,50)&&await_job(a));
    if(!strcmp(what,"display-not-applied")){DISPLAY_CHECK(!a->successful&&a->screen==RESULT&&!strcmp(a->status,"DISPLAY_MODE_NOT_APPLIED")&&a->display_hz==60);return true;}
    if(!strcmp(what,"display-set-error")){
        DISPLAY_CHECK(!a->successful&&a->screen==RESULT&&!a->display_known&&!a->display_hz&&!strcmp(a->status,"FIXTURE_SET_FAILED"));
        DISPLAY_CHECK(test_press(a,A_DONE,50)&&await_job(a)&&a->successful&&a->screen==DISPLAY_SETTINGS&&a->display_hz==60);return true;
    }
    DISPLAY_CHECK(!strcmp(what,"display-roundtrip")&&a->successful&&a->screen==DISPLAY_SETTINGS&&a->display_hz==90&&a->display_actual_hz>89.99&&!test_press(a,A_DISPLAY_90,50));
    DISPLAY_CHECK(test_press(a,A_DISPLAY_60,50)&&await_job(a)&&a->successful&&a->screen==DISPLAY_SETTINGS&&a->display_hz==60&&a->display_actual_hz>59.99);
#undef DISPLAY_CHECK
    return true;
}
static bool workflow_linux(App*a,const char*what){
#define LINUX_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[linux-workflow] failed at line %d: %s; screen=%d status=%s blockers=%s\n",__LINE__,#expression,a->screen,a->status,a->blockers);return false;}}while(0)
    LINUX_CHECK(!a->device&&a->fixture[0]);LINUX_CHECK(start_job(a,J_INVENTORY)&&await_job(a)&&a->inventory_ok&&a->linux_backup_available);
    bool backup=!strcmp(what,"linux-backup")||!strcmp(what,"linux-prep-stale");
    if(backup){action(a,A_BACKUP);LINUX_CHECK(test_press(a,A_REVIEW_BACKUP,50)&&await_job(a)&&a->screen==PREPARE_FILESYSTEM&&a->prepare_known&&a->prepare_mounted&&a->prepare_owned);
        LINUX_CHECK(test_press(a,A_PREPARE_CONTINUE,999)&&!a->job.pid&&a->screen==PREPARE_FILESYSTEM);
        if(!strcmp(what,"linux-prep-stale")){memset(a->prepare_token,'0',64);a->prepare_token[64]=0;}
        LINUX_CHECK(test_press(a,A_PREPARE_CONTINUE,1000)&&await_job(a));
        if(!strcmp(what,"linux-prep-stale")){LINUX_CHECK(!a->successful&&a->screen==RESULT&&a->result_return==PREPARE_FILESYSTEM&&!strcmp(a->status,"VOLUME_CHANGED"));return true;}
    }else{
        LINUX_CHECK(start_job(a,J_LIST)&&await_job(a)&&a->backup_count>0);LINUX_CHECK(test_press(a,A_BACKUP_ITEM,50)&&backup_is_linux_v2(&a->backups[a->selected_backup]));
        LINUX_CHECK(test_press(a,!strcmp(what,"linux-wipe")?A_REVIEW_WIPE:A_REVIEW_RESTORE,50)&&await_job(a)&&a->screen==PREPARE_FILESYSTEM&&a->prepare_known&&!a->prepare_mounted);
        LINUX_CHECK(test_press(a,A_PREPARE_CONTINUE,50)&&await_job(a));
    }
    LINUX_CHECK(a->screen==PLAN&&a->plan_linux_v2&&a->plan_can_execute&&a->plan_linux_admitted&&a->plan_estimate_known&&a->plan_archive_bytes>0);
    int hold=backup?2000:3000;LINUX_CHECK(test_press(a,A_CONFIRM,(unsigned int)hold-1)&&!a->job.pid&&a->screen==PLAN);
    LINUX_CHECK(test_press(a,A_CONFIRM,(unsigned int)hold)&&await_job(a)&&a->successful&&a->screen==RESULT&&!a->plan_can_execute&&!a->token[0]);
#undef LINUX_CHECK
    return true;
}
static bool workflow_usb(App*a,const char*what){
#define USB_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[usb-workflow] failed at line %d: %s; screen=%d status=%s state=%s\n",__LINE__,#expression,a->screen,a->status,a->usb_state);return false;}}while(0)
    USB_CHECK(!a->device&&a->fixture[0]);screen(a,HOME);
    USB_CHECK(test_press(a,A_USB_FILES,50)&&await_job(a)&&a->successful&&a->screen==USB_FILES&&a->usb_known);
    if(!strcmp(what,"usb-unavailable")){USB_CHECK(!a->usb_available&&!test_press(a,A_USB_START,3000));return true;}
    USB_CHECK(a->usb_available&&!strcmp(a->usb_state,"stopped")&&a->usb_candidate_count>0);
    USB_CHECK(test_press(a,A_USB_START,1999)&&!a->job.pid&&a->screen==USB_FILES);
    if(!strcmp(what,"usb-stale-start")){memset(a->usb_candidates[a->usb_selected].mount_token,'0',64);a->usb_candidates[a->usb_selected].mount_token[64]=0;}
    USB_CHECK(test_press(a,A_USB_START,2000)&&await_job(a));
    if(!strcmp(what,"usb-not-applied")){USB_CHECK(!a->successful&&a->screen==RESULT&&!strcmp(a->status,"USB_STATE_NOT_CONFIRMED"));return true;}
    if(!strcmp(what,"usb-stale-start")){USB_CHECK(!a->successful&&a->screen==RESULT&&!strcmp(a->status,"MOUNT_CHANGED"));USB_CHECK(test_press(a,A_DONE,50)&&await_job(a)&&a->successful&&!usb_session_open(a));return true;}
    USB_CHECK(a->successful&&a->screen==USB_FILES&&a->usb_export_valid&&!a->usb_export_writable&&usb_session_open(a));
    if(!strcmp(what,"usb-stale-stop")){
        memset(a->usb_session,'0',64);a->usb_session[64]=0;USB_CHECK(test_press(a,A_USB_STOP,50)&&await_job(a)&&!a->successful&&!strcmp(a->status,"SESSION_CHANGED"));
        USB_CHECK(test_press(a,A_DONE,50)&&await_job(a)&&a->successful&&usb_session_open(a)&&a->usb_export_valid);
    }else{USB_CHECK(!begin_linux_prepare(a,J_BACKUP_PLAN)&&!a->job.pid&&!strcmp(a->status,"USB_EXPORT_ACTIVE"));USB_CHECK(test_press(a,A_DONE,50)&&await_job(a)&&a->successful);}
    USB_CHECK(test_press(a,A_USB_STOP,50)&&await_job(a)&&a->successful&&a->screen==USB_FILES&&!usb_session_open(a)&&!strcmp(a->usb_state,"stopped"));
    if(!strcmp(what,"usb-stale-stop"))return true;
    USB_CHECK(test_press(a,A_USB_RW,50)&&!a->usb_read_only&&test_press(a,A_USB_START,2000)&&await_job(a)&&a->successful&&a->usb_export_valid&&a->usb_export_writable&&usb_session_open(a));
    USB_CHECK(test_press(a,A_USB_STOP,50)&&await_job(a)&&a->successful&&!usb_session_open(a)&&!strcmp(a->usb_state,"stopped"));
#undef USB_CHECK
    return true;
}
static int workflow(App*a,const char*what,const char*render_path){
    if(a->device||!a->fixture[0]){fprintf(stderr,"Headless workflow is restricted to regular-file fixtures.\n");return 2;}
    if(!private_workdir(a->workdir)){fprintf(stderr,"Create a private --workdir first.\n");return 2;}
    if(!strcmp(what,"volumes-motion")){if(!workflow_volumes(a))return 1;}
    else if(!strncmp(what,"files-",6)){if(!workflow_files(a,what))return 1;}
    else if(!strncmp(what,"tools-",6)){if(!workflow_tools(a,what))return 1;}
    else if(!strncmp(what,"display-",8)){if(!workflow_display(a,what))return 1;}
    else if(!strncmp(what,"linux-",6)){if(!workflow_linux(a,what))return 1;}
    else if(!strncmp(what,"usb-",4)){if(!workflow_usb(a,what))return 1;}
    else if(!strcmp(what,"inventory")){if(!start_job(a,J_INVENTORY)||!await_job(a))return 1;screen(a,SYSTEM);}
    else if(!strcmp(what,"backup-linux")||!strcmp(what,"backup-android")){
        copy(a->profile,sizeof(a->profile),!strcmp(what,"backup-linux")?"linux":"android");if(!start_job(a,J_BACKUP_PLAN)||!await_job(a))return 1;
        if(a->screen!=PLAN||!a->plan_can_execute)return 1;
        render(a);pointer(a,true,300,1360,100);pointer(a,false,300,1360,2200);if(!await_job(a))return 1;
    }else if(!strcmp(what,"verify-first")||!strcmp(what,"restore-first")){
        if(!start_job(a,J_LIST)||!await_job(a)||!a->backup_count)return 1;
        a->selected_backup=0;
        if(!strcmp(what,"verify-first")){if(!start_job(a,J_VERIFY)||!await_job(a))return 1;}
        else{if(!start_job(a,J_RESTORE_PLAN)||!await_job(a)||a->screen!=PLAN||!a->plan_can_execute)return 1;render(a);pointer(a,true,300,1360,100);pointer(a,false,300,1360,3200);if(!await_job(a))return 1;}
    }else if(!strcmp(what,"manage-first")){
        /* 2026-11-11: the only backup is renamed and then deleted on its page, as a user does it. */
#define MANAGE_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[manage-workflow] failed at line %d: %s; screen=%d status=%s\n",__LINE__,#expression,a->screen,a->status);return 1;}}while(0)
        MANAGE_CHECK(start_job(a,J_LIST)&&await_job(a)&&a->screen==LIST&&a->backup_count==1);
        char old[sizeof(a->backups[0].name)];copy(old,sizeof(old),a->backups[0].name);
        MANAGE_CHECK(test_press(a,A_BACKUP_ITEM,50)&&a->screen==DETAIL);
        MANAGE_CHECK(test_press(a,A_BACKUP_RENAME,50)&&a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_BACKUP_NAME&&!strcmp(a->fs_prompt,old));
        /* The name it has, a name with a space and a hidden name are not taken. */
        MANAGE_CHECK(!test_press(a,A_FS_PROMPT_NEXT,50));
        MANAGE_CHECK(workflow_type(a,"my copy",true)&&!test_press(a,A_FS_PROMPT_NEXT,50)&&workflow_type(a,".hidden",true)&&!test_press(a,A_FS_PROMPT_NEXT,50));
        MANAGE_CHECK(test_press(a,A_BACK,50)&&a->screen==DETAIL&&!a->job.pid&&!a->fs_prompt[0]);
        MANAGE_CHECK(test_press(a,A_BACKUP_RENAME,50)&&workflow_type(a,"before-root_1.0",true)&&test_press(a,A_FS_PROMPT_NEXT,50)&&await_job(a));
        MANAGE_CHECK(a->successful&&a->screen==LIST&&a->backup_count==1&&!strcmp(a->backups[0].name,"before-root_1.0")&&strstr(a->backups[0].path,"/before-root_1.0"));
        /* A tap does not delete; the hold does, and the list comes back empty. */
        MANAGE_CHECK(test_press(a,A_BACKUP_ITEM,50)&&a->screen==DETAIL&&test_press(a,A_BACKUP_DELETE,600)&&!a->job.pid&&a->screen==DETAIL);
        MANAGE_CHECK(test_press(a,A_BACKUP_DELETE,3200)&&await_job(a)&&a->successful&&a->screen==LIST&&a->backup_count==0);
#undef MANAGE_CHECK
    }else if(!strcmp(what,"card-partition")){
        /* 2026-11-11: a card is partitioned from the Wipe page, as a user does it; sdcard.py on a fixture file. */
#define CARD_CHECK(expression) do{if(!(expression)){fprintf(stderr,"[card-workflow] failed at line %d: %s; screen=%d status=%s\n",__LINE__,#expression,a->screen,a->status);return 1;}}while(0)
        screen(a,WIPE);
        CARD_CHECK(test_press(a,A_CARD,50)&&a->screen==CARD_PAGE&&await_job(a)&&a->card_known&&a->card_count==1&&card_current(a));
        CARD_CHECK(!card_current(a)->busy_code[0]&&card_current(a)->part_count==1&&!strcmp(card_current(a)->parts[0].fs,"ext4")&&!strcmp(card_current(a)->parts[0].label,"old-system"));
        /* What does not fit a card of 3 GiB is no button: 8 GiB of Linux, 4 GiB of swap. */
        render(a);CARD_CHECK(test_button(a,A_CARD_LINUX+1)&&!test_button(a,A_CARD_LINUX+1)->enabled&&!test_button(a,A_CARD_SWAP+3)->enabled&&test_button(a,A_CARD_SWAP+2)->enabled);
        CARD_CHECK(test_press(a,A_CARD_SWAP+1,50)&&a->card_swap==1&&!a->job.pid);
        /* A hold shorter than five seconds writes nothing. */
        CARD_CHECK(test_button(a,A_CARD_APPLY)&&test_button(a,A_CARD_APPLY)->hold_ms==5000&&test_press(a,A_CARD_APPLY,3000)&&!a->job.pid&&a->screen==CARD_PAGE);
        CARD_CHECK(test_press(a,A_CARD_APPLY,5200)&&await_job(a)&&a->successful&&a->screen==RESULT&&a->result_return==CARD_PAGE&&strstr(a->message,"FAT32"));
        /* The result leads back to the page, which reads the card again and shows what was made. */
        CARD_CHECK(test_press(a,A_DONE,50)&&a->screen==CARD_PAGE&&await_job(a)&&a->card_known&&card_current(a)&&card_current(a)->part_count==2);
        CARD_CHECK(!strcmp(card_current(a)->table,"mbr")&&!strcmp(card_current(a)->parts[0].fs,"vfat")&&!strcmp(card_current(a)->parts[1].fs,"swap")&&card_current(a)->parts[1].size==1ULL<<30);
        CARD_CHECK(test_press(a,A_BACK,50)&&a->screen==WIPE);
        a->successful=true;
#undef CARD_CHECK
    }else return 2;
    a->now=milliseconds();render(a);if(render_path&&!write_ppm(a,render_path))return 1;
    printf("{\"ok\":%s,\"workflow\":\"%s\",\"backend_operation\":\"%s\",\"progress_events\":%u,\"fixture_only\":true}\n",a->successful?"true":"false",what,a->last_backend_operation,a->progress_events);return a->successful?0:1;
}
static void sample_system(App*a){
    static const char*const titles[]={"Device","Processor","Battery"};
    static const struct { const char*label,*value,*unit;unsigned char section; } rows[]={{"Name","Galaxy A22 5G","",0},{"Model","SM-A226B","",0},{"Code name","a22x","",0},{"Serial number","R00000000000","",0},{"Region","EUX","",0},
        {"Chip","MT6833V/NZA","",1},{"Performance cores","2 × Cortex-A76, 2.2","GHz",1},{"Efficiency cores","6 × Cortex-A55, 2.0","GHz",1},{"Temperature","31","°C",1},
        {"Level","64","%",2},{"State","Charging","",2},{"Voltage","4.02","V",2},{"Design capacity","5000","mAh",2}};
    static const struct { const char*label,*value,*unit; } summary[]={{"Processor","2.2","GHz"},{"Cores","8",""},{"Memory","4","GB"},{"Storage size","64","GB"}};
    copy(a->sys_name,sizeof(a->sys_name),"Galaxy A22 5G");copy(a->sys_codename,sizeof(a->sys_codename),"a22x");
    a->sys_section_count=3;for(unsigned int n=0;n<3;n++)copy(a->sys_titles[n],sizeof(a->sys_titles[n]),titles[n]);
    a->sys_row_count=sizeof(rows)/sizeof(rows[0]);for(unsigned int n=0;n<a->sys_row_count;n++){SysRow*r=&a->sys_rows[n];copy(r->label,sizeof(r->label),rows[n].label);copy(r->value,sizeof(r->value),rows[n].value);copy(r->unit,sizeof(r->unit),rows[n].unit);r->section=rows[n].section;}
    a->sys_summary_count=4;for(unsigned int n=0;n<4;n++){SysRow*r=&a->sys_summary[n];copy(r->label,sizeof(r->label),summary[n].label);copy(r->value,sizeof(r->value),summary[n].value);copy(r->unit,sizeof(r->unit),summary[n].unit);}
}
static void usage(FILE*f){fputs("A22x native recovery UI\n  --device | --fixture-root DIR\n  --repository DIR (repeat up to 6) --backend FILE --python FILE\n  --workdir PRIVATE_DIR --fb /dev/fb0 --input /dev/input/eventN\n  --storage-helper FILE (device mode; Python helper inventory|mount|umount)\n  --filesystem-helper FILE (mount/browser/editor backend)\n  --management-helper FILE (USB SSH status and diagnostic export)\n  --display-helper FILE (reported panel refresh status and 60/90 Hz selection)\n  --usb-files-helper FILE (explicit mounted-volume MTP sharing)\n  --terminal-helper FILE (explicit local PTY shell)\n  --distro-helper FILE (reviewed ARM64 bundle install and system switch)\n  --install-helper FILE (reviewed BOOT / fixed stock image installer)\n  --profile-render (log aggregate kinetic frame costs)\n  --allow-power-actions (exit 200=system, 201=off, 202=recovery, 203=Download)\n  --preview home|backup|plan|device-restore|restore|storage|system|files|file-browser|file-editor|file-confirm|file-locked|file-info|file-symlink|file-permissions|power|download-confirm|tools|tools-export|display|display-unavailable|file-scroll|volume-scroll|editor-scroll|text-scroll|linux-prepare|linux-plan|linux-space-blocked|linux-wipe|linux-restore|usb-files|usb-active|usb-unavailable|terminal|terminal-unicode|terminal-scroll|terminal-ready|terminal-close|systems|systems-scroll|systems-unmounted|systems-pending|systems-import|systems-switch|systems-resume|systems-manifest\n     --render OUTPUT.ppm (720x1600) | --render-native OUTPUT.ppm (1080x2408)\n  --workflow inventory|backup-linux|backup-android|verify-first|restore-first|files-scroll|files-roundtrip|files-stale-write|files-large-read|files-metadata|files-stale-metadata|files-link|files-link-escape|files-copy-tree|files-stale-tree|tools-status|tools-export|tools-stale-export|tools-no-volume|display-roundtrip|display-unsupported|display-no-service|display-not-applied|display-set-error|volumes-motion|files-fling-60|files-fling-90|files-editor-motion|linux-backup|linux-wipe|linux-restore|linux-prep-stale|usb-roundtrip|usb-stale-start|usb-stale-stop\n     (regular-file fixtures only; uses the real backend and confirmation flow)\n  --self-test\n",f);}
int main(int argc,char**argv){
    App*a=calloc(1,sizeof(*a));if(!a)return 1;initialize(a);bool benchmark=false;const char*preview_name=NULL,*render_path=NULL,*workflow_name=NULL;
    for(int n=1;n<argc;n++){
        const char*arg=argv[n];if(!strcmp(arg,"--self-test")){free(a->pixels);free(a);return self_test();}
        if(!strcmp(arg,"--help")){usage(stdout);free(a->pixels);free(a);return 0;}
        if(!strcmp(arg,"--device")){a->device=true;continue;}if(!strcmp(arg,"--allow-power-actions")){a->allow_power=true;continue;}if(!strcmp(arg,"--profile-render")){a->profile_render=true;continue;}
        if(n+1>=argc){usage(stderr);return 2;}const char*v=argv[++n];
        if(!strcmp(arg,"--fixture-root"))copy(a->fixture,sizeof(a->fixture),v);else if(!strcmp(arg,"--backend"))copy(a->backend,sizeof(a->backend),v);else if(!strcmp(arg,"--python"))copy(a->python,sizeof(a->python),v);else if(!strcmp(arg,"--workdir"))copy(a->workdir,sizeof(a->workdir),v);else if(!strcmp(arg,"--storage-helper"))copy(a->storage_helper,sizeof(a->storage_helper),v);else if(!strcmp(arg,"--filesystem-helper"))copy(a->filesystem_helper,sizeof(a->filesystem_helper),v);else if(!strcmp(arg,"--management-helper"))copy(a->management_helper,sizeof(a->management_helper),v);else if(!strcmp(arg,"--display-helper"))copy(a->display_helper,sizeof(a->display_helper),v);else if(!strcmp(arg,"--usb-files-helper"))copy(a->usb_helper,sizeof(a->usb_helper),v);else if(!strcmp(arg,"--terminal-helper"))copy(a->terminal_helper,sizeof(a->terminal_helper),v);else if(!strcmp(arg,"--distro-helper"))copy(a->distro_helper,sizeof(a->distro_helper),v);else if(!strcmp(arg,"--install-helper"))copy(a->install_helper,sizeof(a->install_helper),v);else if(!strcmp(arg,"--fb"))copy(a->fb_path,sizeof(a->fb_path),v);else if(!strcmp(arg,"--input"))copy(a->input_path,sizeof(a->input_path),v);
        else if(!strcmp(arg,"--repository")){if(a->repository_count>=6){fprintf(stderr,"Too many repositories\n");return 2;}copy(a->repositories[a->repository_count++],PATH_LEN,v);}
        else if(!strcmp(arg,"--benchmark"))benchmark=!strcmp(v,"1");
        else if(!strcmp(arg,"--theme"))(void)theme_select(a,v);
        else if(!strcmp(arg,"--language")){for(unsigned int language=0;language<LANGUAGE_COUNT;language++)if(!strcmp(v,language_codes[language]))ui_language=language;}
        else if(!strcmp(arg,"--preview"))preview_name=v;else if(!strcmp(arg,"--render"))render_path=v;else if(!strcmp(arg,"--render-native")){render_path=v;a->render_native=true;}else if(!strcmp(arg,"--workflow"))workflow_name=v;else{usage(stderr);return 2;}
    }
    if(preview_name&&!render_path&&benchmark){
        if(!preview(a,preview_name))return 2;
        render(a);uint64_t started=milliseconds();for(int n=0;n<60;n++){a->dirty=true;render(a);}printf("{\"preview\":\"%s\",\"theme\":\"%s\",\"render_ms\":%.1f}\n",preview_name,ui_theme->code,(double)(milliseconds()-started)/60.0);return 0;
    }
    if(preview_name){if(!render_path||!preview(a,preview_name)){usage(stderr);return 2;}render(a);int rc=write_ppm(a,render_path)?0:1;free(a->pixels);free(a);return rc;}
    if(a->device==!!a->fixture[0]){fprintf(stderr,"Select exactly one device or fixture environment.\n");return 2;}
    if(!a->repository_count){copy(a->repositories[0],PATH_LEN,"/mnt/backup/A22xBackups");a->repository_count=1;}
    if(workflow_name){int rc=workflow(a,workflow_name,render_path);free(a->pixels);free(a);return rc;}
    if(render_path){usage(stderr);return 2;}
    Display display;if(!open_display(&display,a->fb_path)){perror("framebuffer");return 1;}
    if(a->device){
        /* The saved theme from the first frame on: /init read it for the kernel's splash. */
        char code[24]={0};int fd=open("/run/a22x-recovery/boot-theme",O_RDONLY|O_CLOEXEC);
        if(fd>=0){ssize_t n=read(fd,code,sizeof(code)-1);close(fd);if(n>0){code[strcspn(code,"\n")]=0;(void)theme_select(a,code);}}
        splash_takeover(a);
    }
    if(a->device){a->tr.from=malloc((size_t)NW*NH*sizeof(uint32_t));a->tr.to=malloc((size_t)NW*NH*sizeof(uint32_t));a->animate=a->tr.from&&a->tr.to;}
    fprintf(stderr,"[recovery-ui] framebuffer=%s visible=%ux%u bpp=%u stride=%u\n",a->fb_path,display.v.xres,display.v.yres,display.v.bits_per_pixel,display.f.line_length);
    Input inputs[MAX_INPUTS];int count=0;
    if(a->input_path[0]){if(setup_input(&inputs[0],a->input_path))count=1;}
    else for(int n=0;n<64&&count<MAX_INPUTS;n++){char path[64];snprintf(path,sizeof(path),"/dev/input/event%d",n);if(setup_input(&inputs[count],path))count++;}
    for(int n=0;n<count;n++)fprintf(stderr,"[recovery-ui] input=%s mt=%d x=%d..%d y=%d..%d\n",inputs[n].name,inputs[n].mt,inputs[n].xmin,inputs[n].xmax,inputs[n].ymin,inputs[n].ymax);
    if(!count)fprintf(stderr,"[recovery-ui] no input devices; ensure evdev nodes are ready before startup\n");
    signal(SIGINT,on_signal);signal(SIGTERM,on_signal);update_battery(a);start_job(a,J_INVENTORY);
    a->display_input_blocked=true;if(!start_display_control(a,DC_STATUS,0))a->display_input_blocked=false;
    bool slept=false;uint64_t last_frame=0,last_battery=milliseconds(),splash_start=last_battery,splash_end=last_battery+1300;a->last_activity=last_battery;
    if(a->device){prefer_fast_cores();settings_load(a);a->screen=SPLASH;render(a);(void)present(a,&display);a->dirty=true;}
    /* A stick already in the port produced its uevents before this loop started. */
    a->repository_base=a->repository_count;int hotplug=a->device?hotplug_open():-1;if(a->device&&access("/sys/block/sda",F_OK)==0){a->hotplug_pending=true;a->hotplug_at=milliseconds()+1500;}
    {FILE*f=fopen("/run/a22x-recovery/power-message","r");if(f){char note[320]={0};if(fgets(note,sizeof(note),f)){note[strcspn(note,"\n")]=0;if(note[0])problem(a,"POWER_REFUSED",note);}fclose(f);unlink("/run/a22x-recovery/power-message");}}
    while(a->running||a->display_control.pid){
        a->now=milliseconds();poll_job(a);poll_display_control(a);
        if(!a->running){if(a->display_control.pid)(void)poll(NULL,0,40);continue;}
        if(stop_signal&&!a->job.pid&&!a->display_control.pid){
            if(terminal_session_open(a)){a->terminal_close_requested=a->terminal_left=a->terminal_exit_after_stop=true;a->terminal_input_length=0;stop_signal=0;}
            else break;
        }
        terminal_pump(a);
        bool dark=a->display_blank_known&&a->display_blanked,asleep=dark&&!a->job.pid&&!a->display_control.pid&&!a->settings_pid&&!a->shot_pid&&!a->pointer_down;
        if(dark!=slept){slept=dark;ui_cpu_floor(!dark);}
        if(a->now-last_battery>(asleep?60000u:10000u)){update_battery(a);last_battery=a->now;a->dirty=true;}
        struct pollfd p[MAX_INPUTS+1];for(int n=0;n<count;n++)p[n]=(struct pollfd){inputs[n].fd,POLLIN,0};p[count]=(struct pollfd){hotplug,POLLIN,0};
        bool moving=a->pointer_down||a->kb_bubble_until||a->kinetic.active||a->tr.active||a->tr.armed||a->nav_settle_end||(a->job.pid&&job_long(a->job.kind));unsigned int frame_ms=moving?(display.drm>0?1u:(a->display_hz==90?11u:16u)):70u;
        int timeout=moving?(a->now-last_frame>=frame_ms?1:(int)(frame_ms-(a->now-last_frame))):(asleep?SLEEP_POLL_MS:40);
        int ready=poll(p,(nfds_t)count+(hotplug>=0?1:0),timeout);if(ready>0)for(int n=0;n<count;n++)if(p[n].revents&POLLIN)handle_input(a,&inputs[n],&display);
        if(ready>0&&hotplug>=0&&(p[count].revents&POLLIN))hotplug_read(a,hotplug);
        a->now=milliseconds();tick_inertia(a,a->now);browser_maybe_load(a);terminal_pump(a);(void)screen_timeout_tick(a);
        if(a->pointer_down||a->screen==PROGRESS||(a->job.pid&&job_long(a->job.kind)))a->dirty=true;
        if(a->nav_settle_end){if(a->now>=a->nav_settle_end)a->nav_settle_end=0;a->dirty=true;}
        if(a->kb_bubble_until){if(a->now>=a->kb_bubble_until)a->kb_bubble_until=0;a->dirty=true;}
        /* The panel goes dark while it changes rate, so the logo stays up until that is over. */
        if(a->screen==SPLASH&&a->now>=splash_end&&(a->now>=splash_end+4000||!(a->restore_hz||a->display_restoring))){bool fade=a->animate&&a->animation!=ANIM_OFF;if(fade)memcpy(a->tr.from,a->pixels,(size_t)NW*NH*sizeof(uint32_t));if(a->locked)lock_show(a);else screen(a,HOME);
            if(fade){a->tr.kind=TR_FADE;a->tr.duration=TRANSITION_DISSOLVE_MS;a->tr.blur=false;a->tr.armed=true;a->tr.active=false;a->dirty=true;}}
        settings_flush(a);hotplug_tick(a);if(!a->sys_tried&&a->device&&!a->job.pid&&a->screen!=SPLASH)start_sysinfo_job(a);
        if(a->now<a->lock_shake_until||a->now<a->lock_wait_until)a->dirty=true;
        if(a->screen==SPLASH){unsigned int wanted=a->now>=splash_end?100u:a->splash_from+(unsigned int)((100u-a->splash_from)*(a->now-splash_start)/(splash_end-splash_start));if(wanted!=a->splash_percent){a->splash_percent=wanted;a->dirty=true;}}
        if(a->lock_frame_pending){a->lock_frame_pending=false;render(a);(void)present(a,&display);}
        held_tick(a,dark);
        {int minute=(int)(time(NULL)/60);if(minute!=a->clock_minute){a->clock_minute=minute;a->dirty=true;}}
        /* A finished hold gets a pulse, so the finger knows it can lift. */
        if(a->pointer_down&&a->pressed_action&&a->hold_felt_at!=a->pressed_at)for(int n=0;n<a->button_count;n++)if(a->buttons[n].action==a->pressed_action&&a->buttons[n].enabled&&a->buttons[n].hold_ms&&a->now-a->pressed_at>=(uint64_t)a->buttons[n].hold_ms){a->hold_felt_at=a->pressed_at;haptic(a,2);break;}
        frame_ms=a->pointer_down||a->kinetic.active||a->tr.active||a->tr.armed||(a->job.pid&&job_long(a->job.kind))?(display.drm>0?1u:(a->display_hz==90?11u:16u)):70u;
        if(a->dirty&&!(a->display_blank_known&&a->display_blanked)&&a->now-last_frame>=frame_ms){
            uint64_t before=milliseconds();if(a->tr.armed)transition_start(a);if(!a->tr.active||!transition_frame(a,milliseconds()))render(a);if(!present(a,&display)){fprintf(stderr,"[recovery-ui] native frame allocation or projection failed\n");a->exit_code=1;break;}
            if(a->tr.active){uint64_t cost=milliseconds()-before;a->tr.frames++;if(cost>a->tr.worst)a->tr.worst=cost;}
            if(a->kinetic.active){uint64_t cost=milliseconds()-before;a->motion_frames++;a->motion_render_total+=cost;if(cost>a->motion_render_max)a->motion_render_max=cost;}
            last_frame=a->now;
        }
    }
    for(int n=0;n<count;n++)close(inputs[n].fd);
    /* A power action switches the flashlight off, and leaves this frame up while init finishes the
       work and the phone resets. */
    if(a->exit_code>=200&&a->exit_code<=203)torch_leave(a);
    if(a->device&&a->exit_code>=200&&a->exit_code<=203){render_farewell(a,a->exit_code);(void)present(a,&display);}
    close_display(&display);int rc=a->exit_code;free(a->pixels);free(a);return rc;
}

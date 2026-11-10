/* SPDX-License-Identifier: MIT
 * Wipe, as TWRP's (2026-11-10): cache without the recovery's own folder (filesystem.py wipe
 * keeps a22x-recovery: settings, keys, saved Wi-Fi networks, plans from the Android app and setup
 * scripts), and the way to a whole volume in Files, where every volume is erased with a hold.
 * Nothing is erased before the hold on the review page.
 */
#ifndef A22X_WIPE_UI_H
#define A22X_WIPE_UI_H

static bool wipe_job(int kind){return kind==J_WIPE_CACHE;}
static bool start_wipe_job(App*a){
    if(a->job.pid||a->preview||!a->filesystem_helper[0])return false;
    char*args[10];int n=0;args[n++]=a->python;args[n++]=a->filesystem_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="wipe";args[n++]="--volume";args[n++]="cache";args[n++]="--confirm";args[n]=NULL;
    return launch_job(a,J_WIPE_CACHE,args);
}
static void complete_wipe_job(App*a,const Json*j,bool ok){
    if(!ok||!j){
        char code[64]="WIPE_FAILED",message[256]="Cache could not be wiped.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        files_problem(a,code,message,WIPE);return;
    }
    char freed[48];human_bytes(json_uint(j,json_get(j,0,"freed_bytes"),0),freed,sizeof(freed));
    unsigned int removed=(unsigned int)json_uint(j,json_get(j,0,"removed"),0);
    a->successful=true;copy(a->status,sizeof(a->status),"Cache wiped");
    snprintf(a->message,sizeof(a->message),T("Removed %u items and freed %s. The recovery's own folder stays: its settings, Wi-Fi networks, plans and scripts."),removed,freed);
    a->result_return=WIPE;screen(a,RESULT);
}
static void render_wipe(App*a){
    heading(a,"Wipe","Erase what is no longer needed. Nothing is erased before you hold the button.",true);
    os_card(a,242,150,ICON_TRASH,"Cache","Update packages, logs and temporary files",A_WIPE_CACHE,PANEL);
    os_card(a,408,150,ICON_ERASE,"A whole volume","userdata, cache or an SD card, in Files",A_WIPE_VOLUMES,PANEL);
    wrapped(a,40,590,640,0,MUTED,"Erasing userdata removes every Linux system and every saved image. Android's factory reset comes with the Android installer.",4);
}
static void render_wipe_review(App*a){
    heading(a,"Wipe cache","Nothing has been erased yet.",true);
    rounded(a,(Rect){28,240,664,120},24,PANEL);text(a,52,258,0,ORANGE,"VOLUME");text(a,52,296,2,TEXT,"cache");
    rounded(a,(Rect){28,376,664,216},24,PANEL);text(a,52,394,0,ORANGE,"STAYS");
    wrapped(a,52,432,606,1,TEXT,"a22x-recovery: the recovery's settings and keys, saved Wi-Fi networks, plans from the Android app and setup scripts.",4);
    wrapped(a,40,616,640,0,MUTED,"Everything else on cache is deleted: update packages, logs and temporary files.",3);
    button(a,(Rect){28,1320,664,104},"Hold 3 seconds to wipe",A_WIPE_CONFIRM,ui_free(a)&&!a->job.pid,true,3000);
}
static bool wipe_action(App*a,int id){
    if(id==A_WIPE){if(ui_free(a))screen(a,WIPE);return true;}
    if(a->screen==WIPE){
        if(id==A_BACK){screen(a,OS_MENU);return true;}
        if(id==A_WIPE_CACHE){if(ui_free(a))screen(a,WIPE_REVIEW);return true;}
        if(id==A_WIPE_VOLUMES){
            if(!ui_free(a))return true;
            /* Files exactly as from the home page. */
            a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
        }
    }
    if(a->screen==WIPE_REVIEW){
        if(id==A_BACK){screen(a,WIPE);return true;}
        if(id==A_WIPE_CONFIRM){
            if(ui_free(a)&&!a->job.pid&&!start_wipe_job(a))files_problem(a,"WIPE_UNAVAILABLE","Wait for the current operation to finish.",WIPE);
            return true;
        }
    }
    return false;
}
#endif

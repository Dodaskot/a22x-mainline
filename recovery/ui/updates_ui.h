/* SPDX-License-Identifier: MIT
 * Settings > Updates (2026-11-12, the owner: "a separate item in Settings to update BOOT and
 * recovery, conveniently, right there"). updates.py says what is installed and what is published;
 * an update is downloaded and checked there, and then goes through Flash a partition like any
 * image: reviewed, the old content saved first, written by a hold and read back.
 */
#ifndef A22X_UPDATES_UI_H
#define A22X_UPDATES_UI_H

static bool updates_job(int kind){return kind==J_UPDATES_CHECK||kind==J_UPDATES_FETCH;}
static bool start_updates_job(App*a,int kind,const char*what){
    if(a->job.pid||a->preview||!a->updates_helper[0])return false;
    char*args[12];int n=0;args[n++]=a->python;args[n++]=a->updates_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_UPDATES_CHECK)args[n++]="check";
    else{
        const char*sha=!strcmp(what,"recovery")?a->upd_rec_sha:a->upd_boot_sha;if(!valid_sha256(sha))return false;
        args[n++]="fetch";args[n++]="--what";args[n++]=(char*)what;args[n++]="--sha256";args[n++]=(char*)sha;
        copy(a->flash_after_label,sizeof(a->flash_after_label),!strcmp(what,"recovery")?"recovery":"boot");
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;a->result_return=UPDATES;return true;
}
static void complete_updates_job(App*a,const Json*j,int kind,bool ok){
    if(!ok||!j){
        char code[96]="UPDATE_FAILED",message[384]="The helper did not return a result.";
        if(j){int error=json_get(j,0,"error");if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_UPDATES_CHECK){a->updates_known=false;copy(a->upd_rec_error,sizeof(a->upd_rec_error),code);a->dirty=true;if(a->screen==PROGRESS)screen(a,UPDATES);return;}
        files_problem(a,code,message,UPDATES);return;
    }
    if(kind==J_UPDATES_CHECK){
        int rec=json_get(j,0,"recovery"),boot=json_get(j,0,"boot");
        a->upd_rec_installed[0]=a->upd_rec_latest[0]=a->upd_rec_sha[0]=a->upd_rec_error[0]=0;a->upd_rec_newer=false;
        if(rec>=0){
            jstring(j,rec,"installed",a->upd_rec_installed,sizeof(a->upd_rec_installed));jstring(j,rec,"error",a->upd_rec_error,sizeof(a->upd_rec_error));
            a->upd_rec_newer=json_bool(j,json_get(j,rec,"newer"),0)==1;int latest=json_get(j,rec,"latest");
            if(latest>=0&&j->tokens[latest].type==J_OBJECT){jstring(j,latest,"version",a->upd_rec_latest,sizeof(a->upd_rec_latest));jstring(j,latest,"sha256",a->upd_rec_sha,sizeof(a->upd_rec_sha));}
        }
        /* No published release read is never up to date (2026-11-14 review: GitHub's 403 came as a
           number, read as no error at all). */
        if(!a->upd_rec_error[0]&&!a->upd_rec_latest[0])copy(a->upd_rec_error,sizeof(a->upd_rec_error),"UPDATE_UNAVAILABLE");
        a->upd_boot_kind[0]=a->upd_boot_state[0]=a->upd_boot_latest[0]=a->upd_boot_sha[0]=0;a->upd_boot_newer=false;
        if(boot>=0){
            jstring(j,boot,"kind",a->upd_boot_kind,sizeof(a->upd_boot_kind));jstring(j,boot,"state",a->upd_boot_state,sizeof(a->upd_boot_state));
            a->upd_boot_newer=json_bool(j,json_get(j,boot,"newer"),0)==1;int latest=json_get(j,boot,"latest");
            if(latest>=0&&j->tokens[latest].type==J_OBJECT){jstring(j,latest,"version",a->upd_boot_latest,sizeof(a->upd_boot_latest));jstring(j,latest,"sha256",a->upd_boot_sha,sizeof(a->upd_boot_sha));}
        }
        a->updates_known=true;a->dirty=true;if(a->screen==PROGRESS)screen(a,UPDATES);return;
    }
    /* The checked image waits on cache: Flash a partition takes it from here. */
    char token[96]={0},path[PATH_LEN]={0};jstring(j,0,"volume_token",token,sizeof(token));jstring(j,0,"path",path,sizeof(path));
    if(!token[0]||path[0]!='/'){files_problem(a,"UPDATE_FAILED","The helper did not return a result.",UPDATES);return;}
    a->flash_restore=a->flash_pick=a->flash_review_valid=false;a->flash_known=false;a->flash_reason[0]=0;
    copy(a->flash_volume,sizeof(a->flash_volume),"cache");copy(a->flash_volume_token,sizeof(a->flash_volume_token),token);
    copy(a->flash_path,sizeof(a->flash_path),path);a->flash_member[0]=0;a->flash_after_targets=true;screen(a,FLASH);/* 2026-11-14 review: a list that cannot start drops the pending flash */if(!start_flash_job(a,J_FLASH_TARGETS)&&!a->preview){a->flash_after_targets=false;a->flash_after_label[0]=0;}
}
static void render_updates(App*a){
    heading(a,"Updates","This recovery and the Linux BOOT, from the internet.",true);
    bool free_ui=ui_free(a);char line[192];
    rounded(a,(Rect){28,236,664,330},24,PANEL);icon_tile(a,A_RESTORE,46,252,56,ICON_FILL);text(a,120,262,1,TEXT,"Recovery");
    if(!a->updates_known)wrapped(a,52,330,616,0,MUTED,a->job.pid?"Checking...":"Not checked yet.",2);
    else{
        snprintf(line,sizeof(line),T("Installed: %s"),a->upd_rec_installed[0]?a->upd_rec_installed:"?");text(a,52,330,0,TEXT,line);
        if(a->upd_rec_latest[0]){snprintf(line,sizeof(line),T("Published: %s"),a->upd_rec_latest);text(a,52,372,0,TEXT,line);}
        char failed[192];snprintf(failed,sizeof(failed),T("The releases could not be read (%s). Check again later."),a->upd_rec_error);
        const char*note=!strcmp(a->upd_rec_error,"NO_NETWORK")?tr("The releases could not be read. Join Wi-Fi, then check again."):
            a->upd_rec_error[0]?failed:tr(a->upd_rec_newer?"A newer recovery is published.":"This recovery is up to date.");
        raw_text(true);wrapped(a,52,414,616,0,a->upd_rec_newer&&!a->upd_rec_error[0]?GREEN:MUTED,note,2);raw_text(false);
    }
    button(a,(Rect){46,476,628,74},"Update recovery",A_UPDATES_RECOVERY,free_ui&&a->updates_known&&a->upd_rec_newer,true,0);
    rounded(a,(Rect){28,590,664,330},24,PANEL);icon_tile(a,A_OS,46,606,56,ICON_FILL);text(a,120,616,1,TEXT,"Linux BOOT");
    if(a->updates_known){
        /* "unknown": BOOT could not be read, neither Android nor Linux (2026-11-14 review). */
        const char*note=strcmp(a->upd_boot_kind,"android")&&strcmp(a->upd_boot_kind,"linux")?"BOOT could not be read, so nothing is offered for it.":
            !strcmp(a->upd_boot_kind,"android")?"BOOT holds Android. It is changed in Install, Android.":
            (!strcmp(a->upd_boot_state,"current")?"BOOT holds the current Linux release.":
            (!strcmp(a->upd_boot_state,"older")?"A newer Linux BOOT is published.":"BOOT holds a Linux kernel of another build. Update only if you want the release."));
        if(a->upd_boot_latest[0]){snprintf(line,sizeof(line),T("Published: %s"),a->upd_boot_latest);text(a,52,684,0,TEXT,line);}
        wrapped(a,52,726,616,0,a->upd_boot_newer?GREEN:MUTED,note,3);
    }
    bool boot_ok=a->updates_known&&!strcmp(a->upd_boot_kind,"linux")&&valid_sha256(a->upd_boot_sha)&&strcmp(a->upd_boot_state,"current");
    button(a,(Rect){46,830,628,74},"Update BOOT",A_UPDATES_BOOT,free_ui&&boot_ok,a->upd_boot_newer,0);
    text_tile(a,946,0,MUTED,"The new image is downloaded and checked first. Then Flash a partition shows it: the old content is saved, and a hold writes it.",3);
    button(a,(Rect){28,1190,664,84},"Check again",A_UPDATES_REFRESH,free_ui,false,0);
}
static bool updates_action(App*a,int id){
    if(id==A_UPDATES){if(!ui_free(a))return true;screen(a,UPDATES);if(!a->updates_known)start_updates_job(a,J_UPDATES_CHECK,"");return true;}
    if(a->screen!=UPDATES)return false;
    if(id==A_UPDATES_REFRESH){a->updates_known=false;start_updates_job(a,J_UPDATES_CHECK,"");return true;}
    if(id==A_UPDATES_RECOVERY){start_updates_job(a,J_UPDATES_FETCH,"recovery");return true;}
    if(id==A_UPDATES_BOOT){start_updates_job(a,J_UPDATES_FETCH,"boot");return true;}
    return false;
}

#endif

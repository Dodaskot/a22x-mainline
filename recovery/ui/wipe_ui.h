/* SPDX-License-Identifier: MIT
 * Wipe, as TWRP's (2026-11-10): cache without the recovery's own folder (filesystem.py wipe
 * keeps a22x-recovery: settings, keys, saved Wi-Fi networks, plans from the Android app and setup
 * scripts), and the way to a whole volume in Files, where every volume is erased with a hold.
 * Nothing is erased before the hold on the review page.
 */
#ifndef A22X_WIPE_UI_H
#define A22X_WIPE_UI_H

static bool wipe_job(int kind){return kind==J_WIPE_CACHE||kind==J_ANDROID_RESET;}
/* Android's request to erase its data (2026-11-11): init read misc at the start (bcb.py arrive) and
   left what it found in RAM. The page of Data for Android opens for it once, from the home page. */
static void reset_request_poll(App*a){
    if(a->reset_checked||a->preview)return;
    if(a->screen!=HOME||a->locked||a->job.pid||a->pointer_down)return;
    a->reset_checked=true;
    const char*path=getenv("A22X_RECOVERY_BCB");if(!path||!*path)path="/run/a22x-recovery/bcb.json";
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);char data[2048];ssize_t n=fd>=0?read(fd,data,sizeof(data)-1):-1;if(fd>=0)close(fd);
    if(n<=0)return;
    data[n]=0;JsonToken t[64];Json j;if(json_parse(&j,data,t,64)<1||t[0].type!=J_OBJECT||json_bool(&j,json_get(&j,0,"wipe"),0)!=1)return;
    a->reset_asked=true;a->reset_prompt=strstr(data,"--prompt_and_wipe_data")!=NULL;
    a->wipe_android=true;a->reset_page=true;screen(a,WIPE_REVIEW);
}
/* The owner's word that the request is dropped: misc is cleared, nothing is erased, Android starts as it is. */
static void reset_request_forget(App*a){
    const char*helper=getenv("A22X_RECOVERY_BCB_HELPER");if(!helper||!*helper)helper="/opt/a22x-recovery/bcb.py";
    bool done=a->preview;
    if(!a->preview){
        char*args[6]={a->python,"-B",(char*)helper,a->device?"--device":"--fixture",NULL,NULL};int n=4;
        if(!a->device)args[n++]=a->fixture;
        args[n++]="forget";char*argv[8];for(int k=0;k<n;k++)argv[k]=args[k];argv[n]=NULL;
        pid_t pid=fork();
        if(!pid){(void)close_range(3,~0u,0);int null=open("/dev/null",O_WRONLY);if(null>=0){dup2(null,1);dup2(null,2);}execv(a->python,argv);_exit(2);}
        int status=0;done=pid>0&&waitpid(pid,&status,0)==pid&&WIFEXITED(status)&&WEXITSTATUS(status)==0;
    }
    if(!done){toast(a,"The request could not be cleared");return;}
    a->reset_asked=a->reset_page=false;a->wipe_android=false;unlink(getenv("A22X_RECOVERY_BCB")?getenv("A22X_RECOVERY_BCB"):"/run/a22x-recovery/bcb.json");
    toast(a,"The request is forgotten: Android starts as it is");screen(a,HOME);
}
static bool start_wipe_job(App*a){
    if(a->job.pid||a->preview||!a->filesystem_helper[0])return false;
    char*args[10];int n=0;args[n++]=a->python;
    /* Data for Android (2026-11-10), TWRP's Format Data: samsung_firmware.py reset-data. */
    if(a->wipe_android){if(!a->samsung_helper[0])return false;args[n++]=a->samsung_helper;}else args[n++]=a->filesystem_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(a->wipe_android){args[n++]="reset-data";args[n++]="--confirm";args[n]=NULL;return launch_job(a,J_ANDROID_RESET,args);}
    args[n++]="wipe";args[n++]="--volume";args[n++]="cache";args[n++]="--confirm";args[n]=NULL;
    return launch_job(a,J_WIPE_CACHE,args);
}
static void complete_wipe_job(App*a,const Json*j,bool ok){
    if(!ok||!j){
        char code[64]="WIPE_FAILED",message[256]="Cache could not be wiped.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        files_problem(a,code,message,WIPE);return;
    }
    if(a->wipe_android){
        a->successful=true;copy(a->status,sizeof(a->status),"Data formatted for Android");
        copy(a->message,sizeof(a->message),tr("userdata is formatted for Android and metadata erased; Android sets up its encryption on its next start. The recovery's own folder on cache stays."));
        /* The helper cleared Android's request with the data. */
        bool asked=a->reset_page;a->reset_asked=a->reset_page=false;
        a->android_installed=true;a->systems_reboot_blocked=a->install.reboot_blocked=false;a->result_return=asked?HOME:WIPE;screen(a,RESULT);return;
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
    os_card(a,574,150,ICON_PHONE,"Data for Android","Format userdata and metadata for Android, as TWRP's Format Data",A_WIPE_ANDROID,PANEL);
    os_card(a,740,150,A_STORAGE,"Partition a card","Files, Linux and swap on an SD card",A_CARD,PANEL);
    text_tile(a,922,0,MUTED,"Erasing userdata removes every Linux system and every saved image.",4);
}
static void render_wipe_review(App*a){
    if(a->wipe_android&&a->reset_page){
        /* Android itself asked: what it asked, what the hold erases, and the two ways not to. */
        heading(a,"Android asks for a reset","Nothing has been erased yet.",true);
        int y=240+text_tile(a,240,0,ORANGE,a->reset_prompt?"Android could not open its data and asks to erase it. When it failed only once, starting it again may be enough: forget the request and restart.":
                                                              "A factory reset was asked for in Android's settings: Android restarted into this recovery to have its data erased.",4)+16;
        rounded(a,(Rect){28,y,664,206},24,PANEL);text(a,52,y+18,0,RED,"ERASED");
        text(a,52,y+58,1,TEXT,"userdata");wrapped(a,300,y+64,370,0,MUTED,"Formatted for Android: every Linux system, saved image and Android's own data",3);
        text(a,52,y+152,1,TEXT,"metadata");wrapped(a,300,y+158,370,0,MUTED,"Android creates it again",1);y+=222;
        text_tile(a,y,0,MUTED,"BOOT stays as it is, and a root in it with it. Until the data is erased or the request is forgotten, a restart of the system comes back here.",4);
        button(a,(Rect){28,1104,324,84},"Not now",A_RESET_LATER,ui_free(a),false,0);
        button(a,(Rect){368,1104,324,84},"Forget the request",A_RESET_FORGET,ui_free(a),false,0);
        button(a,(Rect){28,1320,664,104},"Hold 5 seconds to format",A_WIPE_CONFIRM,ui_free(a)&&!a->job.pid,true,5000);
        return;
    }
    if(a->wipe_android){
        heading(a,"Data for Android","Nothing has been erased yet.",true);
        rounded(a,(Rect){28,240,664,252},24,PANEL);text(a,52,258,0,RED,"ERASED");
        text(a,52,298,1,TEXT,"userdata");wrapped(a,300,304,370,0,MUTED,"Formatted for Android: every Linux system, saved image and Android's own data",3);
        text(a,52,392,1,TEXT,"metadata");wrapped(a,300,398,370,0,MUTED,"Android creates it again",2);
        text(a,52,444,1,TEXT,"cache");wrapped(a,300,450,370,0,MUTED,"The recovery's own folder stays",2);
        text_tile(a,516,0,MUTED,"As TWRP's Format Data and Samsung's recovery after Odin: Android sets up its encryption on its next start. The SD card is not touched.",4);
        button(a,(Rect){28,1320,664,104},"Hold 5 seconds to format",A_WIPE_CONFIRM,ui_free(a)&&!a->job.pid,true,5000);
        return;
    }
    heading(a,"Wipe cache","Nothing has been erased yet.",true);
    rounded(a,(Rect){28,240,664,120},24,PANEL);text(a,52,258,0,ORANGE,"VOLUME");text(a,52,296,2,TEXT,"cache");
    rounded(a,(Rect){28,376,664,216},24,PANEL);text(a,52,394,0,ORANGE,"STAYS");
    wrapped(a,52,432,606,1,TEXT,"a22x-recovery: the recovery's settings and keys, saved Wi-Fi networks, plans from the Android app and setup scripts.",4);
    text_tile(a,616,0,MUTED,"Everything else on cache is deleted: update packages, logs and temporary files.",3);
    button(a,(Rect){28,1320,664,104},"Hold 3 seconds to wipe",A_WIPE_CONFIRM,ui_free(a)&&!a->job.pid,true,3000);
}
static bool wipe_action(App*a,int id){
    if(id==A_WIPE){if(ui_free(a))screen(a,WIPE);return true;}
    if(a->screen==WIPE){
        if(id==A_BACK){reopen(a,came_from(a,OS_MENU));return true;}
        if(id==A_WIPE_CACHE||id==A_WIPE_ANDROID){if(ui_free(a)){a->wipe_android=id==A_WIPE_ANDROID;a->reset_page=a->wipe_android&&a->reset_asked;screen(a,WIPE_REVIEW);}return true;}
        if(id==A_WIPE_VOLUMES){
            if(!ui_free(a))return true;
            /* Files exactly as from the home page. */
            a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
        }
    }
    if(a->screen==WIPE_REVIEW){
        /* Opened for Android's request: back, and Not now, go home, and the request stays. */
        if(a->reset_page&&(id==A_BACK||id==A_RESET_LATER)){a->reset_page=false;a->wipe_android=false;screen(a,HOME);return true;}
        if(a->reset_page&&id==A_RESET_FORGET){if(ui_free(a))reset_request_forget(a);return true;}
        if(id==A_BACK){screen(a,WIPE);return true;}
        if(id==A_WIPE_CONFIRM){
            if(ui_free(a)&&!a->job.pid&&!start_wipe_job(a))files_problem(a,"WIPE_UNAVAILABLE","Wait for the current operation to finish.",WIPE);
            return true;
        }
    }
    return false;
}
#endif

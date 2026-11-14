/* SPDX-License-Identifier: MIT
 * Reviewed offline system management. All filesystem changes are delegated to
 * distro.py, which owns archive validation, journal durability and boot gates.
 */
#ifndef A22X_SYSTEMS_UI_H
#define A22X_SYSTEMS_UI_H

static void select_volume(App*a,int index);
static void open_selected_file(App*a);
static bool systems_id(const char*s){
    size_t n=strlen(s);if(!n||n>48||!((s[0]>='a'&&s[0]<='z')||(s[0]>='0'&&s[0]<='9')))return false;
    for(size_t i=1;i<n;i++)if(!((s[i]>='a'&&s[i]<='z')||(s[i]>='0'&&s[i]<='9')||s[i]=='-'))return false;
    return true;
}
static bool systems_array_exact(const Json*j,int parent,const char*key,const char*one){
    int array=json_get(j,parent,key);if(array<0||j->tokens[array].type!=J_ARRAY)return false;
    int child=array+1;if(child>=json_next(j,array)||json_next(j,child)!=json_next(j,array))return false;
    char value[80];return json_string(j,child,value,sizeof(value))>=0&&!strcmp(value,one);
}
static bool systems_array_has(const Json*j,int parent,const char*key,const char*value){
    int array=json_get(j,parent,key);if(array<0||j->tokens[array].type!=J_ARRAY)return false;
    for(int i=array+1;i<json_next(j,array);i=json_next(j,i)){char text_value[80];if(json_string(j,i,text_value,sizeof(text_value))>=0&&!strcmp(text_value,value))return true;}return false;
}
static bool systems_ready(const App*a){return a->systems_known&&a->systems_mounted&&!a->systems_read_only&&!a->systems_transaction[0]&&!a->systems_reboot_blocked&&a->systems_platform;}
/* Userdata that holds no Linux (Android's data, 2026-11-11): nothing there can be mounted or kept,
   and a new system needs no mount - the confirmed installation formats it. */
static bool systems_fresh(const App*a){return a->systems_known&&a->systems_foreign&&!a->systems_mounted&&!a->systems_transaction[0]&&a->systems_platform;}
static void systems_forget_plan(App*a){
    a->systems_plan_valid=false;
    if(a->systems_plan_path[0]){unlink(a->systems_plan_path);a->systems_plan_path[0]=0;}
    a->systems_plan_seal[0]=a->systems_plan_hash[0]=0;a->systems_plan_page=0;
}
static const char*const systems_desktops[]={"image","console","phosh","gnome","plasma","xfce","sxmo","lomiri"};
static const char*const systems_desktop_labels[]={"As in image","Console","Phosh","GNOME","Plasma Mobile","Xfce","Sxmo","Lomiri"};
#define SYSTEMS_DESKTOPS 8u
/* Sxmo and Lomiri are packaged by Debian and Ubuntu only (2026-11-08). An image from the
   catalog tells its family; a file may be anything, and the first start falls back to Phosh. */
static bool systems_desktop_debian(unsigned int n){return n>=6;}
/* Apps for the first start; distro.py and a22x-firstboot.sh know the same names. */
static const char*const systems_app_ids[]={"browser","settings","files","terminal","editor","store","keyboard","telegram","ssh",
    "email","music","video","photos","documents","office","calculator","clock","weather","maps","camera",
    "contacts","calendar","passwords","flatpak","chromium","podcasts","messages","git","monitor"};
static const char*const systems_app_labels[]={"Browser","Settings","Files","Terminal","Text editor","App store","Keyboard","Telegram","SSH server",
    "Email","Music","Video player","Photos","Documents","Office","Calculator","Clock","Weather","Maps","Camera",
    "Contacts","Calendar","Passwords","Flatpak","Chromium","Podcasts","Messages","Git","System monitor"};
#define SYSTEMS_APPS 29u
#define SYSTEMS_SCRIPT_LIMIT 1048576u
/* The family of the chosen image: the catalog says it; for a file, its name usually does
   (ArchLinuxARM-..., Fedora-Cloud-..., ubuntu-26.04-..., mobian-...). NULL when unknown. */
static const char*systems_image_family(const App*a){
    if(a->systems_remote_id[0]){
        for(unsigned int i=0;i<a->catalog_count;i++)if(!strcmp(a->catalog[i].id,a->systems_remote_id))return a->catalog[i].family;
        return NULL;
    }
    const char*name=strrchr(a->systems_manifest,'/');name=name?name+1:a->systems_manifest;
    static const char*const marks[][2]={{"archlinux","arch"},{"manjaro","manjaro"},{"fedora","fedora"},{"ubuntu","ubuntu"},
                                         {"mobian","mobian"},{"debian","debian"}};
    for(unsigned int n=0;n<sizeof(marks)/sizeof(marks[0]);n++)if(strcasestr(name,marks[n][0]))return marks[n][1];
    return NULL;
}
/* Whether the image may have Sxmo and Lomiri packages: Debian, Ubuntu and Mobian do; a file of
   unknown family might (2026-11-09: a Fedora file offered them). */
static bool systems_image_debian(const App*a){
    const char*family=systems_image_family(a);
    if(!family)return true;
    return !strcmp(family,"debian")||!strcmp(family,"ubuntu")||!strcmp(family,"mobian");
}
static bool systems_needs_network(const App*a){return a->systems_desktop>1||a->systems_apps||a->systems_package_count;}
static bool systems_wifi_password_ok(const char*p){
    size_t n=strlen(p);if(!n||(n>=8&&n<=63))return true;if(n!=64)return false;
    for(size_t i=0;i<n;i++)if(!isxdigit((unsigned char)p[i]))return false;
    return true;
}
/* The network recovery joined becomes the new system's network until changed. A password typed
   in this session goes along; a network joined from the saved list has only its key, which the
   plan takes from recovery (2026-11-08: the prefill had sent such a network as open). */
static void systems_wifi_from_recovery(App*a){
    if(!a->wifi_connected||!a->wifi_ssid[0]||strlen(a->wifi_ssid)>32){
        /* Offline now, but a remembered network is in range: the new system gets it with the
           key recovery saved, as if it were joined (2026-11-08, offline install from SD). */
        for(unsigned int n=0;n<a->wifi_count;n++){const WifiNet*w=&a->wifi_nets[n];
            if(w->saved&&w->supported&&w->ssid[0]&&strlen(w->ssid)<=32){copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),w->ssid);
                memset(a->systems_wifi_password,0,sizeof(a->systems_wifi_password));a->systems_wifi_saved=true;return;}}
        return;
    }
    copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),a->wifi_ssid);
    if(a->wifi_password[0]&&systems_wifi_password_ok(a->wifi_password)){copy(a->systems_wifi_password,sizeof(a->systems_wifi_password),a->wifi_password);a->systems_wifi_saved=false;}
    else{memset(a->systems_wifi_password,0,sizeof(a->systems_wifi_password));a->systems_wifi_saved=true;}
}
static void systems_forget_choices(App*a){
    memset(a->systems_wifi_ssid,0,sizeof(a->systems_wifi_ssid));memset(a->systems_wifi_password,0,sizeof(a->systems_wifi_password));a->systems_wifi_saved=false;
    a->systems_apps=0;a->systems_shells=0;a->systems_greeter=0;a->systems_locale=a->systems_tz=-1;a->systems_tz_region=0;a->systems_script_volume[0]=a->systems_script_token[0]=a->systems_script_path[0]=a->systems_script_name[0]=0;
    a->systems_package_count=0;a->pkg_count=0;a->pkg_query[0]=0;a->pkg_error[0]=0;a->pkg_known=false;
    /* The plan from the Android app sets these again after this (android_plan_fill). */
    memset(a->systems_password_hash,0,sizeof(a->systems_password_hash));a->aplan_used=false;
}
/* Empty userdata, or one the installer erases first: either way a clean install. */
static bool systems_clean(const App*a){return a->systems_empty||a->systems_wipe;}
static bool systems_name_ends(const char*name,const char*tail){size_t n=strlen(name),t=strlen(tail);return n>t&&!strcasecmp(name+n-t,tail);}
static bool systems_manifest_name(const char*name){size_t n=strlen(name);return n>5&&!strcmp(name+n-5,".json");}
static bool systems_image_tail(const char*name){
    static const char*const tails[]={".iso",".raw",".img",".tar",".tgz",".xz",".zst",".gz",".bz2",".qcow2",".box",".vhd",".vhdfixed"};
    for(size_t n=0;n<sizeof(tails)/sizeof(tails[0]);n++)if(systems_name_ends(name,tails[n]))return true;
    return false;
}
/* The importer recognises the format from the content; the name only decides what is offered.
   A download can leave one more suffix after the image's own, as ".qcow2.man" did (2026-11-08:
   that file could not be chosen). Such a name counts only for a large file, so a checksum,
   signature or bmap file beside the image is not taken for it. */
static bool systems_image_choice(const char*name,unsigned long long size){
    if(systems_image_tail(name))return true;
    const char*last=strrchr(name,'.');
    if(!last||last==name||strlen(last)>12||size<(16ULL<<20))return false;
    char stem[256];size_t k=(size_t)(last-name);
    if(k>=sizeof(stem))return false;
    memcpy(stem,name,k);stem[k]=0;
    return systems_image_tail(stem);
}
static bool systems_manifest_selected(const App*a){
    const FileVolume*v=a->volume_index>=0&&a->volume_index<a->volume_count?&a->volumes[a->volume_index]:NULL;
    /* A prepared bundle goes beside a Linux that is there; on Android's userdata only an image is installed. */
    return systems_source_allowed(v)&&file_is_regular(&a->fs_selected)&&a->fs_selected.token[0]&&
        ((systems_manifest_name(a->fs_selected.name)&&!a->systems_foreign)||systems_image_choice(a->fs_selected.name,(unsigned long long)a->fs_selected.size));
}
/* Userdata can be mounted from Systems, from New system and from the catalog (2026-11-08: the
   button of New system did nothing, as only Systems handled it). */
static bool systems_mount_screen(int screen_){return screen_==SYSTEMS||screen_==SYSTEMS_NEW||screen_==SYSTEMS_CATALOG||screen_==SYSTEMS_IMAGES||screen_==ANDROID_IMAGE;}
static bool systems_can_mount(const App*a){return a->systems_known&&!a->systems_foreign&&!a->systems_transaction[0]&&a->systems_volume_token[0]&&(!a->systems_mounted||a->systems_read_only);}
/* Mounting from here: the same request Files sends, then Systems reads the volume again. */
static void render_systems_mount(App*a){
    a->button_count=0;veil(a,(Rect){0,0,CW,CH},0,0x000000u,110);
    Rect sheet={28,880,664,540};rounded(a,sheet,40,BG);rounded(a,sheet,40,PANEL);
    text(a,60,912,1,TEXT,"Mount userdata");
    wrapped(a,60,968,600,0,MUTED,a->systems_mounted?"Write access lets Systems install and switch systems. Nothing is formatted.":"Read-only shows the installed systems. Write access also lets you install and switch. Nothing is formatted.",3);
    button(a,(Rect){60,1092,600,92},a->systems_mounted?"Enable write access":"Mount with write access",A_SYS_MOUNT_RW,ui_free(a),true,0);
    if(!a->systems_mounted)button(a,(Rect){60,1198,600,86},"Mount read-only",A_SYS_MOUNT_RO,ui_free(a),false,0);
    button(a,(Rect){60,a->systems_mounted?1198:1298,600,86},"Cancel",A_SYS_MOUNT_CANCEL,true,false,0);
    register_button(a,(Rect){0,0,CW,CH},A_SYS_MOUNT_CANCEL,true,0);
}
static void render_systems(App*a){
    heading(a,"Linux systems","ARM64 root filesystems / shared /home / existing BOOT",true);
    rounded(a,(Rect){28,242,664,152},18,PANEL);
    text(a,52,262,0,ORANGE,a->systems_transaction[0]?"INTERRUPTED SWITCH":"CURRENT SYSTEM");
    ellipsis(a,52,304,systems_can_mount(a)?576:610,1,TEXT,a->systems_active[0]?a->systems_active:(a->systems_known?(a->systems_mounted?(a->systems_empty?"Empty userdata: ready for a clean install":"Existing Linux / not yet adopted"):(a->systems_foreign?"No Linux: userdata holds Android":"Mount userdata to inspect systems")):"Reading installed systems..."));
    /* While the status is read (after a removal, for one) it says so, as Tools and USB files do. */
    const char*access=!a->systems_known?(a->job.pid&&a->job.kind==J_SYS_STATUS?"Checking status...":"Status unavailable"):(!a->systems_mounted?(a->systems_foreign?"A new system formats it for Linux":"Tap to mount userdata"):(a->systems_read_only?"Read-only: tap to enable writes":"Userdata is mounted with write access"));
    ellipsis(a,52,352,560,0,a->systems_mounted&&!a->systems_read_only?GREEN:(systems_can_mount(a)?ORANGE:MUTED),access);
    if(systems_can_mount(a)){icon(a,ICON_CHEVRON,644,300,32,ORANGE);register_button(a,(Rect){28,242,664,152},A_SYS_MOUNT,ui_free(a),0);}
    if(a->systems_transaction[0]){
        text(a,40,432,1,RED,"Normal Linux boot is blocked");
        char steps[96];snprintf(steps,sizeof(steps),T("Journal step %u of %u"),a->systems_step,a->systems_total);text(a,40,490,0,MUTED,steps);
        text(a,40,548,0,ORANGE,"FROM");wrapped(a,40,586,640,1,TEXT,a->systems_source,2);
        text(a,40,678,0,ORANGE,"TO");wrapped(a,40,716,640,1,TEXT,a->systems_target,2);
        wrapped(a,40,824,640,0,MUTED,"The root directory switch was interrupted. Resume finishes it; Rollback restores the previous system. The shared /home stays in place. This is a journaled sequence, not one atomic rename.",7);
        bool writable=a->systems_known&&a->systems_mounted&&!a->systems_read_only;
        button(a,(Rect){28,1070,320,86},"Review Resume",A_SYS_RESUME,ui_free(a)&&writable&&a->systems_resume_allowed,true,0);
        button(a,(Rect){372,1070,320,86},"Review Rollback",A_SYS_ROLLBACK,ui_free(a)&&writable&&a->systems_rollback_allowed,false,0);
    }else{
        a->systems_scroll=(int64_t)scroll_clamp((double)a->systems_scroll,(double)systems_max_scroll(a));
        a->clip_active=true;a->clip=systems_viewport();
        for(int i=(int)(a->systems_scroll/126);i<a->systems_slot_count;i++){
            int y=422+(int)((int64_t)i*126-a->systems_scroll);if(y>=systems_viewport().y+systems_viewport().h)break;SystemSlot*slot=&a->systems_slots[i];
            Rect r={28,y,664,112};rounded(a,r,16,slot->active?ORANGE_DARK:PANEL);{uint32_t bar=slot->active?ORANGE:FILE_BLUE;gradient(a,(Rect){35,y+22,5,68},3,bar,bar);}
            ellipsis(a,52,y+14,608,1,slot->active?ORANGE:TEXT,slot->name);
            char description[160];snprintf(description,sizeof(description),"%s  /  %s",slot->id,tr(slot->active?"active":(slot->boot_tested?"saved system":"boot not yet tested")));ellipsis(a,52,y+62,608,0,MUTED,description);
            register_button(a,r,A_SYS_SLOT+i,ui_free(a)&&!slot->active&&systems_ready(a),0);
        }
        a->clip_active=false;scroll_indicator(a,(Rect){684,422,6,566},a->systems_scroll,(int64_t)a->systems_slot_count*126-14,ORANGE);
        if(!a->systems_slot_count)wrapped(a,40,450,640,1,MUTED,a->systems_empty?"Userdata is empty. The system you install becomes the only one, with the desktop, account, Wi-Fi, apps and setup script you choose.":a->systems_known&&a->systems_mounted?"No saved system slots yet. Import a prepared bundle to keep the current Linux alongside another system.":(a->systems_foreign?"Userdata holds Android's data, which Linux cannot use. A new system formats userdata when its installation starts: Android's apps and data are erased then, not before.":"Tap the card above to mount userdata. Systems are never mounted automatically."),6);
        if(!a->systems_platform&&a->systems_known)text_tile(a,1004,0,ORANGE,"The audited phone platform profile is unavailable. Import and switching remain disabled.",3);
        else installer_card(a,(Rect){28,1004,664,140},0,A_OS,"Install a new system","Erase userdata, then install from the internet or a file",A_SYS_NEW,ui_free(a));
        button(a,(Rect){28,1160,664,86},a->systems_empty?"Install a system":"Import a system",A_SYS_IMPORT,ui_free(a)&&systems_ready(a),true,0);
    }
    button(a,(Rect){28,1280,320,80},"Files / mounts",A_SYS_FILES,ui_free(a),false,0);
    button(a,(Rect){372,1280,320,80},"Refresh systems",A_SYS_REFRESH,ui_free(a),false,0);
    wrapped(a,40,1384,640,0,a->systems_reason[0]?RED:MUTED,a->systems_reason[0]?a->systems_reason:(a->systems_note[0]?a->systems_note:(a->systems_incomplete?"An incomplete import remains in /.a22x-systems/staging for inspection. No automatic cleanup is performed.":"Each system keeps its own /etc and /var. Shared /home requires matching account IDs. A full Linux backup includes all slots.")),3);
    if(a->systems_mount_sheet)render_systems_mount(a);
}
static void render_systems_setup(App*a){
    heading(a,"Install options","Choose what to set up in the new system.",true);
    rounded(a,(Rect){28,226,664,104},18,PANEL);text(a,52,244,0,ORANGE,"IMAGE");ellipsis(a,52,280,610,0,TEXT,a->systems_manifest);
    rounded(a,(Rect){28,346,664,398},18,PANEL);text(a,52,368,0,ORANGE,"DESKTOP");
    bool debian=systems_image_debian(a);
    for(unsigned int n=0;n<SYSTEMS_DESKTOPS;n++)button(a,(Rect){52+(int)(n%3)*212,404+(int)(n/3)*88,192,76},systems_desktop_labels[n],A_SYS_DESKTOP+(int)n,ui_free(a)&&(debian||!systems_desktop_debian(n)),a->systems_desktop==n,0);
    wrapped(a,52,670,610,0,MUTED,systems_desktop_debian(a->systems_desktop)?"Sxmo and Lomiri come from Debian and Ubuntu; other systems get Phosh.":
            (a->systems_desktop>1?"Installed over Wi-Fi: during the installation when recovery is connected, otherwise at the first start.":"Nothing is downloaded for the desktop."),2);
    rounded(a,(Rect){28,760,664,300},18,PANEL);text(a,52,782,0,ORANGE,"ACCOUNT");
    button(a,(Rect){52,822,296,84},"Keep mine",A_SYS_ACCOUNT_KEEP,ui_free(a)&&!systems_clean(a),!a->systems_new_account,0);
    button(a,(Rect){372,822,296,84},"New user",A_SYS_ACCOUNT_NEW,ui_free(a),a->systems_new_account,0);
    char who[96];if(a->systems_new_account)snprintf(who,sizeof(who),T("New user: %s"),a->systems_username);else copy(who,sizeof(who),"Your accounts and /home settings stay available.");
    wrapped(a,52,926,610,0,MUTED,who,2);
    if(a->systems_new_account&&!a->systems_password[0]&&a->systems_password_hash[0])wrapped(a,52,990,610,0,GREEN,"The password was set in the Android app.",2);
    else if(a->systems_new_account&&!a->systems_password[0])wrapped(a,52,990,610,0,ORANGE,"Tap New user to enter the password.",2);
    else wrapped(a,52,990,610,0,MUTED,systems_clean(a)?"A clean install needs a new account: userdata has none to keep.":"Make a backup first if you have not already.",2);
    render_locale_card(a,1076);
    button(a,(Rect){28,1330,664,108},"Next",A_SYS_SETUP_NEXT,ui_free(a)&&(!a->systems_new_account||(a->systems_username[0]&&(a->systems_password[0]||a->systems_password_hash[0]))),true,0);
}
static void render_systems_extras(App*a){
    heading(a,"Network and apps","Set up during the installation, or at the first start.",true);
    bool network=a->systems_wifi_ssid[0]!=0,script=a->systems_script_path[0]!=0;
    rounded(a,(Rect){28,226,664,188},18,PANEL);text(a,52,246,0,ORANGE,"WI-FI");
    ellipsis(a,52,282,610,1,network?TEXT:MUTED,network?a->systems_wifi_ssid:tr("No network"));
    if(network)text(a,a->systems_wifi_saved?500:560,250,0,MUTED,a->systems_wifi_saved?"Saved":(a->systems_wifi_password[0]?"WPA":"Open"));
    button(a,(Rect){52,330,network?296:616,68},network?"Change":"Enter network",A_SYS_WIFI,ui_free(a),!network,0);
    if(network)button(a,(Rect){372,330,296,68},"Remove",A_SYS_WIFI_CLEAR,ui_free(a),false,0);
    render_apps_card(a,430);
    rounded(a,(Rect){28,792,664,252},18,PANEL);text(a,52,812,0,ORANGE,"SETUP SCRIPT");
    ellipsis(a,52,848,610,1,script?TEXT:MUTED,script?a->systems_script_name:tr("None"));
    wrapped(a,52,898,610,0,MUTED,"Runs once as root after the desktop and apps: now if recovery is on Wi-Fi, else at the first start.",2);
    button(a,(Rect){52,962,script?296:616,64},script?"Choose another":"Choose a script",A_SYS_SCRIPT,ui_free(a),false,0);
    if(script)button(a,(Rect){372,962,296,64},"Remove",A_SYS_SCRIPT_CLEAR,ui_free(a),false,0);
    bool blocked=systems_clean(a)&&systems_needs_network(a)&&!network;
    const char*note=blocked?"Enter a Wi-Fi network: the desktop and apps are downloaded at the first start.":
        (!systems_needs_network(a)?"Nothing is downloaded.":(a->wifi_connected?"Recovery is on Wi-Fi: the desktop and apps are installed now, and the system starts ready.":
        (network?"The desktop and apps are downloaded over this network at the first start.":"Without a network here, the saved networks of installed systems are used.")));
    wrapped(a,40,1068,640,0,blocked?RED:MUTED,note,3);
    button(a,(Rect){28,1330,664,108},"Review installation",A_SYS_SETUP_NEXT,ui_free(a)&&!blocked,true,0);
}
/* After an installation: what was installed and where to go next. */
static void render_systems_done(App*a){
    heading(a,"Installation complete",a->systems_done_clean?"The new system is active.":"The new system is saved beside the current one.",true);
    icon_tile(a,A_OS,CW/2-56,250,112,ICON_FILL);
    centered(a,(Rect){28,388,664,56},1,TEXT,a->systems_done_name);
    rounded(a,(Rect){28,470,664,140},18,PANEL);
    ellipsis(a,52,494,610,0,TEXT,a->systems_done_desktop);ellipsis(a,52,544,610,0,TEXT,a->systems_done_network);
    wrapped(a,40,640,640,1,MUTED,a->systems_done_ready?(a->systems_done_clean?"It starts ready: recovery has installed the desktop, apps and account.":"It is ready: recovery has installed the desktop, apps and account. Switch to it in Systems to start it."):
            (a->systems_done_clean?"Its first start sets up the account, network, desktop and apps, then restarts once.":"Switch to it in Systems to start it."),4);
    if(!a->systems_done_ready&&a->systems_done_reason[0]){
        char why[320];snprintf(why,sizeof(why),T("Recovery could not set it up: %s"),tr(a->systems_done_reason));
        raw_text(true);wrapped(a,40,820,640,0,ORANGE,why,4);raw_text(false);
    }
    if(a->systems_done_clean)button(a,(Rect){28,1230,664,108},"Restart into the new system",A_SYS_DONE_RESTART,ui_free(a)&&a->allow_power,true,0);
    else button(a,(Rect){28,1230,664,108},"Open Systems",A_SYS_DONE_SYSTEMS,ui_free(a),true,0);
    button(a,(Rect){28,1358,664,86},"Main menu",A_SYS_DONE_HOME,true,false,0);
}
static bool systems_script_selected(const App*a){
    const FileVolume*v=a->volume_index>=0&&a->volume_index<a->volume_count?&a->volumes[a->volume_index]:NULL;
    bool saved=script_store_volume(v)&&v->mounted&&!strncmp(a->fs_selected.path,"/a22x-recovery/scripts/",23)&&!strchr(a->fs_selected.path+23,'/');
    return (systems_source_allowed(v)||saved)&&file_is_regular(&a->fs_selected)&&a->fs_selected.token[0]&&a->fs_selected.size<=SYSTEMS_SCRIPT_LIMIT;
}
static void render_systems_script(App*a){
    heading(a,"Setup script","Runs once as root at the first start of the new system.",true);
    rounded(a,(Rect){28,242,664,200},18,PANEL);wrapped(a,52,266,608,1,TEXT,a->fs_selected.name,2);
    char size[64];human_bytes(a->fs_selected.size,size,sizeof(size));text(a,52,384,0,MUTED,size);
    text(a,40,476,0,ORANGE,"SOURCE FILE");confirmation_path(a,40,522,640,a->fs_selected.path,a->systems_plan_page,8);
    unsigned int pages=path_pages(a->fs_selected.path,640,8);
    if(pages>1){button(a,(Rect){28,802,180,64},"Previous",A_SYS_PLAN_PREV,a->systems_plan_page>0,false,0);button(a,(Rect){512,802,180,64},"Next",A_SYS_PLAN_NEXT,a->systems_plan_page+1<pages,false,0);}
    bool fits=a->fs_selected.size<=SYSTEMS_SCRIPT_LIMIT;
    wrapped(a,40,908,640,1,fits?MUTED:RED,fits?"Recovery copies the script into the new system and does not run it. Read it first: it gets full control of the new system.":"A setup script can be at most 1 MiB.",6);
    if(fits)text_tile(a,1120,0,MUTED,"The script gets A22X_OS_ID and A22X_PACKAGE_MANAGER (apt, dnf, pacman or zypper) and can install packages with a22x-install.",4);
    button(a,(Rect){28,1330,664,108},"Use this script",A_SYS_USE_SCRIPT,ui_free(a)&&systems_script_selected(a)&&a->systems_plan_page+1>=pages,true,0);
}
static void render_systems_manifest(App*a){
    heading(a,"System image","An ARM64 ISO, disk image (raw, qcow2, VHD), Vagrant box or rootfs archive.",true);
    rounded(a,(Rect){28,242,664,200},18,PANEL);wrapped(a,52,266,608,1,TEXT,a->fs_selected.name,3);
    text(a,40,476,0,ORANGE,"SOURCE FILE");confirmation_path(a,40,522,640,a->fs_selected.path,a->systems_plan_page,8);
    unsigned int pages=path_pages(a->fs_selected.path,640,8);
    if(pages>1){button(a,(Rect){28,802,180,64},"Previous",A_SYS_PLAN_PREV,a->systems_plan_page>0,false,0);button(a,(Rect){512,802,180,64},"Next",A_SYS_PLAN_NEXT,a->systems_plan_page+1<pages,false,0);}
    bool known=systems_manifest_name(a->fs_selected.name)||systems_image_choice(a->fs_selected.name,(unsigned long long)a->fs_selected.size);
    if(file_is_regular(&a->fs_selected)&&!known)text_tile(a,908,1,RED,"This file is not a recognised system image. Supported: ISO, raw or img disk images, qcow2, VHD, Vagrant box and tar archives, also compressed with xz, zstd, gzip or bzip2.",6);
    else if(systems_clean(a))text_tile(a,908,1,MUTED,"Review the archive, phone platform adapter and free space before installation. Userdata is erased first; the new system is checked and then becomes the active one.",6);
    else text_tile(a,908,1,MUTED,"Review the archive, phone platform adapter and free space before installation. The installed root is validated before activation. Installation adds a separate system; it does not activate it.",6);
    button(a,(Rect){28,1330,664,108},"Continue",A_SYS_USE_MANIFEST,ui_free(a)&&systems_manifest_selected(a)&&a->systems_plan_page+1>=pages,true,0);
}
static unsigned int systems_plan_pages(const App*a){return a->systems_plan_kind==J_SYS_IMPORT_PLAN?path_pages(a->systems_manifest,608,4):1;}
static void render_systems_plan(App*a){
    bool importing=a->systems_plan_kind==J_SYS_IMPORT_PLAN,clean=importing&&a->systems_plan_clean;
    heading(a,importing?(clean?"Clean install":"Install system"):"Switch system",importing?(clean?"The new system becomes the only one on userdata.":"Add a new slot alongside the current Linux."):"Keep the previous system available for rollback.",true);
    /* 2026-11-11, in cards as the other pages: what is installed, where it comes from, the room it
       needs, what happens to userdata, and the choices made on the pages before. */
    unsigned int pages=systems_plan_pages(a);
    rounded(a,(Rect){28,242,664,190},24,PANEL);text(a,52,260,0,ORANGE,importing?"NEW SYSTEM":"SWITCH TO");
    wrapped(a,52,300,608,1,TEXT,a->systems_plan_name,2);ellipsis(a,52,388,608,0,MUTED,a->systems_plan_slot);
    if(importing){
        rounded(a,(Rect){28,448,664,222},24,PANEL);
        if(a->systems_remote_id[0]){
            /* From the catalog: downloaded and checked when the installation runs. */
            char download[64],line[160];human_bytes(a->systems_plan_download,download,sizeof(download));
            text(a,52,466,0,ORANGE,"FROM THE CATALOG");raw_text(true);wrapped(a,52,506,616,1,TEXT,a->systems_manifest,2);raw_text(false);
            snprintf(line,sizeof(line),T("Download %s over Wi-Fi, checked against the catalog"),download);wrapped(a,52,596,616,0,MUTED,line,2);
        }else if(!strcmp(a->systems_source_volume,"userdata")){
            /* An image saved on the phone (2026-11-10): it stays in the store after the installation. */
            const char*name=strrchr(a->systems_manifest,'/');
            text(a,52,466,0,ORANGE,"SAVED ON THE PHONE");raw_text(true);wrapped(a,52,506,616,1,TEXT,name?name+1:a->systems_manifest,2);raw_text(false);
            wrapped(a,52,596,616,0,MUTED,"No SD card is needed; the image stays saved after the installation.",2);
        }else{
            text(a,52,466,0,ORANGE,"SOURCE MANIFEST");raw_text(true);ellipsis(a,pages>1?52:52,506,pages>1?400:616,0,MUTED,a->systems_source_volume);raw_text(false);
            confirmation_path(a,52,546,608,a->systems_manifest,a->systems_plan_page,4);
            /* A path longer than the card is read in pages, beside the name of its volume. */
            if(pages>1){button(a,(Rect){468,458,100,52},"↑",A_SYS_PLAN_PREV,a->systems_plan_page>0,false,0);button(a,(Rect){576,458,100,52},"↓",A_SYS_PLAN_NEXT,a->systems_plan_page+1<pages,false,0);}
        }
        char needed[64],free_space[64];human_bytes(a->systems_required_bytes,needed,sizeof(needed));human_bytes(a->systems_available_bytes,free_space,sizeof(free_space));
        /* The labels are translated before the sizes are drawn as they are. */
        const char*needed_label=tr("NEEDED"),*available_label=tr("AVAILABLE");
        raw_text(true);stat_tile(a,(Rect){28,686,324,100},needed_label,needed,TEXT);stat_tile(a,(Rect){368,686,324,100},available_label,free_space,GREEN);raw_text(false);
        int y=818;
        if(a->systems_plan_erase)y+=text_tile(a,y,0,RED,a->systems_plan_format?"Userdata is formatted for Linux first: Android's apps and data on it are erased. The new system is active after installation.":
                                         "Userdata is erased first: every Linux system and /home on it. The new system is active after installation.",4)+16;
        else y+=text_tile(a,y,0,MUTED,clean?"Userdata is empty, so nothing is replaced. The new system is active after installation: restart into it from Power.":"The current Linux stays active. A separate review is required to switch. Existing BOOT and shared /home are retained.",4)+16;
        /* The plan takes a Linux BOOT with it (2026-11-11): said where the owner decides. */
        if(a->systems_plan_boot)y+=text_tile(a,y,0,MUTED,"BOOT gets the Linux kernel when the system is in place; the BOOT there now is kept on cache.",2)+16;
        if(!systems_manifest_name(a->systems_manifest)){
            char line[256];unsigned int apps=0;for(unsigned int n=0;n<SYSTEMS_APPS;n++)apps+=(a->systems_apps>>n)&1u;
            int rows=shells_offered(a)?4:3;rounded(a,(Rect){28,y-16,664,24+rows*38},24,PANEL);
            snprintf(line,sizeof(line),T("Desktop: %s  /  apps: %u"),tr(systems_desktop_labels[a->systems_desktop<SYSTEMS_DESKTOPS?a->systems_desktop:0]),apps);ellipsis(a,52,y-2,616,0,TEXT,line);
            snprintf(line,sizeof(line),T("Wi-Fi: %s  /  script: %s"),a->systems_wifi_ssid[0]?a->systems_wifi_ssid:"—",a->systems_script_name[0]?a->systems_script_name:"—");ellipsis(a,52,y+36,616,0,TEXT,line);
            if(shells_offered(a)){shells_summary(a,line,sizeof(line));ellipsis(a,52,y+74,616,0,TEXT,line);}
            locale_summary(a,line,sizeof(line));ellipsis(a,52,y+(shells_offered(a)?112:74),616,0,TEXT,line);
        }
    }else{
        rounded(a,(Rect){28,448,664,150},24,PANEL);text(a,52,466,0,ORANGE,"CURRENT SYSTEM");wrapped(a,52,506,616,1,TEXT,a->systems_active,2);
        int y=630;
        y+=text_tile(a,y,0,MUTED,"The installed root filesystem changes. /home stays shared; each system retains its own /etc and /var. BOOT is retained.",5)+16;
        text_tile(a,y,0,ORANGE,"The switch is journaled, not fully atomic. If interrupted, return to Systems and choose Resume or Rollback. Linux boot remains blocked until the journal completes.",6);
        /* A saved system that is not running can also be deleted from here (2026-11-09). */
        if(pages<=1)button(a,(Rect){28,1212,664,96},"Remove this system",A_SYS_REMOVE,ui_free(a)&&systems_ready(a),false,0);
    }
    bool current=(uint64_t)time(NULL)<=a->systems_plan_expires;
    if(!current)text(a,40,1286,0,RED,"This plan expired. Return and review it again.");
    button(a,(Rect){28,1330,664,108},importing?"Hold 2 seconds to install":"Hold 3 seconds to switch",A_SYS_PLAN_CONFIRM,ui_free(a)&&a->systems_plan_valid&&current&&a->systems_plan_page+1>=pages,true,importing?2000:3000);
}
/* Delete a saved system that is not running (backend remove-slot): its root filesystem only. */
static void render_systems_remove(App*a){
    heading(a,"Remove system","Delete a saved system that is not running.",true);
    rounded(a,(Rect){28,242,664,250},18,PANEL);text(a,52,266,0,ORANGE,"SYSTEM");
    raw_text(true);wrapped(a,52,310,608,1,TEXT,a->systems_plan_name[0]?a->systems_plan_name:a->systems_plan_slot,2);ellipsis(a,52,420,608,0,MUTED,a->systems_plan_slot);raw_text(false);
    rounded(a,(Rect){28,508,664,150},24,PANEL);text(a,52,526,0,ORANGE,"CURRENT SYSTEM");wrapped(a,52,566,616,1,TEXT,a->systems_active,2);
    text_tile(a,692,1,RED,"Its root filesystem with its programs, /etc and /var is deleted. /home, the active system and BOOT stay. This cannot be undone.",5);
    button(a,(Rect){28,1330,664,108},"Hold 3 seconds to remove",A_SYS_REMOVE_CONFIRM,ui_free(a)&&systems_ready(a)&&systems_id(a->systems_plan_slot),true,3000);
}
static void render_systems_recover(App*a){
    bool rollback=!strcmp(a->systems_direction,"rollback");heading(a,rollback?"Roll back switch":"Resume switch","Review the current interrupted transaction.",true);
    rounded(a,(Rect){28,242,664,430},18,PANEL);text(a,52,266,0,ORANGE,"FROM");wrapped(a,52,310,608,1,TEXT,a->systems_source,2);
    text(a,52,424,0,ORANGE,"TO");wrapped(a,52,466,608,1,TEXT,a->systems_target,2);
    char state[128];snprintf(state,sizeof(state),T("Journal step %u of %u"),a->systems_step,a->systems_total);text(a,52,600,0,MUTED,state);
    wrapped(a,40,734,640,1,TEXT,rollback?"Restore the previous root filesystem using the saved transaction. Shared /home remains in place.":"Finish moving the reviewed root directories into place. Shared /home remains in place.",5);
    text_tile(a,980,0,MUTED,"This is the exact transaction read just before this review. If it changes, the backend refuses this action. The phone does not reboot automatically.",5);
    bool allowed=rollback?a->systems_rollback_allowed:a->systems_resume_allowed;
    button(a,(Rect){28,1330,664,108},rollback?"Hold 3 seconds to roll back":"Hold 3 seconds to resume",A_SYS_RECOVER_CONFIRM,ui_free(a)&&a->systems_known&&a->systems_mounted&&!a->systems_read_only&&allowed&&a->systems_expected_transaction[0]&&!strcmp(a->systems_expected_transaction,a->systems_transaction),true,3000);
}
static const char*systems_operation(int kind){switch(kind){case J_SYS_STATUS:return "status";case J_SYS_IMPORT_PLAN:return "import-plan";case J_SYS_SWITCH_PLAN:return "switch-plan";case J_SYS_IMPORT:return "import";case J_SYS_SWITCH:return "switch";case J_SYS_RECOVER:return "recover";case J_SYS_REMOVE:return "remove-slot";default:return "";}}
static bool start_systems_job(App*a,int kind){
    if(a->job.pid||a->preview||!systems_job(kind))return false;
    if(!a->distro_helper[0]){files_problem(a,"SYSTEMS_UNAVAILABLE","The system manager helper is not configured.",SYSTEMS);return false;}
    char*args[208];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}args[n++]=(char*)systems_operation(kind);
    if(kind==J_SYS_IMPORT_PLAN||kind==J_SYS_SWITCH_PLAN){
        if(!systems_ready(a)&&!(kind==J_SYS_IMPORT_PLAN&&a->systems_wipe&&systems_fresh(a))){files_problem(a,"SYSTEMS_NOT_READY","Mount userdata with write access and refresh Systems before planning.",SYSTEMS);return false;}
        /* The note of the last action (a removed system) stayed under the next one (2026-11-09). */
        systems_forget_plan(a);a->systems_plan_kind=kind;a->systems_note[0]=0;
        if(!private_workdir(a->workdir)||snprintf(a->systems_plan_path,sizeof(a->systems_plan_path),"%s/distro-%ld-%u.json",a->workdir,(long)getpid(),++a->plan_sequence)>=(int)sizeof(a->systems_plan_path)){a->systems_plan_path[0]=0;files_problem(a,"WORKDIR_UNAVAILABLE","A private RAM directory is required for this reviewed plan.",SYSTEMS);return false;}
        args[n++]="--output";args[n++]=a->systems_plan_path;
        if(kind==J_SYS_IMPORT_PLAN){
            /* An image of the catalog is downloaded during the installation; a new system erases userdata first. */
            if(a->systems_remote_id[0]){args[n++]="--remote";args[n++]=a->systems_remote_id;}
            else{args[n++]="--source-volume";args[n++]=a->systems_source_volume;args[n++]="--manifest";args[n++]=a->systems_manifest;args[n++]="--expected-source-token";args[n++]=a->systems_source_token;}
            if(a->systems_wipe)args[n++]="--wipe";
            if(!systems_manifest_name(a->systems_manifest)){
                args[n++]="--desktop";args[n++]=(char*)systems_desktops[a->systems_desktop];args[n++]="--account";args[n++]=a->systems_new_account?"new":"keep";
                if(a->systems_new_account){
                    /* Typed here, or a SHA-512 hash from the Android app's plan (2026-11-10). */
                    bool hashed=!a->systems_password[0]&&a->systems_password_hash[0];const char*secret=hashed?a->systems_password_hash:a->systems_password;
                    if(snprintf(a->systems_password_file,sizeof(a->systems_password_file),"%s/account-%ld",a->workdir,(long)getpid())>=(int)sizeof(a->systems_password_file))return false;
                    int fd=open(a->systems_password_file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
                    if(fd<0)return false;
                    bool written=write(fd,secret,strlen(secret))==(ssize_t)strlen(secret);close(fd);
                    if(!written)return false;
                    args[n++]="--username";args[n++]=a->systems_username;args[n++]=hashed?"--password-hash-file":"--password-file";args[n++]=a->systems_password_file;
                }
                if(a->systems_wifi_ssid[0]){
                    /* The network and its password go through a private RAM file, like the account password. */
                    if(snprintf(a->systems_wifi_file,sizeof(a->systems_wifi_file),"%s/wifi-%ld",a->workdir,(long)getpid())>=(int)sizeof(a->systems_wifi_file))return false;
                    int fd=open(a->systems_wifi_file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
                    if(fd<0){a->systems_wifi_file[0]=0;return false;}
                    char line[256];int length=snprintf(line,sizeof(line),"%s\n%s\n%s",a->systems_wifi_ssid,a->systems_wifi_password,a->systems_wifi_saved?"saved\n":"");
                    bool written=length>0&&length<(int)sizeof(line)&&write(fd,line,(size_t)length)==(ssize_t)length;
                    memset(line,0,sizeof(line));close(fd);
                    if(!written){unlink(a->systems_wifi_file);a->systems_wifi_file[0]=0;return false;}
                    args[n++]="--wifi-file";args[n++]=a->systems_wifi_file;
                }
                for(unsigned int i=0;i<SYSTEMS_APPS;i++)if((a->systems_apps>>i)&1u){args[n++]="--app";args[n++]=(char*)systems_app_ids[i];}
                for(unsigned int i=0;i<a->systems_package_count;i++){args[n++]="--package";args[n++]=a->systems_packages[i];}
                n=shells_append_args(a,args,n);
                n=locale_append_args(a,args,n);
                if(a->systems_script_path[0]){args[n++]="--script-volume";args[n++]=a->systems_script_volume;args[n++]="--script";args[n++]=a->systems_script_path;
                    if(a->systems_script_token[0]){args[n++]="--expected-script-token";args[n++]=a->systems_script_token;}}
            }
        }
        else{if(!systems_id(a->systems_plan_slot))return false;args[n++]="--slot";args[n++]=a->systems_plan_slot;}
    }else if(kind==J_SYS_IMPORT||kind==J_SYS_SWITCH){
        if(!a->systems_plan_valid||kind!=(a->systems_plan_kind==J_SYS_IMPORT_PLAN?J_SYS_IMPORT:J_SYS_SWITCH)||!valid_sha256(a->systems_plan_seal)||!valid_sha256(a->systems_plan_hash))return false;
        args[n++]="--plan-file";args[n++]=a->systems_plan_path;args[n++]="--expected-plan-seal";args[n++]=a->systems_plan_seal;args[n++]="--expected-plan-file-sha256";args[n++]=a->systems_plan_hash;args[n++]="--confirm";a->systems_plan_valid=false;
        if(kind==J_SYS_SWITCH)a->systems_reboot_blocked=true;
    }else if(kind==J_SYS_REMOVE){
        if(!systems_ready(a)||!systems_id(a->systems_plan_slot))return false;
        args[n++]="--slot";args[n++]=a->systems_plan_slot;args[n++]="--confirm";
    }else if(kind==J_SYS_RECOVER){
        if(!a->systems_known||!a->systems_mounted||a->systems_read_only||!systems_id(a->systems_expected_transaction)||strcmp(a->systems_expected_transaction,a->systems_transaction))return false;
        bool rollback=!strcmp(a->systems_direction,"rollback");if((rollback&&!a->systems_rollback_allowed)||(!rollback&&(strcmp(a->systems_direction,"resume")||!a->systems_resume_allowed)))return false;
        args[n++]="--direction";args[n++]=a->systems_direction;args[n++]="--expected-transaction";args[n++]=a->systems_expected_transaction;args[n++]="--confirm";a->systems_reboot_blocked=true;
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;a->result_return=SYSTEMS;return true;
}
static bool parse_systems_status(App*a,const Json*j){
    if(!jequals(j,0,"schema","a22x.distro.status.v1")||!systems_array_exact(j,0,"shared_paths","/home")||json_bool(j,json_get(j,0,"retained_boot"),-1)!=1||json_bool(j,json_get(j,0,"whole_switch_atomic"),-1)!=0)return false;
    int mounted=json_bool(j,json_get(j,0,"mounted"),-1),ro=json_bool(j,json_get(j,0,"read_only"),-1),profile=json_bool(j,json_get(j,0,"platform_profile_available"),-1),blocked=json_bool(j,json_get(j,0,"reboot_blocked"),-1);
    if(mounted<0||ro<0||profile<0||blocked<0)return false;
    char volume[256],active[80];jstring(j,0,"volume_token",volume,sizeof(volume));jstring(j,0,"active",active,sizeof(active));if(!volume[0])return false;
    int array=json_get(j,0,"slots");if(array<0||j->tokens[array].type!=J_ARRAY)return false;SystemSlot slots[16];int count=0,actives=0;
    for(int i=array+1;i<json_next(j,array);i=json_next(j,i)){
        if(count>=16||j->tokens[i].type!=J_OBJECT)return false;
        SystemSlot*s=&slots[count];memset(s,0,sizeof(*s));
        jstring(j,i,"id",s->id,sizeof(s->id));jstring(j,i,"name",s->name,sizeof(s->name));int selected=json_bool(j,json_get(j,i,"active"),-1),tested=json_bool(j,json_get(j,i,"hardware_boot_tested"),-1);
        if(!systems_id(s->id)||!s->name[0]||selected<0||tested<0)return false;
        for(int previous=0;previous<count;previous++)if(!strcmp(slots[previous].id,s->id))return false;
        s->active=selected;s->boot_tested=tested;if(selected){if(strcmp(active,s->id))return false;actives++;}count++;
    }
    int initialized=json_bool(j,json_get(j,0,"initialized"),-1);if(mounted&&initialized<0)return false;if(initialized==1&&(actives!=1||!systems_id(active)))return false;if(initialized!=1&&(count||active[0]))return false;
    char transaction[80]="",source[80]="",target[80]="";uint64_t step=0,total=0;bool resume=false,rollback=false;int tx=json_get(j,0,"transaction");
    if(tx<0)return false;
    if(j->tokens[tx].type==J_OBJECT){
        jstring(j,tx,"id",transaction,sizeof(transaction));jstring(j,tx,"source",source,sizeof(source));jstring(j,tx,"target",target,sizeof(target));step=json_uint(j,json_get(j,tx,"step"),UINT64_MAX);total=json_uint(j,json_get(j,tx,"total"),UINT64_MAX);
        resume=systems_array_has(j,tx,"actions","resume");rollback=systems_array_has(j,tx,"actions","rollback");
        if(!mounted||initialized!=1||!systems_id(transaction)||!systems_id(source)||!systems_id(target)||step>total||!total||total>256||!blocked||!jequals(j,0,"transaction_state","pending")||(!resume&&!rollback))return false;
    }else if(j->tokens[tx].type!=J_PRIMITIVE||j->tokens[tx].end-j->tokens[tx].start!=4||memcmp(j->text+j->tokens[tx].start,"null",4))return false;
    else if(!jequals(j,0,"transaction_state",mounted?"none":"unknown-unmounted"))return false;
    a->systems_known=true;a->systems_mounted=mounted;a->systems_read_only=ro;a->systems_initialized=initialized==1;a->systems_platform=profile;
    a->systems_empty=mounted&&initialized==0&&json_bool(j,json_get(j,0,"empty_userdata"),0)==1;
    a->systems_foreign=!mounted&&json_bool(j,json_get(j,0,"foreign"),0)==1;
    a->systems_boot_android=json_bool(j,json_get(j,0,"boot_linux"),1)==0;a->systems_boot_source[0]=0;jstring(j,0,"boot_source",a->systems_boot_source,sizeof(a->systems_boot_source));a->systems_boot_outdated=json_bool(j,json_get(j,0,"boot_outdated"),0)==1;
    if(mounted||blocked)a->systems_reboot_blocked=blocked;
    copy(a->systems_volume_token,sizeof(a->systems_volume_token),volume);copy(a->systems_active,sizeof(a->systems_active),active);copy(a->systems_transaction,sizeof(a->systems_transaction),transaction);copy(a->systems_source,sizeof(a->systems_source),source);copy(a->systems_target,sizeof(a->systems_target),target);
    a->systems_incomplete=0;int incomplete=json_get(j,0,"incomplete_imports");if(incomplete>=0&&j->tokens[incomplete].type==J_ARRAY)for(int i=incomplete+1;i<json_next(j,incomplete);i=json_next(j,i))a->systems_incomplete++;
    a->systems_resume_allowed=resume;a->systems_rollback_allowed=rollback;a->systems_step=(unsigned)step;a->systems_total=(unsigned)total;
    memcpy(a->systems_slots,slots,(size_t)count*sizeof(*slots));a->systems_slot_count=count;a->systems_scroll=(int64_t)scroll_clamp((double)a->systems_scroll,(double)systems_max_scroll(a));
    a->button_count=0;a->focus=-1;cancel_pointer(a);return true;
}
static bool parse_systems_plan(App*a,const Json*j,int kind){
    char path[PATH_LEN],hash[80],seal[80],slot[80],name[sizeof(a->systems_plan_name)],confirmation[384];
    jstring(j,0,"plan_path",path,sizeof(path));jstring(j,0,"plan_file_sha256",hash,sizeof(hash));int p=json_get(j,0,"plan");
    if(p<0||j->tokens[p].type!=J_OBJECT||!json_bool(j,json_get(j,0,"plan_summary"),0)||strcmp(path,a->systems_plan_path)||!valid_sha256(hash)||!jequals(j,p,"schema","a22x.distro.plan.v1")||!jequals(j,p,"mode",a->device?"device":"fixture")||!jequals(j,p,"operation",kind==J_SYS_IMPORT_PLAN?"import":"switch"))return false;
    jstring(j,p,"seal",seal,sizeof(seal));jstring(j,p,"slot",slot,sizeof(slot));jstring(j,p,"name",name,sizeof(name));jstring(j,p,"confirmation",confirmation,sizeof(confirmation));
    uint64_t expires=json_uint(j,json_get(j,p,"expires_utc"),0),created=json_uint(j,json_get(j,p,"created_utc"),UINT64_MAX);if(!valid_sha256(seal)||!systems_id(slot)||!name[0]||!confirmation[0]||expires<(uint64_t)time(NULL)||created>expires||expires-created>3600)return false;
    uint64_t required=0,available=0;
    if(kind==J_SYS_IMPORT_PLAN){
        char volume[128],manifest[PATH_LEN];jstring(j,p,"source_volume",volume,sizeof(volume));jstring(j,p,"manifest_path",manifest,sizeof(manifest));required=json_uint(j,json_get(j,p,"required_bytes"),UINT64_MAX);available=json_uint(j,json_get(j,p,"available_bytes"),UINT64_MAX);
        bool remote=a->systems_remote_id[0]!=0;
        if((!remote&&(strcmp(volume,a->systems_source_volume)||strcmp(manifest,a->systems_manifest)))||(remote&&(volume[0]||manifest[0]))||!required||required>available||available==UINT64_MAX||json_bool(j,json_get(j,0,"switches_current_system"),-1)!=0)return false;
        a->systems_plan_erase=json_bool(j,json_get(j,p,"erase_userdata"),0)==1;a->systems_plan_format=json_bool(j,json_get(j,p,"format_userdata"),0)==1;a->systems_plan_boot=json_bool(j,json_get(j,p,"write_boot"),0)==1;a->systems_plan_download=json_uint(j,json_get(j,p,"download_bytes"),0);
        if(a->systems_wipe!=a->systems_plan_erase)return false;
    }else if(strcmp(slot,a->systems_plan_slot)||!systems_array_exact(j,p,"preserved_paths","/home")||json_bool(j,json_get(j,p,"whole_switch_atomic"),-1)!=0||json_bool(j,json_get(j,0,"retained_boot"),-1)!=1||json_bool(j,json_get(j,0,"writes_partition_table"),-1)!=0)return false;
    struct stat st;if(lstat(path,&st)<0||!S_ISREG(st.st_mode)||st.st_uid!=geteuid()||st.st_nlink!=1||(st.st_mode&0777)!=0600||st.st_size<=0||st.st_size>4*1024*1024)return false;
    copy(a->systems_plan_hash,sizeof(a->systems_plan_hash),hash);copy(a->systems_plan_seal,sizeof(a->systems_plan_seal),seal);copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),slot);copy(a->systems_plan_name,sizeof(a->systems_plan_name),name);copy(a->systems_confirmation,sizeof(a->systems_confirmation),confirmation);
    a->systems_plan_clean=kind==J_SYS_IMPORT_PLAN&&json_bool(j,json_get(j,p,"clean_install"),0)==1;
    a->systems_required_bytes=required;a->systems_available_bytes=available;a->systems_plan_expires=expires;a->systems_plan_valid=true;a->systems_plan_page=0;screen(a,SYSTEMS_PLAN);return true;
}
static int complete_systems_job(App*a,const Json*j,int kind,bool ok){
    if(a->systems_password_file[0]){unlink(a->systems_password_file);a->systems_password_file[0]=0;}
    if(a->systems_wifi_file[0]){unlink(a->systems_wifi_file);a->systems_wifi_file[0]=0;}
    if(!ok){
        char code[96]="SYSTEMS_FAILED",message[1024]="The system manager did not return a valid result.";if(j){int e=json_get(j,0,"error");jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));
            /* The file a check stopped at goes beside the code (PLATFORM_METADATA_MISMATCH said "lib" only in the backend's JSON, 2026-11-10). */
            char where[160]={0};jstring(j,json_get(j,e,"details"),"path",where,sizeof(where));size_t used=strlen(code);
            if(where[0]&&used+4<sizeof(code))snprintf(code+used,sizeof(code)-used," · %s",where);}
        a->systems_review_recovery=false;a->systems_plan_valid=false;copy(a->systems_reason,sizeof(a->systems_reason),message);
        /* 2026-11-08: a refused status reopened the error page from its own Back button forever;
           the reason is shown on the Systems page instead, which can be left. */
        if(kind==J_SYS_STATUS){a->systems_known=false;if(a->screen!=APLAN&&a->screen!=BACKUP&&a->screen!=ANDROID_IMAGE)screen(a,SYSTEMS);else a->dirty=true;return J_NONE;}
        /* A system that cannot be switched to can still be removed, unless it is the running one. */
        a->systems_remove_offer=kind==J_SYS_SWITCH_PLAN&&systems_id(a->systems_plan_slot)&&strcmp(code,"ALREADY_ACTIVE")&&strcmp(code,"SWITCH_PENDING")&&strcmp(code,"SLOT_UNAVAILABLE");
        files_problem(a,code,message,SYSTEMS);return J_NONE;
    }
    if(!jequals(j,0,"mode",a->device?"device":"fixture")||!jequals(j,0,"operation",systems_operation(kind))){files_problem(a,"INVALID_SYSTEMS_RESULT","The operation or environment did not match the requested system action.",SYSTEMS);a->systems_known=a->systems_plan_valid=false;return J_NONE;}
    if(kind==J_SYS_STATUS){
        if(!parse_systems_status(a,j)){a->systems_known=false;a->systems_review_recovery=false;files_problem(a,"INVALID_SYSTEMS_STATUS","Refresh before choosing a system. The full status was not confirmed.",SYSTEMS);return J_NONE;}
        a->systems_reason[0]=0;
        if(a->systems_review_recovery){a->systems_review_recovery=false;if(strcmp(a->systems_expected_transaction,a->systems_transaction)){files_problem(a,"TRANSACTION_CHANGED","The interrupted transaction changed. Review the refreshed Systems screen.",SYSTEMS);return J_NONE;}screen(a,SYSTEMS_RECOVER);}
        /* The refresh after a userdata mount from New system or the catalog keeps that page:
           it used to jump to Systems, so the mount there seemed to do nothing (2026-11-08, on the phone). */
        else if(a->screen==SYSTEMS_NEW||a->screen==SYSTEMS_CATALOG||a->screen==APLAN||a->screen==BACKUP||a->screen==ANDROID_IMAGE||a->screen==SYSTEMS_IMAGES)a->dirty=true;
        else screen(a,SYSTEMS);
        return J_NONE;
    }
    if(kind==J_SYS_IMPORT_PLAN||kind==J_SYS_SWITCH_PLAN){if(!parse_systems_plan(a,j,kind)){a->systems_plan_valid=false;files_problem(a,"INVALID_SYSTEMS_PLAN","The exact private plan file, source, target or preservation policy was not confirmed.",SYSTEMS);}return J_NONE;}
    if(kind==J_SYS_REMOVE){
        char removed[80];jstring(j,0,"removed_slot",removed,sizeof(removed));bool done=removed[0]&&!strcmp(removed,a->systems_plan_slot);
        a->systems_known=false;a->successful=done;
        copy(a->systems_note,sizeof(a->systems_note),done?"The system was removed. /home and the active system stay.":"The removal was not confirmed. Refresh Systems.");
        screen(a,SYSTEMS);return J_SYS_STATUS;
    }
    bool verified=false;
    bool clean=kind==J_SYS_IMPORT&&a->systems_plan_clean;
    if(kind==J_SYS_IMPORT){char installed[80],active[80];jstring(j,0,"installed_slot",installed,sizeof(installed));jstring(j,0,"active",active,sizeof(active));
        verified=!strcmp(installed,a->systems_plan_slot)&&(clean?json_bool(j,json_get(j,0,"switched"),-1)==1&&!strcmp(active,installed):json_bool(j,json_get(j,0,"switched"),-1)==0);}
    else{char active[80];jstring(j,0,"active",active,sizeof(active));verified=json_bool(j,json_get(j,0,"home_preserved"),-1)==1&&json_bool(j,json_get(j,0,"retained_boot"),-1)==1&&json_bool(j,json_get(j,0,"reboot_blocked"),-1)==0;
        if(kind==J_SYS_SWITCH)verified=verified&&!strcmp(active,a->systems_plan_slot)&&jequals(j,0,"status","committed");
        else{char tx[80];jstring(j,0,"transaction_id",tx,sizeof(tx));verified=verified&&!strcmp(tx,a->systems_expected_transaction)&&!strcmp(active,!strcmp(a->systems_direction,"rollback")?a->systems_source:a->systems_target)&&jequals(j,0,"status",!strcmp(a->systems_direction,"rollback")?"rolled-back":"committed");}}
    systems_forget_plan(a);a->systems_known=false;a->systems_reason[0]=0;a->successful=verified;
    if(verified&&kind==J_SYS_IMPORT){
        char name[sizeof(a->systems_done_name)];jstring(j,0,"name",name,sizeof(name));copy(a->systems_done_name,sizeof(a->systems_done_name),name[0]?name:a->systems_plan_name);
        unsigned int apps=0;for(unsigned int n=0;n<SYSTEMS_APPS;n++)apps+=(a->systems_apps>>n)&1u;
        {char label[96];unsigned int more=0;for(unsigned int m=shells_extra(a);m;m>>=1)more+=m&1u;
         if(more)snprintf(label,sizeof(label),"%s +%u",tr(systems_desktop_labels[a->systems_desktop<SYSTEMS_DESKTOPS?a->systems_desktop:0]),more);
         else copy(label,sizeof(label),tr(systems_desktop_labels[a->systems_desktop<SYSTEMS_DESKTOPS?a->systems_desktop:0]));
         snprintf(a->systems_done_desktop,sizeof(a->systems_done_desktop),T("Desktop: %s  /  apps: %u"),label,apps);}
        snprintf(a->systems_done_network,sizeof(a->systems_done_network),T("Wi-Fi: %s  /  script: %s"),a->systems_wifi_ssid[0]?a->systems_wifi_ssid:"—",a->systems_script_name[0]?a->systems_script_name:"—");
        a->systems_done_clean=clean;a->systems_done_ready=json_bool(j,json_get(j,0,"provisioned"),0);
        /* The plan from the Android app is done: deleted from cache right after (2026-11-10). */
        if(a->aplan_used){a->aplan_used=false;a->aplan_forget_after=a->aplan_sha[0]!=0;}
        /* Why recovery left the setup to the first start, when it tried (2026-11-08). */
        a->systems_done_reason[0]=0;jstring(j,0,"provision_error",a->systems_done_reason,sizeof(a->systems_done_reason));
        if(!a->systems_done_ready&&!a->systems_done_reason[0]&&json_uint(j,json_get(j,0,"provision_status"),0)==3)copy(a->systems_done_reason,sizeof(a->systems_done_reason),"A package download failed in recovery.");
        {char how[24]={0};jstring(j,0,"provision",how,sizeof(how));
         fprintf(stderr,"[recovery-ui:systems-import] provision=%s ready=%d reason=%s\n",how[0]?how:"-",a->systems_done_ready,a->systems_done_reason[0]?a->systems_done_reason:"-");}
        systems_forget_choices(a);screen(a,SYSTEMS_DONE);return J_NONE;
    }
    copy(a->systems_note,sizeof(a->systems_note),verified?(clean?"Installation completed. The new system is active: restart into it from Power. Its first start sets up the account, network, desktop and apps.":kind==J_SYS_IMPORT?"Installation completed. Select the new slot to review switching; it has not been boot-tested.":"The system transaction completed. Read the refreshed active system before restarting Linux."):"The action returned an incomplete receipt. Refresh and inspect the actual system state before another operation.");
    screen(a,SYSTEMS);return J_SYS_STATUS;
}
/* Both fields share the one edit buffer: the focused one is in it, the other is kept aside. */
static void systems_account_focus(App*a,int kind){
    if(a->fs_prompt_kind==J_SYS_ACCOUNT_USER)copy(a->systems_username,sizeof(a->systems_username),a->fs_prompt);
    else if(a->fs_prompt_kind==J_SYS_ACCOUNT_PASS)copy(a->systems_password,sizeof(a->systems_password),a->fs_prompt);
    a->fs_prompt_kind=kind;copy(a->fs_prompt,sizeof(a->fs_prompt),kind==J_SYS_ACCOUNT_USER?a->systems_username:a->systems_password);
    a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;a->dirty=true;
}
static void systems_account_prompt(App*a,int kind){a->fs_prompt_kind=0;systems_account_focus(a,kind);screen(a,FILE_PROMPT);}
static void systems_account_close(App*a,bool keep){
    if(!keep&&!a->systems_new_account){memset(a->systems_username,0,sizeof(a->systems_username));memset(a->systems_password,0,sizeof(a->systems_password));}
    if(keep&&a->systems_password[0])memset(a->systems_password_hash,0,sizeof(a->systems_password_hash));
    memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->fs_prompt_kind=0;a->systems_new_account=a->systems_new_account||keep;screen(a,SYSTEMS_SETUP);
}
/* The Wi-Fi page works like the account page: one edit buffer, the other field kept aside. */
static void systems_wifi_focus(App*a,int kind){
    if(a->fs_prompt_kind==J_SYS_WIFI_SSID)copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),a->fs_prompt);
    else if(a->fs_prompt_kind==J_SYS_WIFI_PASS)copy(a->systems_wifi_password,sizeof(a->systems_wifi_password),a->fs_prompt);
    a->fs_prompt_kind=kind;copy(a->fs_prompt,sizeof(a->fs_prompt),kind==J_SYS_WIFI_SSID?a->systems_wifi_ssid:a->systems_wifi_password);
    a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;a->dirty=true;
}
static void systems_wifi_prompt(App*a){
    copy(a->systems_wifi_old_ssid,sizeof(a->systems_wifi_old_ssid),a->systems_wifi_ssid);copy(a->systems_wifi_old_password,sizeof(a->systems_wifi_old_password),a->systems_wifi_password);
    a->systems_wifi_old_saved=a->systems_wifi_saved;a->systems_wifi_saved=false;
    a->fs_prompt_kind=0;systems_wifi_focus(a,J_SYS_WIFI_SSID);screen(a,FILE_PROMPT);
}
static void systems_wifi_close(App*a,bool keep){
    if(!keep){copy(a->systems_wifi_ssid,sizeof(a->systems_wifi_ssid),a->systems_wifi_old_ssid);copy(a->systems_wifi_password,sizeof(a->systems_wifi_password),a->systems_wifi_old_password);a->systems_wifi_saved=a->systems_wifi_old_saved;}
    memset(a->systems_wifi_old_ssid,0,sizeof(a->systems_wifi_old_ssid));memset(a->systems_wifi_old_password,0,sizeof(a->systems_wifi_old_password));
    memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->fs_prompt_kind=0;screen(a,SYSTEMS_EXTRAS);
}
static bool systems_action(App*a,int id){
    if(a->systems_mount_sheet&&systems_mount_screen((int)a->screen)&&id!=A_SYS_MOUNT&&id!=A_SYS_MOUNT_RO&&id!=A_SYS_MOUNT_RW&&id!=A_SYS_MOUNT_CANCEL){
        a->systems_mount_sheet=false;a->catalog_pending=0;a->dirty=true;return true;
    }
    if(a->screen==SYSTEMS_SETUP){
        if(id>=A_SYS_DESKTOP&&id<A_SYS_DESKTOP+(int)SYSTEMS_DESKTOPS){unsigned int n=(unsigned int)(id-A_SYS_DESKTOP);if(systems_image_debian(a)||!systems_desktop_debian(n)){a->systems_desktop=n;a->dirty=true;}return true;}
        if(id==A_SYS_ACCOUNT_KEEP){a->systems_new_account=false;a->systems_username[0]=a->systems_password[0]=a->systems_password_hash[0]=0;a->dirty=true;return true;}
        if(id==A_SYS_ACCOUNT_NEW){systems_account_prompt(a,J_SYS_ACCOUNT_USER);return true;}
        if(id==A_SYS_SETUP_NEXT){if(!a->systems_new_account||(a->systems_username[0]&&(a->systems_password[0]||a->systems_password_hash[0])))screen(a,shells_offered(a)?SYSTEMS_SHELLS:SYSTEMS_EXTRAS);return true;}
        /* Back to the catalog, the saved images or the plan the image was chosen on; else the systems. */
        if(id==A_BACK){reopen(a,came_from(a,SYSTEMS));return true;}
    }
    if(a->screen==SYSTEMS_DONE){
        if(id==A_SYS_DONE_RESTART){if(a->allow_power&&ui_free(a)){a->power_intent=200;screen(a,POWER_CONFIRM);}return true;}
        if(id==A_SYS_DONE_SYSTEMS){screen(a,SYSTEMS);start_systems_job(a,J_SYS_STATUS);return true;}
        if(id==A_SYS_DONE_HOME||id==A_BACK){screen(a,HOME);return true;}
    }
    if(a->screen==SYSTEMS_EXTRAS){
        if(id>=A_SYS_APP&&id<A_SYS_APP+(int)SYSTEMS_APPS){a->systems_apps^=1u<<(id-A_SYS_APP);a->dirty=true;return true;}
        if(id==A_SYS_WIFI){systems_wifi_prompt(a);return true;}
        if(id==A_SYS_WIFI_CLEAR){memset(a->systems_wifi_ssid,0,sizeof(a->systems_wifi_ssid));memset(a->systems_wifi_password,0,sizeof(a->systems_wifi_password));a->systems_wifi_saved=false;a->dirty=true;return true;}
        if(id==A_SYS_SCRIPT){a->systems_pick_manifest=a->systems_pick_script=true;a->systems_plan_page=0;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;}
        if(id==A_SYS_SCRIPT_CLEAR){a->systems_script_volume[0]=a->systems_script_token[0]=a->systems_script_path[0]=a->systems_script_name[0]=0;a->dirty=true;return true;}
        if(id==A_SYS_SETUP_NEXT){if(!(systems_clean(a)&&systems_needs_network(a)&&!a->systems_wifi_ssid[0]))start_systems_job(a,J_SYS_IMPORT_PLAN);return true;}
        if(id==A_BACK){screen(a,shells_offered(a)?SYSTEMS_SHELLS:SYSTEMS_SETUP);return true;}
    }
    if(a->screen==FILE_PROMPT&&(a->fs_prompt_kind==J_SYS_WIFI_SSID||a->fs_prompt_kind==J_SYS_WIFI_PASS)){
        if(id==A_BACK){systems_wifi_close(a,false);return true;}
        if(id==A_SYS_FIELD_SSID||id==A_SYS_FIELD_WIFI_PASS){systems_wifi_focus(a,id==A_SYS_FIELD_SSID?J_SYS_WIFI_SSID:J_SYS_WIFI_PASS);return true;}
        if(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER){
            if(a->fs_prompt_kind==J_SYS_WIFI_SSID){if(a->fs_prompt[0]&&strlen(a->fs_prompt)<=32)systems_wifi_focus(a,J_SYS_WIFI_PASS);return true;}
            if(!systems_wifi_password_ok(a->fs_prompt))return true;
            systems_wifi_focus(a,J_SYS_WIFI_PASS);
            if(!a->systems_wifi_ssid[0]||strlen(a->systems_wifi_ssid)>32){systems_wifi_focus(a,J_SYS_WIFI_SSID);return true;}
            systems_wifi_close(a,true);return true;
        }
    }
    if(a->screen==FILE_PROMPT&&(a->fs_prompt_kind==J_SYS_ACCOUNT_USER||a->fs_prompt_kind==J_SYS_ACCOUNT_PASS)){
        if(id==A_BACK){systems_account_close(a,false);return true;}
        if(id==A_SYS_FIELD_USER||id==A_SYS_FIELD_PASS){systems_account_focus(a,id==A_SYS_FIELD_USER?J_SYS_ACCOUNT_USER:J_SYS_ACCOUNT_PASS);return true;}
        if(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER){
            if(!a->fs_prompt[0])return true;
            if(a->fs_prompt_kind==J_SYS_ACCOUNT_USER){systems_account_focus(a,J_SYS_ACCOUNT_PASS);return true;}
            systems_account_focus(a,J_SYS_ACCOUNT_PASS);
            if(!a->systems_username[0]){systems_account_focus(a,J_SYS_ACCOUNT_USER);return true;}
            systems_account_close(a,true);return true;
        }
    }
    if(id==A_SYSTEMS||id==A_SYS_REFRESH||(id==A_DONE&&a->result_return==SYSTEMS)){
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard the text editor before changing installed systems.",FILE_EDIT);return true;}
        a->systems_pick_manifest=a->systems_pick_script=a->systems_mount_sheet=false;a->systems_known=false;a->systems_reason[0]=0;a->systems_review_recovery=false;systems_forget_plan(a);screen(a,SYSTEMS);start_systems_job(a,J_SYS_STATUS);return true;
    }
    if(a->systems_pick_manifest){
        if(id==A_SYS_CANCEL_PICK||(id==A_BACK&&a->screen==FILE_VOLUMES)){
            if(a->systems_pick_script){a->systems_pick_manifest=a->systems_pick_script=false;screen(a,SYSTEMS_EXTRAS);return true;}
            /* "From a file" is on New system: Back returns there, not to the list of systems. */
            enum Screen from=came_from_picker(a,SYSTEMS);a->systems_pick_manifest=false;reopen(a,from);return true;
        }
        if(id==A_SYS_USE_SCRIPT){
            if(a->systems_pick_script&&systems_script_selected(a)&&a->systems_plan_page+1>=path_pages(a->fs_selected.path,640,8)){
                FileVolume*v=current_volume(a);copy(a->systems_script_volume,sizeof(a->systems_script_volume),v->id);copy(a->systems_script_token,sizeof(a->systems_script_token),v->token);
                copy(a->systems_script_path,sizeof(a->systems_script_path),a->fs_selected.path);copy(a->systems_script_name,sizeof(a->systems_script_name),a->fs_selected.name);
                a->systems_pick_manifest=a->systems_pick_script=false;screen(a,SYSTEMS_EXTRAS);
            }
            return true;
        }
        if((id==A_BACK&&a->screen==FILE_BROWSER&&!strcmp(a->fs_path,"/"))||id==A_FS_VOLUME){if(!source_back(a))screen(a,FILE_VOLUMES);return true;}
        if(id>=A_FS_VOLUME_ITEM&&id<A_FS_VOLUME_ITEM+a->volume_count){
            int index=id-A_FS_VOLUME_ITEM;FileVolume*v=&a->volumes[index];
            bool saved=a->systems_pick_script&&script_store_volume(v);
            if(systems_source_allowed(v)||(saved&&v->mounted))select_volume(a,index);
            else if(systems_source_listed(v)||saved){
                /* Read-only is enough to read the image; the browser opens once it is mounted. */
                a->volume_index=index;copy(a->volume_id,sizeof(a->volume_id),v->id);copy(a->fs_path,sizeof(a->fs_path),"/");reset_browser(a);
                FileRequest q=file_request(a,J_FS_MOUNT);copy(q.mode,sizeof(q.mode),"ro");start_files_request(a,&q);
            }
            return true;
        }
        int entry=id>=A_FS_ENTRY_MENU&&id<A_FS_ENTRY_MENU+a->file_count?id-A_FS_ENTRY_MENU:(id>=A_FS_ENTRY&&id<A_FS_ENTRY+a->file_count?id-A_FS_ENTRY:-1);
        if(entry>=0){a->fs_selected=a->files[entry];a->systems_plan_page=0;if(file_is_directory(&a->fs_selected))open_selected_file(a);else{screen(a,FILE_INFO);start_files_job(a,J_FS_STAT);}return true;}
        if(id==A_SYS_USE_MANIFEST){if(!a->systems_pick_script&&systems_manifest_selected(a)&&a->systems_plan_page+1>=path_pages(a->fs_selected.path,640,8)){FileVolume*v=current_volume(a);copy(a->systems_source_volume,sizeof(a->systems_source_volume),v->id);copy(a->systems_source_token,sizeof(a->systems_source_token),v->token);copy(a->systems_manifest,sizeof(a->systems_manifest),a->fs_selected.path);a->systems_pick_manifest=false;a->systems_remote_id[0]=0;if(!systems_manifest_name(a->systems_manifest)){a->systems_desktop=0;a->systems_new_account=systems_clean(a);a->systems_username[0]=a->systems_password[0]=0;if(systems_clean(a))copy(a->systems_username,sizeof(a->systems_username),"user");systems_forget_choices(a);
            systems_wifi_from_recovery(a);screen(a,SYSTEMS_SETUP);}else start_systems_job(a,J_SYS_IMPORT_PLAN);}return true;}
        if(id==A_SYS_PLAN_PREV){if(a->systems_plan_page)a->systems_plan_page--;a->dirty=true;return true;}
        if(id==A_SYS_PLAN_NEXT){if(a->systems_plan_page+1<path_pages(a->fs_selected.path,640,8))a->systems_plan_page++;a->dirty=true;return true;}
        if(id==A_BACK||id==A_FS_PREV||id==A_FS_NEXT||id==A_FS_REFRESH||id==A_FS_UP||id==A_DONE)return false;
        return true; /* The manifest picker has no editor or storage mutation actions. */
    }
    if(systems_mount_screen((int)a->screen)&&(id==A_SYS_MOUNT||id==A_SYS_MOUNT_CANCEL)){
        a->systems_mount_sheet=id==A_SYS_MOUNT&&systems_can_mount(a)&&ui_free(a);if(id==A_SYS_MOUNT_CANCEL)a->catalog_pending=0;a->dirty=true;return true;}
    if(systems_mount_screen((int)a->screen)&&(id==A_SYS_MOUNT_RO||id==A_SYS_MOUNT_RW)){
        a->systems_mount_sheet=false;a->dirty=true;
        if(!systems_can_mount(a)||(id==A_SYS_MOUNT_RO&&a->systems_mounted))return true;
        a->systems_mount_return=a->screen;
        FileRequest q={.kind=J_FS_MOUNT};copy(q.volume,sizeof(q.volume),"userdata");copy(q.volume_token,sizeof(q.volume_token),a->systems_volume_token);copy(q.mode,sizeof(q.mode),id==A_SYS_MOUNT_RW?"rw":"ro");
        a->systems_mounting=start_files_request(a,&q);return true;
    }
    if(id==A_SYS_FILES){systems_forget_plan(a);a->systems_pick_manifest=false;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;}
    /* Import offers the catalog too (2026-11-09): the page of New system, in its adding mode. */
    if(id==A_SYS_IMPORT){if(systems_ready(a)){a->systems_remote_id[0]=0;a->systems_wipe=false;a->systems_adding=true;a->systems_mount_sheet=false;screen(a,SYSTEMS_NEW);}return true;}
    if(id>=A_SYS_SLOT&&id<A_SYS_SLOT+a->systems_slot_count&&a->screen==SYSTEMS){int index=id-A_SYS_SLOT;if(systems_ready(a)&&!a->systems_slots[index].active){a->systems_selected=index;copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),a->systems_slots[index].id);start_systems_job(a,J_SYS_SWITCH_PLAN);}return true;}
    if(id==A_SYS_RESUME||id==A_SYS_ROLLBACK){if(a->systems_known&&a->systems_transaction[0]&&a->systems_mounted&&!a->systems_read_only){copy(a->systems_direction,sizeof(a->systems_direction),id==A_SYS_ROLLBACK?"rollback":"resume");copy(a->systems_expected_transaction,sizeof(a->systems_expected_transaction),a->systems_transaction);a->systems_review_recovery=true;start_systems_job(a,J_SYS_STATUS);}return true;}
    if(id==A_SYS_PLAN_CONFIRM&&a->screen==SYSTEMS_PLAN){if(a->systems_plan_valid&&a->systems_plan_page+1>=systems_plan_pages(a)&&(uint64_t)time(NULL)<=a->systems_plan_expires)start_systems_job(a,a->systems_plan_kind==J_SYS_IMPORT_PLAN?J_SYS_IMPORT:J_SYS_SWITCH);return true;}
    if(id==A_SYS_RECOVER_CONFIRM&&a->screen==SYSTEMS_RECOVER){start_systems_job(a,J_SYS_RECOVER);return true;}
    if(id==A_SYS_REMOVE&&a->screen==SYSTEMS_PLAN&&a->systems_plan_kind==J_SYS_SWITCH_PLAN){systems_forget_plan(a);screen(a,SYSTEMS_REMOVE);return true;}
    if(id==A_SYS_REMOVE&&a->screen==RESULT&&a->systems_remove_offer){
        /* The page names the system tapped in the list, never a name left from an earlier plan. */
        a->systems_remove_offer=false;a->systems_plan_name[0]=0;
        if(a->systems_selected>=0&&a->systems_selected<(int)a->systems_slot_count&&!strcmp(a->systems_slots[a->systems_selected].id,a->systems_plan_slot))
            copy(a->systems_plan_name,sizeof(a->systems_plan_name),a->systems_slots[a->systems_selected].name);
        systems_forget_plan(a);screen(a,SYSTEMS_REMOVE);return true;
    }
    if(id==A_SYS_REMOVE_CONFIRM&&a->screen==SYSTEMS_REMOVE){start_systems_job(a,J_SYS_REMOVE);return true;}
    if(a->screen==SYSTEMS_PLAN&&(id==A_SYS_PLAN_PREV||id==A_SYS_PLAN_NEXT)){if(id==A_SYS_PLAN_PREV&&a->systems_plan_page)a->systems_plan_page--;if(id==A_SYS_PLAN_NEXT&&a->systems_plan_page+1<systems_plan_pages(a))a->systems_plan_page++;a->dirty=true;return true;}
    if(id==A_BACK&&(a->screen==SYSTEMS||a->screen==SYSTEMS_PLAN||a->screen==SYSTEMS_RECOVER||a->screen==SYSTEMS_REMOVE)){enum Screen back=a->screen==SYSTEMS?came_from(a,OS_MENU):SYSTEMS;systems_forget_plan(a);a->systems_review_recovery=false;reopen(a,back);return true;}
    return false;
}
#endif

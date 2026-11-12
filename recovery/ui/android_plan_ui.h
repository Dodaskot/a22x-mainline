/* SPDX-License-Identifier: MIT
 * An installation prepared in the Android app (2026-11-10). The app leaves
 * cache:/a22x-recovery/install-plan.json and restarts into the recovery; install_plan.py checks
 * it, and this page opens by itself once after startup. "Review installation" mounts what the
 * installation reads, fills the install pages with the plan and opens their usual review, where
 * Install is held as for any other installation; distro.py checks every choice again. The plan
 * is deleted after that installation, or when it is discarded here.
 */
#ifndef A22X_ANDROID_PLAN_UI_H
#define A22X_ANDROID_PLAN_UI_H

static bool aplan_job(int kind){return kind==J_APLAN_SHOW||kind==J_APLAN_PREPARE||kind==J_APLAN_FORGET;}
static int aplan_index(const char*const*list,unsigned int count,const char*id){
    for(unsigned int i=0;i<count;i++)if(id[0]&&!strcmp(list[i],id))return (int)i;
    return -1;
}
static bool start_aplan_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->distro_helper[0]||!aplan_job(kind))return false;
    /* install_plan.py sits beside the other helpers in /opt/a22x-recovery. */
    copy(a->aplan_helper,sizeof(a->aplan_helper),a->distro_helper);char*slash=strrchr(a->aplan_helper,'/');
    if(slash&&(size_t)(slash-a->aplan_helper)+20<sizeof(a->aplan_helper))strcpy(slash+1,"install_plan.py");else return false;
    char*args[10];int n=0;args[n++]=a->python;args[n++]=a->aplan_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_APLAN_SHOW)args[n++]="show";
    else if(kind==J_APLAN_PREPARE)args[n++]="prepare";
    else{if(strlen(a->aplan_sha)!=64)return false;args[n++]="forget";args[n++]="--expected-sha256";args[n++]=a->aplan_sha;}
    args[n]=NULL;enum Screen back=a->result_return;bool ok=launch_job(a,kind,args);a->result_return=back;return ok;
}
/* Once after startup, behind whatever is on screen; never while locked or during the splash. */
static void android_plan_tick(App*a){
    if(a->aplan_asked||a->preview||!a->distro_helper[0]||a->job.pid||a->screen==SPLASH||a->locked)return;
    a->aplan_asked=true;start_aplan_job(a,J_APLAN_SHOW);
}
static void complete_aplan_job(App*a,const Json*j,int kind,bool ok){
    if(kind==J_APLAN_FORGET){
        a->aplan_forget_after=false;
        if(ok&&j){a->aplan_present=a->aplan_valid=false;a->aplan_sha[0]=0;if(a->screen==APLAN){toast(a,"The plan was discarded");screen(a,HOME);}}
        else if(a->screen==APLAN){copy(a->aplan_error,sizeof(a->aplan_error),tr("The plan could not be deleted from cache."));a->dirty=true;}
        return;
    }
    if(kind==J_APLAN_PREPARE){
        if(!ok||!j){
            char message[320]="Userdata and cache could not be prepared.";
            if(j){int e=json_get(j,0,"error");if(e>=0)jstring(j,e,"message",message,sizeof(message));}
            copy(a->aplan_error,sizeof(a->aplan_error),message[0]?message:"Userdata and cache could not be prepared.");a->dirty=true;return;
        }
        /* Mounted as needed; the install pages read the state of userdata first. */
        a->aplan_pending=true;a->systems_known=false;
        if(!start_systems_job(a,J_SYS_STATUS)){a->aplan_pending=false;copy(a->aplan_error,sizeof(a->aplan_error),tr("Systems could not be read."));a->dirty=true;}
        return;
    }
    a->aplan_known=true;a->aplan_present=a->aplan_valid=false;a->aplan_error[0]=0;
    if(!ok||!j){a->dirty=true;return;}
    a->aplan_present=json_bool(j,json_get(j,0,"present"),0)==1;
    if(!a->aplan_present){a->dirty=true;return;}
    jstring(j,0,"sha256",a->aplan_sha,sizeof(a->aplan_sha));
    if(json_bool(j,json_get(j,0,"valid"),0)!=1){
        int problem_=json_get(j,0,"problem");char message[320]="The plan from the Android app cannot be used.";
        if(problem_>=0)jstring(j,problem_,"message",message,sizeof(message));
        copy(a->aplan_error,sizeof(a->aplan_error),message[0]?message:"The plan from the Android app cannot be used.");
    }else{
        int p=json_get(j,0,"plan"),image=p>=0?json_get(j,p,"image"):-1;
        jstring(j,p,"catalog",a->aplan_catalog,sizeof(a->aplan_catalog));jstring(j,p,"source",a->aplan_source,sizeof(a->aplan_source));
        a->aplan_created=json_uint(j,json_get(j,p,"created_utc"),0);
        jstring(j,image,"name",a->aplan_name,sizeof(a->aplan_name));jstring(j,image,"version",a->aplan_version,sizeof(a->aplan_version));
        jstring(j,image,"file",a->aplan_file,sizeof(a->aplan_file));a->aplan_size=json_uint(j,json_get(j,image,"size"),0);
        a->aplan_erase=json_bool(j,json_get(j,p,"erase_userdata"),1)==1;
        jstring(j,p,"desktop",a->aplan_desktop_id,sizeof(a->aplan_desktop_id));jstring(j,p,"sign_in",a->aplan_greeter_id,sizeof(a->aplan_greeter_id));
        a->aplan_shells=a->aplan_apps=0;bool known=true;
        int list=json_get(j,p,"shells");
        for(int i=list+1;list>=0&&i<j->count&&j->tokens[i].start<j->tokens[list].end;i=json_next(j,i)){
            char id[24];if(json_string(j,i,id,sizeof(id))<0)id[0]=0;int n=aplan_index(systems_desktops,SYSTEMS_DESKTOPS,id);
            if(n>=(int)SHELLS_FIRST)a->aplan_shells|=1u<<n;else known=false;
        }
        list=json_get(j,p,"apps");
        for(int i=list+1;list>=0&&i<j->count&&j->tokens[i].start<j->tokens[list].end;i=json_next(j,i)){
            char id[24];if(json_string(j,i,id,sizeof(id))<0)id[0]=0;int n=aplan_index(systems_app_ids,SYSTEMS_APPS,id);
            if(n>=0)a->aplan_apps|=1u<<n;else known=false;
        }
        int account=json_get(j,p,"account");a->aplan_user[0]=a->aplan_hash[0]=0;
        if(account>=0&&j->tokens[account].type==J_OBJECT){jstring(j,account,"username",a->aplan_user,sizeof(a->aplan_user));jstring(j,account,"password_sha512",a->aplan_hash,sizeof(a->aplan_hash));}
        jstring(j,p,"locale",a->aplan_locale,sizeof(a->aplan_locale));jstring(j,p,"timezone",a->aplan_zone,sizeof(a->aplan_zone));
        jstring(j,p,"script",a->aplan_script,sizeof(a->aplan_script));a->aplan_script_present=json_bool(j,json_get(j,0,"script_present"),0)==1;
        /* What install_plan.py let through must also be known to these pages. */
        known=known&&a->aplan_catalog[0]&&a->aplan_file[0]&&a->aplan_name[0]&&aplan_index(systems_desktops,SYSTEMS_DESKTOPS,a->aplan_desktop_id)>=0
              &&aplan_index(greeter_ids,GREETERS,a->aplan_greeter_id)>=0&&(!a->aplan_erase||(a->aplan_user[0]&&a->aplan_hash[0]));
        if(known)a->aplan_valid=true;else copy(a->aplan_error,sizeof(a->aplan_error),tr("This recovery does not know every choice of the plan."));
    }
    a->dirty=true;
    /* Shown by itself once per start, over the home page only. */
    if(!a->aplan_opened&&a->screen==HOME){a->aplan_opened=true;screen(a,APLAN);}
}
/* The install pages take the plan's choices, as if picked on them one by one. */
static void android_plan_fill(App*a){
    copy(a->systems_remote_id,sizeof(a->systems_remote_id),a->aplan_catalog);copy(a->systems_manifest,sizeof(a->systems_manifest),a->aplan_file);
    a->systems_source_volume[0]=a->systems_source_token[0]=0;a->systems_adding=!a->aplan_erase;a->systems_wipe=a->aplan_erase;a->systems_pick_manifest=false;
    systems_forget_choices(a);
    int d=aplan_index(systems_desktops,SYSTEMS_DESKTOPS,a->aplan_desktop_id);a->systems_desktop=d>=0?(unsigned int)d:0u;
    memset(a->systems_password,0,sizeof(a->systems_password));a->systems_new_account=a->aplan_user[0]!=0;
    copy(a->systems_username,sizeof(a->systems_username),a->aplan_user[0]?a->aplan_user:"user");copy(a->systems_password_hash,sizeof(a->systems_password_hash),a->aplan_hash);
    a->systems_shells=a->aplan_shells;int g=aplan_index(greeter_ids,GREETERS,a->aplan_greeter_id);a->systems_greeter=g>0?(unsigned int)g:0u;
    a->systems_apps=a->aplan_apps;a->systems_locale=aplan_index(sys_locales,SYS_LOCALES,a->aplan_locale);
    a->systems_tz=-1;for(unsigned int i=0;i<TZ_ZONES&&a->aplan_zone[0];i++)if(!strcmp(tz_zones[i].id,a->aplan_zone)){a->systems_tz=(int)i;break;}
    if(a->aplan_script[0]&&a->aplan_script_present){
        copy(a->systems_script_volume,sizeof(a->systems_script_volume),"cache");a->systems_script_token[0]=0;
        snprintf(a->systems_script_path,sizeof(a->systems_script_path),"/a22x-recovery/scripts/%s",a->aplan_script);copy(a->systems_script_name,sizeof(a->systems_script_name),a->aplan_script);
    }
    systems_wifi_from_recovery(a);
    a->aplan_used=true;
}
/* After the state of userdata is known: the review of the installation, or its first page to change. */
static void android_plan_after_status(App*a){
    a->aplan_pending=false;
    if(!systems_ready(a)){
        copy(a->aplan_error,sizeof(a->aplan_error),a->systems_reason[0]?a->systems_reason:tr("Userdata is not ready for an installation. Open Systems to see why."));screen(a,APLAN);return;
    }
    android_plan_fill(a);
    if(a->aplan_edit){screen(a,SYSTEMS_SETUP);return;}
    if(systems_clean(a)&&systems_needs_network(a)&&!a->systems_wifi_ssid[0]){
        copy(a->aplan_error,sizeof(a->aplan_error),tr("Connect to Wi-Fi first: the desktop and apps are downloaded during the installation."));screen(a,APLAN);return;
    }
    if(!start_systems_job(a,J_SYS_IMPORT_PLAN)){copy(a->aplan_error,sizeof(a->aplan_error),tr("The installation could not be planned."));screen(a,APLAN);}
}
static int aplan_row(App*a,int y,const char*label,const char*value,uint32_t ink){
    text(a,52,y,0,MUTED,label);raw_text(true);ellipsis(a,300,y,370,0,ink,value);raw_text(false);return y+56;
}
static void render_aplan(App*a){
    heading(a,"From the Android app","An installation prepared in the A22x app.",true);
    bool busy=a->job.pid&&(aplan_job(a->job.kind)||a->job.kind==J_SYS_STATUS||a->job.kind==J_SYS_IMPORT_PLAN);
    if(!a->aplan_valid){
        rounded(a,(Rect){28,242,664,330},24,PANEL);text(a,52,264,0,RED,"THE PLAN CANNOT BE USED");
        wrapped(a,52,312,606,0,TEXT,a->aplan_error[0]?a->aplan_error:"The plan from the Android app cannot be used.",6);
        wrapped(a,40,600,640,0,MUTED,"Prepare it again in the app, or discard it here.",3);
        button(a,(Rect){28,1320,664,104},"Hold 2 seconds to discard the plan",A_APLAN_FORGET,ui_free(a)&&!a->job.pid&&a->aplan_sha[0],false,2000);
        return;
    }
    Rect hero={28,236,664,196};gradient(a,hero,36,ui_theme->hero_top,ui_theme->hero_bottom);
    veil(a,(Rect){52,258,72,72},36,0xffffffu,46);icon(a,ICON_LINUX,64,270,48,ui_theme->hero_text);
    raw_text(true);ellipsis(a,144,262,520,2,ui_theme->hero_text,a->aplan_name);raw_text(false);
    {char size[48],line[200];human_bytes(a->aplan_size,size,sizeof(size));snprintf(line,sizeof(line),T("%s  /  from the catalog, %s download"),a->aplan_version[0]?a->aplan_version:"—",size);
     ellipsis(a,144,334,520,0,ui_theme->hero_sub,line);}
    ellipsis(a,52,382,620,0,ui_theme->hero_sub,a->aplan_erase?"Clean install: userdata is erased first":"Beside the current Linux");
    rounded(a,(Rect){28,452,664,420},24,PANEL);
    char line[256];int y=474;
    {int d=aplan_index(systems_desktops,SYSTEMS_DESKTOPS,a->aplan_desktop_id);size_t used=(size_t)snprintf(line,sizeof(line),"%s",tr(systems_desktop_labels[d>=0?d:0]));
     for(unsigned int n=SHELLS_FIRST;n<SYSTEMS_DESKTOPS&&used<sizeof(line)-40;n++)if((a->aplan_shells>>n)&1u)used+=(size_t)snprintf(line+used,sizeof(line)-used," + %s",tr(systems_desktop_labels[n]));
     y=aplan_row(a,y,"Desktop",line,TEXT);}
    {int g=aplan_index(greeter_ids,GREETERS,a->aplan_greeter_id);y=aplan_row(a,y,"Sign-in",tr(greeter_labels[g>=0?g:0]),TEXT);}
    {size_t used=0;line[0]=0;unsigned int count=0;for(unsigned int n=0;n<SYSTEMS_APPS;n++)if((a->aplan_apps>>n)&1u){count++;if(used<sizeof(line)-48)used+=(size_t)snprintf(line+used,sizeof(line)-used,"%s%s",used?", ":"",tr(systems_app_labels[n]));}
     y=aplan_row(a,y,"Apps",count?line:"—",TEXT);}
    {if(a->aplan_user[0])snprintf(line,sizeof(line),T("%s, password from the app"),a->aplan_user);else copy(line,sizeof(line),tr("Keep mine"));y=aplan_row(a,y,"Account",line,TEXT);}
    {int l=aplan_index(sys_locales,SYS_LOCALES,a->aplan_locale);const char*city=tr("As in recovery");
     for(unsigned int i=0;i<TZ_ZONES&&a->aplan_zone[0];i++)if(!strcmp(tz_zones[i].id,a->aplan_zone)){city=tz_zones[i].city;break;}
     snprintf(line,sizeof(line),"%s  /  %s",l>=0?sys_locale_names[l]:tr("As in recovery"),city);y=aplan_row(a,y,"Language",line,TEXT);}
    {if(!a->aplan_script[0])y=aplan_row(a,y,"Setup script","—",TEXT);
     else if(a->aplan_script_present)y=aplan_row(a,y,"Setup script",a->aplan_script,TEXT);
     else{snprintf(line,sizeof(line),T("%s is missing in cache"),a->aplan_script);y=aplan_row(a,y,"Setup script",line,ORANGE);}}
    {bool joined=a->wifi_connected&&a->wifi_ssid[0];y=aplan_row(a,y,"Wi-Fi",joined?a->wifi_ssid:tr("Not connected"),joined?GREEN:ORANGE);}
    int note=910;
    if(a->aplan_erase)note+=text_tile(a,note,0,RED,"Userdata is erased first: Android's data, every Linux system and /home on it.",3)+16;
    else note+=text_tile(a,note,0,MUTED,"The current Linux stays active until you switch to the new one.",3)+16;
    if(busy)text(a,40,note-8,0,MUTED,"Preparing...");
    else if(a->aplan_error[0])text_tile(a,note,0,ORANGE,a->aplan_error,3);
    bool free_=ui_free(a)&&!a->job.pid;
    button(a,(Rect){28,1196,324,96},"Change choices",A_APLAN_EDIT,free_,false,0);
    button(a,(Rect){368,1196,324,96},"Discard (hold)",A_APLAN_FORGET,free_,false,2000);
    button(a,(Rect){28,1320,664,108},"Review installation",A_APLAN_REVIEW,free_,true,0);
}
static bool android_plan_action(App*a,int id){
    if(id==A_APLAN){if(ui_free(a)&&a->aplan_present){a->aplan_error[0]=a->aplan_valid?0:a->aplan_error[0];screen(a,APLAN);}return true;}
    if(a->screen!=APLAN)return false;
    /* The plan opens from Home and from Install: Back returns to the one it was opened from. */
    if(id==A_BACK){if(!a->job.pid||!aplan_job(a->job.kind))reopen(a,came_from(a,HOME));return true;}
    if(id==A_APLAN_REVIEW||id==A_APLAN_EDIT){
        if(!ui_free(a)||a->job.pid||!a->aplan_valid)return true;
        a->aplan_error[0]=0;a->aplan_edit=id==A_APLAN_EDIT;if(!start_aplan_job(a,J_APLAN_PREPARE))copy(a->aplan_error,sizeof(a->aplan_error),tr("Wait for the current operation to finish."));
        a->dirty=true;return true;
    }
    if(id==A_APLAN_FORGET){if(ui_free(a)&&!a->job.pid)start_aplan_job(a,J_APLAN_FORGET);return true;}
    return false;
}
/* The Install page offers the plan first, highlighted (2026-11-10, the person's wish). */
static int render_aplan_card(App*a,int y){
    if(!a->aplan_present)return y;
    char note[160];if(a->aplan_valid){raw_text(false);snprintf(note,sizeof(note),T("Ready: %s"),a->aplan_name);}else copy(note,sizeof(note),tr("The plan cannot be used"));
    Rect r={28,y,664,150};gradient(a,r,30,ui_theme->hero_top,ui_theme->hero_bottom);
    veil(a,(Rect){r.x+22,r.y+33,84,84},42,0xffffffu,46);icon(a,ICON_PHONE,r.x+40,r.y+51,48,ui_theme->hero_text);
    text(a,r.x+128,r.y+30,1,ui_theme->hero_text,"From the Android app");raw_text(true);ellipsis(a,r.x+128,r.y+82,480,0,ui_theme->hero_sub,note);raw_text(false);
    icon(a,ICON_CHEVRON,r.x+r.w-56,r.y+55,40,ui_theme->hero_text);register_button(a,r,A_APLAN,ui_free(a),0);
    return y+166;
}
#endif

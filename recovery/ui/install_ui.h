/* SPDX-License-Identifier: MIT
 * Image installation is performed only by install.py. This frontend binds the
 * review to its private plan file, source, backup and exact helper receipt.
 */
#ifndef A22X_INSTALL_UI_H
#define A22X_INSTALL_UI_H

static bool install_hex(const char*s,size_t n){
    if(strlen(s)!=n)return false;
    for(;*s;s++)if(!((*s>='0'&&*s<='9')||(*s>='a'&&*s<='f')))return false;
    return true;
}
static bool install_string(const Json*j,int object,const char*key,char*out,size_t capacity,bool empty){
    int n=json_string(j,json_get(j,object,key),out,capacity);
    return n>=0&&(empty||n>0)&&strlen(out)==(size_t)n;
}
static bool install_false(const Json*j,int object,const char*key){return json_bool(j,json_get(j,object,key),-1)==0;}
static bool install_codes(const Json*j,int object,const char*key,char*out,size_t capacity){
    int array=json_get(j,object,key);if(array<0||j->tokens[array].type!=J_ARRAY)return false;
    out[0]=0;unsigned count=0;
    for(int i=array+1;i<json_next(j,array);i=json_next(j,i)){
        char code[96];int n=json_string(j,i,code,sizeof(code));
        if(n<=0||strlen(code)!=(size_t)n||++count>16)return false;
        for(const char*p=code;*p;p++)if(!((*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||*p=='_'))return false;
        if(strlen(out)+(size_t)n+(out[0]?1u:0u)>=capacity)return false;
        if(out[0])append(out,capacity,"\n");
        append(out,capacity,code);
    }
    return true;
}
static const char*install_operation(int kind){
    switch(kind){case J_INSTALL_STATUS:return "status";case J_INSTALL_BOOT_PLAN:return "boot-plan";case J_INSTALL_STOCK_PLAN:return "stock-plan";case J_INSTALL_RESUME_PLAN:return "resume-plan";case J_INSTALL_EXECUTE:return "execute";default:return "";}
}
static bool install_underlying_operation(const char*operation){return !strcmp(operation,"flash-boot")||!strcmp(operation,"install-stock");}
/* The operation contract is fixed. Geometry and actual device admission remain
 * authoritative in the helper; an unexpected label must not reach a UI hold. */
static int install_stock_rank(const char*label){
    static const char*const labels[]={"super","prism","optics","dtbo","vbmeta_system","vbmeta","boot"};
    for(unsigned n=0;n<sizeof(labels)/sizeof(labels[0]);n++)if(!strcmp(label,labels[n]))return (int)n;
    return -1;
}
static bool install_source_selected(const App*a){
    const FileVolume*v=a->volume_index>=0&&a->volume_index<a->volume_count?&a->volumes[a->volume_index]:NULL;
    const FileEntry*e=&a->fs_selected;size_t n=strlen(e->name);
    return install_source_allowed(v)&&file_is_regular(e)&&e->token[0]&&e->path[0]=='/'&&
        (a->install.stock_selected?!strcmp(e->name,"manifest.json"):(n>4&&e->name[n-4]=='.'&&tolower((unsigned char)e->name[n-3])=='i'&&tolower((unsigned char)e->name[n-2])=='m'&&tolower((unsigned char)e->name[n-1])=='g'));
}
static void install_forget_plan(App*a){
    InstallState*m=&a->install;m->plan_valid=m->can_execute=m->review_bottom_seen=false;
    if(m->plan_path[0]){unlink(m->plan_path);m->plan_path[0]=0;}
    m->seal[0]=m->plan_hash[0]=m->confirmation[0]=0;m->row_count=0;m->review_used=0;m->review_scroll=0;
}
static bool install_ready(const App*a){
    const InstallState*m=&a->install;
    return m->known&&!m->pending_count&&!m->uncertain&&!m->status_blockers[0]&&
        (m->stock_selected?(m->stock&&m->stock_qualified):m->flash_boot);
}
static bool install_pending_equal(const InstallPending*a,const InstallPending*b){
    return !strcmp(a->id,b->id)&&!strcmp(a->operation,b->operation)&&!strcmp(a->volume,b->volume)&&
        !strcmp(a->path,b->path)&&!strcmp(a->token,b->token)&&a->resumable&&b->resumable;
}
static bool install_review_add(App*a,const char*s,uint32_t color){
    InstallState*m=&a->install;size_t length=strlen(s),base=m->review_used;
    if(base+length+1>=sizeof(m->review))return false;
    memcpy(m->review+base,s,length+1);m->review_used+=length+1;
    size_t at=0;
    do{
        if(m->row_count>=INSTALL_REVIEW_ROWS)return false;
        TextRow row=text_row(s,at,608);
        if(row.next==row.end&&row.end<length){
            size_t split=row.end;while(split>at&&s[split]!=' ')split--;
            if(split>at+(row.end-at)/3){row.end=split;row.next=split+1;}
        }
        if(row.end<at||row.end>length||row.end-at>=4096||row.next<row.end||(at<length&&row.next<=at))return false;
        m->rows[m->row_count++]=(InstallReviewRow){(uint32_t)(base+at),(uint32_t)(base+row.end),color};
        at=row.next;
    }while(at<length);
    return true;
}
#define INSTALL_LINE(value,color) do{if(!install_review_add(a,(value),(color)))return false;}while(0)
static bool install_build_review(App*a,bool result){
    InstallState*m=&a->install;m->row_count=0;m->review_used=0;m->review_scroll=0;m->review_bottom_seen=false;
    bool stock=!strcmp(m->operation,"install-stock");char linebuf[1536],size[80];
    if(result){
        INSTALL_LINE("INSTALLATION RECEIPT",ORANGE);
        INSTALL_LINE(stock?"Stock installation completed and independently verified.":"BOOT installation completed and independently verified.",GREEN);
        INSTALL_LINE("The helper verified bytes. This does not prove that the selected system boots.",MUTED);
    }else{
        INSTALL_LINE(m->resuming?"REVIEW EXPLICIT RESUME":"REVIEW EXACT IMAGE INSTALLATION",ORANGE);
        INSTALL_LINE(stock?"Install the fixed stock Android profile":"Flash the selected BOOT image",TEXT);
    }
    INSTALL_LINE("",MUTED);INSTALL_LINE("SOURCE VOLUME",ORANGE);INSTALL_LINE(m->source_volume,TEXT);
    INSTALL_LINE("SOURCE FILE",ORANGE);INSTALL_LINE(m->source_path,TEXT);
    INSTALL_LINE("LINUX BACKUP",ORANGE);INSTALL_LINE(m->backup_path,TEXT);
    snprintf(linebuf,sizeof(linebuf),T("Complete: %s  /  verified: %s  /  same device: %s"),tr(m->backup_complete?"yes":"no"),tr(m->backup_verified?"yes":"no"),tr(m->backup_same_device?"yes":"no"));INSTALL_LINE(linebuf,m->backup_complete&&m->backup_verified&&m->backup_same_device?GREEN:RED);
    if(m->backup_current_required)INSTALL_LINE(m->backup_current?"The backup matches the current Linux state.":"The backup does not match the current Linux state.",m->backup_current?GREEN:RED);
    else if(m->backup_userdata_required)INSTALL_LINE(m->backup_userdata_current?"Current userdata matches the backup. BOOT is bound to the original transaction and checkpoint.":"Current userdata does not match the backup.",m->backup_userdata_current?GREEN:RED);
    if(stock){
        INSTALL_LINE("",MUTED);INSTALL_LINE("STOCK PROFILE",ORANGE);INSTALL_LINE(m->profile_id[0]?m->profile_id:"Profile unavailable",TEXT);INSTALL_LINE(m->firmware[0]?m->firmware:"Firmware not reported",MUTED);
    }
    INSTALL_LINE("",MUTED);INSTALL_LINE(result?"WRITTEN TARGETS":"EXACT WRITE TARGETS",ORANGE);
    if(!m->write_count)INSTALL_LINE("No changed target is listed.",MUTED);
    for(unsigned n=0;n<m->write_count;n++){
        InstallTarget*t=&m->writes[n];human_bytes(t->bytes,size,sizeof(size));
        snprintf(linebuf,sizeof(linebuf),"%u. %s  /  %s  /  %" PRIu64 " bytes",n+1,t->label,size,t->bytes);INSTALL_LINE(linebuf,TEXT);INSTALL_LINE(t->path,TEXT);
        snprintf(linebuf,sizeof(linebuf),"Partition %" PRIu64 "  /  start sector %" PRIu64 "  /  %s",t->partition,t->start,t->action);INSTALL_LINE(linebuf,MUTED);
    }
    INSTALL_LINE("",MUTED);INSTALL_LINE("WRITE ORDER",ORANGE);
    if(!m->order_count)INSTALL_LINE("No writes required.",MUTED);
    for(unsigned n=0;n<m->order_count;n++){snprintf(linebuf,sizeof(linebuf),"%u. %s",n+1,m->order[n]);INSTALL_LINE(linebuf,TEXT);}
    INSTALL_LINE("",MUTED);INSTALL_LINE("VERIFIED IDENTICAL / SKIPPED",ORANGE);
    if(!m->skip_count)INSTALL_LINE("None.",MUTED);
    for(unsigned n=0;n<m->skip_count;n++){
        InstallTarget*t=&m->skips[n];human_bytes(t->bytes,size,sizeof(size));
        snprintf(linebuf,sizeof(linebuf),"%s  /  %s  /  %" PRIu64 " bytes",t->label,size,t->bytes);INSTALL_LINE(linebuf,TEXT);INSTALL_LINE(t->path,TEXT);
        snprintf(linebuf,sizeof(linebuf),"Partition %" PRIu64 "  /  start sector %" PRIu64 "  /  skip-identical",t->partition,t->start);INSTALL_LINE(linebuf,MUTED);
    }
    INSTALL_LINE("",MUTED);INSTALL_LINE("PLATFORM CHECKPOINT",ORANGE);INSTALL_LINE(result?m->result_checkpoint:m->checkpoint,TEXT);INSTALL_LINE(m->checkpoint_labels,MUTED);
    human_bytes(m->checkpoint_bytes,size,sizeof(size));snprintf(linebuf,sizeof(linebuf),T("Checkpoint space: %s"),size);INSTALL_LINE(linebuf,MUTED);
    INSTALL_LINE("This small checkpoint is separate from the full Linux BOOT and userdata backup.",MUTED);
    INSTALL_LINE("TRANSACTION",ORANGE);INSTALL_LINE(m->transaction,TEXT);
    if(result){INSTALL_LINE("DURABLE JOURNAL",ORANGE);INSTALL_LINE(m->result_journal,TEXT);}
    INSTALL_LINE("",MUTED);INSTALL_LINE("WHAT HAPPENS NEXT",stock?RED:ORANGE);
    if(stock){
        INSTALL_LINE("This recovery stays installed, so you can return to Linux from a backup.",RED);
        INSTALL_LINE("Userdata is formatted for Android. This erases every Linux system and /home on the phone.",RED);
        INSTALL_LINE("Keep the verified Linux backup on the microSD card: Restore brings Linux back.",MUTED);
    }else{
        INSTALL_LINE("Userdata and recovery stay unchanged. A valid BOOT header does not prove that this kernel or ramdisk boots on this phone.",MUTED);
    }
    INSTALL_LINE("There is no automatic reboot.",TEXT);
    if(!result){
        INSTALL_LINE("Writes are not atomic. Keep power and microSD connected. If interrupted, inspect installation status and review Resume; do not repeat the old execute command.",ORANGE);
        if(stock)INSTALL_LINE("An interrupted RECOVERY write can require Samsung Download mode and external recovery flashing.",ORANGE);
        if(m->blockers[0]){INSTALL_LINE("",MUTED);INSTALL_LINE("PLAN BLOCKERS",RED);INSTALL_LINE(m->blockers,RED);}
    }
    INSTALL_LINE("",MUTED);INSTALL_LINE(result?"End of installation receipt.":"End of the exact reviewed plan.",MUTED);
    return true;
}
#undef INSTALL_LINE
static void render_install(App*a){
    InstallState*m=&a->install;heading(a,"Install Android","Stock firmware from a prepared bundle.",true);
    if(m->pending_count){
        rounded(a,(Rect){28,242,664,156},18,0x302326);text(a,52,264,1,RED,"An installation is unfinished");
        wrapped(a,52,318,608,0,TEXT,"Review its current journal before doing anything else. New installations are blocked.",2);
        m->pending_scroll=(int64_t)scroll_clamp((double)m->pending_scroll,(double)install_pending_max_scroll(a));
        a->clip_active=true;a->clip=install_pending_viewport();
        for(unsigned n=(unsigned)(m->pending_scroll/202);n<m->pending_count;n++){
            int y=430+(int)((int64_t)n*202-m->pending_scroll);if(y>=1170)break;InstallPending*p=&m->pending[n];
            Rect r={28,y,664,188};rounded(a,r,16,PANEL);text(a,52,y+14,1,ORANGE,!strcmp(p->operation,"install-stock")?"Stock installation":"BOOT installation");
            ellipsis(a,52,y+61,608,0,TEXT,p->id);ellipsis(a,52,y+99,608,0,MUTED,p->state);
            text(a,52,y+139,0,p->resumable?GREEN:RED,p->resumable?"Tap to review a fresh Resume plan":"This journal requires manual inspection");
            register_button(a,r,A_INSTALL_PENDING+(int)n,ui_free(a)&&m->known&&m->can_resume&&p->resumable,0);
        }
        a->clip_active=false;scroll_indicator(a,(Rect){684,430,6,740},m->pending_scroll,(int64_t)m->pending_count*202-14,ORANGE);
        wrapped(a,40,1194,640,0,ORANGE,"No automatic replay. Partial partition writes are not atomic.",3);
        button(a,(Rect){28,1330,664,94},"Refresh installation status",A_INSTALL_REFRESH,ui_free(a),false,0);return;
    }
    /* On the phone BOOT lives under Flash a partition, which reads from any volume. */
    if(a->device)m->stock_selected=true;
    else{button(a,(Rect){28,242,320,104},"Flash BOOT",A_INSTALL_BOOT,ui_free(a),m->stock_selected==false,0);
    button(a,(Rect){372,242,320,104},"Stock Android",A_INSTALL_STOCK,ui_free(a),m->stock_selected,0);}
    wrapped(a,40,376,640,0,MUTED,m->stock_selected?"Install the prepared stock Android. Userdata is formatted first and this recovery stays.":"Select a raw A22x BOOT v2 image. A header check does not prove that its kernel boots.",3);
    rounded(a,(Rect){28,488,664,172},18,PANEL);text(a,52,506,0,ORANGE,m->stock_selected?"STOCK MANIFEST ON MICROSD":"BOOT IMAGE ON MICROSD");
    wrapped(a,52,552,608,0,TEXT,m->source_path[0]?m->source_path:"Tap to choose a source file",2);ellipsis(a,52,618,608,0,MUTED,m->source_volume[0]?m->source_volume:"Mounted writable ext4 microSD required");
    register_button(a,(Rect){28,488,664,172},A_INSTALL_SOURCE,ui_free(a)&&m->known&&!m->uncertain,0);
    rounded(a,(Rect){28,690,664,172},18,PANEL);text(a,52,708,0,ORANGE,"LINUX BACKUP");
    wrapped(a,52,754,608,0,TEXT,m->backup_path[0]?m->backup_path:"Tap to explicitly select a Linux v2 snapshot",3);
    register_button(a,(Rect){28,690,664,172},A_INSTALL_BACKUP,ui_free(a)&&m->known&&!m->uncertain,0);
    const char*reason;
    bool stock_blocked=m->known&&m->stock_selected&&!m->stock_qualified;
    if(m->reason[0])reason=m->reason;
    else if(m->status_blockers[0])reason=m->status_blockers;
    else if(stock_blocked)reason=m->stock_blockers[0]?m->stock_blockers:(m->stock_profile?"The fixed stock profile is not qualified for this phone. Stock installation is unavailable.":"A qualified stock profile is not available in this recovery.");
    else if(m->note[0])reason=m->note;
    else if(!m->known)reason="Refresh to read installer status.";
    else reason=m->stock_selected?"Unmount userdata normally in Files before planning stock. All target partitions must be offline. Keep microSD mounted with write access. The installer verifies the current Linux backup, source and phone identity.":"BOOT must be unmounted and unused. Userdata may remain mounted. Keep microSD mounted with write access. The installer verifies the stored Linux backup, source and phone identity.";
    wrapped(a,40,906,640,0,m->reason[0]||m->uncertain||m->status_blockers[0]||stock_blocked?RED:MUTED,reason,8);
    button(a,(Rect){28,1204,320,80},"Files / mounts",A_INSTALL_FILES,ui_free(a),false,0);
    button(a,(Rect){372,1204,320,80},"Refresh status",A_INSTALL_REFRESH,ui_free(a),false,0);
    button(a,(Rect){28,1320,664,104},"Review exact plan",A_INSTALL_REVIEW,ui_free(a)&&install_ready(a)&&m->source_path[0]&&m->source_volume[0]&&m->source_token[0]&&m->backup_path[0],true,0);
}
static void render_install_source(App*a){
    InstallState*m=&a->install;heading(a,m->stock_selected?"Stock manifest":"BOOT source","Review the full source path before choosing it.",true);
    rounded(a,(Rect){28,242,664,200},18,PANEL);wrapped(a,52,266,608,1,TEXT,a->fs_selected.name,3);
    text(a,40,476,0,ORANGE,"ON MICROSD");confirmation_path(a,40,522,640,a->fs_selected.path,m->path_page,8);
    unsigned pages=path_pages(a->fs_selected.path,640,8);
    if(pages>1){button(a,(Rect){28,802,180,64},"Previous",A_INSTALL_PATH_PREV,m->path_page>0,false,0);button(a,(Rect){512,802,180,64},"Next",A_INSTALL_PATH_NEXT,m->path_page+1<pages,false,0);}
    wrapped(a,40,908,640,1,MUTED,m->stock_selected?"Choose manifest.json from the exact prepared stock bundle. Its payloads and compatibility proof are checked by the installer.":"Choose a regular .img file. The installer checks A22x BOOT geometry and writes only the BOOT partition after a separate reviewed hold.",6);
    button(a,(Rect){28,1330,664,108},"Use this source",A_INSTALL_USE_SOURCE,ui_free(a)&&install_source_selected(a)&&m->path_page+1>=pages,true,0);
}
static void render_install_review(App*a,bool result){
    InstallState*m=&a->install;bool stock=!strcmp(m->operation,"install-stock");
    heading(a,result?"Installation result":(m->resuming?"Review Resume":"Review installation"),result?"Verified write receipt. No automatic reboot.":"Swipe through the full source, backup and target list.",true);
    Rect v=install_review_viewport();rounded(a,v,18,PANEL);m->review_scroll=(int64_t)scroll_clamp((double)m->review_scroll,(double)install_review_max_scroll(a));
    a->clip_active=true;a->clip=v;
    unsigned first=(unsigned)(m->review_scroll/TEXT_ROW_HEIGHT);
    for(unsigned n=first;n<m->row_count;n++){
        int y=v.y+16+(int)((int64_t)n*TEXT_ROW_HEIGHT-m->review_scroll);if(y>=v.y+v.h)break;
        InstallReviewRow*r=&m->rows[n];size_t length=r->end-r->start;char row[4096];
        if(length>=sizeof(row)||r->end>=m->review_used)continue;
        memcpy(row,m->review+r->start,length);row[length]=0;text(a,52,y,0,r->color,row);
    }
    a->clip_active=false;scroll_indicator(a,(Rect){684,v.y,6,v.h},m->review_scroll,(int64_t)m->row_count*TEXT_ROW_HEIGHT+32,ORANGE);
    if(m->review_scroll>=install_review_max_scroll(a))m->review_bottom_seen=true;
    if(result){
        button(a,(Rect){28,1330,320,108},"Install status",A_INSTALL_RESULT_DONE,ui_free(a),false,0);
        button(a,(Rect){372,1330,320,108},"Power options",A_INSTALL_POWER,ui_free(a),false,0);return;
    }
    bool current=(uint64_t)time(NULL)<=m->expires,at_end=m->review_scroll>=install_review_max_scroll(a);
    const char*note=!current?"This plan expired. Return and review again.":(!m->can_execute?"The plan is blocked. Read the reported blockers.":(!at_end?"Scroll to the end before confirming.":"Leaving the button cancels the hold."));
    text(a,40,1282,0,!current||!m->can_execute?RED:MUTED,note);
    button(a,(Rect){28,1330,664,108},stock?"Hold 5 seconds to install stock":"Hold 3 seconds to flash BOOT",A_INSTALL_CONFIRM,ui_free(a)&&m->plan_valid&&m->can_execute&&current&&at_end&&m->review_bottom_seen,true,m->hold_ms);
}
static bool start_install_job(App*a,int kind){
    InstallState*m=&a->install;if(a->job.pid||a->preview||!install_job(kind))return false;
    if(!a->install_helper[0]){files_problem(a,"INSTALLER_UNAVAILABLE","The image installer helper is not configured.",INSTALL);return false;}
    char*args[32];int n=0;args[n++]=a->python;args[n++]=a->install_helper;args[n++]="--mode";args[n++]=a->device?"device":"fixture";
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}args[n++]=(char*)install_operation(kind);
    if(kind==J_INSTALL_BOOT_PLAN||kind==J_INSTALL_STOCK_PLAN||kind==J_INSTALL_RESUME_PLAN){
        bool resume=kind==J_INSTALL_RESUME_PLAN;
        if(!m->known||(!resume&&!install_ready(a))||(resume&&(!m->selected_pending.resumable||!m->can_resume)))return false;
        install_forget_plan(a);m->plan_kind=kind;
        if(!private_workdir(a->workdir)||snprintf(m->plan_path,sizeof(m->plan_path),"%s/install-%ld-%u.json",a->workdir,(long)getpid(),++a->plan_sequence)>=(int)sizeof(m->plan_path)){m->plan_path[0]=0;files_problem(a,"WORKDIR_UNAVAILABLE","A private RAM directory is required for this installation plan.",INSTALL);return false;}
        args[n++]="--output";args[n++]=m->plan_path;
        if(resume){args[n++]="--journal-volume";args[n++]=m->selected_pending.volume;args[n++]="--journal-path";args[n++]=m->selected_pending.path;args[n++]="--expected-journal-token";args[n++]=m->selected_pending.token;}
        else{args[n++]="--source-volume";args[n++]=m->source_volume;args[n++]="--source-path";args[n++]=m->source_path;args[n++]="--expected-source-token";args[n++]=m->source_token;args[n++]="--backup-path";args[n++]=m->backup_path;}
    }else if(kind==J_INSTALL_EXECUTE){
        if(!m->plan_valid||!m->can_execute||!m->review_bottom_seen||m->review_scroll<install_review_max_scroll(a)||(uint64_t)time(NULL)>m->expires||!install_hex(m->seal,64)||!install_hex(m->plan_hash,64)||!m->confirmation[0])return false;
        args[n++]="--plan-file";args[n++]=m->plan_path;args[n++]="--expected-plan-seal";args[n++]=m->seal;args[n++]="--expected-plan-file-sha256";args[n++]=m->plan_hash;args[n++]="--confirmation";args[n++]=m->confirmation;
        m->plan_valid=m->can_execute=false;m->uncertain=m->reboot_blocked=true;m->known=false;
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;a->result_return=INSTALL;return true;
}
static bool parse_install_status(App*a,const Json*j){
    InstallState*m=&a->install;char boot[80],codes[sizeof(m->status_blockers)],stock_codes[sizeof(m->stock_blockers)];
    if(!jequals(j,0,"schema","a22x.install.status.v1")||!install_string(j,0,"boot_id",boot,sizeof(boot),false)||
       !install_false(j,0,"auto_reboot")||json_bool(j,json_get(j,0,"backup_required"),-1)!=1||!install_codes(j,0,"blockers",codes,sizeof(codes)))return false;
    int caps=json_get(j,0,"capabilities"),hold=json_get(j,0,"required_hold_ms"),profile=json_get(j,0,"stock_profile"),pending=json_get(j,0,"pending");
    int flash=json_bool(j,json_get(j,caps,"flash_boot"),-1),stock=json_bool(j,json_get(j,caps,"install_stock"),-1),resume=json_bool(j,json_get(j,caps,"resume"),-1),available=json_bool(j,json_get(j,profile,"available"),-1),qualified=json_bool(j,json_get(j,profile,"qualified"),-1);
    if(flash<0||stock<0||resume<0||available<0||qualified<0||!install_codes(j,profile,"blockers",stock_codes,sizeof(stock_codes))||(qualified&&(!available||stock_codes[0]))||!install_false(j,caps,"android_mount")||!install_false(j,caps,"android_backup_restore")||json_bool(j,json_get(j,caps,"userdata_reset"),-1)!=1||
       json_uint(j,json_get(j,hold,"flash-boot"),0)!=3000||json_uint(j,json_get(j,hold,"install-stock"),0)!=5000||pending<0||j->tokens[pending].type!=J_ARRAY)return false;
    InstallPending entries[INSTALL_PENDING];unsigned count=0;
    for(int i=pending+1;i<json_next(j,pending);i=json_next(j,i)){
        if(count>=INSTALL_PENDING||j->tokens[i].type!=J_OBJECT)return false;
        InstallPending*p=&entries[count];memset(p,0,sizeof(*p));
        if(!install_string(j,i,"transaction_id",p->id,sizeof(p->id),false)||!install_hex(p->id,32)||!install_string(j,i,"operation",p->operation,sizeof(p->operation),false)||!install_underlying_operation(p->operation)||
           !install_string(j,i,"journal_volume",p->volume,sizeof(p->volume),false)||strncmp(p->volume,"sdcard",6)||!install_string(j,i,"journal_path",p->path,sizeof(p->path),false)||p->path[0]!='/'||
           !install_string(j,i,"journal_volume_token",p->token,sizeof(p->token),false)||!install_hex(p->token,64)||!install_string(j,i,"state",p->state,sizeof(p->state),false))return false;
        int resumable=json_bool(j,json_get(j,i,"resumable"),-1);if(resumable<0)return false;p->resumable=resumable;
        for(unsigned n=0;n<count;n++)if(!strcmp(entries[n].id,p->id))return false;
        count++;
    }
    if(m->boot[0]&&strcmp(m->boot,boot)){m->source_path[0]=m->source_volume[0]=m->source_token[0]=m->backup_path[0]=0;m->review_resume=false;}
    copy(m->boot,sizeof(m->boot),boot);copy(m->status_blockers,sizeof(m->status_blockers),codes);copy(m->stock_blockers,sizeof(m->stock_blockers),stock_codes);m->known=true;m->flash_boot=flash;m->stock=stock;m->can_resume=resume;m->stock_profile=available;m->stock_qualified=qualified;
    jstring(j,profile,"id",m->profile_id,sizeof(m->profile_id));jstring(j,profile,"firmware",m->firmware,sizeof(m->firmware));
    memcpy(m->pending,entries,(size_t)count*sizeof(*entries));m->pending_count=count;
    m->uncertain=m->uncertain&&codes[0];m->reboot_blocked=count!=0||m->uncertain||codes[0];
    m->pending_scroll=(int64_t)scroll_clamp((double)m->pending_scroll,(double)install_pending_max_scroll(a));a->button_count=0;a->focus=-1;cancel_pointer(a);return true;
}
static bool install_parse_targets(const Json*j,int plan,const char*key,InstallTarget*out,unsigned*count,bool skip){
    int array=json_get(j,plan,key);if(array<0||j->tokens[array].type!=J_ARRAY)return false;*count=0;
    for(int i=array+1;i<json_next(j,array);i=json_next(j,i)){
        if(*count>=INSTALL_TARGETS||j->tokens[i].type!=J_OBJECT)return false;
        InstallTarget*t=&out[*count];memset(t,0,sizeof(*t));
        if(!install_string(j,i,"label",t->label,sizeof(t->label),false)||!install_string(j,i,"path",t->path,sizeof(t->path),false)||t->path[0]!='/'||!install_string(j,i,"action",t->action,sizeof(t->action),false)||(skip?strcmp(t->action,"skip-identical"):(strcmp(t->action,"write-raw")&&strcmp(t->action,"write-sparse")))||
           !install_string(j,i,"before_sha256",t->before,sizeof(t->before),false)||!install_hex(t->before,64)||!install_string(j,i,"after_sha256",t->after,sizeof(t->after),false)||!install_hex(t->after,64))return false;
        for(const char*p=t->label;*p;p++)if(!((*p>='a'&&*p<='z')||(*p>='0'&&*p<='9')||*p=='_'))return false;
        t->bytes=json_uint(j,json_get(j,i,"size_bytes"),UINT64_MAX);t->partition=json_uint(j,json_get(j,i,"partition_number"),UINT64_MAX);t->start=json_uint(j,json_get(j,i,"start_sector"),UINT64_MAX);
        if(!t->bytes||t->bytes==UINT64_MAX||!t->partition||t->partition==UINT64_MAX||t->start==UINT64_MAX||(skip&&strcmp(t->before,t->after)))return false;
        for(unsigned n=0;n<*count;n++)if(!strcmp(out[n].label,t->label)||!strcmp(out[n].path,t->path))return false;
        (*count)++;
    }
    return true;
}
static bool parse_install_plan(App*a,const Json*j,int kind){
    InstallState*m=&a->install;char path[PATH_LEN],hash[80],boot[80],operation[40],source[PATH_LEN],volume[128],token[80],backup[PATH_LEN],tx[80],journal_volume[128],journal_path[PATH_LEN],journal_token[80],manifest_hash[80];
    bool resume=kind==J_INSTALL_RESUME_PLAN;int p=json_get(j,0,"plan");
    if(!jequals(j,0,"schema","a22x.install.plan-result.v1")||!install_string(j,0,"boot_id",boot,sizeof(boot),false)||strcmp(boot,m->boot)||
       p<0||j->tokens[p].type!=J_OBJECT||json_bool(j,json_get(j,0,"plan_summary"),-1)!=1||!install_string(j,0,"plan_path",path,sizeof(path),false)||strcmp(path,m->plan_path)||
       !install_string(j,0,"plan_file_sha256",hash,sizeof(hash),false)||!install_hex(hash,64)||!jequals(j,p,"schema","a22x.install.plan.v1")||!jequals(j,p,"mode",a->device?"device":"fixture")||!jequals(j,p,"boot_id",m->boot)||
       !install_string(j,p,"operation",operation,sizeof(operation),false)||!install_underlying_operation(operation)||!install_string(j,p,"source_volume",volume,sizeof(volume),false)||strncmp(volume,"sdcard",6)||
       !install_string(j,p,"source_path",source,sizeof(source),false)||source[0]!='/'||!install_string(j,p,"source_volume_token",token,sizeof(token),false)||!install_hex(token,64)||
       !install_string(j,p,"backup_path",backup,sizeof(backup),false)||backup[0]!='/'||!install_string(j,p,"backup_manifest_sha256",manifest_hash,sizeof(manifest_hash),false)||!install_hex(manifest_hash,64)||
       !install_string(j,p,"transaction_id",tx,sizeof(tx),false)||!install_hex(tx,32)||!install_string(j,p,"journal_volume",journal_volume,sizeof(journal_volume),false)||
       !install_string(j,p,"journal_path",journal_path,sizeof(journal_path),false)||journal_path[0]!='/'||!install_string(j,p,"journal_volume_token",journal_token,sizeof(journal_token),false)||!install_hex(journal_token,64))return false;
    if(json_bool(j,json_get(j,p,"resuming"),-1)!=(int)resume)return false;
    char expected_journal[160];
    if(snprintf(expected_journal,sizeof(expected_journal),"/.a22x-installs/%s/journal.json",tx)>=(int)sizeof(expected_journal)||
       strcmp(journal_path,expected_journal)||strcmp(journal_volume,volume)||strcmp(journal_token,token))return false;
    if(resume){InstallPending*q=&m->selected_pending;if(strcmp(tx,q->id)||strcmp(operation,q->operation)||strcmp(journal_volume,q->volume)||strcmp(journal_path,q->path)||strcmp(journal_token,q->token))return false;}
    else if(strcmp(operation,kind==J_INSTALL_BOOT_PLAN?"flash-boot":"install-stock")||strcmp(source,m->source_path)||strcmp(volume,m->source_volume)||strcmp(token,m->source_token)||strcmp(backup,m->backup_path))return false;
    bool stock=!strcmp(operation,"install-stock");
    if(json_bool(j,json_get(j,p,"userdata_written"),-1)!=(int)stock||!install_false(j,p,"automatic_factory_reset")||!install_false(j,p,"auto_reboot")||!install_false(j,p,"atomic")||
       !install_string(j,p,"next_action",m->next_action,sizeof(m->next_action),false)||strcmp(m->next_action,stock?"first-android-boot":"none")||
       !install_string(j,p,"seal",m->seal,sizeof(m->seal),false)||!install_hex(m->seal,64)||!install_string(j,p,"confirmation",m->confirmation,sizeof(m->confirmation),false)||!install_codes(j,p,"blockers",m->blockers,sizeof(m->blockers)))return false;
    const char*prefix=stock?"INSTALL-STOCK-":"FLASH-BOOT-";size_t prefix_length=strlen(prefix);
    if(strncmp(m->confirmation,prefix,prefix_length)||!install_hex(m->confirmation+prefix_length,16))return false;
    int allowed=json_bool(j,json_get(j,p,"can_execute"),-1);uint64_t hold=json_uint(j,json_get(j,p,"required_hold_ms"),0),created=json_uint(j,json_get(j,p,"created_utc"),UINT64_MAX),expires=json_uint(j,json_get(j,p,"expires_utc"),0);
    if(allowed<0||hold!=(stock?5000u:3000u)||expires<(uint64_t)time(NULL)||created>expires||expires-created>1800||created>(uint64_t)time(NULL)+5||(allowed&&m->blockers[0]))return false;
    int gate=json_get(j,p,"backup_gate"),complete=json_bool(j,json_get(j,gate,"complete"),-1),verified=json_bool(j,json_get(j,gate,"verified"),-1),same=json_bool(j,json_get(j,gate,"same_device"),-1),current=json_bool(j,json_get(j,gate,"current"),-1),required=json_bool(j,json_get(j,gate,"current_required"),-1);
    int userdata=json_bool(j,json_get(j,gate,"current_userdata"),-1),userdata_required=json_bool(j,json_get(j,gate,"current_userdata_required"),-1);
    if(complete<0||verified<0||same<0||current<0||userdata<0||required!=(int)(stock&&!resume)||userdata_required!=(int)stock||!jequals(j,gate,"profile","linux")||(allowed&&(!complete||!verified||!same||(required&&!current)||(userdata_required&&!userdata))))return false;
    if(!stock){int header=json_get(j,p,"boot_header");if(header<0||j->tokens[header].type!=J_OBJECT||json_bool(j,json_get(j,header,"layout_validated"),-1)!=1||!install_false(j,header,"runtime_boot_tested"))return false;}
    if(!install_parse_targets(j,p,"write_targets",m->writes,&m->write_count,false)||!install_parse_targets(j,p,"skip_targets",m->skips,&m->skip_count,true)||m->write_count+m->skip_count>INSTALL_TARGETS||!(m->write_count+m->skip_count))return false;
    for(unsigned n=0;n<m->write_count;n++)for(unsigned k=0;k<m->skip_count;k++)if(!strcmp(m->writes[n].label,m->skips[k].label)||!strcmp(m->writes[n].path,m->skips[k].path))return false;
    if(!stock){if(m->write_count+m->skip_count!=1||strcmp(m->write_count?m->writes[0].label:m->skips[0].label,"boot"))return false;}
    else{
        unsigned labels=0;if(m->write_count+m->skip_count!=7)return false;
        for(unsigned n=0;n<m->write_count+m->skip_count;n++){
            InstallTarget*t=n<m->write_count?&m->writes[n]:&m->skips[n-m->write_count];int rank=install_stock_rank(t->label);
            if(rank<0||(labels&(1u<<rank)))return false;
            labels|=1u<<rank;
        }
        if(labels!=127)return false;
    }
    bool recovery_written=false;for(unsigned n=0;n<m->write_count;n++)if(!strcmp(m->writes[n].label,"recovery"))recovery_written=true;
    if(!install_false(j,p,"custom_recovery_replaced"))return false;
    int order=json_get(j,p,"write_order");if(order<0||j->tokens[order].type!=J_ARRAY)return false;m->order_count=0;
    for(int i=order+1;i<json_next(j,order);i=json_next(j,i)){
        if(m->order_count>=m->write_count||json_string(j,i,m->order[m->order_count],sizeof(m->order[0]))<=0)return false;
        bool found=false;for(unsigned n=0;n<m->write_count;n++)if(!strcmp(m->writes[n].label,m->order[m->order_count]))found=true;
        for(unsigned n=0;n<m->order_count;n++)if(!strcmp(m->order[n],m->order[m->order_count]))return false;
        if(!found)return false;
        m->order_count++;
    }
    if(m->order_count!=m->write_count)return false;
    if(stock)for(unsigned n=1;n<m->order_count;n++)if(install_stock_rank(m->order[n])<=install_stock_rank(m->order[n-1]))return false;
    int checkpoint=json_get(j,p,"checkpoint"),labels=json_get(j,checkpoint,"labels");
    if(!install_string(j,checkpoint,"path",m->checkpoint,sizeof(m->checkpoint),false)||m->checkpoint[0]!='/'||labels<0||j->tokens[labels].type!=J_ARRAY)return false;
    m->checkpoint_labels[0]=0;unsigned label_count=0;
    for(int i=labels+1;i<json_next(j,labels);i=json_next(j,i)){
        char label[64];int length=json_string(j,i,label,sizeof(label));if(length<=0||strlen(label)!=(size_t)length||++label_count>INSTALL_TARGETS||strlen(m->checkpoint_labels)+(size_t)length+2>=sizeof(m->checkpoint_labels))return false;
        if(m->checkpoint_labels[0])append(m->checkpoint_labels,sizeof(m->checkpoint_labels),", ");
        append(m->checkpoint_labels,sizeof(m->checkpoint_labels),label);
    }
    m->checkpoint_bytes=json_uint(j,json_get(j,checkpoint,"required_bytes"),UINT64_MAX);if(!label_count||!m->checkpoint_bytes||m->checkpoint_bytes==UINT64_MAX)return false;
    struct stat st;if(lstat(path,&st)<0||!S_ISREG(st.st_mode)||st.st_uid!=geteuid()||st.st_nlink!=1||(st.st_mode&0777)!=0600||st.st_size<=0||st.st_size>4*1024*1024)return false;
    copy(m->plan_hash,sizeof(m->plan_hash),hash);copy(m->operation,sizeof(m->operation),operation);copy(m->source_path,sizeof(m->source_path),source);copy(m->source_volume,sizeof(m->source_volume),volume);copy(m->source_token,sizeof(m->source_token),token);copy(m->backup_path,sizeof(m->backup_path),backup);copy(m->transaction,sizeof(m->transaction),tx);copy(m->journal_path,sizeof(m->journal_path),journal_path);
    int profile=json_get(j,p,"stock_profile");jstring(j,profile,"id",m->profile_id,sizeof(m->profile_id));jstring(j,profile,"firmware",m->firmware,sizeof(m->firmware));
    if(stock&&allowed&&(!m->profile_id[0]||!m->firmware[0]))return false;
    m->backup_complete=complete;m->backup_verified=verified;m->backup_same_device=same;m->backup_current=current;m->backup_current_required=required;
    m->backup_userdata_current=userdata;m->backup_userdata_required=userdata_required;m->recovery_written=recovery_written;
    m->hold_ms=(unsigned)hold;m->expires=expires;m->can_execute=allowed;m->resuming=resume;m->stock_selected=stock;m->plan_kind=kind;
    if(!install_build_review(a,false))return false;
    m->plan_valid=true;screen(a,INSTALL_PLAN);return true;
}
static int complete_install_job(App*a,const Json*j,int kind,bool ok){
    InstallState*m=&a->install;
    if(!ok){
        char code[128]="INSTALLER_FAILED",message[1024]="The image installer did not return a valid result.";
        if(j){int e=json_get(j,0,"error");jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));}
        if(kind==J_INSTALL_STATUS)m->known=false;
        m->review_resume=false;m->plan_valid=m->can_execute=false;copy(m->reason,sizeof(m->reason),message);
        if(kind==J_INSTALL_EXECUTE){m->known=false;m->uncertain=m->reboot_blocked=true;copy(m->note,sizeof(m->note),"Execution was not confirmed. Refresh installation status and inspect its journal. The old execute request will not be repeated.");}
        files_problem(a,code,message,INSTALL);return J_NONE;
    }
    if(!jequals(j,0,"operation",install_operation(kind))||!jequals(j,0,"mode",a->device?"device":"fixture")){
        m->known=m->plan_valid=m->can_execute=false;m->review_resume=false;files_problem(a,"INVALID_INSTALL_RESULT","The installer result did not match the requested environment or operation.",INSTALL);return J_NONE;
    }
    if(kind==J_INSTALL_STATUS){
        if(!parse_install_status(a,j)){m->known=false;m->review_resume=false;files_problem(a,"INVALID_INSTALL_STATUS","The full installer status was not confirmed. Refresh before choosing an operation.",INSTALL);return J_NONE;}
        m->reason[0]=0;
        if(m->review_resume){
            m->review_resume=false;bool found=false;
            for(unsigned n=0;n<m->pending_count;n++)if(install_pending_equal(&m->selected_pending,&m->pending[n]))found=true;
            if(!found){files_problem(a,"INSTALL_TRANSACTION_CHANGED","The selected journal changed. Inspect the refreshed installation status.",INSTALL);return J_NONE;}
            return J_INSTALL_RESUME_PLAN;
        }
        screen(a,INSTALL);return J_NONE;
    }
    if(kind!=J_INSTALL_EXECUTE){
        if(!parse_install_plan(a,j,kind)){m->plan_valid=m->can_execute=false;files_problem(a,"INVALID_INSTALL_PLAN","The exact plan, source, backup, targets, journal or safety policy was not confirmed.",INSTALL);}return J_NONE;
    }
    char tx[80]="",checkpoint[PATH_LEN]="",journal[PATH_LEN]="",expected_journal[PATH_LEN]="";
    bool verified=jequals(j,0,"schema","a22x.install.result.v1")&&jequals(j,0,"boot_id",m->boot)&&jequals(j,0,"install_operation",m->operation)&&jequals(j,0,"next_action",m->next_action)&&
        install_string(j,0,"transaction_id",tx,sizeof(tx),false)&&!strcmp(tx,m->transaction)&&json_bool(j,json_get(j,0,"readback_verified"),-1)==1&&
        json_bool(j,json_get(j,0,"userdata_written"),-1)==(int)!strcmp(m->operation,"install-stock")&&install_false(j,0,"automatic_factory_reset")&&install_false(j,0,"auto_reboot")&&install_false(j,0,"atomic")&&
        install_string(j,0,"checkpoint_path",checkpoint,sizeof(checkpoint),false)&&!strcmp(checkpoint,m->checkpoint)&&install_string(j,0,"journal_path",journal,sizeof(journal),false);
    const char*last=strrchr(m->checkpoint,'/');
    if(!last||strcmp(last,"/platform")||snprintf(expected_journal,sizeof(expected_journal),"%.*s/journal.json",(int)(last-m->checkpoint),m->checkpoint)>=(int)sizeof(expected_journal)||strcmp(expected_journal,journal))verified=false;
    m->known=false;m->plan_valid=m->can_execute=false;
    if(!verified){m->uncertain=m->reboot_blocked=true;files_problem(a,"INSTALL_RECEIPT_UNCONFIRMED","The action returned an incomplete or mismatched write receipt. Refresh installation status. Do not repeat the old plan.",INSTALL);return J_NONE;}
    copy(m->result_checkpoint,sizeof(m->result_checkpoint),checkpoint);copy(m->result_journal,sizeof(m->result_journal),journal);m->stock_replaced=!strcmp(m->operation,"install-stock");m->uncertain=false;m->reboot_blocked=false;
    a->successful=true;copy(m->note,sizeof(m->note),"The last installation completed with independent readback. Boot behavior still needs a deliberate test.");m->reason[0]=0;
    if(m->plan_path[0]){unlink(m->plan_path);m->plan_path[0]=0;}
    if(!install_build_review(a,true)){files_problem(a,"INSTALL_RECEIPT_TOO_LARGE","The write receipt could not be displayed completely. Inspect installation status.",INSTALL);return J_NONE;}
    screen(a,INSTALL_RESULT);return J_NONE;
}
static bool install_action(App*a,int id){
    InstallState*m=&a->install;
    if(id==A_INSTALL||id==A_INSTALL_REFRESH||id==A_INSTALL_RESULT_DONE||(id==A_DONE&&a->result_return==INSTALL)){
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard the text editor before opening image installation.",FILE_EDIT);return true;}
        m->pick_source=m->pick_backup=m->review_resume=false;install_forget_plan(a);m->known=false;m->reason[0]=0;screen(a,INSTALL);start_install_job(a,J_INSTALL_STATUS);return true;
    }
    if(m->pick_backup){
        if(id==A_BACK||id==A_INSTALL_CANCEL_PICK){m->pick_backup=false;screen(a,INSTALL);return true;}
        if(id>=A_BACKUP_ITEM&&id<A_BACKUP_ITEM+a->backup_count){BackupEntry*b=&a->backups[id-A_BACKUP_ITEM];if(backup_is_linux_v2(b)&&b->path[0]=='/'){copy(m->backup_path,sizeof(m->backup_path),b->path);m->pick_backup=false;screen(a,INSTALL);}return true;}
        if(id==A_LIST_PREV||id==A_LIST_NEXT||id==A_DONE)return false;
        return true;
    }
    if(m->pick_source){
        if(id==A_INSTALL_CANCEL_PICK||(id==A_BACK&&a->screen==FILE_VOLUMES)){m->pick_source=false;screen(a,INSTALL);return true;}
        if(id==A_BACK&&a->screen==FILE_BROWSER&&!strcmp(a->fs_path,"/")){screen(a,FILE_VOLUMES);return true;}
        if(id==A_FS_VOLUME){screen(a,FILE_VOLUMES);return true;}
        if(id>=A_FS_VOLUME_ITEM&&id<A_FS_VOLUME_ITEM+a->volume_count){if(install_source_allowed(&a->volumes[id-A_FS_VOLUME_ITEM]))select_volume(a,id-A_FS_VOLUME_ITEM);return true;}
        int entry=id>=A_FS_ENTRY_MENU&&id<A_FS_ENTRY_MENU+a->file_count?id-A_FS_ENTRY_MENU:(id>=A_FS_ENTRY&&id<A_FS_ENTRY+a->file_count?id-A_FS_ENTRY:-1);
        if(entry>=0){a->fs_selected=a->files[entry];m->path_page=0;if(file_is_directory(&a->fs_selected))open_selected_file(a);else{screen(a,FILE_INFO);start_files_job(a,J_FS_STAT);}return true;}
        if(id==A_INSTALL_USE_SOURCE){if(install_source_selected(a)&&m->path_page+1>=path_pages(a->fs_selected.path,640,8)){FileVolume*v=current_volume(a);copy(m->source_volume,sizeof(m->source_volume),v->id);copy(m->source_token,sizeof(m->source_token),v->token);copy(m->source_path,sizeof(m->source_path),a->fs_selected.path);m->pick_source=false;screen(a,INSTALL);}return true;}
        if(id==A_INSTALL_PATH_PREV){if(m->path_page)m->path_page--;a->dirty=true;return true;}
        if(id==A_INSTALL_PATH_NEXT){if(m->path_page+1<path_pages(a->fs_selected.path,640,8))m->path_page++;a->dirty=true;return true;}
        if(id==A_BACK||id==A_FS_PREV||id==A_FS_NEXT||id==A_FS_REFRESH||id==A_FS_UP||id==A_DONE)return false;
        return true;
    }
    if(id==A_INSTALL_BOOT||id==A_INSTALL_STOCK){
        if(!m->pending_count){bool stock=id==A_INSTALL_STOCK;if(m->stock_selected!=stock){install_forget_plan(a);m->source_volume[0]=m->source_path[0]=m->source_token[0]=0;}m->stock_selected=stock;a->dirty=true;}return true;
    }
    if(id==A_INSTALL_SOURCE){if(m->known&&!m->pending_count&&!m->uncertain){install_forget_plan(a);m->pick_source=true;m->path_page=0;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);}return true;}
    if(id==A_INSTALL_BACKUP){if(m->known&&!m->pending_count&&!m->uncertain&&a->repository_count){install_forget_plan(a);m->pick_backup=true;a->list_for_verify=false;start_job(a,J_LIST);a->result_return=INSTALL;}return true;}
    if(id==A_INSTALL_FILES){m->pick_source=false;m->pick_backup=false;install_forget_plan(a);a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;}
    if(id==A_INSTALL_REVIEW){if(install_ready(a)&&m->source_path[0]&&m->backup_path[0])start_install_job(a,m->stock_selected?J_INSTALL_STOCK_PLAN:J_INSTALL_BOOT_PLAN);return true;}
    if(id>=A_INSTALL_PENDING&&id<A_INSTALL_PENDING+(int)m->pending_count&&a->screen==INSTALL){
        InstallPending*p=&m->pending[id-A_INSTALL_PENDING];if(m->known&&m->can_resume&&p->resumable){m->selected_pending=*p;m->review_resume=true;start_install_job(a,J_INSTALL_STATUS);}return true;
    }
    if(id==A_INSTALL_CONFIRM&&a->screen==INSTALL_PLAN){start_install_job(a,J_INSTALL_EXECUTE);return true;}
    if(id==A_INSTALL_POWER&&a->screen==INSTALL_RESULT){screen(a,POWER);return true;}
    if(id==A_BACK&&(a->screen==INSTALL||a->screen==INSTALL_PLAN||a->screen==INSTALL_RESULT)){
        enum Screen from=a->screen,back=from==INSTALL?came_from(a,OS_MENU):INSTALL;install_forget_plan(a);m->review_resume=false;reopen(a,back);if(from!=INSTALL)start_install_job(a,J_INSTALL_STATUS);return true;
    }
    return false;
}
#endif

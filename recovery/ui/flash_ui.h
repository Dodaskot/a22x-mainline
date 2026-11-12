/* SPDX-License-Identifier: MIT
 * Flashing is performed only by flash.py. This screen picks the partition and
 * the file, shows what the helper found, and asks for a long hold.
 */
#ifndef A22X_FLASH_UI_H
#define A22X_FLASH_UI_H

static bool flash_source_allowed(const FileVolume*v){return v&&v->token[0]&&(v->mounted||v->mountable);}
/* Flash a partition names its target; Restore lets the helper find it from the image. */
static const char*flash_target_label(const App*a){
    if(a->flash_restore)return a->flash_label[0]?a->flash_label:"auto";
    return a->flash_selected>=0&&a->flash_selected<(int)a->flash_target_count?a->flash_targets[a->flash_selected].label:"";
}
static enum Screen flash_home(const App*a){return a->flash_restore?LIST:FLASH;}
/* After an image that was refused or reviewed: the folder it was chosen in, to choose another
 * (2026-11-10, as TWRP returns to its list of files); else the list of partitions. */
static enum Screen flash_repick(App*a,enum Screen from){
    if(from!=FILE_BROWSER||!current_volume(a)||!current_volume(a)->mounted)return flash_home(a);
    a->flash_pick=true;return FILE_BROWSER;
}
static bool start_flash_job(App*a,int kind){
    if(a->job.pid||a->preview||!flash_job(kind))return false;
    char*args[20];int n=0;args[n++]=a->python;args[n++]=a->flash_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_FLASH_TARGETS?"targets":(kind==J_FLASH_CHECK?"check":"write");
    if(kind!=J_FLASH_TARGETS){
        const char*label=flash_target_label(a);
        if(!label[0]||(kind==J_FLASH_WRITE&&!strcmp(label,"auto")))return false;
        args[n++]="--target";args[n++]=(char*)label;args[n++]="--volume";args[n++]=a->flash_volume;
        args[n++]="--volume-token";args[n++]=a->flash_volume_token;args[n++]="--path";args[n++]=a->flash_path;
        if(kind==J_FLASH_WRITE){args[n++]="--expected-sha256";args[n++]=a->flash_sha;args[n++]="--expected-identity";args[n++]=a->flash_identity;args[n++]="--confirm";}
        /* A logical partition grows to the checked image before it is written (2026-11-10). */
        static char payload[24];
        if(kind==J_FLASH_WRITE&&!strncmp(label,"super/",6)){snprintf(payload,sizeof(payload),"%llu",(unsigned long long)a->flash_payload);args[n++]="--image-bytes";args[n++]=payload;}
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;a->result_return=flash_home(a);return true;
}
static void complete_flash_job(App*a,const Json*j,int kind,bool ok){
    if(!ok){
        char code[96]="FLASH_FAILED",message[384]="The helper did not return a result.";
        if(j){int error=json_get(j,0,"error");if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_FLASH_TARGETS){a->flash_known=false;copy(a->flash_reason,sizeof(a->flash_reason),message);a->dirty=true;return;}
        a->flash_review_valid=false;files_problem(a,code,message,kind==J_FLASH_CHECK?flash_repick(a,nav_top(a)):flash_home(a));return;
    }
    if(kind==J_FLASH_TARGETS){
        a->flash_target_count=0;int list=json_get(j,0,"targets");if(list<0||j->tokens[list].type!=J_ARRAY)return;
        for(int i=list+1;i<json_next(j,list)&&a->flash_target_count<FLASH_TARGETS;i=json_next(j,i)){
            FlashTarget*t=&a->flash_targets[a->flash_target_count];jstring(j,i,"label",t->label,sizeof(t->label));jstring(j,i,"title",t->title,sizeof(t->title));
            t->bytes=json_uint(j,json_get(j,i,"size_bytes"),0);t->logical=json_bool(j,json_get(j,i,"logical"),0)==1;t->room=json_uint(j,json_get(j,i,"room_bytes"),0);
            if(t->label[0])a->flash_target_count++;
        }
        a->flash_super_free=json_uint(j,json_get(j,0,"super_free_bytes"),0);
        a->flash_known=a->flash_target_count>0;a->flash_reason[0]=0;a->dirty=true;
        /* "Flash it to BOOT" after the stock BOOT was taken out of the firmware (2026-11-10): the
           partitions are known now, so BOOT is chosen and the kept copy checked like any image. */
        if(a->flash_after_targets){
            a->flash_after_targets=false;a->flash_selected=-1;
            for(unsigned int n=0;n<a->flash_target_count;n++)if(!strcmp(a->flash_targets[n].label,"boot"))a->flash_selected=(int)n;
            /* Not for a page that was left while the partitions were read. */
            if(a->flash_selected>=0&&!a->nav_hold)start_flash_job(a,J_FLASH_CHECK);
        }
        return;
    }
    if(kind==J_FLASH_CHECK){
        int image=json_get(j,0,"image"),target=json_get(j,0,"target");char label[24]={0};if(target>=0)jstring(j,target,"label",label,sizeof(label));
        /* The helper chose the partition in Restore; anything but these two is refused here as well. */
        bool known=a->flash_restore?(!strcmp(label,"userdata")||!strcmp(label,"boot")):!strcmp(label,flash_target_label(a));
        if(image<0||!known){files_problem(a,"INVALID_FLASH_REVIEW","The helper did not describe the image.",flash_repick(a,nav_top(a)));return;}
        if(a->flash_restore)copy(a->flash_label,sizeof(a->flash_label),label);
        a->flash_offline=json_bool(j,json_get(j,target,"offline"),1)!=0;a->flash_checksum[0]=0;
        a->flash_logical=json_bool(j,json_get(j,target,"logical"),0)==1;a->flash_size_now=json_uint(j,json_get(j,target,"size_bytes"),0);
        a->flash_size_after=json_uint(j,json_get(j,target,"new_size_bytes"),a->flash_size_now);a->flash_payload=json_uint(j,json_get(j,image,"payload_bytes"),0);jstring(j,image,"checksum_file",a->flash_checksum,sizeof(a->flash_checksum));
        jstring(j,image,"name",a->flash_name,sizeof(a->flash_name));jstring(j,image,"kind",a->flash_kind,sizeof(a->flash_kind));jstring(j,image,"sha256",a->flash_sha,sizeof(a->flash_sha));
        jstring(j,image,"identity",a->flash_identity,sizeof(a->flash_identity));a->flash_bytes=json_uint(j,json_get(j,image,"file_bytes"),0);
        a->flash_identical=json_bool(j,json_get(j,0,"identical"),0);jstring(j,0,"rollback_volume",a->flash_rollback,sizeof(a->flash_rollback));
        /* What a BOOT or recovery holds, from its own contents (2026-11-10): shown as found, untranslated. */
        a->flash_contents[0]=0;int contents=json_get(j,0,"contents");
        if(contents>=0&&j->tokens[contents].type==J_OBJECT){
            char release[72]={0},android[24]={0},patch[16]={0},phone[72]={0};
            jstring(j,contents,"linux",release,sizeof(release));jstring(j,contents,"android",android,sizeof(android));
            jstring(j,contents,"patch_level",patch,sizeof(patch));jstring(j,contents,"phone",phone,sizeof(phone));
            if(json_bool(j,json_get(j,contents,"mainline"),0)==1&&release[0])snprintf(a->flash_contents,sizeof(a->flash_contents),"Linux %s  /  %s",release,phone);
            else if(android[0])snprintf(a->flash_contents,sizeof(a->flash_contents),"Android %s (%s)  /  %s  /  Linux %s",android,patch,phone,release);
        }
        a->flash_warnings[0]=0;int warnings=json_get(j,0,"warnings");
        if(warnings>=0&&j->tokens[warnings].type==J_ARRAY)for(int i=warnings+1;i<json_next(j,warnings);i=json_next(j,i)){
            char line[160];if(json_string(j,i,line,sizeof(line))<=0)continue;if(a->flash_warnings[0])append(a->flash_warnings,sizeof(a->flash_warnings),"\n");append(a->flash_warnings,sizeof(a->flash_warnings),tr(line));
        }
        a->flash_review_valid=valid_sha256(a->flash_sha)&&valid_sha256(a->flash_identity)&&(!a->flash_logical||a->flash_payload>0);
        if(!a->flash_review_valid){files_problem(a,"INVALID_FLASH_REVIEW","The helper did not describe the image.",flash_repick(a,nav_top(a)));return;}
        screen(a,FLASH_REVIEW);return;
    }
    char expected[80]={0};jstring(j,0,"sha256",expected,sizeof(expected));a->flash_review_valid=false;
    if(json_bool(j,json_get(j,0,"verified"),0)!=1||strcmp(expected,a->flash_sha)){files_problem(a,"FLASH_UNCONFIRMED","The result of the write could not be confirmed.",flash_home(a));return;}
    a->successful=true;copy(a->status,sizeof(a->status),a->flash_restore?"Partition restored":"Partition flashed");char kept[PATH_LEN]={0};int rollback=json_get(j,0,"rollback");
    if(rollback>=0&&j->tokens[rollback].type==J_OBJECT){char volume[96]={0},path[256]={0};jstring(j,rollback,"volume",volume,sizeof(volume));jstring(j,rollback,"path",path,sizeof(path));snprintf(kept,sizeof(kept),"\n\n%s\n%s: %s",T("The old content was saved here:"),volume,path);}
    snprintf(a->message,sizeof(a->message),T("%s was written and read back. Every byte matches the image.%s"),flash_target_label(a),kept);
    /* The BOOT that was there, kept on cache first (2026-11-10): the way back. */
    {char saved[PATH_LEN]={0};jstring(j,0,"kept_boot",saved,sizeof(saved));
     if(saved[0]){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("The BOOT that was there is kept on cache:"));
         append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),saved);}}
    /* A Linux copy usually comes with its own kernel; it belongs on BOOT too. */
    if(a->flash_restore&&!strcmp(a->flash_label,"userdata")){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),T("If the backup also has a BOOT image, restore it the same way before starting Linux."));}
    a->result_return=flash_home(a);screen(a,RESULT);
}
static void render_flash(App*a){
    heading(a,"Flash a partition","Pick a partition, then an image from SD, USB stick or phone storage.",true);
    if(!a->flash_known){wrapped(a,48,300,624,1,MUTED,a->job.pid?"Reading partitions...":(a->flash_reason[0]?a->flash_reason:"Partitions are unavailable."),6);return;}
    /* Each partition with a tinted icon, as on the home page; the note follows the last row. */
    static const struct{const char*label;int glyph;unsigned int tone;}looks[]={{"boot",A_OS,0},{"dtbo",ICON_TUNE,3},{"vbmeta",ICON_LOCK,5},
        {"super",A_STORAGE,4},{"prism",ICON_PHONE,1},{"optics",ICON_PHONE,1},{"recovery",A_RESTORE,2}};
    int y=244;bool inside=false;for(unsigned int n=0;n<a->flash_target_count;n++)inside=inside||a->flash_targets[n].logical;
    for(unsigned int n=0;n<a->flash_target_count;n++){
        FlashTarget*t=&a->flash_targets[n];if(t->logical)continue;
        /* With logical partitions, super opens its own page: the whole image or one inside (2026-11-10). */
        int action=inside&&!strcmp(t->label,"super")?A_FLASH_SUPER:A_FLASH_TARGET+(int)n,glyph=ICON_FLASH;unsigned int tone=0;Rect r={28,y,664,100};char size[64];human_bytes(t->bytes,size,sizeof(size));
        for(size_t k=0;k<sizeof(looks)/sizeof(looks[0]);k++)if(!strncmp(t->label,looks[k].label,strlen(looks[k].label))){glyph=looks[k].glyph;tone=looks[k].tone;break;}
        surface(a,r,24,PANEL,action);
        gradient(a,(Rect){r.x+18,y+20,60,60},30,ui_theme->tile_fill[tone],ui_theme->tile_fill[tone]);icon(a,glyph,r.x+30,y+32,36,ui_theme->tile_ink[tone]);
        text(a,122,y+12,1,TEXT,t->label);ellipsis(a,122,y+56,380,0,MUTED,t->title);
        text(a,644-text_width(size,0),y+36,0,MUTED,size);icon(a,ICON_CHEVRON,646,y+35,30,MUTED);register_button(a,r,action,ui_free(a),0);
        y+=110;
    }
    text_tile(a,y+14,0,MUTED,"The bootloader, modem and calibration data are not listed and cannot be flashed here.",3);
}
/* super: the whole image (as Odin's AP has it) or one logical partition inside (a GSI's system). */
static void render_flash_super(App*a){
    heading(a,"super","The whole image, or one Android partition inside it.",true);
    int y=244;char size[64],line[160];
    for(unsigned int n=0;n<a->flash_target_count;n++){
        FlashTarget*t=&a->flash_targets[n];if(!t->logical&&strcmp(t->label,"super"))continue;
        Rect r={28,y,664,112};surface(a,r,24,PANEL,A_FLASH_TARGET+(int)n);human_bytes(t->bytes,size,sizeof(size));
        unsigned int tone=t->logical?4u:1u;gradient(a,(Rect){r.x+18,y+24,60,60},30,ui_theme->tile_fill[tone],ui_theme->tile_fill[tone]);
        icon(a,t->logical?A_STORAGE:ICON_FLASH,r.x+30,y+36,36,ui_theme->tile_ink[tone]);
        raw_text(true);text(a,122,y+12,1,TEXT,t->logical?t->label+6:"super");raw_text(false);
        if(t->logical){char room[64];human_bytes(t->room,room,sizeof(room));snprintf(line,sizeof(line),T("Can grow by %s"),room);}
        else copy(line,sizeof(line),tr("The whole image of all of them"));
        ellipsis(a,122,y+60,380,0,MUTED,line);text(a,644-text_width(size,0),y+40,0,MUTED,size);icon(a,ICON_CHEVRON,646,y+39,30,MUTED);
        register_button(a,r,A_FLASH_TARGET+(int)n,ui_free(a),0);y+=124;
    }
    char free_[64];human_bytes(a->flash_super_free,free_,sizeof(free_));snprintf(line,sizeof(line),T("Free in super: %s"),free_);text(a,40,y+10,0,MUTED,line);
    text_tile(a,y+60,0,MUTED,"A GSI's system.img goes into system. When the image is larger, the partition grows into the free space of super; the other partitions stay as they are.",5);
}
static void render_flash_review(App*a){
    char size[64],line[160];human_bytes(a->flash_bytes,size,sizeof(size));
    heading(a,a->flash_restore?"Restore from an image":"Review","Nothing has been written yet.",true);
    rounded(a,(Rect){28,240,664,120},24,PANEL);text(a,52,258,0,ORANGE,"PARTITION");text(a,52,296,2,TEXT,flash_target_label(a));
    rounded(a,(Rect){28,376,664,296},24,PANEL);text(a,52,394,0,ORANGE,"IMAGE");
    raw_text(true);wrapped(a,52,432,606,1,TEXT,a->flash_name,2);raw_text(false);
    snprintf(line,sizeof(line),"%s  /  %s",tr(a->flash_kind),size);ellipsis(a,52,520,606,0,MUTED,line);
    text(a,52,566,0,ORANGE,"SHA-256");snprintf(line,sizeof(line),"%.32s",a->flash_sha);text(a,52,602,0,MUTED,line);snprintf(line,sizeof(line),"%.32s",a->flash_sha+32);text(a,52,634,0,MUTED,line);
    int y=700;
    if(a->flash_contents[0]){raw_text(true);y+=wrapped(a,40,y,640,0,GREEN,a->flash_contents,2)+16;raw_text(false);}
    /* Nothing is written then: no size change and no copy are announced (2026-11-10). */
    if(a->flash_identical)y+=text_tile(a,y,1,GREEN,"This image is already on the partition. There is nothing to flash.",3)+16;
    if(a->flash_logical&&!a->flash_identical){
        char now[48],after[48];human_bytes(a->flash_size_now,now,sizeof(now));human_bytes(a->flash_size_after,after,sizeof(after));
        if(a->flash_size_after>a->flash_size_now)snprintf(line,sizeof(line),T("The partition grows from %s to %s inside super."),now,after);
        else if(a->flash_payload==a->flash_size_now)snprintf(line,sizeof(line),T("The image fills the whole %s partition."),now);
        else snprintf(line,sizeof(line),T("The image fills part of the %s partition; the rest is zeroed."),now);
        y+=wrapped(a,40,y,640,0,GREEN,line,2)+16;
    }
    if(a->flash_restore){
        if(a->flash_checksum[0]){snprintf(line,sizeof(line),T("Matches the checksum saved with it: %s"),a->flash_checksum);y+=wrapped(a,40,y,640,0,GREEN,line,2)+16;}
        else y+=text_tile(a,y,0,MUTED,"No checksum was saved next to this image. The image is checked again while it is written.",3)+16;
    }
    if(a->flash_warnings[0])y+=text_tile(a,y,0,a->flash_offline?ORANGE:RED,a->flash_warnings,5)+16;
    if(a->flash_rollback[0]&&!a->flash_identical){snprintf(line,sizeof(line),T("The current content will be saved to %s first."),a->flash_rollback);y+=text_tile(a,y,0,MUTED,line,2)+16;}
    text_tile(a,y,0,MUTED,"Recovery stays on the phone, so you can come back here and flash again.",3);
    button(a,(Rect){28,1320,664,104},a->flash_restore?"Hold 3 seconds to restore":"Hold 3 seconds to flash",A_FLASH_CONFIRM,ui_free(a)&&a->flash_review_valid&&!a->flash_identical&&a->flash_offline,true,3000);
}
/* Choosing a file only reads, so the picker offers nothing that changes storage. */
static bool pick_action(App*a,int id,void(*chosen)(App*)){
    /* A card chosen in the sheet returns to the sheet; the other pickers to their page of volumes. */
    if(id==A_FS_VOLUME||(id==A_BACK&&a->screen==FILE_BROWSER&&!strcmp(a->fs_path,"/"))){if(!source_back(a))screen(a,FILE_VOLUMES);return true;}
    if(id>=A_FS_VOLUME_ITEM&&id<A_FS_VOLUME_ITEM+a->volume_count){
        int index=id-A_FS_VOLUME_ITEM;const FileVolume*v=&a->volumes[index];if(!flash_source_allowed(v))return true;
        bool mounted=v->mounted;select_volume(a,index);
        /* An unmounted volume opens read-only by itself. */
        if(!mounted){FileRequest q=file_request(a,J_FS_MOUNT);copy(q.mode,sizeof(q.mode),"ro");start_files_request(a,&q);}
        return true;
    }
    int entry=id>=A_FS_ENTRY_MENU&&id<A_FS_ENTRY_MENU+a->file_count?id-A_FS_ENTRY_MENU:(id>=A_FS_ENTRY&&id<A_FS_ENTRY+a->file_count?id-A_FS_ENTRY:-1);
    if(entry>=0){
        a->fs_selected=a->files[entry];
        if(file_is_directory(&a->fs_selected))open_selected_file(a);
        else if(file_is_regular(&a->fs_selected)&&current_volume(a))chosen(a);
        return true;
    }
    return !(id==A_BACK||id==A_FS_PREV||id==A_FS_NEXT||id==A_FS_REFRESH||id==A_FS_UP||id==A_FS_OPEN||id==A_FS_MOUNT_RO||id==A_DONE);
}
static void flash_chosen(App*a){FileVolume*v=current_volume(a);copy(a->flash_volume,sizeof(a->flash_volume),v->id);copy(a->flash_volume_token,sizeof(a->flash_volume_token),v->token);copy(a->flash_path,sizeof(a->flash_path),a->fs_selected.path);a->flash_pick=false;start_flash_job(a,J_FLASH_CHECK);}
/* Everything a theme needs from the outside: the list, a file, and leaving again. */
static bool theme_action(App*a,int id){
    if(id==A_THEME_ADD||id==A_TERMINAL_ADD){a->theme_pick=a->theme_return=id==A_THEME_ADD?THEME:TERMINAL_THEME;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;}
    if(id==A_THEME_USE){a->theme_return=THEME;start_theme_job(a,J_THEME_IMPORT);return true;}
    if(id>=A_THEME_REMOVE&&id<A_THEME_REMOVE+THEME_FILE_MAX){
        unsigned int n=(unsigned int)(id-A_THEME_REMOVE);if(n>=theme_file_count)return true;
        if(a->theme_armed!=id||a->now>=a->theme_armed_until){toast(a,"Tap again to remove");a->toast_icon=ICON_TRASH;a->theme_armed=id;a->theme_armed_until=a->toast_until;return true;}
        a->theme_armed=0;a->theme_return=a->screen;copy(a->theme_remove_code,sizeof(a->theme_remove_code),theme_files[n].header.code);start_theme_job(a,J_THEME_REMOVE);return true;
    }
    if(id==A_THEME_RESTORE){a->theme_return=THEME;start_theme_job(a,J_THEME_RESTORE);return true;}
    if(a->theme_pick){
        if(id==A_THEME_CANCEL_PICK||(id==A_BACK&&a->screen==FILE_VOLUMES)){enum Screen back=a->theme_pick;a->theme_pick=HOME;screen(a,back);return true;}
        return pick_action(a,id,theme_file_chosen);
    }
    return false;
}
static bool flash_action(App*a,int id){
    if(id==A_RESTORE_IMAGE){
        a->flash_restore=a->flash_pick=true;a->flash_review_valid=false;a->flash_label[0]=0;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;
        screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    }
    if(id==A_FLASH||(id==A_DONE&&a->result_return==FLASH)){a->flash_restore=false;a->flash_pick=a->flash_review_valid=false;a->flash_known=false;a->flash_reason[0]=0;screen(a,FLASH);start_flash_job(a,J_FLASH_TARGETS);return true;}
    if(id==A_FLASH_SUPER&&a->screen==FLASH){screen(a,FLASH_SUPER);return true;}
    if(id==A_BACK&&a->screen==FLASH_SUPER){screen(a,FLASH);return true;}
    if((a->screen==FLASH||a->screen==FLASH_SUPER)&&id>=A_FLASH_TARGET&&id<A_FLASH_TARGET+(int)a->flash_target_count){
        a->flash_selected=id-A_FLASH_TARGET;a->flash_pick=true;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    }
    if(a->flash_pick){
        /* Samsung's firmware is chosen with the same picker (2026-11-10): its AP file. */
        /* Back to the page the image was asked for on: the partitions, those inside super, or Restore. */
        if((id==A_FLASH_CANCEL_PICK||(id==A_BACK&&a->screen==FILE_VOLUMES))&&a->zip_adding&&a->zip_count){
            a->zip_adding=false;a->flash_pick=a->samsung_pick=a->stock_boot_pick=a->zip_pick=a->rom_pick=false;
            zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);a->zip_known=true;screen(a,ZIP_REVIEW);return true;
        }
        if(id==A_FLASH_CANCEL_PICK||(id==A_BACK&&a->screen==FILE_VOLUMES)){bool firmware=a->samsung_pick||a->zip_pick||a->rom_pick;enum Screen from=came_from_picker(a,flash_home(a));a->flash_pick=a->samsung_pick=a->stock_boot_pick=a->zip_pick=a->rom_pick=false;screen(a,firmware?ANDROID_MENU:from);return true;}
        return pick_action(a,id,a->rom_pick?rom_chosen:(a->zip_pick?zip_chosen:(a->samsung_pick?(a->stock_boot_pick?stock_boot_chosen:samsung_chosen):flash_chosen)));
    }
    if(id==A_FLASH_CONFIRM&&a->screen==FLASH_REVIEW){if(a->flash_review_valid&&!a->flash_identical)start_flash_job(a,J_FLASH_WRITE);return true;}
    /* Flash a partition also opens from Root and after a stock BOOT was taken out: Back returns there. */
    if(id==A_BACK&&a->screen==FLASH){reopen(a,came_from(a,OS_MENU));return true;}
    /* The review returns to the folder the image was chosen in, to choose another. */
    if(id==A_BACK&&a->screen==FLASH_REVIEW){reopen(a,flash_repick(a,came_from(a,flash_home(a))));return true;}
    return false;
}
#endif

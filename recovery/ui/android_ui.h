/* SPDX-License-Identifier: MIT
 * Android from this recovery (2026-11-10). Samsung's stock firmware is flashed as Odin flashes
 * it, by samsung_firmware.py: the user chooses the AP file, the CSC, BL and CP beside it are
 * found, and the review lists what becomes of every image. Nothing is written before the hold.
 * The Android images of the catalog (GSIs) are one tap away.
 */
#ifndef A22X_ANDROID_UI_H
#define A22X_ANDROID_UI_H

static bool samsung_job(int kind){return kind==J_SAMSUNG_INSPECT||kind==J_SAMSUNG_INSTALL||kind==J_STOCK_BOOT||kind==J_SAMSUNG_UNPACK||kind==J_ROM_KIND;}
static bool start_zip_job(App*a,int kind);
/* A GSI of the catalog has its own page; one chosen from a file returns to its folder (2026-11-11). */
static bool gsi_of_catalog(const App*a){return a->android_gsi&&!a->gsi_file;}
/* What the file chosen as a ROM is: android_gsi.py classify looks into it. */
static bool start_rom_kind(App*a){
    if(a->job.pid||a->preview||!a->gsi_helper[0]||!a->samsung_path[0])return false;
    char*args[14];int n=0;args[n++]=a->python;args[n++]=a->gsi_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="classify";args[n++]="--volume";args[n++]=a->samsung_volume;args[n++]="--volume-token";args[n++]=a->samsung_token;args[n++]="--path";args[n++]=a->samsung_path;args[n]=NULL;
    if(!launch_job(a,J_ROM_KIND,args))return false;
    a->result_return=ANDROID_MENU;return true;
}
static bool start_samsung_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->samsung_helper[0]||!a->samsung_path[0])return false;
    /* A GSI is reviewed and installed by android_gsi.py with the same plan format (2026-11-10). */
    char*args[18];int n=0;args[n++]=a->python;args[n++]=a->android_gsi?a->gsi_helper:a->samsung_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_SAMSUNG_INSPECT?"inspect":(kind==J_STOCK_BOOT?"extract-boot":(kind==J_SAMSUNG_UNPACK?"unpack":"install"));
    args[n++]="--volume";args[n++]=a->samsung_volume;args[n++]="--volume-token";args[n++]=a->samsung_token;args[n++]="--path";args[n++]=a->samsung_path;
    if(kind==J_SAMSUNG_INSTALL){if(!a->samsung_plan[0])return false;args[n++]="--expected-plan";args[n++]=a->samsung_plan;args[n++]="--confirm";}
    if(kind==J_SAMSUNG_UNPACK)args[n++]="--confirm";
    args[n]=NULL;
    if(!launch_job(a,kind,args))return false;
    a->result_return=kind==J_SAMSUNG_INSTALL?SAMSUNG_REVIEW:ANDROID_MENU;return true;
}
static const char*samsung_blocker_text(const char*code){
    if(!strcmp(code,"AP_INCOMPLETE"))return "The AP file has no super or no BOOT image.";
    if(!strcmp(code,"OTHER_MODEL"))return "This firmware is made for another phone.";
    if(!strcmp(code,"DUPLICATE_IMAGE"))return "Two files bring the same partition. Keep one firmware in the folder.";
    if(!strcmp(code,"STOCK_ANDROID_FIRST"))return "Flash the Samsung firmware first: a GSI starts with Samsung's BOOT and vendor.";
    if(!strcmp(code,"NO_ROOM_IN_SUPER"))return "super has no room for this system image.";
    if(!strcmp(code,"VBMETA_NEEDED"))return "This image brings no vbmeta: install a GSI ZIP with vbmeta.img first.";
    if(!strcmp(code,"VBMETA_IN_DOWNLOAD_MODE"))return "vbmeta has verification on, and the bootloader locks it here: prepare vbmeta on the Root page and flash it in Download mode first.";
    if(!strcmp(code,"PROTECTED_DIFFERS"))return "This firmware's dtbo or vbmeta differ from the phone's, and the bootloader locks them here: flash the AP in Download mode (Odin).";
    return code;
}
/* The picker again, in the folder the file was chosen in: true when `from`, the page before, is
   that folder and its volume is still there. */
static bool samsung_repick(App*a,enum Screen from,bool boot){
    if(gsi_of_catalog(a)||from!=FILE_BROWSER||!current_volume(a)||!current_volume(a)->mounted)return false;
    /* Chosen as a ROM on a card: the folder opens for any ROM again. */
    if(a->rom_origin){a->flash_pick=a->rom_pick=true;a->samsung_pick=a->stock_boot_pick=a->zip_pick=a->flash_restore=false;return true;}
    a->flash_pick=a->samsung_pick=true;a->stock_boot_pick=boot;a->flash_restore=false;return true;
}
static void complete_samsung_job(App*a,const Json*j,int kind,bool ok){
    if(kind==J_ROM_KIND&&ok&&j){
        char what[16]={0};jstring(j,0,"kind",what,sizeof(what));
        if(strcmp(what,"installer"))a->zip_adding=false;  /* no ZIP with an installer: nothing joins a list */
        if(!strcmp(what,"installer")){
            /* It installs itself: the ZIP page reviews it and runs its installer after the hold. */
            copy(a->zip_volume,sizeof(a->zip_volume),a->samsung_volume);copy(a->zip_token,sizeof(a->zip_token),a->samsung_token);copy(a->zip_path,sizeof(a->zip_path),a->samsung_path);a->zip_known=false;
            if(!start_zip_job(a,J_ZIP_INSPECT))files_problem(a,"ZIP_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
            return;
        }
        if(!strcmp(what,"samsung")||!strcmp(what,"gsi")){
            a->android_gsi=a->gsi_file=!strcmp(what,"gsi");a->samsung_known=false;
            size_t length=strlen(a->samsung_path);bool zip=!a->android_gsi&&length>4&&!strcasecmp(a->samsung_path+length-4,".zip");
            if(!start_samsung_job(a,zip?J_SAMSUNG_UNPACK:J_SAMSUNG_INSPECT))files_problem(a,"ANDROID_IMAGE_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
            return;
        }
        /* Nothing this recovery installs: back to the folder, to choose another. */
        enum Screen back=samsung_repick(a,nav_top(a),false)?FILE_BROWSER:ANDROID_MENU;
        files_problem(a,"NOT_A_ROM",!strcmp(what,"payload")?"This is an update for phones with two system slots (payload.bin). This phone has one: choose a ROM made for it, or a system image.":
                      "This file is no ROM this recovery installs. Choose a ZIP with an installer, a system image or Samsung's firmware.",back);
        return;
    }
    if(!ok||!j){
        char code[96]="SAMSUNG_FIRMWARE_FAILED",message[384]="The firmware helper did not return a result.";
        if(j){int e=json_get(j,0,"error");if(e>=0){jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));}}
        if(kind==J_SAMSUNG_INSPECT)a->samsung_known=false;
        /* A file that is no firmware: back to the folder it was chosen in, to choose another, as
           TWRP returns to its list of files (2026-11-10); an Android image returns to its page. */
        enum Screen back=kind==J_SAMSUNG_INSTALL?SAMSUNG_REVIEW:(gsi_of_catalog(a)?ANDROID_IMAGE:ANDROID_MENU);
        if(kind!=J_SAMSUNG_INSTALL&&samsung_repick(a,nav_top(a),kind==J_STOCK_BOOT))back=FILE_BROWSER;
        files_problem(a,code,message,back);return;
    }
    if(kind==J_SAMSUNG_UNPACK){
        /* The firmware ZIP is unpacked beside it: its AP is reviewed as any firmware folder. */
        char ap[PATH_LEN]={0};jstring(j,0,"ap",ap,sizeof(ap));
        if(!ap[0]){files_problem(a,"SAMSUNG_FIRMWARE_FAILED","The firmware helper did not return a result.",ANDROID_MENU);return;}
        copy(a->samsung_path,sizeof(a->samsung_path),ap);
        if(!start_samsung_job(a,J_SAMSUNG_INSPECT))files_problem(a,"SAMSUNG_FIRMWARE_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
        return;
    }
    if(kind==J_STOCK_BOOT){
        /* Samsung's BOOT out of the firmware, kept on cache (2026-11-10): flashed now or later. */
        char release[96]={0},build[40]={0};jstring(j,0,"release",release,sizeof(release));jstring(j,0,"build",build,sizeof(build));
        jstring(j,0,"saved_boot",a->stock_boot_path,sizeof(a->stock_boot_path));jstring(j,0,"volume_token",a->stock_boot_token,sizeof(a->stock_boot_token));
        a->successful=a->stock_boot_path[0]!=0;copy(a->status,sizeof(a->status),"Stock BOOT kept");
        snprintf(a->message,sizeof(a->message),T("Samsung's BOOT %s (Android kernel %s) is kept on cache:"),build[0]?build:"",release);
        append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),a->stock_boot_path);append(a->message,sizeof(a->message),"\n\n");
        append(a->message,sizeof(a->message),tr("Flash it to BOOT now, or later."));
        a->android_installed=false;a->result_return=ANDROID_MENU;screen(a,RESULT);return;
    }
    if(kind==J_SAMSUNG_INSPECT){
        a->samsung_row_count=0;a->samsung_warnings[0]=a->samsung_blockers[0]=a->samsung_files[0]=0;
        jstring(j,0,"build",a->samsung_build,sizeof(a->samsung_build));jstring(j,0,"plan_sha256",a->samsung_plan,sizeof(a->samsung_plan));
        a->samsung_write_bytes=json_uint(j,json_get(j,0,"write_bytes"),0);
        int rows=json_get(j,0,"rows");
        if(rows>=0&&j->tokens[rows].type==J_ARRAY)for(int i=rows+1;i<json_next(j,rows)&&a->samsung_row_count<SAMSUNG_ROWS;i=json_next(j,i)){
            SamsungRow*r=&a->samsung_rows[a->samsung_row_count];memset(r,0,sizeof(*r));
            jstring(j,i,"name",r->name,sizeof(r->name));jstring(j,i,"file",r->file,sizeof(r->file));jstring(j,i,"action",r->action,sizeof(r->action));
            jstring(j,i,"target",r->target,sizeof(r->target));jstring(j,i,"reason",r->reason,sizeof(r->reason));r->size=json_uint(j,json_get(j,i,"size"),0);
            if(r->name[0]&&r->action[0])a->samsung_row_count++;
        }
        int files=json_get(j,0,"files");
        if(files>=0&&j->tokens[files].type==J_ARRAY)for(int i=files+1;i<json_next(j,files);i=json_next(j,i)){
            char kind_[16]={0};jstring(j,i,"kind",kind_,sizeof(kind_));
            if(kind_[0]){if(a->samsung_files[0])append(a->samsung_files,sizeof(a->samsung_files),"  +  ");append(a->samsung_files,sizeof(a->samsung_files),kind_);}
        }
        int list=json_get(j,0,"warnings");
        if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list);i=json_next(j,i)){
            char line[200];if(json_string(j,i,line,sizeof(line))>0){if(a->samsung_warnings[0])append(a->samsung_warnings,sizeof(a->samsung_warnings),"\n");append(a->samsung_warnings,sizeof(a->samsung_warnings),tr(line));}
        }
        list=json_get(j,0,"blockers");
        if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list);i=json_next(j,i)){
            char code[48];if(json_string(j,i,code,sizeof(code))>0){if(a->samsung_blockers[0])append(a->samsung_blockers,sizeof(a->samsung_blockers),"\n");append(a->samsung_blockers,sizeof(a->samsung_blockers),tr(samsung_blocker_text(code)));}
        }
        a->samsung_known=a->samsung_plan[0]!=0;a->page_scroll=0;screen(a,SAMSUNG_REVIEW);return;
    }
    unsigned int written=0;int list=json_get(j,0,"written");
    if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list);i=json_next(j,i))written++;
    char saved[192]={0};jstring(j,0,"saved_boot",saved,sizeof(saved));
    a->successful=true;copy(a->status,sizeof(a->status),a->android_gsi?"Android image installed":"Stock Android installed");
    snprintf(a->message,sizeof(a->message),T("%u partitions written and checked. userdata is ready for Android, and this recovery stays. The first start of Android takes several minutes."),written);
    if(saved[0]){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("The Linux BOOT is kept on cache:"));append(a->message,sizeof(a->message)," ");append(a->message,sizeof(a->message),saved);}
    /* userdata is Android's now: no Linux switch or installation is pending on it any more. */
    a->android_installed=true;a->samsung_known=false;a->systems_reboot_blocked=a->install.reboot_blocked=false;
    a->result_return=ANDROID_MENU;screen(a,RESULT);
}
/* The catalog's Android image saved on the phone, by its file name in the store. */
static const StoredImage*android_saved(const App*a,const CatalogImage*c){
    if(!a->saved_known)return NULL;
    for(unsigned int i=0;i<a->saved_count;i++)if(!strcmp(a->saved[i].file,c->file))return &a->saved[i];
    return NULL;
}
static void open_android_image(App*a,int n){
    a->android_selected=n;a->page_scroll=0;a->saved_note[0]=0;screen(a,ANDROID_IMAGE);
    /* Asked on every phone (2026-11-11): the helper says where the store is, userdata or a card. */
    if(!a->job.pid){a->saved_known=false;start_images_job(a,J_IMAGES,NULL);}
}
static void android_fact(App*a,int y,const char*label,const char*value){
    text(a,52,y,0,MUTED,label);raw_text(true);ellipsis(a,300,y,370,0,TEXT,value);raw_text(false);
}
static void render_android_image(App*a){
    if(a->android_selected<0||(unsigned int)a->android_selected>=a->catalog_count){heading(a,"Android image","",true);return;}
    const CatalogImage*c=&a->catalog[a->android_selected];bool free_ui=ui_free(a),card=store_on_card(a),ready=systems_ready(a)||card;
    char subtitle[112];
    if(!strcmp(c->category,"port")&&c->notes[0])snprintf(subtitle,sizeof(subtitle),"%s  /  %s",c->notes,tr(catalog_variant_label(c->variant)));
    else copy(subtitle,sizeof(subtitle),tr(!strcmp(c->variant,"gms")?"Google's GSI with Google apps":(!strcmp(c->variant,"aosp")?"Google's GSI, plain AOSP":catalog_variant_label(c->variant))));
    raw_text(true);heading(a,c->name,subtitle,true);raw_text(false);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    char size[48],system[48];human_bytes(c->size,size,sizeof(size));human_bytes(c->system_bytes,system,sizeof(system));
    rounded(a,(Rect){28,y,664,296},24,PANEL);
    android_fact(a,y+24,"Build",c->build);android_fact(a,y+78,"Security patch",c->patch);android_fact(a,y+132,"Region",c->region[0]?c->region:"Global");
    android_fact(a,y+186,"Download",size);android_fact(a,y+240,"System image",system);y+=312;
    /* What a start on this phone model showed comes before the rule (2026-11-11). */
    int box=c->fits&&c->tried<=0?168:200;rounded(a,(Rect){28,y,664,box},24,PANEL);
    text(a,52,y+20,0,c->tried<0?RED:(c->fits?GREEN:ORANGE),c->tried<0?"DID NOT START ON THIS PHONE":(c->tried>0?"STARTED ON THIS PHONE":(c->fits?"FITS THIS PHONE":"MAY NOT START")));
    wrapped(a,52,y+58,616,0,TEXT,c->tried<0?"Tried on an SM-A226B: it restarted into the bootloader instead of starting. You can still try it: this recovery stays, and stock Android can be flashed again.":
            (c->tried>0?"Tried on an SM-A226B: it started. Not everything in it was checked; this recovery stays, and stock Android can be flashed again.":
            (c->fits?(c->fit_note[0]?c->fit_note:"Its Android supports this phone's vendor: Samsung's Android 12 vendor, level 5."):
            "Made for newer vendors, level 6 and up: it may not start here. You can try it: this recovery stays, and stock Android can be flashed again.")),4);
    y+=box+16;
    rounded(a,(Rect){28,y,664,200},24,PANEL);text(a,52,y+20,0,ORANGE,"BEFORE INSTALLING");
    wrapped(a,52,y+58,616,0,TEXT,"A GSI starts with Samsung's BOOT and vendor: flash the Samsung firmware first. Installing formats userdata for Android.",4);
    y+=216;
    const StoredImage*s=android_saved(a,c);
    if(!ready&&a->saved_known&&!a->store_volume[0])y+=text_tile(a,y,0,ORANGE,a->store_reason[0]?a->store_reason:"Insert an SD card or attach a USB drive: downloaded images are kept there.",4)+16;
    else if(!ready&&!a->saved_known&&a->job.pid&&a->job.kind==J_IMAGES)y+=wrapped(a,40,y,640,0,MUTED,"Reading the saved images...",2)+16;
    else if(!ready){y+=text_tile(a,y,0,ORANGE,"Mount userdata with write access: downloaded images are kept there.",3)+16;
        button(a,(Rect){28,y,664,84},"Mount userdata",A_SYS_MOUNT,free_ui&&systems_can_mount(a),false,0);y+=108;}
    else if(!a->saved_known)y+=wrapped(a,40,y,640,0,MUTED,"Reading the saved images...",2)+16;
    else if(s){text(a,40,y,0,GREEN,card?"SAVED ON THE CARD":"SAVED ON THE PHONE");y+=44;}
    else if(card){char where[160];snprintf(where,sizeof(where),T("Android uses userdata: the image is saved on the card %s"),a->store_label);
        raw_text(true);y+=text_tile(a,y,0,MUTED,where,3)+16;raw_text(false);}
    if(a->saved_note[0]){raw_text(true);ellipsis(a,40,y,640,0,GREEN,a->saved_note);raw_text(false);y+=44;}
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,page_height(a)},a->page_scroll,a->page_content,ORANGE);
    if(s){
        button(a,(Rect){28,1206,664,96},"Delete the saved image",A_ANDROID_DELETE,free_ui,false,0);
        button(a,(Rect){28,1320,664,104},"Review the installation",A_ANDROID_INSTALL,free_ui,true,0);
    }else{
        /* Without Linux on userdata the download goes to a card, also when none is there yet. */
        bool to_card=card||(a->saved_known&&!a->store_volume[0]);
        char label[96];snprintf(label,sizeof(label),to_card?T("Download to the card (%s)"):T("Download to the phone (%s)"),size);
        button(a,(Rect){28,1320,664,104},label,A_ANDROID_DOWNLOAD,free_ui&&ready&&a->saved_known&&c->reachable,true,0);
    }
    if(a->systems_mount_sheet)render_systems_mount(a);
}
static void stock_boot_chosen(App*a){
    FileVolume*v=current_volume(a);
    copy(a->samsung_volume,sizeof(a->samsung_volume),v->id);copy(a->samsung_token,sizeof(a->samsung_token),v->token);
    copy(a->samsung_path,sizeof(a->samsung_path),a->fs_selected.path);a->flash_pick=a->samsung_pick=a->stock_boot_pick=false;a->stock_boot_path[0]=0;
    if(!start_samsung_job(a,J_STOCK_BOOT))files_problem(a,"STOCK_BOOT_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
}
/* Root (root.py, 2026-11-10): the newest Magisk from GitHub into Android's BOOT (KernelSU and
   KernelSU Next do not install on the 4.14 kernel); the stock BOOT kept on cache removes root. */
static bool root_job(int kind){return kind==J_ROOT_LIST||kind==J_ROOT_INSTALL||kind==J_ROOT_PREPARE;}
/* The APatch app's rule for a SuperKey: 8 to 63 characters with a letter and a digit; here also
   without spaces or letters outside ASCII, since it is typed again in Android. */
static bool superkey_ok(const char*key){
    size_t length=strlen(key);bool letter=false,digit=false;
    if(length<8||length>63)return false;
    for(size_t n=0;n<length;n++){unsigned char c=(unsigned char)key[n];if(c<0x21||c>0x7e)return false;letter=letter||isalpha(c);digit=digit||isdigit(c);}
    return letter&&digit;
}
static bool start_root_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->root_helper[0])return false;
    char*args[18];int n=0;args[n++]=a->python;args[n++]=a->root_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_ROOT_LIST)args[n++]="list";
    else if(kind==J_ROOT_PREPARE)args[n++]="prepare-vbmeta";
    else{
        if(a->root_selected<0||a->root_selected>=(int)a->root_count)return false;
        RootTool*t=&a->root_tools[a->root_selected];
        args[n++]="install";args[n++]="--id";args[n++]=t->id;args[n++]="--expected-sha256";args[n++]=t->sha256;args[n++]="--confirm";
        if(a->root_needs_reset)args[n++]="--erase-data";
        if(t->needs_key){
            /* The SuperKey goes to the helper in a file of the private RAM folder, never as an argument. */
            if(a->root_key_tool!=a->root_selected||!superkey_ok(a->root_superkey))return false;
            if(!private_workdir(a->workdir)||snprintf(a->root_key_file,sizeof(a->root_key_file),"%s/superkey-%ld",a->workdir,(long)getpid())>=(int)sizeof(a->root_key_file)){a->root_key_file[0]=0;return false;}
            int fd=open(a->root_key_file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
            if(fd<0){a->root_key_file[0]=0;return false;}
            size_t length=strlen(a->root_superkey);bool written=write(fd,a->root_superkey,length)==(ssize_t)length&&write(fd,"\n",1)==1;close(fd);
            if(!written){unlink(a->root_key_file);a->root_key_file[0]=0;return false;}
            args[n++]="--superkey-file";args[n++]=a->root_key_file;
        }
    }
    args[n]=NULL;bool launched=launch_job(a,kind,args);
    if(kind==J_ROOT_INSTALL){
        /* Handed over or not: the typed key does not stay in memory. */
        memset(a->root_superkey,0,sizeof(a->root_superkey));a->root_key_tool=-1;
        if(!launched&&a->root_key_file[0]){unlink(a->root_key_file);a->root_key_file[0]=0;}
    }
    if(!launched)return false;
    a->result_return=ROOT_PAGE;return true;
}
static void complete_root_job(App*a,const Json*j,int kind,bool ok){
    if(a->root_key_file[0]){unlink(a->root_key_file);a->root_key_file[0]=0;}
    if(!ok||!j){
        char code[96]="ROOT_FAILED",message[384]="The root helper did not return a result.";
        if(j){int e=json_get(j,0,"error");if(e>=0){jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));}}
        if(kind==J_ROOT_LIST){a->root_known=true;a->root_count=0;copy(a->root_boot_kind,sizeof(a->root_boot_kind),"unknown");a->dirty=true;return;}
        files_problem(a,code,message,ROOT_PAGE);return;
    }
    if(kind==J_ROOT_LIST){
        a->root_count=0;int list=json_get(j,0,"tools");
        if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list)&&a->root_count<ROOT_TOOLS;i=json_next(j,i)){
            RootTool*t=&a->root_tools[a->root_count];memset(t,0,sizeof(*t));
            jstring(j,i,"id",t->id,sizeof(t->id));jstring(j,i,"name",t->name,sizeof(t->name));jstring(j,i,"version",t->version,sizeof(t->version));
            jstring(j,i,"sha256",t->sha256,sizeof(t->sha256));jstring(j,i,"reason",t->reason,sizeof(t->reason));jstring(j,i,"error",t->error,sizeof(t->error));
            jstring(j,i,"published",t->published,sizeof(t->published));t->size=json_uint(j,json_get(j,i,"size"),0);t->supported=json_bool(j,json_get(j,i,"supported"),0)==1;
            t->bundled=json_bool(j,json_get(j,i,"bundled"),0)==1;t->needs_key=json_bool(j,json_get(j,i,"needs_key"),0)==1;
            if(t->id[0])a->root_count++;
        }
        int boot=json_get(j,0,"boot");
        if(boot>=0){jstring(j,boot,"kind",a->root_boot_kind,sizeof(a->root_boot_kind));jstring(j,boot,"release",a->root_boot_release,sizeof(a->root_boot_release));
            a->root_boot_magisk=json_bool(j,json_get(j,boot,"magisk"),0)==1;
            a->root_boot_root[0]=0;jstring(j,boot,"root",a->root_boot_root,sizeof(a->root_boot_root));
            if(!a->root_boot_root[0]&&a->root_boot_magisk)copy(a->root_boot_root,sizeof(a->root_boot_root),"magisk");}
        a->root_needs_reset=json_bool(j,json_get(j,0,"needs_data_reset"),0)==1;
        a->root_verification=json_bool(j,json_get(j,0,"verification"),0)==1;
        jstring(j,0,"stock_boot",a->root_stock_path,sizeof(a->root_stock_path));jstring(j,0,"cache_token",a->root_cache_token,sizeof(a->root_cache_token));
        a->root_known=true;a->dirty=true;return;
    }
    if(kind==J_ROOT_PREPARE){
        /* 2026-11-10: LK write-protects vbmeta while the phone runs: it is flashed in Download mode. */
        char path[PATH_LEN]={0},command[PATH_LEN]={0};jstring(j,0,"vbmeta",path,sizeof(path));jstring(j,0,"command",command,sizeof(command));
        a->successful=true;copy(a->status,sizeof(a->status),"vbmeta prepared");
        snprintf(a->message,sizeof(a->message),"%s\n\n%s\n\n%s\n%s",tr("Flash it in Samsung Download mode, then come back to Root: root is installed and Android's data erased once."),
                 command,tr("The file is on cache:"),path);
        a->root_known=false;a->result_return=ROOT_PAGE;screen(a,RESULT);return;
    }
    char name[32]={0},version[24]={0},kept[PATH_LEN]={0},apk[64]={0},tool[24]={0};jstring(j,0,"name",name,sizeof(name));jstring(j,0,"version",version,sizeof(version));
    jstring(j,0,"kept_boot",kept,sizeof(kept));jstring(j,0,"apk",apk,sizeof(apk));jstring(j,0,"root",tool,sizeof(tool));
    a->successful=true;copy(a->status,sizeof(a->status),"Root installed");
    /* Magisk 31 puts no app into BOOT: root works at once, and the app that manages it comes from GitHub.
       APatch is the same: its app in Android asks for the SuperKey the kernel was patched with. */
    if(!strcmp(tool,"apatch"))snprintf(a->message,sizeof(a->message),T("%s (%s) is in BOOT. In Android, install the APatch app from github.com/bmax121/APatch and enter your SuperKey in it."),name,version);
    else snprintf(a->message,sizeof(a->message),T("%s %s is in BOOT. In Android, install the Magisk app that manages root: %s from github.com/topjohnwu/Magisk."),name,version,apk[0]?apk:"Magisk.apk");
    if(json_get(j,0,"data")>=0){append(a->message,sizeof(a->message),"\n\n");
        append(a->message,sizeof(a->message),tr("Verification is off in vbmeta and Android's data was erased: Android sets itself up again at its first start."));}
    if(kept[0]){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("The BOOT that was there is kept on cache:"));
        append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),kept);}
    a->android_installed=true;a->root_known=false;a->result_return=ANDROID_MENU;screen(a,RESULT);
}
static void open_root(App*a){a->root_known=false;a->root_selected=-1;a->root_key_tool=-1;memset(a->root_superkey,0,sizeof(a->root_superkey));a->page_scroll=0;screen(a,ROOT_PAGE);start_root_job(a,J_ROOT_LIST);}
static void render_superkey_prompt(App*a){
    heading(a,"SuperKey","APatch asks for it in its app in Android.",true);
    account_field(a,236,"SUPERKEY",a->fs_prompt,false,true,A_ROOT_KEY_FIELD);
    bool ready=superkey_ok(a->fs_prompt);
    wrapped(a,40,394,640,0,a->fs_prompt[0]&&!ready?ORANGE:MUTED,"8 to 63 characters without spaces, with a letter and a digit. It is kept nowhere: remember it.",3);
    render_keyboard(a);
    button(a,(Rect){28,1370,256,84},"Cancel",A_BACK,ui_free(a),false,0);
    button(a,(Rect){304,1370,388,84},"Done",A_FS_PROMPT_NEXT,ui_free(a)&&ready,true,0);
}
static void render_root(App*a){
    heading(a,"Root","Magisk or APatch, into Android's BOOT.",true);bool free_ui=ui_free(a),android=!strcmp(a->root_boot_kind,"android");
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    /* The kernel, and on a line of its own the root it carries (2026-11-11: one line cut "Magisk is in it" off). */
    rounded(a,(Rect){28,y,664,a->root_known&&android?176:140},24,PANEL);text(a,52,y+20,0,ORANGE,"BOOT");
    char line[160];
    if(!a->root_known)copy(line,sizeof(line),tr("Reading BOOT and asking GitHub for the newest versions..."));
    else if(android)snprintf(line,sizeof(line),"%s %s",tr("Android kernel"),a->root_boot_release);
    else copy(line,sizeof(line),tr("Not Android's BOOT: install Android first"));
    raw_text(true);ellipsis(a,52,y+62,616,1,TEXT,line);raw_text(false);
    if(a->root_known&&android){
        bool rooted=a->root_boot_root[0]!=0;
        text(a,52,y+118,0,rooted?GREEN:MUTED,!strcmp(a->root_boot_root,"magisk")?"Magisk is in it":(!strcmp(a->root_boot_root,"apatch")?"APatch is in it":"no root"));
        y+=36;
    }
    y+=156;
    if(a->root_known&&a->root_verification){
        rounded(a,(Rect){28,y,664,402},24,PANEL);text(a,52,y+20,0,ORANGE,"VERIFICATION IS ON IN VBMETA");
        wrapped(a,52,y+62,616,0,TEXT,"Root on this Samsung needs vbmeta with verification off. The bootloader locks vbmeta while the phone runs: prepare it here, flash it in Download mode (Heimdall or Odin), then come back.",5);
        /* Said before anything is prepared (2026-11-11, asked for by the owner): the first root costs Android's data. */
        wrapped(a,52,y+216,616,0,RED,"Android's data is erased once when root is installed.",2);
        button(a,(Rect){46,y+286,628,96},"Prepare vbmeta for Download mode",A_ROOT_PREPARE,free_ui,true,0);
        y+=418;
    }
    if(a->root_known&&a->root_needs_reset){
        /* 2026-11-10: on this Samsung a patched BOOT with verification on leaves Android unable to mount its data. */
        rounded(a,(Rect){28,y,664,196},24,PANEL);text(a,52,y+20,0,RED,"ANDROID DATA IS ERASED");
        wrapped(a,52,y+62,616,0,TEXT,"Root on this Samsung needs verification off in vbmeta, and Android's data erased once, as the Magisk app does for Samsung. Later Magisk updates need no reset.",4);
        y+=212;
    }
    if(a->root_known&&android&&!a->root_verification&&!a->root_needs_reset){
        /* 2026-11-11, on the phone: after the one reset, BOOT went Magisk > stock > APatch > stock > Magisk
           and Android kept its data every time. Said here, so nobody expects another reset. */
        y+=text_tile(a,y,0,GREEN,"Verification is off in vbmeta: installing, changing or removing root keeps Android's data.",3)+20;
    }
    for(unsigned int i=0;i<a->root_count;i++){
        const RootTool*t=&a->root_tools[i];
        /* Magisk lives in the ramdisk and APatch in the kernel: one BOOT carries one of them. */
        bool other=a->root_boot_root[0]&&strcmp(a->root_boot_root,t->id),usable=t->supported&&!other;
        int height=!usable?190:(t->needs_key?410:250);
        rounded(a,(Rect){28,y,664,height},24,PANEL);
        char title[96];snprintf(title,sizeof(title),"%s  %s",t->name,t->version[0]?t->version:"");
        raw_text(true);ellipsis(a,52,y+20,616,2,TEXT,title);raw_text(false);
        if(!t->supported)wrapped(a,52,y+78,616,0,MUTED,t->reason,3);
        else if(other)wrapped(a,52,y+78,616,0,ORANGE,"This BOOT already has another root: remove it first.",3);
        else{
            char size[48],note[128];human_bytes(t->size,size,sizeof(size));
            if(t->bundled)copy(note,sizeof(note),tr("Comes with this recovery: nothing is downloaded"));
            else if(t->error[0])copy(note,sizeof(note),tr("No answer from GitHub: connect Wi-Fi"));else snprintf(note,sizeof(note),"%s  /  %s",size,t->published);
            raw_text(true);ellipsis(a,52,y+78,616,0,t->error[0]?ORANGE:MUTED,note);raw_text(false);
            bool ready=free_ui&&!t->error[0]&&t->sha256[0]&&android&&!a->root_verification;int at=y+128;
            if(t->needs_key){
                bool keyed=a->root_key_tool==(int)i&&superkey_ok(a->root_superkey);
                button(a,(Rect){46,at,628,84},keyed?"SuperKey entered: change it":"Enter the SuperKey",A_ROOT_KEY+(int)i,free_ui,false,0);
                wrapped(a,52,at+96,616,0,MUTED,"The APatch app in Android asks for this SuperKey.",2);
                ready=ready&&keyed;at+=160;
            }
            button(a,(Rect){46,at,628,96},a->root_needs_reset?"Hold 5 seconds: erase data, install root":(t->bundled?"Hold 3 seconds: install":"Hold 3 seconds: download and install"),
                   A_ROOT_INSTALL+(int)i,ready,true,a->root_needs_reset?5000:3000);
        }
        y+=height+16;
    }
    if(a->root_known&&a->root_boot_root[0]&&a->root_stock_path[0]){
        button(a,(Rect){28,y,664,96},"Remove root: flash the stock BOOT back",A_ROOT_REMOVE,free_ui,false,0);y+=112;
    }
    y+=text_tile(a,y,0,MUTED,"The BOOT there is kept on cache before it is replaced. Magisk lives in the ramdisk and APatch in the kernel; their modules are installed in their apps in Android.",5)+16;
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,page_height(a)},a->page_scroll,a->page_content,ORANGE);
}
static bool root_action(App*a,int id){
    if(id==A_ROOT&&a->screen==ANDROID_MENU){if(ui_free(a))open_root(a);return true;}
    if(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_ROOT_SUPERKEY){
        /* The SuperKey is typed here and goes back to the Root page; what was typed is wiped either way. */
        bool done=(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER)&&superkey_ok(a->fs_prompt);
        if(id==A_ROOT_KEY_FIELD||((id==A_FS_PROMPT_NEXT||id==A_KB_ENTER)&&!done))return true;
        if(id!=A_BACK&&!done)return false; /* The keys of the keyboard. */
        if(done)copy(a->root_superkey,sizeof(a->root_superkey),a->fs_prompt);
        memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->fs_prompt_kind=0;screen(a,ROOT_PAGE);return true;
    }
    if(a->screen!=ROOT_PAGE)return false;
    if(id==A_BACK){memset(a->root_superkey,0,sizeof(a->root_superkey));a->root_key_tool=-1;screen(a,ANDROID_MENU);return true;}
    if(id>=A_ROOT_KEY&&id<A_ROOT_KEY+(int)ROOT_TOOLS){
        int n=id-A_ROOT_KEY;
        if(n<(int)a->root_count&&a->root_tools[n].needs_key&&ui_free(a)){
            if(a->root_key_tool!=n)memset(a->root_superkey,0,sizeof(a->root_superkey));
            a->root_key_tool=n;a->fs_prompt_kind=J_ROOT_SUPERKEY;copy(a->fs_prompt,sizeof(a->fs_prompt),a->root_superkey);
            a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;screen(a,FILE_PROMPT);
        }
        return true;
    }
    if(id>=A_ROOT_INSTALL&&id<A_ROOT_INSTALL+(int)ROOT_TOOLS){
        int n=id-A_ROOT_INSTALL;if(n<(int)a->root_count&&ui_free(a)){a->root_selected=n;
            if(!start_root_job(a,J_ROOT_INSTALL))files_problem(a,"ROOT_UNAVAILABLE","Wait for the current operation to finish.",ROOT_PAGE);}
        return true;
    }
    if(id==A_ROOT_PREPARE){
        if(ui_free(a)&&!start_root_job(a,J_ROOT_PREPARE))files_problem(a,"ROOT_UNAVAILABLE","Wait for the current operation to finish.",ROOT_PAGE);
        return true;
    }
    if(id==A_ROOT_REMOVE){
        if(!ui_free(a)||!a->root_stock_path[0])return true;
        /* The stock BOOT on cache goes through Flash a partition, as "Flash it to BOOT". */
        a->flash_restore=a->flash_pick=a->flash_review_valid=false;a->flash_known=false;a->flash_reason[0]=0;
        copy(a->flash_volume,sizeof(a->flash_volume),"cache");copy(a->flash_volume_token,sizeof(a->flash_volume_token),a->root_cache_token);
        copy(a->flash_path,sizeof(a->flash_path),a->root_stock_path);a->flash_after_targets=true;screen(a,FLASH);start_flash_job(a,J_FLASH_TARGETS);return true;
    }
    return false;
}
static void samsung_chosen(App*a){
    FileVolume*v=current_volume(a);
    copy(a->samsung_volume,sizeof(a->samsung_volume),v->id);copy(a->samsung_token,sizeof(a->samsung_token),v->token);
    copy(a->samsung_path,sizeof(a->samsung_path),a->fs_selected.path);a->flash_pick=a->samsung_pick=false;a->samsung_known=false;
    /* A ZIP as samfw.com ships the firmware (2026-11-10) is unpacked beside it first. */
    size_t length=strlen(a->samsung_path);bool zip=length>4&&!strcasecmp(a->samsung_path+length-4,".zip");
    if(!start_samsung_job(a,zip?J_SAMSUNG_UNPACK:J_SAMSUNG_INSPECT))files_problem(a,"SAMSUNG_FIRMWARE_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
}
static void rom_chosen(App*a){
    FileVolume*v=current_volume(a);
    copy(a->samsung_volume,sizeof(a->samsung_volume),v->id);copy(a->samsung_token,sizeof(a->samsung_token),v->token);
    copy(a->samsung_path,sizeof(a->samsung_path),a->fs_selected.path);a->flash_pick=a->rom_pick=false;a->samsung_known=false;a->android_gsi=a->gsi_file=false;a->rom_origin=true;
    if(!start_rom_kind(a))files_problem(a,"ANDROID_IMAGE_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
}
static void render_android_menu(App*a){
    heading(a,"Android","Stock Android, a custom ROM, or an Android image from the internet.",true);bool free_ui=ui_free(a);
    installer_card(a,(Rect){28,236,664,142},3,ICON_PHONE,"Samsung firmware","Stock Android from the AP and CSC files, as Odin flashes it",A_SAMSUNG,free_ui);
    installer_card(a,(Rect){28,388,664,142},2,ICON_BROWSER,"Android images","GSIs from the internet: choose the Android version and type",A_ANDROID_CATALOG,free_ui);
    /* A ROM on a card (2026-11-11, the owner's request): a custom ROM's ZIP, a system image or the
       firmware, chosen on an SD card or a USB stick; what it is decides which review opens. */
    installer_card(a,(Rect){28,540,664,142},1,A_FILES,"ROM from a file","A custom ROM or a system image on an SD card or USB stick",A_ANDROID_FILE,free_ui);
    /* Samsung's BOOT out of the firmware (2026-11-10): the way back from a custom kernel. */
    installer_card(a,(Rect){28,692,664,142},1,ICON_FLASH,"Stock BOOT","Take Samsung's BOOT out of the firmware: the way back from a custom kernel",A_STOCK_BOOT,free_ui);
    /* Root (2026-11-10): the newest Magisk from the internet into Android's BOOT. */
    installer_card(a,(Rect){28,844,664,142},4,ICON_LOCK,"Root","Magisk or APatch, into Android's BOOT",A_ROOT,free_ui);
    /* A ZIP that installs itself (2026-11-11): TWRP's Install, in a room of its own. */
    installer_card(a,(Rect){28,996,664,142},5,ICON_FLASH,"ZIP with an installer","Magisk, a kernel or another flashable ZIP runs its own script",A_ZIP,free_ui);
    int y=1154;
    /* The older installer of a bundle prepared on a computer (install.py): no qualified profile is in the
       phone's image, so only the fixture and the previews offer it. */
    if(!a->device){installer_card(a,(Rect){28,y,664,150},1,ICON_FLASH,"Prepared stock bundle","A bundle made on a computer, with its manifest",A_INSTALL,free_ui);y+=166;}
    rounded(a,(Rect){28,y,664,196},24,PANEL);text(a,52,y+20,0,RED,"USERDATA IS ERASED");
    wrapped(a,52,y+58,616,0,TEXT,"Installing Android needs userdata for Android: every Linux system and saved image there is erased. This recovery stays, and so does the SD card.",4);
    text_tile(a,y+224,0,MUTED,"The bootloader, the modem and the partition table are never written here.",3);
    if(a->source_sheet)render_source_sheet(a);
}
static void samsung_section(App*a,int*y,const char*title,uint32_t ink,const char*action){
    unsigned int count=0;for(unsigned int i=0;i<a->samsung_row_count;i++)count+=!strcmp(a->samsung_rows[i].action,action);
    if(!count)return;
    int top=*y;int height=64+(int)count*(strcmp(action,"write")?96:56);
    rounded(a,(Rect){28,top,664,height},24,PANEL);text(a,52,top+18,0,ink,title);int at=top+60;
    for(unsigned int i=0;i<a->samsung_row_count;i++){
        const SamsungRow*r=&a->samsung_rows[i];if(strcmp(r->action,action))continue;
        char size[48],left[120];human_bytes(r->size,size,sizeof(size));
        if(!strcmp(action,"write")){snprintf(left,sizeof(left),"%s",r->target);raw_text(true);text(a,52,at,1,TEXT,left);raw_text(false);
            raw_text(true);ellipsis(a,300,at+6,380,0,MUTED,size);raw_text(false);at+=56;continue;}
        snprintf(left,sizeof(left),"%s  /  %s  /  %s",r->file,r->name,size);raw_text(true);ellipsis(a,52,at,616,0,TEXT,left);raw_text(false);
        ellipsis(a,52,at+38,616,0,MUTED,r->reason[0]?r->reason:"");at+=96;
    }
    *y=top+height+16;
}
static void render_samsung_review(App*a){
    const CatalogImage*gsi=gsi_of_catalog(a)&&a->android_selected>=0&&(unsigned int)a->android_selected<a->catalog_count?&a->catalog[a->android_selected]:NULL;
    heading(a,gsi?gsi->name:(a->android_gsi?"Android image":"Stock Android"),a->samsung_build[0]?a->samsung_build:(a->android_gsi?"GSI":"Samsung firmware"),true);bool free_ui=ui_free(a);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    if(!a->samsung_known){wrapped(a,40,y+12,640,0,MUTED,a->job.pid?"Reading the firmware...":"Choose the AP file of the firmware again.",3);y+=96;}
    else{
        char line[200],size[48];human_bytes(a->samsung_write_bytes,size,sizeof(size));
        snprintf(line,sizeof(line),T("Files: %s  /  %s to write"),a->samsung_files,size);raw_text(true);ellipsis(a,40,y,640,0,MUTED,line);raw_text(false);y+=48;
        if(a->samsung_blockers[0]){y+=text_tile(a,y,0,RED,a->samsung_blockers,6)+16;}
        if(a->samsung_warnings[0]){y+=text_tile(a,y,0,ORANGE,a->samsung_warnings,8)+16;}
        samsung_section(a,&y,"WRITTEN",GREEN,"write");
        rounded(a,(Rect){28,y,664,276},24,PANEL);text(a,52,y+18,0,RED,"ERASED");
        text(a,52,y+60,1,TEXT,"userdata");wrapped(a,300,y+66,370,0,MUTED,"Formatted for Android: every Linux system and saved image is erased",3);
        text(a,52,y+152,1,TEXT,"metadata");wrapped(a,300,y+158,370,0,MUTED,"Android creates it again",2);
        text(a,52,y+212,1,TEXT,"cache");wrapped(a,300,y+218,370,0,MUTED,"The recovery's own folder stays",2);y+=292;
        samsung_section(a,&y,"KEPT",TEXT,"kept");
        samsung_section(a,&y,"INSTEAD",TEXT,"instead");
        samsung_section(a,&y,"NOT WRITTEN",MUTED,"refused");
        y+=text_tile(a,y,0,MUTED,a->android_gsi?"Everything is checked as it is read and read back after writing. It takes a few minutes; then restart into Android.":
                   "Everything is checked as it is read and read back after writing. A Linux BOOT is kept on cache first. It takes about ten minutes; then restart into Android.",5)+24;
    }
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,page_height(a)},a->page_scroll,a->page_content,ORANGE);
    button(a,(Rect){28,1320,664,104},"Hold 5 seconds to install",A_SAMSUNG_CONFIRM,free_ui&&a->samsung_known&&!a->samsung_blockers[0],true,5000);
}
/* A ZIP that installs itself (zip_install.py, 2026-11-11): chosen with the picker of images, reviewed
   (what it is, what its installer can reach), run after a hold; the result names what changed. */
static bool zip_job(int kind){return kind==J_ZIP_INSPECT||kind==J_ZIP_RUN;}
/* Several ZIPs one after another (2026-11-11), what TWRP calls adding more ZIPs. The review of a ZIP lives in
   the fields zip_*; an entry of the list keeps a copy of it and gives it back when its turn comes. */
static void zip_item_from_review(const App*a,ZipItem*it){
    memset(it,0,sizeof(*it));
    copy(it->volume,sizeof(it->volume),a->zip_volume);copy(it->token,sizeof(it->token),a->zip_token);copy(it->path,sizeof(it->path),a->zip_path);
    copy(it->name,sizeof(it->name),a->zip_name);copy(it->sha,sizeof(it->sha),a->zip_sha);copy(it->title,sizeof(it->title),a->zip_title);
    copy(it->what,sizeof(it->what),a->zip_what);copy(it->installer,sizeof(it->installer),a->zip_installer);it->size=a->zip_size;it->files=a->zip_files;
}
static void zip_item_to_review(App*a,const ZipItem*it){
    copy(a->zip_volume,sizeof(a->zip_volume),it->volume);copy(a->zip_token,sizeof(a->zip_token),it->token);copy(a->zip_path,sizeof(a->zip_path),it->path);
    copy(a->zip_name,sizeof(a->zip_name),it->name);copy(a->zip_sha,sizeof(a->zip_sha),it->sha);copy(a->zip_title,sizeof(a->zip_title),it->title);
    copy(a->zip_what,sizeof(a->zip_what),it->what);copy(a->zip_installer,sizeof(a->zip_installer),it->installer);a->zip_size=it->size;a->zip_files=it->files;
}
/* The ZIP just reviewed joins the list: alone when it was chosen afresh, after the others when it
   was added. The same file again keeps its place. */
static void zip_enqueue(App*a){
    bool adding=a->zip_adding&&a->zip_count>0;a->zip_adding=false;
    if(!adding)a->zip_count=0;
    for(unsigned int n=0;n<a->zip_count;n++)if(!strcmp(a->zip_queue[n].volume,a->zip_volume)&&!strcmp(a->zip_queue[n].path,a->zip_path)){
        zip_item_from_review(a,&a->zip_queue[n]);toast(a,"This ZIP is in the list already");return;
    }
    if(a->zip_count<ZIP_QUEUE)zip_item_from_review(a,&a->zip_queue[a->zip_count++]);
}
/* What the installers left: for one ZIP as before; for several, every partition any of them changed,
   what became of each ZIP, and what the last one that ran said. */
static void zip_result(App*a,bool ok,const char*code,const char*changed,const char*log){
    a->successful=ok;
    if(a->zip_count<=1){
        copy(a->status,sizeof(a->status),ok?"The installer finished":(code[0]?code:"ZIP_INSTALLER_FAILED"));
        snprintf(a->message,sizeof(a->message),"%s %s\n\n",tr(ok?"Changed:":"The installer ended with an error. Changed:"),changed[0]?changed:tr("Nothing"));
    }else{
        char all[200]="";
        for(unsigned int n=0;n<a->zip_count;n++){
            char words[160];copy(words,sizeof(words),a->zip_queue[n].changed);
            for(char*word=strtok(words," ");word;word=strtok(NULL," ")){
                char spaced[200],one[48];snprintf(spaced,sizeof(spaced)," %s ",all);snprintf(one,sizeof(one)," %s ",word);
                if(!strstr(spaced,one)){if(all[0])append(all,sizeof(all)," ");append(all,sizeof(all),word);}
            }
        }
        copy(a->status,sizeof(a->status),ok?"The installers finished":(code[0]?code:"ZIP_INSTALLER_FAILED"));
        snprintf(a->message,sizeof(a->message),"%s %s\n\n",tr(ok?"Changed:":"An installer ended with an error. Changed:"),all[0]?all:tr("Nothing"));
        for(unsigned int n=0;n<a->zip_count;n++){
            char line[160];int state=a->zip_queue[n].state;
            snprintf(line,sizeof(line),"%u. %.60s: %s\n",n+1,a->zip_queue[n].name,tr(state==1?"finished":(state==2?"failed":"not run")));
            append(a->message,sizeof(a->message),line);
        }
        append(a->message,sizeof(a->message),"\n");
    }
    append(a->message,sizeof(a->message),log);
    a->root_known=false;a->result_return=ANDROID_MENU;a->zip_ran=true;
    a->zip_restart_at=ok&&a->zip_restart&&a->allow_power?a->now+ZIP_RESTART_MS:0;a->zip_restart_shown=0;
    /* The list stays as it was reviewed, should the owner go back to it. */
    if(a->zip_count)zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);
    screen(a,RESULT);
}
static bool start_zip_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->zip_helper[0]||!a->zip_path[0])return false;
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->zip_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=kind==J_ZIP_INSPECT?"inspect":"run";
    args[n++]="--volume";args[n++]=a->zip_volume;args[n++]="--volume-token";args[n++]=a->zip_token;args[n++]="--path";args[n++]=a->zip_path;
    if(kind==J_ZIP_RUN){if(!a->zip_sha[0])return false;args[n++]="--expected-sha256";args[n++]=a->zip_sha;args[n++]="--confirm";}
    args[n]=NULL;
    if(!launch_job(a,kind,args))return false;
    a->result_return=ANDROID_MENU;return true;
}
static void zip_chosen(App*a){
    FileVolume*v=current_volume(a);
    copy(a->zip_volume,sizeof(a->zip_volume),v->id);copy(a->zip_token,sizeof(a->zip_token),v->token);copy(a->zip_path,sizeof(a->zip_path),a->fs_selected.path);
    a->flash_pick=a->zip_pick=false;a->zip_known=false;
    if(!start_zip_job(a,J_ZIP_INSPECT))files_problem(a,"ZIP_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_MENU);
}
/* A file sideloaded over adb (2026-11-11): adbd writes sideload-<time>.json beside the key
   requests. A ZIP opens its review at once, as TWRP's sideload installs it, and the hold still
   starts its installer; any other file is named in a toast and waits in the store for Flash a
   partition or the installers. Read only while the interface is idle, so that nothing a user is
   doing is replaced; the oldest first. */
static void sideload_poll(App*a){
    if(a->now-a->sideload_checked<500)return;
    a->sideload_checked=a->now;
    if(a->job.pid||a->locked||a->pointer_down||a->preview||a->screen==SPLASH||a->screen==LOCKSCREEN||a->screen==ACCESS_REQUEST||a->screen==ADB_REQUEST||
       a->screen==PROGRESS||a->screen==FILE_PROMPT||a->screen==FILE_CONFIRM||a->screen==POWER_CONFIRM||a->screen==POWER_MENU||a->screen==TERMINAL)return;
    DIR*d=opendir(adb_dir);if(!d)return;
    char found[96]={0};struct dirent*e;
    while((e=readdir(d))){
        size_t n=strlen(e->d_name);
        if(strncmp(e->d_name,"sideload-",9)||n<15||n>=sizeof(found)||strcmp(e->d_name+n-5,".json"))continue;
        if(!found[0]||strcmp(e->d_name,found)<0)copy(found,sizeof(found),e->d_name);
    }
    closedir(d);
    if(!found[0])return;
    char path[PATH_LEN];snprintf(path,sizeof(path),"%s/%s",adb_dir,found);
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);char data[1536];ssize_t n=fd>=0?read(fd,data,sizeof(data)-1):-1;if(fd>=0)close(fd);
    unlink(path); /* Read once: a broken record is not read again and again. */
    if(n<=0)return;
    data[n]=0;JsonToken t[32];Json j;if(json_parse(&j,data,t,32)<1||t[0].type!=J_OBJECT)return;
    char file[200]={0},kind[16]={0},volume[32]={0},token[72]={0},where[PATH_LEN]={0};
    jstring(&j,0,"file",file,sizeof(file));jstring(&j,0,"kind",kind,sizeof(kind));jstring(&j,0,"volume",volume,sizeof(volume));
    jstring(&j,0,"token",token,sizeof(token));jstring(&j,0,"path",where,sizeof(where));
    if(!file[0]||!volume[0]||where[0]!='/')return;
    char note[300];snprintf(note,sizeof(note),T("Arrived over adb: %s"),file);
    if(!strcmp(kind,"zip")&&strlen(token)==64){
        copy(a->zip_volume,sizeof(a->zip_volume),volume);copy(a->zip_token,sizeof(a->zip_token),token);copy(a->zip_path,sizeof(a->zip_path),where);
        a->flash_pick=a->zip_pick=a->samsung_pick=a->stock_boot_pick=false;a->zip_known=false;a->android_gsi=false;a->zip_adding=false;
        if(!start_zip_job(a,J_ZIP_INSPECT))return;
    }
    toast(a,note);a->toast_icon=ICON_USB;
}
/* The picker again, in the folder the ZIP was chosen in. */
static bool zip_repick(App*a,enum Screen from){
    if(from!=FILE_BROWSER||!current_volume(a)||!current_volume(a)->mounted)return false;
    /* Chosen as a ROM on a card: the folder opens for any ROM again. */
    if(a->rom_origin){a->flash_pick=a->rom_pick=true;a->zip_pick=a->samsung_pick=a->stock_boot_pick=a->flash_restore=false;return true;}
    a->flash_pick=a->zip_pick=true;a->samsung_pick=a->stock_boot_pick=a->flash_restore=false;return true;
}
static void zip_lines(const Json*j,int list,char*out,size_t size,bool translate,unsigned int last){
    out[0]=0;if(list<0||j->tokens[list].type!=J_ARRAY)return;
    unsigned int count=0,at=0;for(int i=list+1;i<json_next(j,list);i=json_next(j,i))count++;
    for(int i=list+1;i<json_next(j,list);i=json_next(j,i),at++){
        char line[248];if(at+last<count||json_string(j,i,line,sizeof(line))<=0)continue;
        if(out[0])append(out,size,"\n");
        append(out,size,translate?tr(line):line);
    }
}
static void complete_zip_job(App*a,const Json*j,int kind,bool ok){
    int e=j?json_get(j,0,"error"):-1,details=e>=0?json_get(j,e,"details"):-1;
    if(kind==J_ZIP_RUN&&j&&(ok||(details>=0&&json_get(j,details,"log")>=0))){
        /* The installer ran: what it said last, and the partitions that are not what they were. */
        int from=ok?0:details;char changed[160],log[900],code[96]="";zip_lines(j,json_get(j,from,"changed"),changed,sizeof(changed),false,16);
        for(char*c=changed;*c;c++)if(*c=='\n')*c=' ';
        zip_lines(j,json_get(j,from,"log"),log,sizeof(log),false,ok?7:9);
        if(!ok)jstring(j,e,"code",code,sizeof(code));
        ZipItem*it=a->zip_at<a->zip_count?&a->zip_queue[a->zip_at]:NULL;
        if(it){it->state=ok?1:2;copy(it->changed,sizeof(it->changed),changed);}
        a->root_known=false;
        if(ok&&it&&a->zip_at+1<a->zip_count){
            /* The next one in the list: its review is put back, and its installer starts. */
            a->zip_at++;zip_item_to_review(a,&a->zip_queue[a->zip_at]);
            if(start_zip_job(a,J_ZIP_RUN))return;
            a->zip_queue[a->zip_at].state=2;ok=false;copy(code,sizeof(code),"ZIP_UNAVAILABLE");
        }
        zip_result(a,ok,code,changed,log);return;
    }
    if(!ok||!j){
        char code[96]="ZIP_FAILED",message[384]="The ZIP helper did not return a result.";
        if(e>=0){jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));}
        /* One of several that did not even start: the list says which, and what ran before it. */
        if(kind==J_ZIP_RUN&&a->zip_count>1){if(a->zip_at<a->zip_count)a->zip_queue[a->zip_at].state=2;zip_result(a,false,code,"",tr(message));return;}
        /* A ZIP that was to join the list and cannot: the list stays, and another can be chosen. */
        if(kind==J_ZIP_INSPECT&&a->zip_adding&&a->zip_count){
            zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);a->zip_known=true;
            bool again=zip_repick(a,nav_top(a));if(!again)a->zip_adding=false;
            files_problem(a,code,message,again?FILE_BROWSER:ZIP_REVIEW);return;
        }
        a->zip_known=false;a->zip_adding=false;a->zip_count=0;
        /* Another file can be chosen at once: back in the folder, still choosing. */
        files_problem(a,code,message,kind==J_ZIP_INSPECT&&zip_repick(a,nav_top(a))?FILE_BROWSER:ANDROID_MENU);return;
    }
    jstring(j,0,"name",a->zip_name,sizeof(a->zip_name));jstring(j,0,"sha256",a->zip_sha,sizeof(a->zip_sha));jstring(j,0,"title",a->zip_title,sizeof(a->zip_title));
    jstring(j,0,"what",a->zip_what,sizeof(a->zip_what));jstring(j,0,"installer",a->zip_installer,sizeof(a->zip_installer));
    a->zip_size=json_uint(j,json_get(j,0,"size"),0);a->zip_files=(unsigned int)json_uint(j,json_get(j,0,"files"),0);
    zip_lines(j,json_get(j,0,"warnings"),a->zip_warnings,sizeof(a->zip_warnings),true,8);
    zip_lines(j,json_get(j,0,"partitions"),a->zip_parts,sizeof(a->zip_parts),false,16);
    for(char*c=a->zip_parts;*c;c++)if(*c=='\n')*c=' ';
    a->zip_known=strlen(a->zip_sha)==64;
    if(a->zip_known)zip_enqueue(a);
    else if(a->zip_adding&&a->zip_count){a->zip_adding=false;zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);a->zip_known=true;}
    else{a->zip_adding=false;a->zip_count=0;}
    a->page_scroll=0;screen(a,ZIP_REVIEW);
}
static void render_zip_review(App*a){
    const char*kind=!strcmp(a->zip_what,"magisk")?"Magisk":(!strcmp(a->zip_what,"kernel")?"A kernel (AnyKernel3)":(!strcmp(a->zip_what,"module")?"A module":"A flashable ZIP"));
    heading(a,"ZIP with an installer","Nothing has run yet.",true);bool free_ui=ui_free(a),many=a->zip_count>1;
    int y=236;char line[200],size[48];
    if(!many){
        rounded(a,(Rect){28,y,664,206},24,PANEL);text(a,52,y+18,0,ORANGE,"FILE");
        raw_text(true);ellipsis(a,52,y+56,616,1,TEXT,a->zip_name);raw_text(false);
        human_bytes(a->zip_size,size,sizeof(size));snprintf(line,sizeof(line),"%s  /  %s  /  %s",a->zip_title[0]?a->zip_title:tr(kind),size,tr(!strcmp(a->zip_installer,"program")?"its installer is a program":"its installer is a script"));
        raw_text(true);ellipsis(a,52,y+108,616,0,MUTED,line);ellipsis(a,52,y+152,616,0,MUTED,a->zip_sha);raw_text(false);y+=222;
    }else{
        /* The list, in the order the installers run; the mark at the right takes a ZIP out. */
        int h=60+(int)a->zip_count*56+8;rounded(a,(Rect){28,y,664,h},24,PANEL);text(a,52,y+18,0,ORANGE,"FILES, IN THIS ORDER");
        for(unsigned int n=0;n<a->zip_count;n++){
            int row=y+56+(int)n*56;char number[8];snprintf(number,sizeof(number),"%u",n+1);
            raw_text(true);text(a,52,row+8,1,MUTED,number);ellipsis(a,92,row+8,500,1,TEXT,a->zip_queue[n].name);raw_text(false);
            Rect out={612,row,64,48};surface(a,out,24,BG,A_ZIP_REMOVE+(int)n);icon(a,ICON_TRASH,out.x+17,out.y+9,30,free_ui?MUTED:LINE);
            register_button(a,out,A_ZIP_REMOVE+(int)n,free_ui,0);
        }
        y+=h+16;
    }
    button(a,(Rect){28,y,664,76},"Add another ZIP",A_ZIP_ADD,free_ui&&a->zip_known&&a->zip_count>0&&a->zip_count<ZIP_QUEUE,false,0);y+=88;
    int root=many?232:338;
    rounded(a,(Rect){28,y,664,root},24,PANEL);text(a,52,y+18,0,RED,many?"THEIR INSTALLERS RUN AS ROOT":"ITS INSTALLER RUNS AS ROOT");
    wrapped(a,52,y+58,616,0,TEXT,many?"Each ZIP's own script decides what is written. They can reach only these partitions:":"The ZIP's own script decides what is written. It can reach only these partitions:",2);
    raw_text(true);wrapped(a,52,y+128,616,0,TEXT,a->zip_parts,3);raw_text(false);
    if(!many)wrapped(a,52,y+226,616,0,MUTED,"The bootloader, the modem, this recovery, Android's data and the recovery's settings on cache are out of its reach. The BOOT there is kept on cache first.",4);
    y+=root+16;
    button(a,(Rect){28,y,664,76},a->zip_restart?"Afterwards: restart the system":"Afterwards: stay in recovery",A_ZIP_RESTART,free_ui&&a->allow_power,a->zip_restart,0);y+=88;
    if(a->zip_warnings[0]&&1304-y>=120)y+=text_tile(a,y,0,ORANGE,a->zip_warnings,many?3:5)+12;
    button(a,(Rect){28,1320,664,104},many?"Hold 5 seconds: run the installers":"Hold 5 seconds: run the installer",A_ZIP_CONFIRM,free_ui&&a->zip_known&&a->zip_count>0,true,5000);
}
static bool zip_action(App*a,int id){
    if(id==A_ZIP&&a->screen==ANDROID_MENU){
        if(!ui_free(a))return true;
        /* The same picker as Flash a partition; the chosen file is the ZIP. */
        a->android_gsi=a->gsi_file=a->rom_origin=a->rom_pick=false;a->samsung_pick=a->stock_boot_pick=a->flash_restore=false;a->flash_pick=a->zip_pick=true;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;
        a->zip_adding=false;
        screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    }
    if(a->screen!=ZIP_REVIEW)return false;
    if(id==A_ZIP_ADD){
        if(!ui_free(a)||!a->zip_count||a->zip_count>=ZIP_QUEUE)return true;
        /* One more: the picker again, in the folder the last one was chosen in when there was one. */
        a->zip_adding=true;enum Screen from=came_from(a,ANDROID_MENU);
        if(zip_repick(a,from)){reopen(a,FILE_BROWSER);return true;}
        a->android_gsi=a->gsi_file=a->rom_origin=a->rom_pick=false;a->samsung_pick=a->stock_boot_pick=a->flash_restore=false;a->flash_pick=a->zip_pick=true;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;
        screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
    }
    if(id==A_ZIP_RESTART){if(ui_free(a)&&a->allow_power){a->zip_restart=!a->zip_restart;a->dirty=true;}return true;}
    if(id>=A_ZIP_REMOVE&&id<A_ZIP_REMOVE+ZIP_QUEUE){
        unsigned int n=(unsigned int)(id-A_ZIP_REMOVE);
        if(ui_free(a)&&a->zip_count>1&&n<a->zip_count){
            for(unsigned int k=n;k+1<a->zip_count;k++)a->zip_queue[k]=a->zip_queue[k+1];
            a->zip_count--;zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);a->dirty=true;
        }
        return true;
    }
    /* Back to the folder it was chosen in, or to the page it opened over (2026-11-11: a ZIP sideloaded over adb opens from any page). */
    if(id==A_BACK){enum Screen from=came_from(a,ANDROID_MENU);if(zip_repick(a,from))reopen(a,FILE_BROWSER);else reopen(a,from==FILE_BROWSER?ANDROID_MENU:from);return true;}
    if(id==A_ZIP_CONFIRM){
        if(!ui_free(a)||!a->zip_known||!a->zip_count)return true;
        /* From the first: each waits, and the one that runs lends its review to the job. */
        for(unsigned int n=0;n<a->zip_count;n++){a->zip_queue[n].state=0;a->zip_queue[n].changed[0]=0;}
        a->zip_at=0;zip_item_to_review(a,&a->zip_queue[0]);
        if(!start_zip_job(a,J_ZIP_RUN)){zip_item_to_review(a,&a->zip_queue[a->zip_count-1]);files_problem(a,"ZIP_UNAVAILABLE","Wait for the current operation to finish.",ZIP_REVIEW);}
        return true;
    }
    return false;
}
static bool samsung_action(App*a,int id){
    if(id==A_ANDROID){if(ui_free(a))screen(a,ANDROID_MENU);return true;}
    if(a->screen==ANDROID_MENU){
        if(id==A_BACK){reopen(a,came_from(a,OS_MENU));return true;}
        if(id==A_SAMSUNG||id==A_STOCK_BOOT){
            if(!ui_free(a))return true;
            a->android_gsi=a->gsi_file=a->rom_origin=a->rom_pick=false;a->stock_boot_pick=id==A_STOCK_BOOT;
            /* The same picker as Flash a partition; the chosen file is the AP. */
            a->flash_pick=a->samsung_pick=true;a->flash_restore=false;a->volume_scroll=0;a->fs_inventory_destination=FILE_VOLUMES;
            screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
        }
        if(id==A_ANDROID_FILE){
            if(!ui_free(a))return true;
            /* The sheet asks where the ROM is; the file chosen there is looked into (rom_chosen). */
            a->android_gsi=a->gsi_file=a->rom_origin=false;a->samsung_pick=a->stock_boot_pick=a->zip_pick=a->flash_restore=false;a->flash_pick=a->rom_pick=true;
            source_open(a);return true;
        }
        if(id==A_ANDROID_CATALOG){
            if(!ui_free(a))return true;
            a->catalog_saving=true;a->catalog_only=2;a->catalog_category=CATEGORY_ANDROID;a->catalog_family=0;a->catalog_page=0;a->page_scroll=0;
            a->catalog_checked=a->catalog_check_done=false;a->catalog_return=ANDROID_MENU;screen(a,SYSTEMS_CATALOG);start_catalog_job(a);return true;
        }
        if(id==A_DONE&&a->result_return==ANDROID_MENU)return false;
    }
    if(id==A_STOCK_BOOT_FLASH&&a->screen==RESULT){
        if(!ui_free(a)||!a->stock_boot_path[0])return true;
        /* The kept copy on cache goes through Flash a partition: checked, reviewed, held. */
        a->flash_restore=a->flash_pick=a->flash_review_valid=false;a->flash_known=false;a->flash_reason[0]=0;
        copy(a->flash_volume,sizeof(a->flash_volume),"cache");copy(a->flash_volume_token,sizeof(a->flash_volume_token),a->stock_boot_token);
        copy(a->flash_path,sizeof(a->flash_path),a->stock_boot_path);a->flash_after_targets=true;screen(a,FLASH);start_flash_job(a,J_FLASH_TARGETS);return true;
    }
    if(a->screen==ANDROID_IMAGE){
        const CatalogImage*c=a->android_selected>=0&&(unsigned int)a->android_selected<a->catalog_count?&a->catalog[a->android_selected]:NULL;
        if(a->systems_mount_sheet)return false; /* The sheet's own buttons, in systems_action. */
        if(id==A_BACK){screen(a,SYSTEMS_CATALOG);return true;}
        if(!c)return id==A_ANDROID_DOWNLOAD||id==A_ANDROID_INSTALL||id==A_ANDROID_DELETE;
        if(id==A_ANDROID_DOWNLOAD){if(ui_free(a)&&(systems_ready(a)||store_on_card(a))&&c->reachable){a->saved_note[0]=0;start_images_job(a,J_IMAGES_DOWNLOAD,c->id);}return true;}
        if(id==A_ANDROID_DELETE){const StoredImage*s=android_saved(a,c);if(s&&ui_free(a))start_images_job(a,J_IMAGES_DELETE,s->file);return true;}
        if(id==A_ANDROID_INSTALL){
            const StoredImage*s=android_saved(a,c);if(!s||!ui_free(a))return true;
            /* The saved file, on userdata or on the card, is reviewed by android_gsi.py, like Samsung's firmware. */
            bool card=store_on_card(a);
            a->android_gsi=true;a->gsi_file=a->rom_origin=false;a->samsung_known=false;copy(a->samsung_volume,sizeof(a->samsung_volume),card?a->store_volume:"userdata");
            copy(a->samsung_token,sizeof(a->samsung_token),card?a->store_token:a->systems_volume_token);copy(a->samsung_path,sizeof(a->samsung_path),s->path);
            if(!start_samsung_job(a,J_SAMSUNG_INSPECT))files_problem(a,"ANDROID_IMAGE_UNAVAILABLE","Wait for the current operation to finish.",ANDROID_IMAGE);
            return true;
        }
    }
    if(a->screen==SAMSUNG_REVIEW){
        if(id==A_BACK){
            /* Back to the folder the firmware was chosen in (2026-11-10: it left for the Android menu). */
            if(samsung_repick(a,came_from(a,ANDROID_MENU),false))reopen(a,FILE_BROWSER);else screen(a,gsi_of_catalog(a)?ANDROID_IMAGE:ANDROID_MENU);
            return true;
        }
        if(id==A_SAMSUNG_CONFIRM){
            if(ui_free(a)&&a->samsung_known&&!a->samsung_blockers[0]&&!start_samsung_job(a,J_SAMSUNG_INSTALL))
                files_problem(a,"SAMSUNG_FIRMWARE_UNAVAILABLE","Wait for the current operation to finish.",SAMSUNG_REVIEW);
            return true;
        }
    }
    return false;
}
#endif

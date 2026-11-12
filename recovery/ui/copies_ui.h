/* SPDX-License-Identifier: MIT
 * The copies of Android's partitions on the Restore and Verify pages (2026-11-11, the owner's
 * request: a full restore of Android from a USB stick, in a category of its own beside Linux).
 * identity_backup.py --kind android finds the copies on every mounted SD card and USB drive,
 * checks one and writes one back: an archive piece by piece, a folder made before as it was.
 * Recovery and cache are in no copy, so a restore leaves them as they are.
 */
#ifndef A22X_COPIES_UI_H
#define A22X_COPIES_UI_H

static bool copy_job(int kind){return kind>=J_COPY_LIST&&kind<=J_COPY_DELETE;}
static bool list_android(const App*a){return a->list_category==1&&!a->install.pick_backup;}
static PhoneCopy*copy_current(App*a){return a->copy_pick>=0&&a->copy_pick<(int)a->copy_count?&a->copies[a->copy_pick]:NULL;}
static unsigned int copy_shown_parts(const PhoneCopy*c){return c->parts<COPY_PARTS?c->parts:COPY_PARTS;}
static unsigned int copy_chosen(const App*a,const PhoneCopy*c){unsigned int n=0;for(unsigned int k=0;k<copy_shown_parts(c);k++)n+=!(a->copy_off&(1u<<k));return n;}
/* Android's own data in a copy (2026-11-11): metadata and userdata, written back only when chosen, and together. */
static uint32_t copy_data_parts(const PhoneCopy*c){uint32_t bits=0;for(unsigned int k=0;k<copy_shown_parts(c);k++)if(!strcmp(c->labels[k],"userdata")||!strcmp(c->labels[k],"metadata"))bits|=1u<<k;return bits;}
/* The time a copy was made, as the day and the minute by the recovery's clock (2026-11-11: it
   was shown in UTC, an hour or two off the clock in the corner); a copy without one shows its name. */
static void copy_when(const App*a,const PhoneCopy*c,char*out,size_t size){
    struct tm made={0};int year,month,day,hour,minute,second;
    if(sscanf(c->created,"%4d-%2d-%2dT%2d:%2d:%2dZ",&year,&month,&day,&hour,&minute,&second)==6){
        made.tm_year=year-1900;made.tm_mon=month-1;made.tm_mday=day;made.tm_hour=hour;made.tm_min=minute;made.tm_sec=second;
        time_t at=timegm(&made)+(time_t)recovery_offset_now(a)*60;struct tm local;gmtime_r(&at,&local);
        snprintf(out,size,"%04d-%02d-%02d  %02d:%02d",local.tm_year+1900,local.tm_mon+1,local.tm_mday,local.tm_hour,local.tm_min);
    }else{const char*name=strrchr(c->path,'/');copy(out,size,name?name+1:c->path);}
}
static bool start_copy_job(App*a,int kind){
    if(a->job.pid||a->preview||!copy_job(kind))return false;
    identity_helper(a,a->copy_helper,sizeof(a->copy_helper));
    if(!a->copy_helper[0])return false;
    char*args[24];int n=0;args[n++]=a->python;args[n++]=a->copy_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="--kind";args[n++]="android";
    if(kind==J_COPY_LIST)args[n++]="copies";
    else{
        PhoneCopy*c=copy_current(a);if(!c)return false;
        /* An archive is restored piece by piece: what goes back is named, Android's data only when chosen. */
        if(kind==J_COPY_RESTORE&&c->archive&&(copy_chosen(a,c)<copy_shown_parts(c)||copy_data_parts(c))){
            a->copy_labels_arg[0]=0;
            for(unsigned int k=0;k<copy_shown_parts(c);k++)if(!(a->copy_off&(1u<<k))){if(a->copy_labels_arg[0])append(a->copy_labels_arg,sizeof(a->copy_labels_arg),",");append(a->copy_labels_arg,sizeof(a->copy_labels_arg),c->labels[k]);}
            if(!a->copy_labels_arg[0])return false;
            args[n++]="--labels";args[n++]=a->copy_labels_arg;
        }
        args[n++]=kind==J_COPY_VERIFY?"verify":(kind==J_COPY_RESTORE?"restore":"delete");
        args[n++]="--volume";args[n++]=c->volume;args[n++]="--path";args[n++]=c->path;
        if(kind!=J_COPY_VERIFY){args[n++]="--expected-manifest-sha256";args[n++]=c->sha;args[n++]="--confirm";}
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;
    a->result_return=kind==J_COPY_LIST||kind==J_COPY_DELETE?LIST:COPY_DETAIL;return true;
}
/* The names in an array of the reply, joined by commas for a sentence. */
static void copy_names(const Json*j,const char*key,char*out,size_t size){
    out[0]=0;int array=json_get(j,0,key);if(array<0||j->tokens[array].type!=J_ARRAY)return;
    for(int i=array+1;i<json_next(j,array);i=json_next(j,i)){
        char name[32];if(json_string(j,i,name,sizeof(name))<=0)continue;
        if(out[0])append(out,size,", ");
        append(out,size,name);
    }
}
static void complete_copy_job(App*a,const Json*j,int kind,bool ok){
    if(!ok||!j){
        char code[64]="COPY_FAILED",message[512]="The copy helper did not return a result.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_COPY_LIST){a->copies_known=true;a->copy_count=a->copy_waiting_count=0;a->copy_pick=-1;copy(a->copy_error,sizeof(a->copy_error),message);a->dirty=true;return;}
        files_problem(a,code,message,COPY_DETAIL);return;
    }
    if(kind==J_COPY_LIST){
        char chosen[sizeof(a->copies[0].sha)]={0};PhoneCopy*before=copy_current(a);if(before)copy(chosen,sizeof(chosen),before->sha);
        a->copy_count=a->copy_waiting_count=0;a->copy_pick=-1;a->copy_error[0]=0;int array=json_get(j,0,"backups");
        for(int i=array+1;array>=0&&i<json_next(j,array)&&a->copy_count<COPIES_MAX;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT)continue;
            PhoneCopy*c=&a->copies[a->copy_count];memset(c,0,sizeof(*c));
            jstring(j,i,"volume",c->volume,sizeof(c->volume));jstring(j,i,"volume_token",c->token,sizeof(c->token));jstring(j,i,"volume_label",c->place,sizeof(c->place));
            jstring(j,i,"path",c->path,sizeof(c->path));jstring(j,i,"created_utc",c->created,sizeof(c->created));jstring(j,i,"manifest_sha256",c->sha,sizeof(c->sha));
            c->bytes=json_uint(j,json_get(j,i,"bytes"),0);c->file_bytes=json_uint(j,json_get(j,i,"file_bytes"),0);
            c->archive=json_bool(j,json_get(j,i,"archive"),0)==1;c->writable=json_bool(j,json_get(j,i,"writable"),0)==1;c->same=json_bool(j,json_get(j,i,"same_phone"),0)==1;
            int labels=json_get(j,i,"labels");
            for(int k=labels+1;labels>=0&&j->tokens[labels].type==J_ARRAY&&k<json_next(j,labels);k=json_next(j,k)){
                if(c->parts<COPY_PARTS)json_string(j,k,c->labels[c->parts],sizeof(c->labels[0]));
                c->parts++; /* Counted beyond what is shown; a copy with more is restored whole. */
            }
            if(c->volume[0]&&c->path[0]&&strlen(c->sha)==64&&c->parts)a->copy_count++;
        }
        array=json_get(j,0,"unmounted");
        for(int i=array+1;array>=0&&i<json_next(j,array)&&a->copy_waiting_count<COPY_WAITING;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT)continue;
            CopyVolume*v=&a->copy_waiting[a->copy_waiting_count];memset(v,0,sizeof(*v));
            jstring(j,i,"volume",v->id,sizeof(v->id));jstring(j,i,"volume_token",v->token,sizeof(v->token));jstring(j,i,"volume_label",v->label,sizeof(v->label));
            jstring(j,i,"fs_type",v->fs,sizeof(v->fs));v->bytes=json_uint(j,json_get(j,i,"size_bytes"),0);
            if(v->id[0]&&v->token[0])a->copy_waiting_count++;
        }
        for(unsigned int n=0;n<a->copy_count;n++)if(chosen[0]&&!strcmp(a->copies[n].sha,chosen))a->copy_pick=(int)n;
        a->copies_known=true;a->dirty=true;return;
    }
    if(kind==J_COPY_DELETE){
        /* The copy is gone, and the cards are read again. */
        a->successful=true;toast(a,"Copy deleted");a->toast_icon=ICON_TRASH;a->copy_pick=-1;a->copies_known=false;screen(a,LIST);start_copy_job(a,J_COPY_LIST);return;
    }
    char size[48],line[320],names[200];
    if(kind==J_COPY_VERIFY){
        unsigned int count=0;uint64_t bytes=0;int parts=json_get(j,0,"partitions");
        for(int i=parts+1;parts>=0&&j->tokens[parts].type==J_ARRAY&&i<json_next(j,parts);i=json_next(j,i)){count++;bytes+=json_uint(j,json_get(j,i,"size_bytes"),0);}
        human_bytes(bytes,size,sizeof(size));
        a->successful=true;copy(a->status,sizeof(a->status),"Backup verified");
        snprintf(a->message,sizeof(a->message),T("%u partitions, %s. Every checksum matches."),count,size);
        if(json_bool(j,json_get(j,0,"restorable"),0)!=1){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("This phone's partitions are laid out differently: the copy cannot be written back here."));}
        copy_names(j,"locked",names,sizeof(names));
        if(names[0]){
            snprintf(line,sizeof(line),T("%s on the phone differ from the copy. The bootloader locks them while the phone runs, so a restore leaves them as they are."),names);
            append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),line);
            if(json_bool(j,json_get(j,0,"vbmeta_blocks"),0)==1){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("Verification is on in the phone's vbmeta: flash the copy's vbmeta in Download mode before a restore."));}
        }
        a->result_return=COPY_DETAIL;screen(a,RESULT);return;
    }
    /* Restored: what was written, what was the same already, what stayed locked, and the way back. */
    a->successful=true;a->message[0]=0;
    copy_names(j,"restored",names,sizeof(names));
    copy(a->status,sizeof(a->status),names[0]?"Android restored":"Android matches the copy");
    if(names[0]){snprintf(line,sizeof(line),T("Written and read back: %s."),names);append(a->message,sizeof(a->message),line);}
    else append(a->message,sizeof(a->message),tr("Every partition already holds what the copy holds: nothing was written."));
    copy_names(j,"unchanged",names,sizeof(names));
    if(names[0]&&a->message[0]&&json_bool(j,json_get(j,0,"identical"),0)!=1){snprintf(line,sizeof(line),T("Already the same: %s."),names);append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),line);}
    copy_names(j,"left_locked",names,sizeof(names));
    if(names[0]){snprintf(line,sizeof(line),T("Left as they are, locked by the bootloader: %s. Flash them in Download mode if Android does not start."),names);append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),line);}
    append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("Recovery and cache were not touched."));
    char kept[PATH_LEN]={0},before[PATH_LEN]={0};jstring(j,0,"kept_boot",kept,sizeof(kept));jstring(j,0,"before",before,sizeof(before));
    if(kept[0]){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),tr("The BOOT that was there is kept on cache:"));append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),kept);}
    if(before[0]){append(a->message,sizeof(a->message),"\n\n");append(a->message,sizeof(a->message),T("The old content was saved here:"));append(a->message,sizeof(a->message),"\n");append(a->message,sizeof(a->message),before);}
    a->result_return=COPY_DETAIL;screen(a,RESULT);
}
/* Android's category of Restore and Verify: the copies are looked for on every mounted card. */
static void open_copies(App*a){
    a->list_category=1;a->install.pick_backup=false;a->copies_known=false;a->copy_error[0]=0;a->copy_pick=-1;a->page_scroll=0;screen(a,LIST);
    /* A read that cannot start must not leave the page reading for ever. */
    if(!start_copy_job(a,J_COPY_LIST)&&!a->preview){a->copies_known=true;copy(a->copy_error,sizeof(a->copy_error),"The cards cannot be read now.");}
}
static void list_categories(App*a,int y){
    static const char*const names[]={"Linux","Android"};
    for(int k=0;k<2;k++){
        Rect r={28+k*340,y,324,64};bool on=a->list_category==k;rounded(a,r,32,on?ORANGE:PANEL);
        raw_text(true);centered(a,r,1,on?ui_theme->on_accent:TEXT,names[k]);raw_text(false);register_button(a,r,A_LIST_CATEGORY+k,ui_free(a)&&!on,0);
    }
}
static void render_copies(App*a,int top){
    bool busy=a->job.pid&&a->job.kind==J_COPY_LIST,free_ui=ui_free(a);char size[48],packed[48],line[200],when[64];
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=top-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    if((!a->copies_known||busy)&&!a->copy_count){
        rounded(a,(Rect){28,y,664,150},24,PANEL);icon(a,A_STORAGE,52,y+40,70,ORANGE);
        wrapped(a,140,y+38,520,1,TEXT,"Looking for copies on the cards...",2);y+=166;
    }
    for(unsigned int n=0;n<a->copy_count;n++){
        PhoneCopy*c=&a->copies[n];Rect r={28,y,664,140};surface(a,r,24,PANEL,A_COPY_ITEM+(int)n);
        gradient(a,(Rect){r.x+18,y+38,64,64},32,ui_theme->tile_fill[2],ui_theme->tile_fill[2]);icon(a,ICON_PHONE,r.x+31,y+51,38,ui_theme->tile_ink[2]);
        copy_when(a,c,when,sizeof(when));raw_text(true);ellipsis(a,122,y+14,480,1,TEXT,when);raw_text(false);
        human_bytes(c->bytes,size,sizeof(size));human_bytes(c->file_bytes,packed,sizeof(packed));
        int used=snprintf(line,sizeof(line),T("%u partitions, %s"),c->parts,size);
        if(used>0&&(size_t)used<sizeof(line)){
            if(c->archive)snprintf(line+used,sizeof(line)-(size_t)used,"  /  %s %s",tr("archive"),packed);
            else snprintf(line+used,sizeof(line)-(size_t)used,"  /  %s",tr("folder"));
        }
        raw_text(true);ellipsis(a,122,y+60,480,0,MUTED,line);raw_text(false);
        snprintf(line,sizeof(line),"%s%s%s",c->place[0]?c->place:c->volume,c->writable?"":"  /  ",c->writable?"":tr("read-only"));
        raw_text(true);ellipsis(a,122,y+96,480,0,c->same?MUTED:ORANGE,c->same?line:tr("Another phone"));raw_text(false);
        icon(a,ICON_CHEVRON,646,y+54,30,MUTED);register_button(a,r,A_COPY_ITEM+(int)n,free_ui,0);y+=152;
    }
    /* A card that is there but not mounted may hold copies: a tap mounts it read-only and looks. */
    for(unsigned int n=0;n<a->copy_waiting_count;n++){
        CopyVolume*v=&a->copy_waiting[n];Rect r={28,y,664,128};surface(a,r,24,PANEL,A_COPY_MOUNT+(int)n);
        gradient(a,(Rect){r.x+18,y+32,64,64},32,ui_theme->tile_fill[1],ui_theme->tile_fill[1]);icon(a,A_STORAGE,r.x+31,y+45,38,ui_theme->tile_ink[1]);
        raw_text(true);ellipsis(a,122,y+12,480,1,TEXT,v->label[0]?v->label:v->id);raw_text(false);
        human_bytes(v->bytes,size,sizeof(size));snprintf(line,sizeof(line),"%s  /  %s  /  %s",tr(!strncmp(v->id,"usb-",4)?"USB drive":"SD card"),v->fs[0]?v->fs:"?",v->bytes?size:tr("size unknown"));
        raw_text(true);ellipsis(a,122,y+56,480,0,MUTED,line);raw_text(false);
        ellipsis(a,122,y+88,480,0,ORANGE,"Not mounted: tap to look for copies on it");
        icon(a,ICON_CHEVRON,646,y+48,30,ORANGE);register_button(a,r,A_COPY_MOUNT+(int)n,free_ui,0);y+=140;
    }
    if(a->copies_known&&!busy&&!a->copy_count){
        rounded(a,(Rect){28,y,664,a->copy_waiting_count?164:300},24,PANEL);
        if(!a->copy_waiting_count){icon(a,A_STORAGE,315,y+26,90,ORANGE);centered(a,(Rect){48,y+128,624,50},1,TEXT,"No copy of Android found");
            wrapped(a,62,y+186,596,0,MUTED,a->copy_error[0]?a->copy_error:"Insert the SD card or attach the USB drive that holds A22xAndroidBackups, then read again.",3);y+=316;}
        else{text(a,52,y+20,0,ORANGE,"NO COPY ON THE MOUNTED CARDS");wrapped(a,52,y+60,616,0,MUTED,"Tap a card above: it is mounted read-only and searched. A copy is made in Backup, Phone partitions.",3);y+=180;}
    }
    a->page_content=y+a->page_scroll-top+16;a->clip_active=false;scroll_indicator(a,(Rect){698,top,5,page_height(a)},a->page_scroll,a->page_content,ORANGE);
    text_tile(a,1208,0,MUTED,"A copy holds BOOT, super and the other partitions of Android. Recovery and cache are in no copy: a restore leaves them as they are.",4);
    button(a,(Rect){28,1370,664,72},"Read the cards again",A_COPY_REFRESH,free_ui&&!busy,false,0);
}
static void copy_fact(App*a,int y,const char*label,const char*value){
    text(a,52,y,0,MUTED,label);raw_text(true);ellipsis(a,300,y,370,0,TEXT,value);raw_text(false);
}
static void render_copy_detail(App*a){
    PhoneCopy*c=copy_current(a);
    heading(a,"Copy of Android",a->list_for_verify?"Check it before you rely on it.":"Check it, or write it back to the phone.",true);
    if(!c){text_tile(a,256,1,MUTED,"This copy is no longer listed. Go back and read the cards again.",3);return;}
    bool free_ui=ui_free(a);char when[64],size[48],packed[48],line[200];
    copy_when(a,c,when,sizeof(when));human_bytes(c->bytes,size,sizeof(size));human_bytes(c->file_bytes,packed,sizeof(packed));
    rounded(a,(Rect){28,242,664,292},26,PANEL);icon_tile(a,ICON_PHONE,46,262,64,ICON_FILL);
    raw_text(true);ellipsis(a,130,262,540,1,TEXT,when);raw_text(false);
    text(a,130,308,0,c->same?MUTED:ORANGE,c->same?(c->archive?"One compressed archive":"A folder of images, made before archives"):"Made on another phone");
    snprintf(line,sizeof(line),"%u  /  %s",c->parts,size);copy_fact(a,362,"Partitions",line);
    copy_fact(a,406,"On the card",c->archive?packed:size);
    snprintf(line,sizeof(line),"%s%s%s",c->place[0]?c->place:c->volume,c->writable?"":"  /  ",c->writable?"":tr("read-only"));copy_fact(a,450,"Card",line);
    {const char*name=strrchr(c->path,'/');raw_text(true);ellipsis(a,52,494,616,0,MUTED,name?name+1:c->path);raw_text(false);}
    /* The partitions of the copy: a tap leaves one out of the restore of an archive. */
    unsigned int shown=copy_shown_parts(c),columns=shown>8?3u:2u,rows=(shown+columns-1)/columns;int top=550,height=116+(int)rows*46,width=(616-((int)columns-1)*12)/(int)columns;
    uint32_t data=copy_data_parts(c);bool with_data=data&&c->archive&&(a->copy_off&data)!=data;
    rounded(a,(Rect){28,top,664,height},24,PANEL);text(a,52,top+16,0,ORANGE,"PARTITIONS TO RESTORE");
    for(unsigned int k=0;k<shown;k++){
        Rect r={52+(int)(k%columns)*(width+12),top+56+(int)(k/columns)*46,width,40};bool on=!(a->copy_off&(1u<<k));
        rounded(a,r,20,on?ORANGE_DARK:BG);raw_text(true);ellipsis(a,r.x+18,r.y+7,r.w-36,0,on?TEXT:MUTED,c->labels[k]);raw_text(false);
        register_button(a,r,A_COPY_PART+(int)k,free_ui&&c->archive,0);
    }
    ellipsis(a,52,top+64+(int)rows*46,616,0,MUTED,c->archive?(data?"Tap leaves a partition out; userdata puts the data back.":"Tap a partition to leave it out."):"A folder copy is restored whole.");
    int y=top+height+32;
    if(!c->same)text_tile(a,y,0,ORANGE,"This copy was made on another phone and is not written back here.",3);
    else if(!c->archive&&!c->writable)text_tile(a,y,0,ORANGE,"A folder copy saves the replaced partitions beside itself first: mount this card with write access in Files.",4);
    else if(with_data)text_tile(a,y,0,ORANGE,"userdata and metadata are written whole: everything on userdata now, Linux systems too, is replaced. Android opens the data only while this phone's secure chip keeps the same keys.",5);
    else text_tile(a,y,0,MUTED,"Recovery and cache are in no copy and stay. The copy is checked first; only what differs is written, and each partition is read back.",4);
    bool verify_first=a->list_for_verify;
    button(a,(Rect){28,1088,664,80},"Hold 3 seconds: delete this copy",A_COPY_DELETE,free_ui&&c->writable,false,3000);
    button(a,(Rect){28,1192,664,92},"Verify this copy",A_COPY_VERIFY,free_ui,verify_first,0);
    button(a,(Rect){28,1318,664,96},"Hold 3 seconds: restore Android",A_COPY_RESTORE,free_ui&&c->same&&copy_chosen(a,c)>0&&(c->archive||c->writable),!verify_first,3000);
}
static bool copies_action(App*a,int id){
    if(a->screen==LIST&&!a->install.pick_backup&&(id==A_LIST_CATEGORY||id==A_LIST_CATEGORY+1)){
        if(!ui_free(a))return true;
        if(id==A_LIST_CATEGORY+1){open_copies(a);return true;}
        /* Linux: its snapshots are listed when their storage is there; else the page says where to look. */
        a->list_category=0;a->page_scroll=0;a->dirty=true;
        if(!a->device||a->preview||!access(a->repositories[a->store],F_OK)){if(!start_job(a,J_LIST))screen(a,LIST);}
        else{a->backup_count=a->list_page=0;copy(a->message,sizeof(a->message),"Choose an SD card or USB stick with backups, or restore a partition image.");screen(a,LIST);}
        return true;
    }
    if(a->screen==LIST&&list_android(a)){
        if(id==A_COPY_REFRESH){if(ui_free(a)){a->copies_known=false;a->copy_error[0]=0;if(!start_copy_job(a,J_COPY_LIST))a->copies_known=true;a->dirty=true;}return true;}
        /* Android's own data starts left out of a restore: it replaces everything on userdata. */
        if(id>=A_COPY_ITEM&&id<A_COPY_ITEM+(int)a->copy_count){if(ui_free(a)){a->copy_pick=id-A_COPY_ITEM;a->copy_off=copy_data_parts(&a->copies[a->copy_pick]);screen(a,COPY_DETAIL);}return true;}
        if(id>=A_COPY_MOUNT&&id<A_COPY_MOUNT+(int)a->copy_waiting_count){
            if(!ui_free(a))return true;
            /* Read-only is enough to find and restore an archive; the list is read again once it is mounted. */
            CopyVolume*v=&a->copy_waiting[id-A_COPY_MOUNT];FileRequest q={.kind=J_FS_MOUNT};
            copy(q.volume,sizeof(q.volume),v->id);copy(q.volume_token,sizeof(q.volume_token),v->token);copy(q.mode,sizeof(q.mode),"ro");
            a->copy_mounting=start_files_request(a,&q);return true;
        }
    }
    if(a->screen!=COPY_DETAIL)return false;
    if(id==A_BACK){screen(a,LIST);return true;}
    PhoneCopy*c=copy_current(a);
    if(!c||!ui_free(a))return id==A_COPY_VERIFY||id==A_COPY_RESTORE||id==A_COPY_DELETE||(id>=A_COPY_PART&&id<A_COPY_PART+COPY_PARTS);
    if(id>=A_COPY_PART&&id<A_COPY_PART+(int)copy_shown_parts(c)){
        if(c->archive){uint32_t bit=1u<<(id-A_COPY_PART),data=copy_data_parts(c);a->copy_off^=(bit&data)?data:bit;a->dirty=true;}
        return true;
    }
    if(id==A_COPY_VERIFY||id==A_COPY_RESTORE||id==A_COPY_DELETE){
        int kind=id==A_COPY_VERIFY?J_COPY_VERIFY:(id==A_COPY_RESTORE?J_COPY_RESTORE:J_COPY_DELETE);
        if(kind==J_COPY_RESTORE&&(!c->same||!copy_chosen(a,c)||!(c->archive||c->writable)))return true;
        if(kind==J_COPY_DELETE&&!c->writable)return true;
        if(!start_copy_job(a,kind))files_problem(a,"COPY_UNAVAILABLE","Wait for the current operation to finish.",COPY_DETAIL);
        return true;
    }
    return false;
}
#endif

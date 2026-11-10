/* Phone partitions backup, as TWRP does, with identity_backup.py: the phone's identity (EFS, IMEI
   and NV data, 2026-11-08) or the partitions of an Android installation (BOOT, super, dtbo,
   vbmeta and others, 2026-11-10, --kind android). Saved to a mounted writable volume with a
   manifest of checksums and read back; a copy is written back from the restore page. */

static bool identity_android(const App*a){return a->id_kind==1;}
static void identity_helper(App*a,char*out,size_t size){
    /* identity_backup.py sits beside the other helpers in /opt/a22x-recovery. */
    copy(out,size,a->distro_helper);char*slash=strrchr(out,'/');
    if(slash&&(size_t)(slash-out)+20<size)strcpy(slash+1,"identity_backup.py");else out[0]=0;
}
static FileVolume*identity_volume(App*a){
    FileVolume*v=a->id_volume_index>=0&&a->id_volume_index<a->volume_count?&a->volumes[a->id_volume_index]:NULL;
    return v&&!strcmp(v->id,a->id_volume_id)&&!strcmp(v->token,a->id_volume_token)?v:NULL;
}
static void identity_select(App*a,int index){
    a->id_volume_index=index;a->id_volume_id[0]=a->id_volume_token[0]=0;
    if(index>=0&&index<a->volume_count){copy(a->id_volume_id,sizeof(a->id_volume_id),a->volumes[index].id);copy(a->id_volume_token,sizeof(a->id_volume_token),a->volumes[index].token);}
}
/* Android partitions never go to userdata: a factory reset of Android erases it. */
static bool identity_writable_for(const App*a,const FileVolume*v){
    return v&&v->mounted&&!v->read_only&&v->write_supported&&v->token[0]&&!(identity_android(a)&&(!strcmp(v->id,"userdata")||!strcmp(v->label,"userdata")));
}
#define identity_writable(v) identity_writable_for(a,(v))
static void identity_open_kind(App*a,int kind){
    a->id_kind=kind;a->id_off=0;
    a->id_known=false;a->id_count=0;a->id_error[0]=0;a->id_done[0]=0;a->id_pick=-1;a->id_backup_count=0;identity_select(a,-1);
    a->fs_inventory_destination=IDENTITY;screen(a,IDENTITY);start_files_job(a,J_FS_INVENTORY);
}
static void identity_open(App*a){identity_open_kind(a,0);}
/* After the volume list: removable storage first, then the list of partitions. */
static void identity_inventory(App*a){
    int chosen=-1;for(int n=0;n<a->volume_count;n++){if(!identity_writable(&a->volumes[n]))continue;if(chosen<0)chosen=n;if(!strncmp(a->volumes[n].id,"sdcard",6)||!strncmp(a->volumes[n].id,"usb",3)){chosen=n;break;}}
    identity_select(a,chosen);a->id_list_after=true;screen(a,IDENTITY);
}
static unsigned int identity_chosen(const App*a,uint64_t*bytes){
    unsigned int count=0;if(bytes)*bytes=0;
    for(unsigned int n=0;n<a->id_count;n++)if(!(a->id_off&(1u<<n))){count++;if(bytes)*bytes+=a->id_sizes[n];}
    return count;
}
static bool start_identity_job(App*a,int kind){
    if(a->job.pid||a->preview||kind<J_ID_LIST||kind>J_ID_RESTORE)return false;
    identity_helper(a,a->id_helper,sizeof(a->id_helper));
    if(!a->id_helper[0]){files_problem(a,"IDENTITY_UNAVAILABLE","The identity backup helper is not configured.",IDENTITY);return false;}
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->id_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(identity_android(a)){args[n++]="--kind";args[n++]="android";}
    if(identity_android(a)&&kind==J_ID_BACKUP){
        a->id_labels_arg[0]=0;
        for(unsigned int k=0;k<a->id_count;k++)if(!(a->id_off&(1u<<k))){if(a->id_labels_arg[0])append(a->id_labels_arg,sizeof(a->id_labels_arg),",");append(a->id_labels_arg,sizeof(a->id_labels_arg),a->id_labels[k]);}
        if(!a->id_labels_arg[0])return false;
        args[n++]="--labels";args[n++]=a->id_labels_arg;
    }
    args[n++]=kind==J_ID_LIST?"list":kind==J_ID_BACKUP?"backup":kind==J_ID_BACKUPS?"backups":"restore";
    if(kind==J_ID_BACKUPS||kind==J_ID_RESTORE){
        FileVolume*v=identity_volume(a);
        if(!v){files_problem(a,"DESTINATION_CHANGED","Choose a mounted volume again.",IDENTITY);return false;}
        args[n++]="--volume";args[n++]=a->id_volume_id;
        if(kind==J_ID_RESTORE){
            if(a->id_pick<0||a->id_pick>=(int)a->id_backup_count||!a->id_backups[a->id_pick].same)return false;
            args[n++]="--path";args[n++]=a->id_backups[a->id_pick].path;args[n++]="--expected-manifest-sha256";args[n++]=a->id_backups[a->id_pick].sha;args[n++]="--confirm";
        }
    }
    if(kind==J_ID_BACKUP){
        FileVolume*v=identity_volume(a);
        if(!identity_writable(v)){files_problem(a,"DESTINATION_CHANGED","Choose a mounted writable volume again.",IDENTITY);return false;}
        args[n++]="--volume";args[n++]=a->id_volume_id;a->id_done[0]=0;
    }
    args[n]=NULL;bool ok=launch_job(a,kind,args);a->result_return=IDENTITY;return ok;
}
static void complete_identity_job(App*a,const Json*j,int kind,bool ok){
    if(!ok||!j){
        char code[64]="IDENTITY_FAILED",message[256]="The partition backup did not finish.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_ID_LIST){copy(a->id_error,sizeof(a->id_error),message);a->id_known=true;a->dirty=true;return;}
        files_problem(a,code,message,IDENTITY);return;
    }
    if(kind==J_ID_LIST){
        a->id_count=0;a->id_bytes=json_uint(j,json_get(j,0,"bytes"),0);int array=json_get(j,0,"partitions");
        for(int i=array+1;array>=0&&i<j->count&&j->tokens[i].start<j->tokens[array].end;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT||a->id_count>=ID_PARTS)break;
            jstring(j,i,"label",a->id_labels[a->id_count],sizeof(a->id_labels[0]));a->id_sizes[a->id_count]=json_uint(j,json_get(j,i,"size_bytes"),0);a->id_count++;
        }
        a->id_known=true;a->dirty=true;return;
    }
    if(kind==J_ID_BACKUPS){
        a->id_backup_count=0;a->id_pick=-1;int array=json_get(j,0,"backups");
        for(int i=array+1;array>=0&&i<j->count&&j->tokens[i].start<j->tokens[array].end;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT||a->id_backup_count>=8)break;
            __typeof__(a->id_backups[0])*b=&a->id_backups[a->id_backup_count];memset(b,0,sizeof(*b));
            jstring(j,i,"path",b->path,sizeof(b->path));jstring(j,i,"created_utc",b->created,sizeof(b->created));jstring(j,i,"manifest_sha256",b->sha,sizeof(b->sha));
            b->parts=(unsigned int)json_uint(j,json_get(j,i,"partitions"),0);b->bytes=json_uint(j,json_get(j,i,"bytes"),0);b->same=json_bool(j,json_get(j,i,"same_phone"),0);
            if(b->path[0]&&strlen(b->sha)==64)a->id_backup_count++;
        }
        a->dirty=true;return;
    }
    if(kind==J_ID_RESTORE){
        bool same=json_bool(j,json_get(j,0,"identical"),0);a->successful=true;
        toast(a,identity_android(a)?(same?"Partitions already match this copy":"Android partitions restored and checked")
                                   :(same?"Identity already matches this copy":"Identity restored and checked"));
        screen(a,IDENTITY_RESTORE);return;
    }
    jstring(j,0,"path",a->id_done,sizeof(a->id_done));a->successful=true;
    toast(a,identity_android(a)?"Android partitions saved":"Phone identity saved");screen(a,IDENTITY);
}
/* The two kinds of copy share this page; a choice above switches between them. */
static void identity_kinds(App*a,int y){
    static const char*const names[]={"Identity","Android partitions"};
    for(int k=0;k<2;k++){
        Rect r={28+k*340,y,324,64};bool on=a->id_kind==k;rounded(a,r,32,on?ORANGE:PANEL);
        centered(a,r,1,on?ui_theme->on_accent:TEXT,names[k]);register_button(a,r,A_ID_KIND+k,ui_free(a)&&!on,0);
    }
}
static void render_identity(App*a){
    bool android=identity_android(a);
    heading(a,android?"Android partitions":"Phone identity",android?"BOOT, super, dtbo, vbmeta and others of this phone.":"EFS, IMEI and modem data of this phone.",true);
    identity_kinds(a,232);
    int top=316;rounded(a,(Rect){28,top,664,300},24,PANEL);icon_tile(a,android?ICON_FLASH:ICON_PHONE,46,top+16,56,ICON_FILL);
    text(a,120,top+26,1,TEXT,android?"Partitions to save":"Identity partitions");
    if(!a->id_known)text(a,52,top+106,0,MUTED,a->job.pid?"Reading partitions...":"Not read yet");
    else if(a->id_error[0])wrapped(a,52,top+106,606,0,ORANGE,a->id_error,4);
    else if(android){
        /* Chips: every partition starts chosen; a tap leaves one out. */
        for(unsigned int n=0;n<a->id_count&&n<8;n++){
            Rect r={52+(int)(n%2)*306,top+84+(int)(n/2)*44,294,38};bool on=!(a->id_off&(1u<<n));
            rounded(a,r,19,on?ORANGE_DARK:BG);raw_text(true);ellipsis(a,r.x+18,r.y+6,r.w-36,0,on?TEXT:MUTED,a->id_labels[n]);raw_text(false);
            register_button(a,r,A_ID_PART+(int)n,ui_free(a),0);
        }
        uint64_t bytes;unsigned int chosen=identity_chosen(a,&bytes);char size[48],note[128];human_bytes(bytes,size,sizeof(size));
        snprintf(note,sizeof(note),T("%u of %u partitions, %s"),chosen,a->id_count,size);text(a,52,top+260,0,MUTED,note);
    }else{
        char line[512]="";size_t used=0;for(unsigned int n=0;n<a->id_count&&used<sizeof(line)-40;n++)used+=(size_t)snprintf(line+used,sizeof(line)-used,"%s%s",n?", ":"",a->id_labels[n]);
        wrapped(a,52,top+98,606,1,TEXT,a->id_count?line:"None found",3);
        char size[64],note[128];human_bytes(a->id_bytes,size,sizeof(size));snprintf(note,sizeof(note),T("%u partitions, %s"),a->id_count,size);text(a,52,top+234,0,MUTED,note);
    }
    FileVolume*v=identity_volume(a);
    rounded(a,(Rect){28,640,664,230},24,PANEL);text(a,52,662,0,ORANGE,"SAVE TO");
    wrapped(a,52,706,606,1,TEXT,v?(v->label[0]?v->label:v->id):"No mounted writable volume",2);
    if(v)ellipsis(a,52,790,606,0,MUTED,v->mountpoint);
    button(a,(Rect){28,890,664,84},"Choose next volume",A_ID_DEST_NEXT,ui_free(a)&&v,false,0);
    if(a->id_done[0]){rounded(a,(Rect){28,994,664,150},24,PANEL);text(a,52,1014,0,GREEN,"SAVED AND CHECKED");ellipsis(a,52,1060,606,0,TEXT,a->id_done);}
    else if(android)wrapped(a,40,1000,640,0,MUTED,v?"A new folder A22xAndroidBackups gets each chosen partition and its checksum; empty space takes no room. userdata cannot hold it: a factory reset of Android erases it.":"Mount an SD card or USB drive with write access in Files, then come back.",5);
    else wrapped(a,40,1000,640,0,MUTED,v?"A new folder A22xIdentityBackups is written with every partition and its checksum. Keep it off the phone too: no image or firmware download contains it.":"Mount an SD card or USB drive with write access in Files, then come back.",5);
    button(a,(Rect){28,1212,664,84},"Restore from a copy",A_ID_RESTORE_OPEN,ui_free(a)&&v&&a->id_known&&a->id_count>0,false,0);
    button(a,(Rect){28,1320,664,104},"Hold 2 seconds to back up",A_ID_BACKUP,ui_free(a)&&identity_writable(v)&&a->id_known&&identity_chosen(a,NULL)>0,true,2000);
}
/* Copies on the chosen volume; an identity copy goes back only to the phone it came from. */
static void render_identity_restore(App*a){
    bool android=identity_android(a);
    heading(a,android?"Restore Android partitions":"Restore identity",android?"Write a saved copy back. A replaced BOOT starts Android instead of Linux.":"Write a saved copy back to this phone.",true);
    bool busy=a->job.pid&&a->job.kind==J_ID_BACKUPS;int y=242;
    if(busy)text(a,52,y+10,0,MUTED,"Reading copies...");
    else if(!a->id_backup_count)wrapped(a,40,y+10,640,0,MUTED,android?"No copy of Android partitions on this volume.":"No identity copy on this volume.",3);
    for(unsigned int n=0;n<a->id_backup_count&&!busy;n++){
        Rect r={28,y,664,132};bool chosen=a->id_pick==(int)n;rounded(a,r,24,chosen?ORANGE_DARK:PANEL);
        char line[160],size[48];human_bytes(a->id_backups[n].bytes,size,sizeof(size));
        raw_text(true);text(a,52,y+18,1,TEXT,a->id_backups[n].created[0]?a->id_backups[n].created:a->id_backups[n].path);raw_text(false);
        snprintf(line,sizeof(line),T("%u partitions, %s"),a->id_backups[n].parts,size);text(a,52,y+70,0,a->id_backups[n].same?MUTED:ORANGE,a->id_backups[n].same?line:tr("Another phone"));
        register_button(a,r,A_ID_ITEM+(int)n,ui_free(a),0);y+=148;
    }
    wrapped(a,40,1100,640,0,MUTED,"The partitions that differ are saved to a new copy first, then written and read back.",4);
    bool ok=a->id_pick>=0&&a->id_pick<(int)a->id_backup_count&&a->id_backups[a->id_pick].same;
    button(a,(Rect){28,1320,664,104},"Hold 3 seconds to restore",A_ID_RESTORE,ui_free(a)&&ok&&identity_writable(identity_volume(a)),true,3000);
}
static bool identity_action(App*a,int id){
    if(id==A_IDENTITY){identity_open(a);return true;}
    if(id==A_ID_KIND||id==A_ID_KIND+1){identity_open_kind(a,id-A_ID_KIND);return true;}
    if(id>=A_ID_PART&&id<A_ID_PART+ID_PARTS){a->id_off^=1u<<(id-A_ID_PART);a->dirty=true;return true;}
    if(id==A_ID_DEST_NEXT){if(a->volume_count<=0)return true;int start=a->id_volume_index,n=start;for(int k=0;k<a->volume_count;k++){n=(n+1)%a->volume_count;if(identity_writable(&a->volumes[n]))break;}identity_select(a,n);a->dirty=true;return true;}
    if(id==A_ID_BACKUP){start_identity_job(a,J_ID_BACKUP);return true;}
    if(id==A_ID_RESTORE_OPEN){a->id_backup_count=0;a->id_pick=-1;screen(a,IDENTITY_RESTORE);start_identity_job(a,J_ID_BACKUPS);return true;}
    if(id>=A_ID_ITEM&&id<A_ID_ITEM+8){a->id_pick=id-A_ID_ITEM;a->dirty=true;return true;}
    if(id==A_ID_RESTORE){start_identity_job(a,J_ID_RESTORE);return true;}
    return false;
}
#undef identity_writable

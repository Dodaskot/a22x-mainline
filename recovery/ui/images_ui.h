/* Images saved on the phone (2026-11-10): the store on userdata, /.a22x-images. An image comes
   there from the catalog over Wi-Fi, or is copied over USB files, and installs without an SD card.
   A clean installation keeps the store, and a switch never moves it. */
static bool images_job(int kind){return kind==J_IMAGES||kind==J_IMAGES_DOWNLOAD||kind==J_IMAGES_DELETE;}

static bool start_images_job(App*a,int kind,const char*argument){
    if(a->job.pid||a->preview||!a->distro_helper[0])return false;
    if(argument)copy(a->saved_argument,sizeof(a->saved_argument),argument);
    char*args[10];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_IMAGES)args[n++]="images";
    else if(kind==J_IMAGES_DOWNLOAD){args[n++]="images-download";args[n++]="--remote";args[n++]=a->saved_argument;args[n++]="--confirm";}
    else{args[n++]="images-delete";args[n++]="--file";args[n++]=a->saved_argument;args[n++]="--confirm";}
    args[n]=NULL;
    return launch_job(a,kind,args);
}

static void parse_store(App*a,const Json*j){
    a->saved_count=0;a->saved_free=json_uint(j,json_get(j,0,"free_bytes"),0);
    a->store_volume[0]=a->store_token[0]=a->store_label[0]=a->store_reason[0]=0;
    jstring(j,0,"store_volume",a->store_volume,sizeof(a->store_volume));jstring(j,0,"store_token",a->store_token,sizeof(a->store_token));
    jstring(j,0,"store_label",a->store_label,sizeof(a->store_label));jstring(j,0,"store_reason",a->store_reason,sizeof(a->store_reason));
    int list=json_get(j,0,"images");
    if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list)&&a->saved_count<STORE_MAX;i=json_next(j,i)){
        StoredImage*s=&a->saved[a->saved_count];memset(s,0,sizeof(*s));
        jstring(j,i,"file",s->file,sizeof(s->file));jstring(j,i,"path",s->path,sizeof(s->path));
        jstring(j,i,"name",s->name,sizeof(s->name));jstring(j,i,"version",s->version,sizeof(s->version));jstring(j,i,"family",s->family,sizeof(s->family));
        s->size=json_uint(j,json_get(j,i,"size"),0);s->checked=json_bool(j,json_get(j,i,"checked"),0);
        if(s->file[0]&&s->path[0])a->saved_count++;
    }
    a->saved_known=true;
}

/* The store is on a card: Android holds userdata, so a download has no place on the phone itself. */
static bool store_on_card(const App*a){return a->saved_known&&a->store_volume[0]&&strcmp(a->store_volume,"userdata")!=0;}
static void complete_images_job(App*a,const Json*j,int kind,bool ok){
    if(!ok){
        /* Without a mounted userdata the list stays unknown; the page asks for the mount. */
        if(kind==J_IMAGES){a->saved_known=false;a->dirty=true;return;}
        char code[96]="IMAGES_FAILED",message[512]="The image store did not return a valid result.";
        if(j){int e=json_get(j,0,"error");jstring(j,e,"code",code,sizeof(code));jstring(j,e,"message",message,sizeof(message));}
        a->saved_delete=0;files_problem(a,code,message,a->screen==ANDROID_IMAGE?ANDROID_IMAGE:SYSTEMS_IMAGES);return;
    }
    parse_store(a,j);a->saved_delete=0;
    if(kind==J_IMAGES_DOWNLOAD){char saved[208];jstring(j,0,"saved",saved,sizeof(saved));snprintf(a->saved_note,sizeof(a->saved_note),store_on_card(a)?T("Saved on the card: %s"):T("Saved on the phone: %s"),saved);}
    else if(kind==J_IMAGES_DELETE)copy(a->saved_note,sizeof(a->saved_note),tr("The image was deleted."));
    a->dirty=true;
}

/* An image of the store goes through the same install pages as a file on an SD card. */
static void store_choose(App*a,const StoredImage*s){
    a->systems_remote_id[0]=0;copy(a->systems_source_volume,sizeof(a->systems_source_volume),"userdata");
    copy(a->systems_manifest,sizeof(a->systems_manifest),s->path);copy(a->systems_source_token,sizeof(a->systems_source_token),a->systems_volume_token);
    a->systems_wipe=!a->systems_adding;a->systems_pick_manifest=false;
    a->systems_desktop=0;a->systems_new_account=!a->systems_adding;a->systems_password[0]=0;copy(a->systems_username,sizeof(a->systems_username),"user");
    systems_forget_choices(a);
    systems_wifi_from_recovery(a);
    screen(a,SYSTEMS_SETUP);
}

static void open_images(App*a){
    a->saved_delete=0;a->saved_note[0]=0;a->page_scroll=0;screen(a,SYSTEMS_IMAGES);
    if(systems_ready(a)){a->saved_known=false;start_images_job(a,J_IMAGES,NULL);}
}

static void render_images(App*a){
    heading(a,"Saved images","In the phone's memory: they install without an SD card.",true);
    bool ready=systems_ready(a),free_ui=ui_free(a);
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    if(!ready){
        y+=text_tile(a,y+12,0,ORANGE,"Mount userdata with write access first: the images are kept there.",3)+12;
        button(a,(Rect){28,y,664,84},"Mount userdata",A_SYS_MOUNT,free_ui&&systems_can_mount(a),false,0);y+=108;
    }else{
        button(a,(Rect){28,y,664,96},"Download from the catalog",A_IMAGES_CATALOG,free_ui,false,0);y+=116;
        if(a->saved_known){char size[48],line[120];human_bytes(a->saved_free,size,sizeof(size));snprintf(line,sizeof(line),T("Free on userdata: %s"),size);
            raw_text(true);ellipsis(a,40,y,640,0,MUTED,line);raw_text(false);y+=44;}
        if(a->saved_note[0]){raw_text(true);ellipsis(a,40,y,640,0,GREEN,a->saved_note);raw_text(false);y+=44;}
        if(!a->saved_known){wrapped(a,40,y+8,640,0,MUTED,"Reading the saved images...",2);y+=72;}
        else if(!a->saved_count){y+=text_tile(a,y+20,0,MUTED,"No images are saved yet. Download one from the catalog, or copy an image into the .a22x-images folder of userdata with USB files.",5)+16;}
        for(unsigned int i=0;i<a->saved_count;i++){
            const StoredImage*s=&a->saved[i];unsigned int tone=s->family[0]?catalog_tone(s->family):3;
            Rect r={28,y,560,112};surface(a,r,24,PANEL,A_IMAGE_ITEM+(int)i);
            gradient(a,(Rect){r.x+18,y+24,64,64},32,ui_theme->tile_fill[tone],ui_theme->tile_fill[tone]);icon(a,ICON_LINUX,r.x+31,y+37,38,free_ui?ui_theme->tile_ink[tone]:MUTED);
            char title[112],size[48],note[160];
            if(s->name[0])snprintf(title,sizeof(title),"%s %s",s->name,s->version);else copy(title,sizeof(title),s->file);
            human_bytes(s->size,size,sizeof(size));snprintf(note,sizeof(note),"%s  /  %s",size,tr(s->checked?"checked against the catalog":"copied file"));
            raw_text(true);ellipsis(a,122,y+14,440,1,TEXT,title);ellipsis(a,122,y+62,440,0,MUTED,note);raw_text(false);
            register_button(a,r,A_IMAGE_ITEM+(int)i,free_ui,0);
            Rect d={600,y,92,112};surface(a,d,24,PANEL,A_IMAGE_DELETE+(int)i);icon(a,ICON_TRASH,d.x+27,y+37,38,free_ui?RED:MUTED);
            register_button(a,d,A_IMAGE_DELETE+(int)i,free_ui,0);y+=124;
        }
    }
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
    /* store_delete is the image asked about, counted from 1; 0 is none. */
    if(a->saved_delete&&a->saved_delete<=a->saved_count){
        const StoredImage*s=&a->saved[a->saved_delete-1];char size[48],line[160];human_bytes(s->size,size,sizeof(size));
        rounded(a,(Rect){20,930,680,540},28,PANEL2);
        text(a,52,962,1,TEXT,"Delete this image?");
        raw_text(true);ellipsis(a,52,1020,616,0,TEXT,s->name[0]?s->name:s->file);snprintf(line,sizeof(line),"%s  /  %s",s->file,size);ellipsis(a,52,1062,616,0,MUTED,line);raw_text(false);
        wrapped(a,52,1116,616,0,MUTED,"It is removed from the phone's memory. Installed systems are not affected.",3);
        button(a,(Rect){40,1240,640,96},"Hold 2 seconds to delete",A_IMAGE_DELETE_CONFIRM,free_ui,true,2000);
        button(a,(Rect){40,1352,640,84},"Cancel",A_IMAGE_DELETE_CANCEL,true,false,0);
    }
    if(a->systems_mount_sheet)render_systems_mount(a);
}

static bool images_action(App*a,int id){
    if(a->screen==SYSTEMS_NEW&&id==A_NEW_SAVED){if(ui_free(a))open_images(a);return true;}
    if(a->screen!=SYSTEMS_IMAGES)return false;
    if(a->systems_mount_sheet)return false; /* The sheet's own buttons, in systems_action. */
    if(a->saved_delete){
        if(id==A_IMAGE_DELETE_CANCEL||id==A_BACK){a->saved_delete=0;a->dirty=true;return true;}
        if(id==A_IMAGE_DELETE_CONFIRM){if(a->saved_delete<=a->saved_count&&ui_free(a))start_images_job(a,J_IMAGES_DELETE,a->saved[a->saved_delete-1].file);return true;}
        return true;
    }
    if(id==A_BACK){screen(a,SYSTEMS_NEW);return true;}
    if(id==A_IMAGES_CATALOG){
        if(!ui_free(a))return true;
        a->catalog_saving=true;a->catalog_only=1;a->catalog_return=HOME;a->catalog_family=0;a->catalog_page=0;a->page_scroll=0;a->catalog_checked=a->catalog_check_done=false;
        screen(a,SYSTEMS_CATALOG);start_catalog_job(a);return true;
    }
    if(id>=A_IMAGE_ITEM&&id<A_IMAGE_ITEM+(int)STORE_MAX){unsigned int n=(unsigned int)(id-A_IMAGE_ITEM);if(n<a->saved_count&&ui_free(a)&&systems_ready(a))store_choose(a,&a->saved[n]);return true;}
    if(id>=A_IMAGE_DELETE&&id<A_IMAGE_DELETE+(int)STORE_MAX){unsigned int n=(unsigned int)(id-A_IMAGE_DELETE);if(n<a->saved_count&&ui_free(a)){a->saved_delete=n+1;a->dirty=true;}return true;}
    return false;
}

/* The installer pages of Linux systems (2026-11-08): a new system erases userdata when the
   confirmed installation starts, and comes from the catalog (downloaded over Wi-Fi, checked
   against its pinned SHA-256) or from an image file. Packages, account and desktop follow the
   choices of the usual install pages. */
#define CATALOG_PAGE 6u
static const char*const catalog_families[]={"","fedora","debian","ubuntu","arch","manjaro","opensuse","mobian"};
static const char*const catalog_family_labels[]={"All","Fedora","Debian","Ubuntu","Arch","Manjaro","openSUSE","Mobian"};
#define CATALOG_FAMILIES (sizeof(catalog_families)/sizeof(catalog_families[0]))

static unsigned int catalog_tone(const char*family){
    static const char*const order[]={"fedora","debian","ubuntu","arch","manjaro","opensuse","mobian"};
    for(unsigned int n=0;n<sizeof(order)/sizeof(order[0]);n++)if(!strcmp(family,order[n]))return n%6;
    return 0;
}
/* A family gets a chip only when the catalog has an image of it. */
static bool catalog_family_listed(const App*a,unsigned int family){
    if(!family)return true;
    for(unsigned int i=0;i<a->catalog_count;i++)if(!strcmp(a->catalog[i].family,catalog_families[family]))return true;
    return false;
}
static bool catalog_shown(const App*a,const CatalogImage*c){return !a->catalog_family||!strcmp(c->family,catalog_families[a->catalog_family]);}
__attribute__((unused)) static unsigned int catalog_visible(const App*a){unsigned int n=0;for(unsigned int i=0;i<a->catalog_count;i++)n+=catalog_shown(a,&a->catalog[i]);return n;}

/* A large card with a tinted icon, a title and a short line, as on the home page. */
static void installer_card(App*a,Rect r,unsigned int tone,int glyph,const char*title,const char*note,int action,bool enabled){
    surface(a,r,ui_theme->bento?32:24,PANEL,action);
    gradient(a,(Rect){r.x+20,r.y+(r.h-64)/2,64,64},32,ui_theme->tile_fill[tone],ui_theme->tile_fill[tone]);
    icon(a,glyph,r.x+33,r.y+(r.h-64)/2+13,38,enabled?ui_theme->tile_ink[tone]:MUTED);
    ellipsis(a,r.x+104,r.y+r.h/2-40,r.w-160,1,enabled?TEXT:MUTED,title);
    wrapped(a,r.x+104,r.y+r.h/2+2,r.w-160,0,MUTED,note,2);
    icon(a,ICON_CHEVRON,r.x+r.w-48,r.y+r.h/2-16,32,enabled?MUTED:PANEL2);
    register_button(a,r,action,enabled,0);button_glyph(a,action,glyph);
}

static void render_systems_new(App*a){
    /* Import: a system beside the others, from the catalog or a file; nothing is erased (2026-11-09). */
    if(a->systems_adding)heading(a,"Add a system","Beside the installed systems: they and /home stay.",true);
    else heading(a,"New system","A clean installation, like the installer of a live disk.",true);
    bool ready=systems_ready(a),free_ui=ui_free(a);
    installer_card(a,(Rect){28,236,664,168},2,ICON_BROWSER,"From the internet","Minimal images from the catalog, downloaded over Wi-Fi",A_NEW_INTERNET,free_ui);
    installer_card(a,(Rect){28,420,664,168},1,A_FILES,"From a file","A system image on a USB stick or SD card",A_NEW_FILE,free_ui&&ready);
    installer_card(a,(Rect){28,604,664,168},3,ICON_PHONE,"Saved on the phone","Images in the phone's memory, no SD card needed",A_NEW_SAVED,free_ui);
    int y=800;
    if(!a->systems_known||!a->systems_mounted||a->systems_read_only){
        y+=wrapped(a,40,y,640,0,ORANGE,"Mount userdata with write access first: the installer needs it to check the space.",3)+16;
        button(a,(Rect){28,y,664,84},"Mount userdata",A_SYS_MOUNT,free_ui&&systems_can_mount(a),false,0);y+=108;
    }
    if(a->systems_adding){
        rounded(a,(Rect){28,y,664,164},24,PANEL);text(a,52,y+20,0,GREEN,"NOTHING IS ERASED");
        wrapped(a,52,y+58,616,0,TEXT,"The new system is saved beside the current one and is not activated. Switch to it in Systems.",4);
        y+=184;
        wrapped(a,40,y,640,0,MUTED,"Packages, the account and the desktop are set up as you choose on the next pages.",4);
        if(a->systems_mount_sheet)render_systems_mount(a);
        return;
    }
    rounded(a,(Rect){28,y,664,a->systems_empty?132:164},24,PANEL);
    text(a,52,y+20,0,a->systems_empty?GREEN:RED,a->systems_empty?"USERDATA IS EMPTY":"USERDATA IS ERASED");
    wrapped(a,52,y+58,616,0,TEXT,a->systems_empty?"Nothing is replaced: the new system becomes the only one on userdata.":
            "When the installation starts, every Linux system and /home on userdata are erased. Make a backup first if you need them.",4);
    y+=(a->systems_empty?132:164)+20;
    wrapped(a,40,y,640,0,MUTED,"Packages, the account and the desktop are set up as you choose on the next pages. The terminal stays available for installing by hand.",4);
    if(a->systems_mount_sheet)render_systems_mount(a);
}

static void render_catalog(App*a){
    /* Opened from the saved images: an image is downloaded to the phone, not installed (2026-11-10). */
    if(a->catalog_saving)heading(a,"Download to the phone","Saved in the phone's memory and checked, for installing later.",true);
    else heading(a,"Catalog","Official images, downloaded and checked during the installation.",true);
    bool busy=a->job.pid&&a->job.kind==J_CATALOG,checking=a->job.pid&&a->job.kind==J_CATALOG_CHECK,free_ui=ui_free(a)&&!busy;
    /* One list that scrolls under the finger, with the chips and the state of the check (2026-11-08). */
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    if(a->catalog_known&&!a->catalog_network){
        rounded(a,(Rect){28,y,664,112},22,PANEL);icon(a,ICON_WIFI,48,y+30,44,ORANGE);
        wrapped(a,108,y+18,380,0,TEXT,"Recovery is not connected to Wi-Fi.",2);
        button(a,(Rect){512,y+20,160,72},"Wi-Fi",A_CATALOG_WIFI,free_ui,true,0);y+=128;
    }
    /* Families as chips: the list shows one distribution or all of them. */
    if(a->catalog_family&&!catalog_family_listed(a,a->catalog_family))a->catalog_family=0;
    int x=28;
    for(unsigned int n=0;n<CATALOG_FAMILIES;n++){
        if(!catalog_family_listed(a,n))continue;
        int w=text_width(tr(catalog_family_labels[n]),0)+40;if(x+w>692){x=28;y+=64;}
        Rect r={x,y,w,52};rounded(a,r,26,a->catalog_family==n?ORANGE:PANEL);
        centered(a,r,0,a->catalog_family==n?ui_theme->hero_text:TEXT,catalog_family_labels[n]);
        register_button(a,r,A_CATALOG_FAMILY+(int)n,true,0);x+=w+10;
    }
    y+=64;
    /* What the check of the servers found: newer images, images gone, or no answer, and why. */
    if(a->catalog_network&&(checking||a->catalog_check_done)){
        char line[160];uint32_t ink=MUTED;
        if(checking)copy(line,sizeof(line),tr("Checking the servers for newer images..."));
        else if(a->catalog_failed&&!a->catalog_servers&&!a->catalog_gone){copy(line,sizeof(line),tr("The servers did not answer; the saved versions are shown."));ink=ORANGE;}
        else if(a->catalog_updated){snprintf(line,sizeof(line),T("Updated from the servers: %u"),a->catalog_updated);ink=GREEN;}
        else copy(line,sizeof(line),tr("The images match their servers."));
        raw_text(true);ellipsis(a,40,y,640,0,ink,line);raw_text(false);y+=40;
        if(!checking&&a->catalog_failed&&a->catalog_problem[0]){raw_text(true);ellipsis(a,40,y,640,0,MUTED,a->catalog_problem);raw_text(false);y+=36;}
    }else y+=8;
    if(!a->catalog_count){wrapped(a,40,y+12,640,0,MUTED,busy?"Reading the catalog...":"The catalog is empty in this recovery.",3);y+=80;}
    for(unsigned int i=0;i<a->catalog_count;i++){
        const CatalogImage*c=&a->catalog[i];if(!catalog_shown(a,c))continue;
        Rect r={28,y,664,112};unsigned int tone=catalog_tone(c->family);bool usable=free_ui&&c->reachable;
        surface(a,r,24,PANEL,A_CATALOG_ITEM+(int)i);
        gradient(a,(Rect){r.x+18,y+24,64,64},32,ui_theme->tile_fill[tone],ui_theme->tile_fill[tone]);icon(a,ICON_LINUX,r.x+31,y+37,38,usable?ui_theme->tile_ink[tone]:MUTED);
        char title[112];snprintf(title,sizeof(title),"%s %s",c->name,c->version);
        raw_text(true);ellipsis(a,122,y+14,470,1,usable?TEXT:MUTED,title);raw_text(false);
        char size[48],note[160];human_bytes(c->size,size,sizeof(size));
        if(c->unavailable)copy(note,sizeof(note),tr("No longer on its server"));
        else if(!c->reachable)snprintf(note,sizeof(note),"%s  /  %s",size,tr("Needs HTTPS"));
        else if(c->notes[0])snprintf(note,sizeof(note),"%s  /  %s",size,tr(c->notes));
        else snprintf(note,sizeof(note),"%s%s%s",size,c->minimal?"  /  ":"",c->minimal?tr("minimal"):"");
        /* The distribution's own signature is checked for this one (2026-11-08). */
        if(c->signed_&&!c->unavailable&&c->reachable){append(note,sizeof(note),"  /  ");append(note,sizeof(note),tr("signed"));}
        raw_text(true);ellipsis(a,122,y+62,470,0,c->reachable?MUTED:ORANGE,note);raw_text(false);
        icon(a,ICON_CHEVRON,646,y+40,30,usable?MUTED:PANEL2);
        register_button(a,r,A_CATALOG_ITEM+(int)i,usable,0);y+=124;
    }
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
    if(a->systems_mount_sheet)render_systems_mount(a);
}

static bool start_catalog_job(App*a){
    if(a->job.pid||a->preview||!a->distro_helper[0])return false;
    char*args[8];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="catalog";args[n]=NULL;
    return launch_job(a,J_CATALOG,args);
}
static bool start_catalog_check(App*a){
    if(a->job.pid||a->preview||!a->distro_helper[0])return false;
    char*args[9];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="catalog";args[n++]="--refresh";args[n]=NULL;
    a->catalog_checked=true;return launch_job(a,J_CATALOG_CHECK,args);
}
static void complete_catalog_job(App*a,const Json*j,bool ok,bool check){
    if(check){
        a->catalog_check_done=true;a->catalog_servers=a->catalog_updated=a->catalog_gone=0;a->catalog_failed=1;a->catalog_problem[0]=0;
        int r=ok&&j?json_get(j,0,"refresh"):-1;
        if(r>=0){a->catalog_servers=(unsigned int)json_uint(j,json_get(j,r,"checked"),0);a->catalog_updated=(unsigned int)json_uint(j,json_get(j,r,"updated"),0);
            a->catalog_gone=(unsigned int)json_uint(j,json_get(j,r,"unavailable"),0);a->catalog_failed=(unsigned int)json_uint(j,json_get(j,r,"failed"),0);
            /* The first reason of a failed check, on the page and in the log: nothing private is in it. */
            int problems=json_get(j,r,"problems");
            if(problems>=0&&json_next(j,problems)>problems+1){char id[48]={0},why[96]={0};
                copy(id,sizeof(id),"");int key=problems+1;int n=j->tokens[key].end-j->tokens[key].start;if(n>0&&n<(int)sizeof(id)){memcpy(id,j->text+j->tokens[key].start,(size_t)n);id[n]=0;}
                jstring(j,problems,id,why,sizeof(why));snprintf(a->catalog_problem,sizeof(a->catalog_problem),"%s: %s",id,why);}
        }else if(ok&&j)copy(a->catalog_problem,sizeof(a->catalog_problem),tr("No network for the check"));
        fprintf(stderr,"[recovery-ui:catalog-check] ok=%d checked=%u updated=%u gone=%u failed=%u problem=%s\n",ok,a->catalog_servers,a->catalog_updated,a->catalog_gone,a->catalog_failed,a->catalog_problem);
        /* A failed check leaves the list as it was read. */
        if(!ok||!j){a->dirty=true;return;}
    }
    a->catalog_known=true;a->catalog_count=0;
    if(!ok||!j){a->catalog_network=false;a->dirty=true;return;}
    a->catalog_https=json_bool(j,json_get(j,0,"https"),0);a->catalog_network=json_bool(j,json_get(j,0,"network"),0);
    int list=json_get(j,0,"images");
    if(list>=0)for(int i=list+1;i<json_next(j,list)&&a->catalog_count<CATALOG_MAX;i=json_next(j,i)){
        CatalogImage*c=&a->catalog[a->catalog_count];memset(c,0,sizeof(*c));
        jstring(j,i,"id",c->id,sizeof(c->id));jstring(j,i,"name",c->name,sizeof(c->name));jstring(j,i,"version",c->version,sizeof(c->version));
        jstring(j,i,"family",c->family,sizeof(c->family));jstring(j,i,"file",c->file,sizeof(c->file));jstring(j,i,"desktop",c->desktop,sizeof(c->desktop));jstring(j,i,"notes",c->notes,sizeof(c->notes));
        c->size=json_uint(j,json_get(j,i,"size"),0);c->minimal=json_bool(j,json_get(j,i,"minimal"),0);c->reachable=json_bool(j,json_get(j,i,"reachable"),0);
        c->unavailable=json_bool(j,json_get(j,i,"unavailable"),0);if(c->unavailable)c->reachable=false;
        c->signed_=json_bool(j,json_get(j,i,"signed"),0);
        if(c->id[0]&&c->name[0]&&c->file[0])a->catalog_count++;
    }
    a->dirty=true;
    /* With Wi-Fi, the servers are asked for newer images once per opening of the page. */
    if(!check&&a->catalog_network&&!a->catalog_checked&&a->screen==SYSTEMS_CATALOG)start_catalog_check(a);
}

/* An image chosen from the catalog goes through the same install pages as a file. */
static void catalog_choose(App*a,const CatalogImage*c){
    copy(a->systems_remote_id,sizeof(a->systems_remote_id),c->id);copy(a->systems_manifest,sizeof(a->systems_manifest),c->file);
    a->systems_source_volume[0]=a->systems_source_token[0]=0;a->systems_wipe=!a->systems_adding;a->systems_pick_manifest=false;
    a->systems_desktop=0;a->systems_new_account=!a->systems_adding;a->systems_password[0]=0;copy(a->systems_username,sizeof(a->systems_username),"user");
    systems_forget_choices(a);
    systems_wifi_from_recovery(a);
    screen(a,SYSTEMS_SETUP);
}

static bool installer_action(App*a,int id){
    if(id==A_SYS_NEW){if(ui_free(a)){a->systems_mount_sheet=false;a->systems_adding=false;screen(a,SYSTEMS_NEW);}return true;}
    if(id==A_HOME_CATALOG){
        if(ui_free(a)){a->systems_adding=false;a->catalog_saving=false;a->catalog_family=0;a->catalog_page=0;a->page_scroll=0;a->catalog_checked=a->catalog_check_done=false;screen(a,SYSTEMS_CATALOG);
            /* The install pages need the state of userdata; the catalog is read right after it. */
            a->catalog_after_status=a->distro_helper[0]!=0;if(!a->catalog_after_status||!start_systems_job(a,J_SYS_STATUS)){a->catalog_after_status=false;start_catalog_job(a);}}
        return true;
    }
    if(a->screen==SYSTEMS_NEW){
        if(a->systems_mount_sheet)return false;
        if(id==A_BACK){screen(a,SYSTEMS);return true;}
        if(id==A_NEW_INTERNET){a->catalog_saving=false;a->catalog_family=0;a->catalog_page=0;a->page_scroll=0;a->catalog_checked=a->catalog_check_done=false;screen(a,SYSTEMS_CATALOG);start_catalog_job(a);return true;}
        if(id==A_NEW_FILE){
            if(!systems_ready(a))return true;
            a->systems_remote_id[0]=0;a->systems_wipe=!a->systems_adding;a->systems_pick_manifest=true;a->systems_pick_script=false;a->systems_plan_page=0;a->volume_scroll=0;
            a->fs_inventory_destination=FILE_VOLUMES;screen(a,FILE_VOLUMES);start_files_job(a,J_FS_INVENTORY);return true;
        }
    }
    if(a->screen==SYSTEMS_CATALOG){
        if(a->systems_mount_sheet)return false; /* The sheet's own buttons, in systems_action. */
        if(id==A_BACK){if(a->catalog_saving){a->catalog_saving=false;open_images(a);return true;}screen(a,a->systems_known?SYSTEMS_NEW:HOME);return true;}
        if(id==A_CATALOG_WIFI){a->wifi_from_catalog=true;return wifi_action(a,A_WIFI);}
        if(id>=A_CATALOG_FAMILY&&id<A_CATALOG_FAMILY+(int)CATALOG_FAMILIES){a->catalog_family=(unsigned int)(id-A_CATALOG_FAMILY);a->catalog_page=0;a->page_scroll=0;a->dirty=true;return true;}
        if(id==A_CATALOG_PREV){if(a->catalog_page)a->catalog_page--;a->dirty=true;return true;}
        if(id==A_CATALOG_NEXT){a->catalog_page++;a->dirty=true;return true;}
        if(id>=A_CATALOG_ITEM&&id<A_CATALOG_ITEM+(int)CATALOG_MAX){
            unsigned int n=(unsigned int)(id-A_CATALOG_ITEM);
            if(n<a->catalog_count&&a->catalog[n].reachable&&ui_free(a)){
                /* Userdata not mounted: mounted here, then this image goes on (2026-11-08). */
                if(!systems_ready(a)){
                    if(systems_can_mount(a)){a->catalog_pending=n+1;a->systems_mount_sheet=true;a->dirty=true;}
                    else screen(a,SYSTEMS_NEW);
                    return true;
                }
                if(a->catalog_saving){
                    /* The download's own reply carries the new list of saved images. */
                    a->catalog_saving=false;a->saved_delete=0;a->page_scroll=0;screen(a,SYSTEMS_IMAGES);
                    snprintf(a->saved_note,sizeof(a->saved_note),T("Downloading %s..."),a->catalog[n].name);
                    start_images_job(a,J_IMAGES_DOWNLOAD,a->catalog[n].id);return true;}
                catalog_choose(a,&a->catalog[n]);
            }
            return true;
        }
    }
    return false;
}

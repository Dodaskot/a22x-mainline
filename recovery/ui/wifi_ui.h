/* Recovery Wi-Fi: the visible networks, joining one, and the state of the connection.
   wifi.py brings up the MediaTek driver built for the recovery kernel and talks to
   wpa_supplicant; nothing is stored, the connection lasts until recovery restarts. */
#define WIFI_PAGE 7u

/* Four bars, the way phones show signal: bottom-aligned, growing to the right. */
static void wifi_bars(App*a,int x,int y,unsigned int bars,uint32_t on,uint32_t off){
    for(unsigned int n=0;n<4;n++){int h=14+(int)n*10;rounded(a,(Rect){x+(int)n*15,y+44-h,10,h},4,n<bars?on:off);}
}
static unsigned int wifi_joined_bars(const App*a){
    for(unsigned int n=0;n<a->wifi_count;n++)if(a->wifi_nets[n].connected)return a->wifi_nets[n].bars;
    return a->wifi_connected?3u:0u;
}
static bool wifi_busy(const App*a){return a->job.pid&&wifi_job(a->job.kind);}
static const char*wifi_busy_text(const App*a){
    switch(a->job.kind){
    case J_WIFI_SCAN:return a->wifi_on?"Searching for networks...":"Turning on Wi-Fi...";
    case J_WIFI_CONNECT:return "Connecting...";
    case J_WIFI_DISCONNECT:return "Disconnecting...";
    default:return "Reading Wi-Fi...";
    }
}
static unsigned int wifi_pages(const App*a){unsigned int p=(a->wifi_count+WIFI_PAGE-1)/WIFI_PAGE;return p?p:1;}
static bool wifi_usable(const App*a){return !a->wifi_known||a->wifi_available;}

/* The settings row: the joined network's name, shown as it is, or the state in words. */
static void wifi_settings_row(App*a,int y,bool enabled){
    if(a->wifi_connected&&a->wifi_ssid[0]){raw_text(true);settings_row(a,y,ICON_WIFI,"Wi-Fi",a->wifi_ssid,A_WIFI,enabled);raw_text(false);}
    else settings_row(a,y,ICON_WIFI,"Wi-Fi",a->wifi_known&&!a->wifi_available?"Unavailable":"Not connected",A_WIFI,enabled);
}

static void render_wifi(App*a){
    heading(a,"Wi-Fi","Visible networks. Recovery stays connected until it restarts.",true);
    bool busy=wifi_busy(a),free_ui=ui_free(a)&&!busy;
    rounded(a,(Rect){28,226,664,204},24,PANEL);
    wifi_bars(a,54,262,wifi_joined_bars(a),GREEN,PANEL2);
    text(a,136,246,0,ORANGE,"CONNECTION");
    if(a->wifi_connected){
        raw_text(true);ellipsis(a,136,282,532,1,TEXT,a->wifi_ssid);raw_text(false);
        char line[96];snprintf(line,sizeof(line),T("Address %s"),a->wifi_address[0]?a->wifi_address:"—");
        raw_text(true);ellipsis(a,136,330,300,0,GREEN,line);raw_text(false);
    }else{
        const char*state=busy?wifi_busy_text(a):(!wifi_usable(a)?"Unavailable":(a->wifi_on?"Not connected":"Off"));
        ellipsis(a,136,282,532,1,busy?ORANGE:TEXT,state);
        ellipsis(a,136,330,300,0,MUTED,!wifi_usable(a)?"No Wi-Fi driver in this recovery":"Choose a network below");
    }
    button(a,(Rect){452,342,216,72},a->wifi_connected?"Disconnect":"Search",a->wifi_connected?A_WIFI_DISCONNECT:A_WIFI_SCAN,
           free_ui&&(a->wifi_connected||wifi_usable(a)),false,0);
    text(a,40,452,0,ORANGE,"VISIBLE NETWORKS");
    unsigned int pages=wifi_pages(a);if(a->wifi_page>=pages)a->wifi_page=pages-1;
    /* Joined, the card offers Disconnect; searching again moves next to the list. */
    int right=680;
    if(a->wifi_connected){
        button(a,(Rect){492,436,200,52},"Search",A_WIFI_SCAN,free_ui,false,0);right=476;
        bool kept=false;for(unsigned int n=0;n<a->wifi_count;n++)if(a->wifi_nets[n].connected&&a->wifi_nets[n].saved)kept=true;
        if(kept){button(a,(Rect){320,436,160,52},"Forget",A_WIFI_FORGET,free_ui,false,0);right=304;}
    }
    if(pages>1){char page[32];snprintf(page,sizeof(page),"%u / %u",a->wifi_page+1,pages);raw_text(true);text(a,right-text_width(page,0),452,0,MUTED,page);raw_text(false);}
    int y=492;
    if(!a->wifi_count)wrapped(a,52,y+12,616,0,MUTED,busy?"Searching for networks...":(a->wifi_known?"No networks found. Search again, or choose Other network for a hidden one.":"Tap Search to turn Wi-Fi on."),3);
    for(unsigned int i=a->wifi_page*WIFI_PAGE;i<a->wifi_count&&i<(a->wifi_page+1)*WIFI_PAGE;i++,y+=112){
        const WifiNet*w=&a->wifi_nets[i];Rect r={28,y,664,104};bool usable=free_ui&&w->supported&&!w->connected;
        surface(a,r,24,PANEL,A_WIFI_NET+(int)i);
        wifi_bars(a,50,y+30,w->bars,w->connected?GREEN:(w->supported?TEXT:MUTED),PANEL2);
        raw_text(true);ellipsis(a,128,y+14,w->secure?468:516,1,w->supported?TEXT:MUTED,w->ssid);raw_text(false);
        char note[96];
        if(w->connected)copy(note,sizeof(note),tr("Connected"));
        else if(!w->supported)snprintf(note,sizeof(note),T("%s is not supported"),w->security);
        else if(w->saved)snprintf(note,sizeof(note),"%s  /  %s",tr("Saved"),w->band);
        else snprintf(note,sizeof(note),"%s  /  %s",w->secure?w->security:tr("Open"),w->band);
        raw_text(true);ellipsis(a,128,y+60,468,0,w->connected?GREEN:MUTED,note);raw_text(false);
        if(w->secure)icon(a,ICON_LOCK,622,y+32,40,w->supported?MUTED:PANEL2);
        register_button(a,r,A_WIFI_NET+(int)i,usable,0);
    }
    if(a->wifi_error[0])wrapped(a,40,1290,640,0,RED,a->wifi_error,2);
    if(pages>1){
        button(a,(Rect){28,1370,150,84},"‹",A_WIFI_PAGE_PREV,a->wifi_page>0,false,0);
        button(a,(Rect){542,1370,150,84},"›",A_WIFI_PAGE_NEXT,a->wifi_page+1<pages,false,0);
    }
    button(a,(Rect){pages>1?190:28,1370,pages>1?340:664,84},"Other network",A_WIFI_HIDDEN,free_ui&&wifi_usable(a),false,0);
}

/* The join page works like the install Wi-Fi page: one edit buffer, the other field kept aside.
   A network picked from the list keeps its exact name; only the password is typed. */
static void wifi_focus(App*a,int kind){
    if(a->fs_prompt_kind==J_WIFI_SSID)copy(a->wifi_edit_ssid,sizeof(a->wifi_edit_ssid),a->fs_prompt);
    else if(a->fs_prompt_kind==J_WIFI_PASS)copy(a->wifi_edit_password,sizeof(a->wifi_edit_password),a->fs_prompt);
    a->fs_prompt_kind=kind;copy(a->fs_prompt,sizeof(a->fs_prompt),kind==J_WIFI_SSID?a->wifi_edit_ssid:a->wifi_edit_password);
    a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;a->dirty=true;
}
static void wifi_forget_edit(App*a){
    memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));memset(a->fs_prompt,0,sizeof(a->fs_prompt));
    a->prompt_cursor=0;a->fs_prompt_kind=0;
}
static void wifi_prompt(App*a,const WifiNet*w){
    memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));
    if(w){copy(a->wifi_edit_ssid,sizeof(a->wifi_edit_ssid),w->ssid);copy(a->wifi_edit_hex,sizeof(a->wifi_edit_hex),w->hex);}
    else a->wifi_edit_ssid[0]=a->wifi_edit_hex[0]=0;
    a->wifi_edit_fixed=w!=NULL;a->fs_prompt_kind=0;wifi_focus(a,w?J_WIFI_PASS:J_WIFI_SSID);screen(a,FILE_PROMPT);
}

static bool start_wifi_job(App*a,int kind){
    if(a->job.pid||a->preview||!wifi_job(kind)||!a->wifi_helper[0])return false;
    static const char*const verbs[]={"status","scan","connect","disconnect","forget"};
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->wifi_helper;
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=(char*)verbs[kind-J_WIFI_STATUS];
    if(kind==J_WIFI_FORGET){if(!a->wifi_ssid_hex[0])return false;args[n++]="--ssid-hex";args[n++]=a->wifi_ssid_hex;}
    if(kind==J_WIFI_CONNECT&&a->wifi_edit_saved){args[n++]="--ssid-hex";args[n++]=a->wifi_edit_hex;args[n++]="--saved";}
    else if(kind==J_WIFI_CONNECT){
        if(a->wifi_edit_fixed&&a->wifi_edit_hex[0]){args[n++]="--ssid-hex";args[n++]=a->wifi_edit_hex;}
        else{args[n++]="--ssid";args[n++]=a->wifi_edit_ssid;args[n++]="--hidden";}
        if(a->wifi_edit_password[0]){
            /* The password goes through a private file, never through the command line. */
            if(!private_workdir(a->workdir)||snprintf(a->wifi_password_file,sizeof(a->wifi_password_file),"%s/wifi-%ld",a->workdir,(long)getpid())>=(int)sizeof(a->wifi_password_file))return false;
            int fd=open(a->wifi_password_file,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
            if(fd<0){a->wifi_password_file[0]=0;return false;}
            size_t length=strlen(a->wifi_edit_password);bool written=write(fd,a->wifi_edit_password,length)==(ssize_t)length;close(fd);
            if(!written){unlink(a->wifi_password_file);a->wifi_password_file[0]=0;return false;}
            args[n++]="--password-file";args[n++]=a->wifi_password_file;
        }
    }
    args[n]=NULL;a->wifi_error[0]=0;
    bool ok=launch_job(a,kind,args);
    if(!ok&&a->wifi_password_file[0]){unlink(a->wifi_password_file);a->wifi_password_file[0]=0;}
    a->dirty=true;return ok;
}

static const char*wifi_error_text(const char*code,const char*message){
    static const struct{const char*code,*text;}known[]={
        {"WIFI_UNAVAILABLE","This recovery has no Wi-Fi driver."},{"WIFI_START_FAILED","Wi-Fi did not start. Restart recovery and try again."},
        {"WIFI_AUTH_FAILED","The password is wrong."},{"WIFI_NOT_FOUND","The network is not in range."},
        {"WIFI_TIMEOUT","The network did not answer in time."},{"WIFI_NO_ADDRESS","Joined the network, but it gave no address."},
        {"WIFI_SCAN_FAILED","Wi-Fi could not search for networks."},{"WIFI_PASSWORD_INVALID","A Wi-Fi password has 8 to 63 characters."},
        {"WIFI_NAME_INVALID","A network name has at most 32 bytes."},{"WIFI_NOT_SAVED","This network is not saved."}};
    for(size_t n=0;n<sizeof(known)/sizeof(known[0]);n++)if(!strcmp(code,known[n].code))return known[n].text;
    return message&&message[0]?message:"Wi-Fi failed.";
}
static void wifi_read_state(App*a,const Json*j){
    a->wifi_known=true;
    a->wifi_available=json_bool(j,json_get(j,0,"available"),0);a->wifi_on=json_bool(j,json_get(j,0,"on"),0);
    a->wifi_connected=json_bool(j,json_get(j,0,"connected"),0);
    jstring(j,0,"ssid",a->wifi_ssid,sizeof(a->wifi_ssid));jstring(j,0,"ssid_hex",a->wifi_ssid_hex,sizeof(a->wifi_ssid_hex));
    jstring(j,0,"address",a->wifi_address,sizeof(a->wifi_address));
    if(!a->wifi_connected){a->wifi_ssid[0]=a->wifi_ssid_hex[0]=a->wifi_address[0]=0;memset(a->wifi_password,0,sizeof(a->wifi_password));}
}
static void wifi_read_networks(App*a,const Json*j){
    int list=json_get(j,0,"networks");a->wifi_count=0;
    if(list<0)return;
    for(int i=list+1;i<json_next(j,list)&&a->wifi_count<WIFI_MAX;i=json_next(j,i)){
        WifiNet*w=&a->wifi_nets[a->wifi_count];memset(w,0,sizeof(*w));
        jstring(j,i,"ssid",w->ssid,sizeof(w->ssid));jstring(j,i,"ssid_hex",w->hex,sizeof(w->hex));
        jstring(j,i,"security",w->security,sizeof(w->security));jstring(j,i,"band",w->band,sizeof(w->band));
        uint64_t bars=json_uint(j,json_get(j,i,"bars"),1);w->bars=bars>4?4u:(unsigned int)bars;
        w->secure=json_bool(j,json_get(j,i,"secure"),1);w->supported=json_bool(j,json_get(j,i,"supported"),0);
        w->connected=json_bool(j,json_get(j,i,"connected"),0);w->saved=json_bool(j,json_get(j,i,"saved"),0);
        if(w->ssid[0]&&w->hex[0])a->wifi_count++;
    }
}
static int complete_wifi_job(App*a,Json*doc,int kind,bool ok){
    if(a->wifi_password_file[0]){unlink(a->wifi_password_file);a->wifi_password_file[0]=0;}
    int next=0;
    if(!ok){
        char code[64]="WIFI_FAILED",message[256]="";
        if(doc){int error=json_get(doc,0,"error");if(error>=0){jstring(doc,error,"code",code,sizeof(code));jstring(doc,error,"message",message,sizeof(message));}}
        copy(a->wifi_error,sizeof(a->wifi_error),wifi_error_text(code,message));
        if(!strcmp(code,"WIFI_UNAVAILABLE")){a->wifi_known=true;a->wifi_available=false;}
        /* A remembered key that no longer works: the password is asked again for that network. */
        if(kind==J_WIFI_CONNECT&&a->wifi_edit_saved&&(!strcmp(code,"WIFI_AUTH_FAILED")||!strcmp(code,"WIFI_NOT_SAVED"))&&a->screen==WIFI){
            a->wifi_edit_saved=false;memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));
            a->fs_prompt_kind=0;wifi_focus(a,J_WIFI_PASS);screen(a,FILE_PROMPT);
        }
    }else{
        wifi_read_state(a,doc);
        if(kind==J_WIFI_SCAN)wifi_read_networks(a,doc);
        /* The joined network and its password go to the next installation's Wi-Fi page. */
        if(kind==J_WIFI_CONNECT&&a->wifi_connected)copy(a->wifi_password,sizeof(a->wifi_password),a->wifi_edit_password);
        if(kind==J_WIFI_CONNECT||kind==J_WIFI_DISCONNECT||kind==J_WIFI_FORGET){a->wifi_page=0;next=J_WIFI_SCAN;}
    }
    if(kind==J_WIFI_CONNECT){memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));a->wifi_edit_saved=false;}
    a->dirty=true;return next;
}

static bool wifi_action(App*a,int id){
    if(id==A_WIFI){if(ui_free(a)){a->wifi_from_home=a->screen==HOME;a->wifi_from_catalog=a->screen==SYSTEMS_CATALOG;a->wifi_page=0;a->wifi_error[0]=0;screen(a,WIFI);start_wifi_job(a,J_WIFI_SCAN);}return true;}
    if(a->screen==WIFI){
        if(id==A_BACK){if(a->wifi_from_catalog){a->catalog_checked=a->catalog_check_done=false;screen(a,SYSTEMS_CATALOG);start_catalog_job(a);return true;}screen(a,a->wifi_from_home?HOME:TOOLS);return true;}
        if(wifi_busy(a)||a->job.pid)return id==A_WIFI_SCAN||id==A_WIFI_DISCONNECT||id==A_WIFI_FORGET||id==A_WIFI_HIDDEN||(id>=A_WIFI_NET&&id<A_WIFI_NET+(int)WIFI_MAX);
        if(id==A_WIFI_SCAN){start_wifi_job(a,J_WIFI_SCAN);return true;}
        if(id==A_WIFI_DISCONNECT){start_wifi_job(a,J_WIFI_DISCONNECT);return true;}
        if(id==A_WIFI_FORGET){start_wifi_job(a,J_WIFI_FORGET);return true;}
        if(id==A_WIFI_HIDDEN){if(wifi_usable(a))wifi_prompt(a,NULL);return true;}
        if(id==A_WIFI_PAGE_PREV){if(a->wifi_page)a->wifi_page--;a->dirty=true;return true;}
        if(id==A_WIFI_PAGE_NEXT){if(a->wifi_page+1<wifi_pages(a))a->wifi_page++;a->dirty=true;return true;}
        if(id>=A_WIFI_NET&&id<A_WIFI_NET+(int)WIFI_MAX){
            unsigned int n=(unsigned int)(id-A_WIFI_NET);if(n>=a->wifi_count)return true;
            const WifiNet*w=&a->wifi_nets[n];if(!w->supported||w->connected)return true;
            /* A remembered network joins at once with its kept key. */
            if(w->saved){copy(a->wifi_edit_ssid,sizeof(a->wifi_edit_ssid),w->ssid);copy(a->wifi_edit_hex,sizeof(a->wifi_edit_hex),w->hex);a->wifi_edit_fixed=true;
                         a->wifi_edit_saved=true;memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));start_wifi_job(a,J_WIFI_CONNECT);return true;}
            a->wifi_edit_saved=false;
            if(w->secure)wifi_prompt(a,w);
            else{copy(a->wifi_edit_ssid,sizeof(a->wifi_edit_ssid),w->ssid);copy(a->wifi_edit_hex,sizeof(a->wifi_edit_hex),w->hex);a->wifi_edit_fixed=true;
                 memset(a->wifi_edit_password,0,sizeof(a->wifi_edit_password));start_wifi_job(a,J_WIFI_CONNECT);}
            return true;
        }
    }
    if(a->screen==FILE_PROMPT&&(a->fs_prompt_kind==J_WIFI_SSID||a->fs_prompt_kind==J_WIFI_PASS)){
        if(id==A_BACK){wifi_forget_edit(a);screen(a,WIFI);return true;}
        if(id==A_WIFI_FIELD_SSID){if(!a->wifi_edit_fixed)wifi_focus(a,J_WIFI_SSID);return true;}
        if(id==A_WIFI_FIELD_PASS){wifi_focus(a,J_WIFI_PASS);return true;}
        if(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER){
            if(a->fs_prompt_kind==J_WIFI_SSID){if(a->fs_prompt[0]&&strlen(a->fs_prompt)<=32)wifi_focus(a,J_WIFI_PASS);return true;}
            if(!systems_wifi_password_ok(a->fs_prompt))return true;
            wifi_focus(a,J_WIFI_PASS);
            if(!a->wifi_edit_ssid[0]||strlen(a->wifi_edit_ssid)>32){wifi_focus(a,J_WIFI_SSID);return true;}
            memset(a->fs_prompt,0,sizeof(a->fs_prompt));a->prompt_cursor=0;a->fs_prompt_kind=0;screen(a,WIFI);
            start_wifi_job(a,J_WIFI_CONNECT);return true;
        }
    }
    return false;
}

/* Recovery may join a remembered network by itself at start (wifi.py auto, from init): the state
   file it writes keeps the page, the status bar and the home tile current. */
static void wifi_state_poll(App*a){
    if(!a->device||a->preview||a->now<a->wifi_state_at)return;
    a->wifi_state_at=a->now+2000;
    if(a->job.pid&&wifi_job(a->job.kind))return;
    struct stat st;if(stat(a->wifi_state_path,&st)<0||st.st_size<=0||st.st_size>2048)return;
    uint64_t stamp=(uint64_t)st.st_mtim.tv_sec*1000000000ull+(uint64_t)st.st_mtim.tv_nsec+(uint64_t)st.st_size;
    if(stamp==a->wifi_state_stamp)return;
    char text[2049];int fd=open(a->wifi_state_path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return;
    ssize_t n=read(fd,text,sizeof(text)-1);close(fd);if(n<=0)return;text[n]=0;
    JsonToken tokens[64];Json doc;if(json_parse(&doc,text,tokens,64)<=0)return;
    a->wifi_state_stamp=stamp;wifi_read_state(a,&doc);a->dirty=true;
}

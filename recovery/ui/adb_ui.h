/* ADB (2026-11-10): a computer's new key is shown here before adbd lets it in. adbd writes
   request-<id>.json into adb_dir and waits for allowed-<id> or denied-<id>; the phone must be
   unlocked to answer. The recovery password, when one is set, is still asked afterwards. */
static void adb_close(App*a){
    copy(a->adb_answered,sizeof(a->adb_answered),a->adb_id);a->adb_id[0]=0;
    if(a->screen==ADB_REQUEST)screen(a,a->adb_return);
    a->dirty=true;
}

static void adb_poll(App*a){
    if(a->now-a->adb_checked<400)return;
    a->adb_checked=a->now;
    char path[256];
    if(a->adb_id[0]){
        /* adbd gave up (timeout or disconnect): so does the page. */
        snprintf(path,sizeof(path),"%s/request-%s.json",adb_dir,a->adb_id);
        if(access(path,F_OK))adb_close(a);
        return;
    }
    if(a->locked||a->screen==SPLASH||a->screen==LOCKSCREEN||a->screen==ACCESS_REQUEST)return;
    DIR*d=opendir(adb_dir);if(!d)return;
    char found[96]={0};struct dirent*e;
    while((e=readdir(d))){
        size_t n=strlen(e->d_name);
        if(strncmp(e->d_name,"request-",8)||n<14||n>=sizeof(found)||strcmp(e->d_name+n-5,".json"))continue;
        if(a->adb_answered[0]&&!strncmp(e->d_name+8,a->adb_answered,strlen(a->adb_answered)))continue;
        copy(found,sizeof(found),e->d_name);break;
    }
    closedir(d);
    if(!found[0])return;
    snprintf(path,sizeof(path),"%s/%s",adb_dir,found);int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return;
    char data[512];ssize_t n=read(fd,data,sizeof(data)-1);close(fd);if(n<=0)return;data[n]=0;
    JsonToken t[32];Json j;if(json_parse(&j,data,t,32)<1||t[0].type!=J_OBJECT)return;
    jstring(&j,0,"id",a->adb_id,sizeof(a->adb_id));jstring(&j,0,"fingerprint",a->adb_fingerprint,sizeof(a->adb_fingerprint));
    jstring(&j,0,"comment",a->adb_comment,sizeof(a->adb_comment));jstring(&j,0,"peer",a->adb_peer,sizeof(a->adb_peer));
    /* Only an id of letters and digits names the answer file. */
    for(const char*c=a->adb_id;*c;c++)if(!isalnum((unsigned char)*c)){a->adb_id[0]=0;return;}
    if(!a->adb_id[0])return;
    a->adb_return=a->screen;a->last_activity=a->now;screen(a,ADB_REQUEST);
}

static void render_adb_request(App*a){
    heading(a,"USB debugging","A computer is asking to use ADB with this recovery.",false);
    rounded(a,(Rect){28,236,664,250},26,PANEL);icon_tile(a,ICON_USB,52,262,84,ICON_FILL);
    text(a,160,264,2,TEXT,"ADB connection");
    raw_text(true);ellipsis(a,160,326,500,1,MUTED,a->adb_comment[0]?a->adb_comment:a->adb_peer);raw_text(false);
    wrapped(a,52,394,616,0,MUTED,"It gets a root shell, copies files and can restart the phone.",2);
    rounded(a,(Rect){28,502,664,142},24,PANEL);text(a,52,518,0,ORANGE,"KEY FINGERPRINT");raw_text(true);wrapped(a,52,558,616,0,TEXT,a->adb_fingerprint,2);raw_text(false);
    int y=676;y+=text_tile(a,y,1,ORANGE,"Allow this computer only if you started this yourself.",3)+16;
    text_tile(a,y,0,MUTED,"An allowed computer is remembered. When the recovery has a password, it is asked as well.",3);
    button(a,(Rect){28,1190,664,90},"Deny",A_ADB_DENY,true,false,0);button(a,(Rect){28,1320,664,102},"Allow",A_ADB_ALLOW,true,true,0);
}

static bool adb_action(App*a,int id){
    if(a->screen!=ADB_REQUEST)return false;
    if(id==A_ADB_ALLOW||id==A_ADB_DENY||id==A_BACK){
        if(a->adb_id[0]){
            char path[256];snprintf(path,sizeof(path),"%s/%s-%s",adb_dir,id==A_ADB_ALLOW?"allowed":"denied",a->adb_id);
            int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);if(fd>=0)close(fd);
        }
        adb_close(a);
    }
    return true; /* Nothing else on this page. */
}

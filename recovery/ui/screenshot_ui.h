/* Screenshots as in TWRP (2026-11-09): Power + Volume down saves the screen as PNG.
   Since 2026-11-11 the two keys are taken in either order, and screenshot.py packs the picture with
   zlib and places it: A22xScreenshots on an SD card or a USB drive (mounted for the moment when it
   is not), else a22x-recovery/screenshots on cache, else RAM. Without that helper the interface
   writes the PNG itself with stored deflate blocks, exact but large, to a writable card or RAM. */

static uint32_t png_crc(uint32_t crc,const unsigned char*p,size_t n){
    static uint32_t table[256];static bool ready=false;
    if(!ready){for(uint32_t i=0;i<256;i++){uint32_t c=i;for(int k=0;k<8;k++)c=c&1?0xedb88320u^(c>>1):c>>1;table[i]=c;}ready=true;}
    crc=~crc;for(size_t i=0;i<n;i++)crc=table[(crc^p[i])&0xff]^(crc>>8);return ~crc;
}
static bool png_chunk(FILE*f,const char*type,const unsigned char*data,uint32_t n){
    unsigned char head[8]={(unsigned char)(n>>24),(unsigned char)(n>>16),(unsigned char)(n>>8),(unsigned char)n,(unsigned char)type[0],(unsigned char)type[1],(unsigned char)type[2],(unsigned char)type[3]};
    uint32_t crc=png_crc(0,head+4,4);if(n)crc=png_crc(crc,data,n);
    unsigned char tail[4]={(unsigned char)(crc>>24),(unsigned char)(crc>>16),(unsigned char)(crc>>8),(unsigned char)crc};
    return fwrite(head,1,8,f)==8&&(!n||fwrite(data,1,n,f)==n)&&fwrite(tail,1,4,f)==4;
}
/* RGB rows with filter 0, wrapped in a zlib stream of stored blocks. */
static bool write_png(const char*path,const uint32_t*frame,int width,int height){
    size_t row=(size_t)width*3+1,raw=row*(size_t)height,blocks=(raw+65534)/65535,zlen=2+raw+blocks*5+4;
    unsigned char*z=malloc(zlen),*line=malloc(row);if(!z||!line){free(z);free(line);return false;}
    size_t at=0,left=raw,in_block=0;uint32_t s1=1,s2=0;z[at++]=0x78;z[at++]=0x01;
    for(int y=0;y<height;y++){
        line[0]=0;for(int x=0;x<width;x++){uint32_t c=frame[(size_t)y*width+x];line[1+x*3]=(unsigned char)(c>>16);line[2+x*3]=(unsigned char)(c>>8);line[3+x*3]=(unsigned char)c;}
        for(size_t i=0;i<row;i++){
            if(!in_block){size_t n=left<65535?left:65535;in_block=n;left-=n;z[at++]=left?0:1;z[at++]=(unsigned char)n;z[at++]=(unsigned char)(n>>8);z[at++]=(unsigned char)~n;z[at++]=(unsigned char)(~n>>8);}
            z[at++]=line[i];in_block--;s1=(s1+line[i])%65521;s2=(s2+s1)%65521;
        }
    }
    uint32_t adler=(s2<<16)|s1;z[at++]=(unsigned char)(adler>>24);z[at++]=(unsigned char)(adler>>16);z[at++]=(unsigned char)(adler>>8);z[at++]=(unsigned char)adler;free(line);
    FILE*f=fopen(path,"wbx");if(!f){free(z);return false;}
    unsigned char ihdr[13]={(unsigned char)(width>>24),(unsigned char)(width>>16),(unsigned char)(width>>8),(unsigned char)width,(unsigned char)(height>>24),(unsigned char)(height>>16),(unsigned char)(height>>8),(unsigned char)height,8,2,0,0,0};
    bool ok=fwrite("\x89PNG\r\n\x1a\n",1,8,f)==8&&png_chunk(f,"IHDR",ihdr,13)&&png_chunk(f,"IDAT",z,(uint32_t)at)&&png_chunk(f,"IEND",NULL,0);
    free(z);ok=fflush(f)==0&&fsync(fileno(f))==0&&ok;return fclose(f)==0&&ok;
}
/* A mounted writable SD card or USB drive under /mnt/volumes, else private RAM. */
static bool screenshot_folder(char*out,size_t size,bool*removable){
    struct stat base;*removable=false;
    if(stat("/mnt/volumes",&base)==0){DIR*d=opendir("/mnt/volumes");struct dirent*e;
        while(d&&(e=readdir(d))){
            if(strncmp(e->d_name,"sdcard",6)&&strncmp(e->d_name,"usb",3))continue;
            char root[PATH_LEN];struct stat st;struct statvfs fs;snprintf(root,sizeof(root),"/mnt/volumes/%s",e->d_name);
            if(lstat(root,&st)||!S_ISDIR(st.st_mode)||st.st_dev==base.st_dev||statvfs(root,&fs)||(fs.f_flag&ST_RDONLY))continue;
            snprintf(out,size,"%s/A22xScreenshots",root);if(mkdir(out,0700)&&errno!=EEXIST)continue;*removable=true;closedir(d);return true;
        }
        if(d)closedir(d);
    }
    snprintf(out,size,"/run/a22x-recovery/screenshots");return !mkdir(out,0700)||errno==EEXIST;
}
/* The rows as PNG wants them before they are packed: a filter byte and RGB. Each row is written
   against the row above (Up) or the pixel before (Sub), whichever leaves the smaller numbers: flat
   areas and gradients then pack to almost nothing. */
static bool write_scanlines(const char*path,const uint32_t*frame,int width,int height){
    size_t row=(size_t)width*3;unsigned char*now=malloc(row),*above=calloc(row,1),*up=malloc(row+1),*sub=malloc(row+1);
    FILE*f=now&&above&&up&&sub?fopen(path,"wbx"):NULL;bool ok=f!=NULL;
    for(int y=0;ok&&y<height;y++){
        for(int x=0;x<width;x++){uint32_t c=frame[(size_t)y*width+x];now[x*3]=(unsigned char)(c>>16);now[x*3+1]=(unsigned char)(c>>8);now[x*3+2]=(unsigned char)c;}
        unsigned long weight_up=0,weight_sub=0;up[0]=2;sub[0]=1;
        for(size_t i=0;i<row;i++){
            unsigned char u=(unsigned char)(now[i]-above[i]),s=(unsigned char)(now[i]-(i>=3?now[i-3]:0));
            up[1+i]=u;sub[1+i]=s;weight_up+=u<128?u:256u-u;weight_sub+=s<128?s:256u-s;
        }
        ok=fwrite(weight_up<=weight_sub?up:sub,1,row+1,f)==row+1;memcpy(above,now,row);
    }
    if(f)ok=fclose(f)==0&&ok;
    free(now);free(above);free(up);free(sub);if(!ok&&f)unlink(path);
    return ok;
}
/* The helper is started beside whatever runs: it packs the picture and says where it put it. */
static bool screenshot_hand_over(App*a,const uint32_t*frame,int width,int height,const char*stamp){
    if(a->preview||!a->shot_helper[0]||access(a->shot_helper,R_OK)||!private_workdir(a->workdir))return false;
    snprintf(a->shot_raw,sizeof(a->shot_raw),"%s/shot-%ld-%u.raw",a->workdir,(long)getpid(),++a->shot_sequence);
    snprintf(a->shot_result,sizeof(a->shot_result),"%s.json",a->shot_raw);unlink(a->shot_raw);unlink(a->shot_result);
    if(!write_scanlines(a->shot_raw,frame,width,height))return false;
    char w[16],h[16],name[64];snprintf(w,sizeof(w),"%d",width);snprintf(h,sizeof(h),"%d",height);snprintf(name,sizeof(name),"recovery-%s.png",stamp);
    char*args[20];int n=0;args[n++]=a->python;args[n++]="-B";args[n++]=a->shot_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="save";args[n++]="--raw";args[n++]=a->shot_raw;args[n++]="--width";args[n++]=w;args[n++]="--height";args[n++]=h;
    args[n++]="--name";args[n++]=name;args[n++]="--result";args[n++]=a->shot_result;args[n]=NULL;
    pid_t pid=fork();if(pid<0){unlink(a->shot_raw);return false;}
    if(!pid){
        /* Like the settings: no storage lease and no input descriptor goes along; what it prints is not read. */
        int null=open("/dev/null",O_WRONLY);if(null>=0){dup2(null,STDOUT_FILENO);dup2(null,STDERR_FILENO);}
        (void)close_range(3,~0u,0);unsetenv("A22X_RECOVERY_LOCK_FD");execv(a->python,args);_exit(2);
    }
    a->shot_pid=pid;a->shot_deadline=a->now+SHOT_TIMEOUT_MS;return true;
}
/* The helper has ended: its word on where the picture is becomes the toast. */
static void screenshot_poll(App*a){
    if(a->shot_flash){if(a->now<a->shot_flash||a->now-a->shot_flash>=SHOT_FLASH_MS)a->shot_flash=0;a->dirty=true;}
    if(!a->shot_pid)return;
    int status=0;pid_t got=waitpid(a->shot_pid,&status,WNOHANG);
    if(!got){if(a->now<a->shot_deadline)return;kill(a->shot_pid,SIGKILL);(void)waitpid(a->shot_pid,&status,0);}
    a->shot_pid=0;unlink(a->shot_raw);
    char data[1024]={0},where[16]={0},label[96]={0},note[240];
    int fd=open(a->shot_result,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);ssize_t n=fd>=0?read(fd,data,sizeof(data)-1):-1;if(fd>=0)close(fd);
    unlink(a->shot_result);
    JsonToken t[48];Json j;
    if(n>0&&json_parse(&j,data,t,48)>0&&t[0].type==J_OBJECT&&json_bool(&j,json_get(&j,0,"ok"),0)==1){jstring(&j,0,"where",where,sizeof(where));jstring(&j,0,"label",label,sizeof(label));}
    fprintf(stderr,"[recovery-ui:screenshot] helper where=%s\n",where[0]?where:"none");
    if(!strcmp(where,"card")){snprintf(note,sizeof(note),T("Screenshot saved to %s, in A22xScreenshots"),label);toast(a,note);}
    else if(!strcmp(where,"cache"))toast(a,"Screenshot saved on cache, in a22x-recovery/screenshots");
    else if(!strcmp(where,"ram"))toast(a,"Screenshot saved in RAM until restart: no card or cache could take it");
    else toast(a,"Screenshot not saved");
    a->toast_icon=ICON_DISPLAY;
}
/* Whatever needs storage next waits for a screenshot that is still being placed (a second at most). */
static void screenshot_wait(App*a){
    for(int n=0;a->shot_pid&&n<400;n++){a->now=milliseconds();screenshot_poll(a);if(a->shot_pid){struct timespec pause={0,10000000};nanosleep(&pause,NULL);}}
}
static void take_screenshot(App*a){
    if(a->shot_pid){toast(a,"The last screenshot is still being saved");return;}
    int width=1080,height=2408;uint32_t*frame=malloc((size_t)width*height*sizeof(*frame));
    char folder[PATH_LEN],path[PATH_LEN+64],stamp[32];bool removable=false;time_t now=time(NULL);struct tm tm;
    localtime_r(&now,&tm);strftime(stamp,sizeof(stamp),"%Y%m%d-%H%M%S",&tm);
    if(frame&&compose_native(a,frame,width,height,0,4,width,2400)){
        a->shot_flash=a->now?a->now:1;a->dirty=true;
        if(screenshot_hand_over(a,frame,width,height,stamp)){free(frame);return;}
    }
    bool ok=frame&&compose_native(a,frame,width,height,0,4,width,2400)&&screenshot_folder(folder,sizeof(folder),&removable);
    if(ok){
        for(int n=0;n<100;n++){snprintf(path,sizeof(path),n?"%s/recovery-%s-%d.png":"%s/recovery-%s.png",folder,stamp,n);if(access(path,F_OK))break;}
        ok=write_png(path,frame,width,height);}
    free(frame);
    fprintf(stderr,"[recovery-ui:screenshot] ok=%d removable=%d\n",ok,removable);
    toast(a,!ok?"Screenshot not saved":(removable?"Screenshot saved to A22xScreenshots":"Screenshot saved in RAM: no writable SD or USB"));
}

/* Screenshots as in TWRP (2026-11-09): Power + Volume down saves the screen as PNG to
   A22xScreenshots on a mounted writable SD card or USB drive, else to RAM. The PNG uses
   stored deflate blocks: no compression library in the static UI, and the file stays exact. */

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
static void take_screenshot(App*a){
    int width=1080,height=2408;uint32_t*frame=malloc((size_t)width*height*sizeof(*frame));
    char folder[PATH_LEN],path[PATH_LEN+64],stamp[32];bool removable=false;time_t now=time(NULL);struct tm tm;
    bool ok=frame&&compose_native(a,frame,width,height,0,4,width,2400)&&screenshot_folder(folder,sizeof(folder),&removable);
    if(ok){localtime_r(&now,&tm);strftime(stamp,sizeof(stamp),"%Y%m%d-%H%M%S",&tm);
        for(int n=0;n<100;n++){snprintf(path,sizeof(path),n?"%s/recovery-%s-%d.png":"%s/recovery-%s.png",folder,stamp,n);if(access(path,F_OK))break;}
        ok=write_png(path,frame,width,height);}
    free(frame);
    fprintf(stderr,"[recovery-ui:screenshot] ok=%d removable=%d\n",ok,removable);
    toast(a,!ok?"Screenshot not saved":(removable?"Screenshot saved to A22xScreenshots":"Screenshot saved in RAM: no writable SD or USB"));
}

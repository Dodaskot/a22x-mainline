/* SPDX-License-Identifier: MIT
 * Static replacement for opt/a22x-conn/wmtload.py and wmtlaunch.py so that a
 * target distribution needs no Python for MT6833 CONSYS bring-up. Ioctl
 * numbers, arguments and the launcher command replies match the Python tools.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <unistd.h>
#define W_IOR(n) ((2u<<30)|(4u<<16)|((unsigned)'w'<<8)|(n))
#define W_IOW(n) ((1u<<30)|(4u<<16)|((unsigned)'w'<<8)|(n))
#define L_IOW(n,s) ((1u<<30)|((unsigned)(s)<<16)|(0xa0u<<8)|(n))
#define L_IOWR(n,s) ((3u<<30)|((unsigned)(s)<<16)|(0xa0u<<8)|(n))
static int load(int argc,char**argv){
    int fd=open("/dev/wmtdetect",O_RDWR|O_CLOEXEC);long chip=0,r;
    if(fd<0){perror("/dev/wmtdetect");return 1;}
    for(int i=0;i<argc;i++){const char*s=argv[i];
        if(!strcmp(s,"soc")){chip=ioctl(fd,W_IOR(3),0);if(chip<0)goto bad;printf("soc chip id 0x%lx\n",chip);}
        else if(!strcmp(s,"adie")){r=ioctl(fd,W_IOR(9),0);if(r<0)goto bad;printf("adie chip id 0x%lx\n",r);}
        else if(!strcmp(s,"set")){r=ioctl(fd,W_IOW(1),chip);if(r<0)goto bad;printf("set chip %ld\n",r);}
        else if(!strcmp(s,"hwinit")){r=ioctl(fd,W_IOR(10),0);if(r<0)goto bad;printf("soc hw init %ld\n",r);}
        else if(!strcmp(s,"init")){r=ioctl(fd,W_IOR(4),chip);if(r<0)goto bad;printf("module init %ld\n",r);}
        else{fprintf(stderr,"unknown step %s\n",s);return 2;}
        continue;
bad:    fprintf(stderr,"ioctl for %s: %s\n",s,strerror(errno));return 1;
    }
    return 0;
}
static int rom_patch_info(int fd,const char*prefix){
    static const char*const kinds[]={"mcu","bt","wifi"};
    for(int i=0;i<3;i++){
        char name[256],path[320];unsigned char hdr[32];unsigned char buf[264];
        snprintf(name,sizeof(name),"soc2_2_ram_%s_%s_hdr.bin",kinds[i],prefix);
        snprintf(path,sizeof(path),"/lib/firmware/%s",name);
        int f=open(path,O_RDONLY|O_CLOEXEC);if(f<0)return -1;
        ssize_t n=read(f,hdr,sizeof(hdr));close(f);if(n!=(ssize_t)sizeof(hdr))return -1;
        const unsigned char*info=hdr+0x18;uint32_t type=info[7];
        memset(buf,0,sizeof(buf));memcpy(buf,&type,4);buf[4]=0;memcpy(buf+5,info+1,3);
        memcpy(buf+8,name,strlen(name));
        if(ioctl(fd,L_IOW(31,8),buf)<0)return -1;
        printf("[launcher] rom patch type %u %s\n",type,name);fflush(stdout);
    }
    return 0;
}
static int serve(void){
    const char*prefix=getenv("WMT_PATCH_SET");if(!prefix)prefix="1_1";
    int fd=open("/dev/stpwmt",O_RDWR|O_NOCTTY|O_CLOEXEC);
    if(fd<0){perror("/dev/stpwmt");return 1;}
    unsigned char cfg[256]={0};memcpy(cfg,"WMT_SOC.cfg",11);
    if(ioctl(fd,L_IOWR(21,8),cfg)<0||ioctl(fd,L_IOW(5,4),0x23)<0){perror("wmt setup");return 1;}
    puts("[launcher] cfg + stp mode set");fflush(stdout);
    for(;;){
        fd_set set;FD_ZERO(&set);FD_SET(fd,&set);struct timeval tv={1,0};
        int ready=select(fd+1,&set,NULL,NULL,&tv);
        if(ready<0){if(errno==EINTR)continue;perror("select");return 1;}
        if(!ready)continue;
        char cmd[257];ssize_t n=read(fd,cmd,256);
        if(n<0){if(errno==EINTR||errno==EAGAIN)continue;perror("read");return 1;}
        cmd[n]=0;if(!cmd[0]){usleep(100000);continue;}
        printf("[launcher] cmd %s\n",cmd);fflush(stdout);
        const char*reply="ok";
        if(!strcmp(cmd,"srh_rom_patch")){if(rom_patch_info(fd,prefix)<0){perror("rom patch");reply="fail";}}
        else if(!strcmp(cmd,"srh_patch"))reply="fail";
        if(write(fd,reply,strlen(reply))<0)perror("reply");
    }
}
int main(int argc,char**argv){
    if(argc>=2&&!strcmp(argv[1],"serve"))return serve();
    if(argc>=3&&!strcmp(argv[1],"load"))return load(argc-2,argv+2);
    fprintf(stderr,"usage: a22x-wmt serve | a22x-wmt load STEP...\n");return 2;
}

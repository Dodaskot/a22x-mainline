/* SPDX-License-Identifier: MIT
 * Themes that arrive as files. themes.py has checked and compiled them, so all this has
 * to prove is that a file is exactly as long as its header says and its strings end.
 * Bundled themes come with the image; imported ones wait in RAM, copied from storage at start.
 */
#ifndef A22X_THEME_FILES_H
#define A22X_THEME_FILES_H

#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "theme.h"

#define THEME_FILE_MAX 12
#define THEME_FLAG_UI 1u
#define THEME_FLAG_TERMINAL 2u
#define THEME_FLAG_BENTO 4u
#define THEME_FLAG_LIGHT 8u
#define THEME_THUMB_W 117u
#define THEME_THUMB_H 261u
#define THEME_IMAGE_W 540u
#define THEME_IMAGE_H 1204u

typedef struct {
    char magic[8];uint32_t flags;
    uint32_t colors[25],tile_fill[6],tile_ink[6],terminal_bg,terminal_fg,palette[16];
    uint32_t surface_alpha,edge_alpha,shadow_strength,preview_top,preview_bottom;
    uint32_t image_w,image_h,thumb_w,thumb_h;
    char code[24],name[48],note[80];
} ThemeFileHeader;
_Static_assert(sizeof(ThemeFileHeader)==420,"the layout is shared with HEADER in themes.py");

typedef struct {
    ThemeFileHeader header;Theme theme;TerminalScheme scheme;uint8_t *thumb;char path[192];
} ThemeFile;
static ThemeFile theme_files[THEME_FILE_MAX];
static unsigned int theme_file_count;
static bool theme_files_hidden;
static const char *theme_folders[2]={"/opt/a22x-recovery/themes","/run/a22x-recovery/themes"};

static inline bool theme_file_ui(const ThemeFile*f){return (f->header.flags&THEME_FLAG_UI)!=0;}
static inline bool theme_file_terminal(const ThemeFile*f){return (f->header.flags&THEME_FLAG_TERMINAL)!=0;}
static bool theme_text_ends(const char*s,size_t size){return memchr(s,0,size)!=NULL;}

static void theme_file_fill(ThemeFile*f){
    const ThemeFileHeader*h=&f->header;const uint32_t*c=h->colors;
    f->theme=(Theme){.code=h->code,.name=h->name,.bg=c[0],.panel=c[1],.panel2=c[2],.line=c[3],.edge=c[4],.text=c[5],.muted=c[6],.accent=c[7],.container=c[8],.container_edge=c[9],
        .on_accent=c[10],.icon_fill=c[11],.knob=c[12],.green=c[13],.red=c[14],.blue=c[15],.purple=c[16],.danger=c[17],.toast_fill=c[18],.toast_text=c[19],.shadow=c[20],
        .hero_top=c[21],.hero_bottom=c[22],.hero_text=c[23],.hero_sub=c[24],.terminal_bg=h->terminal_bg,.terminal_fg=h->terminal_fg,
        .terminal_palette=(h->flags&THEME_FLAG_TERMINAL)?h->palette:((h->flags&THEME_FLAG_LIGHT)?terminal_light_palette:NULL),
        .surface_alpha=h->surface_alpha,.edge_alpha=h->edge_alpha,.shadow_strength=h->shadow_strength,.bento=(h->flags&THEME_FLAG_BENTO)!=0};
    memcpy(f->theme.tile_fill,h->tile_fill,sizeof(h->tile_fill));memcpy(f->theme.tile_ink,h->tile_ink,sizeof(h->tile_ink));
    f->scheme=(TerminalScheme){.code=h->code,.name=h->name,.bg=h->terminal_bg,.fg=h->terminal_fg};memcpy(f->scheme.palette,h->palette,sizeof(h->palette));
}

/* Read one header and its thumbnail. A later folder replaces a theme of the same name. */
static void theme_file_add(const char*path){
    ThemeFileHeader h;struct stat st;int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return;
    bool good=fstat(fd,&st)==0&&S_ISREG(st.st_mode)&&read(fd,&h,sizeof(h))==(ssize_t)sizeof(h)&&!memcmp(h.magic,"A22XTHM1",8)&&(h.flags&(THEME_FLAG_UI|THEME_FLAG_TERMINAL))
        &&theme_text_ends(h.code,sizeof(h.code))&&theme_text_ends(h.name,sizeof(h.name))&&theme_text_ends(h.note,sizeof(h.note))&&h.code[0]
        &&h.image_w<=THEME_IMAGE_W&&h.image_h<=THEME_IMAGE_H&&(h.image_w==0)==(h.image_h==0)
        &&((h.thumb_w==THEME_THUMB_W&&h.thumb_h==THEME_THUMB_H)||(h.thumb_w==0&&h.thumb_h==0));
    size_t thumb=(size_t)h.thumb_w*h.thumb_h*3,image=(size_t)h.image_w*h.image_h*3;
    if(good)good=(uint64_t)st.st_size==sizeof(h)+thumb+image;
    uint8_t*pixels=NULL;
    if(good&&thumb){pixels=malloc(thumb);good=pixels&&read(fd,pixels,thumb)==(ssize_t)thumb;}
    close(fd);
    if(!good){free(pixels);return;}
    unsigned int slot=theme_file_count;
    for(unsigned int n=0;n<theme_file_count;n++)if(!strcmp(theme_files[n].header.code,h.code))slot=n;
    if(slot==THEME_FILE_MAX){free(pixels);return;}
    ThemeFile*f=&theme_files[slot];free(f->thumb);memset(f,0,sizeof(*f));
    f->header=h;f->thumb=pixels;snprintf(f->path,sizeof(f->path),"%s",path);theme_file_fill(f);
    if(slot==theme_file_count)theme_file_count++;
}

static bool theme_file_suffix(const char*name,const char*suffix){size_t a=strlen(name),b=strlen(suffix);return a>b&&!strcmp(name+a-b,suffix);}
static int theme_file_order(const struct dirent**one,const struct dirent**two){return strcmp((*one)->d_name,(*two)->d_name);}

static void theme_files_scan(void){
    for(unsigned int n=0;n<theme_file_count;n++)free(theme_files[n].thumb);
    memset(theme_files,0,sizeof(theme_files));theme_file_count=0;theme_files_hidden=false;
    for(unsigned int folder=0;folder<2;folder++){
        struct dirent**names=NULL;int count=scandir(theme_folders[folder],&names,NULL,theme_file_order);
        for(int n=0;n<count;n++){
            const char*name=names[n]->d_name;char path[192],note[192];
            if(theme_file_suffix(name,".hidden"))theme_files_hidden=true;
            if(theme_file_suffix(name,".theme")&&snprintf(path,sizeof(path),"%s/%s",theme_folders[folder],name)<(int)sizeof(path)){
                /* A theme of the image that was removed stays away until it is asked back. */
                int stem=(int)strlen(name)-6;bool hidden=folder==0&&snprintf(note,sizeof(note),"%s/%.*s.hidden",theme_folders[1],stem,name)<(int)sizeof(note)&&!access(note,F_OK);
                if(!hidden)theme_file_add(path);
            }
            free(names[n]);
        }
        free(names);
    }
}

/* The picture of one theme, width and height in its header. The caller frees it. */
static uint8_t*theme_file_image(const ThemeFile*f){
    size_t thumb=(size_t)f->header.thumb_w*f->header.thumb_h*3,size=(size_t)f->header.image_w*f->header.image_h*3;if(!size)return NULL;
    int fd=open(f->path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return NULL;
    uint8_t*pixels=malloc(size);
    if(pixels&&pread(fd,pixels,size,(off_t)(sizeof(ThemeFileHeader)+thumb))!=(ssize_t)size){free(pixels);pixels=NULL;}
    close(fd);return pixels;
}
#endif

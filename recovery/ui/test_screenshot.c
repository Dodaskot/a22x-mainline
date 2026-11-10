/* SPDX-License-Identifier: MIT — the screenshot PNG writer: a frame larger than one stored
   block, decoded by an independent reader in tools/test_screenshot.py. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
int main(int argc,char**argv){
    if(argc!=2)return 2;
    int w=301,h=257;uint32_t*f=malloc((size_t)w*h*4);if(!f)return 2;
    for(int y=0;y<h;y++)for(int x=0;x<w;x++)f[(size_t)y*w+x]=((uint32_t)(x*7&255)<<16)|((uint32_t)(y*5&255)<<8)|(uint32_t)((x^y)&255);
    unlink(argv[1]);bool ok=write_png(argv[1],f,w,h);bool again=write_png(argv[1],f,w,h);free(f);
    printf("{\"written\":%s,\"no_overwrite\":%s}\n",ok?"true":"false",again?"false":"true");return ok&&!again?0:1;
}

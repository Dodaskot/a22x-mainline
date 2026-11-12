/* SPDX-License-Identifier: MIT — the screenshot writers, decoded by an independent reader in
   tools/test_screenshot.py: the exact PNG of stored blocks (a frame larger than one block), the
   filtered rows handed to screenshot.py, and a whole screenshot taken through that helper. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
int main(int argc,char**argv){
    if(argc!=2&&argc!=7)return 2;
    int w=301,h=257;uint32_t*f=malloc((size_t)w*h*4);if(!f)return 2;
    for(int y=0;y<h;y++)for(int x=0;x<w;x++)f[(size_t)y*w+x]=((uint32_t)(x*7&255)<<16)|((uint32_t)(y*5&255)<<8)|(uint32_t)((x^y)&255);
    unlink(argv[1]);bool ok=write_png(argv[1],f,w,h);bool again=write_png(argv[1],f,w,h);
    if(argc==2){free(f);printf("{\"written\":%s,\"no_overwrite\":%s}\n",ok?"true":"false",again?"false":"true");return ok&&!again?0:1;}
    /* argv: exact.png rows.raw fixture helper python page.png */
    unlink(argv[2]);bool rows=write_scanlines(argv[2],f,w,h),rows_again=write_scanlines(argv[2],f,w,h);free(f);
    App*a=calloc(1,sizeof(*a));if(!a)return 2;
    initialize(a);a->inventory_ok=true;a->now=milliseconds();a->screen=HOME;render(a);
    copy(a->fixture,sizeof(a->fixture),argv[3]);copy(a->shot_helper,sizeof(a->shot_helper),argv[4]);copy(a->python,sizeof(a->python),argv[5]);
    snprintf(a->workdir,sizeof(a->workdir),"%s/work",argv[3]);
    /* The page as the old exact writer saves it, to compare the helper's picture with. */
    int width=1080,height=2408;uint32_t*frame=malloc((size_t)width*height*sizeof(*frame));if(!frame)return 2;
    bool exact=compose_native(a,frame,width,height,0,4,width,2400)&&(unlink(argv[6]),write_png(argv[6],frame,width,height));free(frame);
    take_screenshot(a);bool started=a->shot_pid>0&&a->shot_flash;
    take_screenshot(a);bool refused=strstr(a->toast,"still being saved")!=NULL; /* one at a time */
    screenshot_wait(a);bool ended=!a->shot_pid&&access(a->shot_raw,F_OK)!=0&&access(a->shot_result,F_OK)!=0;
    printf("{\"written\":%s,\"no_overwrite\":%s,\"rows\":%s,\"rows_no_overwrite\":%s,\"exact\":%s,\"started\":%s,\"second_refused\":%s,\"ended\":%s,\"toast\":\"%s\"}\n",
           ok?"true":"false",again?"false":"true",rows?"true":"false",rows_again?"false":"true",exact?"true":"false",started?"true":"false",refused?"true":"false",ended?"true":"false",a->toast);
    free(a->pixels);free(a);
    return ok&&!again&&rows&&!rows_again&&exact&&started&&refused&&ended?0:1;
}

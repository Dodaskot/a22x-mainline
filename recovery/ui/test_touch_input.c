/* SPDX-License-Identifier: MIT — actual evdev decoder through owned pipes only. */
#define main recovery_ui_program_main
#ifndef UI_SOURCE
#define UI_SOURCE "recovery_ui.c"
#endif
#include UI_SOURCE
#undef main

static unsigned int checks;
#define TCHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"touch-input check %u line %d failed: %s\n",checks,__LINE__,#expr);goto done;}}while(0)
static bool feed_input(int writer,Input*i,App*a,const Display*d,unsigned type,unsigned code,int value,uint64_t now){
    struct input_event event={.time={.tv_sec=(time_t)(now/1000),.tv_usec=(suseconds_t)((now%1000)*1000)},.type=(uint16_t)type,.code=(uint16_t)code,.value=value};
    if(write(writer,&event,sizeof(event))!=(ssize_t)sizeof(event))return false;
    handle_input(a,i,d);return true;
}
static bool stopping_contact(const char*name,enum ScrollSurface surface,bool mt,bool with_btn){
    bool ok=false;int fds[2]={-1,-1};App*a=calloc(1,sizeof(*a));if(!a)return false;initialize(a);
    TCHECK(preview(a,name));a->profile_render=true;
    apply_scroll(a,surface,500);render(a);
    enum Screen screen_before=a->screen;int volume_before=a->volume_index;size_t cursor_before=a->fs_cursor;bool dirty_before=a->fs_dirty;
    TCHECK(!a->job.pid&&kinetic_begin(&a->kinetic,(double)scroll_offset(a,surface),2200,(double)scroll_limit(a,surface),1000));
    a->motion_surface=surface;tick_inertia(a,1040);render(a);int64_t offset=scroll_offset(a,surface);
    TCHECK(offset>500&&a->kinetic.active);
    TCHECK(pipe2(fds,O_NONBLOCK|O_CLOEXEC)==0);
    Input input={.fd=fds[0],.clock_monotonic=true,.mt=mt,.xmin=0,.xmax=CW-1,.ymin=0,.ymax=CH-1,.primary_id=-1};
    for(int n=0;n<MAX_SLOTS;n++)input.slots[n].id=-1;
    Display display={.vw=CW,.vh=CH};display.v.xres=CW;display.v.yres=CH;
    if(with_btn){
        TCHECK(feed_input(fds[1],&input,a,&display,EV_KEY,BTN_TOUCH,1,1100));
        TCHECK(a->kinetic.active&&!a->pointer_down&&a->motion_surface==surface);
        TCHECK(input.slots[0].id==(mt?-1:0));
    }
    if(mt){
        TCHECK(feed_input(fds[1],&input,a,&display,EV_ABS,ABS_MT_SLOT,0,1100));
        TCHECK(feed_input(fds[1],&input,a,&display,EV_ABS,ABS_MT_TRACKING_ID,77,1100));
    }
    int x=surface==SCROLL_TEXT?120:250,y=surface==SCROLL_TEXT?450:650;
    TCHECK(feed_input(fds[1],&input,a,&display,EV_ABS,mt?ABS_MT_POSITION_X:ABS_X,x,1100));
    TCHECK(feed_input(fds[1],&input,a,&display,EV_ABS,mt?ABS_MT_POSITION_Y:ABS_Y,y,1100));
    TCHECK(feed_input(fds[1],&input,a,&display,EV_SYN,SYN_REPORT,0,1100));
    TCHECK(a->pointer_down&&!a->kinetic.active&&a->scroll_stop_tap&&!a->pressed_action&&scroll_offset(a,surface)==offset);
    if(with_btn)TCHECK(feed_input(fds[1],&input,a,&display,EV_KEY,BTN_TOUCH,0,1150));
    if(mt)TCHECK(feed_input(fds[1],&input,a,&display,EV_ABS,ABS_MT_TRACKING_ID,-1,1150));
    TCHECK(feed_input(fds[1],&input,a,&display,EV_SYN,SYN_REPORT,0,1150));
    TCHECK(!a->pointer_down&&!input.down&&!a->kinetic.active&&!a->pressed_action&&!a->scroll_stop_tap);
    TCHECK(a->screen==screen_before&&a->volume_index==volume_before&&a->fs_cursor==cursor_before&&a->fs_dirty==dirty_before&&!a->job.pid&&scroll_offset(a,surface)==offset);
    ok=true;
done:
    for(int n=0;n<2;n++)if(fds[n]>=0)close(fds[n]);
    free(a->pixels);free(a);return ok;
}
static bool navigation_key(void){
    bool ok=false;int fds[2]={-1,-1};App*a=calloc(1,sizeof(*a));if(!a)return false;initialize(a);
    TCHECK(preview(a,"volume-scroll"));render(a);a->profile_render=true;
    TCHECK(kinetic_begin(&a->kinetic,500,2200,(double)volume_max_scroll(a),2000));a->motion_surface=SCROLL_VOLUMES;
    TCHECK(pipe2(fds,O_NONBLOCK|O_CLOEXEC)==0);Input input={.fd=fds[0],.clock_monotonic=true};Display display={0};
    TCHECK(feed_input(fds[1],&input,a,&display,EV_KEY,KEY_VOLUMEDOWN,1,2100));
    TCHECK(!a->kinetic.active&&a->motion_surface==SCROLL_NONE&&a->focus>=0&&!a->pointer_down&&!a->job.pid);
    ok=true;
done:
    for(int n=0;n<2;n++)if(fds[n]>=0)close(fds[n]);
    free(a->pixels);free(a);return ok;
}
int main(void){
    if(!stopping_contact("volume-scroll",SCROLL_VOLUMES,true,true)||
       !stopping_contact("file-scroll",SCROLL_FOLDER,true,true)||
       !stopping_contact("editor-scroll",SCROLL_TEXT,true,true)||
       !stopping_contact("volume-scroll",SCROLL_VOLUMES,false,true)||
       !stopping_contact("volume-scroll",SCROLL_VOLUMES,true,false)||!navigation_key())return 1;
    printf("{\"ok\":true,\"workflows\":6,\"checks\":%u,\"actual_evdev_decoder\":true,\"physical_device_io\":false}\n",checks);return 0;
}

/* SPDX-License-Identifier: MIT
 * The sheet that asks where a file is (2026-11-11, the owner's request: "From a file" did nothing
 * while userdata was not mounted, and a card had to be mounted on another page before). It lists
 * the SD card and the USB drives; a tap mounts the one chosen read-only and opens its folders.
 */
#ifndef A22X_SOURCE_UI_H
#define A22X_SOURCE_UI_H

#define SOURCE_ROWS 4
static void select_volume(App*a,int index);
/* What the file is for decides what is offered: a Linux image comes from a card; a ROM or a ZIP
   may also wait on cache (pushed over adb) or on a Linux userdata. */
static bool source_listed(const App*a,const FileVolume*v){
    if(!v||!v->token[0]||!(v->mounted||v->mountable))return false;
    bool card=!strncmp(v->id,"sdcard",6)||!strncmp(v->id,"usb-",4);
    if(a->systems_pick_manifest)return card;
    return card||!strcmp(v->id,"cache")||!strcmp(v->id,"userdata");
}
/* The choices of a file that begin with the sheet: a system image, and a ROM. */
static bool source_pick(const App*a){return (a->systems_pick_manifest&&!a->systems_pick_script)||a->rom_pick;}
static void source_open(App*a){
    a->source_sheet=true;a->source_known=false;a->volume_scroll=0;a->dirty=true;
    if(!start_files_job(a,J_FS_INVENTORY))a->source_known=true;
}
/* Back from the top folder of a card chosen in the sheet: the page under it, and the sheet again. */
static bool source_back(App*a){
    if(!source_pick(a))return false;
    screen(a,came_from_picker(a,a->rom_pick?ANDROID_MENU:SYSTEMS_NEW));source_open(a);return true;
}
static void render_source_sheet(App*a){
    a->button_count=0;veil(a,(Rect){0,0,CW,CH},0,0x000000u,110);
    int listed[SOURCE_ROWS],count=0;for(int i=0;i<a->volume_count&&count<SOURCE_ROWS;i++)if(source_listed(a,&a->volumes[i]))listed[count++]=i;
    bool busy=a->job.pid&&a->job.kind==J_FS_INVENTORY,free_ui=ui_free(a);
    /* The line under the title takes two lines in the longer languages. */
    const char*hint="An SD card or a USB drive. A tap mounts it read-only.";int lines=wrapped_height(a,600,0,hint,2);
    int height=98+lines+(count?count*132:176)+112;Rect sheet={28,1424-height,664,height};
    rounded(a,sheet,40,BG);rounded(a,sheet,40,PANEL);
    text(a,60,sheet.y+30,1,TEXT,"Where is the file?");
    wrapped(a,60,sheet.y+80,600,0,MUTED,hint,2);
    int y=sheet.y+94+lines;
    for(int n=0;n<count;n++,y+=132){
        FileVolume*v=&a->volumes[listed[n]];Rect r={48,y,624,120};surface(a,r,24,PANEL2,A_SOURCE_ITEM+listed[n]);volume_icon(a,v,r.x+20,y+38);
        raw_text(true);ellipsis(a,r.x+88,y+12,470,1,TEXT,v->label[0]?v->label:v->id);raw_text(false);
        char line[200],size[48];human_bytes(v->bytes,size,sizeof(size));
        const char*what=!strncmp(v->id,"usb-",4)?"USB drive":(!strncmp(v->id,"sdcard",6)?"SD card":"Phone memory");
        snprintf(line,sizeof(line),"%s  /  %.24s  /  %s",tr(what),v->fs_type[0]?v->fs_type:tr("raw"),v->bytes?size:tr("size unknown"));
        raw_text(true);ellipsis(a,r.x+88,y+54,470,0,MUTED,line);raw_text(false);
        ellipsis(a,r.x+88,y+84,470,0,v->mounted?GREEN:ORANGE,v->mounted?"Mounted: tap to open":"Not mounted: tap to mount and open");
        icon(a,ICON_CHEVRON,r.x+r.w-44,y+44,30,MUTED);register_button(a,r,A_SOURCE_ITEM+listed[n],free_ui,0);
    }
    if(!count){
        if(!a->source_known||busy)wrapped(a,60,y+40,600,1,MUTED,"Looking for cards...",2);
        else{text(a,60,y+16,1,TEXT,"No SD card or USB drive");wrapped(a,60,y+66,600,0,MUTED,"Insert an SD card or attach a USB drive, then read again.",3);}
    }
    int bottom=sheet.y+height-100;
    button(a,(Rect){60,bottom,290,76},"Read again",A_SOURCE_REFRESH,free_ui&&!busy,false,0);
    button(a,(Rect){370,bottom,290,76},"Cancel",A_SOURCE_CANCEL,true,false,0);
    register_button(a,(Rect){0,0,CW,CH},A_SOURCE_CANCEL,true,0);
}
static bool source_action(App*a,int id){
    if(!a->source_sheet)return false;
    if(id==A_SOURCE_REFRESH){if(ui_free(a)&&!a->job.pid){a->source_known=false;if(!start_files_job(a,J_FS_INVENTORY))a->source_known=true;}a->dirty=true;return true;}
    if(id>=A_SOURCE_ITEM&&id<A_SOURCE_ITEM+a->volume_count){
        int index=id-A_SOURCE_ITEM;FileVolume*v=&a->volumes[index];
        if(!source_listed(a,v)||a->job.pid)return true;
        a->source_sheet=false;
        if(v->mounted){select_volume(a,index);return true;}
        /* Read-only is enough to read a file; the folders open once the card is mounted. */
        a->volume_index=index;copy(a->volume_id,sizeof(a->volume_id),v->id);copy(a->fs_path,sizeof(a->fs_path),"/");reset_browser(a);
        FileRequest q=file_request(a,J_FS_MOUNT);copy(q.mode,sizeof(q.mode),"ro");start_files_request(a,&q);
        return true;
    }
    /* Cancel, a tap beside the sheet, Back: the choice of a file is given up. */
    a->source_sheet=false;a->systems_pick_manifest=a->systems_pick_script=false;a->flash_pick=a->rom_pick=false;a->dirty=true;
    return true;
}
#endif

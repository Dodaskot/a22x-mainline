/* SPDX-License-Identifier: MIT
 * Why Android did not start (2026-11-12). On the owner's phone Google's Android 15 image restarted
 * for ever; the reason stood in the memory its kernel's log outlives a restart in ("NetBpfLoad:
 * Android V requires kernel 4.19", then init's reboot,bpfloader-failed), and nobody could read it
 * without a serial cable. init reads that log once at the start (android_log.py) and leaves its
 * judgement in RAM; this page opens for it once from the home page, for each failed start once:
 * the id of the one the owner has read about is kept with the settings.
 */
#ifndef A22X_ANDROIDLOG_UI_H
#define A22X_ANDROIDLOG_UI_H

static void android_log_poll(App*a){
    if(a->alog_checked||a->preview)return;
    if(a->screen!=HOME||a->locked||a->job.pid||a->pointer_down)return;
    const char*path=getenv("A22X_RECOVERY_ANDROID_LOG");if(!path||!*path)path="/run/a22x-recovery/android-last.json";
    if(!a->alog_since)a->alog_since=a->now?a->now:1;
    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    /* init reads the log in the background: the answer may come a few seconds after the home page. */
    if(fd<0){if(a->now-a->alog_since>20000)a->alog_checked=true;return;}
    a->alog_checked=true;
    char data[4096];ssize_t n=read(fd,data,sizeof(data)-1);close(fd);if(n<=0)return;
    data[n]=0;JsonToken t[96];Json j;if(json_parse(&j,data,t,96)<1||t[0].type!=J_OBJECT||json_bool(&j,json_get(&j,0,"found"),0)!=1)return;
    jstring(&j,0,"code",a->alog_code,sizeof(a->alog_code));jstring(&j,0,"id",a->alog_id,sizeof(a->alog_id));
    if(strcmp(a->alog_code,"KERNEL_TOO_OLD")&&strcmp(a->alog_code,"INIT_GAVE_UP")&&strcmp(a->alog_code,"KERNEL_PANIC")){a->alog_code[0]=0;return;}
    jstring(&j,0,"needs",a->alog_needs,sizeof(a->alog_needs));jstring(&j,0,"reason",a->alog_reason,sizeof(a->alog_reason));
    jstring(&j,0,"detail",a->alog_detail,sizeof(a->alog_detail));
    int seconds=json_get(&j,0,"seconds");a->alog_seconds=seconds>=0?(unsigned int)(strtod(j.text+j.tokens[seconds].start,NULL)+0.5):0;
    a->alog_line_count=0;int lines=json_get(&j,0,"lines");
    if(lines>=0&&j.tokens[lines].type==J_ARRAY)for(int i=lines+1;i<j.count&&j.tokens[i].start<j.tokens[lines].end&&a->alog_line_count<6;i=json_next(&j,i))
        if(json_string(&j,i,a->alog_lines[a->alog_line_count],sizeof(a->alog_lines[0]))>=0)a->alog_line_count++;
    /* The owner has read about this very start already. */
    if(a->alog_id[0]&&!strcmp(a->alog_id,a->alog_seen))return;
    screen(a,ANDROID_LOG);
}
static void android_log_read(App*a){
    if(a->alog_id[0]&&strcmp(a->alog_seen,a->alog_id)){copy(a->alog_seen,sizeof(a->alog_seen),a->alog_id);settings_changed(a);}
}
static void render_android_log(App*a){
    heading(a,"Android did not start","What its kernel wrote before the phone restarted.",true);
    char line[360],seconds[16];snprintf(seconds,sizeof(seconds),"%u",a->alog_seconds);int y=240;
    if(!strcmp(a->alog_code,"KERNEL_TOO_OLD")){
        if(a->alog_needs[0])snprintf(line,sizeof(line),tr("This Android needs kernel %s or newer. The Android kernel of this phone is older, so Android restarts the phone on every start."),a->alog_needs);
        else copy(line,sizeof(line),tr("This Android needs a newer kernel than the Android kernel of this phone, so Android restarts the phone on every start."));
        raw_text(true);y+=text_tile(a,y,0,ORANGE,line,5)+16;raw_text(false);
        y+=text_tile(a,y,0,TEXT,"Another Android is needed. Images made for older phones (Treble GSIs such as crDroid) start with this kernel, and so does Samsung's firmware.",5)+16;
    }else if(!strcmp(a->alog_code,"KERNEL_PANIC")){
        snprintf(line,sizeof(line),tr("Android's kernel stopped %s seconds after its start."),seconds);
        raw_text(true);y+=text_tile(a,y,0,ORANGE,line,3)+16;if(a->alog_detail[0])y+=text_tile(a,y,0,TEXT,a->alog_detail,4)+16;raw_text(false);
    }else{
        snprintf(line,sizeof(line),tr("Android restarted the phone itself %s seconds after its start."),seconds);
        char why[200];snprintf(why,sizeof(why),tr("Its reason: %s"),a->alog_reason);  /* translated before the text is taken as it is */
        raw_text(true);y+=text_tile(a,y,0,ORANGE,line,3)+16;
        if(a->alog_reason[0])y+=text_tile(a,y,0,TEXT,why,3)+16;
        raw_text(false);
        y+=text_tile(a,y,0,MUTED,"When this happens on every start, the installed Android does not fit the phone or its data: install another one, or format the data for Android in Wipe.",5)+16;
    }
    if(a->alog_line_count){
        /* The kernel's own lines, as they are: never translated. */
        int rows=0;for(unsigned int n=0;n<a->alog_line_count;n++)rows+=strlen(a->alog_lines[n])>52?2:1;
        int height=64+rows*34+(int)a->alog_line_count*8;if(y+height>1070)height=1070-y;
        if(height>110){
            rounded(a,(Rect){28,y,664,height},24,PANEL);text(a,52,y+18,0,ORANGE,"THE KERNEL'S LAST LINES");int at=y+58;
            raw_text(true);
            for(unsigned int n=0;n<a->alog_line_count&&at+34<=y+height-8;n++){
                int lines=strlen(a->alog_lines[n])>52?2:1;if(at+lines*34>y+height-8)lines=1;
                wrapped(a,52,at,616,0,n+1==a->alog_line_count?TEXT:MUTED,a->alog_lines[n],lines);at+=lines*34+8;
            }
            raw_text(false);y+=height+16;
        }
    }
    text_tile(a,y,0,MUTED,"The whole log of that start goes with Export diagnostics.",3);
    button(a,(Rect){28,1104,324,84},"Android images",A_ALOG_IMAGES,ui_free(a),false,0);
    button(a,(Rect){368,1104,324,84},"Samsung firmware",A_ALOG_STOCK,ui_free(a),false,0);
    button(a,(Rect){28,1320,664,104},"Understood",A_ALOG_OK,true,true,0);
}
static bool android_log_action(App*a,int id){
    if(a->screen!=ANDROID_LOG)return false;
    if(id==A_BACK||id==A_ALOG_OK){android_log_read(a);screen(a,HOME);return true;}
    if(id==A_ALOG_IMAGES||id==A_ALOG_STOCK){if(ui_free(a)){android_log_read(a);screen(a,ANDROID_MENU);}return true;}
    return false;
}
#endif

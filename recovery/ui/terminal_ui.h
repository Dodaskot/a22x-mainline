/* SPDX-License-Identifier: MIT
 * UI-only terminal transport, drawing and actions. Included after App, renderer,
 * helper launch and JSON helpers. All shell/PTY work belongs to terminal.py.
 */
#ifndef A22X_TERMINAL_UI_H
#define A22X_TERMINAL_UI_H

static const char*terminal_operation(int kind){
    return kind==J_TERM_STATUS?"status":(kind==J_TERM_START?"start":(kind==J_TERM_READ?"read":(kind==J_TERM_WRITE?"write":(kind==J_TERM_RESIZE?"resize":"stop"))));
}
static void terminal_reset_output(App*a){
    (void)tm_init(&a->terminal,TERMINAL_ROWS,TERMINAL_COLS);a->terminal_scroll=0;a->terminal_seq=0;a->terminal_read_after=0;
    a->terminal_lost=0;a->terminal_follow=true;
}
static void terminal_reset_view(App*a){
    terminal_reset_output(a);a->terminal_input_length=a->terminal_sent=0;memset(a->terminal_input,0,sizeof(a->terminal_input));
}
static void terminal_failure(App*a,const char*code,const char*message){a->terminal_autostart=false;
    copy(a->terminal_reason,sizeof(a->terminal_reason),message);
    a->terminal_paused=true;a->terminal_pending=J_NONE;a->terminal_expected=0;a->terminal_close_requested=false;
    a->terminal_input_length=a->terminal_sent=0;memset(a->terminal_input,0,sizeof(a->terminal_input));
    if(terminal_screen(a)){copy(a->status,sizeof(a->status),code);a->successful=false;a->dirty=true;a->button_count=0;a->focus=-1;}
    /* A session in the background is no reason to disturb another page, and it must not hold storage unseen. */
    else if(a->terminal_left){a->terminal_paused=false;a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=milliseconds()+TERMINAL_LEFT_RETRY_MS;}
    if(a->terminal_exit_after_stop){a->terminal_exit_after_stop=false;stop_signal=0;}
}
/* A finished command's output stays on the page, and the next shell starts under it. */
static void terminal_continue_view(App*a){
    a->terminal_resume=false;a->terminal_seq=a->terminal_read_after=a->terminal_lost=0;a->terminal_follow=true;
    a->terminal_input_length=a->terminal_sent=0;memset(a->terminal_input,0,sizeof(a->terminal_input));
    tm_feed(&a->terminal,"\r\n",2);a->terminal_scroll=terminal_max_scroll(a);
}
/* Leaving never asks: what is running stays in the background, an idle shell closes. */
static void terminal_leave(App*a){
    bool lost=a->terminal_paused;
    a->terminal_left=true;a->terminal_left_busy=a->terminal_autostart=a->terminal_paused=false;a->terminal_reason[0]=0;a->terminal_next_poll=0;
    if(lost){a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=0;}
    else if(strcmp(a->terminal_state,"active")&&strcmp(a->terminal_state,"exited")&&strcmp(a->terminal_state,"starting"))a->terminal_close_requested=true;
}
static void terminal_closed(App*a){
    bool exit=a->terminal_exit_after_stop,kept=a->terminal_left_busy&&!exit;
    if(kept)a->terminal_resume=true;else terminal_reset_view(a);
    a->terminal_expected=0;a->terminal_paused=false;a->terminal_reason[0]=0;a->dirty=true;
    a->terminal_close_requested=a->terminal_exit_after_stop=a->terminal_left=a->terminal_left_busy=false;
    if(exit)a->running=false;
}
/* Left behind, a session lives as long as its command: the last output is read, then it closes. */
static void terminal_left_check(App*a,bool more){
    if(!a->terminal_left)return;
    if(!terminal_session_open(a)){terminal_closed(a);return;}
    if(a->terminal_busy){if(!a->terminal_left_busy){a->terminal_left_busy=true;a->dirty=true;}return;}
    if(more||a->terminal_input_length||milliseconds()-a->terminal_typed<TERMINAL_SETTLE_MS)return;
    a->terminal_close_requested=true;
}
/* Cells keep the model's colors; the chosen scheme decides how they look. */
static uint32_t terminal_color(uint32_t color){
    if(color==TM_DEFAULT_FG)return terminal_scheme_fg();
    if(color==TM_DEFAULT_BG)return terminal_scheme_bg();
    const uint32_t*palette=terminal_scheme_palette();
    if(palette){static const uint32_t colors[16]=TM_ANSI_PALETTE;for(unsigned int n=0;n<16;n++)if(color==colors[n])return palette[n];}
    return color;
}
static void terminal_glyph(App*a,int x,int y,uint32_t cp,uint32_t color,bool bold){
    unsigned lo=0,hi=TERMINAL_GLYPHS;
    while(lo<hi){unsigned middle=(lo+hi)/2;if(terminal_codepoints[middle]<cp)lo=middle+1;else hi=middle;}
    if(lo>=TERMINAL_GLYPHS||terminal_codepoints[lo]!=cp)lo=TERMINAL_GLYPHS-1;
    const Glyph*g=&terminal_glyphs[lo];
    if(!g->w||!g->h)return;
    Bounds b=canvas_bounds(a);const uint8_t*alpha=terminal_font_alpha+g->off;
    canvas_mask(a->pixels,b,nx(x)+g->x,nx(y)+g->y,g->w,g->h,alpha,color);
    if(bold)canvas_mask(a->pixels,b,nx(x)+g->x+1,nx(y)+g->y,g->w,g->h,alpha,color);
}
static void terminal_draw_grid(App*a){
    Rect view=terminal_viewport();gradient(a,(Rect){28,232,664,528},24,terminal_scheme_bg(),terminal_scheme_bg());
    int64_t maximum=terminal_max_scroll(a);a->terminal_scroll=(int64_t)scroll_clamp((double)a->terminal_scroll,(double)maximum);
    bool clip=a->clip_active;Rect previous=a->clip;a->clip_active=true;a->clip=view;
    unsigned first=(unsigned)(a->terminal_scroll/TERMINAL_CELL_H),lines=tm_line_count(&a->terminal);
    for(unsigned row=first;row<lines;row++){
        int y=view.y+(int)((int64_t)row*TERMINAL_CELL_H-a->terminal_scroll);if(y>=view.y+view.h)break;
        const TmLine*line=tm_line(&a->terminal,row);if(!line)break;
        for(unsigned col=0;col<TERMINAL_COLS;col++){
            const TmCell*c=&line->cells[col];if(!c->width)continue;
            int x=view.x+(int)col*TERMINAL_CELL_W,width=(int)c->width*TERMINAL_CELL_W;
            uint32_t fg=terminal_color(c->fg),bg=terminal_color(c->bg);if(c->flags&TM_INVERSE){uint32_t swap=fg;fg=bg;bg=swap;}
            if(c->flags&TM_DIM)fg=((fg&0xfefefeu)>>1)+((bg&0xfefefeu)>>1);
            rectangle(a,(Rect){x,y,width,TERMINAL_CELL_H},bg);
            Rect cell_clip={x,y,width,TERMINAL_CELL_H};int bottom=y+TERMINAL_CELL_H;if(cell_clip.y<view.y){cell_clip.y=view.y;cell_clip.h=bottom-view.y;}if(cell_clip.y+cell_clip.h>view.y+view.h)cell_clip.h=view.y+view.h-cell_clip.y;
            a->clip=cell_clip;
            if(!(c->flags&TM_HIDDEN)){
                terminal_glyph(a,x,y,c->cp?c->cp:' ',fg,(c->flags&TM_BOLD)!=0);
                if(c->mark)terminal_glyph(a,x,y,c->mark,fg,(c->flags&TM_BOLD)!=0);
                if(c->flags&TM_UNDERLINE)rectangle(a,(Rect){x,y+TERMINAL_CELL_H-3,width,1},fg);
            }
            a->clip=view;
        }
    }
    const TmPage*p=tm_const_page(&a->terminal);unsigned cursor_row=(a->terminal.page?0:a->terminal.history_count)+p->row;
    if(a->terminal.cursor_visible&&!strcmp(a->terminal_state,"active")){
        int x=view.x+(int)p->col*TERMINAL_CELL_W,y=view.y+(int)((int64_t)cursor_row*TERMINAL_CELL_H-a->terminal_scroll);
        rectangle(a,(Rect){x,y+TERMINAL_CELL_H-3,TERMINAL_CELL_W,2},ORANGE);
    }
    a->clip_active=clip;a->clip=previous;
    scroll_indicator(a,(Rect){680,view.y,4,view.h},a->terminal_scroll,(int64_t)lines*TERMINAL_CELL_H,ORANGE);
}
static void render_terminal(App*a){
    heading(a,"Terminal","Recovery RAM / root shell\nLinux: /mnt/volumes/userdata",true);
    /* The shell opens by itself, so these moments show the terminal, not a page about it. */
    if(!terminal_session_open(a)&&(a->terminal_autostart||(a->job.pid&&a->job.kind==J_TERM_START))){terminal_draw_grid(a);return;}
    if(!terminal_session_open(a)){
        rounded(a,(Rect){28,252,664,600},18,PANEL);text(a,52,282,0,ORANGE,"LOCAL SHELL");
        wrapped(a,52,340,604,1,TEXT,"Run commands directly on this phone.",3);
        wrapped(a,52,466,604,0,MUTED,"The starting root is recovery RAM. Mount Linux in Files first, then use /mnt/volumes/userdata. The microSD backup mount is /mnt/backup.",6);
        wrapped(a,52,676,604,0,MUTED,"Basic ANSI, UTF-8, history and completion. Full-screen programs may have limited display support.",4);
        if(a->terminal_reason[0])wrapped(a,40,904,640,0,RED,a->terminal_reason,8);
        else wrapped(a,40,904,640,0,MUTED,"While the shell is open, other storage operations wait for this terminal to close.",4);
        button(a,(Rect){28,1198,664,84},"Refresh terminal status",A_TERM_REFRESH,ui_free(a),false,0);
        button(a,(Rect){28,1320,664,102},a->job.pid?"Reading terminal state...":"Start local shell",A_TERM_START,a->terminal_known&&a->terminal_available&&!strcmp(a->terminal_state,"stopped")&&ui_free(a),true,0);
        return;
    }
    terminal_draw_grid(a);
    char info[192];
    if(a->terminal_reason[0])copy(info,sizeof(info),a->terminal_reason);
    else if(!strcmp(a->terminal_state,"exited"))snprintf(info,sizeof(info),T("Shell exited (%d). Exit to release storage."),a->terminal_exit_code);
    else if(a->terminal_lost)snprintf(info,sizeof(info),"Output gap: %" PRIu64 " bytes skipped / %s",a->terminal_lost,tr(a->terminal_follow?"live":"history"));
    else snprintf(info,sizeof(info),T("%s / %u history rows / basic ANSI"),tr(a->terminal_follow?"Live output":"Scrollback"),a->terminal.history_count);
    ellipsis(a,34,770,652,0,a->terminal_paused?RED:MUTED,info);
    bool keys=terminal_input_ready(a);
    button(a,(Rect){28,806,122,50},"Tab",A_TERM_TAB,keys,false,0);
    button(a,(Rect){160,806,122,50},"Esc",A_TERM_ESCAPE,keys,false,0);
    button(a,(Rect){292,806,136,50},"Ctrl+C",A_TERM_INTERRUPT,keys,false,0);
    button(a,(Rect){438,806,136,50},"Ctrl+D",A_TERM_EOF,keys,false,0);
    button(a,(Rect){584,806,108,50},"Live",A_TERM_LIVE,true,a->terminal_follow,0);
    button(a,(Rect){28,866,154,54},"←",A_TERM_LEFT,keys,false,0);
    button(a,(Rect){198,866,154,54},"↑",A_TERM_UP,keys,false,0);
    button(a,(Rect){368,866,154,54},"↓",A_TERM_DOWN,keys,false,0);
    button(a,(Rect){538,866,154,54},"→",A_TERM_RIGHT,keys,false,0);
    render_keyboard(a);
    button(a,(Rect){28,1370,224,84},"Refresh",A_TERM_REFRESH,ui_free(a)||terminal_background_job(a->job.kind),false,0);
    button(a,(Rect){272,1370,420,84},"Exit terminal",A_TERM_CLOSE,ui_free(a)||terminal_background_job(a->job.kind),true,0);
}
/* Read and write are the hot path: talk to the session supervisor directly instead of
 * starting a Python client for every key press and poll. Same requests, same replies. */
static bool terminal_fast(App*a,int kind){
    static char boot[64],reply[131072];
    if(!boot[0]){FILE*f=fopen("/proc/sys/kernel/random/boot_id","r");if(!f)return false;if(!fgets(boot,sizeof(boot),f)){fclose(f);return false;}fclose(f);boot[strcspn(boot,"\n")]=0;}
    char request[6144];int length;
    if(kind==J_TERM_READ){a->terminal_read_after=a->terminal_seq;
        length=snprintf(request,sizeof(request),"{\"operation\":\"read\",\"boot_id\":\"%s\",\"session_token\":\"%s\",\"after_seq\":%" PRIu64 ",\"max_bytes\":16384}\n",boot,a->terminal_session,a->terminal_read_after);
    }else{char encoded[5465];a->terminal_sent=a->terminal_input_length<4096?a->terminal_input_length:4096;
        if(!a->terminal_sent||tm_base64_encode(a->terminal_input,a->terminal_sent,encoded,sizeof(encoded))==SIZE_MAX)return false;
        length=snprintf(request,sizeof(request),"{\"operation\":\"write\",\"boot_id\":\"%s\",\"session_token\":\"%s\",\"data_base64\":\"%s\"}\n",boot,a->terminal_session,encoded);}
    if(length<=0||length>=(int)sizeof(request))return false;
    int fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return false;
    struct sockaddr_un address={.sun_family=AF_UNIX};copy(address.sun_path,sizeof(address.sun_path),"/run/a22x-recovery/terminal/control.sock");
    struct timeval limit={1,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&limit,sizeof(limit));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&limit,sizeof(limit));
    if(connect(fd,(struct sockaddr*)&address,sizeof(address))<0||write(fd,request,(size_t)length)!=length){close(fd);memset(request,0,sizeof(request));return false;}
    memset(request,0,sizeof(request));shutdown(fd,SHUT_WR);
    /* The Python client adds the operation name; keep the reply contract the same. */
    size_t used=(size_t)snprintf(reply,sizeof(reply),"{\"operation\":\"%s\",",kind==J_TERM_READ?"read":"write");size_t head=used;
    for(;;){ssize_t n=read(fd,reply+used,sizeof(reply)-1-used);if(n<=0)break;used+=(size_t)n;if(used>=sizeof(reply)-1)break;}
    close(fd);
    if(used<=head+1||reply[head]!='{'){memset(reply,0,used);complete_terminal_job(a,NULL,kind,false);return true;}
    memmove(reply+head,reply+head+1,used-head-1);used--;reply[used]=0;
    JsonToken*tok=calloc(8192,sizeof(*tok));Json doc;bool parsed=tok&&json_parse(&doc,reply,tok,8192)>0;
    bool ok=parsed&&json_bool(&doc,json_get(&doc,0,"ok"),0);
    complete_terminal_job(a,parsed?&doc:NULL,kind,ok);free(tok);memset(reply,0,used);if(terminal_screen(a))a->dirty=true;return true;
}
static bool start_terminal_job(App*a,int kind){
    if(a->job.pid||a->preview||!terminal_job(kind))return false;
    if(a->device&&(kind==J_TERM_READ||kind==J_TERM_WRITE)&&valid_sha256(a->terminal_session)&&terminal_fast(a,kind))return true;
    if(!a->terminal_helper[0]){terminal_failure(a,"TERMINAL_UNAVAILABLE","The local terminal helper is not configured.");return false;}
    if(kind!=J_TERM_STATUS&&kind!=J_TERM_START&&!valid_sha256(a->terminal_session)){terminal_failure(a,"SESSION_UNAVAILABLE","Refresh the terminal state before continuing.");return false;}
    char rows[16],cols[16],after[32],encoded[5465];char*args[24];int n=0;
    args[n++]=a->python;args[n++]=a->terminal_helper;args[n++]="--mode";args[n++]=a->device?"device":"fixture";
    if(!a->device){args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]=(char*)terminal_operation(kind);
    if(kind!=J_TERM_STATUS&&kind!=J_TERM_START){args[n++]="--expected-session-token";args[n++]=a->terminal_session;}
    if(kind==J_TERM_START||kind==J_TERM_RESIZE){snprintf(rows,sizeof(rows),"%u",TERMINAL_ROWS);snprintf(cols,sizeof(cols),"%u",TERMINAL_COLS);args[n++]="--rows";args[n++]=rows;args[n++]="--cols";args[n++]=cols;}
    if(kind==J_TERM_READ){a->terminal_read_after=a->terminal_seq;snprintf(after,sizeof(after),"%" PRIu64,a->terminal_read_after);args[n++]="--after-seq";args[n++]=after;args[n++]="--max-bytes";args[n++]="16384";}
    if(kind==J_TERM_WRITE){
        a->terminal_sent=a->terminal_input_length<4096?a->terminal_input_length:4096;
        if(!a->terminal_sent||tm_base64_encode(a->terminal_input,a->terminal_sent,encoded,sizeof(encoded))==SIZE_MAX)return false;
        args[n++]="--data-base64";args[n++]=encoded;
    }
    args[n]=NULL;
    if(!launch_job(a,kind,args)){if(!a->terminal_left)screen(a,TERMINAL);terminal_failure(a,"TERMINAL_CLIENT_FAILED","Could not launch the terminal client. No input will be retried automatically.");return false;}
    a->result_return=TERMINAL;return true;
}
static bool terminal_common(App*a,const Json*j,int kind){
    char state[32],session[80],boot[80];jstring(j,0,"state",state,sizeof(state));jstring(j,0,"session_token",session,sizeof(session));jstring(j,0,"boot_id",boot,sizeof(boot));
    if(!jequals(j,0,"mode",a->device?"device":"fixture")||!jequals(j,0,"operation",terminal_operation(kind))||!boot[0])return false;
    bool stopped=!strcmp(state,"stopped"),session_state=!strcmp(state,"starting")||!strcmp(state,"active")||!strcmp(state,"exited")||!strcmp(state,"stopping")||!strcmp(state,"failed");
    if(!stopped&&!session_state)return false;
    if(session_state&&!valid_sha256(session))return false;
    if(kind!=J_TERM_STATUS&&kind!=J_TERM_START&&strcmp(session,a->terminal_session))return false;
    if(a->terminal_boot[0]&&strcmp(a->terminal_boot,boot))return false;
    if(kind==J_TERM_STATUS&&a->terminal_expected==1&&strcmp(session,a->terminal_session))return false;
    if(kind==J_TERM_STATUS&&a->terminal_expected==2&&(!stopped||session[0]||json_bool(j,json_get(j,0,"storage_lease_held"),1)))return false;
    if((!strcmp(state,"starting")||!strcmp(state,"active")||!strcmp(state,"exited")||!strcmp(state,"stopping"))&&!json_bool(j,json_get(j,0,"storage_lease_held"),0))return false;
    uint64_t rows=json_uint(j,json_get(j,0,"rows"),0),cols=json_uint(j,json_get(j,0,"cols"),0);
    if(session_state&&(!rows||rows>1000||!cols||cols>1000))return false;
    if(kind==J_TERM_STATUS&&session[0]&&strcmp(session,a->terminal_session))terminal_reset_view(a);
    bool changed=strcmp(state,a->terminal_state)||strcmp(session,a->terminal_session);
    copy(a->terminal_state,sizeof(a->terminal_state),state);copy(a->terminal_session,sizeof(a->terminal_session),session);copy(a->terminal_boot,sizeof(a->terminal_boot),boot);
    a->terminal_rows=(unsigned)rows;a->terminal_cols=(unsigned)cols;a->terminal_known=true;a->terminal_available=json_bool(j,json_get(j,0,"available"),0);
    a->terminal_exit_code=(int)json_uint(j,json_get(j,0,"shell_exit_code"),0);
    bool busy=json_bool(j,json_get(j,0,"command_running"),0);if(busy!=a->terminal_busy&&terminal_screen(a))a->dirty=true;
    a->terminal_busy=busy;
    if(changed){a->button_count=0;a->focus=-1;a->dirty=true;}
    return true;
}
static bool terminal_accept_output(App*a,const Json*j){
    char encoded[(TERMINAL_READ_LIMIT+2)/3*4+1];unsigned char raw[TERMINAL_READ_LIMIT];
    if(json_string(j,json_get(j,0,"data_base64"),encoded,sizeof(encoded))<0)return false;
    size_t length=tm_base64_decode(encoded,raw,sizeof(raw));if(length==SIZE_MAX)return false;
    uint64_t start=json_uint(j,json_get(j,0,"start_seq"),UINT64_MAX),next=json_uint(j,json_get(j,0,"next_seq"),UINT64_MAX);
    uint64_t end=json_uint(j,json_get(j,0,"end_seq"),UINT64_MAX),lost=json_uint(j,json_get(j,0,"lost_bytes"),UINT64_MAX);
    uint64_t first=start>a->terminal_read_after?start:a->terminal_read_after;
    if(start==UINT64_MAX||next==UINT64_MAX||end==UINT64_MAX||lost==UINT64_MAX||a->terminal_seq!=a->terminal_read_after||start>end||first>end||length>end-first||next!=first+length||lost!=(start>a->terminal_read_after?start-a->terminal_read_after:0))return false;
    bool here=terminal_screen(a),following=a->terminal_follow&&(!here||(!a->scroll_touch&&!a->kinetic.active));unsigned old_page=a->terminal.page;uint64_t dropped=a->terminal.dropped_lines;
    if(lost){
        uint64_t previous_lost=a->terminal_lost;terminal_reset_output(a);a->terminal_lost=UINT64_MAX-previous_lost<lost?UINT64_MAX:previous_lost+lost;following=true;dropped=0;
        if(a->motion_surface==SCROLL_TERMINAL||a->scroll_surface==SCROLL_TERMINAL)cancel_pointer(a);
    }
    tm_feed(&a->terminal,raw,length);a->terminal_seq=next;
    if(length||lost){
        if(a->terminal.dropped_lines>=dropped){
            uint64_t removed=a->terminal.dropped_lines-dropped;int64_t shift=removed>INT64_MAX/TERMINAL_CELL_H?INT64_MAX:(int64_t)removed*TERMINAL_CELL_H;
            a->terminal_scroll=a->terminal_scroll>shift?a->terminal_scroll-shift:0;
            if(a->scroll_surface==SCROLL_TERMINAL){a->scroll_start_offset=a->scroll_start_offset>shift?a->scroll_start_offset-shift:0;for(unsigned n=0;n<a->scroll_samples.count;n++)a->scroll_samples.samples[n].position-=shift;}
            if(a->motion_surface==SCROLL_TERMINAL)a->kinetic.position=scroll_clamp(a->kinetic.position-shift,(double)terminal_max_scroll(a));
        }
        if(old_page!=a->terminal.page){if(here)stop_inertia(a,"terminal-screen");following=true;}
        if(following)a->terminal_scroll=terminal_max_scroll(a);
        a->terminal_scroll=(int64_t)scroll_clamp((double)a->terminal_scroll,(double)terminal_max_scroll(a));a->terminal_follow=following;if(here)a->dirty=true;
    }
    if(a->terminal.reply_length){
        if(!strcmp(a->terminal_state,"active")&&a->terminal.reply_length<=sizeof(a->terminal_input)-a->terminal_input_length){memcpy(a->terminal_input+a->terminal_input_length,a->terminal.replies,a->terminal.reply_length);a->terminal_input_length+=a->terminal.reply_length;}
        a->terminal.reply_length=0;
    }
    /* Out of sight a running command is looked at once a second; a flood then shows as an output gap. */
    uint64_t now=milliseconds();
    if(a->terminal_left&&a->terminal_busy)a->terminal_next_poll=now+TERMINAL_LEFT_POLL_MS;
    else a->terminal_next_poll=next<end?0:now+(a->terminal_left?100u:(!strcmp(a->terminal_state,"exited")?500u:40u));
    terminal_left_check(a,next<end);
    return true;
}
static void complete_terminal_job(App*a,const Json*j,int kind,bool ok){
    if(a->terminal_close_requested&&terminal_background_job(kind)){
        /* Explicit Close supersedes pending input and output. Its next action is
         * one Stop for the already pinned token, even if a write was uncertain. */
        a->terminal_input_length=a->terminal_sent=0;memset(a->terminal_input,0,sizeof(a->terminal_input));return;
    }
    if(!ok){
        char code[96]="TERMINAL_CLIENT_FAILED",message[384]="The terminal helper did not return a valid response.";
        if(j){int error=json_get(j,0,"error");if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_TERM_STATUS&&a->terminal_expected==2&&milliseconds()<a->terminal_stop_deadline&&(!strcmp(code,"IO_ERROR")||!strcmp(code,"SERVICE_STALE"))){
            a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=milliseconds()+120u;copy(a->terminal_reason,sizeof(a->terminal_reason),"Waiting for the terminal supervisor to exit...");a->dirty=true;return;
        }
        if(kind==J_TERM_STATUS&&a->terminal_expected==1&&!strcmp(a->terminal_state,"starting")&&milliseconds()<a->terminal_start_deadline&&(!strcmp(code,"IO_ERROR")||!strcmp(code,"SERVICE_STALE"))){
            a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=milliseconds()+250u;copy(a->terminal_reason,sizeof(a->terminal_reason),"The existing shell session is still starting...");a->dirty=true;return;
        }
        terminal_failure(a,code,kind==J_TERM_WRITE?"Input delivery was not confirmed. Pending input was discarded and will not be replayed. Refresh to inspect the shell.":message);return;
    }
    if(!terminal_common(a,j,kind)){terminal_failure(a,"INVALID_TERMINAL_STATE","The terminal session, boot, operation or storage lease was not confirmed. Refresh before continuing.");return;}
    if(terminal_screen(a))a->successful=true;
    if(kind==J_TERM_START){a->terminal_expected=1;a->terminal_pending=J_TERM_STATUS;a->terminal_start_deadline=milliseconds()+10000u;return;}
    if(kind==J_TERM_STOP){
        if(strcmp(a->terminal_state,"stopped")||json_bool(j,json_get(j,0,"storage_lease_held"),1)){terminal_failure(a,"TERMINAL_STOP_PENDING","The shell has not stopped completely. The terminal remains open.");return;}
        a->terminal_close_requested=false;a->terminal_expected=2;a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=0;a->terminal_stop_deadline=milliseconds()+2500u;return;
    }
    if(kind==J_TERM_RESIZE){a->terminal_expected=3;a->terminal_pending=J_TERM_STATUS;return;}
    if(kind==J_TERM_WRITE){
        uint64_t accepted=json_uint(j,json_get(j,0,"bytes_written"),UINT64_MAX);
        if(accepted!=a->terminal_sent||a->terminal_sent>a->terminal_input_length){terminal_failure(a,"TERMINAL_INPUT_UNCONFIRMED","The input acknowledgement did not match. Pending input was discarded and will not be replayed.");return;}
        a->terminal_input_length-=a->terminal_sent;memmove(a->terminal_input,a->terminal_input+a->terminal_sent,a->terminal_input_length);memset(a->terminal_input+a->terminal_input_length,0,sizeof(a->terminal_input)-a->terminal_input_length);a->terminal_sent=0;a->terminal_next_poll=0;return;
    }
    if(kind==J_TERM_READ){if(!terminal_accept_output(a,j))terminal_failure(a,"INVALID_TERMINAL_OUTPUT","Output sequence or encoding changed. Refresh the shell state; input has been paused.");return;}
    if(a->terminal_expected==2){terminal_closed(a);return;}
    if(!strcmp(a->terminal_state,"starting")){
        if(a->terminal_expected!=1){a->terminal_expected=1;a->terminal_start_deadline=milliseconds()+10000u;}
        if(milliseconds()>=a->terminal_start_deadline){terminal_failure(a,"TERMINAL_START_PENDING","The existing shell has not become ready. Refresh or close this exact session; another shell is not started.");return;}
        a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=milliseconds()+250u;copy(a->terminal_reason,sizeof(a->terminal_reason),"The existing shell session is still starting...");a->dirty=true;return;
    }
    if(a->terminal_expected==1&&strcmp(a->terminal_state,"active")&&strcmp(a->terminal_state,"exited")){terminal_failure(a,"TERMINAL_START_UNCONFIRMED","A running shell was not confirmed. No additional session was started.");return;}
    if(a->terminal_expected==3&&(a->terminal_rows!=TERMINAL_ROWS||a->terminal_cols!=TERMINAL_COLS)){terminal_failure(a,"TERMINAL_SIZE_UNCONFIRMED","The requested terminal dimensions were not applied.");return;}
    a->terminal_expected=0;a->terminal_paused=false;a->terminal_reason[0]=0;
    jstring(j,0,"reason",a->terminal_reason,sizeof(a->terminal_reason));
    if(terminal_session_open(a)&&(!strcmp(a->terminal_state,"active")||!strcmp(a->terminal_state,"exited"))){
        if(a->terminal_rows!=TERMINAL_ROWS||a->terminal_cols!=TERMINAL_COLS){terminal_reset_view(a);a->terminal_pending=J_TERM_RESIZE;}
        else a->terminal_next_poll=0;
    }
    a->dirty=true;
    terminal_left_check(a,false);
}
static void terminal_queue(App*a,const void*bytes,size_t length){
    if(!terminal_input_ready(a)||!length)return;
    if(length>sizeof(a->terminal_input)-a->terminal_input_length){copy(a->terminal_reason,sizeof(a->terminal_reason),"Input queue is full. Wait for the shell before typing more.");a->dirty=true;return;}
    stop_inertia(a,"terminal-key");a->terminal_scroll=terminal_max_scroll(a);a->terminal_follow=true;a->terminal_typed=milliseconds();
    memcpy(a->terminal_input+a->terminal_input_length,bytes,length);a->terminal_input_length+=length;a->dirty=true;
}
static bool terminal_action(App*a,int id){
    if(id==A_TERMINAL){
        if(a->job.pid)return true;
        if(a->fs_dirty||a->fs_new_file){files_problem(a,"UNSAVED_EDITS","Save or explicitly discard the edited text before opening a shell that can change storage.",FILE_EDIT);return true;}
        /* Opened again while the old shell closes: the new one starts when it has gone. */
        if(a->terminal_close_requested||a->terminal_expected==2){a->terminal_autostart=true;screen(a,TERMINAL);return true;}
        a->terminal_left=a->terminal_left_busy=false;
        a->terminal_known=false;a->terminal_paused=false;a->terminal_reason[0]=0;a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=0;a->terminal_expected=0;a->terminal_start_deadline=0;
        if(!terminal_session_open(a)&&!a->terminal_resume)terminal_reset_view(a);
        a->terminal_autostart=true;screen(a,TERMINAL);terminal_pump(a);return true;
    }
    if(!terminal_screen(a))return false;
    if(a->terminal_expected==2)return true; /* Wait for status-only stop confirmation. */
    if(a->job.pid&&!terminal_background_job(a->job.kind))return true;
    /* The terminal opens over any page (swipe up and hold): Back returns to that page, as it was left. */
    if(id==A_BACK||id==A_TERM_CLOSE){enum Screen from=came_from(a,HOME);if(terminal_session_open(a))terminal_leave(a);screen(a,from);terminal_pump(a);return true;}
    if(id==A_TERM_REFRESH){a->terminal_pending=J_TERM_STATUS;a->terminal_pending_after=0;a->terminal_paused=false;a->terminal_reason[0]=0;a->terminal_expected=0;terminal_pump(a);return true;}
    if(id==A_TERM_START){
        if(a->fs_dirty||a->fs_new_file){terminal_failure(a,"UNSAVED_EDITS","Save or discard the file editor before starting this shell.");return true;}
        if(a->usb_known&&usb_session_open(a)){terminal_failure(a,"USB_EXPORT_ACTIVE","Stop USB file sharing before starting the local shell.");return true;}
        if(a->terminal_known&&a->terminal_available&&!strcmp(a->terminal_state,"stopped")&&!terminal_session_open(a)){
            if(a->terminal_resume)terminal_continue_view(a);else terminal_reset_view(a);
            a->terminal_paused=false;a->terminal_reason[0]=0;start_terminal_job(a,J_TERM_START);
        }
        return true;
    }
    if(id==A_TERM_LIVE){stop_inertia(a,"live");terminal_scroll_to(a,terminal_max_scroll(a));a->terminal_follow=true;return true;}
    if(!terminal_input_ready(a))return true;
    if(id>=A_KB_KEY&&id<A_KB_KEY+a->keyboard_count){char bytes[5];size_t n=utf8_encode(a->keyboard_keys[id-A_KB_KEY],bytes);terminal_queue(a,bytes,n);return true;}
    if(id==A_KB_SHIFT){a->kb_shift=!a->kb_shift;a->dirty=true;return true;}
    if(id==A_KB_LANG){a->kb_uk=!a->kb_uk;a->dirty=true;return true;}
    if(id==A_KB_SYMBOLS){a->kb_symbols=!a->kb_symbols;a->dirty=true;return true;}
    const char*bytes=id==A_KB_ENTER?"\r":(id==A_KB_BACKSPACE?"\177":(id==A_KB_SPACE?" ":(id==A_TERM_TAB?"\t":(id==A_TERM_ESCAPE?"\033":(id==A_TERM_INTERRUPT?"\003":(id==A_TERM_EOF?"\004":NULL))))));
    char arrow[4]={27,a->terminal.application_cursor?'O':'[',0,0};
    if(id==A_TERM_UP||id==A_TERM_DOWN||id==A_TERM_LEFT||id==A_TERM_RIGHT){arrow[2]=id==A_TERM_UP?'A':(id==A_TERM_DOWN?'B':(id==A_TERM_RIGHT?'C':'D'));bytes=arrow;}
    if(bytes)terminal_queue(a,bytes,strlen(bytes));
    return true;
}
static void terminal_pump(App*a){
    if(a->preview||a->job.pid||(!terminal_screen(a)&&!a->terminal_left))return;
    if(a->terminal_close_requested){a->terminal_close_requested=false;start_terminal_job(a,J_TERM_STOP);a->terminal_close_requested=a->job.pid!=0;return;}
    if(a->terminal_pending){if(milliseconds()<a->terminal_pending_after)return;int kind=a->terminal_pending;a->terminal_pending=J_NONE;a->terminal_pending_after=0;start_terminal_job(a,kind);return;}
    if(a->terminal_autostart&&a->terminal_known){
        a->terminal_autostart=false;
        if(a->screen==TERMINAL&&a->terminal_available&&!strcmp(a->terminal_state,"stopped")&&!terminal_session_open(a)){(void)terminal_action(a,A_TERM_START);return;}
    }
    if(!terminal_session_open(a)||!a->terminal_available||a->terminal_paused)return;
    if(a->terminal_input_length&&!strcmp(a->terminal_state,"active")){start_terminal_job(a,J_TERM_WRITE);return;}
    if((!strcmp(a->terminal_state,"active")||!strcmp(a->terminal_state,"exited"))&&milliseconds()>=a->terminal_next_poll)start_terminal_job(a,J_TERM_READ);
}
#endif

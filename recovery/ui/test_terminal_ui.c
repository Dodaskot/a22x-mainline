/* SPDX-License-Identifier: MIT — runs the actual UI against an explicit helper double. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main

#define TCHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"terminal-ui check %u line %d failed: %s; screen=%d status=%s\n",checks,__LINE__,#expr,a->screen,a->status);free(a->pixels);free(a);return 1;}}while(0)
static bool terminal_test_settle(App*a){
    uint64_t deadline=milliseconds()+10000;
    while((a->job.pid||a->terminal_pending||a->terminal_close_requested)&&milliseconds()<deadline){
        a->now=milliseconds();poll_job(a);if(!a->job.pid&&(a->terminal_pending||a->terminal_close_requested))terminal_pump(a);
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);
    }
    return !a->job.pid&&!a->terminal_pending&&!a->terminal_close_requested;
}
static bool terminal_test_read(App*a){a->terminal_next_poll=0;terminal_pump(a);return terminal_test_settle(a);}
/* What the main loop does for a session left behind, until that session has closed. */
static bool terminal_test_closed(App*a){
    uint64_t deadline=milliseconds()+10000;
    while((terminal_session_open(a)||a->job.pid||a->terminal_pending||a->terminal_left)&&milliseconds()<deadline){
        a->now=milliseconds();poll_job(a);terminal_pump(a);
        struct timespec pause={0,1000000};nanosleep(&pause,NULL);
    }
    return !terminal_session_open(a)&&!a->job.pid&&!a->terminal_left;
}
/* The next visit opens the session that is really there, or a new one. */
static bool terminal_test_visit(App*a){return test_press(a,A_TERMINAL,50)&&terminal_test_settle(a)&&a->screen==TERMINAL;}
int main(int argc,char**argv){
    if(argc!=5)return 2;
    unsigned checks=0;const char*which=argv[3];App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->terminal_helper,sizeof(a->terminal_helper),argv[2]);copy(a->python,sizeof(a->python),argv[4]);screen(a,HOME);
    copy(a->fs_text,sizeof(a->fs_text),"untouched editor buffer Ґ");a->fs_length=strlen(a->fs_text);a->fs_cursor=5;a->fs_dirty=true;
    TCHECK(test_press(a,A_TERMINAL,50)&&a->screen==RESULT&&a->result_return==FILE_EDIT&&!a->job.pid&&!strcmp(a->fs_text,"untouched editor buffer Ґ"));
    a->fs_dirty=false;screen(a,HOME);render(a);
    Button*terminal_button=test_button(a,A_TERMINAL);TCHECK(terminal_button!=NULL);
    for(int n=0;n<a->button_count;n++)if(a->buttons[n].action!=A_TERMINAL){Rect r=a->buttons[n].r,t=terminal_button->r;TCHECK(r.x+r.w<=t.x||t.x+t.w<=r.x||r.y+r.h<=t.y||t.y+t.h<=r.y);}
    TCHECK(test_press(a,A_TERMINAL,50)&&terminal_test_settle(a)&&a->screen==TERMINAL&&a->terminal_known&&!terminal_session_open(a));render(a);
    TCHECK(!a->terminal_input_length&&a->fs_cursor==5&&!strcmp(a->fs_text,"untouched editor buffer Ґ"));
    if(!strcmp(which,"unavailable")){TCHECK(!a->terminal_available&&!test_press(a,A_TERM_START,50));goto done;}
    /* The shell starts by itself: no second tap. */
    terminal_pump(a);TCHECK(terminal_test_settle(a)&&!a->terminal_autostart);
    if(!strcmp(which,"busy")){TCHECK(a->terminal_paused&&!strcmp(a->status,"OPERATION_BUSY")&&!terminal_session_open(a));goto done;}
    TCHECK(terminal_session_open(a)&&a->terminal_known&&!strcmp(a->terminal_state,"active")&&a->terminal_rows==21&&a->terminal_cols==48);
    TCHECK(terminal_test_read(a));
    if(!strcmp(which,"bad-sequence")){TCHECK(a->terminal_paused&&!strcmp(a->status,"INVALID_TERMINAL_OUTPUT")&&a->terminal_seq==0);goto close_session;}
    TCHECK(a->terminal.history_count>90&&a->terminal_scroll==terminal_max_scroll(a)&&a->terminal_seq>0);
    if(!strcmp(which,"gap"))TCHECK(a->terminal_lost==37);
    else TCHECK(a->terminal_lost==0);
    if(!strcmp(which,"gap-inflight")){
        a->terminal_next_poll=0;terminal_pump(a);TCHECK(a->job.pid&&a->job.kind==J_TERM_READ);
        TCHECK(test_press(a,A_TERM_INTERRUPT,50)&&a->terminal_input_length==1&&a->terminal_input[0]==3);
        TCHECK(terminal_test_settle(a)&&a->terminal_lost==37&&a->terminal_input_length==1&&a->terminal_input[0]==3);
        terminal_pump(a);TCHECK(a->job.pid&&a->job.kind==J_TERM_WRITE&&terminal_test_settle(a)&&!a->terminal_input_length);
    }
    uint64_t at=milliseconds();workflow_swipe(a,250,350,650,at);TCHECK(a->kinetic.active&&a->motion_surface==SCROLL_TERMINAL&&!a->terminal_follow);
    int64_t before=a->terminal_scroll;a->terminal_next_poll=0;terminal_pump(a);TCHECK(a->job.pid&&a->job.kind==J_TERM_READ);
    tick_inertia(a,at+120);TCHECK(a->terminal_scroll<before&&a->kinetic.active);TCHECK(terminal_test_settle(a));
    render(a);int64_t stopped=a->terminal_scroll;size_t input_before=a->terminal_input_length;
    pointer(a,true,200,450,at+130);pointer(a,false,200,450,at+160);TCHECK(!a->kinetic.active&&a->terminal_scroll==stopped&&a->terminal_input_length==input_before&&a->fs_cursor==5);
    terminal_scroll_to(a,INT64_MAX);TCHECK(a->terminal_scroll==terminal_max_scroll(a));terminal_scroll_to(a,-500);TCHECK(a->terminal_scroll==0);
    TCHECK(test_press(a,A_TERM_LIVE,50)&&a->terminal_follow&&a->terminal_scroll==terminal_max_scroll(a));
    /* Read completion does not cancel a held keyboard contact or change the hit map. */
    a->terminal_next_poll=0;terminal_pump(a);TCHECK(a->job.pid&&a->job.kind==J_TERM_READ);render(a);
    int letter=-1;for(int n=0;n<a->keyboard_count;n++)if(a->keyboard_keys[n]=='a')letter=A_KB_KEY+n;
    TCHECK(letter>=0);Button*b=test_button(a,letter);TCHECK(b&&b->enabled);int x=b->r.x+b->r.w/2,y=b->r.y+b->r.h/2;
    uint64_t down=milliseconds();pointer(a,true,x,y,down);TCHECK(terminal_test_settle(a));pointer(a,false,x,y,down+50);
    TCHECK(a->terminal_input_length==1&&a->terminal_input[0]=='a');
    a->kb_uk=true;render(a);letter=-1;for(int n=0;n<a->keyboard_count;n++)if(a->keyboard_keys[n]==0x491)letter=A_KB_KEY+n;
    TCHECK(letter>=0&&test_press(a,letter,50)&&a->terminal_input_length==3&&!memcmp(a->terminal_input+1,"ґ",2));
    TCHECK(test_press(a,A_TERM_TAB,50)&&test_press(a,A_TERM_ESCAPE,50)&&test_press(a,A_TERM_INTERRUPT,50)&&test_press(a,A_TERM_UP,50)&&test_press(a,A_TERM_DOWN,50)&&test_press(a,A_TERM_LEFT,50)&&test_press(a,A_TERM_RIGHT,50)&&test_press(a,A_KB_ENTER,50));
    TCHECK(a->terminal_input_length==19&&!memcmp(a->terminal_input+3,"\t\033\003\033[A\033[B\033[D\033[C\r",16));
    TCHECK(!a->fs_dirty&&a->fs_cursor==5&&!strcmp(a->fs_text,"untouched editor buffer Ґ"));
    terminal_pump(a);TCHECK(a->job.pid&&a->job.kind==J_TERM_WRITE);
    /* Leaving does not lose what was typed: it is delivered once, then the idle shell closes. */
    if(!strcmp(which,"close-during-write")){TCHECK(test_press(a,A_TERM_CLOSE,50)&&a->screen==HOME&&terminal_test_closed(a)&&a->screen==HOME);goto done;}
    TCHECK(terminal_test_settle(a));
    if(!strcmp(which,"bad-ack")){
        TCHECK(a->terminal_paused&&!a->terminal_input_length&&!strcmp(a->status,"TERMINAL_INPUT_UNCONFIRMED"));
        TCHECK(test_press(a,A_TERM_REFRESH,50)&&terminal_test_settle(a)&&!a->terminal_paused&&!a->terminal_input_length);goto close_session;
    }
    TCHECK(!a->terminal_input_length&&!a->terminal_paused&&terminal_test_read(a));
    if(!strcmp(which,"background")){
        /* A running command outlives the page; the page says so, and another page is left alone. */
        int enter=A_KB_ENTER;TCHECK(test_press(a,enter,50));terminal_pump(a);TCHECK(terminal_test_settle(a)&&terminal_test_read(a)&&a->terminal_busy);render(a);
        unsigned rows=a->terminal.history_count;a->screen=RESULT;a->successful=false;copy(a->status,sizeof(a->status),"page status");a->screen=TERMINAL;
        TCHECK(test_press(a,A_TERM_CLOSE,50)&&a->screen==HOME&&a->terminal_left&&terminal_session_open(a));
        a->screen=RESULT;a->dirty=false;
        uint64_t limit=milliseconds()+10000;
        while(!a->terminal_left_busy&&milliseconds()<limit){a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(a->terminal_left_busy&&terminal_session_open(a)&&!a->successful&&!strcmp(a->status,"page status"));
        a->screen=HOME;render(a);
        /* A page refused for it says why, and any other trouble keeps its own words. */
        problem(a,"OPERATION_BUSY","helper words");TCHECK(strstr(a->message,"terminal")!=NULL);problem(a,"OTHER","helper words");TCHECK(!strcmp(a->message,"helper words"));a->screen=HOME;
        /* Out of sight it is looked at once a second, not forty times. */
        TCHECK(a->terminal_next_poll>=milliseconds()+TERMINAL_LEFT_POLL_MS-300);
        /* It ends by itself: the shell closes and its output waits on the page. */
        while(terminal_session_open(a)&&milliseconds()<limit){a->terminal_next_poll=a->terminal_busy?0:a->terminal_next_poll;a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(terminal_test_closed(a)&&a->terminal_resume&&a->terminal.history_count>=rows);render(a);
        TCHECK(terminal_test_visit(a));terminal_pump(a);TCHECK(terminal_test_settle(a)&&terminal_session_open(a)&&!a->terminal_resume&&a->terminal.history_count>=rows);
        TCHECK(terminal_test_read(a)&&a->terminal.history_count>rows);goto close_session;
    }
    if(!strcmp(which,"reopen-closing")){
        /* Opened again while the idle shell closes: one Stop, then a new shell, no page in between. */
        TCHECK(test_press(a,A_TERM_CLOSE,50)&&a->screen==HOME);
        for(unsigned n=0;n<4000&&!(a->job.pid&&a->job.kind==J_TERM_STOP);n++){a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(a->job.pid&&a->job.kind==J_TERM_STOP);action(a,A_TERMINAL);TCHECK(a->queued_action==A_TERMINAL&&a->screen==HOME);
        for(unsigned n=0;n<8000&&a->screen!=TERMINAL;n++){a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(a->screen==TERMINAL);
        for(unsigned n=0;n<8000&&(a->job.pid||a->terminal_pending||!terminal_session_open(a));n++){a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(terminal_session_open(a)&&!strcmp(a->terminal_state,"active")&&!a->terminal_left);goto close_session;
    }
    /* EOF is a real byte; exited state still has a session, and leaving closes it. */
    TCHECK(test_press(a,A_TERM_EOF,50));terminal_pump(a);TCHECK(terminal_test_settle(a)&&!strcmp(a->terminal_state,"exited")&&terminal_session_open(a));
    TCHECK(!test_press(a,A_KB_ENTER,50)&&terminal_test_read(a));
    if(!strcmp(which,"stale-stop")){
        /* The read is refused out of sight, nothing on the next page changes, and status finds the real session. */
        memset(a->terminal_session,'0',64);a->terminal_session[64]=0;copy(a->status,sizeof(a->status),"page status");
        TCHECK(test_press(a,A_TERM_CLOSE,50)&&a->screen==HOME&&a->terminal_left);
        for(unsigned n=0;n<4000&&(a->job.pid||!a->terminal_pending);n++){a->now=milliseconds();poll_job(a);terminal_pump(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        TCHECK(a->terminal_pending==J_TERM_STATUS&&a->terminal_pending_after>milliseconds()&&a->screen==HOME&&!strcmp(a->status,"page status")&&terminal_session_open(a));
        a->terminal_pending_after=0;TCHECK(terminal_test_closed(a)&&a->screen==HOME);goto done;
    }
close_session:
    /* A lock or the power menu only covers the page: the shell stays as it is. */
    {enum Screen shown=a->screen;a->screen=LOCKSCREEN;a->terminal_next_poll=0;terminal_pump(a);TCHECK(!a->job.pid&&!a->terminal_close_requested&&!a->terminal_left&&terminal_session_open(a));a->screen=shown;}
    /* Leaving an idle shell closes it, with no page asking. */
    TCHECK(test_press(a,A_TERM_CLOSE,50)&&a->screen==HOME&&a->terminal_left&&!a->terminal_left_busy);
    render(a);TCHECK(terminal_test_closed(a)&&a->screen==HOME&&!a->terminal_resume);
    TCHECK(!a->terminal_input_length&&!a->terminal.history_count&&a->terminal_seq==0);
done:
    free(a->pixels);free(a);printf("{\"ok\":true,\"case\":\"%s\",\"checks\":%u,\"helper_double\":true,\"device_io\":false,\"pty_io\":false}\n",which,checks);return 0;
}

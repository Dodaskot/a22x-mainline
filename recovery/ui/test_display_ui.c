/* SPDX-License-Identifier: MIT — real UI input and child transport; no device I/O. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main

#define DCHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"display-ui check %u line %d failed: %s; screen=%d blank=%d blocked=%d uncertain=%d note=%s\n",checks,__LINE__,#expr,a->screen,a->display_blanked,a->display_input_blocked,a->display_uncertain,a->display_light_note);goto failed;}}while(0)
static bool display_test_settle(App*a){
    uint64_t deadline=milliseconds()+12000;
    while(a->display_control.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);poll_display_control(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
    return !a->display_control.pid;
}
static void display_test_power(App*a,unsigned int hold){uint64_t at=milliseconds();a->now=at;key_event(a,KEY_POWER,1);a->now=at+hold;key_event(a,KEY_POWER,0);}
static void display_test_stop(App*a){
    if(a->display_control.pid){(void)kill(a->display_control.pid,SIGKILL);(void)waitpid(a->display_control.pid,NULL,0);if(!a->display_control.out_eof)close(a->display_control.fdout);if(!a->display_control.err_eof)close(a->display_control.fderr);}
    if(a->job.pid){(void)kill(a->job.pid,SIGKILL);(void)waitpid(a->job.pid,NULL,0);if(!a->job.out_eof)close(a->job.fdout);if(!a->job.err_eof)close(a->job.fderr);}
    free(a->job.output);free(a->pixels);free(a);
}
static bool display_test_json(App*a,const char*text_value){JsonToken tokens[128];Json doc;return json_parse(&doc,text_value,tokens,128)>0&&parse_display_light(a,&doc);}
static ThemeFile*theme_file_in_use(void){for(unsigned int n=0;n<theme_file_count;n++)if(&theme_files[n].theme==ui_theme)return &theme_files[n];return NULL;}
static int theme_index(const char*code){for(int n=0;n<(int)theme_file_count;n++)if(!strcmp(theme_files[n].header.code,code))return n;return -1;}
int main(int argc,char**argv){
    if(argc!=5)return 2;
    unsigned int checks=0;const char*which=argv[3];App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    /* 2026-11-10: the check for a plan from the Android app runs once from hotplug_tick() and starts
       its helper; these display cases do not cover it, so it counts as done (lock-dark met it). */
    a->aplan_asked=true;
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->display_helper,sizeof(a->display_helper),argv[2]);copy(a->python,sizeof(a->python),argv[4]);
    char path[PATH_LEN],number[24];snprintf(path,sizeof(path),"%s/display-inherited-fd-sentinel",argv[1]);int original=open(path,O_RDWR|O_CREAT|O_EXCL,0600);int sentinel=original>=0?fcntl(original,F_DUPFD,128):-1;if(original>=0)close(original);
    DCHECK(sentinel>=128);snprintf(number,sizeof(number),"%d",sentinel);DCHECK(setenv("A22X_RECOVERY_LOCK_FD",number,1)==0&&setenv("A22X_DISPLAY_FIXTURE_SENTINEL_FD",number,1)==0);
    screen(a,HOME);a->display_input_blocked=true;DCHECK(start_display_control(a,DC_STATUS,0)&&display_test_settle(a));
    if(!strcmp(which,"malformed")){
        DCHECK(!a->display_light_known&&!a->display_brightness_supported&&!a->display_blank_supported&&!a->display_input_blocked);
        screen(a,DISPLAY_SETTINGS);render(a);DCHECK(!display_slider_usable(a)&&!test_button(a,A_DISPLAY_BRIGHTNESS)->enabled);goto done;
    }
    DCHECK(a->display_light_known&&!a->display_control.pid&&!a->job.pid&&a->screen==HOME);
    if(!strcmp(which,"unavailable")){
        DCHECK(!a->display_brightness_supported&&!a->display_blank_supported&&!a->display_blank_known&&!a->display_input_blocked);
        display_test_power(a,100);DCHECK(!a->display_control.pid&&!a->display_input_blocked);
        screen(a,DISPLAY_SETTINGS);render(a);DCHECK(!display_slider_usable(a)&&!test_button(a,A_DISPLAY_BRIGHTNESS)->enabled);goto done;
    }
    DCHECK(a->display_brightness_supported&&a->display_blank_supported&&a->display_blank_known&&a->display_max==255&&a->display_last_percent==64);
    if(!strcmp(which,"startup-blank")){
        DCHECK(a->display_blanked&&a->display_input_blocked&&a->display_percent==0);
        render(a);pointer(a,true,188,506,100);DCHECK(a->pointer_requires_release&&!a->pressed_action&&!a->pointer_down);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&!a->display_input_blocked&&a->display_percent==64);
        pointer(a,true,188,506,300);pointer(a,false,188,506,400);DCHECK(a->screen==HOME&&!a->job.pid);
        DCHECK(test_press(a,A_BACKUP,50)&&a->screen==BACKUP);goto done;
    }
    DCHECK(!a->display_blanked&&!a->display_input_blocked&&a->display_percent==64);
    if(!strcmp(which,"stick-choice")){
        /* Any stick may be the one inserted now, so a remembered choice never opens it for writing. */
        JsonToken tokens[64];Json doc;char stick[PATH_LEN],folder[PATH_LEN+16],listing[PATH_LEN+128];
        snprintf(stick,sizeof(stick),"%.900s/stick",argv[1]);snprintf(folder,sizeof(folder),"%s/A22xBackups",stick);DCHECK(mkdir(stick,0700)==0&&mkdir(folder,0700)==0);
        copy(a->repositories[0],sizeof(a->repositories[0]),"/mnt/backup/A22xBackups");a->repository_base=a->repository_count=1;a->storage_prefers_usb=true;
        snprintf(listing,sizeof(listing),"{\"volumes\":[{\"id\":\"usb-p1\",\"fs_type\":\"exfat\",\"mountpoint\":\"%s\",\"read_only\":true}]}",stick);
        DCHECK(json_parse(&doc,listing,tokens,64)>0);usb_repositories(a,&doc);DCHECK(a->repository_count==2&&a->store==0&&repository_needs_setup(a,1)&&!a->job.pid);
        snprintf(listing,sizeof(listing),"{\"volumes\":[{\"id\":\"usb-p1\",\"fs_type\":\"exfat\",\"mountpoint\":\"%s\",\"read_only\":false}]}",stick);
        DCHECK(json_parse(&doc,listing,tokens,64)>0);usb_repositories(a,&doc);DCHECK(a->store==1&&!a->job.pid);
        a->device=true;a->store=0;copy(a->repositories[0],sizeof(a->repositories[0]),folder);DCHECK(destination_ready(a)&&a->screen==HOME);
        copy(a->repositories[0],sizeof(a->repositories[0]),"/nonexistent/A22xBackups");DCHECK(!destination_ready(a)&&a->screen==STORAGE&&!strcmp(a->toast,"Choose storage first"));a->device=false;a->store=1;
        DCHECK(json_parse(&doc,"{\"volumes\":[]}",tokens,64)>0);complete_usb_eject(a,&doc,true);
        DCHECK(a->repository_count==1&&a->store==0&&!strcmp(a->toast,"You can remove the USB stick now"));goto done;
    }
    if(!strcmp(which,"password")){
        /* The whole path through the real helper: setting, changing and removing need the old password. */
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(settings_helper!=NULL);screen(a,TOOLS);
        #define ENTER(value) do{copy(a->fs_prompt,sizeof(a->fs_prompt),value);a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);}while(0)
        action(a,A_LOCK_SET);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_LOCK_SET&&!a->lock_enabled);
        ENTER("123");DCHECK(a->fs_prompt_kind==J_LOCK_SET&&a->lock_note[0]);
        ENTER("1234");DCHECK(a->fs_prompt_kind==J_LOCK_CONFIRM&&!a->fs_prompt[0]);
        ENTER("9999");DCHECK(a->fs_prompt_kind==J_LOCK_SET&&!strcmp(a->lock_note,"The passwords do not match")&&!a->lock_enabled);
        ENTER("1234");ENTER("1234");DCHECK(a->lock_enabled&&a->screen==TOOLS&&!a->lock_new[0]&&!a->lock_old[0]);
        DCHECK(!lock_call(a,"verify","1234",NULL)&&lock_call(a,"verify","0000",NULL)==1);
        /* Changing asks for the old one first and a wrong one goes no further. */
        action(a,A_LOCK_SET);DCHECK(a->fs_prompt_kind==J_LOCK_OLD);ENTER("0000");DCHECK(a->fs_prompt_kind==J_LOCK_OLD&&!strcmp(a->lock_note,"Wrong password"));
        ENTER("1234");DCHECK(a->fs_prompt_kind==J_LOCK_SET);ENTER("abcd");ENTER("abcd");DCHECK(a->screen==TOOLS);
        DCHECK(!lock_call(a,"verify","abcd",NULL)&&lock_call(a,"verify","1234",NULL)==1);
        /* The helper itself refuses without the old password, whatever the page does. */
        DCHECK(lock_call(a,"set-password","hack",NULL)==1&&lock_call(a,"clear-password",NULL,NULL)==1&&!lock_call(a,"verify","abcd",NULL));
        /* Leaving a flow half way changes nothing and leaves no secret behind. */
        action(a,A_LOCK_SET);ENTER("abcd");DCHECK(a->fs_prompt_kind==J_LOCK_SET&&a->lock_old[0]);action(a,A_BACK);DCHECK(a->screen==TOOLS&&!a->lock_old[0]&&a->lock_flow==LF_NONE);
        /* The lock mode and computer access are protected the same way. */
        a->lock_mode=2;action(a,A_LOCK_MODE);DCHECK(a->fs_prompt_kind==J_LOCK_OLD&&a->lock_mode==2);ENTER("nope");DCHECK(a->lock_mode==2);ENTER("abcd");DCHECK(a->lock_mode==0&&a->screen==TOOLS);
        a->access_mode=1;action(a,A_ACCESS_TOGGLE);ENTER("abcd");DCHECK(a->access_mode==0);
        action(a,A_LOCK_CLEAR);DCHECK(a->fs_prompt_kind==J_LOCK_OLD);ENTER("nope");DCHECK(a->lock_enabled);ENTER("abcd");DCHECK(!a->lock_enabled&&a->screen==TOOLS);
        DCHECK(!lock_call(a,"verify","anything",NULL));goto done;
    }
    if(!strcmp(which,"lock-guard")){
        /* While locked there is no way in but the password, whatever else happens. */
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(settings_helper!=NULL);DCHECK(lock_call(a,"set-password","1234",NULL)%3==0);
        a->lock_enabled=true;a->lock_mode=2;screen(a,TOOLS);render(a);lock_show(a);render(a);
        DCHECK(a->locked&&a->screen==LOCKSCREEN&&a->lock_return==TOOLS&&a->lock_backdrop);
        screen(a,HOME);DCHECK(a->screen==LOCKSCREEN);action(a,A_TOOLS);action(a,A_BACKUP);action(a,A_BACK);action(a,A_LOCK_CLEAR);DCHECK(a->screen==LOCKSCREEN&&a->lock_enabled);
        problem(a,"X","Y");DCHECK(a->screen==LOCKSCREEN&&a->lock_return==RESULT);
        files_problem(a,"X","Y",TOOLS);DCHECK(a->screen==LOCKSCREEN);
        a->fs_prompt_kind=J_FS_RENAME;screen(a,FILE_PROMPT);DCHECK(a->screen==LOCKSCREEN);a->fs_prompt_kind=0;
        /* A tap or a short drag is not an unlock request. */
        uint64_t at=milliseconds();pointer(a,true,360,1000,at);pointer(a,false,360,1000,at+50);DCHECK(a->screen==LOCKSCREEN);
        pointer(a,true,360,1000,at+100);pointer(a,true,360,900,at+120);pointer(a,false,360,900,at+140);DCHECK(a->screen==LOCKSCREEN&&!a->lock_drag);
        /* A swipe up opens the password page, still locked. */
        pointer(a,true,360,1000,at+200);pointer(a,true,360,700,at+220);DCHECK(a->lock_drag==300);pointer(a,false,360,700,at+240);
        DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_LOCK_ENTER&&a->locked);render(a);
        /* The keyboard works while an operation runs, or a lock during a backup could not open. */
        a->job.pid=1;a->job.kind=J_BACKUP;DCHECK(keyboard_enabled(a));render(a);DCHECK(test_button(a,A_KB_BACKSPACE)->enabled&&!test_button(a,A_JOB_CANCEL));
        action(a,A_KB_KEY+0);DCHECK(a->fs_prompt[0]);action(a,A_KB_BACKSPACE);DCHECK(!a->fs_prompt[0]);a->job.pid=0;
        /* Back returns to the lock screen, not past it. */
        action(a,A_BACK);DCHECK(a->screen==LOCKSCREEN&&a->locked);
        pointer(a,true,360,1000,at+300);pointer(a,true,360,600,at+320);pointer(a,false,360,600,at+340);DCHECK(a->screen==FILE_PROMPT);
        /* Wrong passwords: the fifth one starts a wait, and during the wait even the right one is ignored. */
        for(int n=0;n<5;n++){copy(a->fs_prompt,sizeof(a->fs_prompt),"0000");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);DCHECK(a->locked);}
        DCHECK(a->lock_failures==5&&a->lock_wait_until>a->now);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");action(a,A_FS_PROMPT_NEXT);DCHECK(a->locked);
        a->lock_wait_until=0;copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);
        /* The right one opens what was asked for during the lock. */
        DCHECK(!a->locked&&a->screen==RESULT&&!a->lock_failures&&!a->fs_prompt[0]);goto done;
    }
    if(!strcmp(which,"lock-keeps-text")){
        /* Text being typed into a file prompt comes back after the lock. */
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(lock_call(a,"set-password","1234",NULL)%3==0);a->lock_enabled=true;
        a->fs_prompt_kind=J_FS_RENAME;copy(a->fs_prompt,sizeof(a->fs_prompt),"draft");screen(a,FILE_PROMPT);render(a);lock_show(a);DCHECK(a->screen==LOCKSCREEN&&!a->fs_prompt[0]);
        lock_prompt(a,J_LOCK_ENTER);copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);
        DCHECK(!a->locked&&a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_FS_RENAME&&!strcmp(a->fs_prompt,"draft"));goto done;
    }
    if(!strcmp(which,"access")){
        /* A computer waits; only the password on the phone lets it in. */
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(lock_call(a,"set-password","1234",NULL)%3==0);a->lock_enabled=true;a->access_mode=1;
        char base[PATH_LEN],path[PATH_LEN+64];snprintf(base,sizeof(base),"%.900s/access",argv[1]);DCHECK(mkdir(base,0700)==0);access_dir=base;
        #define REQUEST(id) do{snprintf(path,sizeof(path),"%s/request-" id ".json",base);FILE*f=fopen(path,"w");DCHECK(f!=NULL);fputs("{\"id\":\"" id "\",\"channel\":\"ssh\",\"peer\":\"172.16.42.2\"}",f);fclose(f);}while(0)
        #define POLL() do{a->now+=500;access_poll(a);}while(0)
        screen(a,TOOLS);a->now=1000;POLL();DCHECK(a->screen==TOOLS);
        REQUEST("aa");POLL();DCHECK(a->screen==ACCESS_REQUEST&&!strcmp(a->access_id,"aa")&&!strcmp(a->access_channel,"ssh")&&!strcmp(a->access_peer,"172.16.42.2"));render(a);
        /* A refusal tells the computer and goes back. */
        action(a,A_ACCESS_DENY);snprintf(path,sizeof(path),"%s/denied-aa.json",base);DCHECK(!access(path,F_OK)&&a->screen==TOOLS&&!a->access_id[0]);
        (void)unlink(path);snprintf(path,sizeof(path),"%s/request-aa.json",base);(void)unlink(path);
        /* If the computer gives up, the page goes away by itself. */
        REQUEST("bb");POLL();DCHECK(a->screen==ACCESS_REQUEST);(void)unlink(path);POLL();DCHECK(a->screen==TOOLS&&!a->access_id[0]);
        /* A wrong password keeps it out; the right one lets it in. */
        REQUEST("cc");POLL();action(a,A_ACCESS_ALLOW);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_LOCK_ACCESS);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"0000");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);char grant[PATH_LEN+16];snprintf(grant,sizeof(grant),"%s/granted",base);DCHECK(access(grant,F_OK)!=0&&a->screen==FILE_PROMPT);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);DCHECK(!access(grant,F_OK)&&a->screen==TOOLS);(void)unlink(path);
        /* A finished operation cannot hide a waiting request. */
        (void)unlink(grant);REQUEST("dd");a->now+=500;POLL();DCHECK(a->screen==ACCESS_REQUEST);a->locked=false;problem(a,"X","Y");DCHECK(a->screen==RESULT);POLL();DCHECK(a->screen==ACCESS_REQUEST);
        action(a,A_ACCESS_DENY);DCHECK(a->screen==RESULT);(void)unlink(path);snprintf(path,sizeof(path),"%s/denied-dd.json",base);(void)unlink(path);
        /* Locking takes the permission back, and a request shows over the lock screen without opening it. */
        access_file("granted");screen(a,HOME);render(a);lock_show(a);DCHECK(access(grant,F_OK)!=0);
        REQUEST("ee");POLL();DCHECK(a->screen==ACCESS_REQUEST&&a->locked);action(a,A_ACCESS_DENY);DCHECK(a->screen==LOCKSCREEN&&a->locked);goto done;
    }
    if(!strcmp(which,"adb")){
        /* 2026-11-10: a new ADB key waits for the phone; each answer writes its file, a computer
           that gives up closes the page, a locked phone does not show it, and a strange id names nothing. */
        char base[PATH_LEN],path[PATH_LEN+64];snprintf(base,sizeof(base),"%.900s/adb",argv[1]);DCHECK(mkdir(base,0700)==0);adb_dir=base;
        #define ADB_FILE(id) do{snprintf(path,sizeof(path),"%s/request-" id ".json",base);FILE*f=fopen(path,"w");DCHECK(f!=NULL);fputs("{\"id\":\"" id "\",\"fingerprint\":\"SHA256:abc\",\"comment\":\"user@pc\",\"peer\":\"172.16.42.2:5000\"}",f);fclose(f);}while(0)
        #define ADB_POLL() do{a->now+=500;adb_poll(a);}while(0)
        screen(a,TOOLS);a->now=1000;ADB_POLL();DCHECK(a->screen==TOOLS);
        ADB_FILE("aa11");ADB_POLL();DCHECK(a->screen==ADB_REQUEST&&!strcmp(a->adb_id,"aa11")&&!strcmp(a->adb_fingerprint,"SHA256:abc")&&!strcmp(a->adb_comment,"user@pc"));render(a);
        action(a,A_ADB_ALLOW);snprintf(path,sizeof(path),"%s/allowed-aa11",base);DCHECK(!access(path,F_OK)&&a->screen==TOOLS&&!a->adb_id[0]);
        ADB_POLL();DCHECK(a->screen==TOOLS); /* not asked twice before adbd removes it */
        (void)unlink(path);snprintf(path,sizeof(path),"%s/request-aa11.json",base);(void)unlink(path);
        ADB_FILE("bb22");ADB_POLL();DCHECK(a->screen==ADB_REQUEST);action(a,A_ADB_DENY);snprintf(path,sizeof(path),"%s/denied-bb22",base);DCHECK(!access(path,F_OK)&&a->screen==TOOLS);
        (void)unlink(path);snprintf(path,sizeof(path),"%s/request-bb22.json",base);(void)unlink(path);
        ADB_FILE("cc33");ADB_POLL();DCHECK(a->screen==ADB_REQUEST);(void)unlink(path);ADB_POLL();DCHECK(a->screen==TOOLS&&!a->adb_id[0]);
        a->locked=true;ADB_FILE("dd44");ADB_POLL();DCHECK(a->screen!=ADB_REQUEST);a->locked=false;(void)unlink(path);
        snprintf(path,sizeof(path),"%s/request-x.json",base);{FILE*f=fopen(path,"w");DCHECK(f!=NULL);fputs("{\"id\":\"../x\"}",f);fclose(f);}ADB_POLL();DCHECK(a->screen==TOOLS&&!a->adb_id[0]);
        goto done;
    }
    if(!strcmp(which,"animation-styles")){
        size_t bytes=(size_t)NW*NH*sizeof(uint32_t);a->tr.from=malloc(bytes);a->tr.to=malloc(bytes);DCHECK(a->tr.from&&a->tr.to);a->animate=true;
        screen(a,HOME);render(a);Rect tile=test_button(a,A_STORAGE)->r;
        static const struct{unsigned int style;enum TransitionKind forward,back;bool blur;}styles[]={{ANIM_ZOOM,TR_OPEN,TR_CLOSE,false},{ANIM_BLUR,TR_OPEN,TR_CLOSE,true},{ANIM_SLIDE,TR_SLIDE,TR_SLIDE_BACK,false},{ANIM_FADE,TR_FADE,TR_FADE,false},{ANIM_DISSOLVE,TR_DISSOLVE,TR_DISSOLVE,false}};
        for(unsigned int n=0;n<sizeof(styles)/sizeof(styles[0]);n++){
            a->animation=styles[n].style;a->tr_depth=0;a->pressed_rect=tile;a->pressed_glyph=A_STORAGE;tap_action(a,A_STORAGE);
            DCHECK(a->screen==STORAGE&&a->tr.armed&&a->tr.kind==styles[n].forward&&a->tr.blur==styles[n].blur);
            transition_start(a);transition_compose(a,1.f);DCHECK(!memcmp(a->pixels,a->tr.to,bytes));transition_compose(a,.5f);DCHECK(memcmp(a->pixels,a->tr.to,bytes)&&memcmp(a->pixels,a->tr.from,bytes));
            /* Zoom with blur is told from the plain zoom at a glance (2026-11-10, on the phone: it could
               not be). A third of the way in, the page behind is its frosted copy and the page itself is
               still in the frost; the plain zoom shows two sharp pages and mixes only the edge between. */
            if(styles[n].forward==TR_OPEN){
                size_t mixed=0;transition_compose(a,.35f);for(size_t k=0;k<(size_t)NW*NH;k++)mixed+=a->pixels[k]!=a->tr.from[k]&&a->pixels[k]!=a->tr.to[k];
                if(styles[n].blur)DCHECK(mixed>(size_t)NW*NH/4&&a->pixels[20]==a->tr.soft_from[20]&&a->tr.duration==TRANSITION_BLUR_OPEN_MS);
                else DCHECK(mixed<(size_t)NW*NH/20&&a->tr.duration==TRANSITION_OPEN_MS);
            }
            a->tr.active=false;render(a);tap_action(a,A_BACK);DCHECK(a->screen==HOME&&a->tr.armed&&a->tr.kind==styles[n].back);
            transition_start(a);transition_compose(a,1.f);DCHECK(a->pixels[20]==a->tr.to[20]&&a->pixels[(size_t)2300*NW+1000]==a->tr.to[(size_t)2300*NW+1000]);
            /* Closing is the same film backwards: half way, the page that returns is still frosted. */
            if(styles[n].back==TR_CLOSE){
                size_t mixed=0;transition_compose(a,.5f);for(size_t k=0;k<(size_t)NW*NH;k++)mixed+=a->pixels[k]!=a->tr.from[k]&&a->pixels[k]!=a->tr.to[k];
                if(styles[n].blur)DCHECK(mixed>(size_t)NW*NH/4&&a->tr.duration==TRANSITION_BLUR_BACK_MS);else DCHECK(mixed<(size_t)NW*NH/20);
            }
            a->tr.active=false;render(a);
        }
        /* Off means no animation of any kind, the theme circle included. */
        a->animation=ANIM_OFF;a->pressed_rect=tile;tap_action(a,A_STORAGE);DCHECK(a->screen==STORAGE&&!a->tr.armed);render(a);tap_action(a,A_BACK);DCHECK(!a->tr.armed);
        screen(a,THEME);render(a);a->pressed_rect=test_button(a,A_THEME_ITEM+1)->r;tap_action(a,A_THEME_ITEM+1);DCHECK(!a->tr.armed);goto done;
    }
    if(!strcmp(which,"pad")){
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(settings_helper!=NULL);
        /* A new password starts on the digits. Keys type where they are drawn; the side keys erase or leave. */
        screen(a,TOOLS);render(a);action(a,A_LOCK_SET);render(a);
        DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_LOCK_SET&&!a->lock_keyboard&&a->keyboard_count==10&&test_button(a,A_LOCK_KEYBOARD)&&!test_button(a,A_KB_SHIFT)&&!test_button(a,A_FS_LEFT));
        DCHECK(test_button(a,A_BACK)&&!test_button(a,A_KB_BACKSPACE)&&!test_button(a,A_FS_PROMPT_NEXT)->enabled);
        for(int n=0;n<4;n++){DCHECK(test_press(a,A_KB_KEY+n,40));render(a);}
        DCHECK(!strcmp(a->fs_prompt,"1234")&&test_button(a,A_KB_BACKSPACE)&&test_button(a,A_FS_PROMPT_NEXT)->enabled&&!strcmp(lock_button(a),"Next"));
        DCHECK(test_press(a,A_KB_KEY+9,40));DCHECK(!strcmp(a->fs_prompt,"12340"));render(a);DCHECK(test_press(a,A_KB_BACKSPACE,40));DCHECK(!strcmp(a->fs_prompt,"1234"));render(a);
        /* The full keyboard is one tap away and what was typed stays. */
        DCHECK(test_press(a,A_LOCK_KEYBOARD,40));render(a);DCHECK(a->lock_keyboard&&test_button(a,A_KB_SHIFT)&&!strcmp(a->fs_prompt,"1234")&&a->keyboard_count>30);
        DCHECK(test_press(a,A_LOCK_KEYBOARD,40));render(a);DCHECK(!a->lock_keyboard&&a->keyboard_count==10);
        /* A short password is refused; the page says so and stays. */
        copy(a->fs_prompt,sizeof(a->fs_prompt),"12");action(a,A_FS_PROMPT_NEXT);DCHECK(a->fs_prompt_kind==J_LOCK_SET&&a->lock_note[0]);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");action(a,A_FS_PROMPT_NEXT);DCHECK(a->fs_prompt_kind==J_LOCK_CONFIRM&&!strcmp(lock_button(a),"Set password"));
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");action(a,A_FS_PROMPT_NEXT);DCHECK(a->lock_enabled&&a->lock_digits&&a->screen==TOOLS);
        /* A digit password is asked on the pad, and each button says what it will do. */
        lock_prompt(a,J_LOCK_ENTER);DCHECK(!a->lock_keyboard&&!strcmp(lock_button(a),"Unlock"));lock_prompt(a,J_LOCK_ACCESS);DCHECK(!strcmp(lock_button(a),"Allow"));
        a->lock_flow=LF_REMOVE;lock_prompt(a,J_LOCK_OLD);DCHECK(!strcmp(lock_button(a),"Remove password"));a->lock_flow=LF_MODE;DCHECK(!strcmp(lock_button(a),"Confirm"));
        lock_done(a);action(a,A_LOCK_SET);DCHECK(a->fs_prompt_kind==J_LOCK_OLD&&!strcmp(lock_button(a),"Next"));
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);DCHECK(a->fs_prompt_kind==J_LOCK_SET&&!a->lock_keyboard);
        /* A password with letters is repeated on the keyboard it was typed on, and asked there from then on. */
        action(a,A_LOCK_KEYBOARD);copy(a->fs_prompt,sizeof(a->fs_prompt),"ab12");action(a,A_FS_PROMPT_NEXT);DCHECK(a->fs_prompt_kind==J_LOCK_CONFIRM&&a->lock_keyboard&&!strcmp(lock_button(a),"Change password"));
        copy(a->fs_prompt,sizeof(a->fs_prompt),"ab12");action(a,A_FS_PROMPT_NEXT);DCHECK(a->lock_enabled&&!a->lock_digits&&a->screen==TOOLS);
        lock_prompt(a,J_LOCK_ENTER);DCHECK(a->lock_keyboard);render(a);DCHECK(test_button(a,A_KB_SHIFT)&&test_button(a,A_LOCK_KEYBOARD));
        /* While locked the switch still works; it opens nothing. */
        a->locked=true;action(a,A_LOCK_KEYBOARD);DCHECK(!a->lock_keyboard&&a->locked&&a->screen==FILE_PROMPT);render(a);a->locked=false;goto done;
    }
    if(!strcmp(which,"settings-retry")){
        /* While a terminal holds the storage lease a save stays in RAM. It is repeated until it is on disk. */
        settings_helper=getenv("A22X_TEST_FLAKY_SETTINGS");DCHECK(settings_helper!=NULL);a->device=true;a->now=milliseconds();
        #define SETTLE() do{for(int n=0;n<4000&&a->settings_pid;n++){settings_poll(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}}while(0)
        settings_changed(a);settings_flush(a);DCHECK(!a->settings_pid&&a->settings_dirty); /* Not before the delay. */
        a->now+=SETTINGS_DELAY_MS;settings_flush(a);DCHECK(a->settings_pid&&!a->settings_dirty);SETTLE();
        DCHECK(!a->settings_pid&&a->settings_dirty&&a->settings_retry_at==a->now+SETTINGS_RETRY_MS);
        settings_flush(a);DCHECK(!a->settings_pid); /* Not at once either. */
        a->now+=SETTINGS_RETRY_MS;settings_flush(a);DCHECK(a->settings_pid);SETTLE();DCHECK(a->settings_dirty);
        a->now+=SETTINGS_RETRY_MS;settings_flush(a);DCHECK(a->settings_pid);SETTLE();DCHECK(!a->settings_dirty&&!a->settings_pid);
        a->now+=2*SETTINGS_RETRY_MS;settings_flush(a);DCHECK(!a->settings_pid);a->device=false;goto done;
    }
    if(!strcmp(which,"vblank-stays")){
        /* A panel that is off refuses the wait. That must not end vsync for the rest of the boot. */
        Display d;memset(&d,0,sizeof(d));d.drm=open("/dev/null",O_RDONLY|O_CLOEXEC);DCHECK(d.drm>0);wait_vblank(&d);DCHECK(d.drm>0);close(d.drm);d.drm=99;wait_vblank(&d);DCHECK(d.drm==-1);goto done;
    }
    if(!strcmp(which,"lock-dark")){
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(lock_call(a,"set-password","1234",NULL)%3==0);a->lock_enabled=true;a->lock_mode=2;a->locked=false;
        /* The screen going out locks, and the frame is ready before the screen comes back. */
        screen(a,TOOLS);render(a);a->display_blank_known=a->display_blanked=true;hotplug_tick(a);DCHECK(a->locked&&a->screen==LOCKSCREEN&&a->lock_frame_pending);a->lock_frame_pending=false;
        /* A password half typed does not survive the dark: waking shows the lock screen. */
        a->display_blanked=false;lock_prompt(a,J_LOCK_ENTER);copy(a->fs_prompt,sizeof(a->fs_prompt),"12");copy(a->lock_note,sizeof(a->lock_note),"Wrong password");
        hotplug_tick(a);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt[0]);
        a->display_blanked=true;hotplug_tick(a);DCHECK(a->screen==LOCKSCREEN&&!a->fs_prompt[0]&&!a->lock_note[0]&&a->lock_frame_pending&&a->locked);a->lock_frame_pending=false;
        /* A computer waiting for permission is still asked after the dark. */
        a->display_blanked=false;screen(a,ACCESS_REQUEST);lock_prompt(a,J_LOCK_ACCESS);a->display_blanked=true;hotplug_tick(a);DCHECK(a->screen==ACCESS_REQUEST&&a->locked);a->display_blanked=false;
        /* A new password being typed is dropped, and unlocking opens Settings. */
        a->locked=false;a->lock_flow=LF_SET;lock_prompt(a,J_LOCK_SET);copy(a->fs_prompt,sizeof(a->fs_prompt),"9999");render(a);a->display_blanked=true;hotplug_tick(a);
        DCHECK(a->locked&&a->lock_return==TOOLS&&!a->fs_prompt[0]&&!a->fs_prompt_kind);a->lock_frame_pending=false;a->display_blanked=false;
        lock_prompt(a,J_LOCK_ENTER);copy(a->fs_prompt,sizeof(a->fs_prompt),"1234");a->now=milliseconds();action(a,A_FS_PROMPT_NEXT);DCHECK(!a->locked&&a->screen==TOOLS);goto done;
    }
    if(!strcmp(which,"account-page")){
        /* Name and password on one page; the password never leaves its buffer as text. */
        screen(a,SYSTEMS_SETUP);action(a,A_SYS_ACCOUNT_NEW);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_SYS_ACCOUNT_USER&&!a->fs_prompt[0]);
        render(a);DCHECK(test_button(a,A_SYS_FIELD_USER)&&test_button(a,A_SYS_FIELD_PASS));Button*next=test_button(a,A_FS_PROMPT_NEXT);DCHECK(next&&!next->enabled);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"olena");a->prompt_cursor=5;action(a,A_FS_PROMPT_NEXT);
        DCHECK(a->fs_prompt_kind==J_SYS_ACCOUNT_PASS&&!strcmp(a->systems_username,"olena")&&!a->fs_prompt[0]);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"a'b c");action(a,A_SYS_FIELD_USER);
        DCHECK(a->fs_prompt_kind==J_SYS_ACCOUNT_USER&&!strcmp(a->fs_prompt,"olena")&&!strcmp(a->systems_password,"a'b c")&&a->prompt_cursor==5);
        action(a,A_SYS_FIELD_PASS);DCHECK(!strcmp(a->fs_prompt,"a'b c"));render(a);
        action(a,A_FS_PROMPT_NEXT);DCHECK(a->screen==SYSTEMS_SETUP&&a->systems_new_account&&!strcmp(a->systems_password,"a'b c")&&!a->fs_prompt[0]);
        /* Cancelling a new, unconfirmed account leaves nothing behind; a confirmed one stays. */
        action(a,A_SYS_ACCOUNT_NEW);DCHECK(!strcmp(a->fs_prompt,"olena"));action(a,A_BACK);DCHECK(a->screen==SYSTEMS_SETUP&&a->systems_new_account&&!strcmp(a->systems_username,"olena"));
        action(a,A_SYS_ACCOUNT_KEEP);action(a,A_SYS_ACCOUNT_NEW);copy(a->fs_prompt,sizeof(a->fs_prompt),"temp");action(a,A_BACK);
        DCHECK(!a->systems_new_account&&!a->systems_username[0]&&!a->systems_password[0]);
        goto done;
    }
    if(!strcmp(which,"clean-install")){
        /* 2026-11-08: a formatted userdata gets a clean install: a new account is required,
           Wi-Fi is typed in recovery, apps are a choice, and nothing is planned without a network. */
        a->systems_known=a->systems_mounted=a->systems_platform=true;a->systems_empty=true;copy(a->systems_volume_token,sizeof(a->systems_volume_token),"userdata-token");
        screen(a,SYSTEMS);render(a);Button*install=test_button(a,A_SYS_IMPORT);DCHECK(install&&install->enabled);DCHECK(!test_button(a,A_SYS_MOUNT));
        a->systems_desktop=2;a->systems_new_account=true;copy(a->systems_username,sizeof(a->systems_username),"user");screen(a,SYSTEMS_SETUP);render(a);
        Button*keep=test_button(a,A_SYS_ACCOUNT_KEEP);DCHECK(keep&&!keep->enabled);Button*next=test_button(a,A_SYS_SETUP_NEXT);DCHECK(next&&!next->enabled);
        action(a,A_SYS_SETUP_NEXT);DCHECK(a->screen==SYSTEMS_SETUP);
        /* A desktop leads through the shells and sign-in page (2026-11-09). */
        copy(a->systems_password,sizeof(a->systems_password),"246810");action(a,A_SYS_SETUP_NEXT);DCHECK(a->screen==SYSTEMS_SHELLS);
        action(a,A_SHELLS_NEXT);DCHECK(a->screen==SYSTEMS_EXTRAS);
        render(a);Button*review=test_button(a,A_SYS_SETUP_NEXT);DCHECK(review&&!review->enabled);
        action(a,A_SYS_APP+7);action(a,A_SYS_APP+0);action(a,A_SYS_APP+7);DCHECK(a->systems_apps==1u);
        /* The network page keeps the name aside while the password is typed; a short password is not taken. */
        action(a,A_SYS_WIFI);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_SYS_WIFI_SSID);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"Дім 5G");action(a,A_FS_PROMPT_NEXT);DCHECK(a->fs_prompt_kind==J_SYS_WIFI_PASS&&!strcmp(a->systems_wifi_ssid,"Дім 5G"));
        copy(a->fs_prompt,sizeof(a->fs_prompt),"short");render(a);Button*done=test_button(a,A_FS_PROMPT_NEXT);DCHECK(done&&!done->enabled);
        action(a,A_FS_PROMPT_NEXT);DCHECK(a->screen==FILE_PROMPT);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"long enough");action(a,A_FS_PROMPT_NEXT);
        DCHECK(a->screen==SYSTEMS_EXTRAS&&!strcmp(a->systems_wifi_password,"long enough")&&!a->fs_prompt[0]);
        /* Cancel puts back what was there before the page opened. */
        action(a,A_SYS_WIFI);copy(a->fs_prompt,sizeof(a->fs_prompt),"Other");action(a,A_BACK);
        DCHECK(a->screen==SYSTEMS_EXTRAS&&!strcmp(a->systems_wifi_ssid,"Дім 5G")&&!strcmp(a->systems_wifi_password,"long enough"));
        render(a);review=test_button(a,A_SYS_SETUP_NEXT);DCHECK(review&&review->enabled);
        action(a,A_SYS_WIFI_CLEAR);DCHECK(!a->systems_wifi_ssid[0]&&!a->systems_wifi_password[0]);
        /* An open network is a name without a password. */
        action(a,A_SYS_WIFI);copy(a->fs_prompt,sizeof(a->fs_prompt),"cafe");action(a,A_FS_PROMPT_NEXT);action(a,A_FS_PROMPT_NEXT);
        DCHECK(a->screen==SYSTEMS_EXTRAS&&!strcmp(a->systems_wifi_ssid,"cafe")&&!a->systems_wifi_password[0]);
        /* Choosing a script goes through the file picker and comes back here. */
        action(a,A_SYS_SCRIPT);DCHECK(a->systems_pick_manifest&&a->systems_pick_script&&a->screen==FILE_VOLUMES);
        if(a->job.pid){(void)kill(a->job.pid,SIGKILL);(void)waitpid(a->job.pid,NULL,0);if(!a->job.out_eof)close(a->job.fdout);if(!a->job.err_eof)close(a->job.fderr);a->job.pid=0;a->job.kind=J_NONE;}
        action(a,A_SYS_CANCEL_PICK);DCHECK(a->screen==SYSTEMS_EXTRAS&&!a->systems_pick_manifest&&!a->systems_pick_script);
        action(a,A_BACK);DCHECK(a->screen==SYSTEMS_SHELLS);
        action(a,A_BACK);DCHECK(a->screen==SYSTEMS_SETUP);
        goto done;
    }
    if(!strcmp(which,"format-done")){
        /* 2026-11-08: a finished format reads the volumes again instead of staying on the progress page. */
        static const char result[]="{\"ok\":true,\"operation\":\"format\",\"formatted\":\"ext4\"}";Json doc;JsonToken tokens[16];
        DCHECK(json_parse(&doc,result,tokens,16)>0);a->screen=PROGRESS;
        DCHECK(complete_files_job(a,&doc,J_FS_FORMAT)==J_FS_INVENTORY&&a->fs_inventory_destination==FILE_VOLUME&&!strcmp(a->fs_status,"Formatted"));
        goto done;
    }
    if(!strcmp(which,"check-done")){
        /* 2026-11-09: a check reports clean or repaired, then reads the volumes again. */
        static const char repaired[]="{\"ok\":true,\"operation\":\"check\",\"checked\":\"ext4\",\"result\":\"repaired\",\"exit_code\":1}";
        static const char clean[]="{\"ok\":true,\"operation\":\"check\",\"checked\":\"ext4\",\"result\":\"clean\",\"exit_code\":0}";
        Json doc;JsonToken tokens[16];
        DCHECK(json_parse(&doc,repaired,tokens,16)>0);a->screen=PROGRESS;
        DCHECK(complete_files_job(a,&doc,J_FS_CHECK)==J_FS_INVENTORY&&a->fs_inventory_destination==FILE_VOLUME&&!strcmp(a->fs_status,"Check: errors repaired"));
        DCHECK(json_parse(&doc,clean,tokens,16)>0);a->screen=PROGRESS;
        DCHECK(complete_files_job(a,&doc,J_FS_CHECK)==J_FS_INVENTORY&&!strcmp(a->fs_status,"Check: no errors found"));
        static const char grown[]="{\"ok\":true,\"operation\":\"resize\",\"result\":\"grown\",\"bytes_after\":1073741824}";
        static const char same[]="{\"ok\":true,\"operation\":\"resize\",\"result\":\"unchanged\",\"bytes_after\":1073741824}";
        DCHECK(json_parse(&doc,grown,tokens,16)>0);a->screen=PROGRESS;
        DCHECK(complete_files_job(a,&doc,J_FS_RESIZE)==J_FS_INVENTORY&&a->fs_inventory_destination==FILE_VOLUME&&!strcmp(a->fs_status,"Grown to 1.0 GiB"));
        DCHECK(json_parse(&doc,same,tokens,16)>0);a->screen=PROGRESS;
        DCHECK(complete_files_job(a,&doc,J_FS_RESIZE)==J_FS_INVENTORY&&!strcmp(a->fs_status,"Already fills the partition: 1.0 GiB"));
        DCHECK(file_job(J_FS_RESIZE)&&!strcmp(filesystem_verb(J_FS_RESIZE),"resize")&&!strcmp(job_name(J_FS_RESIZE),"files-resize"));
        DCHECK(file_job(J_FS_CHECK)&&!strcmp(filesystem_verb(J_FS_CHECK),"check")&&!strcmp(job_name(J_FS_CHECK),"files-check")
               &&!strcmp(job_name(J_FS_FORMAT),"files-format")&&!strcmp(job_name(J_TOOLS_STATUS),"diagnostic-status")&&!strcmp(job_name(J_USB_EJECT),"usb-eject"));
        goto done;
    }
    if(!strcmp(which,"wifi")){
        /* 2026-11-08: recovery joins a visible network: list, password page, state, errors. */
        static const char scan[]="{\"ok\":true,\"operation\":\"scan\",\"available\":true,\"on\":true,\"connected\":false,\"ssid\":\"\",\"ssid_hex\":\"\",\"address\":\"\",\"networks\":["
            "{\"ssid\":\"Home\",\"ssid_hex\":\"486f6d65\",\"bars\":4,\"band\":\"5 GHz\",\"security\":\"WPA2\",\"secure\":true,\"supported\":true,\"connected\":false},"
            "{\"ssid\":\"Cafe\",\"ssid_hex\":\"43616665\",\"bars\":2,\"band\":\"2.4 GHz\",\"security\":\"Open\",\"secure\":false,\"supported\":true,\"connected\":false},"
            "{\"ssid\":\"Lab\",\"ssid_hex\":\"4c6162\",\"bars\":1,\"band\":\"5 GHz\",\"security\":\"WPA3\",\"secure\":true,\"supported\":false,\"connected\":false},"
            "{\"ssid\":\"Office\",\"ssid_hex\":\"4f6666696365\",\"bars\":3,\"band\":\"5 GHz\",\"security\":\"WPA2\",\"secure\":true,\"supported\":true,\"connected\":false,\"saved\":true}]}";
        static const char joined[]="{\"ok\":true,\"operation\":\"connect\",\"available\":true,\"on\":true,\"connected\":true,\"ssid\":\"Home\",\"ssid_hex\":\"486f6d65\",\"address\":\"10.42.0.23\"}";
        static const char refused[]="{\"ok\":false,\"operation\":\"connect\",\"error\":{\"code\":\"WIFI_AUTH_FAILED\",\"message\":\"The password is wrong.\"}}";
        Json doc;JsonToken tokens[128];a->wifi_helper[0]=0;
        screen(a,TOOLS);render(a);DCHECK(test_button(a,A_WIFI));
        screen(a,WIFI);DCHECK(json_parse(&doc,scan,tokens,128)>0&&complete_wifi_job(a,&doc,J_WIFI_SCAN,true)==0);
        DCHECK(a->wifi_count==4&&a->wifi_on&&!a->wifi_connected&&!strcmp(a->wifi_nets[0].hex,"486f6d65")&&a->wifi_nets[3].saved);
        render(a);Button*b=test_button(a,A_WIFI_NET+0);DCHECK(b&&b->enabled);b=test_button(a,A_WIFI_NET+2);DCHECK(b&&!b->enabled);
        /* A remembered network joins without the password page; a key that stopped working asks again. */
        action(a,A_WIFI_NET+3);DCHECK(a->screen==WIFI&&a->wifi_edit_saved&&!strcmp(a->wifi_edit_hex,"4f6666696365"));
        DCHECK(json_parse(&doc,refused,tokens,128)>0&&complete_wifi_job(a,&doc,J_WIFI_CONNECT,false)==0);
        DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_WIFI_PASS&&!a->wifi_edit_saved&&!strcmp(a->wifi_edit_ssid,"Office"));
        action(a,A_BACK);DCHECK(a->screen==WIFI);a->wifi_error[0]=0;
        action(a,A_WIFI_NET+0);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_WIFI_PASS&&a->wifi_edit_fixed&&!strcmp(a->wifi_edit_ssid,"Home"));
        action(a,A_WIFI_FIELD_SSID);DCHECK(a->fs_prompt_kind==J_WIFI_PASS);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"short");render(a);b=test_button(a,A_FS_PROMPT_NEXT);DCHECK(b&&!b->enabled);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"correct horse");render(a);b=test_button(a,A_FS_PROMPT_NEXT);DCHECK(b&&b->enabled);
        action(a,A_FS_PROMPT_NEXT);DCHECK(a->screen==WIFI&&!strcmp(a->wifi_edit_password,"correct horse")&&!a->fs_prompt[0]);
        DCHECK(json_parse(&doc,joined,tokens,128)>0&&complete_wifi_job(a,&doc,J_WIFI_CONNECT,true)==J_WIFI_SCAN);
        DCHECK(a->wifi_connected&&!strcmp(a->wifi_ssid,"Home")&&!strcmp(a->wifi_password,"correct horse")&&!a->wifi_edit_password[0]);
        render(a);DCHECK(test_button(a,A_WIFI_DISCONNECT)&&test_button(a,A_WIFI_SCAN)&&!test_button(a,A_WIFI_FORGET));
        a->wifi_nets[0].connected=a->wifi_nets[0].saved=true;render(a);DCHECK(test_button(a,A_WIFI_FORGET));
        DCHECK(json_parse(&doc,refused,tokens,128)>0&&complete_wifi_job(a,&doc,J_WIFI_CONNECT,false)==0&&!strcmp(a->wifi_error,"The password is wrong."));
        action(a,A_WIFI_HIDDEN);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_WIFI_SSID&&!a->wifi_edit_fixed);
        action(a,A_BACK);DCHECK(a->screen==WIFI);action(a,A_BACK);DCHECK(a->screen==TOOLS);
        /* The home page: Wi-Fi and theme at hand, the browser waits for its program. Back returns home. */
        screen(a,HOME);render(a);DCHECK(test_button(a,A_WIFI)&&test_button(a,A_HOME_THEME)&&test_button(a,A_HOME_CATALOG));
        action(a,A_HOME_THEME);DCHECK(a->screen==THEME);action(a,A_BACK);DCHECK(a->screen==HOME);
        screen(a,WIFI);a->wifi_from_home=true;action(a,A_BACK);DCHECK(a->screen==HOME);
        goto done;
    }
    if(!strcmp(which,"packages")){
        /* 2026-11-08: 29 apps on their own page, and packages found in the distribution's repositories. */
        static const char found[]="{\"ok\":true,\"family\":\"debian\",\"query\":\"keepass\",\"results\":["
            "{\"name\":\"keepassxc\",\"version\":\"2.7.10\",\"summary\":\"Password manager\",\"available\":true},"
            "{\"name\":\"keepass2\",\"version\":\"2.57\",\"summary\":\"Password manager\",\"available\":false},"
            "{\"name\":\"kpcli\",\"version\":\"\",\"summary\":\"Command line\",\"available\":null}]}";
        Json doc;JsonToken tokens[128];a->distro_helper[0]=0;
        CatalogImage*c=&a->catalog[0];memset(c,0,sizeof(*c));a->catalog_count=1;copy(c->id,sizeof(c->id),"debian-13");copy(c->name,sizeof(c->name),"Debian");
        copy(c->version,sizeof(c->version),"13 trixie");copy(c->family,sizeof(c->family),"debian");copy(a->systems_remote_id,sizeof(a->systems_remote_id),"debian-13");
        screen(a,SYSTEMS_EXTRAS);render(a);DCHECK(test_button(a,A_APPS_OPEN)&&test_button(a,A_PKG_OPEN));
        action(a,A_APPS_OPEN);DCHECK(a->screen==SYSTEMS_APP_LIST);render(a);DCHECK(test_button(a,A_SYS_APP+28));
        action(a,A_SYS_APP+22);DCHECK((a->systems_apps>>22)&1u);action(a,A_APPS_DONE);DCHECK(a->screen==SYSTEMS_EXTRAS);
        action(a,A_PKG_OPEN);DCHECK(a->screen==SYSTEMS_SEARCH);render(a);DCHECK(test_button(a,A_PKG_QUERY)&&!test_button(a,A_PKG_FAMILY+0));
        char family[24],release[24],label[112];DCHECK(pkg_target(a,family,sizeof(family),release,sizeof(release),label,sizeof(label))&&!strcmp(family,"debian")&&!strcmp(release,"trixie"));
        action(a,A_PKG_QUERY);DCHECK(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_PKG_QUERY);render(a);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"k");action(a,A_FS_PROMPT_NEXT);DCHECK(a->screen==SYSTEMS_SEARCH&&a->pkg_error[0]&&!a->pkg_query[0]);
        action(a,A_PKG_QUERY);copy(a->fs_prompt,sizeof(a->fs_prompt),"keepass");action(a,A_FS_PROMPT_NEXT);DCHECK(a->screen==SYSTEMS_SEARCH&&!strcmp(a->pkg_query,"keepass"));
        DCHECK(json_parse(&doc,found,tokens,128)>0);complete_package_search(a,&doc,true);DCHECK(a->pkg_count==3&&a->pkg_results[0].available==1&&a->pkg_results[1].available==0&&a->pkg_results[2].available==-1);
        render(a);Button*b=test_button(a,A_PKG_RESULT+1);DCHECK(b&&!b->enabled);
        action(a,A_PKG_RESULT+0);action(a,A_PKG_RESULT+1);action(a,A_PKG_RESULT+2);DCHECK(a->systems_package_count==2&&pkg_chosen(a,"keepassxc")&&pkg_chosen(a,"kpcli")&&!pkg_chosen(a,"keepass2"));
        action(a,A_PKG_RESULT+2);DCHECK(a->systems_package_count==1);DCHECK(systems_needs_network(a));
        /* Many results scroll under the finger instead of pages (2026-11-08). */
        for(unsigned int n=0;n<PKG_RESULTS;n++){PkgResult*r=&a->pkg_results[n];snprintf(r->name,sizeof(r->name),"package-%02u",n);r->available=1;}
        a->pkg_count=PKG_RESULTS;a->page_scroll=0;render(a);DCHECK(page_max_scroll(a)>0&&test_button(a,A_PKG_RESULT+0)&&!test_button(a,A_PKG_RESULT+PKG_RESULTS-1));
        page_scroll_to(a,INT64_MAX);render(a);DCHECK(test_button(a,A_PKG_RESULT+PKG_RESULTS-1)&&test_button(a,A_PKG_DONE));
        a->pkg_count=0;
        action(a,A_PKG_DONE);DCHECK(a->screen==SYSTEMS_EXTRAS);
        /* An image source card that is not mounted can be chosen: it is mounted read-only. */
        a->volume_count=1;FileVolume*v=&a->volumes[0];memset(v,0,sizeof(*v));copy(v->id,sizeof(v->id),"sdcard-p1");copy(v->token,sizeof(v->token),"t");v->mountable=true;
        a->systems_pick_manifest=true;screen(a,FILE_VOLUMES);render(a);b=test_button(a,A_FS_VOLUME_ITEM+0);DCHECK(b&&b->enabled);
        copy(v->id,sizeof(v->id),"userdata");render(a);b=test_button(a,A_FS_VOLUME_ITEM+0);DCHECK(b&&!b->enabled);a->systems_pick_manifest=false;a->volume_count=0;screen(a,SYSTEMS_EXTRAS);
        /* A file can be any distribution: the family is chosen on the page. */
        a->systems_remote_id[0]=0;action(a,A_PKG_OPEN);render(a);DCHECK(test_button(a,A_PKG_FAMILY+2));action(a,A_PKG_FAMILY+2);
        DCHECK(!pkg_target(a,family,sizeof(family),release,sizeof(release),label,sizeof(label))&&!strcmp(family,"fedora"));
        goto done;
    }
    if(!strcmp(which,"installer")){
        /* 2026-11-08: a new system from the catalog erases userdata and takes the usual install pages. */
        static const char listing[]="{\"ok\":true,\"https\":false,\"network\":true,\"images\":["
            "{\"id\":\"debian-13\",\"name\":\"Debian\",\"version\":\"13\",\"family\":\"debian\",\"file\":\"debian.qcow2\",\"size\":337641472,\"minimal\":true,\"reachable\":true},"
            "{\"id\":\"fedora-45\",\"name\":\"Fedora Cloud Base\",\"version\":\"45\",\"family\":\"fedora\",\"file\":\"fedora.qcow2\",\"size\":596705280,\"minimal\":true,\"reachable\":false}]}";
        Json doc;JsonToken tokens[96];a->distro_helper[0]=0;
        a->systems_known=a->systems_platform=a->systems_mounted=true;a->systems_read_only=false;a->systems_empty=false;
        screen(a,SYSTEMS);render(a);DCHECK(test_button(a,A_SYS_NEW));
        action(a,A_SYS_NEW);DCHECK(a->screen==SYSTEMS_NEW);render(a);DCHECK(test_button(a,A_NEW_INTERNET)&&test_button(a,A_NEW_FILE));
        action(a,A_NEW_INTERNET);DCHECK(a->screen==SYSTEMS_CATALOG);
        DCHECK(json_parse(&doc,listing,tokens,96)>0);complete_catalog_job(a,&doc,true,false);DCHECK(a->catalog_count==2&&a->catalog_network&&!a->catalog_https);
        render(a);Button*b=test_button(a,A_CATALOG_ITEM+1);DCHECK(b&&!b->enabled);b=test_button(a,A_CATALOG_ITEM+0);DCHECK(b&&b->enabled);
        /* No pages: every image is in one list. */
        DCHECK(!test_button(a,A_CATALOG_NEXT)&&!test_button(a,A_CATALOG_PREV));
        action(a,A_CATALOG_FAMILY+1);DCHECK(a->catalog_family==1&&catalog_visible(a)==1);render(a);DCHECK(!test_button(a,A_CATALOG_ITEM+0)&&!test_button(a,A_CATALOG_FAMILY+4)&&test_button(a,A_CATALOG_FAMILY+2));
        action(a,A_CATALOG_FAMILY+0);action(a,A_CATALOG_ITEM+1);DCHECK(a->screen==SYSTEMS_CATALOG);
        /* Recovery joined "Test" from its saved list: the plan takes the saved key, not an open network. */
        a->wifi_connected=true;copy(a->wifi_ssid,sizeof(a->wifi_ssid),"Test");a->wifi_password[0]=0;
        action(a,A_CATALOG_ITEM+0);DCHECK(a->screen==SYSTEMS_SETUP&&!strcmp(a->systems_remote_id,"debian-13")&&a->systems_wipe&&a->systems_new_account&&!strcmp(a->systems_manifest,"debian.qcow2"));
        DCHECK(!strcmp(a->systems_wifi_ssid,"Test")&&!a->systems_wifi_password[0]&&a->systems_wifi_saved);
        screen(a,SYSTEMS_CATALOG);copy(a->wifi_password,sizeof(a->wifi_password),"secret12");action(a,A_CATALOG_ITEM+0);
        DCHECK(!strcmp(a->systems_wifi_password,"secret12")&&!a->systems_wifi_saved);a->wifi_connected=false;a->wifi_password[0]=0;
        /* 2026-11-09: Import offers the catalog too; an image chosen there is added beside, keeping the account. */
        screen(a,SYSTEMS);action(a,A_SYS_IMPORT);DCHECK(a->screen==SYSTEMS_NEW&&a->systems_adding);render(a);DCHECK(test_button(a,A_NEW_INTERNET)&&test_button(a,A_NEW_FILE));
        action(a,A_NEW_INTERNET);DCHECK(a->screen==SYSTEMS_CATALOG);DCHECK(json_parse(&doc,listing,tokens,96)>0);complete_catalog_job(a,&doc,true,false);
        action(a,A_CATALOG_ITEM+0);DCHECK(a->screen==SYSTEMS_SETUP&&!strcmp(a->systems_remote_id,"debian-13")&&!a->systems_wipe&&!a->systems_new_account);
        screen(a,SYSTEMS);action(a,A_SYS_NEW);DCHECK(a->screen==SYSTEMS_NEW&&!a->systems_adding);
        /* Offline with a remembered network in range: it is offered with its saved key. */
        {unsigned int keep=a->wifi_count;WifiNet old=a->wifi_nets[0];memset(&a->wifi_nets[0],0,sizeof(WifiNet));copy(a->wifi_nets[0].ssid,sizeof(a->wifi_nets[0].ssid),"Home AP");
         a->wifi_nets[0].saved=a->wifi_nets[0].supported=true;a->wifi_count=1;screen(a,SYSTEMS_CATALOG);action(a,A_CATALOG_ITEM+0);
         DCHECK(!strcmp(a->systems_wifi_ssid,"Home AP")&&a->systems_wifi_saved&&!a->systems_wifi_password[0]);
         a->wifi_nets[0].saved=false;systems_forget_choices(a);screen(a,SYSTEMS_CATALOG);action(a,A_CATALOG_ITEM+0);DCHECK(!a->systems_wifi_ssid[0]);
         a->wifi_nets[0]=old;a->wifi_count=keep;}
        render(a);b=test_button(a,A_SYS_ACCOUNT_KEEP);DCHECK(b&&!b->enabled);
        /* 2026-11-08: with userdata not mounted, New system's Mount button and a catalog image open the mount sheet. */
        a->systems_mounted=false;copy(a->systems_volume_token,sizeof(a->systems_volume_token),"userdata-token");
        screen(a,SYSTEMS_NEW);render(a);b=test_button(a,A_SYS_MOUNT);DCHECK(b&&b->enabled);action(a,A_SYS_MOUNT);DCHECK(a->systems_mount_sheet&&a->screen==SYSTEMS_NEW);
        render(a);DCHECK(test_button(a,A_SYS_MOUNT_RW)&&test_button(a,A_SYS_MOUNT_RO));action(a,A_SYS_MOUNT_CANCEL);DCHECK(!a->systems_mount_sheet);
        screen(a,SYSTEMS_CATALOG);action(a,A_CATALOG_ITEM+0);DCHECK(a->screen==SYSTEMS_CATALOG&&a->systems_mount_sheet&&a->catalog_pending==1);
        render(a);DCHECK(test_button(a,A_SYS_MOUNT_RW));action(a,A_BACK);DCHECK(!a->systems_mount_sheet&&a->catalog_pending==0&&a->screen==SYSTEMS_CATALOG);
        a->systems_mounted=true;
        screen(a,SYSTEMS_CATALOG);action(a,A_BACK);DCHECK(a->screen==SYSTEMS_NEW);action(a,A_BACK);DCHECK(a->screen==SYSTEMS);
        action(a,A_SYS_IMPORT);DCHECK(!a->systems_remote_id[0]&&!a->systems_wipe);await_job(a);
        screen(a,HOME);render(a);DCHECK(test_button(a,A_HOME_CATALOG));action(a,A_HOME_CATALOG);DCHECK(a->screen==SYSTEMS_CATALOG);
        goto done;
    }
    if(!strcmp(which,"systems-mount")){
        /* 2026-11-08: tapping the current-system card asks how to mount userdata. */
        a->systems_known=a->systems_platform=true;a->systems_mounted=false;copy(a->systems_volume_token,sizeof(a->systems_volume_token),"userdata-token");
        screen(a,SYSTEMS);render(a);DCHECK(test_button(a,A_SYS_MOUNT));action(a,A_SYS_MOUNT);DCHECK(a->systems_mount_sheet);
        render(a);DCHECK(test_button(a,A_SYS_MOUNT_RW)&&test_button(a,A_SYS_MOUNT_RO)&&!test_button(a,A_SYS_IMPORT));
        action(a,A_BACK);DCHECK(!a->systems_mount_sheet&&a->screen==SYSTEMS);
        a->systems_mounted=a->systems_read_only=true;action(a,A_SYS_MOUNT);render(a);DCHECK(test_button(a,A_SYS_MOUNT_RW)&&!test_button(a,A_SYS_MOUNT_RO));
        action(a,A_SYS_MOUNT_CANCEL);DCHECK(!a->systems_mount_sheet);
        a->systems_read_only=false;render(a);DCHECK(!test_button(a,A_SYS_MOUNT));
        goto done;
    }
    if(!strcmp(which,"restore-image")){
        /* Restore lets the helper name the partition, and accepts only userdata or BOOT from it. */
        const char*hex="51e89bf2746f00592082347074d48010f4be8ebe4c8aa4d27142d04c51ca35ce";char reply[1400];JsonToken tok[256];Json j;
        a->flash_restore=true;a->flash_label[0]=0;DCHECK(!strcmp(flash_target_label(a),"auto")&&flash_home(a)==LIST);
        snprintf(reply,sizeof(reply),"{\"ok\":true,\"operation\":\"check\",\"target\":{\"label\":\"recovery\",\"size_bytes\":41943040,\"offline\":true},\"image\":{\"name\":\"r.img\",\"kind\":\"Raw image\",\"sha256\":\"%s\",\"identity\":\"%s\",\"file_bytes\":1},\"identical\":false,\"warnings\":[]}",hex,hex);
        DCHECK(json_parse(&j,reply,tok,256)>0);complete_flash_job(a,&j,J_FLASH_CHECK,true);DCHECK(a->screen==RESULT&&a->result_return==LIST&&!a->flash_review_valid);
        snprintf(reply,sizeof(reply),"{\"ok\":true,\"operation\":\"check\",\"target\":{\"label\":\"userdata\",\"size_bytes\":53393489920,\"offline\":false},\"image\":{\"name\":\"userdata.img.zst\",\"kind\":\"ext4 filesystem image\",\"sha256\":\"%s\",\"identity\":\"%s\",\"file_bytes\":3456532659,\"checksum_file\":\"userdata.img.zst.transport.json\"},\"identical\":false,\"warnings\":[\"Everything on userdata is replaced by this image\",\"The partition is mounted. Unmount it in Files before writing\"]}",hex,hex);
        DCHECK(json_parse(&j,reply,tok,256)>0);complete_flash_job(a,&j,J_FLASH_CHECK,true);
        DCHECK(a->screen==FLASH_REVIEW&&a->flash_review_valid&&!strcmp(a->flash_label,"userdata")&&!strcmp(flash_target_label(a),"userdata")&&!a->flash_offline);
        DCHECK(!strcmp(a->flash_checksum,"userdata.img.zst.transport.json"));
        /* Mounted: nothing to hold. Unmounted: the hold writes exactly the partition the helper named. */
        render(a);Button*hold=test_button(a,A_FLASH_CONFIRM);DCHECK(hold&&!hold->enabled);
        a->flash_offline=true;render(a);hold=test_button(a,A_FLASH_CONFIRM);DCHECK(hold&&hold->enabled);
        snprintf(reply,sizeof(reply),"{\"ok\":true,\"operation\":\"write\",\"target\":{\"label\":\"userdata\"},\"sha256\":\"%s\",\"verified\":true,\"rollback\":null}",hex);
        DCHECK(json_parse(&j,reply,tok,256)>0);complete_flash_job(a,&j,J_FLASH_WRITE,true);
        DCHECK(a->screen==RESULT&&a->successful&&a->result_return==LIST&&strstr(a->message,"userdata")&&strstr(a->message,"BOOT"));
        render(a);DCHECK(!test_button(a,A_REBOOT_RECOVERY)&&!test_button(a,A_REBOOT));  /* a restore offers no restart */
        /* Back from the review goes to Restore, and Flash a partition forgets the restore mode. */
        a->screen=FLASH_REVIEW;action(a,A_BACK);DCHECK(a->screen==LIST);
        a->flash_restore=true;flash_action(a,A_FLASH);DCHECK(!a->flash_restore&&a->screen==FLASH);a->job.pid=0;a->job.kind=J_NONE;
        /* 2026-11-10: after a flash of the recovery the result offers its restart, through the usual hold. */
        bool power=a->allow_power;a->allow_power=true;a->flash_target_count=1;a->flash_selected=0;
        copy(a->flash_targets[0].label,sizeof(a->flash_targets[0].label),"recovery");copy(a->flash_sha,sizeof(a->flash_sha),hex);
        snprintf(reply,sizeof(reply),"{\"ok\":true,\"operation\":\"write\",\"target\":{\"label\":\"recovery\"},\"sha256\":\"%s\",\"verified\":true,\"rollback\":null}",hex);
        DCHECK(json_parse(&j,reply,tok,256)>0);complete_flash_job(a,&j,J_FLASH_WRITE,true);
        DCHECK(a->screen==RESULT&&a->successful&&a->result_return==FLASH);render(a);
        Button*restart=test_button(a,A_REBOOT_RECOVERY);DCHECK(restart&&restart->enabled&&!test_button(a,A_REBOOT));
        action(a,A_REBOOT_RECOVERY);DCHECK(a->screen==POWER_CONFIRM&&a->power_intent==202&&a->running&&!a->exit_code);
        a->allow_power=power;
        goto done;
    }
    if(!strcmp(which,"pad-round")){
        /* A round key's shadow is round: it fades around the circle and leaves the corners of its square alone. */
        uint32_t*px=a->pixels;Bounds all=canvas_bounds(a);int cx=540,cy=1200,radius=PAD_RADIUS*3/2;
        for(size_t n=0;n<(size_t)NW*NH;n++)px[n]=0xffffff;
        canvas_disc_shadow(px,all,cx,cy,(float)radius,PAD_SHADOW_SPREAD,PAD_SHADOW_DROP,0,200);
        #define AT(x,y) px[(size_t)(y)*NW+(size_t)(x)]
        DCHECK(AT(cx,cy)==0xffffff&&AT(cx,cy+radius-4)==0xffffff);
        DCHECK(AT(cx,cy+radius+4)!=0xffffff&&AT(cx+radius+2,cy)!=0xffffff&&AT(cx-radius-2,cy)!=0xffffff);
        /* Corners of the bounding square, where a card shadow used to show. */
        DCHECK(AT(cx+radius-6,cy+radius-6)==0xffffff&&AT(cx-radius+6,cy+radius-6)==0xffffff&&AT(cx+radius-6,cy-radius+6)==0xffffff);
        /* It falls below the key more than above, and reaches no further than the gap to the next key. */
        DCHECK((AT(cx,cy+radius+8)&0xff)<(AT(cx,cy-radius-8)&0xff));
        DCHECK(AT(cx,cy+radius+(int)PAD_SHADOW_SPREAD+PAD_SHADOW_DROP+2)==0xffffff&&(int)PAD_SHADOW_SPREAD+PAD_SHADOW_DROP<=(PAD_PITCH-2*PAD_RADIUS)*3/2+4);
        #undef AT
        /* The keys come after all the shadows, so none of them falls across a key. */
        a->locked=false;a->fs_prompt_kind=J_LOCK_ENTER;a->screen=FILE_PROMPT;a->fs_prompt[0]=0;render(a);DCHECK(a->keyboard_count==10);
        uint32_t centers[10];for(int n=0;n<10;n++){int column=n<9?n%3:1,row=n<9?n/3:3;centers[n]=px[(size_t)nx(PAD_TOP+row*PAD_PITCH+PAD_RADIUS-6)*NW+(size_t)nx(pad_columns[column])];}
        a->fs_prompt_kind=0;screen(a,HOME);
        for(int n=1;n<10;n++){int difference=abs((int)(centers[n]&0xff)-(int)(centers[0]&0xff));DCHECK(difference<=24);}
        goto done;
    }
    if(!strcmp(which,"pad-dots")){
        /* Only typed digits show as dots, and the keys stand out from the page behind them. */
        a->lock_digits=true;lock_prompt(a,J_LOCK_SET);render(a);size_t bytes=(size_t)NW*NH*sizeof(uint32_t);uint32_t*empty=malloc(bytes);DCHECK(empty!=NULL);memcpy(empty,a->pixels,bytes);
        DCHECK(empty[(size_t)nx(340)*NW+540]==empty[(size_t)nx(300)*NW+540]);
        copy(a->fs_prompt,sizeof(a->fs_prompt),"1");a->prompt_cursor=1;render(a);DCHECK(memcmp(a->pixels,empty,bytes));DCHECK(a->pixels[(size_t)nx(340)*NW+540]==TEXT);free(empty);
        Button*done=test_button(a,A_FS_PROMPT_NEXT);DCHECK(done&&done->r.y>=1400);a->fs_prompt[0]=0;action(a,A_BACK);goto done;
    }
    if(!strcmp(which,"power-menu")){
        a->allow_power=true;screen(a,TOOLS);render(a);a->now=milliseconds();
        /* Held long enough, Power opens the menu and letting go does not touch the screen. */
        key_event(a,KEY_POWER,1);a->now+=POWER_MENU_MS-1;held_tick(a,false);DCHECK(a->screen==TOOLS);a->now+=1;held_tick(a,false);DCHECK(a->screen==POWER_MENU&&a->menu_return==TOOLS&&a->power_held&&a->menu_backdrop);
        render(a);DCHECK(test_button(a,A_SHUTDOWN)&&test_button(a,A_REBOOT)&&test_button(a,A_REBOOT_RECOVERY)&&test_button(a,A_REBOOT_DOWNLOAD)&&test_button(a,A_MENU_CLOSE));
        a->now+=100;key_event(a,KEY_POWER,0);DCHECK(!a->display_control.pid&&!a->power_held&&a->screen==POWER_MENU);
        /* A tap anywhere else leaves it. */
        uint64_t at=milliseconds();pointer(a,true,360,200,at);pointer(a,false,360,200,at+40);DCHECK(a->screen==TOOLS);
        /* While something runs, nothing here can be chosen. */
        render(a);power_menu_open(a);a->job.pid=1;a->job.kind=J_BACKUP;render(a);DCHECK(!test_button(a,A_SHUTDOWN)->enabled);action(a,A_SHUTDOWN);DCHECK(a->running);a->job.pid=0;a->job.kind=J_NONE;
        /* A pending switch keeps the system from booting, the rest stays. */
        a->systems_reboot_blocked=true;render(a);DCHECK(!test_button(a,A_REBOOT)->enabled&&test_button(a,A_SHUTDOWN)->enabled);a->systems_reboot_blocked=false;action(a,A_MENU_CLOSE);
        /* On a locked phone it works too, and closes back onto the lock. */
        settings_helper=getenv("A22X_TEST_SETTINGS");DCHECK(lock_call(a,"set-password","1234",NULL)%3==0);a->lock_enabled=true;render(a);lock_show(a);render(a);power_menu_open(a);DCHECK(a->screen==POWER_MENU&&a->menu_return==LOCKSCREEN&&a->locked);render(a);
        action(a,A_MENU_CLOSE);DCHECK(a->screen==LOCKSCREEN&&a->locked);render(a);power_menu_open(a);render(a);
        action(a,A_REBOOT_RECOVERY);DCHECK(!a->running&&a->exit_code==202);a->running=true;a->exit_code=0;a->locked=false;goto done;
    }
    if(!strcmp(which,"navigation")){
        uint64_t at=milliseconds();a->nav_mode=NAV_GESTURES;a->animate=false;
        #define SWIPE(x0,y0,x1,y1) do{at+=500;pointer(a,true,x0,y0,at);pointer(a,true,((x0)+(x1))/2,((y0)+(y1))/2,at+20);pointer(a,true,x1,y1,at+40);render(a);pointer(a,false,x1,y1,at+60);render(a);}while(0)
        /* From either edge towards the middle: one page back. */
        screen(a,THEME);render(a);SWIPE(6,800,140,804);DCHECK(a->screen==TOOLS&&!a->gesture&&!a->pointer_down);
        screen(a,THEME);render(a);SWIPE(714,800,580,800);DCHECK(a->screen==TOOLS);
        /* A short pull is nothing, and neither is a swipe that starts inside the page. */
        screen(a,THEME);render(a);SWIPE(6,800,60,800);DCHECK(a->screen==THEME);SWIPE(200,800,400,800);DCHECK(a->screen==THEME);
        /* From the bottom up: home from anywhere, and a picker left this way leaves no mode behind. */
        a->theme_pick=THEME;screen(a,FILE_VOLUMES);render(a);SWIPE(360,1590,360,1400);DCHECK(a->screen==HOME&&!a->theme_pick);
        screen(a,THEME);render(a);SWIPE(360,1590,360,1540);DCHECK(a->screen==THEME);
        /* The pull follows the finger, slows down as it nears the limit, and never passes it. */
        DCHECK(!nav_stretch(0,NAV_LIFT_LIMIT)&&!nav_stretch(-40,NAV_LIFT_LIMIT)&&nav_stretch(10,NAV_LIFT_LIMIT)>=9);
        {int last=0,step=1000;for(int d=20;d<=2000;d+=20){int v=nav_stretch(d,NAV_LIFT_LIMIT),gain=v-last;DCHECK(gain>=0&&gain<=step+1&&v<=NAV_LIFT_LIMIT);last=v;step=gain;}DCHECK(last==NAV_LIFT_LIMIT);}
        /* Let go short of the mark, it springs back, fast at first. A page that changes takes it along. */
        screen(a,THEME);render(a);at+=500;pointer(a,true,360,1590,at);pointer(a,true,360,1540,at+20);render(a);int held=nav_amount(a);DCHECK(held>30&&held<=NAV_LIFT_LIMIT);
        pointer(a,false,360,1540,at+40);DCHECK(a->screen==THEME&&a->nav_settle_kind==GESTURE_BOTTOM&&a->nav_settle_amount==held&&a->nav_settle_end==at+40+NAV_SETTLE_MS);
        a->now=at+40;DCHECK(nav_settling(a)==held);a->now=at+40+NAV_SETTLE_MS/2;{int half=nav_settling(a);DCHECK(half>0&&half<held/4);}render(a);a->now=at+40+NAV_SETTLE_MS;DCHECK(!nav_settling(a));
        at+=500;pointer(a,true,6,800,at);pointer(a,true,70,800,at+20);render(a);held=nav_amount(a);DCHECK(held>40&&held<=NAV_PULL_LIMIT);pointer(a,false,70,800,at+40);DCHECK(a->screen==THEME&&a->nav_settle_kind==GESTURE_BACK&&a->nav_settle_amount==held);a->now=at+40+NAV_SETTLE_MS/4;render(a);
        SWIPE(6,800,140,804);DCHECK(a->screen==TOOLS&&!a->nav_settle_end);
        /* Unsaved text is asked about first. */
        a->fs_dirty=true;copy(a->fs_text,sizeof(a->fs_text),"draft");screen(a,FILE_EDIT);render(a);SWIPE(360,1590,360,1400);DCHECK(a->screen==FILE_DISCARD);a->fs_dirty=false;a->fs_text[0]=0;
        /* Up and held: the terminal. */
        screen(a,TOOLS);render(a);at+=500;pointer(a,true,360,1590,at);pointer(a,true,360,1400,at+30);a->now=at+30+NAV_HOLD_MS-1;held_tick(a,false);DCHECK(!a->gesture_terminal);a->now=at+30+NAV_HOLD_MS;held_tick(a,false);DCHECK(a->gesture_terminal);render(a);pointer(a,false,360,1400,at+600);
        DCHECK(a->screen==TERMINAL||a->job.pid);DCHECK(await_job(a));
        /* The gestures rest while the page cannot be left, or while the phone is locked. */
        screen(a,TOOLS);render(a);a->job.pid=1;a->job.kind=J_BACKUP;SWIPE(6,800,140,800);DCHECK(a->screen==TOOLS);a->job.pid=0;a->job.kind=J_NONE;
        a->locked=true;a->screen=LOCKSCREEN;render(a);SWIPE(360,1590,360,1500);DCHECK(a->screen==LOCKSCREEN);a->locked=false;
        /* Buttons: back, home and the terminal at the right. */
        a->nav_mode=NAV_BUTTONS;screen(a,THEME);render(a);DCHECK(test_press(a,A_NAV_BACK,40)&&a->screen==TOOLS);render(a);DCHECK(test_press(a,A_NAV_HOME,40)&&a->screen==HOME);render(a);DCHECK(!test_button(a,A_NAV_BACK)->enabled&&test_button(a,A_NAV_TERMINAL)->r.x>400);
        SWIPE(6,800,140,800);DCHECK(a->screen==HOME);
        /* Off: nothing at the bottom, nothing at the edges, and the arrows of the pages still work. */
        a->nav_mode=NAV_OFF;screen(a,THEME);render(a);DCHECK(!test_button(a,A_NAV_HOME));SWIPE(6,800,140,800);SWIPE(360,1590,360,1400);DCHECK(a->screen==THEME&&test_press(a,A_BACK,40)&&a->screen==TOOLS);
        action(a,A_NAV_MODE+1);DCHECK(a->nav_mode==NAV_BUTTONS&&a->settings_dirty);goto done;
    }
    if(!strcmp(which,"prompt-field")){
        /* A file prompt keeps its field: the pad belongs to passwords only. */
        a->fs_prompt_kind=J_FS_RENAME;copy(a->fs_prompt,sizeof(a->fs_prompt),"name");screen(a,FILE_PROMPT);render(a);
        DCHECK(test_button(a,A_FS_CARET)&&test_button(a,A_KB_SHIFT)&&test_button(a,A_FS_LEFT)&&!test_button(a,A_LOCK_KEYBOARD));goto done;
    }
    if(!strcmp(which,"theme-files")){
        /* Whole files count. The broken ones beside them are not themes and do no harm. */
        const char*token=getenv("A22X_TEST_TOKEN");DCHECK(token&&theme_file_count==3);int beaver=-1,sample=-1,green=-1;
        for(int n=0;n<(int)theme_file_count;n++){const char*code=theme_files[n].header.code;if(!strcmp(code,"beaver"))beaver=n;else if(!strcmp(code,"sample"))sample=n;else if(!strcmp(code,"green-tty"))green=n;}
        DCHECK(beaver>=0&&sample>=0&&green>=0);
        DCHECK(theme_file_ui(&theme_files[beaver])&&theme_file_terminal(&theme_files[beaver])&&theme_files[beaver].thumb&&!theme_file_terminal(&theme_files[sample])&&!theme_files[sample].thumb&&!theme_file_ui(&theme_files[green]));
        a->page_scroll=0;screen(a,THEME);render(a);
        DCHECK(test_button(a,A_THEME_ITEM+THEME_COUNT+beaver)&&test_button(a,A_THEME_ITEM+THEME_COUNT+sample)&&!test_button(a,A_THEME_ITEM+THEME_COUNT+green));
        /* A theme with a picture shows it. A bundled theme has nothing to remove. */
        action(a,A_THEME_ITEM+THEME_COUNT+beaver);DCHECK(ui_theme==&theme_files[beaver].theme&&a->theme_image&&a->backdrop&&ui_theme->bento);render(a);render(a);
        DCHECK(a->backdrop[(size_t)NW*2300+800]!=a->backdrop[(size_t)NW*200+800]);
        page_scroll_to(a,100000);render(a);DCHECK(test_button(a,A_THEME_ADD)&&test_button(a,A_THEME_REMOVE+beaver)&&test_button(a,A_THEME_REMOVE+sample)&&!test_button(a,A_THEME_RESTORE));
        /* A theme without a picture leaves no old picture behind. */
        action(a,A_THEME_ITEM+THEME_COUNT+sample);DCHECK(ui_theme==&theme_files[sample].theme&&!a->theme_image&&!ui_theme->backdrop);render(a);
        /* The list can be read again and what is in use stays in use. */
        theme_reload(a);DCHECK(theme_file_in_use()&&!strcmp(ui_theme->code,"sample")&&a->screen==THEME);
        for(int n=0;n<(int)theme_file_count;n++){const char*code=theme_files[n].header.code;if(!strcmp(code,"beaver"))beaver=n;else if(!strcmp(code,"sample"))sample=n;else if(!strcmp(code,"green-tty"))green=n;}
        /* Terminal colors have their own list, and a theme with colors of its own is in both. */
        a->page_scroll=0;screen(a,TERMINAL_THEME);render(a);DCHECK(test_button(a,A_TERMINAL_ADD)||a->page_content>1254);
        action(a,A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+green);DCHECK(ui_terminal==&theme_files[green].scheme&&terminal_scheme_bg()==0x000000u&&terminal_scheme_fg()==0x33ff33u);
        action(a,A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+beaver);DCHECK(ui_terminal==&theme_files[beaver].scheme&&terminal_scheme_palette()==theme_files[beaver].scheme.palette);
        /* Back in the list: the file picker offers no way to change storage and can be left. */
        a->theme_pick=a->theme_return=THEME;screen(a,FILE_VOLUMES);render(a);action(a,A_THEME_CANCEL_PICK);DCHECK(a->screen==THEME&&!a->theme_pick);
        /* A file from a stick arrives, is chosen and is told about. */
        a->volume_count=1;a->volume_index=0;FileVolume*v=&a->volumes[0];copy(v->id,sizeof(v->id),"usb-p1");copy(v->token,sizeof(v->token),token);copy(a->volume_id,sizeof(a->volume_id),"usb-p1");v->mounted=true;
        memset(&a->fs_selected,0,sizeof(a->fs_selected));copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/fresh.a22xtheme");copy(a->fs_selected.name,sizeof(a->fs_selected.name),"fresh.a22xtheme");copy(a->fs_selected.type,sizeof(a->fs_selected.type),"file");
        a->theme_pick=a->theme_return=THEME;theme_file_chosen(a);DCHECK(a->job.pid&&a->job.kind==J_THEME_IMPORT&&a->screen==PROGRESS);DCHECK(await_job(a));
        DCHECK(a->screen==THEME&&!a->theme_pick&&a->successful&&!strcmp(ui_theme->code,"fresh")&&theme_file_count==4&&!strcmp(a->toast,"Theme added"));
        /* The file manager offers the same for a theme file. */
        screen(a,FILE_INFO);render(a);DCHECK(test_button(a,A_THEME_USE)&&test_button(a,A_FS_READ));
        copy(a->fs_selected.name,sizeof(a->fs_selected.name),"notes.txt");render(a);DCHECK(!test_button(a,A_THEME_USE));
        /* A file that is no theme says so and changes nothing. */
        copy(a->fs_selected.path,sizeof(a->fs_selected.path),"/wrong.a22xtheme");a->theme_pick=a->theme_return=THEME;theme_file_chosen(a);DCHECK(await_job(a));
        DCHECK(a->screen==RESULT&&!a->successful&&!strcmp(a->status,"THEME_NOT_JSON")&&!strcmp(ui_theme->code,"fresh")&&!a->theme_pick);action(a,A_DONE);DCHECK(a->screen==THEME);
        /* Removing what is in use falls back to the first theme. A terminal scheme of the same file goes with it. */
        a->page_scroll=0;screen(a,THEME);render(a);DCHECK(theme_file_in_use()&&!strcmp(theme_file_in_use()->header.code,"fresh"));
        int fresh=theme_index("fresh");a->now=milliseconds();action(a,A_THEME_REMOVE+fresh);DCHECK(!a->job.pid&&a->theme_armed==A_THEME_REMOVE+fresh&&!strcmp(a->toast,"Tap again to remove")&&theme_file_count==4);render(a);
        /* The request runs out, and a tap on another theme asks again. */
        a->now=a->theme_armed_until;action(a,A_THEME_REMOVE+fresh);DCHECK(!a->job.pid&&a->theme_armed==A_THEME_REMOVE+fresh);
        action(a,A_THEME_REMOVE+theme_index("sample"));DCHECK(!a->job.pid&&a->theme_armed==A_THEME_REMOVE+theme_index("sample"));
        action(a,A_THEME_REMOVE+fresh);DCHECK(!a->job.pid);action(a,A_THEME_REMOVE+fresh);DCHECK(a->job.kind==J_THEME_REMOVE&&await_job(a));
        DCHECK(a->screen==THEME&&ui_theme==&themes[0]&&theme_file_count==3&&theme_index("fresh")<0&&!strcmp(a->toast,"Theme removed"));render(a);
        /* A theme of the image goes the same way, and can be asked back. */
        int bundled=theme_index("beaver");action(a,A_THEME_ITEM+THEME_COUNT+bundled);DCHECK(!strcmp(ui_theme->code,"beaver"));a->now=milliseconds();
        action(a,A_THEME_REMOVE+bundled);action(a,A_THEME_REMOVE+bundled);DCHECK(await_job(a));DCHECK(theme_index("beaver")<0&&ui_theme==&themes[0]&&theme_files_hidden&&theme_file_count==2);
        render(a);DCHECK(test_button(a,A_THEME_RESTORE));action(a,A_THEME_RESTORE);DCHECK(await_job(a));DCHECK(theme_index("beaver")>=0&&!theme_files_hidden&&theme_file_count==3&&a->screen==THEME);render(a);
        /* Removed from the terminal list, the colors in use go back to following the theme. */
        int colors=theme_index("green-tty");a->page_scroll=0;screen(a,TERMINAL_THEME);action(a,A_TERMINAL_THEME_ITEM+TERMINAL_SCHEME_COUNT+colors);DCHECK(ui_terminal==&theme_files[colors].scheme);
        a->now=milliseconds();action(a,A_THEME_REMOVE+colors);action(a,A_THEME_REMOVE+colors);DCHECK(await_job(a));DCHECK(ui_terminal==&terminal_schemes[0]&&a->screen==TERMINAL_THEME&&theme_index("green-tty")<0&&!strcmp(a->toast,"Colors removed"));render(a);goto done;
    }
    if(!strcmp(which,"phone-details")){
        /* The way there is on the home page, and the details are read when it opens. */
        screen(a,HOME);render(a);DCHECK(test_button(a,A_SYSTEM));
        action(a,A_SYSTEM);DCHECK(a->screen==SYSTEM&&a->job.pid&&a->job.kind==J_SYSINFO&&ui_free(a));render(a);DCHECK(await_job(a));
        DCHECK(a->screen==SYSTEM&&!strcmp(a->sys_model,"SM-A226B")&&!strcmp(a->sys_name,"Galaxy A22 5G")&&!strcmp(a->sys_version,"0.6.31")&&!strcmp(a->sys_codename,"a22x"));
        DCHECK(a->sys_section_count==6&&a->sys_row_count>20&&a->sys_row_count<=SYS_ROWS&&a->sys_summary_count==4&&!strcmp(a->sys_titles[1],"Processor"));
        render(a);DCHECK(a->page_content>1254);
        /* The page scrolls to its end, where Refresh is. */
        page_scroll_to(a,100000);render(a);DCHECK(a->page_scroll==page_max_scroll(a)&&test_button(a,A_REFRESH));
        /* A read that fails keeps what is on the page. */
        unsigned int rows=a->sys_row_count;sysinfo_helper="/nonexistent/sysinfo.py";DCHECK(start_sysinfo_job(a));DCHECK(await_job(a));DCHECK(a->sys_row_count==rows&&a->screen==SYSTEM);goto done;
    }
    if(!strcmp(which,"blur")){
        /* A frosted frame keeps the picture's colours and leaves no sharp edge to read. */
        size_t bytes=(size_t)NW*NH*sizeof(uint32_t);uint32_t*soft=malloc(bytes);DCHECK(soft!=NULL);
        for(int y=0;y<NH;y++)for(int x=0;x<NW;x++)a->pixels[(size_t)y*NW+(size_t)x]=(x/40+y/40)&1?0xffffffu:0x000000u;
        canvas_blur(soft,a->pixels);unsigned int low=255,high=0;
        for(size_t n=0;n<(size_t)NW*NH;n+=97){unsigned int v=soft[n]&255u;low=v<low?v:low;high=v>high?v:high;}
        DCHECK(low>80&&high<175);
        for(size_t n=0;n<(size_t)NW*NH;n++)a->pixels[n]=0x336699u;
        canvas_blur(soft,a->pixels);DCHECK((soft[12345]>>16)>=0x33&&(soft[12345]>>16)<=0x34&&(soft[12345]&255u)>=0x99&&(soft[12345]&255u)<=0x9a);free(soft);goto done;
    }
    if(!strcmp(which,"transitions")){
        size_t bytes=(size_t)NW*NH*sizeof(uint32_t);a->tr.from=malloc(bytes);a->tr.to=malloc(bytes);DCHECK(a->tr.from&&a->tr.to);a->animate=true;
        /* A tile opens its page out of its own rectangle and glyph. */
        screen(a,HOME);render(a);Button*tile=test_button(a,A_STORAGE);DCHECK(tile&&tile->glyph==A_STORAGE);Rect origin=tile->r;uint64_t at=milliseconds();
        pointer(a,true,origin.x+30,origin.y+30,at);render(a);pointer(a,false,origin.x+30,origin.y+30,at+60);
        DCHECK(a->screen==STORAGE&&a->tr.armed&&a->tr.kind==TR_OPEN&&a->tr.glyph==A_STORAGE&&a->tr.origin.x==origin.x&&a->tr_depth==1);
        transition_start(a);DCHECK(a->tr.active&&!a->tr.armed&&test_button(a,A_BACK));
        /* The first frame is still the old page around the tile, and the last is exactly the new page. */
        transition_compose(a,0.f);DCHECK(a->pixels[10]==a->tr.from[10]&&a->pixels[(size_t)2300*NW+540]==a->tr.from[(size_t)2300*NW+540]);
        transition_compose(a,1.f);DCHECK(!memcmp(a->pixels,a->tr.to,bytes));
        transition_compose(a,.5f);DCHECK(memcmp(a->pixels,a->tr.to,bytes)&&memcmp(a->pixels,a->tr.from,bytes));
        DCHECK(transition_frame(a,a->tr.started+10)&&a->dirty&&a->tr.active);
        /* A clock read just before the start must not count as a finished animation. */
        DCHECK(transition_frame(a,a->tr.started-7)&&a->tr.active);
        DCHECK(!transition_frame(a,a->tr.started+TRANSITION_OPEN_MS+1)&&!a->tr.active);render(a);
        /* Back closes the page into the same tile. */
        Button*back=test_button(a,A_BACK);DCHECK(back);at+=1000;pointer(a,true,back->r.x+10,back->r.y+10,at);render(a);pointer(a,false,back->r.x+10,back->r.y+10,at+60);
        DCHECK(a->screen==HOME&&a->tr.armed&&a->tr.kind==TR_CLOSE&&a->tr.origin.x==origin.x&&a->tr.glyph==A_STORAGE&&!a->tr_depth);
        transition_start(a);transition_compose(a,1.f);DCHECK(a->pixels[10]==a->tr.to[10]);
        /* A touch during the animation lands on the live page at once. */
        at+=1000;pointer(a,true,300,1420,at);DCHECK(!a->tr.active&&!a->tr.armed&&a->dirty);pointer(a,false,300,1420,at+400);render(a);
        /* A new theme grows out of the finger as a circle. */
        a->tr_depth=0;screen(a,THEME);render(a);Button*item=test_button(a,A_THEME_ITEM+1);DCHECK(item);Rect chosen=item->r;int tx=chosen.x+400,ty=chosen.y+40,px=nx(chosen.x+70),py=nx(chosen.y+105);const Theme*before=ui_theme;at+=1000;
        pointer(a,true,tx,ty,at);render(a);pointer(a,false,tx,ty,at+60);
        DCHECK(ui_theme!=before&&a->tr.armed&&a->tr.kind==TR_THEME&&a->tr.cx==px&&a->tr.cy==py&&a->screen==THEME);
        transition_start(a);transition_compose(a,0.f);DCHECK(!memcmp(a->pixels,a->tr.from,bytes));
        transition_compose(a,.5f);DCHECK(memcmp(a->pixels,a->tr.from,bytes)&&memcmp(a->pixels,a->tr.to,bytes));
        DCHECK(a->pixels[(size_t)py*NW+(size_t)px]==a->tr.to[(size_t)py*NW+(size_t)px]);
        transition_compose(a,1.f);DCHECK(!memcmp(a->pixels,a->tr.to,bytes));
        /* Pages that only change themselves do not animate. */
        a->tr.active=a->tr.armed=false;screen(a,HOME);render(a);int before_screen=(int)a->screen;transition_after(a,A_REFRESH,before_screen,ui_theme);DCHECK(!a->tr.armed);goto done;
    }
    if(!strcmp(which,"rate-change")){
        /* While the rate changes nothing on the page may turn grey or jump. */
        a->display_known=a->display_supported=a->display_service=a->display_60=a->display_90=true;a->display_hz=60;screen(a,DISPLAY_SETTINGS);render(a);
        unsigned int moved=0,polls=0;
        DCHECK(!test_button(a,A_DISPLAY_60)->enabled&&test_button(a,A_DISPLAY_90)->enabled&&test_button(a,A_DISPLAY_REFRESH)->enabled);
        action(a,A_DISPLAY_90);DCHECK(a->job.pid&&a->job.kind==J_DISPLAY_SET&&a->display_requested_hz==90&&a->screen==DISPLAY_SETTINGS);
        uint64_t deadline=milliseconds()+12000;
        while(a->job.pid&&milliseconds()<deadline){
            a->now=milliseconds();render(a);polls++;
            Button*fast=test_button(a,A_DISPLAY_90),*slow=test_button(a,A_DISPLAY_60),*refresh=test_button(a,A_DISPLAY_REFRESH);
            DCHECK(fast&&!fast->enabled&&slow&&slow->enabled&&refresh&&refresh->enabled);DCHECK(display_slider_ready(a));DCHECK(a->screen==DISPLAY_SETTINGS);
            moved=a->job.kind==J_DISPLAY_STATUS?moved+1:moved;
            poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);
        }
        DCHECK(!a->job.pid&&a->display_hz==90&&!a->display_requested_hz&&a->screen==DISPLAY_SETTINGS&&polls>0&&moved>0);
        /* Leaving during a change must not be undone when it finishes. */
        action(a,A_DISPLAY_60);DCHECK(a->job.kind==J_DISPLAY_SET);action(a,A_BACK);DCHECK(a->screen==TOOLS);
        deadline=milliseconds()+12000;while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
        DCHECK(!a->job.pid&&a->display_hz==60&&a->screen==TOOLS);goto done;
    }
    if(!strncmp(which,"restore-",8)){
        /* Saved values return without taking the person away from the page they are on. */
        bool tap=!strcmp(which,"restore-tap"),locked=!strcmp(which,"restore-locked"),applied=tap||locked||!strcmp(which,"restore-boot"),skipped=!strcmp(which,"restore-unavailable"),seen=false;
        uint64_t deadline=milliseconds()+12000;if(locked){a->locked=true;lock_prompt(a,J_LOCK_ENTER);}
        enum Screen start=a->screen;a->restore_hz=90;a->restore_brightness=40;
        while((a->restore_hz||a->restore_brightness||a->job.pid||a->display_control.pid)&&milliseconds()<deadline){
            a->now=milliseconds();restore_display(a);
            if(a->display_restoring&&!seen){seen=true;render(a);if(!locked){Button*b=test_button(a,A_BACKUP);DCHECK(b&&b->enabled);}if(tap){action(a,A_BACKUP);DCHECK(a->queued_action==A_BACKUP&&a->screen==HOME);}}
            poll_job(a);poll_display_control(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);
        }
        DCHECK(!a->restore_hz&&!a->restore_brightness&&!a->job.pid&&!a->display_control.pid&&!a->display_restoring);
        DCHECK(a->screen==(tap?BACKUP:start));DCHECK(a->display_percent==40&&!a->display_requested_hz);
        if(applied)DCHECK(a->display_hz==90&&a->display_known);
        DCHECK((applied||skipped)==!a->toast[0]);
        if(locked){DCHECK(a->locked&&a->screen==FILE_PROMPT);goto done;}
        if(!tap){render(a);DCHECK(test_press(a,A_BACKUP,50)&&a->screen==BACKUP);}
        goto done;
    }
    if(!strcmp(which,"busy-backup")){
        char*job_args[]={a->python,"-c","import json,sys,time;print(json.dumps({'event':'progress','phase':'copying','bytes_done':37,'bytes_total':99}),file=sys.stderr,flush=True);time.sleep(2);print(json.dumps({'ok':True}),flush=True)",NULL};
        DCHECK(launch_job(a,J_BACKUP,job_args)&&a->screen==PROGRESS);pid_t main_pid=a->job.pid;unsigned int events=a->progress_events;
        a->pressed_action=A_CONFIRM;a->pressed_hold=3000;a->pointer_down=true;a->pressed_at=milliseconds();a->kinetic.active=true;a->motion_surface=SCROLL_FOLDER;
        uint64_t at=milliseconds();a->now=at;key_event(a,KEY_POWER,1);DCHECK(!a->pressed_action&&!a->pointer_down&&!a->kinetic.active&&a->job.pid==main_pid&&!a->display_control.pid);
        a->now=at+40;key_event(a,KEY_POWER,2);a->now=at+80;key_event(a,KEY_POWER,1);DCHECK(!a->display_control.pid);
        a->now=at+120;key_event(a,KEY_POWER,0);DCHECK(a->display_control.pid&&a->job.pid==main_pid);
        DCHECK(display_test_settle(a)&&a->display_blanked&&a->display_input_blocked&&a->job.pid==main_pid&&a->screen==PROGRESS);
        DCHECK(a->progress_events>events&&a->progress_done==37);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&a->job.pid==main_pid&&a->screen==PROGRESS&&a->display_hz==90);
        DCHECK(await_job(a)&&a->successful&&a->screen==RESULT);goto done;
    }
    if(!strcmp(which,"power-focus-hold")){
        a->allow_power=true;a->power_intent=201;screen(a,POWER_CONFIRM);render(a);Button*b=test_button(a,A_POWER_CONFIRM);DCHECK(b&&b->enabled);
        a->focus=(int)(b-a->buttons);uint64_t at=milliseconds();pointer(a,true,b->r.x+100,b->r.y+50,at);DCHECK(a->pressed_action==A_POWER_CONFIRM);
        a->now=at+100;key_event(a,KEY_POWER,1);DCHECK(!a->pressed_action&&!a->pointer_down&&a->pointer_requires_release);
        a->now=at+200;key_event(a,KEY_POWER,2);a->now=at+1800;key_event(a,KEY_POWER,2);a->now=at+2100;key_event(a,KEY_POWER,0);
        pointer(a,false,b->r.x+100,b->r.y+50,at+2200);DCHECK(a->running&&!a->exit_code&&!a->display_control.pid&&!a->job.pid);
        key_event(a,KEY_POWER,0);key_event(a,KEY_POWER,2);DCHECK(!a->display_control.pid&&a->running);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&a->display_blanked&&a->running&&!a->exit_code&&a->screen==POWER_CONFIRM);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&a->running&&!a->exit_code);goto done;
    }
    if(!strcmp(which,"wake-contact")){
        render(a);a->focus=1;int focus=a->focus;display_test_power(a,100);DCHECK(display_test_settle(a)&&a->display_blanked);
        pointer(a,true,188,506,100);key_event(a,KEY_VOLUMEUP,1);key_event(a,KEY_ENTER,1);key_event(a,KEY_ENTER,0);
        DCHECK(a->screen==HOME&&a->focus==focus&&!a->job.pid&&!a->pressed_action&&a->pointer_requires_release);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&a->pointer_requires_release);
        Input key_only={.need_release=true};for(int k=0;k<MAX_SLOTS;k++)key_only.slots[k].id=-1;Display unused={0};
        input_sync(a,&key_only,&unused);DCHECK(a->pointer_requires_release&&a->screen==HOME&&!a->job.pid);
        pointer(a,true,188,506,300);pointer(a,false,188,506,400);DCHECK(a->screen==HOME&&!a->job.pid&&!a->pointer_requires_release);
        DCHECK(test_press(a,A_BACKUP,50)&&a->screen==BACKUP);
        /* The page reads the systems status once, to know whether userdata is Android's (2026-11-11):
           the read ends, and the page stays. */
        {uint64_t deadline=milliseconds()+12000;while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}}
        DCHECK(!a->job.pid&&a->screen==BACKUP);goto done;
    }
    if(!strcmp(which,"terminal-mtp")){
        screen(a,TERMINAL);a->terminal_known=a->terminal_available=true;copy(a->terminal_state,sizeof(a->terminal_state),"active");copy(a->terminal_session,sizeof(a->terminal_session),"fixture-active-pty");
        a->terminal_input[0]='x';a->terminal_input_length=1;a->usb_known=true;copy(a->usb_state,sizeof(a->usb_state),"active");copy(a->usb_session,sizeof(a->usb_session),"fixture-active-mtp");
        display_test_power(a,100);DCHECK(display_test_settle(a)&&a->display_blanked&&a->screen==TERMINAL);
        key_event(a,KEY_ENTER,1);key_event(a,KEY_BACKSPACE,1);key_event(a,KEY_ENTER,2);DCHECK(a->terminal_input_length==1&&a->terminal_input[0]=='x');
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&a->screen==TERMINAL&&a->terminal_input_length==1);
        DCHECK(!strcmp(a->terminal_session,"fixture-active-pty")&&!strcmp(a->usb_session,"fixture-active-mtp")&&!strcmp(a->usb_state,"active")&&!a->job.pid);
        key_event(a,KEY_ENTER,1);DCHECK(a->terminal_input_length==2&&a->terminal_input[1]=='\r');goto done;
    }
    if(!strcmp(which,"input-timestamps")){
        int pipes[2];DCHECK(pipe2(pipes,O_NONBLOCK|O_CLOEXEC)==0);Input input={.fd=pipes[0],.clock_monotonic=true};for(int k=0;k<MAX_SLOTS;k++)input.slots[k].id=-1;
        Display d={0};struct input_event held[]={{{1,0},EV_KEY,KEY_POWER,1},{{1,200000},EV_KEY,KEY_POWER,2},{{3,0},EV_KEY,KEY_POWER,0}};
        DCHECK(write(pipes[1],held,sizeof(held))==(ssize_t)sizeof(held));a->now=12345;handle_input(a,&input,&d);DCHECK(!a->display_control.pid&&!a->display_power_down&&a->now==3000);
        struct input_event tap[]={{{4,0},EV_KEY,KEY_POWER,1},{{4,50000},EV_KEY,KEY_POWER,2},{{4,100000},EV_KEY,KEY_POWER,0}};
        DCHECK(write(pipes[1],tap,sizeof(tap))==(ssize_t)sizeof(tap));handle_input(a,&input,&d);close(pipes[0]);close(pipes[1]);
        DCHECK(a->display_control.pid&&display_test_settle(a)&&a->display_blanked&&a->running&&!a->exit_code);goto done;
    }
    if(!strcmp(which,"lost-reply")||!strcmp(which,"lost-status")||!strcmp(which,"timeout-reply")){
        display_test_power(a,100);pid_t first=a->display_control.pid;DCHECK(first>0);
        display_test_power(a,100);DCHECK(a->display_control.pid==first);
        DCHECK(display_test_settle(a)&&a->display_blanked&&a->display_input_blocked&&!a->job.pid&&a->screen==HOME);
        if(!strcmp(which,"lost-status"))DCHECK(a->display_uncertain&&!a->display_light_known);
        else DCHECK(!a->display_uncertain&&a->display_light_known);
        render(a);pointer(a,true,188,506,100);pointer(a,false,188,506,200);DCHECK(a->screen==HOME&&!a->job.pid);
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked&&!a->display_input_blocked&&!a->display_uncertain&&a->display_percent==64&&a->display_hz==90);
        /* Screen timeout: nothing before the limit, one blank at the limit, none for Never. */
        if(strcmp(which,"lost-reply"))goto done;
        a->screen_timeout_index=0;a->now=milliseconds();a->last_activity=a->now;DCHECK(!screen_timeout_tick(a));
        a->screen_timeout_index=5;a->now+=31000;DCHECK(!screen_timeout_tick(a));
        a->screen_timeout_index=0;DCHECK(screen_timeout_tick(a)&&display_test_settle(a)&&a->display_blanked&&!screen_timeout_tick(a));
        display_test_power(a,100);DCHECK(display_test_settle(a)&&!a->display_blanked);goto done;
    }
    if(!strcmp(which,"slider-live")){
        /* A brightness check running under the finger must not end the drag or grey out the page. */
        screen(a,DISPLAY_SETTINGS);render(a);uint64_t at=milliseconds();pointer(a,true,360,1064,at);DCHECK(a->display_brightness_drag);
        DCHECK(start_display_control(a,DC_BRIGHTNESS,50)&&a->display_brightness_drag&&a->pointer_down&&!a->display_input_blocked);
        render(a);Button*refresh=test_button(a,A_DISPLAY_REFRESH),*timeout=test_button(a,A_TIMEOUT+1);DCHECK(refresh&&refresh->enabled&&timeout&&timeout->enabled);
        pointer(a,true,500,1064,at+30);DCHECK(a->display_brightness_drag&&a->display_preview_percent==display_slider_percent(500)&&a->display_drag_x==500);
        DCHECK(display_test_settle(a)&&a->display_brightness_drag&&!a->display_input_blocked);
        /* The status read that follows each change must not grey the page either. */
        DCHECK(start_display_control(a,DC_STATUS,0));render(a);refresh=test_button(a,A_DISPLAY_REFRESH);DCHECK(refresh&&refresh->enabled);DCHECK(display_test_settle(a));
        pointer(a,true,540,1064,at+60);DCHECK(a->display_brightness_drag&&a->display_drag_x==540);
        pointer(a,false,540,1064,at+90);DCHECK(!a->display_brightness_drag&&display_test_settle(a)&&a->display_percent==display_slider_percent(540));goto done;
    }
    if(!strcmp(which,"slider")||!strcmp(which,"no-drm")||!strcmp(which,"brightness-not-applied")||!strcmp(which,"slider-cancel")){
        screen(a,DISPLAY_SETTINGS);render(a);Button*b60=test_button(a,A_DISPLAY_60),*b90=test_button(a,A_DISPLAY_90),*brightness=test_button(a,A_DISPLAY_BRIGHTNESS);
        DCHECK(b60&&b90&&brightness&&brightness->enabled&&b60->r.x==52&&b60->r.y==360&&b60->r.w==296&&b60->r.h==84&&b90->r.x==372&&b90->r.y==360);
        if(!strcmp(which,"no-drm"))DCHECK(!a->display_supported&&!a->display_hz&&!b60->enabled&&!b90->enabled&&display_slider_usable(a));
        uint64_t at=milliseconds();pointer(a,true,360,1064,at);DCHECK(a->display_brightness_drag&&a->display_preview_percent==51&&a->display_percent==64&&!a->display_control.pid);
        pointer(a,true,-100,1064,at+20);DCHECK(a->display_preview_percent==1&&a->display_percent==64&&!a->display_control.pid);
        if(!strcmp(which,"slider-cancel")){
            display_test_power(a,100);DCHECK(!a->display_brightness_drag&&a->display_control.pid&&display_test_settle(a)&&a->display_blanked);
            pointer(a,false,1000,1064,at+300);display_test_power(a,100);DCHECK(display_test_settle(a)&&a->display_percent==64&&!a->display_blanked);goto done;
        }
        pointer(a,true,1000,1064,at+40);DCHECK(a->display_preview_percent==100&&a->display_percent==64&&!a->display_control.pid);
        pointer(a,false,1000,1064,at+60);DCHECK(!a->display_brightness_drag&&a->display_control.pid&&display_test_settle(a)&&a->screen==DISPLAY_SETTINGS&&!a->display_input_blocked&&!a->display_blanked);
        if(!strcmp(which,"brightness-not-applied")){DCHECK(a->display_percent==64&&strstr(a->display_light_note,"Readback:")&&!a->display_uncertain);goto done;}
        DCHECK(a->display_percent==100&&a->display_raw==255&&strstr(a->display_light_note,"confirmed"));
        render(a);pointer(a,true,76,1064,at+200);pointer(a,false,-100,1064,at+250);DCHECK(display_test_settle(a)&&a->display_percent==1&&a->display_raw==3&&a->display_last_percent==1);
        DCHECK(a->display_hz==(!strcmp(which,"no-drm")?0u:90u));goto done;
    }
    if(!strcmp(which,"strict-readback")){
        const char*valid="{\"brightness_supported\":true,\"blank_supported\":true,\"blanked\":false,\"brightness_percent\":64,\"brightness_raw\":163,\"brightness_max\":255,\"brightness_requested_raw\":163,\"brightness_actual_raw\":163,\"last_nonzero_percent\":64}";
        DCHECK(display_test_json(a,valid));
        const char*bad[]={"{\"brightness_supported\":1,\"blank_supported\":true,\"blanked\":false}","{\"brightness_supported\":true,\"blank_supported\":true,\"blanked\":\"false\"}","{\"brightness_supported\":true,\"blank_supported\":true,\"blanked\":null}","{\"brightness_supported\":true,\"blank_supported\":true,\"blanked\":false,\"brightness_percent\":101,\"brightness_raw\":256,\"brightness_max\":255,\"brightness_requested_raw\":256,\"brightness_actual_raw\":256,\"last_nonzero_percent\":64}"};
        for(unsigned int k=0;k<sizeof(bad)/sizeof(*bad);k++){DCHECK(!display_test_json(a,bad[k]));DCHECK(a->display_percent==64&&a->display_max==255&&!a->display_blanked);}
        DCHECK(display_slider_percent(-2147480000)==1&&display_slider_percent(2147480000)==100);
        DCHECK(!start_display_control(a,DC_BRIGHTNESS,0)&&!start_display_control(a,DC_BRIGHTNESS,101)&&!a->display_control.pid);goto done;
    }
    DCHECK(false);
done:
    /* The Backup page reads the systems status once (2026-11-11); a case that ends there lets it end. */
    {uint64_t deadline=milliseconds()+12000;while(a->job.pid&&a->job.kind==J_SYS_STATUS&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}}
    DCHECK(!a->display_control.pid&&!a->job.pid&&a->running&&!a->exit_code);
    close(sentinel);unsetenv("A22X_RECOVERY_LOCK_FD");unsetenv("A22X_DISPLAY_FIXTURE_SENTINEL_FD");display_test_stop(a);
    printf("{\"ok\":true,\"case\":\"%s\",\"checks\":%u,\"helper_double\":true,\"device_io\":false}\n",which,checks);return 0;
failed:
    if(sentinel>=0)close(sentinel);
    display_test_stop(a);return 1;
}

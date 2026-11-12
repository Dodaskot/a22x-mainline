/* SPDX-License-Identifier: MIT — actual UI, explicit installer transport double. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
#define ICHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"install-ui check %u line %d failed: %s; screen=%d status=%s message=%s\n",checks,__LINE__,#expr,a->screen,a->status,a->message);free(a->pixels);free(a);return 1;}}while(0)
static bool install_settle(App*a){uint64_t deadline=milliseconds()+30000;while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}return !a->job.pid;}
static void install_review_end(App*a){
    uint64_t now=milliseconds();
    while(a->install.review_scroll<install_review_max_scroll(a)){
        workflow_swipe(a,250,1150,500,now);stop_inertia(a,"test-next-drag");render(a);now+=200;
    }
    render(a);
}
int main(int argc,char**argv){
    if(argc!=6)return 2;
    unsigned checks=0;const char*which=argv[4];App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->install_helper,sizeof(a->install_helper),argv[2]);copy(a->backend,sizeof(a->backend),argv[2]);copy(a->filesystem_helper,sizeof(a->filesystem_helper),argv[3]);copy(a->python,sizeof(a->python),argv[5]);
    ICHECK(snprintf(a->workdir,sizeof(a->workdir),"%s/privateplans",argv[1])<(int)sizeof(a->workdir)&&private_workdir(a->workdir));
    ICHECK(snprintf(a->repositories[0],PATH_LEN,"%s/repo",argv[1])<PATH_LEN);a->repository_count=1;
    screen(a,TOOLS);render(a);
    a->tools_scroll=200;render(a);ICHECK(!test_button(a,A_INSTALL)&&test_button(a,A_DISPLAY));a->tools_scroll=0;render(a);
    /* A drag originating on a real button must not activate it. */
    uint64_t now=milliseconds();int drag_y=test_button(a,A_DISPLAY)->r.y+50;pointer(a,true,188,drag_y,now);pointer(a,true,188,drag_y-70,now+30);pointer(a,false,188,drag_y-70,now+45);
    ICHECK(a->screen==TOOLS&&!a->job.pid&&a->tools_scroll==70&&a->kinetic.active&&a->motion_surface==SCROLL_TOOLS);
    tick_inertia(a,now+60);ICHECK(a->tools_scroll>70&&a->tools_scroll<=tools_max_scroll(a));int64_t before=a->tools_scroll;
    render(a);pointer(a,true,532,1290,now+65);pointer(a,false,532,1290,now+90);ICHECK(!a->kinetic.active&&a->screen==TOOLS&&!a->job.pid&&a->tools_scroll==before);
    tools_scroll_to(a,INT64_MAX);render(a);ICHECK(a->tools_scroll==tools_max_scroll(a));
    a->fs_dirty=true;copy(a->fs_text,sizeof(a->fs_text),"unsaved installer editor ґ");
    screen(a,ANDROID_MENU);render(a);ICHECK(test_press(a,A_INSTALL,50)&&a->screen==RESULT&&a->result_return==FILE_EDIT&&!a->job.pid);
    a->fs_dirty=false;screen(a,ANDROID_MENU);render(a);ICHECK(test_press(a,A_INSTALL,50)&&install_settle(a)&&a->screen==INSTALL&&a->install.known);
    if(!strcmp(which,"guard-missing")){
        ICHECK(!a->install.pending_count&&a->install.status_blockers[0]&&a->install.reboot_blocked&&!install_ready(a));
        a->allow_power=true;screen(a,POWER);ICHECK(!test_press(a,A_REBOOT,50));action(a,A_REBOOT);ICHECK(a->screen==POWER&&a->running);
        ICHECK(test_press(a,A_REBOOT_RECOVERY,50)&&a->screen==POWER_CONFIRM&&a->power_intent==202&&a->running);
        goto done;
    }
    bool pending=!strcmp(which,"resume")||!strcmp(which,"pending-change");
    if(pending){
        ICHECK(a->install.pending_count==1&&a->install.reboot_blocked&&!test_press(a,A_INSTALL_SOURCE,50)&&!test_press(a,A_INSTALL_REVIEW,50));
        a->allow_power=true;screen(a,POWER);ICHECK(!test_press(a,A_REBOOT,50));action(a,A_REBOOT);ICHECK(a->screen==POWER&&a->running);screen(a,INSTALL);
        ICHECK(test_press(a,A_INSTALL_PENDING,50)&&a->job.kind==J_INSTALL_STATUS&&install_settle(a));
        if(!strcmp(which,"pending-change")){ICHECK(a->screen==RESULT&&!strcmp(a->status,"INSTALL_TRANSACTION_CHANGED")&&!a->install.plan_valid);goto done;}
        ICHECK(a->screen==INSTALL_PLAN&&a->install.resuming&&a->install.plan_valid&&!strcmp(a->install.transaction,a->install.selected_pending.id));
    }else{
        bool stock=!strcmp(which,"stock")||!strncmp(which,"stock-",6)||!strcmp(which,"blocked")||!strcmp(which,"bad-order")||!strcmp(which,"uncertain")||!strcmp(which,"extra-target");
        ICHECK(test_press(a,stock?A_INSTALL_STOCK:A_INSTALL_BOOT,50)&&!a->job.pid);
        if(!strcmp(which,"stock-unqualified")){
            ICHECK(!a->install.stock_qualified&&a->install.stock_blockers[0]&&!a->install.status_blockers[0]&&!a->install.reboot_blocked&&!install_ready(a)&&!test_press(a,A_INSTALL_REVIEW,50));
            ICHECK(test_press(a,A_INSTALL_BOOT,50)&&install_ready(a));goto done;
        }
        ICHECK(test_press(a,A_INSTALL_SOURCE,50)&&install_settle(a)&&a->screen==FILE_VOLUMES&&a->install.pick_source);
        int sd=-1,user=-1,ro=-1;for(int n=0;n<a->volume_count;n++){if(!strcmp(a->volumes[n].id,"sdcard-p1"))sd=n;if(!strcmp(a->volumes[n].id,"userdata"))user=n;if(!strcmp(a->volumes[n].id,"sdcard-p2"))ro=n;}
        ICHECK(sd>=0&&user>=0&&ro>=0&&!test_press(a,A_FS_VOLUME_ITEM+user,50)&&!test_press(a,A_FS_VOLUME_ITEM+ro,50));
        ICHECK(test_press(a,A_FS_VOLUME_ITEM+sd,50)&&install_settle(a)&&a->screen==FILE_BROWSER);
        int invalid=-1,valid=-1;for(int n=0;n<a->file_count;n++){if(!strcmp(a->files[n].name,"bad.txt"))invalid=n;if(!strcmp(a->files[n].name,stock?"manifest.json":"boot.img"))valid=n;}
        ICHECK(invalid>=0&&valid>=0&&test_press(a,A_FS_ENTRY+invalid,50)&&install_settle(a)&&a->screen==FILE_INFO);
        ICHECK(!test_press(a,A_INSTALL_USE_SOURCE,50)&&!test_press(a,A_FS_DELETE,3100)&&!test_press(a,A_FS_EDIT,50));
        ICHECK(test_press(a,A_BACK,50)&&install_settle(a)&&test_press(a,A_FS_ENTRY+valid,50)&&install_settle(a)&&install_source_selected(a));
        ICHECK(test_press(a,A_INSTALL_USE_SOURCE,50)&&a->screen==INSTALL&&!a->install.pick_source&&!a->job.pid&&a->install.source_path[0]);
        ICHECK(test_press(a,A_INSTALL_BACKUP,50)&&install_settle(a)&&a->screen==LIST&&a->install.pick_backup&&a->backup_count==3);
        ICHECK(!test_press(a,A_BACKUP_ITEM+1,50)&&!test_press(a,A_BACKUP_ITEM+2,50));
        ICHECK(test_press(a,A_BACKUP_ITEM,50)&&a->screen==INSTALL&&!a->install.pick_backup&&a->install.backup_path[0]&&!a->job.pid);
        ICHECK(test_press(a,A_INSTALL_REVIEW,50)&&install_settle(a));
        if(!strncmp(which,"wrong-",6)||!strcmp(which,"bad-order")||!strcmp(which,"bad-gate")||!strcmp(which,"extra-target")||!strcmp(which,"bad-hold")||!strcmp(which,"bad-token")||!strcmp(which,"bad-schema")||!strcmp(which,"result-mode")||!strcmp(which,"stock-data-gate")||!strcmp(which,"stock-protected-target")||!strcmp(which,"stock-omitted-target")||!strncmp(which,"bad-journal",11)){
            ICHECK(a->screen==RESULT&&!a->install.plan_valid&&!a->install.can_execute&&!test_press(a,A_INSTALL_CONFIRM,6000));
            ICHECK(!strcmp(a->status,!strcmp(which,"result-mode")?"INVALID_INSTALL_RESULT":"INVALID_INSTALL_PLAN"));goto done;
        }
    }
    ICHECK(a->screen==INSTALL_PLAN&&a->install.plan_valid&&install_review_max_scroll(a)>0);
    if(!strcmp(which,"boot-noop")||!strcmp(which,"stock-noop"))ICHECK(!a->install.write_count&&!a->install.order_count&&a->install.skip_count==(!strcmp(which,"stock-noop")?7u:1u)&&!a->install.recovery_written);
    ICHECK(!test_press(a,A_INSTALL_CONFIRM,6000)&&!a->job.pid);
    int distance=(int)(install_review_max_scroll(a)/2);if(distance>500)distance=500;
    uint64_t start=milliseconds();workflow_swipe(a,250,1150,1150-distance,start);ICHECK(a->install.review_scroll==distance&&a->kinetic.active&&a->motion_surface==SCROLL_INSTALL_REVIEW&&!a->job.pid);
    tick_inertia(a,start+120);ICHECK(a->install.review_scroll>distance&&a->install.review_scroll<=install_review_max_scroll(a));
    before=a->install.review_scroll;render(a);pointer(a,true,250,650,start+130);pointer(a,false,250,650,start+160);ICHECK(a->install.review_scroll==before&&!a->kinetic.active&&!a->job.pid);
    install_scroll_to(a,false,INT64_MAX);ICHECK(a->button_count==0&&a->install.review_scroll==install_review_max_scroll(a));
    /* A stale hit map is empty until the next frame is rendered. */
    pointer(a,true,360,1370,start+170);pointer(a,false,360,1370,start+7000);ICHECK(!a->job.pid);
    install_scroll_to(a,false,0);install_review_end(a);ICHECK(a->install.review_bottom_seen&&a->install.review_scroll==install_review_max_scroll(a));
    if(!strcmp(which,"blocked")){ICHECK(!a->install.can_execute&&!test_press(a,A_INSTALL_CONFIRM,6000)&&!a->job.pid);goto done;}
    unsigned hold=a->install.hold_ms;ICHECK(hold==(!strcmp(a->install.operation,"install-stock")?5000u:3000u));
    ICHECK(test_press(a,A_INSTALL_CONFIRM,hold-1)&&!a->job.pid&&a->install.plan_valid);
    render(a);now=milliseconds();pointer(a,true,360,1370,now);pointer(a,true,10,1370,now+100);pointer(a,true,360,1370,now+hold);pointer(a,false,360,1370,now+hold+100);ICHECK(!a->job.pid&&a->install.plan_valid);
    if(!strcmp(which,"tamper")){int fd=open(a->install.plan_path,O_WRONLY|O_APPEND|O_NOFOLLOW|O_CLOEXEC);ICHECK(fd>=0);ssize_t written=write(fd," ",1);int closed=close(fd);ICHECK(written==1&&closed==0);}
    ICHECK(test_press(a,A_INSTALL_CONFIRM,hold)&&a->job.kind==J_INSTALL_EXECUTE&&!a->install.plan_valid&&a->install.uncertain&&install_settle(a));
    if(!strcmp(which,"tamper")){ICHECK(a->screen==RESULT&&!strcmp(a->status,"PLAN_FILE_CHANGED")&&!a->install.plan_valid);goto done;}
    if(!strcmp(which,"bad-receipt")){ICHECK(a->screen==RESULT&&!strcmp(a->status,"INSTALL_RECEIPT_UNCONFIRMED")&&a->install.uncertain&&!a->successful);goto done;}
    if(!strcmp(which,"uncertain")){
        ICHECK(a->screen==RESULT&&!strcmp(a->status,"FIXTURE_ACK_LOST")&&a->install.uncertain&&a->install.reboot_blocked);
        action(a,A_INSTALL_CONFIRM);ICHECK(!a->job.pid&&!a->install.plan_valid);
        ICHECK(test_press(a,A_DONE,50)&&a->job.kind==J_INSTALL_STATUS&&install_settle(a)&&a->screen==INSTALL&&a->install.pending_count==1);
        ICHECK(!test_press(a,A_INSTALL_REVIEW,50)&&test_press(a,A_INSTALL_PENDING,50)&&install_settle(a)&&a->screen==INSTALL_PLAN&&a->install.resuming);
        install_review_end(a);ICHECK(test_press(a,A_INSTALL_CONFIRM,5000)&&install_settle(a));
    }
    ICHECK(a->screen==INSTALL_RESULT&&a->successful&&!a->install.uncertain&&!a->install.plan_valid&&a->install.result_journal[0]);
    ICHECK(!a->install.reboot_blocked&&a->running&&a->exit_code==0);
    ICHECK(test_press(a,A_INSTALL_RESULT_DONE,50)&&a->job.kind==J_INSTALL_STATUS&&install_settle(a)&&a->screen==INSTALL&&!a->install.pending_count);
done:
    ICHECK(!strcmp(a->fs_text,"unsaved installer editor ґ")&&!a->fs_dirty&&a->running&&a->exit_code==0);
    install_forget_plan(a);free(a->pixels);free(a);
    printf("{\"ok\":true,\"case\":\"%s\",\"checks\":%u,\"actual_filesystem_backend\":true,\"installer_contract_double\":true,\"device_io\":false}\n",which,checks);return 0;
}

/* SPDX-License-Identifier: MIT
 * Actual frontend -> actual install.py/filesystem.py CLI, owned regular files.
 * The backup picker uses a labelled read-only manifest adapter because installer
 * fixture backups intentionally contain raw regular-image simulation data.
 */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main

#define CHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"install backend UI check %u line %d failed: %s; screen=%d status=%s message=%s\n",checks,__LINE__,#expr,a->screen,a->status,a->message);free(a->pixels);free(a);return 1;}}while(0)

static bool settle_install(App*a){
    uint64_t deadline=milliseconds()+45000;
    while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}
    return !a->job.pid;
}
static bool select_install_source(App*a,const char*source){
    if(!test_press(a,A_INSTALL_SOURCE,50)||!settle_install(a)||a->screen!=FILE_VOLUMES)return false;
    int sd=-1;for(int n=0;n<a->volume_count;n++)if(!strcmp(a->volumes[n].id,"sdcard-p1"))sd=n;
    if(sd<0||!test_press(a,A_FS_VOLUME_ITEM+sd,50)||!settle_install(a)||a->screen!=FILE_BROWSER)return false;
    char path[PATH_LEN];if(!relative_path(source,path,sizeof(path)))return false;
    char*saved=NULL,*part=strtok_r(path+1,"/",&saved);
    while(part){
        int item=-1;for(int n=0;n<a->file_count;n++)if(!strcmp(a->files[n].name,part))item=n;
        if(item<0||!test_press(a,A_FS_ENTRY+item,50)||!settle_install(a))return false;
        part=strtok_r(NULL,"/",&saved);
        if(part&&a->screen!=FILE_BROWSER)return false;
    }
    return a->screen==FILE_INFO&&install_source_selected(a)&&test_press(a,A_INSTALL_USE_SOURCE,50)&&
        a->screen==INSTALL&&!strcmp(a->install.source_path,source);
}
static bool review_all_install_rows(App*a){
    unsigned gestures=0;uint64_t now=milliseconds();
    while(a->install.review_scroll<install_review_max_scroll(a)){
        if(++gestures>256)return false;
        workflow_swipe(a,250,1150,500,now);stop_inertia(a,"fixture-review");render(a);now+=200;
    }
    render(a);return a->install.review_bottom_seen;
}

int main(int argc,char**argv){
    if(argc!=9)return 2;
    unsigned checks=0;const char*which=argv[5];bool resume=!strcmp(which,"resume");
    bool stock=resume||!strncmp(which,"stock",5);bool denied=!strcmp(which,"tamper")||!strcmp(which,"source-change");
    bool noop=!strcmp(which,"boot-noop")||!strcmp(which,"stock-noop");
    App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->install_helper,sizeof(a->install_helper),argv[2]);
    copy(a->backend,sizeof(a->backend),argv[3]);copy(a->filesystem_helper,sizeof(a->filesystem_helper),argv[4]);copy(a->python,sizeof(a->python),argv[6]);
    copy(a->repositories[0],PATH_LEN,argv[7]);a->repository_count=1;
    CHECK(snprintf(a->workdir,sizeof(a->workdir),"%s/privateplans",argv[1])<(int)sizeof(a->workdir)&&private_workdir(a->workdir));
    screen(a,HOME);render(a);CHECK(!a->job.pid&&!a->install.known);
    screen(a,ANDROID_MENU);render(a);CHECK(test_press(a,A_INSTALL,50)&&settle_install(a)&&a->screen==INSTALL&&a->install.known);
    if(resume){
        CHECK(a->install.pending_count==1&&a->install.reboot_blocked&&!test_press(a,A_INSTALL_REVIEW,50));
        CHECK(test_press(a,A_INSTALL_PENDING,50)&&a->job.kind==J_INSTALL_STATUS&&settle_install(a));
        CHECK(a->screen==INSTALL_PLAN&&a->install.resuming&&a->install.plan_valid);
    }else{
        CHECK(!a->install.pending_count&&test_press(a,stock?A_INSTALL_STOCK:A_INSTALL_BOOT,50));
        CHECK(select_install_source(a,argv[8]));
        CHECK(test_press(a,A_INSTALL_BACKUP,50)&&settle_install(a)&&a->screen==LIST&&a->backup_count==1&&a->install.pick_backup);
        CHECK(test_press(a,A_BACKUP_ITEM,50)&&a->screen==INSTALL&&a->install.backup_path[0]&&!a->install.pick_backup);
        CHECK(test_press(a,A_INSTALL_REVIEW,50)&&settle_install(a)&&a->screen==INSTALL_PLAN&&a->install.plan_valid);
    }
    CHECK(a->install.can_execute&&a->install.backup_complete&&a->install.backup_verified&&a->install.backup_same_device);
    unsigned changed=a->install.write_count,skipped=a->install.skip_count;
    if(noop)CHECK(!changed&&!a->install.order_count&&skipped==(stock?7u:1u));
    else CHECK(changed>0);
    CHECK(!test_press(a,A_INSTALL_CONFIRM,6000)&&!a->job.pid);
    CHECK(review_all_install_rows(a));unsigned hold=stock?5000:3000;
    CHECK(a->install.hold_ms==hold&&test_press(a,A_INSTALL_CONFIRM,hold-1)&&!a->job.pid);
    if(!strcmp(which,"tamper")){
        int fd=open(a->install.plan_path,O_WRONLY|O_APPEND|O_NOFOLLOW|O_CLOEXEC);CHECK(fd>=0);
        ssize_t put=write(fd," ",1);int closed=close(fd);CHECK(put==1&&closed==0);
    }else if(!strcmp(which,"source-change")){
        FileVolume*v=current_volume(a);char path[PATH_LEN];
        CHECK(v&&strlen(v->mountpoint)>strlen(a->fixture)&&!strncmp(v->mountpoint,a->fixture,strlen(a->fixture))&&v->mountpoint[strlen(a->fixture)]=='/');
        CHECK(snprintf(path,sizeof(path),"%s%s",v->mountpoint,a->install.source_path)<(int)sizeof(path));
        int fd=open(path,O_WRONLY|O_APPEND|O_NOFOLLOW|O_CLOEXEC);CHECK(fd>=0);
        ssize_t put=write(fd," ",1);int closed=close(fd);CHECK(put==1&&closed==0);
    }
    CHECK(test_press(a,A_INSTALL_CONFIRM,hold)&&a->job.kind==J_INSTALL_EXECUTE&&a->install.uncertain&&settle_install(a));
    if(denied){
        CHECK(a->screen==RESULT&&!a->successful&&!a->install.plan_valid&&a->install.reboot_blocked&&a->status[0]);
        action(a,A_INSTALL_CONFIRM);CHECK(!a->job.pid);
    }else{
        CHECK(a->screen==INSTALL_RESULT&&a->successful&&!a->install.uncertain&&a->install.result_checkpoint[0]&&a->install.result_journal[0]);
        CHECK(!a->install.reboot_blocked&&test_press(a,A_INSTALL_RESULT_DONE,50)&&settle_install(a)&&a->screen==INSTALL&&!a->install.pending_count);
    }
    CHECK(a->running&&a->exit_code==0&&!a->job.pid);
    printf("{\"ok\":true,\"case\":\"%s\",\"checks\":%u,\"actual_install_backend\":true,\"actual_filesystem_backend\":true,\"backup_picker_manifest_adapter\":true,\"device_io\":false,\"write_targets\":%u,\"skip_targets\":%u,\"execution_refused\":%s}\n",which,checks,changed,skipped,denied?"true":"false");
    install_forget_plan(a);free(a->pixels);free(a);return 0;
}
